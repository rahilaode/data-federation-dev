# ASCAM Event Normalizer (`orchestrator`)

Connects the schema monitors (through Kafka) with Knowledge. It consumes `ddl_event_log`
messages, turns each column statement into a structured event, and sends it to Knowledge, which
analyses the impact and builds the adaptation plan (ADR-0016, ADR-0017). It makes no decision
itself. In the thesis and the paper the component is called the Event Normalizer; the code, the
container (`ascam-orchestrator`), and the Kafka consumer group keep the name `orchestrator`.

The runtime configuration (sources, their topics, the Kafka broker) is read from Knowledge; only
the Knowledge URL, the token file, and the OBDF name are set here.

| Variable | Default | Meaning |
|---|---|---|
| `ASCAM_ORCH_KNOWLEDGE_URL` | `http://ascam-knowledge-service:8000` | Knowledge Service |
| `ASCAM_ORCH_TOKEN_FILE`, `ASCAM_ORCH_TOKEN_CLIENT` | –, `orchestrator` | Token file and client name |
| `ASCAM_ORCH_OBDF` | `bansos` | Name of the OBDF |
| `ASCAM_ORCH_GROUP_ID` | `ascam-orchestrator` | Kafka consumer group; the start scripts use a new group each time |
| `ASCAM_ORCH_OFFSET_RESET` | `earliest` | Where a new group starts; the start scripts use `latest` so that old DDL is not replayed |

## Running

```bash
docker-compose -f setup/ascam/orchestrator/docker-compose.yaml up -d --build
curl -s http://127.0.0.1:18200/health
```

`/health` shows the worker state, the topics, and counters for messages, events sent, plans,
ignored events, duplicates, conflicts, skipped messages (with the last reason), and failures.

## Tests

```bash
docker run --rm -v "$PWD/setup/ascam/orchestrator/service:/src:ro" python:3.12-slim sh -c \
  'cp -r /src /w && cd /w && pip install -q ".[test]" && python -m pytest -q'
```

The tests need neither Kafka nor Knowledge: the consumer and the Knowledge client are fakes.
