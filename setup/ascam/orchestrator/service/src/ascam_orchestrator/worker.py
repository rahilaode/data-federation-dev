"""
Orchestrator worker: consumes the DDL topics, normalises them, and sends them to Knowledge.

Offsets are committed only after the event was sent (at-least-once). A second delivery is safe
because `event_uid` is deterministic and Knowledge treats events idempotently (ADR-0016). If
Knowledge cannot be reached, the consumer seeks back to the offset of the failed message and
retries, so no event is skipped.
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
from .normalizer import normalize, skip_reason

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
    skipped: int = 0
    conflicts: int = 0
    last_skipped: dict | None = None
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

    # ── set-up ──────────────────────────────────────────────────────────────────
    def prepare(self) -> tuple[KnowledgeClient, int, dict[str, dict]]:
        knowledge = self._knowledge or KnowledgeClient(self.settings.knowledge_url,
                                                       self.settings.bearer())
        obdf_id = knowledge.obdf_id(self.settings.obdf_name)
        by_topic = {s['kafka_topic']: s for s in knowledge.sources(obdf_id) if s.get('kafka_topic')}
        if not by_topic:
            raise RuntimeError('no source with kafka_topic in Knowledge')
        self.stats.topics = sorted(by_topic)
        return knowledge, obdf_id, by_topic

    def _consumer(self, topics: list[str], bootstrap: str):
        if self._consumer_factory:
            return self._consumer_factory(topics, bootstrap)
        return KafkaConsumer(*topics, bootstrap_servers=bootstrap,
                             group_id=self.settings.group_id, enable_auto_commit=False,
                             auto_offset_reset=self.settings.offset_reset,
                             value_deserializer=lambda v: json.loads(v.decode()) if v else None)

    # ── processing ──────────────────────────────────────────────────────────────
    def handle(self, knowledge: KnowledgeClient, obdf_id: int, source: dict, message) -> None:
        events = normalize(message.topic, message.partition, message.offset, message.value,
                           source['logical_name'], source.get('dbms', 'postgresql'))
        self.stats.messages += 1
        if not events:
            # Messages that give no event are recorded with their reason (finding of F6: after
            # every container was restarted, messages were received but dropped without a trace)
            reason = skip_reason(message.value)
            self.stats.skipped += 1
            self.stats.last_skipped = {'topic': message.topic, 'offset': message.offset,
                                       'reason': reason,
                                       'excerpt': str(message.value)[:300]}
            log.warning('message %s@%s skipped: %s', message.topic, message.offset, reason)
            return
        for event in events:
            result = knowledge.post_event(obdf_id, event.payload())
            self.stats.events_sent += 1
            self.stats.last_event_at = datetime.now(timezone.utc).isoformat()
            if result.get('conflict'):
                # same uid but different content: the idempotency key collided
                self.stats.conflicts += 1
                log.warning('event_uid %s collides with another event with different content',
                            event.event_uid)
            if result.get('duplicate'):
                self.stats.duplicates += 1
            elif (result.get('event') or {}).get('status') == 'ignored':
                self.stats.events_ignored += 1
            else:
                self.stats.events_planned += 1
                plan = result.get('plan') or {}
                log.info('event %s -> plan %s (%s/%s)', event.operation, plan.get('id'),
                         plan.get('decision'), plan.get('status'))

    def run(self) -> None:
        """The set-up is retried until it succeeds.

        Knowledge may be restarting when the Orchestrator starts; a set-up tried only once
        killed the worker for good, even after Knowledge became healthy.
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
                log.warning('set-up failed (%s); retrying in %.0f seconds',
                            self.stats.last_error, self.settings.retry_seconds)
                if self._stop.wait(self.settings.retry_seconds):
                    self.stats.state = 'stopped'
                    return
        else:
            self.stats.state = 'stopped'
            return

        self.stats.state = 'running'
        self.stats.last_error = None
        log.info('listening on topics %s at %s (group %s, start %s)', self.stats.topics, bootstrap,
                 self.settings.group_id, self.settings.offset_reset)
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
                        except Exception as exc:        # noqa: BLE001 — retry without commit
                            self.stats.failures += 1
                            self.stats.last_error = f'{type(exc).__name__}: {exc}'[:300]
                            log.warning('processing offset %s failed: %s', message.offset,
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
