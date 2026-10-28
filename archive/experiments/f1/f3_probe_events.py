"""
F3-probe — Melihat bentuk pesan DDL yang benar-benar ada di Kafka.

Orchestrator harus menormalkan pesan monitor skema (PostgreSQL event trigger dan MySQL event
scheduler, dikirim Debezium) menjadi event terformalisasi. Bentuk pesannya perlu dilihat apa
adanya, bukan diasumsikan. Skrip ini hanya MEMBACA (consumer group sementara, tanpa commit).
"""
import json
import os
from datetime import datetime

from kafka import KafkaConsumer, TopicPartition

BOOTSTRAP = os.getenv('KAFKA_BOOTSTRAP', 'kafka:9092')
TOPICS = os.getenv('TOPICS', 'kemensos.schema_monitor.ddl_event_log,dukcapil.dukcapil.ddl_event_log').split(',')
MAX_MESSAGES = int(os.getenv('MAX_MESSAGES', '3'))
REPORT = {}


def dump(topic: str) -> None:
    consumer = KafkaConsumer(bootstrap_servers=BOOTSTRAP, enable_auto_commit=False,
                             consumer_timeout_ms=8000, value_deserializer=lambda v: v)
    partitions = consumer.partitions_for_topic(topic)
    if not partitions:
        print(f'   topik {topic}: tidak ditemukan')
        REPORT[topic] = {'exists': False}
        return
    assignment = [TopicPartition(topic, p) for p in partitions]
    consumer.assign(assignment)
    ends = consumer.end_offsets(assignment)
    total = sum(ends.values())
    for tp in assignment:                                  # ambil beberapa pesan terakhir
        consumer.seek(tp, max(0, ends[tp] - MAX_MESSAGES))
    print(f'   topik {topic}: {total} pesan')
    messages = []
    for message in consumer:
        try:
            value = json.loads(message.value.decode())
        except Exception:                                  # noqa: BLE001
            value = {'raw': message.value[:200].decode('utf-8', 'replace')}
        payload = value.get('payload', value) if isinstance(value, dict) else value
        messages.append(payload)
        print(f"     offset {message.offset} | kunci struktur: {sorted(payload) if isinstance(payload, dict) else type(payload)}")
        print(f'     isi: {json.dumps(payload, default=str)[:600]}')
        if len(messages) >= MAX_MESSAGES:
            break
    consumer.close()
    REPORT[topic] = {'exists': True, 'total': total, 'messages': messages}


if __name__ == '__main__':
    print(f'== pesan DDL di Kafka ({BOOTSTRAP})')
    try:
        for topic in TOPICS:
            dump(topic.strip())
    finally:
        os.makedirs('/out', exist_ok=True)
        path = f"/out/f3_events_{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
        with open(path, 'w') as fh:
            json.dump(REPORT, fh, indent=2, default=str)
        print(f'\nlaporan: results/f1/{os.path.basename(path)}')
