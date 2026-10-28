"""Case-study views (scenarios A007-A009) over the REAL artifacts in the repository (ADR-0023).

The VDB, mapping, and ontology are read from setup/; the Teiid runtime metadata (SYS.*,
SYSADMIN.Views, SYSADMIN.Usage) is built from that VDB following the semantics observed in F0.7
(ADR-0006): column-level rows for the source of every view column, and object-level rows for
every column referred to anywhere in the view definition. These tests pin the decisions and
plans of ASCAM for the three scenarios, and check that scenarios A001-A006 do not change because
views were added. That Teiid itself accepts the statements is tested separately
(archive/experiments/f0/f0_8.sh).
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
TYPES = {'varchar': 'string', 'integer': 'integer', 'date': 'date', 'timestamp': 'timestamp',
        'boolean': 'boolean'}
B = 'http://bansos.go.id/ontology/'


def _ddl_models() -> list[tuple[str, bool, str]]:
    out = []
    for model in minidom.parseString(VDB_XML).getElementsByTagName('model'):
        text = ''.join(n.data for m in model.getElementsByTagName('metadata') for n in m.childNodes
                       if n.nodeType in (n.CDATA_SECTION_NODE, n.TEXT_NODE))
        out.append((model.getAttribute('name'),
                    (model.getAttribute('type') or 'PHYSICAL').upper() != 'VIRTUAL', text))
    return out


def case_study_metadata() -> dict:
    tables, columns, keys, views, usage = [], [], [], [], []
    uid = {}

    def new_uid(key, prefix):
        uid[key] = f'{prefix}{len(uid) + 1}'
        return uid[key]

    for model, physical, ddl in _ddl_models():
        if physical:
            for name, body_text in re.findall(r'CREATE FOREIGN TABLE (\w+)\s*\((.*?)\)\s*OPTIONS', ddl, re.S):
                tables.append({'SchemaName': model, 'Name': name, 'Type': 'Table', 'IsPhysical': True,
                               'NameInSource': f'`{model}`.`{name}`' if model == 'dukcapil' else None,
                               'UID': new_uid((model, name), 't')})
                for position, line in enumerate([b.strip() for b in body_text.split(',') if b.strip()], 1):
                    column, sql_type = line.split()[:2]
                    columns.append({'SchemaName': model, 'TableName': name, 'Name': column,
                                    'Position': position, 'NameInSource': None,
                                    'DataType': TYPES[re.sub(r'\(.*', '', sql_type)],
                                    'NullType': 'No Nulls' if 'not null' in line else 'Nullable',
                                    'Length': 0, 'Precision': 0, 'Scale': 0,
                                    'UID': new_uid((model, name, column), 'c')})
                    if 'primary key' in line:
                        keys.append({'SchemaName': model, 'TableName': name, 'Name': column,
                                     'KeyName': 'PK', 'KeyType': 'Primary', 'Position': 1})
            continue
        for name, body in re.findall(r'CREATE VIEW (\w+) AS\s+(.*?);', ddl, re.S):
            body = ' '.join(body.split())
            views.append({'SchemaName': model, 'Name': name, 'Body': body})
            table_uid = new_uid((model, name), 't')
            tables.append({'SchemaName': model, 'Name': name, 'Type': 'Table', 'IsPhysical': False,
                           'NameInSource': None, 'UID': table_uid})
            tree = sqlglot.parse_one(body)
            source_table = tree.find(exp.Table)
            src_model, src_table = source_table.db, source_table.name
            referenced = set()
            for position, item in enumerate(tree.expressions, 1):
                column = item.alias_or_name
                columns.append({'SchemaName': model, 'TableName': name, 'Name': column,
                                'Position': position, 'NameInSource': None, 'DataType': 'string',
                                'NullType': 'Nullable', 'Length': 0, 'Precision': 0, 'Scale': 0,
                                'UID': new_uid((model, name, column), 'c')})
                for c in item.find_all(exp.Column):
                    referenced.add(c.name)
                    usage.append(_usage(uid[(model, name, column)], model, name, column,
                                        uid[(src_model, src_table, c.name)], src_model, src_table, c.name))
            for c in tree.args['where'].find_all(exp.Column) if tree.args.get('where') else []:
                referenced.add(c.name)
            usage.append(_usage(table_uid, model, name, None, uid[(src_model, src_table)],
                                src_model, src_table, None))
            for c in sorted(referenced):
                usage.append(_usage(table_uid, model, name, None, uid[(src_model, src_table, c)],
                                    src_model, src_table, c))
    return {
        'virtual_databases': [{'Name': 'government', 'Version': '1',
                               'LoadingTimestamp': datetime(2026, 10, 9, 1, 0),
                               'ActiveTimestamp': datetime(2026, 10, 9, 1, 0, 1)}],
        'schemas': [{'Name': m, 'IsPhysical': f} for m, f, _ in _ddl_models()],
        'tables': tables, 'columns': columns, 'keys': keys, 'views': views, 'usage': usage,
        'matviews': [], 'procedures': [], 'triggers': [],
    }


def _usage(uid, schema, name, element, uses_uid, uses_schema, uses_name, uses_element):
    return {'UID': uid, 'object_type': 'Column' if element else 'View', 'SchemaName': schema,
            'Name': name, 'ElementName': element, 'Uses_UID': uses_uid,
            'Uses_object_type': 'Column' if uses_element else 'Table', 'Uses_SchemaName': uses_schema,
            'Uses_Name': uses_name, 'Uses_ElementName': uses_element}


def _vdb_info() -> dict:
    """get-vdb with the virtual model `layanan` in place of model `v` of the common fixture."""
    info = fx.vdb_info()
    for model in info['models']:
        if model['model-name'] == 'v':
            model['model-name'] = 'layanan'
    return info


@pytest.fixture
def case_study(api):
    oid = new_obdf_with_sources(api)
    set_clients(api, meta=fx.FakeMetadata(case_study_metadata()),
                admin=fx.FakeAdmin(info=_vdb_info(), content=VDB_XML.encode()),
                agent=fx.FakeAgent(mapping=MAPPING, ontology=ONTOLOGY))
    api.put(f'/api/v1/obdf/{oid}/naming-policy',
            json={'property_iri_template': '{namespace}{column_camel}',
                  'on_collision': 'qualify_with_class', 'namespace': B})
    r = api.post(f'/api/v1/obdf/{oid}/sync')
    assert r.status_code == 200, r.text
    return oid


def actions_of(out, operation):
    return [a for a in out['actions'] if a['operation'] == operation]


def local_part(iri):
    return iri.rsplit('#', 1)[-1]


def test_a007_drop_through_passthrough_view_postgresql(api, case_study):
    out = impact(api, case_study, operation='drop', source='kemensos', table='penerima_manfaat',
                 column='no_kartu_keluarga')
    assert out['decision'] == 'auto', out['reasons']
    assert [(a['model'], a['table'], a['body']) for a in actions_of(out, 'alter_view')] == [
        ('layanan', 'v_penerima_aktif',
         'SELECT penerima_id FROM kemensos.penerima_manfaat WHERE aktif = TRUE')]
    assert [local_part(a['triples_map_iri']) for a in actions_of(out, 'rewrite_logical_table')] == ['MapPenerimaAktif']
    assert {(local_part(a['triples_map_iri']), a['predicate_iri'])
            for a in actions_of(out, 'remove_predicate_object_map')} == {
        ('MapPenerimaAktif', B + 'noKartuKeluarga')}
    assert [a['predicate_iri'] for a in actions_of(out, 'deprecate_property')] == [B + 'noKartuKeluarga']


def test_a008_drop_through_aliased_view_mysql(api, case_study):
    out = impact(api, case_study, operation='drop', source='dukcapil', table='riwayat_perubahan_data',
                 column='diubah_oleh')
    assert out['decision'] == 'auto', out['reasons']
    assert [(a['table'], a['body'], a['removed_columns']) for a in actions_of(out, 'alter_view')] == [
        ('v_riwayat_perubahan', 'SELECT riwayat_id, kolom_diubah FROM dukcapil.riwayat_perubahan_data',
         ['petugas'])]
    assert actions_of(out, 'rewrite_logical_table') == []          # SELECT * over a view
    assert {local_part(a['triples_map_iri']) for a in actions_of(out, 'remove_predicate_object_map')} == {
        'MapRiwayat'}
    assert [a['predicate_iri'] for a in actions_of(out, 'deprecate_property')] == [B + 'diubahOleh']


def test_a009_drop_used_in_view_expression_is_notified(api, case_study):
    out = impact(api, case_study, operation='drop', source='kemensos', table='program_bansos',
                 column='periode_selesai')
    assert out['decision'] == 'notify' and out['actions'] == []
    assert any('expression' in r and 'layanan.v_program_berakhir' in r for r in out['reasons'])
    # the 𝒟(c, ℱ) reported to the administrator includes the affected view and property
    assert 'layanan.v_program_berakhir' in out['affected']['views']
    assert B + 'tahunBerakhir' in out['affected']['predicates']


@pytest.mark.parametrize('source, table, column', [
    ('kemensos', 'program_bansos', 'tipe_program'),             # A002
    ('dukcapil', 'master_penduduk', 'status_hidup'),            # A005
])
def test_existing_drop_scenarios_do_not_touch_views(api, case_study, source, table, column):
    out = impact(api, case_study, operation='drop', source=source, table=table, column=column)
    assert out['decision'] == 'auto', out['reasons']
    assert actions_of(out, 'alter_view') == []


def test_where_column_of_view_is_still_notified(api, case_study):
    out = impact(api, case_study, operation='drop', source='kemensos', table='penerima_manfaat',
                 column='aktif')
    assert out['decision'] == 'notify'
    assert any('layanan.v_penerima_aktif' in r for r in out['reasons'])


def test_real_artifacts_sync_without_errors(api, case_study):
    version = api.get(f'/api/v1/obdf/{case_study}/versions?limit=1').json()[0]
    detail = api.get(f"/api/v1/versions/{version['id']}").json()
    assert [i for i in detail['issues'] if i['severity'] == 'error'] == []
    # the three properties through views are known to the ontology (no vocabulary warnings)
    messages = ' '.join(i['message'] for i in detail['issues'])
    for name in ('noKartuKeluarga', 'diubahOleh', 'kolomDiubah', 'tahunBerakhir', 'PenerimaAktif'):
        assert name not in messages, detail['issues']
