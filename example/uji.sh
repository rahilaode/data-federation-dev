#!/usr/bin/env bash
# =============================================================================
# uji.sh — menguji contoh Teiid multi-source model.
#
#   ./uji.sh        kueri dasar: metadata, gabungan tiga sumber, filter per sumber
#   ./uji.sh drop   skenario: hanya Dinsos Jawa Barat menghapus kolom email, lalu dipulihkan
#
# Kueri dikirim ke Teiid lewat transport ODBC (protokol PostgreSQL, port 35432), memakai
# klien psql di kontainer example-pgsql yang satu jaringan dengan example-teiid.
# Jalankan dari folder example/ setelah: docker-compose up -d --build
# =============================================================================
set -uo pipefail
cd "$(dirname "$0")"

TEIID="host=example-teiid port=35432 dbname=nasional user=user1 password=Password12345_ sslmode=disable gssencmode=disable"

teiid() {   # kueri ke VDB nasional di Teiid
  docker exec -i example-pgsql psql "$TEIID" -X -v ON_ERROR_STOP=1 -c "$1"
}
sumber() {  # kueri langsung ke salah satu basis data sumber
  docker exec -i example-pgsql psql -U postgres -d "$1" -X -v ON_ERROR_STOP=1 -c "$2"
}

echo "== menunggu VDB nasional aktif di Teiid (maks. 240 s) =="
for i in $(seq 1 48); do
  if galat=$(teiid "SELECT 1" 2>&1); then echo "siap"; break; fi
  if [ "$i" -eq 48 ]; then
    echo "VDB belum aktif setelah 240 s. Galat terakhir dari Teiid:"
    echo "$galat"
    echo; echo "-- marker deployment (teiid/deployments):"
    ls -la teiid/deployments
    echo; echo "-- log Teiid yang relevan:"
    docker logs example-teiid 2>&1 | grep -i -E 'nasional|TEIID[0-9]+|ERROR|WFLYDS' | tail -40
    exit 1
  fi
  sleep 5
done

dasar() {
  echo; echo "== 1. kolom foreign table di metadata Teiid (provinsi harus ada) =="
  teiid "SELECT \"Name\", \"DataType\" FROM SYS.Columns
         WHERE \"SchemaName\" = 'dinsos' AND \"TableName\" = 'penerima_manfaat'
         ORDER BY \"Position\""

  echo; echo "== 2. satu foreign table, data dari tiga sumber (UNION ALL) =="
  teiid "SELECT provinsi, penerima_id, nik, nama_lengkap, email
         FROM dinsos.penerima_manfaat ORDER BY provinsi, penerima_id"

  echo; echo "== 3. filter per sumber: hanya sumber jabar yang diakses =="
  teiid "SELECT provinsi, penerima_id, nama_lengkap
         FROM dinsos.penerima_manfaat WHERE provinsi = 'jabar'"
}

drop() {
  echo; echo "== 4. Dinsos Jawa Barat (saja) menghapus kolom email =="
  sumber dinsos_jabar "CREATE TABLE IF NOT EXISTS cadangan_email AS SELECT penerima_id, email FROM penerima_manfaat"
  sumber dinsos_jabar "ALTER TABLE penerima_manfaat DROP COLUMN email"

  echo; echo "== 5. kueri yang membaca email: diharapkan GAGAL (sumber jabar tidak punya kolom email) =="
  teiid "SELECT provinsi, nik, email FROM dinsos.penerima_manfaat ORDER BY provinsi" \
    || echo ">> gagal, sesuai harapan"

  echo; echo "== 6. kueri tanpa email: tetap berjalan (Teiid hanya meminta kolom yang dipakai) =="
  teiid "SELECT provinsi, nik, nama_lengkap FROM dinsos.penerima_manfaat ORDER BY provinsi"

  echo; echo "== 7. kueri email yang dibatasi ke sumber lain: berjalan =="
  teiid "SELECT provinsi, nik, email FROM dinsos.penerima_manfaat WHERE provinsi <> 'jabar' ORDER BY provinsi"

  echo; echo "== 8. pemulihan kolom email di jabar =="
  sumber dinsos_jabar "ALTER TABLE penerima_manfaat ADD COLUMN email VARCHAR(100)"
  sumber dinsos_jabar "UPDATE penerima_manfaat p SET email = c.email FROM cadangan_email c WHERE c.penerima_id = p.penerima_id"
  sumber dinsos_jabar "DROP TABLE cadangan_email"
  teiid "SELECT provinsi, nik, email FROM dinsos.penerima_manfaat ORDER BY provinsi"
}

dasar
if [ "${1:-}" = "drop" ]; then
  drop
fi
