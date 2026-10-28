"""Recording of plan executions: steps, validations, completion, and plan status."""
import pytest

from test_events import send_event
from test_impact import obdf                          # noqa: F401 — synced OBDF fixture


def approved_plan(api, obdf_id, hitl=False):
    if hitl:
        plan = send_event(api, obdf_id, operation='add', source='kemensos', schema='public',
                     table='penerima_manfaat', column='alamat', column_type='varchar(50)')['plan']
        api.post(f"/api/v1/plans/{plan['id']}/approve")
        return plan['id']
    return send_event(api, obdf_id, operation='drop', source='kemensos', schema='public',
                 table='penerima_manfaat', column='status_ekonomi')['plan']['id']


def test_execution_records_steps_and_marks_plan_executed(api, obdf):
    plan_id = approved_plan(api, obdf)
    execution = api.post(f'/api/v1/plans/{plan_id}/executions',
                         json={'candidate_spec_version_id': None}).json()
    assert execution['status'] == 'running' and execution['steps'] == []

    eid = execution['id']
    for seq, (step_name, status) in enumerate([('deploy_vdb', 'succeeded'), ('validate', 'succeeded'),
                                          ('switch', 'succeeded'), ('reload_ontop', 'succeeded'),
                                          ('verify', 'succeeded')], start=1):
        api.post(f'/api/v1/executions/{eid}/steps',
                 json={'seq': seq, 'name': step_name, 'status': status, 'detail': {'ms': seq * 100}})
    api.post(f'/api/v1/executions/{eid}/validations',
             json={'validator': 'ontop_validate', 'passed': True, 'details': {'exit_code': 0}})
    finished = api.post(f'/api/v1/executions/{eid}/finish',
                       json={'status': 'succeeded', 'timings': {'total_ms': 12000}}).json()

    assert finished['status'] == 'succeeded' and finished['finished_at']
    assert [s['name'] for s in finished['steps']] == ['deploy_vdb', 'validate', 'switch',
                                                     'reload_ontop', 'verify']
    assert api.get(f'/api/v1/plans/{plan_id}').json()['status'] == 'executed'
    history = api.get(f'/api/v1/obdf/{obdf}/executions').json()
    assert history[0]['id'] == eid and history[0]['timings'] == {'total_ms': 12000}


def test_bluegreen_steps_are_accepted(api, obdf):
    """ADR-0022: order of the blue-green steps, including start_ontop (finding of VM test 422)."""
    plan_id = approved_plan(api, obdf)
    eid = api.post(f'/api/v1/plans/{plan_id}/executions').json()['id']
    steps = ['deploy_vdb', 'validate', 'start_ontop', 'verify', 'switch', 'sync']
    for seq, step_name in enumerate(steps, start=1):
        r = api.post(f'/api/v1/executions/{eid}/steps',
                     json={'seq': seq, 'name': step_name, 'status': 'succeeded',
                           'detail': {'instance': 'green'}})
        assert r.status_code in (200, 201), r.text
    finished = api.post(f'/api/v1/executions/{eid}/finish', json={'status': 'succeeded'}).json()
    assert [s['name'] for s in finished['steps']] == steps


def test_failed_execution_marks_plan_failed_and_notifies(api, obdf):
    plan_id = approved_plan(api, obdf)
    eid = api.post(f'/api/v1/plans/{plan_id}/executions').json()['id']
    api.post(f'/api/v1/executions/{eid}/steps',
             json={'seq': 1, 'name': 'deploy_vdb', 'status': 'failed',
                   'detail': {'errors': ['TEIID31259 invalid ddl']}})
    api.post(f'/api/v1/executions/{eid}/validations',
             json={'validator': 'teiid_status', 'passed': False, 'details': {'status': 'FAILED'}})
    result = api.post(f'/api/v1/executions/{eid}/finish',
                     json={'status': 'failed',
                           'failure': {'message': 'new VDB version FAILED'}}).json()
    assert result['status'] == 'failed'
    assert api.get(f'/api/v1/plans/{plan_id}').json()['status'] == 'failed'
    audit = [a['action'] for a in api.get(f'/api/v1/obdf/{obdf}/audit').json()]
    assert 'execution_failed' in audit


def test_rollback_is_recorded(api, obdf):
    plan_id = approved_plan(api, obdf)
    eid = api.post(f'/api/v1/plans/{plan_id}/executions').json()['id']
    api.post(f'/api/v1/executions/{eid}/steps',
             json={'seq': 6, 'name': 'rollback', 'status': 'succeeded',
                   'detail': {'reason': 'SPARQL verification failed'}})
    result = api.post(f'/api/v1/executions/{eid}/finish', json={'status': 'rolled_back'}).json()
    assert result['status'] == 'rolled_back'
    assert api.get(f'/api/v1/plans/{plan_id}').json()['status'] == 'failed'


def test_only_approved_plans_can_be_executed(api, obdf):
    plan = send_event(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')['plan']
    assert plan['status'] == 'pending_approval'
    r = api.post(f"/api/v1/plans/{plan['id']}/executions")
    assert r.status_code == 409 and 'approved' in r.json()['detail']


def test_second_running_execution_is_rejected(api, obdf):
    plan_id = approved_plan(api, obdf)
    api.post(f'/api/v1/plans/{plan_id}/executions')
    second = api.post(f'/api/v1/plans/{plan_id}/executions')
    assert second.status_code == 409 and 'still running' in second.json()['detail']


def test_finish_twice_is_rejected(api, obdf):
    plan_id = approved_plan(api, obdf)
    eid = api.post(f'/api/v1/plans/{plan_id}/executions').json()['id']
    api.post(f'/api/v1/executions/{eid}/finish', json={'status': 'succeeded'})
    again = api.post(f'/api/v1/executions/{eid}/finish', json={'status': 'failed'})
    assert again.status_code == 409


@pytest.mark.parametrize('path', ['/api/v1/executions/999999/steps',
                                  '/api/v1/executions/999999/validations',
                                  '/api/v1/executions/999999/finish'])
def test_unknown_execution_is_404(api, obdf, path):
    body = {'seq': 1, 'name': 'verify', 'status': 'succeeded'} if 'steps' in path else (
        {'validator': 'teiid_status', 'passed': True} if 'validations' in path
        else {'status': 'succeeded'})
    assert api.post(path, json=body).status_code == 404


def test_notified_plan_cannot_be_executed(api, obdf):
    plan = send_event(api, obdf, operation='drop', source='dukcapil', table='master_penduduk',
                 column='nik')['plan']
    assert plan['status'] == 'notified'
    r = api.post(f"/api/v1/plans/{plan['id']}/executions")
    assert r.status_code == 409
