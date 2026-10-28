"""
Audit of the Kafka messages for the C_safe criterion (thesis proposal §3.11.1, eqs. 3.13–3.15).

Runs INSIDE a container attached to the ascam-networks network (see evaluate.py):
  python kafka_audit.py offset            -> end offset of every partition, used as the start point
  python kafka_audit.py audit start.json  -> classification of every message since the start point

Classes of message:
  log            a ddl_event_log row (N_log, positive control)
  production     a row of another source table, e.g. penerima_manfaat (N_prod, must be 0)
  schema_change  a Debezium schema event (DDL text, no data row)
  tombstone      an empty delete marker
  other          a shape that is not recognised (reported as it is)

Principle: the result file holds NO production row values. For a production message only the
topic, offset, table, operation, and column NAMES are recorded; for a log message only the
names of the log columns.
"""
import json
import sys

from kafka import KafkaConsumer, TopicPartition

BOOTSTRAP = 'kafka:9092'
PREFIXES = ('kemensos', 'dukcapil', 'schema-monitor-')


def relevant_topics(consumer) -> list[str]:
    """Topics of the source connectors; internal Kafka and Kafka Connect topics are ignored."""
    return sorted(t for t in consumer.topics()
                  if not t.startswith('_') and 'connect' not in t and t.startswith(PREFIXES))


def end_offsets() -> dict:
    consumer = KafkaConsumer(bootstrap_servers=BOOTSTRAP, enable_auto_commit=False)
    result = {}
    for topic in relevant_topics(consumer):
        partitions = [TopicPartition(topic, p) for p in consumer.partitions_for_topic(topic) or []]
        result[topic] = {str(tp.partition): off for tp, off in consumer.end_offsets(partitions).items()}
    consumer.close()
    return result


def classify(value) -> tuple[str, dict]:
    if value is None:
        return 'tombstone', {}
    try:
        body = json.loads(value)
    except ValueError:
        return 'other', {'reason': 'not JSON'}
    payload = body.get('payload') if isinstance(body, dict) and isinstance(body.get('payload'), dict) else body
    if not isinstance(payload, dict):
        return 'other', {'reason': f'type {type(payload).__name__}'}
    source = payload.get('source') or {}
    table = source.get('table')
    if 'ddl' in payload or 'tableChanges' in payload or 'historyRecord' in payload:
        return 'schema_change', {'db': source.get('db'), 'table': table}
    row = payload.get('after') or payload.get('before')
    if table == 'ddl_event_log':
        return 'log', {'columns': sorted(row) if isinstance(row, dict) else []}
    if payload.get('op') in ('c', 'u', 'd', 'r') and isinstance(row, dict):
        return 'production', {'db': source.get('db'), 'schema': source.get('schema'), 'table': table,
                              'op': payload.get('op'), 'columns': sorted(row)}
    return 'other', {'keys': sorted(payload)[:10]}


def audit(start: dict) -> dict:
    consumer = KafkaConsumer(bootstrap_servers=BOOTSTRAP, enable_auto_commit=False,
                             consumer_timeout_ms=5000, value_deserializer=lambda v: v)
    counts = {'log': 0, 'production': 0, 'schema_change': 0, 'tombstone': 0, 'other': 0}
    per_topic, production_samples, log_columns = {}, [], set()
    for topic in relevant_topics(consumer):
        partitions = [TopicPartition(topic, p) for p in consumer.partitions_for_topic(topic) or []]
        end = consumer.end_offsets(partitions)
        for tp in partitions:
            first = int((start.get(topic) or {}).get(str(tp.partition), 0))
            if end[tp] <= first:
                continue
            consumer.assign([tp])
            consumer.seek(tp, first)
            while True:
                batch = consumer.poll(timeout_ms=3000)
                messages = [m for records in batch.values() for m in records]
                if not messages:
                    break
                done = False
                for message in messages:
                    if message.offset >= end[tp]:
                        done = True
                        break
                    kind, detail = classify(message.value)
                    counts[kind] += 1
                    per_topic.setdefault(topic, {}).setdefault(kind, 0)
                    per_topic[topic][kind] += 1
                    if kind == 'log':
                        log_columns.update(detail.get('columns', []))
                    if kind == 'production' and len(production_samples) < 20:
                        production_samples.append({'topic': topic, 'offset': message.offset, **detail})
                if done or messages[-1].offset >= end[tp] - 1:
                    break
    consumer.close()
    return {'N_log': counts['log'], 'N_prod': counts['production'], 'counts': counts,
            'per_topic': per_topic, 'log_message_columns': sorted(log_columns),
            'production_message_samples': production_samples,
            'C_safe': counts['production'] == 0 and counts['log'] > 0}


if __name__ == '__main__':
    if sys.argv[1] == 'offset':
        print(json.dumps(end_offsets()))
    elif sys.argv[1] == 'audit':
        with open(sys.argv[2]) as file:
            print(json.dumps(audit(json.load(file))))
