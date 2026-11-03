#!/bin/bash
# Step 4 of 4: the init fails (and the container stops) if the split is not what was intended,
# including any foreign key left pointing at a dropped table.
set -eo pipefail
SOCK="${SOCKET:-/var/run/mysqld/mysqld.sock}"
q() { mysql --protocol=socket --socket="$SOCK" -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "$1"; }
db="$MYSQL_DATABASE"
# the 45 NPD tables, without the 4 tables of the schema monitor
tables=$(q "select count(*) from information_schema.tables where table_schema='$db' and table_type='BASE TABLE'
            and table_name not in ('captured_tables_ref','ddl_event_log','ddl_event_capture','ddl_column_snapshot')")
pks=$(q "select count(*) from information_schema.table_constraints where table_schema='$db' and constraint_type='PRIMARY KEY'
         and table_name not in ('captured_tables_ref','ddl_event_log','ddl_event_capture','ddl_column_snapshot')")
fks=$(q "select count(*) from information_schema.referential_constraints where constraint_schema='$db'")
dangling=$(q "select count(*) from information_schema.referential_constraints r left join information_schema.tables t
             on t.table_schema=r.unique_constraint_schema and t.table_name=r.referenced_table_name
             where r.constraint_schema='$db' and t.table_name is null")
monitored=$(q "select count(*) from $db.captured_tables_ref where is_active = 1")
snapshot=$(q "select count(distinct table_name) from $db.ddl_column_snapshot")
event=$(q "select count(*) from information_schema.events where event_schema='$db' and event_name='evt_detect_ddl_changes'")
echo "[npd] registry: $tables tables, $pks primary keys, $fks foreign keys, $dangling dangling, $monitored monitored, $snapshot in snapshot, $event event (expected 45, 45, 59, 0, 45, 45, 1)"
[ "$tables" = 45 ] && [ "$pks" = 45 ] && [ "$fks" = 59 ] && [ "$dangling" = 0 ] && [ "$monitored" = 45 ] \
  && [ "$snapshot" = 45 ] && [ "$event" = 1 ] || { echo "[npd] check FAILED"; exit 1; }
