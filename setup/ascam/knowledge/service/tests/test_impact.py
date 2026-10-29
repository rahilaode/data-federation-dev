"""Impact analysis and decisions D11 over the specification produced by a sync."""
import uuid

import pytest
from sqlalchemy import select

from ascam_knowledge import impact as impact_service
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
    assert out['targets'][0]['column'] == 'tanggal_lahir'         # the Teiid name does not change
    assert out['actions'] == [{'artifact': 'vdb', 'operation': 'set_name_in_source',
                               'model': 'dukcapil', 'table': 'master_penduduk',
                               'column': 'tanggal_lahir', 'name_in_source': 'tgl_lahir_baru'}]


def test_rename_into_existing_source_name_is_notified(api, obdf):
    out = impact(api, obdf, operation='rename', source='dukcapil', table='master_penduduk',
                 column='tgl_lahir_ktp', new_column='nik')
    assert out['decision'] == 'notify' and any('already used' in r for r in out['reasons'])
    assert out['actions'] == []


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
    """bansos:nik is used by two TriplesMaps; the property must not be deprecated. Dropping nik itself
    leads to a notification (IRI template), so the shared-predicate check is tested directly."""
    out = impact(api, obdf, operation='drop', source='kemensos', schema='public',
                 table='penerima_manfaat', column='nik')
    assert out['decision'] == 'notify' and out['actions'] == []
    assert any(p.endswith('/nik') for p in out['affected']['predicates'])
    column_ids = {t['column_id'] for t in out['targets']}
    with api.factory() as db:
        assert impact_service._predicate_shared(db, out['spec_version_id'],
                                                'http://bansos.go.id/ontology/nik', column_ids)


def test_drop_primary_key_or_iri_template_is_notified(api, obdf):
    out = impact(api, obdf, operation='drop', source='dukcapil', table='master_penduduk', column='nik')
    assert out['decision'] == 'notify' and out['actions'] == []
    assert any('primary key' in r for r in out['reasons'])
    assert any('iri_template' in r for r in out['reasons'])


def test_drop_column_used_by_view_predicate_is_notified(api, obdf):
    """tanggal_lahir is used only in WHERE inside the view read by MapView (experiment F0.7)."""
    out = impact(api, obdf, operation='drop', source='dukcapil', table='master_penduduk',
                 column='tgl_lahir_ktp')
    assert out['decision'] == 'notify'
    assert any('MapView' in r and 'sql_predicate' in r for r in out['reasons'])


def test_drop_column_reached_through_expression_needs_hitl(api, obdf):
    """nik_upper is an expression over nik; its path is marked expression."""
    with api.factory() as db:
        rows = db.execute(select(spec.ColumnUsage.weakest_link)
                          .where(spec.ColumnUsage.role == 'literal_value')).scalars().all()
    assert 'expression' in rows
    out = impact(api, obdf, operation='drop', source='dukcapil', table='master_penduduk', column='nik')
    assert any('expression' in r for r in out['reasons'])


