#!/bin/sh
# Membuat role aplikasi ascam_app (hak DML saja; bukan pemilik skema).
# Dijalankan sekali oleh image postgres saat volume data masih kosong.
set -eu
APP_PASSWORD=$(cat /run/secrets/knowledge_db_app_password)
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v app_password="$APP_PASSWORD" -v db="$POSTGRES_DB" <<'SQL'
CREATE ROLE ascam_app LOGIN PASSWORD :'app_password';
GRANT CONNECT ON DATABASE :"db" TO ascam_app;
SQL
