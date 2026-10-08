"""Penyesuaian view Teiid pada DROP (ADR-0023)."""
import copy

import pytest

import fixtures_obdf as fx
from ascam_knowledge.views import RewriteError, inline_columns, remove_projection
from test_impact import impact
from test_sync import new_obdf_with_sources, set_clients

MAPPING = fx.MAPPING_TTL + """
<#MapProfil> a rr:TriplesMap ;
    rr:logicalTable [ rr:tableName "v.profil_ringkas" ] ;
    rr:subjectMap [ rr:template "http://bansos.go.id/resource/profil/{nik}" ] ;
    rr:predicateObjectMap [ rr:predicate bansos:pekerjaan ; rr:objectMap [ rr:column "pekerjaan" ] ] .

<#MapProfilSql> a rr:TriplesMap ;
    rr:logicalTable [ rr:sqlQuery "SELECT nik, pekerjaan FROM v.profil" ] ;
    rr:subjectMap [ rr:template "http://bansos.go.id/resource/profil2/{nik}" ] ;
    rr:predicateObjectMap [ rr:predicate bansos:pekerjaan ; rr:objectMap [ rr:column "pekerjaan" ] ] .
"""
ONTOLOGY = fx.ONTOLOGY_TTL + 'bansos:pekerjaan a owl:DatatypeProperty ; rdfs:range xsd:string .\n'


def kolom(schema, table, name, uid, position, physical=True):
    return {'SchemaName': schema, 'TableName': table, 'Name': name, 'Position': position,
            'NameInSource': None, 'DataType': 'string', 'NullType': 'Nullable', 'Length': 50,
            'Precision': 0, 'Scale': 0, 'UID': uid}


def pakai(uid, schema, name, element, uses_uid, uses_schema, uses_name, uses_element):
    return {'UID': uid, 'object_type': 'Column' if element else 'View', 'SchemaName': schema,
            'Name': name, 'ElementName': element, 'Uses_UID': uses_uid,
            'Uses_object_type': 'Column' if uses_element else 'Table', 'Uses_SchemaName': uses_schema,
            'Uses_Name': uses_name, 'Uses_ElementName': uses_element}


def metadata_view():
    meta = copy.deepcopy(fx.metadata())
    meta['columns'] += [
        kolom('dukcapil', 'master_penduduk', 'pekerjaan', 'c5', 3),
        kolom('dukcapil', 'master_penduduk', 'alamat', 'c6', 4),
        kolom('v', 'profil', 'nik', 'c7', 1), kolom('v', 'profil', 'pekerjaan', 'c8', 2),
        kolom('v', 'profil', 'alamat', 'c9', 3),
        kolom('v', 'profil_ringkas', 'nik', 'cv4', 1), kolom('v', 'profil_ringkas', 'pekerjaan', 'cv5', 2),
        kolom('v', 'label', 'nik', 'cv6', 1), kolom('v', 'label', 'label', 'cv7', 2),
    ]
    meta['tables'] += [
        {'SchemaName': 'v', 'Name': n, 'Type': 'Table', 'NameInSource': None, 'IsPhysical': False,
         'UID': uid} for n, uid in (('profil', 't4'), ('profil_ringkas', 't5'), ('label', 't6'))]
    meta['views'] += [
        {'SchemaName': 'v', 'Name': 'profil',
         'Body': 'SELECT p.nik, p.pekerjaan, p.alamat FROM dukcapil.master_penduduk AS p'},
        {'SchemaName': 'v', 'Name': 'profil_ringkas', 'Body': 'SELECT r.nik, r.pekerjaan FROM v.profil AS r'},
        {'SchemaName': 'v', 'Name': 'label',
         'Body': 'SELECT p.nik, CONCAT(p.nik, p.alamat) AS label FROM dukcapil.master_penduduk AS p'},
    ]
    mp = ('dukcapil', 'master_penduduk')
    meta['usage'] += [
        pakai('c7', 'v', 'profil', 'nik', 'c1', *mp, 'nik'),
        pakai('c8', 'v', 'profil', 'pekerjaan', 'c5', *mp, 'pekerjaan'),
        pakai('c9', 'v', 'profil', 'alamat', 'c6', *mp, 'alamat'),
        pakai('cv4', 'v', 'profil_ringkas', 'nik', 'c7', 'v', 'profil', 'nik'),
        pakai('cv5', 'v', 'profil_ringkas', 'pekerjaan', 'c8', 'v', 'profil', 'pekerjaan'),
        pakai('cv6', 'v', 'label', 'nik', 'c1', *mp, 'nik'),
        pakai('cv7', 'v', 'label', 'label', 'c1', *mp, 'nik'),
        pakai('cv7', 'v', 'label', 'label', 'c6', *mp, 'alamat'),
    ]
    return meta


