#!/bin/bash
# Step 1 of 4 (runs once, on the first start of an empty volume).
# Loads the FULL NPD dump into the database "registry"; 02-split-registry.sql then removes
# the tables that belong to the other source.
# The dump comes from MySQL 5.7 and sets SQL_MODE='...,POSTGRESQL', a mode MySQL 8 no longer
# has (ERROR 1231). It is replaced by the modes the dump needs: ANSI_QUOTES for its
# double-quoted identifiers, plus PIPES_AS_CONCAT and IGNORE_SPACE.
set -eo pipefail
SOCK="${SOCKET:-/var/run/mysqld/mysqld.sock}"
run_mysql() { mysql --protocol=socket --socket="$SOCK" -uroot -p"$MYSQL_ROOT_PASSWORD" "$@"; }
echo "[npd] loading the MySQL dump into $MYSQL_DATABASE ..."
sed "s/SQL_MODE='NO_AUTO_VALUE_ON_ZERO,POSTGRESQL'/SQL_MODE='NO_AUTO_VALUE_ON_ZERO,ANSI_QUOTES,PIPES_AS_CONCAT,IGNORE_SPACE'/" \
    /npd/npd.mysql | run_mysql --default-character-set=utf8mb4 "$MYSQL_DATABASE"
run_mysql -e "GRANT ALL ON \`$MYSQL_DATABASE\`.* TO '$MYSQL_USER'@'%'; FLUSH PRIVILEGES;"
