"""Penerimaan event skema, pembentukan rencana, dan alur persetujuan HITL."""
import uuid

import pytest

import fixtures_obdf as fx
from test_impact import obdf                      # noqa: F401 — fixture OBDF tersinkron
from test_sync import set_clients


def kirim(api, oid, **body):
    r = api.post(f'/api/v1/obdf/{oid}/events', json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_auto_event_creates_approved_plan_with_actions(api, obdf):
    out = kirim(api, obdf, operation='drop', source='kemensos', schema='public',
                table='penerima_manfaat', column='status_ekonomi',
                raw={'asal': 'debezium'})
    assert out['event']['status'] == 'planned' and out['duplicate'] is False
    plan = out['plan']
    assert plan['decision'] == 'auto' and plan['status'] == 'approved' and plan['pattern'] == 'P-002'
    assert [(a['seq'], a['artifact'], a['operation']) for a in plan['actions']] == [
        (1, 'vdb', 'drop_column'), (2, 'r2rml', 'remove_predicate_object_map'),
        (3, 'ontology', 'deprecate_property')]
    assert plan['impact']['targets'][0]['column'] == 'status_ekonomi'


def test_event_is_idempotent(api, obdf):
    uid = str(uuid.uuid4())
    first = kirim(api, obdf, event_uid=uid, operation='drop', source='kemensos', schema='public',
                  table='penerima_manfaat', column='status_ekonomi')
    second = kirim(api, obdf, event_uid=uid, operation='drop', source='kemensos', schema='public',
                   table='penerima_manfaat', column='status_ekonomi')
    assert second['duplicate'] is True
    assert second['event']['id'] == first['event']['id']
    assert second['plan']['id'] == first['plan']['id']
    assert len(api.get(f'/api/v1/obdf/{obdf}/plans').json()) == 1


def test_hitl_event_waits_for_approval(api, obdf):
    out = kirim(api, obdf, operation='drop', source='dukcapil', table='master_penduduk', column='nik')
    plan = out['plan']
    assert plan['decision'] == 'hitl' and plan['status'] == 'pending_approval'
    assert any('kunci primer' in r for r in plan['reasons'])
    pending = api.get(f'/api/v1/obdf/{obdf}/plans', params={'status': 'pending_approval'}).json()
    assert [p['id'] for p in pending] == [plan['id']]

    approved = api.post(f"/api/v1/plans/{plan['id']}/approve", json={'note': 'disetujui admin'}).json()
    assert approved['status'] == 'approved' and approved['decided_by'] == 'ui:penguji'
    lagi = api.post(f"/api/v1/plans/{plan['id']}/approve")
    assert lagi.status_code == 409


def test_plan_can_be_rejected(api, obdf):
    plan = kirim(api, obdf, operation='drop', source='dukcapil', table='master_penduduk',
                 column='nik')['plan']
    rejected = api.post(f"/api/v1/plans/{plan['id']}/reject", json={'note': 'tidak disetujui'}).json()
    assert rejected['status'] == 'rejected'
    assert api.post(f"/api/v1/plans/{plan['id']}/approve").status_code == 409


def test_stale_plan_cannot_be_approved(api, obdf):
    """Spesifikasi berubah sejak rencana dibuat: rencana ditandai superseded."""
    plan = kirim(api, obdf, operation='drop', source='dukcapil', table='master_penduduk',
                 column='nik')['plan']
    set_clients(api, meta=fx.FakeMetadata(fx.metadata(extra_column=True)))
    assert api.post(f'/api/v1/obdf/{obdf}/sync').json()['changed']
    r = api.post(f"/api/v1/plans/{plan['id']}/approve")
    assert r.status_code == 409 and 'sudah berubah' in r.json()['detail']
    assert api.get(f"/api/v1/plans/{plan['id']}").json()['status'] == 'superseded'


def test_ignored_event_creates_no_plan(api, obdf):
    out = kirim(api, obdf, operation='drop', source='kemensos', schema='public',
                table='tabel_entah_apa', column='x')
    assert out['event']['status'] == 'ignored' and out['plan'] is None
    assert 'tidak ditemukan' in out['event']['ignore_reason']
    assert api.get(f'/api/v1/obdf/{obdf}/plans').json() == []


def test_unknown_source_is_ignored(api, obdf):
    out = kirim(api, obdf, operation='drop', source='sumber-asing', table='t', column='c')
    assert out['event']['status'] == 'ignored' and out['event']['source_system_id'] is None


def test_add_event_plans_property_and_mapping(api, obdf):
    plan = kirim(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat_lengkap',
                 column_type='varchar(200)')['plan']
    assert plan['decision'] == 'auto' and plan['pattern'] == 'P-001'
    ontology = [a for a in plan['actions'] if a['artifact'] == 'ontology'][0]
    assert ontology['params']['iri'].endswith('/alamatLengkap')


def test_event_history_and_audit(api, obdf):
    # event membawa nama kolom DI SUMBER (tgl_lahir_ktp), bukan nama kolom Teiid (tanggal_lahir)
    kirim(api, obdf, operation='rename', source='dukcapil', table='master_penduduk',
          column='tgl_lahir_ktp', new_column='tgl_lahir_baru')
    events = api.get(f'/api/v1/obdf/{obdf}/events').json()
    assert events[0]['structured']['operation'] == 'rename' and events[0]['status'] == 'planned'
    actions = [a['action'] for a in api.get(f'/api/v1/obdf/{obdf}/audit').json()]
    assert 'plan_created' in actions


def test_event_uses_source_column_name_not_teiid_name(api, obdf):
    """Event memuat nama kolom di sumber; nama kolom Teiid dapat berbeda (NAMEINSOURCE)."""
    di_sumber = kirim(api, obdf, operation='rename', source='dukcapil', table='master_penduduk',
                      column='tgl_lahir_ktp', new_column='tgl_lahir_2026')
    assert di_sumber['plan']['impact']['targets'][0]['column'] == 'tanggal_lahir'
    nama_teiid = kirim(api, obdf, operation='rename', source='dukcapil', table='master_penduduk',
                       column='tanggal_lahir', new_column='x')
    assert nama_teiid['event']['status'] == 'ignored'


def test_deterministic_uuid_and_unsupported_operation(api, obdf):
    """UUID versi 5 (deterministik) diterima; DDL tak didukung tetap masuk antrean HITL."""
    import uuid as _uuid
    uid = str(_uuid.uuid5(_uuid.NAMESPACE_URL, 'ascam:topik:0:42:0'))
    first = kirim(api, obdf, event_uid=uid, operation='other', source='kemensos',
                  table='penerima_manfaat', column='status_ekonomi',
                  raw={'ddl_command': 'ALTER TABLE penerima_manfaat ALTER COLUMN x TYPE text'})
    assert first['event']['status'] == 'planned'
    plan = first['plan']
    assert plan['decision'] == 'hitl' and plan['pattern'] is None and plan['actions'] == []
    assert any('tidak didukung' in r for r in plan['reasons'])
    assert kirim(api, obdf, event_uid=uid, operation='other', source='kemensos',
                 table='penerima_manfaat')['duplicate'] is True


def test_plan_can_be_superseded(api, obdf):
    plan_id = kirim(api, obdf, operation='drop', source='kemensos', schema='public',
                    table='penerima_manfaat', column='status_ekonomi')['plan']['id']
    hasil = api.post(f'/api/v1/plans/{plan_id}/supersede', json={'note': 'versi dasar usang'}).json()
    assert hasil['status'] == 'superseded'
    assert api.get(f'/api/v1/obdf/{obdf}/plans', params={'status': 'approved'}).json() == []
    assert api.post(f'/api/v1/plans/{plan_id}/supersede').status_code == 409
    assert 'plan_superseded' in [a['action'] for a in api.get(f'/api/v1/obdf/{obdf}/audit').json()]


def test_uid_reuse_with_different_content_is_flagged(api, obdf):
    """Regresi F6: uid lama dipakai ulang untuk DDL lain setelah topik Kafka dibuat ulang."""
    uid = str(uuid.uuid4())
    kirim(api, obdf, event_uid=uid, operation='drop', source='kemensos', schema='public',
          table='penerima_manfaat', column='status_ekonomi')
    sama = kirim(api, obdf, event_uid=uid, operation='drop', source='kemensos', schema='public',
                 table='penerima_manfaat', column='status_ekonomi')
    assert sama['duplicate'] is True and sama['conflict'] is False
    beda = kirim(api, obdf, event_uid=uid, operation='add', source='kemensos', schema='public',
                 table='ascam_probe', column='uji')
    assert beda['duplicate'] is True and beda['conflict'] is True
    assert 'event_uid_conflict' in [a['action'] for a in api.get(f'/api/v1/obdf/{obdf}/audit').json()]



def test_hitl_add_creates_descriptive_notification_and_can_be_acknowledged(api, obdf):
    api.put(f'/api/v1/obdf/{obdf}/settings/adaptation.add_column', json={'value': {'mode': 'hitl'}})
    plan = kirim(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')['plan']
    assert plan['status'] == 'pending_approval' and len(plan['actions']) >= 3
    belum = api.get(f'/api/v1/obdf/{obdf}/notifications', params={'unread': True}).json()
    milik = [n for n in belum if n['plan_id'] == plan['id']]
    assert milik and 'ADD pada kemensos:public.penerima_manfaat.alamat' in milik[0]['message']
    assert 'menunggu persetujuan' in milik[0]['message']

    dibaca = api.post(f"/api/v1/notifications/{milik[0]['id']}/ack").json()
    assert dibaca['acknowledged'] is True and dibaca['acknowledged_by'] == 'ui:penguji'
    sisa = api.get(f'/api/v1/obdf/{obdf}/notifications', params={'unread': True}).json()
    assert all(n['id'] != milik[0]['id'] for n in sisa)
    assert api.post('/api/v1/notifications/999999/ack').status_code == 404


def test_approval_acknowledges_plan_notification(api, obdf):
    api.put(f'/api/v1/obdf/{obdf}/settings/adaptation.add_column', json={'value': {'mode': 'hitl'}})
    plan = kirim(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')['plan']
    api.post(f"/api/v1/plans/{plan['id']}/approve", json={'note': 'nama property sudah tepat'})
    semua = api.get(f'/api/v1/obdf/{obdf}/notifications').json()
    assert all(n['acknowledged'] for n in semua if n['plan_id'] == plan['id'])


def test_only_ui_client_may_assert_human_actor(api, obdf):
    """Persetujuan HITL tercatat atas nama administrator, bukan hanya nama klien token."""
    api.put(f'/api/v1/obdf/{obdf}/settings/adaptation.add_column', json={'value': {'mode': 'hitl'}})
    plan = kirim(api, obdf, operation='add', source='kemensos', schema='public',
                 table='penerima_manfaat', column='alamat', column_type='varchar(50)')['plan']
    hasil = api.post(f"/api/v1/plans/{plan['id']}/approve",
                     headers={'X-ASCAM-User': 'rahil.admin'}).json()
    assert hasil['decided_by'] == 'ui:rahil.admin'


def test_malformed_actor_header_is_ignored(api, obdf):
    plan = kirim(api, obdf, operation='drop', source='dukcapil', table='master_penduduk',
                 column='nik')['plan']
    hasil = api.post(f"/api/v1/plans/{plan['id']}/reject",
                     headers={'X-ASCAM-User': 'admin\nPALSU: disetujui'}).json()
    assert hasil['decided_by'] == 'ui'                  # header berbahaya diabaikan