def siapkan(api, vdb_xml=None):
    oid = new_obdf_with_sources(api)
    admin = fx.FakeAdmin(content=vdb_xml.encode()) if vdb_xml else None
    set_clients(api, meta=fx.FakeMetadata(metadata_view()), admin=admin,
                agent=fx.FakeAgent(mapping=MAPPING, ontology=ONTOLOGY))
    api.put(f'/api/v1/obdf/{oid}/naming-policy',
            json={'property_iri_template': '{namespace}{column_camel}',
                  'on_collision': 'qualify_with_class', 'namespace': 'http://bansos.go.id/ontology/'})
    assert api.post(f'/api/v1/obdf/{oid}/sync').status_code == 200
    return oid


def tindakan(out, operation):
    return [a for a in out['actions'] if a['operation'] == operation]


def test_drop_through_passthrough_views_is_automatic_and_rewrites_views(api):
    oid = siapkan(api)
    out = impact(api, oid, operation='drop', source='dukcapil', table='master_penduduk',
                 column='pekerjaan')
    assert out['decision'] == 'auto', out['reasons']
    views = {(a['model'], a['table']): a for a in tindakan(out, 'alter_view')}
    assert views[('v', 'profil')]['body'] == 'SELECT p.nik, p.alamat FROM dukcapil.master_penduduk AS p'
    # perambatan ke view bertingkat yang membaca v.profil
    assert views[('v', 'profil_ringkas')]['body'] == 'SELECT r.nik FROM v.profil AS r'
    assert ('v', 'label') not in views                       # tidak memakai pekerjaan
    # mapping: proyeksi eksplisit atas kolom VIEW ikut ditulis ulang, POM dihapus, property usang
    assert tindakan(out, 'rewrite_logical_table')[0]['triples_map_iri'].endswith('MapProfilSql')
    assert {a['triples_map_iri'].rsplit('#', 1)[-1]
            for a in tindakan(out, 'remove_predicate_object_map')} == {'MapProfil', 'MapProfilSql'}
    assert tindakan(out, 'deprecate_property')[0]['predicate_iri'].endswith('pekerjaan')
    assert tindakan(out, 'drop_column')[0]['column'] == 'pekerjaan'


def test_drop_used_in_view_expression_needs_hitl(api):
    oid = siapkan(api)
    out = impact(api, oid, operation='drop', source='dukcapil', table='master_penduduk',
                 column='alamat')
    assert out['decision'] == 'hitl'
    assert any('ekspresi' in r and 'v.label' in r for r in out['reasons'])


def test_drop_through_view_with_inline_columns_needs_hitl(api):
    xml = ('<?xml version="1.0"?><vdb name="government" version="1"><model name="v" type="VIRTUAL">'
           '<metadata type="DDL"><![CDATA[CREATE VIEW profil (nik string, pekerjaan string, '
           'alamat string) AS SELECT p.nik, p.pekerjaan, p.alamat FROM dukcapil.master_penduduk AS p;'
           ']]></metadata></model></vdb>')
    oid = siapkan(api, vdb_xml=xml)
    out = impact(api, oid, operation='drop', source='dukcapil', table='master_penduduk',
                 column='pekerjaan')
    assert out['decision'] == 'hitl'
    assert any('inline' in r and 'v.profil' in r for r in out['reasons'])


def test_existing_view_predicate_case_still_needs_hitl(api):
    oid = siapkan(api)
    out = impact(api, oid, operation='drop', source='dukcapil', table='master_penduduk',
                 column='tgl_lahir_ktp')
    assert out['decision'] == 'hitl'
    assert any('WHERE/JOIN view v.penduduk_ringkas' in r for r in out['reasons'])


# ── penulisan ulang teks definisi ──────────────────────────────────────────────
@pytest.mark.parametrize('body, names, expected', [
    ('SELECT a, b, c FROM t', {'b'}, 'SELECT a, c FROM t'),
    ('select DISTINCT x.a AS id, x.b FROM s.t AS x WHERE x.b > 1', {'id'},
     'select DISTINCT x.b FROM s.t AS x WHERE x.b > 1'),
    ('SELECT CONCAT(a, \', \', b) AS n, c FROM t', {'c'}, "SELECT CONCAT(a, ', ', b) AS n FROM t"),
    ('SELECT "from", fromage FROM t', {'fromage'}, 'SELECT "from" FROM t'),
    ('SELECT a, B FROM t', {'b'}, 'SELECT a FROM t'),
])
def test_remove_projection_keeps_the_rest_of_the_text(body, names, expected):
    assert remove_projection(body, names) == expected


@pytest.mark.parametrize('body, names, pesan', [
    ('SELECT a FROM t', {'a'}, 'seluruh kolom'),
    ('SELECT a FROM t UNION SELECT a FROM u', {'a'}, 'SELECT tunggal'),
    ('SELECT a, b FROM t', {'z'}, 'tidak ditemukan'),
])
def test_remove_projection_refuses_unsafe_rewrites(body, names, pesan):
    with pytest.raises(RewriteError, match=pesan):
        remove_projection(body, names)


def test_inline_column_detection():
    assert inline_columns('CREATE VIEW "v"."profil" (a string) AS SELECT 1', 'v', 'profil')
    assert inline_columns('create virtual view profil(a string) as select 1', 'v', 'profil')
    assert not inline_columns('CREATE VIEW profil AS SELECT a FROM t', 'v', 'profil')
    assert not inline_columns(None, 'v', 'profil')
