"""Pekerja Orchestrator: pengiriman event, penanganan galat, dan offset."""
from dataclasses import dataclass

import pytest

from ascam_orchestrator.config import Settings
from ascam_orchestrator.worker import Orchestrator
from test_normalizer import PG_ROW, pesan


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
                {'logical_name': 'tanpa-topik', 'dbms': 'mysql', 'kafka_topic': None}]

    def kafka_bootstrap(self, obdf_id):
        return 'kafka:9092'

    def post_event(self, obdf_id, payload):
        if self.fail_times:
            self.fail_times -= 1
            raise RuntimeError('Knowledge tidak dapat dihubungi')
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

    def poll(timeout_ms=0):                       # hentikan setelah batch habis
        result = original(timeout_ms)
        if not consumer.batches:
            worker.stop()
        return result
    consumer.poll = poll
    return worker, consumer


TOPIC = 'kemensos.schema_monitor.ddl_event_log'


def test_messages_become_events_and_offsets_are_committed():
    knowledge = FakeKnowledge()
    batch = {('p', 0): [Message(TOPIC, 0, 1, pesan(PG_ROW))]}
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
    batch = {('p', 0): [Message(TOPIC, 0, 1, pesan(PG_ROW)), Message(TOPIC, 0, 2, pesan(row_drop))]}
    worker, _ = build([batch], knowledge)
    worker.run()
    assert worker.stats.duplicates == 1 and worker.stats.events_ignored == 1


def test_failure_does_not_commit_and_rewinds_offset():
    knowledge = FakeKnowledge(fail_times=1)
    batch = {('p', 0): [Message(TOPIC, 0, 7, pesan(PG_ROW))]}
    worker, consumer = build([batch], knowledge)
    worker.run()
    assert consumer.commits == 0                       # offset tidak maju saat gagal
    assert consumer.seeks == [(TOPIC, 7)]              # pesan akan dibaca ulang
    assert worker.stats.failures == 1 and 'tidak dapat dihubungi' in worker.stats.last_error


def test_unknown_topic_is_skipped():
    knowledge = FakeKnowledge()
    batch = {('p', 0): [Message('topik.asing', 0, 1, pesan(PG_ROW))]}
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


# ── pemilihan token ─────────────────────────────────────────────────────────────
def test_token_is_selected_by_client_name(tmp_path):
    berkas = tmp_path / 'tokens'
    berkas.write_text('# komentar\nui:token-ui\norchestrator:token-orch\nexecutor:token-exec\n')
    assert Settings(token_file=str(berkas)).bearer() == 'token-orch'
    assert Settings(token_file=str(berkas), token_client='executor').bearer() == 'token-exec'
    polos = tmp_path / 'polos'
    polos.write_text('token-polos\n')
    assert Settings(token_file=str(polos)).bearer() == 'token-polos'
    with pytest.raises(RuntimeError, match='tidak ada'):
        Settings(token_file=str(berkas), token_client='tidak-ada').bearer()


def test_worker_retries_preparation_until_knowledge_is_available():
    """Regresi: Knowledge yang sedang restart membuat pekerja mati permanen."""
    class KnowledgeTerlambat(FakeKnowledge):
        def __init__(self, gagal):
            super().__init__()
            self.gagal = gagal

        def sources(self, obdf_id):
            if self.gagal:
                self.gagal -= 1
                raise ConnectionRefusedError('Knowledge belum siap')
            return super().sources(obdf_id)

    knowledge = KnowledgeTerlambat(gagal=2)
    batch = {('p', 0): [Message(TOPIC, 0, 1, pesan(PG_ROW))]}
    worker, consumer = build([batch], knowledge)
    worker.run()
    assert worker.stats.state == 'stopped'
    assert len(knowledge.posted) == 1                  # pesan tetap diproses setelah pulih
    assert worker.stats.last_error is None


def test_skipped_messages_are_counted_with_reason():
    knowledge = FakeKnowledge()
    batch = {('p', 0): [Message(TOPIC, 0, 5, {'op': 'u', 'after': PG_ROW})]}
    worker, consumer = build([batch], knowledge)
    worker.run()
    assert knowledge.posted == [] and worker.stats.skipped == 1
    assert worker.stats.last_skipped['offset'] == 5
    assert "'u'" in worker.stats.last_skipped['alasan']
    assert consumer.commits == 1                        # dilewati bukan gagal: offset tetap maju
