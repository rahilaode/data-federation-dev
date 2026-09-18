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
    api.post(f'/api/v1/obdf/{oid}/sources', json={'logical_name': 'kemensos', 'dbms': 'postgresql',
                                                  'database_name': 'kemensos'})
    return oid


def set_clients(api, **kwargs):
    api.app_ref.state.sync_clients = lambda db, obdf_id: fx.clients(**kwargs)


def test_first_sync_creates_active_version(api):
    oid = new_obdf_with_sources(api)
    set_clients(api)
    result = api.post(f'/api/v1/obdf/{oid}/sync').json()
    assert result['changed'] and result['version_no'] == 1 and result['drift_detected'] is False
    counts = result['counts']
    assert counts['models'] == 3 and counts['tables'] == 3 and counts['columns'] == 6
    assert counts['views'] == 1 and counts['artifacts'] == 3 and counts['triples'] > 0
    assert counts['triples_maps'] == 3 and counts['entities'] == 7

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
    assert second['counts']['columns'] == 7
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
    assert {'vdb_validity_error', 'source_system_unresolved'} <= codes
    detail = api.get(f"/api/v1/versions/{result['spec_version_id']}").json()
    errors = [i for i in detail['issues'] if i['code'] == 'vdb_validity_error']
    assert errors and errors[0]['severity'] == 'error'
    # entri INFO dari Teiid tidak ikut dicatat sebagai masalah
    assert all('sedang dimuat' not in i['message'] for i in detail['issues'])


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


# ── struktur ℳ dan 𝒯 ────────────────────────────────────────────────────────────
def synced(api):
    oid = new_obdf_with_sources(api)
    set_clients(api)
    return oid, api.post(f'/api/v1/obdf/{oid}/sync').json()['spec_version_id']


def test_mapping_structure_is_stored(api):
    _, vid = synced(api)
    with api.factory() as db:
        maps = {m.iri.rsplit('#', 1)[-1]: m for m in db.execute(
            select(spec.TriplesMap).filter_by(spec_version_id=vid)).scalars()}
        assert set(maps) == {'MapPenduduk', 'MapPenerima', 'MapRingkas'}
        assert maps['MapPenduduk'].logical_table_kind == 'sql_query'
        assert maps['MapPenduduk'].sql_parse_status == 'ok'
        assert maps['MapPenerima'].logical_table_kind == 'table'

        # logical table berbentuk tabel: kolom diambil dari Σ_S
        kolom_penerima = db.execute(select(spec.LogicalColumn.name, spec.LogicalColumn.expression_kind)
                                    .filter_by(triples_map_id=maps['MapPenerima'].id)).all()
        assert sorted(kolom_penerima) == [('nik', 'passthrough'), ('penerima_id', 'passthrough'),
                                          ('status_ekonomi', 'passthrough')]
        # SELECT * diperluas dari Σ_S dan ditandai star
        kinds = {k for (k,) in db.execute(select(spec.LogicalColumn.expression_kind)
                                          .filter_by(triples_map_id=maps['MapRingkas'].id)).all()}
        assert kinds == {'star'}
        # ekspresi dikenali dan kolom sumbernya tercatat
        expr = db.execute(select(spec.LogicalColumn).filter_by(triples_map_id=maps['MapPenduduk'].id,
                                                               name='nik_upper')).scalar_one()
        # sqlglot menormalkan fungsi (UCASE -> UPPER); yang penting kolom sumbernya terlacak
        assert expr.expression_kind == 'expression' and 'NIK' in expr.expression_sql.upper()
        sources = db.execute(select(spec.TeiidColumn.name).join(
            spec.LogicalColumnSource, spec.LogicalColumnSource.teiid_column_id == spec.TeiidColumn.id)
            .where(spec.LogicalColumnSource.logical_column_id == expr.id)).scalars().all()
        assert sources == ['nik']

        # kolom WHERE tercatat sebagai rujukan SQL
        refs = db.execute(select(spec.SqlReference.clause, spec.TeiidColumn.name).join(
            spec.TeiidColumn, spec.TeiidColumn.id == spec.SqlReference.teiid_column_id)
            .where(spec.SqlReference.triples_map_id == maps['MapPenduduk'].id)).all()
        assert refs == [('where', 'tanggal_lahir')]

        # subject map, kelas, predikat, datatype, dan join condition
        subject = db.execute(select(spec.TermMap).filter_by(
            triples_map_id=maps['MapPenduduk'].id, position='subject')).scalar_one()
        assert subject.value_kind == 'template' and subject.term_type == 'iri'
        classes = db.execute(select(spec.SubjectClass.class_iri)
                             .filter_by(triples_map_id=maps['MapPenduduk'].id)).scalars().all()
        assert classes == ['http://bansos.go.id/ontology/Penduduk']
        datatypes = db.execute(select(spec.TermMap.datatype).filter_by(
            triples_map_id=maps['MapPenduduk'].id, position='object')).scalars().all()
        assert 'http://www.w3.org/2001/XMLSchema#date' in datatypes
        join = db.execute(select(spec.JoinCondition).filter_by(spec_version_id=vid)).scalar_one()
        assert (join.child_column, join.parent_column) == ('nik', 'nik')
        parent = db.execute(select(spec.TermMap).filter_by(
            spec_version_id=vid, value_kind='parent_triples_map')).scalar_one()
        assert parent.value.endswith('#MapPenduduk')

        # template multikolom menautkan dua logical column
        ringkas_subject = db.execute(select(spec.TermMap).filter_by(
            triples_map_id=maps['MapRingkas'].id, position='subject')).scalar_one()
        linked = db.execute(select(spec.LogicalColumn.name).join(
            spec.TermMapColumn, spec.TermMapColumn.logical_column_id == spec.LogicalColumn.id)
            .where(spec.TermMapColumn.term_map_id == ringkas_subject.id)
            .order_by(spec.LogicalColumn.name)).scalars().all()
        assert linked == ['nik', 'penerima_id']


