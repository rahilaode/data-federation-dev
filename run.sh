#!/usr/bin/env bash
# Hentikan skrip segera bila ada perintah yang gagal (mis. build image),
# agar kegagalan tidak tertutup oleh langkah-langkah berikutnya.
set -e

# Jaringan bersama semua stack (abaikan bila sudah ada, agar aman dengan set -e)
docker network create ascam-networks >/dev/null 2>&1 || true

# data source
docker compose -f ./setup/data-source/docker-compose.yaml down -v
docker compose -f ./setup/data-source/docker-compose.yaml up -d

# data federation
# Siapkan folder deployment scanner: bersihkan marker lama, lalu minta
# deploy awal VDB lewat marker .dodeploy (scanner dalam manual mode untuk XML).
DEPLOY_DIR=./setup/data-federation/deployments
rm -f "$DEPLOY_DIR"/*.dodeploy "$DEPLOY_DIR"/*.isdeploying "$DEPLOY_DIR"/*.deployed \
      "$DEPLOY_DIR"/*.failed "$DEPLOY_DIR"/*.undeployed "$DEPLOY_DIR"/*.pending
touch "$DEPLOY_DIR"/government-vdb.xml.dodeploy
# Kontainer Teiid berjalan sebagai user jboss; folder harus dapat ditulisi
# agar scanner bisa membuat/menghapus marker (cukup untuk lingkungan riset).
chmod 777 "$DEPLOY_DIR"

docker compose -f ./setup/data-federation/docker-compose.yaml down -v
docker compose -f ./setup/data-federation/docker-compose.yaml up --build --detach

# tunggu VDB awal ter-deploy sebelum Ontop dinyalakan
echo "Menunggu VDB government ter-deploy..."
VDB_OK=0
for i in $(seq 1 60); do
  if [ -f "$DEPLOY_DIR"/government-vdb.xml.deployed ]; then VDB_OK=1; echo "VDB ter-deploy."; break; fi
  if [ -f "$DEPLOY_DIR"/government-vdb.xml.failed ]; then
    echo "Deploy VDB gagal:"; cat "$DEPLOY_DIR"/government-vdb.xml.failed; exit 1
  fi
  sleep 3
done
if [ "$VDB_OK" -ne 1 ]; then
  echo "VDB belum ter-deploy setelah 180 detik. Periksa: docker logs data-federation-teiid"
  exit 1
fi

# vkg-system
docker compose -f ./setup/vkg-system/docker-compose.yaml down -v
docker compose -f ./setup/vkg-system/docker-compose.yaml up --build --detach

# ascam schema monitor
docker compose -f ./setup/ascam/schema-monitor/docker-compose.yaml down -v
docker compose -f ./setup/ascam/schema-monitor/docker-compose.yaml up --build --detach

sleep 40

# register postgres connector with ascam schema monitor
curl -X POST -H "Content-Type: application/json" \
     --data @./setup/ascam/schema-monitor/register-postgres.json \
     http://localhost:8083/connectors

# register mysql connector with ascam schema monitor
curl -X POST -H "Content-Type: application/json" \
     --data @./setup/ascam/schema-monitor/register-mysql.json \
     http://localhost:8083/connectors


# ascam adaptive engine (iterasi 1) TIDAK dijalankan lagi: mesin tersebut mengubah mapping
# .obda, sedangkan ℳ kini berupa R2RML (ADR-0005, ADR-0010). Penggantinya (Orchestrator dan
# Executor) sedang dibangun; lihat setup/ascam/adaptive-engine/README.md.
