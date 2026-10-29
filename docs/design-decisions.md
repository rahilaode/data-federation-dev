# Design decisions

This file condenses the 24 architecture decision records (ADRs) written while ASCAM was built.
Each entry keeps its original number, because the code, the commit history, and the thesis
refer to decisions by that number (for example "ADR-0022"). The full records, with every
measurement table, are in the git history under `docs/adr/` (last present in commit `a1e9b0a`,
branch `eval/final`).

Feasibility tests are labelled F0.x to F6. Their scripts are kept, unchanged, in
`archive/experiments/`; raw results were stored locally and never committed.

Some later decisions revise earlier ones. Where that happens the entry says so, and the
[summary table](#summary) shows which rule is in force.

## Summary

| ADR | Decision | MAPE-K phase | Test | Still in force |
|---|---|---|---|---|
| [0001](#adr-0001-reloading-ontop-after-an-adaptation) | Restart Ontop with `init` and an entrypoint that `exec`s Java | Execute | F0.1 | As the `restart` strategy; the default is now ADR-0022 |
| [0002](#adr-0002-changing-the-federated-schema-by-appending-alter-statements) | Change Σ_S only by appending `ALTER` statements; RENAME through `NAMEINSOURCE` | Plan, Execute | F0.2 | Yes |
| [0003](#adr-0003-reading-the-federated-schema-from-teiid-system-tables-over-odbc) | Read Σ_S from Teiid system tables over ODBC | Knowledge, Execute | F0.3 | Yes |
| [0004](#adr-0004-teiid-effector-management-api-and-blue-green-vdb-versions) | Deploy Σ′_S as a new VDB version through the management API | Execute | F0.4, F0.4b | Yes |
| [0005](#adr-0005-r2rml-mapping-and-validation-before-activation) | ℳ is R2RML; validate ℳ′ and 𝒯′ before activation | Plan, Execute | F0.5 | Yes |
| [0006](#adr-0006-teiid-view-dependencies) | View dependencies from `SYSADMIN.Usage` and `SYSADMIN.Views` | Knowledge, Analyze | F0.7 | Yes |
| [0007](#adr-0007-knowledge-schema-and-integrity-in-the-database) | Knowledge schema with integrity enforced by the database | Knowledge | F1.2 | Yes |
| [0008](#adr-0008-knowledge-service-api) | Knowledge Service API, tokens, encrypted credentials, declarative configuration | Knowledge | F1.3 | Yes |
| [0009](#adr-0009-where-the-vdb-file-and-the-federated-schema-come-from) | Where the VDB artifact and Σ_S are fetched from | Knowledge, Execute | F1.4a, F1.4b | Yes |
| [0010](#adr-0010-r2rml-at-run-time-and-retirement-of-iteration-1) | Ontop loads R2RML; the iteration-1 engine is retired | – | F0.5 | Yes |
| [0011](#adr-0011-ontop-agent) | An agent next to Ontop is the only path to the OBDA artifacts | Knowledge, Execute | F1.4d | Yes |
| [0012](#adr-0012-sync-and-specification-versions) | One sync makes at most one specification version | Knowledge | F1.5a | Yes |
| [0013](#adr-0013-parsing-the-mapping-and-the-ontology) | Parse ℳ and 𝒯 into tables | Knowledge, Analyze | F1.5b | Yes |
| [0014](#adr-0014-column-lineage) | Materialised column lineage | Knowledge, Analyze, Plan | F1.5c | Yes |
| [0015](#adr-0015-impact-analysis-and-decision-matrix-d11) | Impact analysis and decision matrix D11 live in Knowledge | Analyze, Plan | F1.6 | Yes, with the ADD and HITL rules of ADR-0021 and ADR-0024 |
| [0016](#adr-0016-schema-events-and-the-plan-life-cycle) | Schema events and the plan life cycle | Monitor, Analyze, Plan | F3a | Yes |
| [0017](#adr-0017-the-event-normalizer-orchestrator) | The Event Normalizer only normalises and forwards | Monitor | F3b | Yes |
| [0018](#adr-0018-writing-the-obda-artifacts) | Atomic artifact writes, backups, and Ontop reload by the agent | Execute | F4a | Yes |
| [0019](#adr-0019-how-the-vdb-the-ontology-and-the-mapping-are-edited) | How the VDB, the ontology, and the mapping are edited | Execute | F4b | Yes |
| [0020](#adr-0020-execution-engine) | Execution engine with a safe point before the switch | Execute | F4c | Yes; order of steps revised by ADR-0022 |
| [0021](#adr-0021-add-needs-the-administrators-approval) | ADD needs the administrator's approval in the console | Plan | F5 | Yes, made unconditional by ADR-0024 |
| [0022](#adr-0022-blue-green-obda-layer) | Two Ontop instances behind a proxy (blue-green) | Execute | F6 | Yes (default strategy) |
| [0023](#adr-0023-adjusting-teiid-views-when-a-column-is-dropped) | Adjust Teiid views when a source column is dropped | Analyze, Plan, Execute | F0.8 | Yes |
| [0024](#adr-0024-approval-only-for-add-notification-for-unsafe-dropping-and-renaming) | Approval only for ADD; notification for unsafe DROP/RENAME; lineage in the console | Plan, Knowledge | F6 | Yes |

---

## ADR-0001: Reloading Ontop after an adaptation

*2026-09-17. Execute.*

**Context.** Ontop reads the ontology, the mapping, and the database metadata only when it
starts, and version 4.1.1 has no production hot reload. In the first experiments the restart
took about 19 s of a Δt_adapt of 22–28 s. Log timestamps showed that 11.3–11.8 s of it was the
old container refusing to stop: `wait-for-it.sh` execs the Ontop entrypoint, bash becomes PID 1,
and the entrypoint starts `java` without `exec`, so SIGTERM never reaches the JVM and Docker
waits 10 s before SIGKILL.

**Options.** (R1) restart the container, in four configurations; (R2) restart inside the JVM
through `POST /ontop/restart`, which only exists in the unauthenticated development mode;
(R3) blue-green behind a proxy, postponed at the time.

**Decision.** R1 with `init: true` on the Ontop service and a copy of the 4.1.1 entrypoint that
runs `exec java` (`setup/vkg-system/entrypoint/`). With n = 3, stop time fell from 10.6–11.0 s
to 0.8–0.9 s, the full restart from 20.0–23.8 s to 9.7–10.4 s, and Spring shut down cleanly in
3 of 3 runs instead of 0 of 3.

**Consequences.** The repository keeps a one-line patch of an upstream file, tied to 4.1.1
(Ontop 5.0.0 already uses `exec java`). The endpoint is still down for 9–10 s on every
restart; ADR-0022 removes that.

## ADR-0002: Changing the federated schema by appending ALTER statements

*2026-09-17. Plan and Execute.*

**Context.** Σ_S is the Teiid DDL (`CREATE FOREIGN TABLE ... OPTIONS`) inside the VDB XML. The
first implementation edited it with regular expressions, and general SQL parsers such as
sqlglot do not understand that dialect. The Teiid Reference Guide documents `ALTER TABLE` with
`ADD COLUMN`, `DROP COLUMN`, `RENAME COLUMN`, and column `OPTIONS`.

**Test F0.2.** On a separate test VDB, `ALTER ... ALTER COLUMN tgl OPTIONS (SET NAMEINSOURCE
'tgl_baru')`, `RENAME COLUMN`, `ADD COLUMN`, and `DROP COLUMN` appended after the original
`CREATE` all gave an `ACTIVE` VDB, on both MySQL and PostgreSQL. With `NAMEINSOURCE` the Teiid
column keeps its name while the source column changes. A dropped column is rejected by Teiid
(`TEIID31118`) before the query reaches the source.

**Decision.** ASCAM never parses or rewrites existing Teiid DDL. Every adaptation appends
`ALTER FOREIGN TABLE` statements, generated from the structured event, to the metadata of the
model, through an XML parser that keeps CDATA. ADD becomes `ADD COLUMN`, DROP becomes
`DROP COLUMN`, and RENAME becomes `ALTER COLUMN ... OPTIONS (SET NAMEINSOURCE '<new name>')`
(decision D8), so mappings, joins, IRI templates, and views that use the Teiid name stay valid.
Knowledge reads the effective Σ_S from Teiid system tables, not from the file text.

**Consequences.** Changes are small and auditable. Knowledge has to store the Teiid name and
the source name separately. D8 can cause a name clash (a source renames `a → b`, then adds a new
`a`); Plan then has to choose another Teiid name. After a DROP, `Position` values are no longer
contiguous. The VDB file grows with every adaptation; compaction is future work.

## ADR-0003: Reading the federated schema from Teiid system tables over ODBC

*2026-09-17. Knowledge and Execute.*

**Context.** Knowledge is a runtime model of the OBDF and must read Σ_S from a Teiid that may run
on another host, without parsing the VDB file. Teiid 16 offers an ODBC transport that speaks the
PostgreSQL protocol (port 35432) and the `SYS.*` system tables.

**Test F0.3.** Both psycopg2 and psycopg 3 read the whole Σ_S of the `government` VDB (2 physical
models, 7 tables, all columns and keys) in about 36 ms. Two findings: the runtime column names
of `SYS.Columns` differ from the Reference Guide (`Length` and `IsLengthFixed` instead of
`ElementLength` and `sLengthFixed`), and `PRECISION` is a reserved word in the Teiid grammar.

**Decision.** Read `SYS.VirtualDatabases`, `SYS.Schemas`, `SYS.Tables`, `SYS.Columns`, and
`SYS.KeyColumns` over ODBC with psycopg 3. Ask for the column list of a system table first and
request only columns that exist. Quote every identifier in generated SQL and DDL. Judge VDB
activation by the Teiid VDB status, not by WildFly deployment markers.

**Consequences.** A sync takes tens of milliseconds and needs no file access on the Teiid host.
ASCAM needs a Teiid account that may read metadata. The ODBC transport is not encrypted, which
is accepted for the research environment.

## ADR-0004: Teiid effector: management API and blue-green VDB versions

*2026-09-17. Execute.*

**Context.** The first implementation wrote the VDB into a shared deployment folder and relied
on the 5-second scanner; it needed a shared file system, its timing varied by 1.5–5 s, and a
broken VDB stopped the OBDF until it was reverted.

**Tests F0.4 and F0.4b.** Through the WildFly HTTP management API (Digest authentication) a VDB
was deployed in about 0.1 s and was `ACTIVE` some milliseconds later. A VDB with invalid DDL
gives a WildFly `success` while its Teiid status is `FAILED`, so the WildFly outcome alone says
nothing. With two versions of a VDB, a connection without a version number goes to the version
with connection type `ANY` (to the highest one if several are `ANY`, which is observed, not
documented); `NONE` refuses new connections while existing ones keep running.

**Decision.** Σ′_S is deployed as VDB version N+1 next to version N. If it is `FAILED`, the
`ERROR` entries of `validity-errors` are recorded, N+1 is removed, and version N is untouched.
If it is `ACTIVE`, Σ′_S is checked over ODBC and ℳ′ and 𝒯′ are validated against N+1; then N+1
gets `ANY`, Ontop is reloaded, and the SPARQL answers are verified. On failure N+1 gets `NONE`
and N gets `ANY` again. ASCAM only removes deployments it created itself.

**Consequences.** The OBDF keeps serving while Σ′_S is prepared, and a rollback takes about
0.02 s. Ontop must connect without a VDB version number. ASCAM needs a ManagementRealm account.

## ADR-0005: R2RML mapping and validation before activation

*2026-09-17. Plan, Execute, Knowledge.*

**Context.** The research scope fixes ℳ as R2RML in Turtle, while Ontop still loaded a native
`.obda` mapping. ADR-0004 also needed a way to validate ℳ′ and 𝒯′ against the new VDB version.

**Test F0.5.** `ontop validate` accepts `mapping.ttl` and catches a mapping column that does not
exist in the source query, but it does not catch a predicate that the ontology does not declare.
With `--db-url ...;version=9` it fails with `TEIID40046`, so validation against a given VDB
version works. Answers of `.obda`, the hand-written `mapping.ttl`, and the converted mapping
were identical (36 predicates, 7 classes, the scenario queries).

**Decision.** ℳ is the hand-written `mapping.ttl`. Before activation, `ontop validate` runs
against VDB version N+1, and an ASCAM validator checks that every predicate in ℳ′ is declared in
𝒯′ with the right kind and is not deprecated. Failure of either stops the execution before the
connection type changes. Error causes are read from the whole `Caused by` chain.

**Consequences.** A validation takes about 5 s (mostly JVM start-up), before the switch. The
equivalence of the mapping migration holds for the current data, not formally for every
database instance.

## ADR-0006: Teiid view dependencies

*2026-09-17. Knowledge and Analyze.*

**Context.** Mappings can read Teiid views, and views can use foreign-table columns directly or
through other views. Without column-level dependencies ASCAM cannot see the impact of a source
change on views and mappings.

**Test F0.7.** A test VDB with four views (pass-through with an expression and `WHERE`,
`SELECT *`, `JOIN`, and a view on a view). `SYSADMIN.Usage` lists, at object level, every column a
view refers to anywhere, and at column level the source columns of each view column; it does not
tell a pass-through column from an expression. Dropping a column used in an expression, a
`WHERE`, or a `JOIN` makes the VDB `FAILED`, and the failure spreads to nested views. Dropping a
column used only through `SELECT *`, adding a column, and RENAME through `SET NAMEINSOURCE` keep
the VDB `ACTIVE`; `RENAME COLUMN` breaks the views.

**Decision.** Knowledge stores the dependencies from `SYSADMIN.Usage` and the definitions from
`SYSADMIN.Views` per version. Each view column is classified from the view body with sqlglot as
`passthrough`, `expression`, `star`, or `unknown`; a column used by a view but feeding no view
column gets the role `predicate`. Views are recognised by `IsPhysical = false`.

**Consequences.** Dependencies come from Teiid itself; the parser only classifies. Views over
temporary tables or common table expressions are treated conservatively.

## ADR-0007: Knowledge schema and integrity in the database

*2026-09-17. Knowledge.*

**Decision.** The SQLAlchemy 2 models are the single source of the table structure; Alembic
migrations are generated from them and `alembic check` runs in the tests. Enumerations are
`TEXT` with `CHECK`. Every `spec` table carries `spec_version_id`, and references between `spec`
tables use composite foreign keys `(spec_version_id, id)`, so a cross-version reference is
rejected by the database. The content of a version can only change while it is `candidate`
(trigger `spec.guard_sealed_rows` on every content table); a sealed version's attributes are
fixed except `note` and `teiid_connection_type`; status transitions are limited to
`candidate→active|rejected`, `active→superseded|rolled_back`, `superseded→active`, and
`superseded|rejected|rolled_back→purged`; only one version per OBDF can be `active`. Version rows
are never deleted: `spec.purge_version(id)` deletes the content and sets `purged`. The `ops`
schema is append-only and `ops.audit_log` cannot be updated. The role `ascam_owner` owns the
schemas and runs migrations; `ascam_app` only has DML.

**Evidence.** 23 tests on PostgreSQL 16 at the time (8/31/9 tables per schema, sealing, status
transitions, purge, append-only, `CHECK` constraints).

**Consequences.** An application error that would break P2 or P3 fails in the database. A new
`spec` table must include `spec_version_id` and the `trg_guard_sealed` trigger; two tests fail if
it does not.

## ADR-0008: Knowledge Service API

*2026-09-17, revised in F6. Knowledge.*

**Decision.** FastAPI with synchronous SQLAlchemy, one transaction per request, `/health` and
`/ready` (database reachable and schema at the latest migration). Services authenticate with one
bearer token per client (`ui`, `orchestrator`, `executor`), compared in constant time. Credentials
are encrypted in the application with Fernet; several keys can be loaded (MultiFernet) so a key can
be rotated, and secrets are never returned by the API or written to the audit log. Credentials
have names. A declarative YAML/JSON configuration (OBDF, sources, credentials, targets, settings,
naming policy, type mappings) is applied idempotently, non-destructively, and atomically, through
the API, the CLI, or at start-up; secrets appear only as `secret_file`. Each target kind has its
own validation rules. Connection checks run inside the service and are stored in
`registry.connection_check` without secrets.

**Revision F6: commit before the response.** The transaction used to be committed when the
`get_db` dependency closed, which in current FastAPI happens after the response has been sent.
The Executor calls `/sync` and then `/finish` with the new version id at once; when `/finish` came
first, the foreign key failed with 409. In 5 of 60 treatment runs of an early evaluation an
execution was left `running` although the adaptation had finished. All routers now use
`TransactionalRoute`, which commits after the handler and before the response.

## ADR-0009: Where the VDB file and the federated schema come from

*2026-09-17. Knowledge and Execute.*

**Tests F1.4a and F1.4b.** `SYSADMIN.VDBResources` is empty for an XML VDB.
`/deployment=<name>:read-content` with `useStreamAsResponse` returns the original file byte for
byte, and the deployment's content hash is its SHA-1. `get-vdb` gives status, connection type,
models, source mappings, and `validity-errors`.

**Decision.** A sync fetches, all over the network: the `vdb_xml` artifact through `read-content`,
checked against the SHA-1 the server reports (a mismatch becomes a consistency issue); the
effective Σ_S from `SYS.*` and `SYSADMIN.*` over ODBC; status and source mappings from `get-vdb`,
which link a source name to a registered source system.

**Consequences.** No file access on the Teiid host. The SHA-1 also reveals drift without
downloading the file.

## ADR-0010: R2RML at run time and retirement of iteration 1

*2026-09-17.*

**Decision.** Ontop loads `mapping.ttl`; `mapping.obda` is removed from the configuration. The
iteration-1 adaptation engine, which could only edit `.obda` and deployed through the shared
folder, is no longer started; its code is kept as evidence of the earlier design-science
iteration (now in `archive/setup/adaptive-engine`). New components handle ℳ only as R2RML.

**Consequences.** The iteration-1 results had to be repeated on the final architecture.

## ADR-0011: Ontop agent

*2026-09-18. Knowledge and Execute.*

**Context.** Σ_S can be read and changed over the network, but Ontop reads ℳ and 𝒯 as files and
has no API for them.

**Decision.** A sidecar agent next to Ontop is the only component that touches the artifact
files. It offers `GET /health`, `GET /api/v1/artifacts` (with SHA-256, size, modification time),
`GET /api/v1/artifacts/{kind}`, and `POST /api/v1/validate`, with bearer tokens. Only `r2rml` and
`ontology` are exposed; the Ontop properties file, which holds the JDBC password, is never
readable through the API, and file names are derived from the kind, so no arbitrary path can be
requested. The agent runs the Ontop CLI in a one-off container from the same image with
`volumes_from` the Ontop container, so the validated files are the ones the endpoint uses.

**Consequences.** Every other ASCAM component works over the network. The agent needs the
Docker socket, which gives it broad rights; this is accepted and recorded as a limitation.

## ADR-0012: Sync and specification versions

*2026-09-18. Knowledge.*

**Decision.** One sync reads all Teiid system tables in one ODBC connection, plus `get-vdb`,
`read-content` (SHA-1 checked), and ℳ and 𝒯 from the agent (SHA-256 checked). A new version is
made only when the content changed: `content_digest` covers the normalised Σ_S and the hash of
each artifact. The new version is created as `candidate`, filled, and activated, and the previous
active version becomes `superseded` in the same transaction. A difference from the active
version is flagged as `drift_detected`. A failed sync rolls back, writes an `ops.sync_run` row
with status `failed` in a separate transaction, and answers 502. Findings such as
`vdb_validity_error` or `artifact_hash_mismatch` become consistency issues, not sync failures.

## ADR-0013: Parsing the mapping and the ontology

*2026-09-18. Knowledge and Analyze.*

**Decision.** ℳ is parsed as an RDF graph (rdflib) following R2RML, including the shortcut
forms (`rr:subject`, `rr:class`, ...) and referencing object maps with join conditions. Relative
IRIs are resolved against the fixed base `urn:ascam:mapping`. Blank node labels are not stable,
so each predicate-object map gets a structural signature. The columns of a logical table are the
Σ_S columns for `rr:tableName`, or the sqlglot analysis of `rr:sqlQuery` (pass-through,
expression with its source columns, `SELECT *` expanded from Σ_S); columns used in `WHERE`,
`JOIN`, `GROUP BY`, `HAVING`, `ORDER BY`, and subqueries are recorded as SQL references. 𝒯 is
parsed into ontologies, entities (punning allowed), domains (complex class expressions included),
ranges, property relations, and annotations. OWL 2 QL compatibility is flagged on data property
ranges only.

**Evidence.** On the case-study artifacts at the time: 8 TriplesMaps; 11 classes, 39 data
properties, and 9 object properties, with `xsd:boolean` and `xsd:date` flagged outside OWL 2 QL.

## ADR-0014: Column lineage

*2026-09-18. Knowledge, Analyze, Plan.*

**Decision.** The path source column → foreign-table column → view columns → logical column →
term map → predicate → ontology entity is computed at sync time and stored in `spec.column_usage`,
one row per usage of a foreign-table column. Each row has a role (`literal_value`,
`iri_template`, `join_key`, `sql_predicate`, `dynamic_predicate`, `projection_only`), the path,
and the weakest edge on the path, in the order `direct` → `passthrough` → `star` → `expression`
→ `predicate`. A column used in the `WHERE` or `JOIN` of a view becomes `sql_predicate` for every
TriplesMap that reads the view. Vocabulary checks that `ontop validate` does not do are run here
(`predicate_undeclared`, `predicate_kind_mismatch`, `predicate_deprecated_in_use`,
`unused_property`).

**Consequence.** A change to the stored format raises `CONTENT_VERSION`, so a new version is made
even if the OBDF did not change; the content digest must cover the format as well as the data.

## ADR-0015: Impact analysis and decision matrix D11

*2026-09-18. Analyze and Plan. The ADD rule and the use of `hitl` were later changed by ADR-0021
and ADR-0024.*

**Decision.** Impact analysis is a read-only operation of the Knowledge Service
(`POST /api/v1/obdf/{id}/impact`), because all its data is there and the console can use the
same endpoint for a preview. An event is matched to Σ_S by source system, schema (the source's
`default_schema` when the event has none), and the source names of table and column. Rules:

- RENAME is automatic, with `SET NAMEINSOURCE`; ℳ, 𝒯, and views do not change. A clash with
  another column's source name is not automatic.
- DROP is automatic only when every usage is `literal_value` or `projection_only` and the
  weakest edge is not `expression` or `predicate`. A primary-key column, an IRI template, a join
  key, an SQL clause, a dynamic predicate, or a path through a view expression or clause is not
  automatic. A logical table that names the column explicitly in `rr:sqlQuery` gets a
  `rewrite_logical_table` action (found with scenario A002: otherwise `ontop validate` rejects
  the mapping). `SELECT *` needs no such action.
- A property still used by another column is kept (`keep_property`), not deprecated.
- The target TriplesMap of every mapping action is chosen by Knowledge from the lineage. For ADD
  only TriplesMaps whose logical table picks up the new column by itself (`rr:tableName` or
  `SELECT *`) are used.
- Punning and domain clashes for a new property follow the naming policy (`qualify_with_class`)
  or go to the administrator.
- Multi-source models, tables referenced by procedures or triggers, and unparsable logical-table
  SQL make the decision conservative.
- Unknown sources, tables, or columns are `ignored`, not failed.

## ADR-0016: Schema events and the plan life cycle

*2026-09-18. Monitor, Analyze, Plan.*

**Decision.** Events enter only through `POST /api/v1/obdf/{id}/events`. They are idempotent by
`event_uid`: a repeated message returns the existing event and plan, marked `duplicate`. Events
carry source names, not Teiid names. An `auto` plan is `approved` at once; a plan that needs a
person waits as `pending_approval` with a notification. Each plan stores its base specification
version; if the active version changed before approval, approval is refused with 409 and the
plan becomes `superseded` (committed separately so the rollback does not undo it). Irrelevant
events are stored as `ignored` with the reason.

## ADR-0017: The Event Normalizer (orchestrator)

*2026-09-18, revised in F6. Monitor.*

**Context.** The monitors write `ddl_event_log` rows that Debezium sends to Kafka. PostgreSQL
uses `schema_name` and MySQL `db_name`; a RENAME row stores only the old column name; one row
can hold several DDL statements; other ALTER forms are captured too.

**Decision.** The component only normalises and forwards; all decisions stay in Knowledge.
`ddl_command` is parsed with sqlglot in the source's dialect, and each column statement becomes
one event; unsupported statements are sent as operation `other`. `event_uid` is a UUID v5 derived
from the DDL itself (source, log row id, `captured_at`, statement index). Kafka offsets are
committed only after the event was delivered (at least once). Configuration comes from
Knowledge. Messages that do not become events are counted and logged with the reason.

**Revision F6.** The uid was first derived from topic, partition, and offset. After a full
restart with `down -v`, topics are recreated and offsets start at 0 again, so new DDL got the uid
of old events and was silently treated as duplicate. Start-up is also retried until Knowledge
answers; a single attempt left the worker dead.

**Naming.** In the thesis and the paper this component is the **Event Normalizer**, part of the
Monitor phase. The code, the service (`ascam-orchestrator`), and the Kafka consumer group keep
the original name `orchestrator`.

## ADR-0018: Writing the OBDA artifacts

*2026-09-18. Execute.*

**Decision.** The agent writes a new artifact to a temporary file in the same directory and
moves it with `os.replace`, so Ontop never reads a half-written file. Every write first stores
the old content in `ascam-backups/` (kind, timestamp, hash in the name); a restore by `backup_id`
is backed up as well. A caller can send `expected_sha256`, and the write is refused with 409 if
the file changed in between. Only `r2rml` and `ontology` can be written or restored. A reload
restarts the Ontop container and is ready when the SPARQL endpoint answers, not when the
container runs. Backups are pruned per kind.

## ADR-0019: How the VDB, the ontology, and the mapping are edited

*2026-09-18. Execute.*

**Decision.**

- **VDB:** only `ALTER` statements are appended, through an XML parser that keeps CDATA, and the
  VDB version is raised; identifiers are quoted.
- **Ontology:** append-only. New triples go into a managed block at the end of the file, so the
  hand-written part and its comments stay as they are. Adding a property is idempotent; a
  property is never removed but marked `owl:deprecated`. Metadata uses `rdfs:label`,
  `rdfs:comment`, `dcterms:created`, `dcterms:modified`, and `skos:changeNote`.
- **Mapping:** a new predicate-object map is written as a managed block at the end of the file;
  in Turtle, triples for the same subject merge with the existing TriplesMap. Each block is
  wrapped in markers that carry its key (predicate and TriplesMap), so ASCAM can remove its own
  block as text and the file returns to exactly what it was. Removing a hand-written mapping
  needs a re-serialisation with rdflib, which loses comments in the body; the leading comment and
  prefixes are kept, the full content of every version is in Knowledge, and the agent backs up
  the file.
- Mapping edits target a TriplesMap IRI given by the plan; guessing by table name once attached
  a mapping to the wrong TriplesMap.
- A logical-table query can be rewritten to drop a column from its projection; the rewrite is
  refused if the query cannot be parsed or would project nothing.
- Editors are pure functions (text in, text out).

## ADR-0020: Execution engine

*2026-09-18, revised in F5 and F6. Execute. The order of steps for the OBDA layer was changed
by ADR-0022.*

**Decision.** Order: deploy Σ′_S as a new VDB version → write and validate ℳ′ and 𝒯′ → move the
connection → reload Ontop → verify → revert if needed. Until the new VDB version gets `ANY` the
old version serves every query, so a failure in deploy or validate only removes the new version
and restores the artifacts. Validation uses `jdbc:teiid:<vdb>@mm://host:31000;version=<N+1>`.
Verification compares graph fingerprints (triple count per predicate) with an expectation per
pattern: P-002 requires the target predicates to disappear and the others to stay, P-003
requires identical answers, P-001 requires that no predicate disappears. After success the old
VDB version gets `NONE` and Knowledge is synced, so the new specification version comes from
the real OBDF. Every step is reported with its duration. A plan whose base version is no longer
active is refused and marked `superseded`. `/control/pause` and `/control/resume` stop and
resume picking up plans. Passwords and tokens come from the Executor's own Docker secrets.

**F5: Teiid restarts.** VDB versions deployed through the management API live in WildFly's
`standalone/data`; a volume was added for it, so they survive container re-creation. A test
showed that connection types survive `docker restart`.

**F6: every execution is closed.** Only `ExecutionError` was caught; other errors after
`start_execution` left an execution `running` for ever. The whole region is now protected: an
unexpected error closes the execution as `failed` with the last finished step (`after_step`),
and `/finish` is retried up to three times on transient errors.

## ADR-0021: ADD needs the administrator's approval

*2026-09-22. Plan. Made unconditional by ADR-0024.*

**Context.** Of the three operations only ADD adds a new semantic commitment to the TBox: a
property IRI, its domain, range, and label, which every downstream consumer will see. Naming new
vocabulary is a decision for a person.

**Decision.** ADD waits for approval in the web console. The plan is still complete (VDB,
ontology, and mapping actions); before this, a plan waiting for approval held only the VDB
action, so after approval the property and its mapping were never created. Notifications state
the operation, source, object, number of actions, and reasons. The console is a
backend-for-frontend: the browser never holds a service token, requests are forwarded through an
allow list, sessions use a signed `HttpOnly`, `SameSite=Strict` cookie, and state-changing
requests need the `X-ASCAM-UI` header. Knowledge accepts the administrator name in `X-ASCAM-User`
only from the `ui` client, so decisions are recorded as `ui:<name>`.

**Effect on the evaluation.** The proposal's C_FMI criterion assumes N_manual = 0. With this
decision A001 has one manual decision and no manual repair, so the metric separates decisions
from repairs, and Δt_adapt is reported both end to end and as machine time without the
administrator's decision.

## ADR-0022: Blue-green OBDA layer

*2026-10-08. Execute.*

**Context.** With one Ontop instance restarted in place, the endpoint was down for a median of
9–16 s per adaptation, the restart caused the tail of the Δt_adapt distribution (one run took
90.8 s, 63.4 s of it restarting), and verification could only happen after traffic was moved, so
users could see a version that was later rolled back.

**Decision.** An nginx proxy takes the old endpoint name and port (`vkg-system-ontop-teiid:8080`).
Two instances, `vkg-system-ontop-blue` and `vkg-system-ontop-green`, each read their own slot in
`setup/vkg-system/slots/<colour>/`; only the active one runs. Each instance is pinned to one VDB
version with `;version=N` in its JDBC URL, so switching the proxy also switches the federation
layer Ontop reads, and a rollback to the old instance returns to the old VDB version. Execution
order: deploy VDB N+1 → write ℳ′ and 𝒯′ into the standby slot and validate them → start the
standby → verify directly on the standby → switch the proxy with `nginx -s reload` → promote the
artifacts to `config/` and stop the old instance → N+1 gets `ANY` and N gets `NONE`. A failure
before the switch is invisible to users. `config/` always mirrors the active instance, so Knowledge
reads ℳ and 𝒯 where it always did. In the evaluation, t_end is the end of the `switch` step. The
old behaviour remains available as `ASCAM_EXEC_OBDA_STRATEGY=restart`.

**Consequences.** Adaptation no longer interrupts the endpoint. Peak memory grows by one Ontop
JVM during an adaptation, and every query passes one more hop.

## ADR-0023: Adjusting Teiid views when a column is dropped

*2026-10-08. Analyze, Plan, Execute.*

**Context.** ASCAM used to change only foreign tables. A DROP of a column that a view passed on
unchanged was decided automatically without touching the view, and the new VDB then held a view
that referred to a missing column. The Reference Guide allows `ALTER VIEW name AS query` but no
change of column information.

**Decision.** Removing a column from a view is treated as drop(v, c) and propagated along the
Teiid dependencies, nested views included. It is automatic only when it narrows the projection:
the view column that passes the column on is removed with `ALTER VIEW`, and the rule continues one
level up; `SELECT *` views are not rewritten but are still propagated. It is not automatic when
the column is used in `WHERE`, `JOIN`, `GROUP BY`, `HAVING`, or `ORDER BY` of the view, inside an
expression, or is passed on by a view with inline columns, a materialised view, a view that cannot
be parsed, or a view that would lose all its columns. The new definition is the original text
with projection items removed, so Teiid functions and clauses are not rewritten by a parser.
Explicit logical-table projections of a disappearing view column are rewritten as for a
foreign-table column. Verification of P-002 requires all deprecated predicates, and only those,
to disappear. The statement form is configurable (`ASCAM_EXEC_VIEW_STATEMENT=alter|recreate`).

**Test F0.8.** Seven VDB versions, all as expected: dropping a column without adjusting the view
fails; `ALTER VIEW` on both levels gives `ACTIVE`, as does `DROP VIEW`/`CREATE VIEW`; adjusting only
the base view fails on the nested view (`TEIID31118`), so propagation is required; a view with
inline columns cannot be narrowed (`TEIID30066`). The default stays `alter`.

**Case study.** The virtual model `layanan` (three views) and the TriplesMaps `MapPenerimaAktif`,
`MapRiwayat`, and `MapProgramBerakhir` were added, with scenarios A007–A009.

**Known compromise.** For a view without a key Ontop 4.1.1 adds `SELECT DISTINCT`, and Teiid then
refuses `ORDER BY` over an IRI expression (`TEIID30088`). A key can only be declared together with
inline columns, which cannot be narrowed by `ALTER VIEW`.

## ADR-0024: Approval only for ADD, notification for unsafe dropping and renaming

*2026-10-16. Plan, Knowledge, Monitor.*

**Context.** `hitl` was used for two different things: ADD, whose plan is complete and only the
property name and domain are open, and DROP/RENAME that cannot be shown to be safe, whose actions
may be incomplete. In A009, for example, the view `layanan.v_program_berakhir` has to be redefined
by a person; approving that plan would have been risky, so in an early evaluation it was always
rejected. Administrators also had no view of which OBDF elements depend on a column.

**Decision.**

- Three decisions. `auto` is unchanged. `hitl` is only for ADD and always applies; the
  `adaptation.add_column` setting no longer makes ADD automatic. `notify` is new: for a DROP or
  RENAME that meets an unsafe condition, an unsupported operation, or a RENAME without a new name.
- A `notify` plan has status `notified` and no actions, so nothing can be approved or executed.
  The affected elements 𝒟(c, ℱ) are stored per layer in `impact.affected` (columns, views as a
  transitive closure over the dependencies, TriplesMaps, properties), and the notification names
  them, the reasons, and the next step: repair by hand, then sync.
- The administrator may revise an ADD plan in the console (property name and domain). Knowledge
  checks the local name, that the domain is a class of an exposing TriplesMap, and IRI clashes. A
  changed choice becomes a new plan (`revision_of`, status `approved`) and the original becomes
  `revised`, because `ops` rows are append-only.
- `GET /api/v1/obdf/{id}/lineage` builds a read-only graph from `column_usage` of the active
  version; the console highlights every path through a node, and each notification links to the
  lineage of the changed column.
- Migration 0007 widens the `decision` and `status` constraints and adds `revision_of`.

**Evidence.** 158 Knowledge tests and 25 console tests. On the case-study artifacts the old and new
analysis gave byte-identical plans for A001 and A004 and identical decisions, actions, and reasons
for A002 and A005–A008; only A009 changed, from `hitl` with three actions to `notify` with
none, with the same reasons.

**Effect on the evaluation.** A009 is expected to be `notify`; a run records `notified`, no
actions, a notification naming the view, no execution, an unchanged version, and an affected query
that still fails. A009 needs no manual decision; its repair remains manual.
