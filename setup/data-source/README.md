# Data sources

Two source databases of the social-assistance case study, each with its own schema monitor.

| Container | DBMS | Database | Host port | Tables |
|---|---|---|---|---|
| `datasources-pgsql` | PostgreSQL 15 (`debezium/postgres:15`, `wal_level=logical`) | `kemensos` | 15433 | `program_bansos`, `penerima_manfaat`, `transaksi_bansos`, `eligibility_check` |
| `datasources-mysql` | MySQL 8.0 (`quay.io/debezium/example-mysql:2.0`) | `dukcapil` | 13306 | `master_wilayah`, `master_keluarga`, `master_penduduk`, `riwayat_perubahan_data` |

`pgsql/init.sql` and `mysql/init.sql` create the tables, the sample data, and the monitors. They
run only when the data volume is empty; `./start.sh` removes the volumes and so rebuilds both
databases from these files.

**Schema monitors.** `ALTER TABLE ... ADD COLUMN`, `DROP COLUMN`, and `RENAME COLUMN` are recorded
as rows of `ddl_event_log`. Debezium captures that log table only, never the business tables
(ADR-0017; C_safe in `docs/evaluation.md`). Whether a table is federated is decided by Knowledge,
not by the monitors.

- PostgreSQL: an event trigger on `ddl_command_end` writes every such statement to
  `schema_monitor.ddl_event_log` (with `REPLICA IDENTITY FULL`), and sets `is_regulated` when the
  table is listed in `schema_monitor.captured_tables_ref`.
- MySQL has no DDL triggers. The Event Scheduler runs `sp_detect_ddl_changes` every 10 seconds for
  the tables listed in `captured_tables_ref`; it compares `information_schema.COLUMNS` with the
  snapshot in `ddl_column_snapshot` and writes the differences to `ddl_event_capture`, from where a
  trigger forwards them to `ddl_event_log`. A column that disappears and a column that appears in
  the same table with the same position and type are recorded as one `RENAME COLUMN`. A change
  that is undone before the next scan is not seen (`docs/evaluation.md`).

Connect by hand:

```bash
docker exec -it datasources-pgsql psql -U postgres -d kemensos
docker exec -it datasources-mysql mysql -umysql -pmysql dukcapil
```
