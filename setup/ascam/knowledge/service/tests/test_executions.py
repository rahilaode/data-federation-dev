"""Perekaman eksekusi rencana: langkah, validasi, penyelesaian, dan status rencana."""
import pytest

from test_events import kirim
from test_impact import obdf                          # noqa: F401 — fixture OBDF tersinkron


def rencana_disetujui(api, obdf_id, hitl=False):
    if hitl:
        plan = kirim(api, obdf_id, operation='drop', source='dukcapil',
                     table='master_penduduk', column='nik')['plan']
        api.post(f"/api/v1/plans/{plan['id']}/approve")
        return plan['id']
    return kirim(api, obdf_id, operation='drop', source='kemensos', schema='public',
                 table='penerima_manfaat', column='status_ekonomi')['plan']['id']


def test_execution_records_steps_and_marks_plan_executed(api, obdf):
    plan_id = rencana_disetujui(api, obdf)
    execution = api.post(f'/api/v1/plans/{plan_id}/executions',
                         json={'candidate_spec_version_id': None}).json()
    assert execution['status'] == 'running' and execution['steps'] == []

    eid = execution['id']
    for seq, (nama, status) in enumerate([('deploy_vdb', 'succeeded'), ('validate', 'succeeded'),
                                          ('switch', 'succeeded'), ('reload_ontop', 'succeeded'),
                                          ('verify', 'succeeded')], start=1):
        api.post(f'/api/v1/executions/{eid}/steps',
                 json={'seq': seq, 'name': nama, 'status': status, 'detail': {'ms': seq * 100}})
    api.post(f'/api/v1/executions/{eid}/validations',
             json={'validator': 'ontop_validate', 'passed': True, 'details': {'exit_code': 0}})
    selesai = api.post(f'/api/v1/executions/{eid}/finish',
                       json={'status': 'succeeded', 'timings': {'total_ms': 12000}}).json()

    assert selesai['status'] == 'succeeded' and selesai['finished_at']
    assert [s['name'] for s in selesai['steps']] == ['deploy_vdb', 'validate', 'switch',
                                                     'reload_ontop', 'verify']
    assert api.get(f'/api/v1/plans/{plan_id}').json()['status'] == 'executed'
    riwayat = api.get(f'/api/v1/obdf/{obdf}/executions').json()
    assert riwayat[0]['id'] == eid and riwayat[0]['timings'] == {'total_ms': 12000}


def test_failed_execution_marks_plan_failed_and_notifies(api, obdf):
    plan_id = rencana_disetujui(api, obdf)
    eid = api.post(f'/api/v1/plans/{plan_id}/executions').json()['id']
    api.post(f'/api/v1/executions/{eid}/steps',
             json={'seq': 1, 'name': 'deploy_vdb', 'status': 'failed',
                   'detail': {'errors': ['TEIID31259 ddl tidak valid']}})
    api.post(f'/api/v1/executions/{eid}/validations',
             json={'validator': 'teiid_status', 'passed': False, 'details': {'status': 'FAILED'}})
    hasil = api.post(f'/api/v1/executions/{eid}/finish',
                     json={'status': 'failed',
                           'failure': {'message': 'VDB versi baru FAILED'}}).json()
    assert hasil['status'] == 'failed'
    assert api.get(f'/api/v1/plans/{plan_id}').json()['status'] == 'failed'
    audit = [a['action'] for a in api.get(f'/api/v1/obdf/{obdf}/audit').json()]
    assert 'execution_failed' in audit


def test_rollback_is_recorded(api, obdf):
    plan_id = rencana_disetujui(api, obdf)
    eid = api.post(f'/api/v1/plans/{plan_id}/executions').json()['id']
    api.post(f'/api/v1/executions/{eid}/steps',
             json={'seq': 6, 'name': 'rollback', 'status': 'succeeded',
                   'detail': {'alasan': 'verifikasi SPARQL gagal'}})
    hasil = api.post(f'/api/v1/executions/{eid}/finish', json={'status': 'rolled_back'}).json()
    assert hasil['status'] == 'rolled_back'
    assert api.get(f'/api/v1/plans/{plan_id}').json()['status'] == 'failed'


def test_only_approved_plans_can_be_executed(api, obdf):
    plan = kirim(api, obdf, operation='drop', source='dukcapil', table='master_penduduk',
                 column='nik')['plan']
    assert plan['status'] == 'pending_approval'
    r = api.post(f"/api/v1/plans/{plan['id']}/executions")
    assert r.status_code == 409 and 'approved' in r.json()['detail']


def test_second_running_execution_is_rejected(api, obdf):
    plan_id = rencana_disetujui(api, obdf)
    api.post(f'/api/v1/plans/{plan_id}/executions')
    kedua = api.post(f'/api/v1/plans/{plan_id}/executions')
    assert kedua.status_code == 409 and 'masih berjalan' in kedua.json()['detail']


def test_finish_twice_is_rejected(api, obdf):
    plan_id = rencana_disetujui(api, obdf)
    eid = api.post(f'/api/v1/plans/{plan_id}/executions').json()['id']
    api.post(f'/api/v1/executions/{eid}/finish', json={'status': 'succeeded'})
    lagi = api.post(f'/api/v1/executions/{eid}/finish', json={'status': 'failed'})
    assert lagi.status_code == 409


@pytest.mark.parametrize('path', ['/api/v1/executions/999999/steps',
                                  '/api/v1/executions/999999/validations',
                                  '/api/v1/executions/999999/finish'])
def test_unknown_execution_is_404(api, obdf, path):
    body = {'seq': 1, 'name': 'verify', 'status': 'succeeded'} if 'steps' in path else (
        {'validator': 'teiid_status', 'passed': True} if 'validations' in path
        else {'status': 'succeeded'})
    assert api.post(path, json=body).status_code == 404
