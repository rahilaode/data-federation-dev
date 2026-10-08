"""Studi kasus view (skenario A007-A009) atas artefak NYATA di repositori (ADR-0023).

VDB, mapping, dan ontologi dibaca dari setup/; metadata runtime Teiid (SYS.*, SYSADMIN.Views,
SYSADMIN.Usage) disusun dari VDB itu mengikuti semantik yang teramati pada F0.7 (ADR-0006):
baris tingkat kolom untuk sumber setiap kolom view, dan baris tingkat objek untuk setiap kolom
yang dirujuk di mana pun dalam definisi view. Uji ini memastikan keputusan dan rencana ASCAM
untuk ketiga skenario, serta bahwa skenario A001-A006 tidak berubah karena view ditambahkan.
Penerimaan pernyataan oleh Teiid sendiri diuji terpisah (experiments/f0/f0_8.sh).
"""
import pathlib
import re
from datetime import datetime
from xml.dom import minidom

import pytest
import sqlglot
from sqlglot import exp

import fixtures_obdf as fx
from test_impact import impact
from test_sync import new_obdf_with_sources, set_clients

REPO = pathlib.Path(__file__).resolve().parents[5]
VDB_XML = (REPO / 'setup/data-federation/deployments/government-vdb.xml').read_text()
MAPPING = (REPO / 'setup/vkg-system/config/mapping.ttl').read_text()
ONTOLOGY = (REPO / 'setup/vkg-system/config/ontology_file.ttl').read_text()
TIPE = {'varchar': 'string', 'integer': 'integer', 'date': 'date', 'timestamp': 'timestamp',
        'boolean': 'boolean'}
B = 'http://bansos.go.id/ontology/'


def _ddl_models() -> list[tuple[str, bool, str]]:
    out = []
    for model in minidom.parseString(VDB_XML).getElementsByTagName('model'):
        teks = ''.join(n.data for m in model.getElementsByTagName('metadata') for n in m.childNodes
                       if n.nodeType in (n.CDATA_SECTION_NODE, n.TEXT_NODE))
        out.append((model.getAttribute('name'),
                    (model.getAttribute('type') or 'PHYSICAL').upper() != 'VIRTUAL', teks))
    return out


def metadata_studi_kasus() -> dict:
    tables, columns, keys, views, usage = [], [], [], [], []
    uid = {}

    def baru(kunci, awalan):
        uid[kunci] = f'{awalan}{len(uid) + 1}'
        return uid[kunci]

    for model, fisik, ddl in _ddl_models():
        if fisik:
            for nama, isi in re.findall(r'CREATE FOREIGN TABLE (\w+)\s*\((.*?)\)\s*OPTIONS', ddl, re.S):
                tables.append({'SchemaName': model, 'Name': nama, 'Type': 'Table', 'IsPhysical': True,
                               'NameInSource': f'`{model}`.`{nama}`' if model == 'dukcapil' else None,
                               'UID': baru((model, nama), 't')})
                for posisi, baris in enumerate([b.strip() for b in isi.split(',') if b.strip()], 1):
                    kolom, tipe = baris.split()[:2]
                    columns.append({'SchemaName': model, 'TableName': nama, 'Name': kolom,
                                    'Position': posisi, 'NameInSource': None,
                                    'DataType': TIPE[re.sub(r'\(.*', '', tipe)],
                                    'NullType': 'No Nulls' if 'not null' in baris else 'Nullable',
                                    'Length': 0, 'Precision': 0, 'Scale': 0,
                                    'UID': baru((model, nama, kolom), 'c')})
                    if 'primary key' in baris:
                        keys.append({'SchemaName': model, 'TableName': nama, 'Name': kolom,
                                     'KeyName': 'PK', 'KeyType': 'Primary', 'Position': 1})
            continue
        for nama, body in re.findall(r'CREATE VIEW (\w+) AS\s+(.*?);', ddl, re.S):
            body = ' '.join(body.split())
            views.append({'SchemaName': model, 'Name': nama, 'Body': body})
            tabel_uid = baru((model, nama), 't')
            tables.append({'SchemaName': model, 'Name': nama, 'Type': 'Table', 'IsPhysical': False,
                           'NameInSource': None, 'UID': tabel_uid})
            pohon = sqlglot.parse_one(body)
            sumber = pohon.find(exp.Table)
            s_model, s_tabel = sumber.db, sumber.name
            dirujuk = set()
            for posisi, item in enumerate(pohon.expressions, 1):
                kolom = item.alias_or_name
                columns.append({'SchemaName': model, 'TableName': nama, 'Name': kolom,
                                'Position': posisi, 'NameInSource': None, 'DataType': 'string',
                                'NullType': 'Nullable', 'Length': 0, 'Precision': 0, 'Scale': 0,
                                'UID': baru((model, nama, kolom), 'c')})
                for c in item.find_all(exp.Column):
                    dirujuk.add(c.name)
                    usage.append(_pakai(uid[(model, nama, kolom)], model, nama, kolom,
                                        uid[(s_model, s_tabel, c.name)], s_model, s_tabel, c.name))
            for c in pohon.args['where'].find_all(exp.Column) if pohon.args.get('where') else []:
                dirujuk.add(c.name)
            usage.append(_pakai(tabel_uid, model, nama, None, uid[(s_model, s_tabel)],
                                s_model, s_tabel, None))
            for c in sorted(dirujuk):
                usage.append(_pakai(tabel_uid, model, nama, None, uid[(s_model, s_tabel, c)],
                                    s_model, s_tabel, c))
    return {
        'virtual_databases': [{'Name': 'government', 'Version': '1',
                               'LoadingTimestamp': datetime(2026, 10, 9, 1, 0),
                               'ActiveTimestamp': datetime(2026, 10, 9, 1, 0, 1)}],
        'schemas': [{'Name': m, 'IsPhysical': f} for m, f, _ in _ddl_models()],
        'tables': tables, 'columns': columns, 'keys': keys, 'views': views, 'usage': usage,
        'matviews': [], 'procedures': [], 'triggers': [],
    }


