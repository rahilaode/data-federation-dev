#!/bin/bash
# Step 4 of 4: the init fails (and the container stops) if the split is not what was intended.
set -eo pipefail
q() { psql -At -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -c "$1"; }
tables=$(q "select count(*) from information_schema.tables where table_schema='public' and table_type='BASE TABLE'")
fks=$(q "select count(*) from pg_constraint c join pg_namespace n on n.oid=c.connamespace where n.nspname='public' and c.contype='f'")
pks=$(q "select count(*) from pg_constraint c join pg_namespace n on n.oid=c.connamespace where n.nspname='public' and c.contype='p'")
monitored=$(q "select count(*) from schema_monitor.captured_tables_ref where is_active")
trigger=$(q "select count(*) from pg_event_trigger where evtname='trg_monitor_alter_column'")
echo "[npd] reporting: $tables tables, $pks primary keys, $fks foreign keys, $monitored monitored, $trigger event trigger (expected 25, 25, 19, 25, 1)"
[ "$tables" = 25 ] && [ "$pks" = 25 ] && [ "$fks" = 19 ] && [ "$monitored" = 25 ] && [ "$trigger" = 1 ] \
  || { echo "[npd] check FAILED"; exit 1; }
