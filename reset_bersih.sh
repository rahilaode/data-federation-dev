#!/usr/bin/env bash
# PERINGATAN: reset_bersih.sh MENGHAPUS SELURUH LINGKUNGAN beserta volumenya: data sumber
# PostgreSQL dan MySQL (dibangun ulang dari init.sql), VDB yang ter-deploy, topik Kafka dan
# offset konektor, basis data Knowledge, serta cadangan agen Ontop. Hasil evaluasi di
# results/ (berkas di host) TIDAK disentuh.
#
# Urutan:
#   1) turunkan semua stack dari yang paling bergantung ke yang paling dasar (down -v);
#   2) kembalikan artefak di host: mapping dan ontologi dari git, penanda deployment VDB;
#   3) naikkan sumber data dan tunggu sampai init.sql selesai;
#   4) serahkan ke ./jalankan.sh, yang menaikkan Teiid, Ontop, Kafka/Debezium, dan ASCAM
#      berurutan dengan penantian kesiapan, sinkronisasi awal, dan uji rantai.
#
# Pemakaian:  ./reset_bersih.sh          (meminta konfirmasi)
#             ./reset_bersih.sh --ya     (tanpa konfirmasi)
set -euo pipefail
cd "$(dirname "$0")"

KONFIRMASI=1
[ "${1:-}" = "--ya" ] && KONFIRMASI=0

if [ "$KONFIRMASI" -eq 1 ]; then
  echo "Seluruh kontainer dan volume OBDF serta ASCAM akan dihapus, termasuk data sumber."
  read -r -p "Ketik 'hapus' untuk melanjutkan: " jawaban
  [ "$jawaban" = "hapus" ] || { echo "Dibatalkan."; exit 1; }
fi

compose() { docker compose -f "$1" "${@:2}"; }

tunggu() {  # $1 deskripsi, $2 batas detik, sisanya perintah yang harus berhasil
  local deskripsi="$1" batas="$2"; shift 2
  local mulai=$SECONDS
  until "$@" >/dev/null 2>&1; do
    if (( SECONDS - mulai >= batas )); then
      echo "  GAGAL: $deskripsi belum siap setelah $batas detik"; return 1
    fi
    sleep 3
  done
  echo "  $deskripsi siap ($(( SECONDS - mulai )) s)"
}

echo "==== 1) menurunkan semua stack beserta volumenya ===="
# dari yang paling bergantung ke yang paling dasar
for f in setup/ascam/ui setup/ascam/executor setup/ascam/orchestrator setup/ascam/ontop-agent \
         setup/ascam/knowledge setup/ascam/adaptive-engine setup/ascam/schema-monitor \
         setup/vkg-system setup/data-federation setup/data-source; do
  if [ -f "$f/docker-compose.yaml" ]; then
    echo "  $f"
    compose "$f/docker-compose.yaml" down -v --remove-orphans >/dev/null 2>&1 || true
  fi
done
sisa=$(docker ps -a --format '{{.Names}}' | grep -E '^(ascam-|datasources-|data-federation|vkg-system)' || true)
if [ -n "$sisa" ]; then
  echo "  kontainer tersisa, dihapus paksa:"; echo "$sisa" | sed 's/^/    /'
  echo "$sisa" | xargs -r docker rm -f >/dev/null
fi

echo "==== 2) mengembalikan artefak di host ===="
git checkout -- setup/vkg-system/config/mapping.ttl setup/vkg-system/config/ontology_file.ttl
echo "  mapping.ttl dan ontology_file.ttl dikembalikan ke isi di git"
DEPLOY_DIR=setup/data-federation/deployments
rm -f "$DEPLOY_DIR"/*.dodeploy "$DEPLOY_DIR"/*.isdeploying "$DEPLOY_DIR"/*.deployed \
      "$DEPLOY_DIR"/*.failed "$DEPLOY_DIR"/*.undeployed "$DEPLOY_DIR"/*.pending
touch "$DEPLOY_DIR"/government-vdb.xml.dodeploy
rm -rf setup/vkg-system/slots setup/vkg-system/proxy/runtime   # dibuat ulang oleh up.sh (ADR-0022)
chmod 777 "$DEPLOY_DIR"
echo "  penanda deployment VDB disiapkan (government-vdb.xml.dodeploy)"
ubah=$(git status --porcelain -- setup | grep -v '\.dodeploy$' || true)
if [ -n "$ubah" ]; then
  echo "  PERHATIAN: masih ada perubahan pada setup/ yang tidak berasal dari git:"
  echo "$ubah" | sed 's/^/    /'
fi

echo "==== 3) sumber data ===="
docker network create ascam-networks >/dev/null 2>&1 || true
compose setup/data-source/docker-compose.yaml up -d >/dev/null
tunggu "PostgreSQL" 120 docker exec datasources-pgsql pg_isready -U postgres -d kemensos
tunggu "MySQL" 180 docker exec datasources-mysql sh -c 'mysqladmin ping -uroot -p"$MYSQL_ROOT_PASSWORD" --silent'
# init.sql selesai bila tabel terakhir dan tabel log monitor sudah ada
tunggu "skema Kemensos (init.sql)" 120 docker exec datasources-pgsql psql -U postgres -d kemensos -tAc \
  "SELECT 1/count(*) FROM information_schema.tables WHERE table_schema='schema_monitor' AND table_name='ddl_event_log'"
tunggu "skema Dukcapil (init.sql)" 180 docker exec datasources-mysql sh -c \
  'mysql -N -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SELECT 1/count(*) FROM information_schema.tables WHERE table_schema=\"dukcapil\" AND table_name=\"ddl_event_log\"" 2>/dev/null | grep -q 1'

echo "==== 4) Teiid, Ontop, Kafka/Debezium, dan ASCAM lewat jalankan.sh ===="
./jalankan.sh --uji-rantai
