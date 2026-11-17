# Data sources

The two sources of the running example (`docs/running-example.md`), each with its own schema
monitor. The rows are invented and kept small (two or three per table). The NPD case study is on
branch `feature/npd`.

| Container | DBMS | Database | Host port | Tables |
|---|---|---|---|---|
| `datasources-pgsql` | PostgreSQL 15 (`debezium/postgres:15`, `wal_level=logical`) | `reporting` | 15433 | 12 tables in `public`: `person`, `contact`, `product`, `note`, `memo`, `doc`, `staff`, `city`, `account`, `event`, `audit`, `tool` |
| `datasources-mysql` | MySQL 8.0 (`quay.io/debezium/example-mysql:2.0`) | `registry` | 13306 | 1 table: `member` |

The init scripts run only when the data volume is empty, in this order:

| Script | PostgreSQL (`pgsql/`) | MySQL (`mysql/`) |
|---|---|---|
| `01-example.sql` | Creates the 12 tables and their rows | Creates `member` and its rows |
| `02-schema-monitor.sql` | Event trigger and `schema_monitor.*` tables, all 12 tables monitored | Event Scheduler job and monitor tables, `member` monitored |

Which table serves which scenario is listed in `docs/running-example.md`. `./start.sh` removes
the volumes and so reloads both databases.

**Schema monitors.** `ALTER TABLE ... ADD COLUMN`, `DROP COLUMN`, and `RENAME COLUMN` are recorded
as rows of `ddl_event_log`. Debezium captures that log table only, never the business tables
(ADR-0017; C_safe in `docs/evaluation.md`). Whether a table is federated is decided by Knowledge,
not by the monitors.

- PostgreSQL: an event trigger on `ddl_command_end` writes every such statement on a table that is
  listed (and active) in `schema_monitor.captured_tables_ref` to `schema_monitor.ddl_event_log`
  (with `REPLICA IDENTITY FULL`); statements on any other table are not logged. The table name is
  taken from `pg_class` (so its case is kept) and the column name without its quotes.
- MySQL has no DDL triggers. The Event Scheduler runs `sp_detect_ddl_changes` every 10 seconds for
  the tables listed in `captured_tables_ref`; it compares `information_schema.COLUMNS` with the
  snapshot in `ddl_column_snapshot` and writes the differences to `ddl_event_capture`, from where a
  trigger forwards them to `ddl_event_log`. A column that disappears and a column that appears in
  the same table with the same position and type are recorded as one `RENAME COLUMN`. A change
  that is undone before the next scan is not seen (`docs/evaluation.md`).

Connect by hand:

```bash
docker exec -it datasources-pgsql psql -U postgres -d reporting
docker exec -it datasources-mysql mysql -umysql -pmysql registry
```
