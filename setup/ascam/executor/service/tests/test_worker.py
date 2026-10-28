"""Executor worker: taking plans, counters, and resilience to errors."""
import pytest

import fixtures_plan as fx
from ascam_executor.config import Settings, read_secret
from ascam_executor.engine import ExecutionError
from ascam_executor.worker import ExecutorWorker


class KnowledgeWithPlans(fx.FakeKnowledge):
    def __init__(self, plans):
        super().__init__()
        self.plans = plans

    def obdf_id(self, name):
        return 1

    def approved_plans(self, obdf_id):
        return self.plans


class FakeExecutor:
    def __init__(self, results):
        self.results = results
        self.executed = []

    def execute(self, plan):
        self.executed.append(plan['id'])
        outcome = self.results.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_worker(plans, results):
    knowledge = KnowledgeWithPlans(plans)
    executor = FakeExecutor(results)
    worker = ExecutorWorker(Settings(),
                            knowledge=knowledge, executor_factory=lambda k, o: executor)
    return worker, knowledge, executor


def test_plans_are_executed_in_order():
    worker, knowledge, executor = make_worker(
        [fx.plan_drop(plan_id=5), fx.plan_add(plan_id=3)],
        [{'status': 'succeeded', 'timings': {'total_ms': 12000}},
         {'status': 'succeeded', 'timings': {'total_ms': 9000}}])
    k, obdf_id, e = worker.build()
    worker.process_once(k, obdf_id, e)
    assert executor.executed == [3, 5]                 # in order of plan id
    assert worker.stats.executed == 2 and worker.stats.failed == 0
    assert worker.stats.last_timings == {'total_ms': 9000}     # the last plan (id 5)


def test_rollback_and_failure_are_counted_and_do_not_stop_worker():
    worker, knowledge, executor = make_worker(
        [fx.plan_drop(plan_id=1), fx.plan_drop(plan_id=2), fx.plan_drop(plan_id=3)],
        [ExecutionError('VDB FAILED'), {'status': 'rolled_back', 'timings': {}},
         {'status': 'succeeded', 'timings': {}}])
    k, obdf_id, e = worker.build()
    worker.process_once(k, obdf_id, e)
    assert executor.executed == [1, 2, 3]              # the next plans are still tried
    assert (worker.stats.failed, worker.stats.rolled_back, worker.stats.executed) == (1, 1, 1)
    assert 'VDB FAILED' in worker.stats.last_error


def test_unexpected_error_is_captured():
    worker, knowledge, executor = make_worker([fx.plan_drop()], [RuntimeError('connection lost')])
    k, obdf_id, e = worker.build()
    worker.process_once(k, obdf_id, e)
    assert worker.stats.failed == 1 and 'connection lost' in worker.stats.last_error


def test_missing_targets_are_reported():
    class WithoutTargets(fx.FakeKnowledge):
        def obdf_id(self, name):
            return 1

        def targets(self, obdf_id):
            return [{'kind': 'kafka', 'enabled': True, 'endpoint': {}, 'credential_id': None}]
    worker = ExecutorWorker(Settings(), knowledge=WithoutTargets())
    with pytest.raises(RuntimeError, match='targets not registered'):
        worker.build()


def test_secret_files_are_read_per_client(tmp_path):
    token_file = tmp_path / 'tokens'
    token_file.write_text('# comment\nui:token-ui\nexecutor:token-exec\n')
    assert read_secret(str(token_file), 'executor') == 'token-exec'
    plain_file = tmp_path / 'password'
    plain_file.write_text('Password12345_\n')
    assert read_secret(str(plain_file)) == 'Password12345_'
    with pytest.raises(RuntimeError, match='not found'):
        read_secret(str(token_file), 'does-not-exist')


def test_health_reports_state():
    from fastapi.testclient import TestClient
    from ascam_executor.app import create_app
    worker, _, _ = make_worker([], [])
    with TestClient(create_app(Settings(), worker=worker, run_worker=False)) as client:
        body = client.get('/health').json()
    assert body['status'] == 'ok' and body['state'] == 'disabled'


def test_worker_retries_build_until_knowledge_is_available():
    """Regression: a set-up that failed once killed the worker for good."""
    class KnowledgeLate(fx.FakeKnowledge):
        def __init__(self):
            super().__init__()
            self.remaining = 2

        def obdf_id(self, name):
            if self.remaining:
                self.remaining -= 1
                raise ConnectionRefusedError('Knowledge not ready')
            return 1

        def approved_plans(self, obdf_id):
            return []

    knowledge = KnowledgeLate()
    worker = ExecutorWorker(Settings(poll_seconds=0), knowledge=knowledge,
                            executor_factory=lambda k, o: FakeExecutor([]))
    import threading
    stopper = threading.Timer(0.5, worker.stop)
    stopper.start()
    worker.run()
    stopper.cancel()
    assert knowledge.remaining == 0 and worker.stats.state == 'stopped'
    assert worker.stats.obdf_id == 1                   # the set-up eventually succeeds


def test_pause_and_resume_endpoints():
    from fastapi.testclient import TestClient
    from ascam_executor.app import create_app
    worker, _, _ = make_worker([], [])
    with TestClient(create_app(Settings(), worker=worker, run_worker=False)) as client:
        assert client.post('/control/pause').json() == {'paused': True}
        assert client.get('/health').json()['paused'] is True
        assert client.post('/control/resume').json() == {'paused': False}
        assert client.get('/health').json()['paused'] is False


def test_paused_worker_does_not_take_plans():
    import threading
    worker, knowledge, executor = make_worker([fx.plan_drop()], [{'status': 'succeeded', 'timings': {}}])
    worker.settings.poll_seconds = 0
    worker.pause()
    stopper = threading.Timer(0.4, worker.stop)
    stopper.start()
    worker.run()
    stopper.cancel()
    assert executor.executed == []                   # no plan is taken while paused
