# ASCAM console

A web interface to watch the adaptation loop and to **decide on proposals** that are not run
automatically: adding a column (ADR-0021, ADR-0024).

| Page | Shows |
|---|---|
| Overview | State of the MAPE-K loop, component health, latest changes and executions |
| Approvals | Proposals waiting for a decision: what changed at the source, what will be done to the VDB, the mapping, and the ontology, and why; the property name and domain can be revised |
| Notifications | Changes ASCAM did not adapt, with the affected elements and a link to their lineage |
| Schema changes | Every DDL captured by the monitors, with its analysis |
| Plans and executions | Plans, decisions, and execution steps with their durations |
| Specification versions | Version history and a diff of the artifacts between versions |
| Column lineage | Source column → foreign table → view → TriplesMap → property, with every path through a node highlighted |
| Impact analysis | A what-if analysis of a schema change; nothing is changed |
| Configuration | Sources, OBDF components (with connection checks), and policies |
| Audit trail | Every action with its actor |

## Running

```bash
setup/ascam/knowledge/scripts/init-secrets.sh      # creates the console password and session key
docker-compose -f setup/ascam/ui/docker-compose.yaml up -d --build
```

Open <http://127.0.0.1:18400> and log in as `admin` with the password in
`setup/ascam/knowledge/secrets/ui_admin_password`.

## Security

- The browser never receives a service token. The console forwards requests to Knowledge
  through an allow list (reads, plan decisions, notifications, connection checks, sync, impact
  analysis, adaptation policy, lineage).
- The session is a signed cookie with `HttpOnly` and `SameSite=Strict`.
- Requests that change state must carry the header `X-ASCAM-UI`.
- The administrator's name is passed to Knowledge, so the audit trail records who approved or
  rejected.

## Tests

```bash
docker run --rm -v "$PWD/setup/ascam/ui/service:/src:ro" python:3.12-slim sh -c \
  'cp -r /src /w && cd /w && pip install -q ".[test]" && python -m pytest -q'
```