def test_ontology_structure_is_stored(api):
    _, vid = synced(api)
    with api.factory() as db:
        ont = db.execute(select(spec.Ontology).filter_by(spec_version_id=vid)).scalar_one()
        assert ont.iri == 'http://bansos.go.id/ontology' and ont.version_info == '1.0.1'
        kinds = db.execute(select(spec.OntEntity.kind, func.count()).filter_by(spec_version_id=vid)
                           .group_by(spec.OntEntity.kind)).all()
        assert dict(kinds) == {'class': 2, 'datatype_property': 4, 'object_property': 1}

        deprecated = db.execute(select(spec.OntEntity.iri).filter_by(spec_version_id=vid, deprecated=True)
                                ).scalars().all()
        assert deprecated == ['http://bansos.go.id/ontology/statusEkonomi']
        ranges = dict(db.execute(select(spec.OntEntity.iri, spec.OntRange.owl2ql_compatible)
                                 .join(spec.OntRange, spec.OntRange.entity_id == spec.OntEntity.id)
                                 .where(spec.OntEntity.spec_version_id == vid)).all())
        assert ranges['http://bansos.go.id/ontology/tanggalLahir'] is False      # xsd:date
        assert ranges['http://bansos.go.id/ontology/nik'] is True                # xsd:string
        assert ranges['http://bansos.go.id/ontology/memilikDataKependudukan'] is None   # object property
        relation = db.execute(select(spec.OntPropertyRelation).filter_by(spec_version_id=vid)).scalar_one()
        assert relation.relation == 'sub_property_of'
        label = db.execute(select(spec.OntAnnotation).filter_by(spec_version_id=vid)
                           .where(spec.OntAnnotation.property_iri.like('%label'))).scalar_one()
        assert (label.value, label.lang) == ('NIK', 'id')


def test_profile_and_resolution_issues(api):
    _, vid = synced(api)
    codes = {i['code'] for i in api.get(f'/api/v1/versions/{vid}').json()['issues']}
    assert 'owl2ql_profile_violation' in codes          # xsd:date di luar OWL 2 QL


def test_unknown_column_in_mapping_is_reported(api):
    oid = new_obdf_with_sources(api)
    broken = fx.MAPPING_TTL.replace('rr:column "status_ekonomi"', 'rr:column "kolom_hilang"')
    set_clients(api, agent=fx.FakeAgent(mapping=broken))
    result = api.post(f'/api/v1/obdf/{oid}/sync').json()
    issues = [i for i in result['issues'] if i['code'] == 'logical_column_unresolved']
    assert issues and issues[0]['severity'] == 'error' and 'kolom_hilang' in issues[0]['message']


def test_unparsable_sql_is_reported_but_sync_succeeds(api):
    oid = new_obdf_with_sources(api)
    broken = fx.MAPPING_TTL.replace('SELECT * FROM kemensos.penerima_manfaat', 'BUKAN SQL ###')
    set_clients(api, agent=fx.FakeAgent(mapping=broken))
    result = api.post(f'/api/v1/obdf/{oid}/sync').json()
    assert result['changed']
    assert 'sql_unparsed' in {i['code'] for i in result['issues']}
