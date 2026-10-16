"""Lineage kolom untuk konsol (ADR-0024) terhadap artefak studi kasus."""
from test_studi_kasus_view import B, studi        # noqa: F401 — fixture artefak studi kasus


def lineage(api, oid, **params):
    r = api.get(f'/api/v1/obdf/{oid}/lineage', params=params)
    assert r.status_code == 200, r.text
    return r.json()


def label_jalur(out):
    nama = {n['id']: n['label'] for n in out['nodes']}
    return [[nama[n] for n in p['nodes']] for p in out['paths']]


def test_a009_column_reaches_property_through_view_expression(api, studi):
    out = lineage(api, studi, source='kemensos', table='program_bansos', column='periode_selesai')
    lapisan = {n['layer'] for n in out['nodes']}
    assert lapisan == {'source', 'sigma', 'mapping', 'ontology'}
    jalur = [p for p in out['paths'] if p['role'] == 'literal_value']
    assert jalur and jalur[0]['weakest_link'] == 'expression'
    label = label_jalur({'nodes': out['nodes'], 'paths': jalur})[0]
    assert label[0] == 'kemensos:public.program_bansos.periode_selesai'
    assert any('v_program_berakhir' in x for x in label[2:-2])           # melewati view
    assert label[-1] == 'tahunBerakhir'
    tepi = {(e['from'], e['to']): e['kind'] for e in out['edges']}
    assert 'expression' in tepi.values()


def test_whole_specification_and_unused_columns(api, studi):
    semua = lineage(api, studi)
    assert semua['version_no'] >= 1
    sumber = {n['source'] for n in semua['nodes'] if n['layer'] == 'source'}
    assert sumber == {'kemensos', 'dukcapil'}
    # setiap jalur diawali kolom sumber dan setiap simpul jalur ada di daftar simpul
    ids = {n['id'] for n in semua['nodes']}
    assert all(p['nodes'][0].startswith('src:') and set(p['nodes']) <= ids for p in semua['paths'])
    assert all(p['role'] is None for p in semua['paths'] if len(p['nodes']) == 2)
    assert len(semua['unused_columns']) == sum(1 for p in semua['paths'] if p['role'] is None)


def test_lineage_of_unknown_version_is_404(api, studi):
    assert api.get(f'/api/v1/obdf/{studi}/lineage', params={'version_id': 999999}).status_code == 404
