"""Orchestrator worker: sending events, error handling, and offsets."""
from dataclasses import dataclass

import pytest

from ascam_orchestrator.config import Settings
from ascam_orchestrator.worker import Orchestrator
from test_normalizer import PG_ROW, message


@dataclass
class Message:
    topic: str
    partition: int
    offset: int
    value: dict


class FakeKnowledge:
    def __init__(self, results=None, fail_times=0):
        self.posted = []
        self.results = results or {}
        self.fail_times = fail_times

    def obdf_id(self, name):
        return 1

    def sources(self, obdf_id):
        return [{'logical_name': 'kemensos', 'dbms': 'postgresql',
                 'kafka_topic': 'kemensos.schema_monitor.ddl_event_log'},
                {'logical_name': 'no-topic', 'dbms': 'mysql', 'kafka_topic': None}]

    def kafka_bootstrap(self, obdf_id):
        return 'kafka:9092'

    def post_event(self, obdf_id, payload):
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError('Knowledge cannot be reached')
        self.posted.append(payload)
        return self.results.get(payload['operation'],
                                {'event': {'status': 'planned'}, 'duplicate': False,
                                 'plan': {'id': 9, 'decision': 'auto', 'status': 'approved'}})


class FakeConsumer:
    def __init__(self, batches):
        self.batches = list(batches)
        self.commits = 0
        self.seeks = []
        self.closed = False

    def poll(self, timeout_ms=0):
        return self.batches.pop(0) if self.batches else {}

    def commit(self):
        self.commits += 1

    def seek(self, tp, offset):
        self.seeks.append((tp.topic, offset))

    def close(self):
        self.closed = True


def build(batches, knowledge, stop_after=None):
    consumer = FakeConsumer(batches)
    settings = Settings(token='t', retry_seconds=0)
    worker = Orchestrator(settings, knowledge=knowledge, consumer_factory=lambda t, b: consumer)
    original = consumer.poll

    def poll(timeout_ms=0):                       # stop after the batch is used up
        result = original(timeout_ms)
        if not consumer.batches:
            worker.stop()
        return result
    consumer.poll = poll
    return worker, consumer


TOPIC = 'kemensos.schema_monitor.ddl_event_log'


def test_messages_become_events_and_offsets_are_committed():
    knowledge = FakeKnowledge()
    batch = {('p', 0): [Message(TOPIC, 0, 1, message(PG_ROW))]}
    worker, consumer = build([batch], knowledge)
    worker.run()
    assert len(knowledge.posted) == 1 and knowledge.posted[0]['operation'] == 'add'
    assert consumer.commits == 1 and consumer.closed
    assert worker.stats.events_planned == 1 and worker.stats.messages == 1
    assert worker.stats.topics == [TOPIC]


def test_duplicate_and_ignored_events_are_counted():
    knowledge = FakeKnowledge(results={'add': {'event': {'status': 'planned'}, 'duplicate': True},
                                       'drop': {'event': {'status': 'ignored'}, 'duplicate': False}})
    row_drop = {**PG_ROW, 'ddl_command': 'ALTER TABLE public.x DROP COLUMN y;'}
    batch = {('p', 0): [Message(TOPIC, 0, 1, message(PG_ROW)), Message(TOPIC, 0, 2, message(row_drop))]}
    worker, _ = build([batch], knowledge)
    worker.run()
    assert worker.stats.duplicates == 1 and worker.stats.events_ignored == 1


def test_failure_does_not_commit_and_rewinds_offset():
    knowledge = FakeKnowledge(fail_times=1)
    batch = {('p', 0): [Message(TOPIC, 0, 7, message(PG_ROW))]}
    worker, consumer = build([batch], knowledge)
    worker.run()
    assert consumer.commits == 0                       # the offset does not advance on failure
    assert consumer.seeks == [(TOPIC, 7)]              # the message will be read again
    assert worker.stats.failures == 1 and 'cannot be reached' in worker.stats.last_error


def test_unknown_topic_is_skipped():
    knowledge = FakeKnowledge()
    batch = {('p', 0): [Message('unknown.topic', 0, 1, message(PG_ROW))]}
    worker, _ = build([batch], knowledge)
    worker.run()
    assert knowledge.posted == [] and worker.stats.messages == 0