def _pakai(uid, schema, name, element, uses_uid, uses_schema, uses_name, uses_element):
    return {'UID': uid, 'object_type': 'Column' if element else 'View', 'SchemaName': schema,
            'Name': name, 'ElementName': element, 'Uses_UID': uses_uid,
            'Uses_object_type': 'Column' if uses_element else 'Table', 'Uses_SchemaName': uses_schema,
            'Uses_Name': uses_name, 'Uses_ElementName': uses_element}


def _vdb_info() -> dict:
    """get-vdb dengan model virtual `layanan` menggantikan model `v` milik fixture umum."""
    info = fx.vdb_info()
    for model in info['models']:
        if model['model-name'] == 'v':
            model['model-name'] = 'layanan'
    return info


@pytest.fixture
def studi(api):
    oid = new_obdf_with_sources(api)
    set_clients(api, meta=fx.FakeMetadata(metadata_studi_kasus()),
                admin=fx.FakeAdmin(info=_vdb_info(), content=VDB_XML.encode()),
                agent=fx.FakeAgent(mapping=MAPPING, ontology=ONTOLOGY))
    api.put(f'/api/v1/obdf/{oid}/naming-policy',
            json={'property_iri_template': '{namespace}{column_camel}',
                  'on_collision': 'qualify_with_class', 'namespace': B})
    r = api.post(f'/api/v1/obdf/{oid}/sync')
    assert r.status_code == 200, r.text
    return oid


def tindakan(out, operation):
    return [a for a in out['actions'] if a['operation'] == operation]


def ujung(iri):
    return iri.rsplit('#', 1)[-1]


def test_a007_drop_through_passthrough_view_postgresql(api, studi):
    out = impact(api, studi, operation='drop', source='kemensos', table='penerima_manfaat',
                 column='no_kartu_keluarga')
    assert out['decision'] == 'auto', out['reasons']
    assert [(a['model'], a['table'], a['body']) for a in tindakan(out, 'alter_view')] == [
        ('layanan', 'v_penerima_aktif',
         'SELECT penerima_id FROM kemensos.penerima_manfaat WHERE aktif = TRUE')]
    assert [ujung(a['triples_map_iri']) for a in tindakan(out, 'rewrite_logical_table')] == ['MapPenerimaAktif']
    assert {(ujung(a['triples_map_iri']), a['predicate_iri'])
            for a in tindakan(out, 'remove_predicate_object_map')} == {
        ('MapPenerimaAktif', B + 'noKartuKeluarga')}
    assert [a['predicate_iri'] for a in tindakan(out, 'deprecate_property')] == [B + 'noKartuKeluarga']


def test_a008_drop_through_aliased_view_mysql(api, studi):
    out = impact(api, studi, operation='drop', source='dukcapil', table='master_penduduk',
                 column='created_at')
    assert out['decision'] == 'auto', out['reasons']
    assert [(a['table'], a['body'], a['removed_columns']) for a in tindakan(out, 'alter_view')] == [
        ('v_penduduk_tercatat', 'SELECT nik FROM dukcapil.master_penduduk', ['waktu_pencatatan'])]
    assert tindakan(out, 'rewrite_logical_table') == []          # SELECT * atas view
    assert {ujung(a['triples_map_iri']) for a in tindakan(out, 'remove_predicate_object_map')} == {
        'MapPendudukTercatat'}
    assert [a['predicate_iri'] for a in tindakan(out, 'deprecate_property')] == [B + 'createdAt']


def test_a009_drop_used_in_view_expression_needs_hitl(api, studi):
    out = impact(api, studi, operation='drop', source='kemensos', table='program_bansos',
                 column='periode_selesai')
    assert out['decision'] == 'hitl'
    assert any('ekspresi' in r and 'layanan.v_program_berakhir' in r for r in out['reasons'])
    assert tindakan(out, 'alter_view') == []


@pytest.mark.parametrize('source, table, column', [
    ('kemensos', 'program_bansos', 'tipe_program'),             # A002
    ('dukcapil', 'master_penduduk', 'status_hidup'),            # A005
])
def test_existing_drop_scenarios_do_not_touch_views(api, studi, source, table, column):
    out = impact(api, studi, operation='drop', source=source, table=table, column=column)
    assert out['decision'] == 'auto', out['reasons']
    assert tindakan(out, 'alter_view') == []


def test_where_column_of_view_still_needs_hitl(api, studi):
    out = impact(api, studi, operation='drop', source='kemensos', table='penerima_manfaat',
                 column='aktif')
    assert out['decision'] == 'hitl'
    assert any('layanan.v_penerima_aktif' in r for r in out['reasons'])


def test_real_artifacts_sync_without_errors(api, studi):
    versi = api.get(f'/api/v1/obdf/{studi}/versions?limit=1').json()[0]
    detail = api.get(f"/api/v1/versions/{versi['id']}").json()
    assert [i for i in detail['issues'] if i['severity'] == 'error'] == []
    # ketiga property lewat view dikenali ontologi (tanpa peringatan kosakata)
    pesan = ' '.join(i['message'] for i in detail['issues'])
    for nama in ('noKartuKeluarga', 'createdAt', 'tahunBerakhir', 'PenerimaAktif'):
        assert nama not in pesan, detail['issues']
