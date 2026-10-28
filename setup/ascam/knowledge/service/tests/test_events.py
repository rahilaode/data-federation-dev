"""Intake of schema events, creation of plans, and the HITL approval flow."""
import uuid

import pytest

import fixtures_obdf as fx
from test_impact import obdf                      # noqa: F401 — synced OBDF fixture
from test_sync import set_clients


def send_event(api, oid, **body):
    r = api.post(f'/api/v1/obdf/{oid}/events', json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_auto_event_creates_approved_plan_with_actions(api, obdf):
    out = send_event(api, obdf, operation='drop', source='kemensos', schema='public',
                table='penerima_manfaat', column='status_ekonomi',
                raw={'origin': 'debezium'})
    assert out['event']['status'] == 'planned' and out['duplicate'] is False
    plan = out['plan']
    assert plan['decision'] == 'auto' and plan['status'] == 'approved' and plan['pattern'] == 'P-002'
    assert [(a['seq'], a['artifact'], a['operation']) for a in plan['actions']] == [
        (1, 'vdb', 'drop_column'), (2, 'r2rml', 'remove_predicate_object_map'),
        (3, 'ontology', 'deprecate_property')]
    assert plan['impact']['targets'][0]['column'] == 'status_ekonomi'


def test_event_is_idempotent(api, obdf):
    uid = str(uuid.uuid4())
    first = send_event(api, obdf, event_uid=uid, operation='drop', source='kemensos', schema='public',
                  table='penerima_manfaat', column='status_ekonomi')
    second = send_event(api, obdf, event_uid=uid, operation='drop', source='kemensos', schema='public',
                   table='penerima_manfaat', column='status_ekonomi')
    assert second['duplicate'] is True
    assert second['event']['id'] == first['event']['id']
    assert second['plan']['id'] == first['plan']['id']
    assert len(api.get(f'/api/v1/obdf/{obdf}/plans').json()) == 1


def test_unsafe_drop_is_notified_without_actions(api, obdf):
    """ADR-0024: a DROP that is not shown to be safe gives no plan that can be approved, only a
    notification that names the affected elements."""
    out = send_event(api, obdf, operation='drop', source='dukcapil', table='master_penduduk', column='nik')
    plan = out['plan']
    assert plan['decision'] == 'notify' and plan['status'] == 'notified' and plan['actions'] == []
    assert any('primary key' in r for r in plan['reasons'])
    assert plan['impact']['affected']['triples_maps']
    assert api.get(f'/api/v1/obdf/{obdf}/plans', params={'status': 'pending_approval'}).json() == []
    assert api.get(f'/api/v1/obdf/{obdf}/plans', params={'status': 'approved'}).json() == []
    for action in ('approve', 'reject'):
        assert api.post(f"/api/v1/plans/{plan['id']}/{action}").status_code == 409
    messages = [n['message'] for n in api.get(f'/api/v1/obdf/{obdf}/notifications').json()
             if n['plan_id'] == plan['id']]
    assert messages and 'was not executed' in messages[0] and 'TriplesMap' in messages[0]
    assert 'run a sync' in messages[0]


def test_hitl_event_waits_for_approval(api, obdf):
    plan = send_event(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')['plan']
    assert plan['decision'] == 'hitl' and plan['status'] == 'pending_approval'
    assert any('modelling decision' in r for r in plan['reasons'])
    pending = api.get(f'/api/v1/obdf/{obdf}/plans', params={'status': 'pending_approval'}).json()
    assert [p['id'] for p in pending] == [plan['id']]

    approved = api.post(f"/api/v1/plans/{plan['id']}/approve", json={'note': 'approved by admin'}).json()
    assert approved['status'] == 'approved' and approved['decided_by'] == 'ui:tester'
    again = api.post(f"/api/v1/plans/{plan['id']}/approve")
    assert again.status_code == 409


def test_plan_can_be_rejected(api, obdf):
    plan = send_event(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')['plan']
    rejected = api.post(f"/api/v1/plans/{plan['id']}/reject", json={'note': 'not approved'}).json()
    assert rejected['status'] == 'rejected'
    assert api.post(f"/api/v1/plans/{plan['id']}/approve").status_code == 409


def test_stale_plan_cannot_be_approved(api, obdf):
    """The specification changed after the plan was created: the plan is marked superseded."""
    plan = send_event(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')['plan']
    set_clients(api, meta=fx.FakeMetadata(fx.metadata(extra_column=True)))
    assert api.post(f'/api/v1/obdf/{obdf}/sync').json()['changed']
    r = api.post(f"/api/v1/plans/{plan['id']}/approve")
    assert r.status_code == 409 and 'has changed' in r.json()['detail']
    assert api.get(f"/api/v1/plans/{plan['id']}").json()['status'] == 'superseded'


def test_ignored_event_creates_no_plan(api, obdf):
    out = send_event(api, obdf, operation='drop', source='kemensos', schema='public',
                table='no_such_table', column='x')
    assert out['event']['status'] == 'ignored' and out['plan'] is None
    assert 'not found' in out['event']['ignore_reason']
    assert api.get(f'/api/v1/obdf/{obdf}/plans').json() == []


def test_unknown_source_is_ignored(api, obdf):
    out = send_event(api, obdf, operation='drop', source='unknown-source', table='t', column='c')
    assert out['event']['status'] == 'ignored' and out['event']['source_system_id'] is None


def test_add_event_plans_property_and_mapping(api, obdf):
    plan = send_event(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat_lengkap',
                 column_type='varchar(200)')['plan']
    assert plan['decision'] == 'hitl' and plan['pattern'] == 'P-001'
    ontology = [a for a in plan['actions'] if a['artifact'] == 'ontology'][0]
    assert ontology['params']['iri'].endswith('/alamatLengkap')


def test_event_history_and_audit(api, obdf):
    # the event carries the column name IN THE SOURCE (tgl_lahir_ktp), not the Teiid column name (tanggal_lahir)
    send_event(api, obdf, operation='rename', source='dukcapil', table='master_penduduk',
          column='tgl_lahir_ktp', new_column='tgl_lahir_baru')
    events = api.get(f'/api/v1/obdf/{obdf}/events').json()
    assert events[0]['structured']['operation'] == 'rename' and events[0]['status'] == 'planned'
    actions = [a['action'] for a in api.get(f'/api/v1/obdf/{obdf}/audit').json()]
    assert 'plan_created' in actions


def test_event_uses_source_column_name_not_teiid_name(api, obdf):
    """Events hold the column name in the source; the Teiid column name may differ (NAMEINSOURCE)."""
    by_source_name = send_event(api, obdf, operation='rename', source='dukcapil', table='master_penduduk',
                      column='tgl_lahir_ktp', new_column='tgl_lahir_2026')
    assert by_source_name['plan']['impact']['targets'][0]['column'] == 'tanggal_lahir'
    by_teiid_name = send_event(api, obdf, operation='rename', source='dukcapil', table='master_penduduk',
                       column='tanggal_lahir', new_column='x')
    assert by_teiid_name['event']['status'] == 'ignored'


def test_deterministic_uuid_and_unsupported_operation(api, obdf):
    """A version-5 (deterministic) UUID is accepted; unsupported DDL is reported to the administrator."""
    import uuid as _uuid
    uid = str(_uuid.uuid5(_uuid.NAMESPACE_URL, 'ascam:topic:0:42:0'))
    first = send_event(api, obdf, event_uid=uid, operation='other', source='kemensos',
                  table='penerima_manfaat', column='status_ekonomi',
                  raw={'ddl_command': 'ALTER TABLE penerima_manfaat ALTER COLUMN x TYPE text'})
    assert first['event']['status'] == 'planned'
    plan = first['plan']
    assert plan['decision'] == 'notify' and plan['pattern'] is None and plan['actions'] == []
    assert any('not supported' in r for r in plan['reasons'])
    assert send_event(api, obdf, event_uid=uid, operation='other', source='kemensos',
                 table='penerima_manfaat')['duplicate'] is True


def test_plan_can_be_superseded(api, obdf):
    plan_id = send_event(api, obdf, operation='drop', source='kemensos', schema='public',
                    table='penerima_manfaat', column='status_ekonomi')['plan']['id']
    result = api.post(f'/api/v1/plans/{plan_id}/supersede', json={'note': 'stale base version'}).json()
    assert result['status'] == 'superseded'
    assert api.get(f'/api/v1/obdf/{obdf}/plans', params={'status': 'approved'}).json() == []
    assert api.post(f'/api/v1/plans/{plan_id}/supersede').status_code == 409
    assert 'plan_superseded' in [a['action'] for a in api.get(f'/api/v1/obdf/{obdf}/audit').json()]


def test_uid_reuse_with_different_content_is_flagged(api, obdf):
    """Regression F6: an old uid is reused for another DDL after the Kafka topic was recreated."""
    uid = str(uuid.uuid4())
    send_event(api, obdf, event_uid=uid, operation='drop', source='kemensos', schema='public',
          table='penerima_manfaat', column='status_ekonomi')
    same = send_event(api, obdf, event_uid=uid, operation='drop', source='kemensos', schema='public',
                 table='penerima_manfaat', column='status_ekonomi')
    assert same['duplicate'] is True and same['conflict'] is False
    different = send_event(api, obdf, event_uid=uid, operation='add', source='kemensos', schema='public',
                 table='ascam_probe', column='test')
    assert different['duplicate'] is True and different['conflict'] is True
    assert 'event_uid_conflict' in [a['action'] for a in api.get(f'/api/v1/obdf/{obdf}/audit').json()]



def test_hitl_add_creates_descriptive_notification_and_can_be_acknowledged(api, obdf):
    plan = send_event(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')['plan']
    assert plan['status'] == 'pending_approval' and len(plan['actions']) >= 3
    unread = api.get(f'/api/v1/obdf/{obdf}/notifications', params={'unread': True}).json()
    own = [n for n in unread if n['plan_id'] == plan['id']]
    assert own and 'ADD change on kemensos:public.penerima_manfaat.alamat' in own[0]['message']
    assert 'waiting for approval' in own[0]['message']

    read_back = api.post(f"/api/v1/notifications/{own[0]['id']}/ack").json()
    assert read_back['acknowledged'] is True and read_back['acknowledged_by'] == 'ui:tester'
    remaining = api.get(f'/api/v1/obdf/{obdf}/notifications', params={'unread': True}).json()
    assert all(n['id'] != own[0]['id'] for n in remaining)
    assert api.post('/api/v1/notifications/999999/ack').status_code == 404


def test_approval_acknowledges_plan_notification(api, obdf):
    plan = send_event(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')['plan']
    api.post(f"/api/v1/plans/{plan['id']}/approve", json={'note': 'the property name is right'})
    every = api.get(f'/api/v1/obdf/{obdf}/notifications').json()
    assert all(n['acknowledged'] for n in every if n['plan_id'] == plan['id'])


def test_only_ui_client_may_assert_human_actor(api, obdf):
    """A HITL approval is recorded under the administrator's name, not only the token client's."""
    plan = send_event(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')['plan']
    result = api.post(f"/api/v1/plans/{plan['id']}/approve",
                     headers={'X-ASCAM-User': 'rahil.admin'}).json()
    assert result['decided_by'] == 'ui:rahil.admin'


def test_malformed_actor_header_is_ignored(api, obdf):
    plan = send_event(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')['plan']
    result = api.post(f"/api/v1/plans/{plan['id']}/reject",
                     headers={'X-ASCAM-User': 'admin\nFAKE: approved'}).json()
    assert result['decided_by'] == 'ui'                  # a malicious header is ignored


# ── revision of ADD plans by the administrator (ADR-0024) ───────────────────────
def test_admin_can_rename_property_when_approving(api, obdf):
    plan = send_event(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')['plan']
    proposal = plan['impact']['add_options']
    assert proposal['proposed_iri'].endswith('/alamat')
    result = api.post(f"/api/v1/plans/{plan['id']}/approve",
                     json={'note': 'clearer name', 'property_name': 'alamatDomisili',
                           'domain': proposal['proposed_domain']})
    assert result.status_code == 200, result.text
    revision = result.json()
    assert revision['id'] != plan['id'] and revision['revision_of'] == plan['id']
    assert revision['status'] == 'approved' and revision['decided_by'] == 'ui:tester'
    iri = 'http://bansos.go.id/ontology/alamatDomisili'
    assert [(a['seq'], a['artifact'], a['operation']) for a in revision['actions']][:2] == [
        (1, 'vdb', 'add_column'), (2, 'ontology', 'add_datatype_property')]
    assert all(a['params'].get('predicate_iri', iri) == iri and a['params'].get('iri', iri) == iri
               for a in revision['actions'] if a['artifact'] != 'vdb')
    assert {a['params']['triples_map_iri'].rsplit('#', 1)[-1] for a in revision['actions']
            if a['artifact'] == 'r2rml'} == {'MapPenerima', 'MapRingkas'}
    original = api.get(f"/api/v1/plans/{plan['id']}").json()
    assert original['status'] == 'revised'
    approved_plans = api.get(f'/api/v1/obdf/{obdf}/plans', params={'status': 'approved'}).json()
    assert [p['id'] for p in approved_plans] == [revision['id']]
    assert api.post(f"/api/v1/plans/{plan['id']}/approve").status_code == 409


def test_unchanged_choice_approves_original_plan(api, obdf):
    plan = send_event(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')['plan']
    proposal = plan['impact']['add_options']
    result = api.post(f"/api/v1/plans/{plan['id']}/approve",
                     json={'property_name': 'alamat', 'domain': proposal['proposed_domain']}).json()
    assert result['id'] == plan['id'] and result['status'] == 'approved'


@pytest.mark.parametrize('choice, fragment', [
    ({'property_name': '1invalid'}, 'must start with a letter'),
    ({'property_name': 'nik', 'domain': 'http://bansos.go.id/ontology/PenerimaBansos'}, 'different domain'),
    ({'property_name': 'alamatBaru', 'domain': 'http://bansos.go.id/ontology/OtherClass'}, 'one of the classes'),
])
def test_invalid_admin_choice_is_refused(api, obdf, choice, fragment):
    plan = send_event(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')['plan']
    r = api.post(f"/api/v1/plans/{plan['id']}/approve", json=choice)
    assert r.status_code == 422 and fragment in r.json()['detail']
    assert api.get(f"/api/v1/plans/{plan['id']}").json()['status'] == 'pending_approval'


def test_property_choice_only_for_add_and_approval(api, obdf):
    plan = send_event(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')['plan']
    r = api.post(f"/api/v1/plans/{plan['id']}/reject", json={'property_name': 'alamatBaru'})
    assert r.status_code == 422
