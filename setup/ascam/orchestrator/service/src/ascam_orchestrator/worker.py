"""
Pekerja Orchestrator: konsumsi topik DDL, normalisasi, kirim ke Knowledge.

Offset hanya di-commit setelah event berhasil dikirim (at-least-once). Pengiriman ganda aman
karena `event_uid` bersifat deterministik dan Knowledge memperlakukan event secara idempoten
(ADR-0016). Bila Knowledge tidak dapat dihubungi, konsumen mundur ke offset pesan yang gagal
dan mencoba lagi, sehingga tidak ada event yang terlewat.
"""
import json
import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from kafka import KafkaConsumer, TopicPartition

from .config import Settings
from .knowledge_client import KnowledgeClient
from .normalizer import normalize

log = logging.getLogger('ascam.orchestrator')


@dataclass
class Stats:
    state: str = 'starting'
    topics: list[str] = field(default_factory=list)
    messages: int = 0
    events_sent: int = 0
    events_planned: int = 0
    events_ignored: int = 0
    duplicates: int = 0
    failures: int = 0
    last_error: str | None = None
    last_event_at: str | None = None

    def snapshot(self) -> dict:
        return asdict(self)


class Orchestrator:
    def __init__(self, settings: Settings, knowledge: KnowledgeClient | None = None,
                 consumer_factory=None):
        self.settings = settings
        self.stats = Stats()
        self._knowledge = knowledge
        self._consumer_factory = consumer_factory
        self._stop = threading.Event()

    # ── penyiapan ───────────────────────────────────────────────────────────────
    def prepare(self) -> tuple[KnowledgeClient, int, dict[str, dict]]:
        knowledge = self._knowledge or KnowledgeClient(self.settings.knowledge_url,
                                                       self.settings.bearer())
        obdf_id = knowledge.obdf_id(self.settings.obdf_name)
        by_topic = {s['kafka_topic']: s for s in knowledge.sources(obdf_id) if s.get('kafka_topic')}
        if not by_topic:
            raise RuntimeError('tidak ada sumber dengan kafka_topic pada Knowledge')
        self.stats.topics = sorted(by_topic)
        return knowledge, obdf_id, by_topic

    def _consumer(self, topics: list[str], bootstrap: str):
        if self._consumer_factory:
            return self._consumer_factory(topics, bootstrap)
        return KafkaConsumer(*topics, bootstrap_servers=bootstrap,
                             group_id=self.settings.group_id, enable_auto_commit=False,
                             auto_offset_reset='earliest',
                             value_deserializer=lambda v: json.loads(v.decode()) if v else None)

    # ── pemrosesan ──────────────────────────────────────────────────────────────
    def handle(self, knowledge: KnowledgeClient, obdf_id: int, source: dict, message) -> None:
        events = normalize(message.topic, message.partition, message.offset, message.value,
                           source['logical_name'], source.get('dbms', 'postgresql'))
        self.stats.messages += 1
        for event in events:
            result = knowledge.post_event(obdf_id, event.payload())
            self.stats.events_sent += 1
            self.stats.last_event_at = datetime.now(timezone.utc).isoformat()
            if result.get('duplicate'):
                self.stats.duplicates += 1
            elif (result.get('event') or {}).get('status') == 'ignored':
                self.stats.events_ignored += 1
            else:
                self.stats.events_planned += 1
                plan = result.get('plan') or {}
                log.info('event %s -> rencana %s (%s/%s)', event.operation, plan.get('id'),
                         plan.get('decision'), plan.get('status'))

    def run(self) -> None:
        """Penyiapan diulang sampai berhasil.

        Knowledge dapat sedang restart saat Orchestrator start; penyiapan yang hanya dicoba
        sekali membuat pekerja mati permanen meski Knowledge kemudian sehat.
        """
        while not self._stop.is_set():
            try:
                knowledge, obdf_id, by_topic = self.prepare()
                bootstrap = self.settings.bootstrap or knowledge.kafka_bootstrap(obdf_id)
                consumer = self._consumer(sorted(by_topic), bootstrap)
                break
            except Exception as exc:                # noqa: BLE001
                self.stats.state = 'retrying'
                self.stats.last_error = f'{type(exc).__name__}: {exc}'[:300]
                log.warning('penyiapan gagal (%s); mencoba lagi dalam %.0f detik',
                            self.stats.last_error, self.settings.retry_seconds)
                if self._stop.wait(self.settings.retry_seconds):
                    self.stats.state = 'stopped'
                    return
        else:
            self.stats.state = 'stopped'
            return

        self.stats.state = 'running'
        self.stats.last_error = None
        log.info('mendengarkan topik %s pada %s', self.stats.topics, bootstrap)
        try:
            while not self._stop.is_set():
                batches = consumer.poll(timeout_ms=self.settings.poll_timeout_ms)
                for partition, messages in (batches or {}).items():
                    for message in messages:
                        source = by_topic.get(message.topic)
                        if source is None:
                            continue
                        try:
                            self.handle(knowledge, obdf_id, source, message)
                        except Exception as exc:        # noqa: BLE001 — coba lagi tanpa commit
                            self.stats.failures += 1
                            self.stats.last_error = f'{type(exc).__name__}: {exc}'[:300]
                            log.warning('gagal memproses offset %s: %s', message.offset,
                                        self.stats.last_error)
                            consumer.seek(TopicPartition(message.topic, message.partition),
                                          message.offset)
                            time.sleep(self.settings.retry_seconds)
                            break
                        consumer.commit()
        finally:
            self.stats.state = 'stopped'
            consumer.close()

    def stop(self) -> None:
        self._stop.set()
