"""Analisis dampak dan keputusan D11 terhadap spesifikasi hasil sync."""
import uuid

import pytest
from sqlalchemy import select

from ascam_knowledge.db import spec
import fixtures_obdf as fx
from test_sync import new_obdf_with_sources, set_clients


@pytest.fixture
def obdf(api):
    oid = new_obdf_with_sources(api)
    set_clients(api)
    api.put(f'/api/v1/obdf/{oid}/naming-policy',
            json={'property_iri_template': '{namespace}{column_camel}',
                  'on_collision': 'qualify_with_class',
                  'namespace': 'http://bansos.go.id/ontology/'})
    api.put(f'/api/v1/obdf/{oid}/settings/adaptation.add_column', json={'value': {'mode': 'auto'}})
    api.post(f'/api/v1/obdf/{oid}/sync')
    return oid


def impact(api, oid, **body):
    r = api.post(f'/api/v1/obdf/{oid}/impact', json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ── RENAME ──────────────────────────────────────────────────────────────────────
def test_rename_is_automatic_via_name_in_source(api, obdf):
    out = impact(api, obdf, operation='rename', source='dukcapil', table='master_penduduk',
                 column='tgl_lahir_ktp', new_column='tgl_lahir_baru')
    assert out['decision'] == 'auto' and out['pattern'] == 'P-003'
    assert out['targets'][0]['column'] == 'tanggal_lahir'         # nama Teiid tidak berubah
    assert out['actions'] == [{'artifact': 'vdb', 'operation': 'set_name_in_source',
                               'model': 'dukcapil', 'table': 'master_penduduk',
                               'column': 'tanggal_lahir', 'name_in_source': 'tgl_lahir_baru'}]


def test_rename_into_existing_source_name_needs_hitl(api, obdf):
    out = impact(api, obdf, operation='rename', source='dukcapil', table='master_penduduk',
                 column='tgl_lahir_ktp', new_column='nik')
    assert out['decision'] == 'hitl' and any('sudah dipakai' in r for r in out['reasons'])


# ── DROP ────────────────────────────────────────────────────────────────────────
def test_drop_literal_value_is_automatic_and_deprecates_unused_predicate(api, obdf):
    out = impact(api, obdf, operation='drop', source='kemensos', schema='public',
                 table='penerima_manfaat', column='status_ekonomi')
    assert out['decision'] == 'auto' and out['pattern'] == 'P-002'
    operations = [(a['artifact'], a['operation']) for a in out['actions']]
    assert ('vdb', 'drop_column') in operations
    assert ('r2rml', 'remove_predicate_object_map') in operations
    assert ('ontology', 'deprecate_property') in operations


def test_drop_shared_predicate_keeps_property(api, obdf):
    """bansos:nik dipakai dua TriplesMap; property tidak boleh di-deprecate."""
    out = impact(api, obdf, operation='drop', source='kemensos', schema='public',
                 table='penerima_manfaat', column='nik')
    keep = [a for a in out['actions'] if a['operation'] == 'keep_property']
    assert keep and keep[0]['predicate_iri'].endswith('/nik')
    assert not [a for a in out['actions'] if a['operation'] == 'deprecate_property']


def test_drop_primary_key_or_iri_template_needs_hitl(api, obdf):
    out = impact(api, obdf, operation='drop', source='dukcapil', table='master_penduduk', column='nik')
    assert out['decision'] == 'hitl'
    assert any('kunci primer' in r for r in out['reasons'])
    assert any('iri_template' in r for r in out['reasons'])


def test_drop_column_used_by_view_predicate_needs_hitl(api, obdf):
    """tanggal_lahir hanya dipakai WHERE di dalam view yang dibaca MapView (bukti F0.7)."""
    out = impact(api, obdf, operation='drop', source='dukcapil', table='master_penduduk',
                 column='tgl_lahir_ktp')
    assert out['decision'] == 'hitl'
    assert any('MapView' in r and 'sql_predicate' in r for r in out['reasons'])


def test_drop_column_reached_through_expression_needs_hitl(api, obdf):
    """nik_upper adalah ekspresi atas nik; jalurnya ditandai expression."""
    with api.factory() as db:
        rows = db.execute(select(spec.ColumnUsage.weakest_link)
                          .where(spec.ColumnUsage.role == 'literal_value')).scalars().all()
    assert 'expression' in rows
    out = impact(api, obdf, operation='drop', source='dukcapil', table='master_penduduk', column='nik')
    assert any('expression' in r for r in out['reasons'])


# ── ADD ─────────────────────────────────────────────────────────────────────────
def test_add_column_is_automatic_with_generated_property(api, obdf):
    out = impact(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat_lengkap', column_type='varchar(200)')
    assert out['decision'] == 'auto' and out['pattern'] == 'P-001'
    ontology = [a for a in out['actions'] if a['artifact'] == 'ontology'][0]
    assert ontology['iri'] == 'http://bansos.go.id/ontology/alamatLengkap'
    assert ontology['domain'] == ['http://bansos.go.id/ontology/PenerimaBansos']
    assert [a for a in out['actions'] if a['artifact'] == 'r2rml'][0]['column'] == 'alamat_lengkap'


def test_add_respects_policy(api, obdf):
    api.put(f'/api/v1/obdf/{obdf}/settings/adaptation.add_column', json={'value': {'mode': 'hitl'}})
    out = impact(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')
    assert out['decision'] == 'hitl'
    assert any('mewajibkan persetujuan' in r for r in out['reasons'])


def test_hitl_add_plan_is_complete(api, obdf):
    """Regresi: kebijakan HITL memotong analisis sehingga rencana hanya berisi tindakan VDB;
    setelah disetujui, property dan pemetaan tidak pernah dibuat."""
    api.put(f'/api/v1/obdf/{obdf}/settings/adaptation.add_column', json={'value': {'mode': 'hitl'}})
    out = impact(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')
    lapisan = {(a['artifact'], a['operation']) for a in out['actions']}
    assert lapisan >= {('vdb', 'add_column'), ('ontology', 'add_datatype_property'),
                       ('r2rml', 'add_predicate_object_map')}
    assert out['decision'] == 'hitl' and out['pattern'] == 'P-001'


def test_add_existing_column_name_needs_hitl(api, obdf):
    out = impact(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='nik', column_type='varchar(16)')
    assert out['decision'] == 'hitl' and any('sudah ada' in r for r in out['reasons'])


def test_add_with_conflicting_property_domain(api, obdf):
    """bansos:nik berdomain Penduduk; memakainya untuk PenerimaBansos akan menyesatkan inferensi."""
    # kebijakan: tambahkan kualifikasi kelas pada IRI
    api.put(f'/api/v1/obdf/{obdf}/naming-policy',
            json={'property_iri_template': '{namespace}nik', 'on_collision': 'qualify_with_class',
                  'namespace': 'http://bansos.go.id/ontology/'})
    out = impact(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='nik_baru', column_type='varchar(16)')
    assert out['decision'] == 'auto'
    iri = [a for a in out['actions'] if a['artifact'] == 'ontology'][0]['iri']
    assert iri.endswith('_PenerimaBansos') and any('domain berbeda' in r for r in out['reasons'])

    # kebijakan: serahkan ke administrator
    api.put(f'/api/v1/obdf/{obdf}/naming-policy',
            json={'property_iri_template': '{namespace}nik', 'on_collision': 'hitl',
                  'namespace': 'http://bansos.go.id/ontology/'})
    out = impact(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='nik_baru', column_type='varchar(16)')
    assert out['decision'] == 'hitl' and any('domain berbeda' in r for r in out['reasons'])


# ── kasus yang diabaikan ────────────────────────────────────────────────────────
@pytest.mark.parametrize('body, alasan', [
    ({'operation': 'drop', 'source': 'tidak-ada', 'table': 'x', 'column': 'y'}, 'tidak terdaftar'),
    ({'operation': 'drop', 'source': 'dukcapil', 'table': 'tabel_lain', 'column': 'y'},
     'tidak ditemukan'),
    ({'operation': 'add', 'source': 'dukcapil', 'table': 'tabel_lain', 'column': 'y'},
     'tidak difederasikan'),
])
def test_unknown_objects_are_ignored(api, obdf, body, alasan):
    out = impact(api, obdf, **body)
    assert out['decision'] == 'ignored' and alasan in ' '.join(out['reasons'])


def test_unparsable_sql_forces_hitl(api, obdf):
    """SQL logical table yang tidak dapat diurai membuat seluruh keputusan DROP konservatif."""
    broken = fx.MAPPING_TTL.replace('SELECT * FROM kemensos.penerima_manfaat', 'BUKAN SQL ###')
    set_clients(api, agent=fx.FakeAgent(mapping=broken))
    assert api.post(f'/api/v1/obdf/{obdf}/sync').json()['changed']
    out = impact(api, obdf, operation='drop', source='kemensos', schema='public',
                 table='penerima_manfaat', column='status_ekonomi')
    assert out['decision'] == 'hitl' and any('tidak dapat diurai' in r for r in out['reasons'])


# ── pemilihan TriplesMap untuk kolom baru (regresi F4c) ─────────────────────────
def test_add_targets_only_triples_maps_that_expose_new_columns(api, obdf):
    """Regresi: pemetaan sempat ditempelkan ke TriplesMap dengan SELECT eksplisit,
    sehingga `ontop validate` menolak artefak."""
    out = impact(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')
    assert out['decision'] == 'auto'
    sasaran = {a['triples_map_iri'].rsplit('#', 1)[-1] for a in out['actions']
               if a['artifact'] == 'r2rml'}
    assert sasaran == {'MapPenerima', 'MapRingkas'}       # rr:tableName dan SELECT *
    assert 'MapLinkPenerima' not in sasaran               # SELECT dengan daftar kolom eksplisit


def test_add_is_hitl_when_no_mapping_exposes_new_columns(api, obdf):
    broken = fx.MAPPING_TTL.replace('SELECT * FROM kemensos.penerima_manfaat',
                                    'SELECT penerima_id, nik FROM kemensos.penerima_manfaat')
    broken = broken.replace('rr:logicalTable [ rr:tableName "kemensos.penerima_manfaat" ]',
                            'rr:logicalTable [ rr:sqlQuery "SELECT penerima_id FROM '
                            'kemensos.penerima_manfaat" ]')
    set_clients(api, agent=fx.FakeAgent(mapping=broken))
    api.post(f'/api/v1/obdf/{obdf}/sync')
    out = impact(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')
    assert out['decision'] == 'hitl'
    assert any('daftar kolom eksplisit' in r for r in out['reasons'])


def test_add_refuses_iri_already_used_as_object_property(api, obdf):
    """Nama yang dipakai object property tidak boleh jadi data property (punning)."""
    api.put(f'/api/v1/obdf/{obdf}/naming-policy',
            json={'property_iri_template': '{namespace}memilikDataKependudukan',
                  'on_collision': 'hitl', 'namespace': 'http://bansos.go.id/ontology/'})
    out = impact(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')
    assert out['decision'] == 'hitl'
    assert any('object_property' in r for r in out['reasons'])

    api.put(f'/api/v1/obdf/{obdf}/naming-policy',
            json={'property_iri_template': '{namespace}memilikDataKependudukan',
                  'on_collision': 'qualify_with_class', 'namespace': 'http://bansos.go.id/ontology/'})
    out = impact(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')
    assert out['decision'] == 'auto'
    iri = [a for a in out['actions'] if a['artifact'] == 'ontology'][0]['iri']
    assert iri.endswith('_PenerimaBansos')


def test_drop_action_names_the_triples_map(api, obdf):
    out = impact(api, obdf, operation='drop', source='kemensos', schema='public',
                 table='penerima_manfaat', column='status_ekonomi')
    hapus = [a for a in out['actions'] if a['operation'] == 'remove_predicate_object_map']
    assert hapus and all(a['triples_map_iri'].rsplit('#', 1)[-1] in ('MapPenerima', 'MapRingkas')
                         for a in hapus)


def test_drop_rewrites_explicit_logical_table(api, obdf):
    """Regresi A002: kolom yang disebut eksplisit pada rr:sqlQuery harus ikut dikeluarkan."""
    out = impact(api, obdf, operation='drop', source='dukcapil', table='master_penduduk',
                 column='tgl_lahir_ktp')
    tulis_ulang = [a for a in out['actions'] if a['operation'] == 'rewrite_logical_table']
    assert tulis_ulang and tulis_ulang[0]['column'] == 'tanggal_lahir'
    assert tulis_ulang[0]['triples_map_iri'].endswith('#MapPenduduk')


def test_drop_of_star_column_needs_no_rewrite(api, obdf):
    out = impact(api, obdf, operation='drop', source='kemensos', schema='public',
                 table='penerima_manfaat', column='status_ekonomi')
    assert not [a for a in out['actions'] if a['operation'] == 'rewrite_logical_table']
