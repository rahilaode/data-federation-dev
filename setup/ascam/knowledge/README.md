# ASCAM Knowledge

The Knowledge component: the Knowledge Base (PostgreSQL) and the Knowledge Service (FastAPI)
that is its only client. It stores the runtime model of the OBDF, runs the sync, the impact
analysis, and the decision matrix, and keeps the trail of events, plans, and executions.
Schema and diagrams: [`docs/knowledge-base.md`](../../../docs/knowledge-base.md). Decisions:
ADR-0007, ADR-0008, ADR-0012 to ADR-0016, ADR-0023, ADR-0024
([`docs/design-decisions.md`](../../../docs/design-decisions.md)).

```
knowledge/
├── docker-compose.yaml       knowledge-db, knowledge-migrate (runs once), knowledge-service
├── config/obdf-bansos.yaml   declarative configuration of the case-study OBDF, applied at start-up
├── db-init/                  creates the application role ascam_app on first initialisation
├── scripts/init-secrets.sh   writes the secret files in secrets/ (not committed)
└── service/
    ├── src/ascam_knowledge/  models, API, security, connection checks, sync, impact analysis, CLI
    ├── migrations/versions/  0001 initial schema and integrity rules, 0002 credential name,
    │                         0003 structured connection check, 0004 version content digest,
    │                         0005 default schema of a source, 0006 start_ontop step,
    │                         0007 notify decision and revised ADD plans
    ├── tests/                migrations, integrity, API, sync, lineage, impact, events
    └── scripts/test.sh       runs the tests against a temporary PostgreSQL
```

## Running

```bash
setup/ascam/knowledge/scripts/init-secrets.sh        # safe to run again; never overwrites
docker-compose -f setup/ascam/knowledge/docker-compose.yaml up -d --build
```

The service listens on `http://127.0.0.1:18000`; interactive API documentation is at `/docs`.
Client tokens are in `secrets/knowledge_api_tokens` (one `<client>:<token>` per line; clients
`ui`, `orchestrator`, `executor`).

```bash
TOKEN=$(grep '^ui:' setup/ascam/knowledge/secrets/knowledge_api_tokens | cut -d: -f2)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/api/v1/obdf
```

## API overview

All paths are under `/api/v1` and need a bearer token, except `/health` and `/ready`.

| Area | Endpoints |
|---|---|
| Registry | `GET/POST /obdf`, `GET /obdf/{id}`, `GET/POST /obdf/{id}/sources`, `/credentials`, `/targets`, `PATCH /targets/{id}`, `PUT /credentials/{id}/secret`, `GET/PUT /obdf/{id}/settings[/{key}]`, `/naming-policy`, `/type-mappings` |
| Configuration | `POST /config/apply` |
| Connection checks | `POST /obdf/{id}/checks`, `POST /targets/{id}/check`, `GET /targets/{id}/checks`, `GET /obdf/{id}/status` |
| Sync and versions | `POST /obdf/{id}/sync`, `GET /obdf/{id}/sync-runs`, `GET /obdf/{id}/versions`, `GET /versions/{id}`, `GET /versions/{id}/artifacts/{kind}` |
| Analysis | `POST /obdf/{id}/impact` (read-only), `GET /obdf/{id}/lineage` |
| Events and plans | `POST/GET /obdf/{id}/events`, `GET /obdf/{id}/plans`, `GET /plans/{id}`, `POST /plans/{id}/approve`, `/reject`, `/supersede` |
| Executions | `POST /plans/{id}/executions`, `POST /executions/{id}/steps`, `/validations`, `/finish`, `GET /obdf/{id}/executions` |
| Notifications and audit | `GET /obdf/{id}/notifications`, `POST /notifications/{id}/ack`, `GET /obdf/{id}/audit` |

### Connection checks

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/api/v1/obdf/1/checks
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/api/v1/obdf/1/status
```

Results are stored in `registry.connection_check` without secrets and feed the console dashboard.

### Sync

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/api/v1/obdf/1/sync
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/api/v1/obdf/1/versions
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/api/v1/versions/1/artifacts/r2rml
```

A sync reads Σ_S from Teiid (ODBC and the management API) and ℳ and 𝒯 from the Ontop agent, and
stores them as one specification version when the content changed (ADR-0012).

### Impact analysis

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"operation":"drop","source":"kemensos","table":"penerima_manfaat","column":"status_ekonomi"}' \
  http://127.0.0.1:18000/api/v1/obdf/1/impact
```

Nothing is changed. The answer lists the affected Teiid columns and their usages, the decision
(`auto`, `hitl`, `notify`, or `ignored`) with its reasons, the affected elements per layer, and the
planned actions per artifact.

## Declarative configuration

A `config/*.yaml` document (version 1) holds the OBDF, its sources, credentials (only as
`secret_file`), targets, settings, naming policy, and type mappings. Applying it is idempotent,
non-destructive, and atomic. Besides start-up (`ASCAM_KNOWLEDGE_BOOTSTRAP_CONFIG`), it can be
applied with `POST /api/v1/config/apply` or `ascam-knowledge apply-config <file>`.

Placeholders of `property_iri_template`: `{namespace}`, `{column}`, `{column_camel}`.

## Tests

```bash
setup/ascam/knowledge/service/scripts/test.sh          # needs Docker; 158 tests
```

## Adding a table to the `spec` schema

A new table must have `spec_version_id`, use composite foreign keys for references to other
`spec` tables, and get the `trg_guard_sealed` trigger in its migration. The tests
`test_every_spec_table_is_versioned` and `test_integrity_triggers_installed` enforce this.