def test_prepare_requires_topic():
    class NoTopics(FakeKnowledge):
        def sources(self, obdf_id):
            return [{'logical_name': 'x', 'dbms': 'mysql', 'kafka_topic': None}]
    worker = Orchestrator(Settings(token='t'), knowledge=NoTopics())
    with pytest.raises(RuntimeError, match='kafka_topic'):
        worker.prepare()


def test_health_endpoint_reports_stats():
    from fastapi.testclient import TestClient
    from ascam_orchestrator.app import create_app
    worker = Orchestrator(Settings(token='t'), knowledge=FakeKnowledge())
    with TestClient(create_app(Settings(token='t'), orchestrator=worker, run_worker=False)) as client:
        body = client.get('/health').json()
    assert body['status'] == 'ok' and body['state'] == 'starting' and body['messages'] == 0


# ── token selection ─────────────────────────────────────────────────────────────
def test_token_is_selected_by_client_name(tmp_path):
    token_file = tmp_path / 'tokens'
    token_file.write_text('# comment\nui:token-ui\norchestrator:token-orch\nexecutor:token-exec\n')
    assert Settings(token_file=str(token_file)).bearer() == 'token-orch'
    assert Settings(token_file=str(token_file), token_client='executor').bearer() == 'token-exec'
    plain_file = tmp_path / 'plain'
    plain_file.write_text('token-plain\n')
    assert Settings(token_file=str(plain_file)).bearer() == 'token-plain'
    with pytest.raises(RuntimeError, match='not found'):
        Settings(token_file=str(token_file), token_client='does-not-exist').bearer()


def test_worker_retries_preparation_until_knowledge_is_available():
    """Regression: a Knowledge that was restarting killed the worker for good."""
    class KnowledgeLate(FakeKnowledge):
        def __init__(self, failures_left):
            super().__init__()
            self.failures_left = failures_left

        def sources(self, obdf_id):
            if self.failures_left:
                self.failures_left -= 1
                raise ConnectionRefusedError('Knowledge not ready')
            return super().sources(obdf_id)

    knowledge = KnowledgeLate(failures_left=2)
    batch = {('p', 0): [Message(TOPIC, 0, 1, message(PG_ROW))]}
    worker, consumer = build([batch], knowledge)
    worker.run()
    assert worker.stats.state == 'stopped'
    assert len(knowledge.posted) == 1                  # the message is still processed after recovery
    assert worker.stats.last_error is None


def test_skipped_messages_are_counted_with_reason():
    knowledge = FakeKnowledge()
    batch = {('p', 0): [Message(TOPIC, 0, 5, {'op': 'u', 'after': PG_ROW})]}
    worker, consumer = build([batch], knowledge)
    worker.run()
    assert knowledge.posted == [] and worker.stats.skipped == 1
    assert worker.stats.last_skipped['offset'] == 5
    assert "'u'" in worker.stats.last_skipped['reason']
    assert consumer.commits == 1                        # skipped is not failed: the offset still advances


def test_uid_conflict_is_counted():
    knowledge = FakeKnowledge(results={'add': {'event': {'status': 'planned'}, 'duplicate': True,
                                               'conflict': True}})
    batch = {('p', 0): [Message(TOPIC, 0, 1, message(PG_ROW))]}
    worker, _ = build([batch], knowledge)
    worker.run()
    assert worker.stats.conflicts == 1 and worker.stats.duplicates == 1


def test_offset_reset_is_passed_to_consumer(monkeypatch):
    """After Knowledge is reset, a new group starts from the latest message so that old DDL is not replayed."""
    import ascam_orchestrator.worker as w
    received = {}

    class RecordingConsumer:
        def __init__(self, *topics, **kwargs):
            received.update(kwargs)

    monkeypatch.setattr(w, 'KafkaConsumer', RecordingConsumer)
    worker = Orchestrator(Settings(token='t', group_id='ascam-orchestrator-20260922', offset_reset='latest'))
    worker._consumer(['topic'], 'kafka:9092')
    assert received['auto_offset_reset'] == 'latest'
    assert received['group_id'] == 'ascam-orchestrator-20260922'


def test_invalid_offset_reset_is_rejected():
    with pytest.raises(ValueError, match='earliest or latest'):
        Settings(token='t', offset_reset='beginning')
