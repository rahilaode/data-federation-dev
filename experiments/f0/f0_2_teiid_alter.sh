#!/usr/bin/env bash
# F0.2 — Uji kelayakan: pernyataan ALTER di dalam metadata DDL VDB Teiid.
#
# Pertanyaan yang diuji:
#   3a. RENAME diserap lewat ALTER COLUMN ... OPTIONS (SET NAMEINSOURCE ...)  (keputusan D8)
#   3b. RENAME kolom Teiid lewat ALTER FOREIGN TABLE ... RENAME COLUMN
#   3c. ADD dan DROP kolom lewat ALTER FOREIGN TABLE ... ADD/DROP COLUMN
#   +   Σ_S dapat dibaca dari SYS.Columns lewat transport ODBC (pratinjau F0.3)
#
# Sintaks mengikuti Teiid Reference Guide, "Schema object DDL" (ALTER TABLE)
# dan "BNF for SQL grammar" (ALTER TABLE, alter column options).
#
# Uji memakai tabel sementara f0_uji dan VDB sementara f0*; VDB government,
# tabel studi kasus, dan artefak OBDF tidak disentuh. ASCAM dihentikan selama uji
# lalu dinyalakan kembali. Semua objek sementara dibersihkan di akhir (juga bila gagal).
#
# Pemakaian (dari root repository, stack sedang berjalan):
#   experiments/f0/f0_2_teiid_alter.sh
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
DEP="$ROOT/setup/data-federation/deployments"
OUT="$ROOT/results/f0/f0_2_$(date +%Y%m%dT%H%M%S)"
mkdir -p "$OUT"
exec > >(tee "$OUT/output.txt") 2>&1
T0=$(date -u +%Y-%m-%dT%H:%M:%SZ)
VDBS=(f0base_my f0base_pg f0a f0b f0c)

pg()  { docker exec datasources-pgsql psql -U postgres -d kemensos -q -c "$1"; }
my()  {
  local o rc
  o=$(docker exec datasources-mysql mysql -umysql -pmysql dukcapil -e "$1" 2>&1); rc=$?
  o=$(printf '%s\n' "$o" | grep -v 'Using a password' || true)
  [ -n "$o" ] && printf '%s\n' "$o"
  return $rc
}
teiid() {  # $1 = nama VDB, $2 = SQL
  echo "   SQL> $2"
  docker exec -e PGPASSWORD=Password12345_ -e PGSSLMODE=disable -e PGGSSENCMODE=disable \
    datasources-pgsql psql -h data-federation-teiid -p 35432 -U user1 -d "$1" \
    -A -F ' | ' -c "$2" 2>&1 | grep -v -i 'warning' | sed 's/^/        /'
}

write_vdb() {  # $1 nama, $2 model, $3 translator, $4 jndi, $5 DDL
  cat > "$DEP/$1-vdb.xml" <<XML
<?xml version="1.0" encoding="UTF-8"?>
<vdb name="$1" version="1">
  <model visible="true" name="$2">
    <source name="$2" translator-name="$3" connection-jndi-name="$4"/>
    <metadata type="DDL"><![CDATA[
$5
    ]]></metadata>
  </model>
</vdb>
XML
}

deploy() {  # $1 nama -> hasil scanner WildFly
  local f="$DEP/$1-vdb.xml"
  rm -f "$f.failed"; touch "$f.dodeploy"
  for _ in $(seq 1 120); do
    if [ ! -e "$f.dodeploy" ] && [ ! -e "$f.isdeploying" ]; then
      if [ -e "$f.failed" ]; then echo "   deploy $1: FAILED -> $(head -c 400 "$f.failed")"; return 1; fi
      if [ -e "$f.deployed" ]; then echo "   deploy $1: DEPLOYED"; sleep 2; return 0; fi
    fi
    sleep 0.5
  done
  echo "   deploy $1: TIMEOUT"; return 1
}

undeploy() {  # hapus .deployed -> scanner melakukan undeploy (README deployments WildFly)
  local f="$DEP/$1-vdb.xml"
  if [ -e "$f.deployed" ]; then
    rm -f "$f.deployed"
    for _ in $(seq 1 60); do [ -e "$f.undeployed" ] && break; sleep 0.5; done
  fi
  rm -f "$f" "$f.undeployed" "$f.dodeploy" "$f.failed" "$f.isdeploying"
}

cleanup() {
  echo; echo "== pembersihan"
  for v in "${VDBS[@]}"; do undeploy "$v"; done
  my "DROP TABLE IF EXISTS f0_uji;" >/dev/null
  pg "DROP TABLE IF EXISTS public.f0_uji;" >/dev/null
  docker start ascam-adaptive-engine >/dev/null && echo "   ASCAM dinyalakan kembali"
  echo "   sisa berkas uji di folder deployment: $(find "$DEP" -maxdepth 1 -name 'f0*' | wc -l)"
  echo "   hasil tersimpan di: ${OUT#$ROOT/}"
}
trap cleanup EXIT

