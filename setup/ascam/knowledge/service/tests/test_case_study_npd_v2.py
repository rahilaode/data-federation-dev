"""Branch feature/npd_v2: the plan of ASCAM for every rule of the Plan in the paper, on the NPD
federation with its validation layer (setup/npd/tools/validation.py,
docs/npd-v2-modifications.md). The artifacts are those in the repository; the Teiid runtime
metadata is built from the VDB by teiid_metadata.py.

Each test names the sentence of the paper or the row of Table 1 that it checks.
"""
import pytest

from test_case_study_npd import NPDV, actions_of, api_module, make_npd   # noqa: F401
from test_impact import impact

NPDX = 'https://github.com/rahilaode/data-federation-dev/npd-validation#'
XSD = 'http://www.w3.org/2001/XMLSchema#'
PG = {'source': 'reporting', 'schema': 'public'}
MY = {'source': 'registry', 'schema': 'registry'}


@pytest.fixture(scope='module')
def npd(api_module):
    return make_npd(api_module, 'npd-v2')


def run(api, oid, operation, where, table, column, **extra):
    return impact(api, oid, operation=operation, table=table, column=column, **where, **extra)


def reasons_with(out, *fragments):
    return [r for r in out['reasons'] if all(f in r for f in fragments)]


def tm(action):
    return action['triples_map_iri'].rsplit(':', 1)[-1].rsplit('#', 1)[-1]


# ── Example 1-3 of the paper on NPD: company, v_current_operator, Validation:CurrentOperator ──
def test_v01_example_drop_runs_automatically(npd):
    """Example 2 / P-002: D(c,F) = {ĉ, v_current_operator.orgNumber, a2}; W = {orgNumber},
    ℳ_c = {a2}; the view keeps its WHERE; npdx:orgNumber is orphaned and deprecated; a1 stays."""
    api, oid = npd
    out = run(api, oid, 'drop', MY, 'company', 'cmpOrgNumberBrReg')
    assert out['decision'] == 'auto' and out['pattern'] == 'P-002', out['reasons']
    assert [a['operation'] for a in out['actions']] == [
        'drop_column', 'alter_view', 'remove_predicate_object_map', 'deprecate_property']
    [view] = actions_of(out, 'alter_view')
    assert view['body'] == ('SELECT "cmpNpdidCompany" FROM "registry"."company" '
                            'WHERE "cmpLicenceOperCurrent" = \'Y\'')
    [pom] = actions_of(out, 'remove_predicate_object_map')
    assert (tm(pom), pom['predicate_iri'], pom['object']) == ('CurrentOperator', NPDX + 'orgNumber', '"orgNumber"')
    assert [a['predicate_iri'] for a in actions_of(out, 'deprecate_property')] == [NPDX + 'orgNumber']


def test_v02_example_rename_is_repair_a(npd):
    """Example 3 (a) / P-003: one change to ĉ, the view, ℳ and 𝒯 stay."""
    api, oid = npd
    out = run(api, oid, 'rename', MY, 'company', 'cmpOrgNumberBrReg', new_column='cmpOrgNumber')
    assert out['decision'] == 'auto' and out['pattern'] == 'P-003', out['reasons']
    assert [(a['operation'], a['column'], a['name_in_source']) for a in out['actions']] == [
        ('set_name_in_source', 'cmpOrgNumberBrReg', 'cmpOrgNumber')]


def test_v03_example_drop_of_where_column_is_notified(npd):
    """Plan, first paragraph and Table 1 (drop): SQL predicate and weakest link predicate."""
    api, oid = npd
    out = run(api, oid, 'drop', MY, 'company', 'cmpLicenceOperCurrent')
    assert out['decision'] == 'notify' and out['actions'] == []
    assert reasons_with(out, 'Validation:CurrentOperator', 'sql_predicate')
    assert reasons_with(out, 'WHERE', 'services.v_current_operator')


def test_v04_example_drop_of_key_is_notified(npd):
    """Table 1 (drop): primary key and role IRI template."""
    api, oid = npd
    out = run(api, oid, 'drop', MY, 'company', 'cmpNpdidCompany')
    assert out['decision'] == 'notify' and out['actions'] == []
    assert reasons_with(out, 'primary key')
    assert reasons_with(out, 'Validation:CurrentOperator', 'iri_template')


# ── Table 1: the other notification conditions ──────────────────────────────────────────────
def test_v05_rename_to_a_name_already_read_is_notified(npd):
    """Table 1 (rename): company.cmpGroupName already reads c'."""
    api, oid = npd
    out = run(api, oid, 'rename', MY, 'company', 'cmpGroup', new_column='cmpGroupName')
    assert out['decision'] == 'notify' and out['actions'] == []
    assert reasons_with(out, 'already used', 'cmpGroupName')


