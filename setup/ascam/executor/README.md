# ASCAM Executor

Applies adaptation plans with status `approved` to the OBDF. Decisions and plans are made by
Knowledge (ADR-0015, ADR-0016, ADR-0024); the Executor only carries them out. It polls Knowledge
every 5 s, refuses a plan whose base version is no longer active, and reports every step with its
duration.

## Artifact editors

Pure functions, text in and text out (ADR-0019):

| Module | Does |
|---|---|
| `artifacts/vdb.py` | Appends `ALTER FOREIGN TABLE` and `ALTER VIEW` statements to the model metadata and raises the VDB version |
| `artifacts/ontology.py` | Adds datatype properties and deprecates properties, append-only, in a managed block |
| `artifacts/r2rml.py` | Adds and removes predicate-object maps; rewrites the projection of a logical-table query |

## Execution steps

Blue-green strategy (default, ADR-0022):

| Step | Does |
|---|---|
| `deploy_vdb` | Deploys Σ′_S as a **new VDB version** next to the version that serves |
| `validate` | Writes ℳ′ and 𝒯′ into the standby slot through the agent and runs `ontop validate` against the new VDB version |
| `start_ontop` | Starts the standby Ontop instance, pinned to the new VDB version |
| `verify` | Checks the answers of the standby instance with graph fingerprints, according to the pattern |
| `switch` | Switches the proxy to the standby instance, promotes the artifacts to `config/`, stops the old instance, and moves the Teiid connection type (`ANY` to the new version, `NONE` to the old) |
| `sync` | Syncs Knowledge, so the new specification version is read from the running OBDF |

Any failure before `switch` discards the standby instance and the new VDB version; when it is the
verification that fails, this is recorded as a `rollback` step. Users see no change. With `ASCAM_EXEC_OBDA_STRATEGY=restart` the older order of ADR-0020 is used: `deploy_vdb`,
`validate`, `switch` (connection type), `reload_ontop` (single instance restarted in place),
`verify`, and `rollback` when verification fails.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `ASCAM_EXEC_KNOWLEDGE_URL` | `http://ascam-knowledge-service:8000` | Knowledge Service |
| `ASCAM_EXEC_TOKEN_FILE`, `ASCAM_EXEC_TOKEN_CLIENT` | –, `executor` | Token file and client name for Knowledge |
| `ASCAM_EXEC_OBDF` | `bansos` | Name of the OBDF |
| `ASCAM_EXEC_TEIID_PASSWORD_FILE` | – | Teiid ManagementRealm password |
| `ASCAM_EXEC_AGENT_TOKEN_FILE` | – | Token for the Ontop agent |
| `ASCAM_EXEC_TEIID_JDBC_HOST`, `ASCAM_EXEC_TEIID_JDBC_PORT` | `teiid`, `31000` | Teiid address used in the validation JDBC URL |
| `ASCAM_EXEC_POLL_SECONDS` | `5` | Polling interval |
| `ASCAM_EXEC_ENABLED` | `true` | `false` starts the service without executing plans |
| `ASCAM_EXEC_OBDA_STRATEGY` | `bluegreen` | `bluegreen` or `restart` |
| `ASCAM_EXEC_VIEW_STATEMENT` | `alter` | `alter` (`ALTER VIEW`) or `recreate` (`DROP VIEW` + `CREATE VIEW`), ADR-0023 |

Addresses of the OBDF components come from Knowledge; passwords and tokens come from the
Executor's own Docker secrets, because Knowledge never returns secrets.

## Running

```bash
docker-compose -f setup/ascam/executor/docker-compose.yaml up -d --build
curl -s http://127.0.0.1:18300/health
curl -s -X POST http://127.0.0.1:18300/control/pause     # stop picking up plans
curl -s -X POST http://127.0.0.1:18300/control/resume
```

Pausing does not interrupt an execution that is already running.

## Tests

```bash
docker run --rm -v "$PWD/setup/ascam/executor/service:/src:ro" python:3.12-slim sh -c \
  'cp -r /src /w && cd /w && pip install -q ".[test]" && python -m pytest -q'
```
