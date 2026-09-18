"""Pekerja Executor: pengambilan rencana, pencacah, dan ketahanan terhadap galat."""
import pytest

import fixtures_plan as fx
from ascam_executor.config import Settings, baca_rahasia
from ascam_executor.engine import ExecutionError
from ascam_executor.worker import ExecutorWorker


class KnowledgeDenganRencana(fx.FakeKnowledge):
    def __init__(self, plans):
        super().__init__()
        self.plans = plans

    def obdf_id(self, name):
        return 1

    def approved_plans(self, obdf_id):
        return self.plans


class ExecutorPalsu:
    def __init__(self, hasil):
        self.hasil = hasil
        self.dijalankan = []

    def execute(self, plan):
        self.dijalankan.append(plan['id'])
        keluaran = self.hasil.pop(0)
        if isinstance(keluaran, Exception):
            raise keluaran
        return keluaran


def pekerja(plans, hasil):
    knowledge = KnowledgeDenganRencana(plans)
    executor = ExecutorPalsu(hasil)
    worker = ExecutorWorker(Settings(),
                            knowledge=knowledge, executor_factory=lambda k, o: executor)
    return worker, knowledge, executor


def test_plans_are_executed_in_order():
    worker, knowledge, executor = pekerja(
        [fx.plan_drop(plan_id=5), fx.plan_add(plan_id=3)],
        [{'status': 'succeeded', 'timings': {'total_ms': 12000}},
         {'status': 'succeeded', 'timings': {'total_ms': 9000}}])
    k, obdf_id, e = worker.build()
    worker.process_once(k, obdf_id, e)
    assert executor.dijalankan == [3, 5]                 # urut menurut id rencana
    assert worker.stats.executed == 2 and worker.stats.failed == 0
    assert worker.stats.last_timings == {'total_ms': 9000}     # rencana terakhir (id 5)


def test_rollback_and_failure_are_counted_and_do_not_stop_worker():
    worker, knowledge, executor = pekerja(
        [fx.plan_drop(plan_id=1), fx.plan_drop(plan_id=2), fx.plan_drop(plan_id=3)],
        [ExecutionError('VDB FAILED'), {'status': 'rolled_back', 'timings': {}},
         {'status': 'succeeded', 'timings': {}}])
    k, obdf_id, e = worker.build()
    worker.process_once(k, obdf_id, e)
    assert executor.dijalankan == [1, 2, 3]              # rencana berikutnya tetap dicoba
    assert (worker.stats.failed, worker.stats.rolled_back, worker.stats.executed) == (1, 1, 1)
    assert 'VDB FAILED' in worker.stats.last_error


def test_unexpected_error_is_captured():
    worker, knowledge, executor = pekerja([fx.plan_drop()], [RuntimeError('koneksi putus')])
    k, obdf_id, e = worker.build()
    worker.process_once(k, obdf_id, e)
    assert worker.stats.failed == 1 and 'koneksi putus' in worker.stats.last_error


def test_missing_targets_are_reported():
    class TanpaTarget(fx.FakeKnowledge):
        def obdf_id(self, name):
            return 1

        def targets(self, obdf_id):
            return [{'kind': 'kafka', 'enabled': True, 'endpoint': {}, 'credential_id': None}]
    worker = ExecutorWorker(Settings(), knowledge=TanpaTarget())
    with pytest.raises(RuntimeError, match='target belum terdaftar'):
        worker.build()


def test_secret_files_are_read_per_client(tmp_path):
    berkas = tmp_path / 'tokens'
    berkas.write_text('# komentar\nui:token-ui\nexecutor:token-exec\n')
    assert baca_rahasia(str(berkas), 'executor') == 'token-exec'
    polos = tmp_path / 'sandi'
    polos.write_text('Password12345_\n')
    assert baca_rahasia(str(polos)) == 'Password12345_'
    with pytest.raises(RuntimeError, match='tidak ada'):
        baca_rahasia(str(berkas), 'tidak-ada')


def test_health_reports_state():
    from fastapi.testclient import TestClient
    from ascam_executor.app import create_app
    worker, _, _ = pekerja([], [])
    with TestClient(create_app(Settings(), worker=worker, run_worker=False)) as client:
        body = client.get('/health').json()
    assert body['status'] == 'ok' and body['state'] == 'disabled'