def test_v06_drop_of_iri_template_column_outside_the_key_is_notified(npd):
    """Table 1 (drop): role IRI template; wlbWell is not in the primary key of the table."""
    api, oid = npd
    out = run(api, oid, 'drop', PG, 'wellbore_npdid_overview', 'wlbWell')
    assert out['decision'] == 'notify' and out['actions'] == []
    assert reasons_with(out, 'iri_template') and not reasons_with(out, 'primary key')


def test_v07_drop_inside_a_view_expression_is_notified(npd):
    """Table 1 (drop): weakest link expression (v_wellbore_core_ft, scenario A009)."""
    api, oid = npd
    out = run(api, oid, 'drop', PG, 'wellbore_core', 'wlbCoreIntervalBottom')
    assert out['decision'] == 'notify' and out['actions'] == []
    assert reasons_with(out, 'expression', 'v_wellbore_core_ft')


@pytest.mark.parametrize('code, where, table, column, fragments', [
    ('v08', PG, 'wlbPoint', 'wlbField', ('v_point_field', 'lose all')),
    ('v09', MY, 'pipLine', 'pipNameCurrentOperator', ('v_pipeline_operator', 'inline')),
    ('v10', MY, 'field_description', 'fldDescriptionText', ('materialized', 'v_field_text_cache')),
])
def test_v08_v10_views_that_cannot_be_rewritten_are_notified(npd, code, where, table, column, fragments):
    """Table 1 (drop): a view passing ĉ on would lose all its columns, declares them inline, or
    is materialized."""
    api, oid = npd
    out = run(api, oid, 'drop', where, table, column)
    assert out['decision'] == 'notify' and out['actions'] == [], code
    assert reasons_with(out, *fragments), (code, out['reasons'])


def test_v11_add_under_a_union_is_notified(npd):
    """Plan, first paragraph and Table 1 (add): v_field_status selects * in one branch of a
    UNION ALL only (ADR-0032)."""
    api, oid = npd
    out = run(api, oid, 'add', MY, 'field_activity_status_hst', 'fldStatusNote', column_type='VARCHAR(100)')
    assert out['decision'] == 'notify' and out['actions'] == []
    assert reasons_with(out, 'v_field_status', 'UNION')


# ── P-002 ─────────────────────────────────────────────────────────────────────────────────
def test_v12_drop_of_a_projected_column_only_rewrites(npd):
    """P-002, rw_c: wlbProductionFacility is named in a logical table but used by no triple."""
    api, oid = npd
    out = run(api, oid, 'drop', PG, 'wlbPoint', 'wlbProductionFacility')
    assert out['decision'] == 'auto', out['reasons']
    assert [a['operation'] for a in out['actions']] == ['drop_column', 'rewrite_logical_table']
    assert tm(actions_of(out, 'rewrite_logical_table')[0]) == 'PointFacility'


def test_v13_drop_keeps_a_property_that_another_assertion_populates(npd):
    """P-002: npdx:drillingFacility is also filled from wellbore_shallow_all, so it is not
    deprecated ("a property that an assertion of ℳ' still populates is not")."""
    api, oid = npd
    out = run(api, oid, 'drop', PG, 'wlbPoint', 'wlbDrillingFacility')
    assert out['decision'] == 'auto', out['reasons']
    [pom] = actions_of(out, 'remove_predicate_object_map')
    assert (tm(pom), pom['predicate_iri']) == ('PointFacility', NPDX + 'drillingFacility')
    assert actions_of(out, 'deprecate_property') == []
    assert [a['predicate_iri'] for a in actions_of(out, 'keep_property')] == [NPDX + 'drillingFacility']


def test_v14_drop_through_a_pass_through_view(npd):
    """P-002, W ≠ ∅ (scenario A007): ALTER VIEW v_wellbore_mud; npdv:mudViscosity is orphaned."""
    api, oid = npd
    out = run(api, oid, 'drop', PG, 'wellbore_mud', 'wlbMudViscosityAtMD')
    assert out['decision'] == 'auto', out['reasons']
    [view] = actions_of(out, 'alter_view')
    assert view['table'] == 'v_wellbore_mud' and 'wlbMudViscosityAtMD' not in view['body']
    assert [a['predicate_iri'] for a in actions_of(out, 'deprecate_property')] == [NPDV + 'mudViscosity']


def test_v15_drop_through_an_aliasing_view_read_with_select_star(npd):
    """P-002 (scenario A008): the view renames the column, the mapping reads it with SELECT *,
    so ℳ_c is found through the alias and no logical table is rewritten."""
    api, oid = npd
    out = run(api, oid, 'drop', MY, 'field_investment_yearly', 'prfInvestmentsMillNOK')
    assert out['decision'] == 'auto', out['reasons']
    assert [a['table'] for a in actions_of(out, 'alter_view')] == ['v_field_investment']
    assert actions_of(out, 'rewrite_logical_table') == []
    [pom] = actions_of(out, 'remove_predicate_object_map')
    assert pom['object'] == '"investmentMillNOK"'


