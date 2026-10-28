# ASCAM Ontop Agent

A sidecar that runs **on the Ontop host**. It is the only ASCAM component that touches the OBDA
artifact files, so the Knowledge Service, the Event Normalizer, and the Executor need no file
access to that host. It also drives the blue-green OBDA layer (ADR-0022). Decisions: ADR-0011,
ADR-0018, ADR-0022.

## Endpoints

| Endpoint | Does |
|---|---|
| `GET /health` | No token; agent status and presence of the artifacts |
| `GET /api/v1/artifacts` | ℳ and 𝒯 with SHA-256, size, and modification time |
| `GET /api/v1/artifacts/{kind}` | Content of an artifact (`r2rml`, `ontology`) |
| `PUT /api/v1/artifacts/{kind}` | Atomic write; the old content is backed up. `expected_sha256` refuses the write if the file changed meanwhile |
| `GET /api/v1/backups`, `POST /api/v1/backups/prune` | List and prune backups |
| `POST /api/v1/artifacts/restore` | Restore an artifact from a backup |
| `POST /api/v1/reload` | Restart Ontop and wait until the SPARQL endpoint answers (restart strategy) |
| `POST /api/v1/validate` | Run `ontop validate`; `db_url` can name a VDB version (ADR-0005) |
| `GET /api/v1/bluegreen` | Active and standby colours, the VDB version of each instance, and their direct SPARQL URLs |
| `POST /api/v1/bluegreen/prepare` | Write ℳ′ and 𝒯′ into the standby slot and pin it to a VDB version |
| `POST /api/v1/bluegreen/validate` | Validate the standby slot |
| `POST /api/v1/bluegreen/start` | Start the standby instance and wait until it answers |
| `POST /api/v1/bluegreen/switch` | Point the proxy at the standby (`nginx -s reload`), promote its artifacts to `config/`, stop the old instance |
| `POST /api/v1/bluegreen/discard` | Stop the standby instance without switching traffic |
| `POST /api/v1/bluegreen/reset` | Rebuild the active instance from the artifacts in `config/`, pinned to a base VDB version (used between evaluation runs; the endpoint may be interrupted) |

Backups are kept in `setup/vkg-system/config/ascam-backups/` (not committed). **The Ontop
properties file is never exposed**, because it holds the JDBC password.

## Running

```bash
setup/ascam/knowledge/scripts/init-secrets.sh          # creates the agent token if missing
docker-compose -f setup/ascam/ontop-agent/docker-compose.yaml up -d --build
curl -s http://127.0.0.1:18100/health
```

The agent mounts the Docker socket to start and stop Ontop containers and to run the Ontop CLI;
this gives it broad rights on the host, which is accepted for the research setting.

## Tests

```bash
docker run --rm -v "$PWD/setup/ascam/ontop-agent/service:/src:ro" python:3.12-slim sh -c \
  'cp -r /src /w && cd /w && pip install -q ".[test]" && python -m pytest -q'
```

The tests need neither Docker nor Ontop: the Docker wrapper is replaced by a fake.
