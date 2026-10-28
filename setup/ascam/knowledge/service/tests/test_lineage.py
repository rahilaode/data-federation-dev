"""Column lineage for the console (ADR-0024) over the case-study artifacts."""
from test_case_study_view import B, case_study        # noqa: F401 — case-study artifact fixture


def lineage(api, oid, **params):
    r = api.get(f'/api/v1/obdf/{oid}/lineage', params=params)
    assert r.status_code == 200, r.text
    return r.json()


def path_labels(out):
    label_of = {n['id']: n['label'] for n in out['nodes']}
    return [[label_of[n] for n in p['nodes']] for p in out['paths']]


def test_a009_column_reaches_property_through_view_expression(api, case_study):
    out = lineage(api, case_study, source='kemensos', table='program_bansos', column='periode_selesai')
    layers = {n['layer'] for n in out['nodes']}
    assert layers == {'source', 'sigma', 'mapping', 'ontology'}
    paths_found = [p for p in out['paths'] if p['role'] == 'literal_value']
    assert paths_found and paths_found[0]['weakest_link'] == 'expression'
    labels = path_labels({'nodes': out['nodes'], 'paths': paths_found})[0]
    assert labels[0] == 'kemensos:public.program_bansos.periode_selesai'
    assert any('v_program_berakhir' in x for x in labels[2:-2])           # passes through the view
    assert labels[-1] == 'tahunBerakhir'
    edges = {(e['from'], e['to']): e['kind'] for e in out['edges']}
    assert 'expression' in edges.values()


def test_whole_specification_and_unused_columns(api, case_study):
    whole = lineage(api, case_study)
    assert whole['version_no'] >= 1
    sources = {n['source'] for n in whole['nodes'] if n['layer'] == 'source'}
    assert sources == {'kemensos', 'dukcapil'}
    # every path starts at a source column and every node of a path is in the node list
    ids = {n['id'] for n in whole['nodes']}
    assert all(p['nodes'][0].startswith('src:') and set(p['nodes']) <= ids for p in whole['paths'])
    assert all(p['role'] is None for p in whole['paths'] if len(p['nodes']) == 2)
    assert len(whole['unused_columns']) == sum(1 for p in whole['paths'] if p['role'] is None)


def test_lineage_of_unknown_version_is_404(api, case_study):
    assert api.get(f'/api/v1/obdf/{case_study}/lineage', params={'version_id': 999999}).status_code == 404