# ── P-003 on PostgreSQL ───────────────────────────────────────────────────────────────────
def test_v16_rename_on_postgresql(npd):
    """P-003 (scenario A003): NAMEINSOURCE is quoted for the mixed-case PostgreSQL names."""
    api, oid = npd
    out = run(api, oid, 'rename', PG, 'wellbore_exploration_all', 'wlbDrillingOperator',
              new_column='wlbDrillingCompany')
    assert out['decision'] == 'auto' and out['pattern'] == 'P-003', out['reasons']
    assert [a['name_in_source'] for a in out['actions']] == ['wlbDrillingCompany']


# ── P-001 ─────────────────────────────────────────────────────────────────────────────────
def test_v17_add_with_a_safe_domain_reuses_the_deprecated_property(npd):
    """P-001, X ≠ ∅: both maps over the table select *; the one without class shares the
    template of the class map that reads every row, so ex:OverviewWellbore is safe (R2,
    ADR-0033). npdv:wlbEntryYear is deprecated with that domain and range, so it is reused and
    its deprecation withdrawn. v_wellbore_overview selects * and is no notification condition."""
    api, oid = npd
    out = run(api, oid, 'add', PG, 'wellbore_npdid_overview', 'wlbEntryYear', column_type='INTEGER')
    assert out['decision'] == 'hitl' and out['pattern'] == 'P-001', out['reasons']
    options = out['add_options']
    assert options['classes'] == [NPDX + 'OverviewWellbore']
    assert options['safe_domains'] == [NPDX + 'OverviewWellbore']
    assert options['proposed_iri'] == NPDV + 'wlbEntryYear'
    assert reasons_with(out, 'deprecated', 'withdraws the deprecation')
    assert not reasons_with(out, 'selects *')
    [prop] = actions_of(out, 'add_datatype_property')
    assert prop['domain'] == [NPDX + 'OverviewWellbore']
    assert sorted(tm(a) for a in actions_of(out, 'add_predicate_object_map')) == [
        'OverviewWellbore', 'OverviewWellboreType']


def test_v18_add_with_an_unsafe_domain_gets_no_domain_and_a_new_name(npd):
    """P-001, several classes: X_C keeps the map without class (FacilityRecord, other IRIs) and
    leaves out MoveableFacility; no class is safe, so the property gets no domain, and the
    deprecated npdv:fclStatus (domain MoveableFacility) is not reused (ADR-0033)."""
    api, oid = npd
    out = run(api, oid, 'add', MY, 'facility_moveable', 'fclStatus', column_type='VARCHAR(20)')
    assert out['decision'] == 'hitl', out['reasons']
    options = out['add_options']
    assert options['classes'] == [NPDX + 'ForeignFlaggedFacility', NPDX + 'MoveableFacility']
    assert options['safe_domains'] == []
    assert options['proposed_iri'] == NPDV + 'fclStatus_ForeignFlaggedFacility'
    assert reasons_with(out, 'gets no rdfs:domain', 'FacilityRecord')
    [prop] = actions_of(out, 'add_datatype_property')
    assert prop['domain'] == []
    assert sorted(tm(a) for a in actions_of(out, 'add_predicate_object_map')) == [
        'FacilityRecord', 'ForeignFlaggedFacility']


@pytest.mark.parametrize('where, table, column, column_type, domain', [
    (PG, 'wellbore_mud', 'wlbMudPh', 'DECIMAL(6, 2)', NPDV + 'WellboreDrillingMudSample'),
    (MY, 'company', 'cmpCountryCode', 'VARCHAR(2)', NPDV + 'Company'),
])
def test_v19_v20_add_when_every_logical_table_lists_its_columns(npd, where, table, column, column_type, domain):
    """P-001, X = ∅ (scenarios A001, A004): one new TriplesMap that keeps the template and the
    query of a class map; the domain is safe because the subjects are those of that map."""
    api, oid = npd
    out = run(api, oid, 'add', where, table, column, column_type=column_type)
    assert out['decision'] == 'hitl', out['reasons']
    assert actions_of(out, 'add_predicate_object_map') == []
    [new] = actions_of(out, 'add_triples_map')
    assert column in new['sql_query']
    assert [a['domain'] for a in actions_of(out, 'add_datatype_property')] == [[domain]]


def test_v21_add_to_a_table_without_class_map_changes_sigma_only(npd):
    """P-001, X_C = ∅: no map exposes field_description and none of its maps has a class."""
    api, oid = npd
    out = run(api, oid, 'add', MY, 'field_description', 'fldNote', column_type='VARCHAR(20)')
    assert out['decision'] == 'hitl', out['reasons']
    assert [a['operation'] for a in out['actions']] == ['add_column']
    assert reasons_with(out, 'only the VDB column')