# ── ADD ─────────────────────────────────────────────────────────────────────────
def test_add_always_needs_approval_with_generated_proposal(api, obdf):
    out = impact(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat_lengkap', column_type='varchar(200)')
    assert out['decision'] == 'hitl' and out['pattern'] == 'P-001'
    assert any('modelling decision' in r for r in out['reasons'])
    proposal = out['add_options']
    assert proposal['proposed_iri'] == 'http://bansos.go.id/ontology/alamatLengkap'
    assert proposal['proposed_domain'] == 'http://bansos.go.id/ontology/PenerimaBansos'
    assert proposal['classes'] == ['http://bansos.go.id/ontology/PenerimaBansos']
    ontology = [a for a in out['actions'] if a['artifact'] == 'ontology'][0]
    assert ontology['iri'] == 'http://bansos.go.id/ontology/alamatLengkap'
    assert ontology['domain'] == ['http://bansos.go.id/ontology/PenerimaBansos']
    assert [a for a in out['actions'] if a['artifact'] == 'r2rml'][0]['column'] == 'alamat_lengkap'


def test_add_ignores_old_auto_policy(api, obdf):
    """ADR-0024: the policy adaptation.add_column can no longer make ADD automatic."""
    api.put(f'/api/v1/obdf/{obdf}/settings/adaptation.add_column', json={'value': {'mode': 'auto'}})
    out = impact(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')
    assert out['decision'] == 'hitl'


def test_hitl_add_plan_is_complete(api, obdf):
    """Regression: the HITL policy cut the analysis short, so the plan held only VDB actions;
    after approval, the property and the mapping were never created."""
    out = impact(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')
    layer_ops = {(a['artifact'], a['operation']) for a in out['actions']}
    assert layer_ops >= {('vdb', 'add_column'), ('ontology', 'add_datatype_property'),
                       ('r2rml', 'add_predicate_object_map')}
    assert out['decision'] == 'hitl' and out['pattern'] == 'P-001'


def test_add_existing_column_name_needs_hitl(api, obdf):
    out = impact(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='nik', column_type='varchar(16)')
    assert out['decision'] == 'hitl' and any('already exists' in r for r in out['reasons'])


def test_add_with_conflicting_property_domain(api, obdf):
    """bansos:nik has domain Penduduk; reusing it for PenerimaBansos would mislead inference."""
    # policy: qualify the IRI with the class
    api.put(f'/api/v1/obdf/{obdf}/naming-policy',
            json={'property_iri_template': '{namespace}nik', 'on_collision': 'qualify_with_class',
                  'namespace': 'http://bansos.go.id/ontology/'})
    out = impact(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='nik_baru', column_type='varchar(16)')
    assert out['decision'] == 'hitl'
    iri = [a for a in out['actions'] if a['artifact'] == 'ontology'][0]['iri']
    assert iri.endswith('_PenerimaBansos') and any('different domain' in r for r in out['reasons'])

    # policy: leave it to the administrator
    api.put(f'/api/v1/obdf/{obdf}/naming-policy',
            json={'property_iri_template': '{namespace}nik', 'on_collision': 'hitl',
                  'namespace': 'http://bansos.go.id/ontology/'})
    out = impact(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='nik_baru', column_type='varchar(16)')
    assert out['decision'] == 'hitl' and any('different domain' in r for r in out['reasons'])
    assert out['add_options']['proposed_iri'] is None          # the administrator must give a name
    assert [a['artifact'] for a in out['actions']] == ['vdb']


# ── ignored cases ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize('body, fragment', [
    ({'operation': 'drop', 'source': 'does-not-exist', 'table': 'x', 'column': 'y'}, 'is not registered'),
    ({'operation': 'drop', 'source': 'dukcapil', 'table': 'other_table', 'column': 'y'},
     'not found'),
    ({'operation': 'add', 'source': 'dukcapil', 'table': 'other_table', 'column': 'y'},
     'is not federated'),
])
def test_unknown_objects_are_ignored(api, obdf, body, fragment):
    out = impact(api, obdf, **body)
    assert out['decision'] == 'ignored' and fragment in ' '.join(out['reasons'])


def test_unparsable_sql_forces_notification(api, obdf):
    """A logical-table SQL that cannot be parsed makes every DROP decision conservative."""
    broken = fx.MAPPING_TTL.replace('SELECT * FROM kemensos.penerima_manfaat', 'NOT SQL ###')
    set_clients(api, agent=fx.FakeAgent(mapping=broken))
    assert api.post(f'/api/v1/obdf/{obdf}/sync').json()['changed']
    out = impact(api, obdf, operation='drop', source='kemensos', schema='public',
                 table='penerima_manfaat', column='status_ekonomi')
    assert out['decision'] == 'notify' and any('cannot be parsed' in r for r in out['reasons'])


# ── choice of TriplesMap for a new column (regression F4c) ──────────────────────
def test_add_targets_only_triples_maps_that_expose_new_columns(api, obdf):
    """Regression: the mapping was once attached to a TriplesMap with an explicit SELECT,
    so `ontop validate` rejected the artifacts."""
    out = impact(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')
    assert out['decision'] == 'hitl'
    targets = {a['triples_map_iri'].rsplit('#', 1)[-1] for a in out['actions']
               if a['artifact'] == 'r2rml'}
    assert targets == {'MapPenerima', 'MapRingkas'}       # rr:tableName and SELECT *
    assert 'MapLinkPenerima' not in targets               # SELECT with an explicit column list


def test_add_is_hitl_when_exposing_maps_assign_several_classes(api, obdf):
    """The domain of a new property cannot be derived from the DDL when the TriplesMaps that expose
    the table give different classes; the alphabetically first class used to be taken silently."""
    two_classes = fx.MAPPING_TTL.replace(
        'rr:subjectMap [ rr:template "http://bansos.go.id/resource/ringkas/{penerima_id}/{nik}" ]',
        'rr:subjectMap [ rr:template "http://bansos.go.id/resource/ringkas/{penerima_id}/{nik}" ;'
        ' rr:class bansos:Ringkasan ]')
    assert two_classes != fx.MAPPING_TTL
    set_clients(api, agent=fx.FakeAgent(mapping=two_classes))
    assert api.post(f'/api/v1/obdf/{obdf}/sync').json()['changed']
    out = impact(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')
    assert out['decision'] == 'hitl'
    assert any('more than one class' in r for r in out['reasons'])
    layer_ops = {(a['artifact'], a['operation']) for a in out['actions']}
    assert ('ontology', 'add_datatype_property') in layer_ops      # the plan stays complete


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
    assert any('explicit column lists' in r for r in out['reasons'])


def test_add_refuses_iri_already_used_as_object_property(api, obdf):
    """A name used by an object property must not become a data property (punning)."""
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
    assert out['decision'] == 'hitl'
    iri = [a for a in out['actions'] if a['artifact'] == 'ontology'][0]['iri']
    assert iri.endswith('_PenerimaBansos')


def test_drop_action_names_the_triples_map(api, obdf):
    out = impact(api, obdf, operation='drop', source='kemensos', schema='public',
                 table='penerima_manfaat', column='status_ekonomi')
    removals = [a for a in out['actions'] if a['operation'] == 'remove_predicate_object_map']
    assert removals and all(a['triples_map_iri'].rsplit('#', 1)[-1] in ('MapPenerima', 'MapRingkas')
                         for a in removals)


def test_drop_rewrites_explicit_logical_table(api, obdf):
    """Regression A002: a column named explicitly in rr:sqlQuery must be removed from it as well.
    Dropping this column itself leads to a notification (used in WHERE), so the tracing of
    explicit projections is tested directly."""
    out = impact(api, obdf, operation='drop', source='dukcapil', table='master_penduduk',
                 column='tgl_lahir_ktp')
    column_id = out['targets'][0]['column_id']
    with api.factory() as db:
        projections = impact_service._explicit_projections(db, out['spec_version_id'], column_id)
    assert [(iri.rsplit('#', 1)[-1], logical_name) for iri, logical_name, _ in projections] == [
        ('MapPenduduk', 'tanggal_lahir')]


def test_drop_of_star_column_needs_no_rewrite(api, obdf):
    out = impact(api, obdf, operation='drop', source='kemensos', schema='public',
                 table='penerima_manfaat', column='status_ekonomi')
    assert not [a for a in out['actions'] if a['operation'] == 'rewrite_logical_table']


# ── reusing a property whose column was dropped earlier (ADD → DROP → ADD) ──────
def test_add_that_reuses_a_deprecated_property_says_so(api, obdf):
    """bansos:statusEkonomi is deprecated in the fixture; a proposal that reuses it must tell the
    administrator that approving withdraws the deprecation."""
    api.put(f'/api/v1/obdf/{obdf}/naming-policy',
            json={'property_iri_template': '{namespace}statusEkonomi', 'on_collision': 'hitl',
                  'namespace': 'http://bansos.go.id/ontology/'})
    out = impact(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='status_baru', column_type='varchar(20)')
    assert out['decision'] == 'hitl'
    assert out['add_options']['proposed_iri'] == 'http://bansos.go.id/ontology/statusEkonomi'
    assert out['add_options']['range_iri'] == 'http://www.w3.org/2001/XMLSchema#string'
    assert any('is deprecated' in r and 'withdraws the deprecation' in r for r in out['reasons'])


def test_add_that_reuses_a_property_with_another_range_is_refused(api, obdf):
    api.put(f'/api/v1/obdf/{obdf}/type-mappings', json=[
        {'dbms': 'postgresql', 'native_type': 'integer', 'teiid_type': 'integer',
         'xsd_datatype': 'xsd:integer'}])
    api.put(f'/api/v1/obdf/{obdf}/naming-policy',
            json={'property_iri_template': '{namespace}statusEkonomi', 'on_collision': 'hitl',
                  'namespace': 'http://bansos.go.id/ontology/'})
    out = impact(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='status_baru', column_type='integer')
    assert out['add_options']['range_iri'] == 'http://www.w3.org/2001/XMLSchema#integer'
    assert any('already exists with range' in r for r in out['reasons'])
    assert out['add_options']['proposed_iri'] is None          # the administrator must give a name
    assert [a['artifact'] for a in out['actions']] == ['vdb']