MY_DDL="CREATE FOREIGN TABLE f0_uji (id integer not null primary key, nama varchar(50), tgl date) OPTIONS(UPDATABLE 'FALSE');"
PG_DDL="CREATE FOREIGN TABLE f0_uji (id integer not null primary key, nama varchar(50), kode varchar(10)) OPTIONS(UPDATABLE 'FALSE');"
COLS="SELECT SchemaName, TableName, Name, Position, NameInSource, DataType FROM SYS.Columns WHERE TableName = 'f0_uji' ORDER BY Position"

echo "== 0) persiapan"
docker stop ascam-adaptive-engine >/dev/null && echo "   ASCAM dihentikan selama uji"
my "DROP TABLE IF EXISTS f0_uji; CREATE TABLE f0_uji (id INT PRIMARY KEY, nama VARCHAR(50), tgl DATE);
    INSERT INTO f0_uji VALUES (1,'Ani','1990-01-01'),(2,'Budi','1985-05-05');"
pg "DROP TABLE IF EXISTS public.f0_uji; CREATE TABLE public.f0_uji (id INT PRIMARY KEY, nama VARCHAR(50), kode VARCHAR(10));
    INSERT INTO public.f0_uji VALUES (1,'Ani','K1'),(2,'Budi','K2');"

echo; echo "== 1) VDB baseline, sebelum perubahan skema fisik"
write_vdb f0base_my m mysql5 java:/mysql-dukcapil "$MY_DDL"; deploy f0base_my
write_vdb f0base_pg p postgresql java:/pgsql-kemensos "$PG_DDL"; deploy f0base_pg
teiid f0base_my "SELECT id, nama, tgl FROM m.f0_uji ORDER BY id"
teiid f0base_pg "SELECT id, nama, kode FROM p.f0_uji ORDER BY id"
teiid f0base_my "$COLS"

echo; echo "== 2) perubahan skema fisik di sumber"
my "ALTER TABLE f0_uji RENAME COLUMN tgl TO tgl_baru;" && echo "   MySQL : tgl -> tgl_baru"
pg "ALTER TABLE public.f0_uji ADD COLUMN email VARCHAR(100);
    ALTER TABLE public.f0_uji DROP COLUMN kode;
    UPDATE public.f0_uji SET email = 'ani@contoh.id' WHERE id = 1;" && echo "   PgSQL : +email, -kode"
echo "   -- VDB baseline sesudah perubahan fisik (diharapkan GAGAL)"
teiid f0base_my "SELECT id, nama, tgl FROM m.f0_uji ORDER BY id"
teiid f0base_pg "SELECT id, nama, kode FROM p.f0_uji ORDER BY id"

echo; echo "== 3a) RENAME diserap lewat NAMEINSOURCE (D8)"
write_vdb f0a m mysql5 java:/mysql-dukcapil "$MY_DDL
ALTER FOREIGN TABLE f0_uji ALTER COLUMN tgl OPTIONS (SET NAMEINSOURCE 'tgl_baru');"
deploy f0a && { teiid f0a "SELECT id, nama, tgl FROM m.f0_uji ORDER BY id"; teiid f0a "$COLS"; }

echo; echo "== 3b) RENAME kolom Teiid lewat RENAME COLUMN"
write_vdb f0b m mysql5 java:/mysql-dukcapil "$MY_DDL
ALTER FOREIGN TABLE f0_uji RENAME COLUMN tgl TO tgl_baru;"
deploy f0b && { teiid f0b "SELECT id, nama, tgl_baru FROM m.f0_uji ORDER BY id"; teiid f0b "$COLS"; }

echo; echo "== 3c) ADD dan DROP lewat ALTER (PostgreSQL)"
write_vdb f0c p postgresql java:/pgsql-kemensos "$PG_DDL
ALTER FOREIGN TABLE f0_uji ADD COLUMN email varchar(100);
ALTER FOREIGN TABLE f0_uji DROP COLUMN kode;"
deploy f0c && {
  teiid f0c "SELECT id, nama, email FROM p.f0_uji ORDER BY id"
  teiid f0c "$COLS"
  echo "   -- kolom yang di-DROP tidak lagi dikenal Teiid (diharapkan GAGAL validasi)"
  teiid f0c "SELECT kode FROM p.f0_uji"
}

echo; echo "== 4) log Teiid terkait VDB uji"
docker logs --since "$T0" data-federation-teiid 2>&1 \
  | grep -E "VDB (f0base_my|f0base_pg|f0a|f0b|f0c)|TEIID(31|40)[0-9]+" | cut -c1-240 | head -40
