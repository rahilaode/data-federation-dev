"""Sync: pembentukan versi spesifikasi, deteksi perubahan, konsistensi, dan endpoint."""
import uuid

import pytest
from sqlalchemy import func, select, text

from ascam_knowledge.db import ops, spec
from ascam_knowledge.sync import sigma
import fixtures_obdf as fx


def new_obdf_with_sources(api):
    name = f'sync-{uuid.uuid4().hex[:8]}'
    oid = api.post('/api/v1/obdf', json={'name': name}).json()['id']
    api.post(f'/api/v1/obdf/{oid}/sources', json={'logical_name': 'dukcapil', 'dbms': 'mysql',
                                                  'database_name': 'dukcapil'})
    return oid


def set_clients(api, **kwargs):
    api.app_ref.state.sync_clients = lambda db, obdf_id: fx.clients(**kwargs)


def test_first_sync_creates_active_version(api):
    oid = new_obdf_with_sources(api)
    set_clients(api)
    result = api.post(f'/api/v1/obdf/{oid}/sync').json()
    assert result['changed'] and result['version_no'] == 1 and result['drift_detected'] is False
    counts = result['counts']
    assert counts['models'] == 2 and counts['tables'] == 2 and counts['columns'] == 3
    assert counts['views'] == 1 and counts['artifacts'] == 3 and counts['triples'] > 0

    detail = api.get(f"/api/v1/versions/{result['spec_version_id']}").json()
    assert detail['version']['status'] == 'active'
    assert detail['version']['teiid_vdb_name'] == 'government'
    assert {a['kind'] for a in detail['artifacts']} == {'vdb_xml', 'r2rml', 'ontology'}

    with api.factory() as db:
        col = db.execute(select(spec.TeiidColumn).join(spec.TeiidTable, spec.TeiidColumn.table_id == spec.TeiidTable.id)
                         .where(spec.TeiidColumn.spec_version_id == result['spec_version_id'],
                                spec.TeiidColumn.name == 'tanggal_lahir')).scalar_one()
        assert col.source_column == 'tgl_lahir_ktp'          # NameInSource dinormalisasi (ADR-0002)
        table = db.get(spec.TeiidTable, col.table_id)
        assert (table.source_schema, table.source_table) == ('dukcapil', 'master_penduduk')
        view = db.execute(select(spec.TeiidView).filter_by(spec_version_id=result['spec_version_id'])).scalar_one()
        assert view.parse_status == 'ok' and view.uses_star is False
        roles = db.execute(select(spec.TeiidDependency.derived_role)
                           .filter_by(spec_version_id=result['spec_version_id'])).scalars().all()
        assert sorted(roles) == ['predicate', 'projection', 'table']   # WHERE, proyeksi, tingkat tabel


def test_second_sync_without_change_creates_no_version(api):
    oid = new_obdf_with_sources(api)
    set_clients(api)
    first = api.post(f'/api/v1/obdf/{oid}/sync').json()
    second = api.post(f'/api/v1/obdf/{oid}/sync').json()
    assert second['changed'] is False and second['spec_version_id'] == first['spec_version_id']
    assert len(api.get(f'/api/v1/obdf/{oid}/versions').json()) == 1
    runs = api.get(f'/api/v1/obdf/{oid}/sync-runs').json()
    assert [r['status'] for r in runs] == ['succeeded', 'succeeded']
    assert runs[0]['produced_spec_version_id'] is None


def test_change_creates_new_version_and_supersedes(api):
    oid = new_obdf_with_sources(api)
    set_clients(api)
    first = api.post(f'/api/v1/obdf/{oid}/sync').json()
    set_clients(api, meta=fx.FakeMetadata(fx.metadata(extra_column=True)))
    second = api.post(f'/api/v1/obdf/{oid}/sync').json()
    assert second['changed'] and second['version_no'] == 2 and second['drift_detected'] is True
    assert second['counts']['columns'] == 4
    versions = {v['version_no']: v for v in api.get(f'/api/v1/obdf/{oid}/versions').json()}
    assert versions[1]['status'] == 'superseded' and versions[2]['status'] == 'active'
    assert versions[2]['parent_id'] == first['spec_version_id']
    assert versions[1]['content_digest'] != versions[2]['content_digest']


def test_sealed_version_content_cannot_change(api):
    oid = new_obdf_with_sources(api)
    set_clients(api)
    vid = api.post(f'/api/v1/obdf/{oid}/sync').json()['spec_version_id']
    with api.factory() as db:
        with pytest.raises(Exception) as info:
            db.execute(text('UPDATE spec.teiid_column SET name = :n WHERE spec_version_id = :v'),
                       {'n': 'x', 'v': vid})
            db.flush()
        assert getattr(info.value.orig, 'sqlstate', None) == '55000'


def test_vdb_validity_errors_and_source_gap_become_issues(api):
    name = f'sync-{uuid.uuid4().hex[:8]}'
    oid = api.post('/api/v1/obdf', json={'name': name}).json()['id']      # tanpa sumber terdaftar
    errors = [{'severity': 'ERROR', 'message': 'TEIID31259 ddl tidak valid'},
              {'severity': 'INFO', 'message': 'sedang dimuat'}]
    set_clients(api, admin=fx.FakeAdmin(info=fx.vdb_info(validity_errors=errors)))
    result = api.post(f'/api/v1/obdf/{oid}/sync').json()
    codes = {i['code'] for i in result['issues']}
    assert codes == {'vdb_validity_error', 'source_system_unresolved'}
    detail = api.get(f"/api/v1/versions/{result['spec_version_id']}").json()
    assert len(detail['issues']) == 2
    assert [i for i in detail['issues'] if i['code'] == 'vdb_validity_error'][0]['severity'] == 'error'


def test_artifact_hash_mismatch_is_reported(api):
    oid = new_obdf_with_sources(api)
    set_clients(api, admin=fx.FakeAdmin(reported_hash='0' * 40))
    result = api.post(f'/api/v1/obdf/{oid}/sync').json()
    assert 'artifact_hash_mismatch' in {i['code'] for i in result['issues']}


def test_artifact_content_is_retrievable(api):
    oid = new_obdf_with_sources(api)
    set_clients(api)
    vid = api.post(f'/api/v1/obdf/{oid}/sync').json()['spec_version_id']
    body = api.get(f'/api/v1/versions/{vid}/artifacts/r2rml').json()
    assert body['content'] == fx.MAPPING_TTL and body['media_type'] == 'text/turtle'
    assert api.get(f'/api/v1/versions/{vid}/artifacts/tidak-ada').status_code == 404


def test_failed_sync_is_recorded(api):
    oid = new_obdf_with_sources(api)

    class Broken:
        def fetch_all(self):
            raise RuntimeError('koneksi ODBC putus')

    set_clients(api, meta=Broken())
    r = api.post(f'/api/v1/obdf/{oid}/sync')
    assert r.status_code == 502 and 'koneksi ODBC putus' in r.json()['detail']
    runs = api.get(f'/api/v1/obdf/{oid}/sync-runs').json()
    assert runs[0]['status'] == 'failed' and runs[0]['error']['type'] == 'RuntimeError'
    assert api.get(f'/api/v1/obdf/{oid}/versions').json() == []      # tidak ada versi setengah jadi


def test_agent_artifact_digest_is_verified():
    """Klien agen menolak isi yang tidak cocok dengan sidik jarinya."""
    import httpx
    from ascam_knowledge.sync.clients import OntopAgentClient

    def handler(request):
        if request.url.path.endswith('/artifacts'):
            return httpx.Response(200, json=[{'kind': 'r2rml', 'exists': True}])
        return httpx.Response(200, json={'kind': 'r2rml', 'name': 'mapping.ttl',
                                         'media_type': 'text/turtle', 'content': 'isi diubah',
                                         'sha256': 'a' * 64})
    client = OntopAgentClient({'host': 'agent', 'port': 8000}, 'token',
                              client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(RuntimeError, match='sidik jari'):
        client.artifacts()


def test_sigma_digest_is_stable_and_sensitive():
    base = sigma.build(fx.metadata(), fx.vdb_info(), {'dukcapil': 'insensitive'})
    same = sigma.build(fx.metadata(), fx.vdb_info(), {'dukcapil': 'insensitive'})
    changed = sigma.build(fx.metadata(extra_column=True), fx.vdb_info(), {'dukcapil': 'insensitive'})
    assert base.digest() == same.digest() != changed.digest()
