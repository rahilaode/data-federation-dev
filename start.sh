#!/usr/bin/env bash
# start.sh — restart bersih seluruh OBDF dan ASCAM, berurutan sesuai dependensi.
# PERHATIAN: semua kontainer dan volume dihapus, termasuk data sumber (dibangun ulang dari init.sql).
# Bekerja dengan docker-compose v1 maupun Compose v2: tidak memakai opsi --wait.
set -Eeuo pipefail
cd "$(dirname "$0")"
trap 'echo; echo "GAGAL pada langkah: $LANGKAH"; exit 1' ERR

if docker compose version >/dev/null 2>&1; then DC=(docker compose); else DC=(docker-compose); fi
compose() { "${DC[@]}" -f "$1" "${@:2}"; }
SECRETS=setup/ascam/knowledge/secrets
KNOWLEDGE=http://127.0.0.1:18000
CONNECT=http://localhost:8083
LANGKAH=persiapan
langkah() { LANGKAH="$1"; printf '\n==> %s\n' "$1"; }

tunggu() {  # $1 nama, $2 batas detik, sisanya perintah yang harus berhasil
  local nama="$1" batas="$2" i=0; shift 2
  until "$@" >/dev/null 2>&1; do
    i=$((i + 3)); [ "$i" -ge "$batas" ] && { echo "  $nama belum siap setelah $batas s"; return 1; }
    sleep 3
  done
  echo "  $nama siap"
}
sehat() {  # semua kontainer pada berkas compose $1: healthy, berjalan, atau selesai dengan kode 0
  local id s ids
  ids="$(compose "$1" ps -q)"; [ -n "$ids" ] || return 1
  for id in $ids; do
    s="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}:{{.State.ExitCode}}{{end}}' "$id")"
    case "$s" in healthy|running:0|exited:0) ;; *) return 1 ;; esac
  done
}
naik() {  # $1 berkas compose, $2 nama tampilan; build bila perlu, lalu tunggu sehat
  compose "$1" up -d --build >/dev/null
  tunggu "$2" 300 sehat "$1"
}
teiid_aktif() {
  curl -sf --digest -u "admin:$(head -n1 "$SECRETS/obdf_teiid_mgmt_password")" -H 'Content-Type: application/json' \
    -d '{"operation":"list-vdbs","address":[{"subsystem":"teiid"}]}' http://localhost:19990/management \
    | python3 -c 'import json,sys; r=json.load(sys.stdin).get("result") or []; sys.exit(0 if any(v.get("vdb-name")=="government" and v.get("status")=="ACTIVE" for v in r) else 1)'
}
konektor_jalan() {
  curl -sf "$CONNECT/connectors/$1/status" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d["tasks"] and all(t["state"]=="RUNNING" for t in d["tasks"]) else 1)'
}

langkah "1/9 prasyarat (Compose: ${DC[*]})"
docker info >/dev/null
docker network create ascam-networks >/dev/null 2>&1 || true
setup/ascam/knowledge/scripts/init-secrets.sh | sed 's/^/  /'

langkah "2/9 menurunkan semua komponen beserta volumenya"
for d in ui executor orchestrator ontop-agent knowledge adaptive-engine schema-monitor; do
  [ -f "setup/ascam/$d/docker-compose.yaml" ] && compose "setup/ascam/$d/docker-compose.yaml" down -v --remove-orphans >/dev/null 2>&1 || true
done
for d in vkg-system data-federation data-source; do
  compose "setup/$d/docker-compose.yaml" down -v --remove-orphans >/dev/null 2>&1 || true
done

langkah "3/9 mengembalikan mapping, ontologi, dan penanda deployment VDB"
git checkout -- setup/vkg-system/config/mapping.ttl setup/vkg-system/config/ontology_file.ttl
DEP=setup/data-federation/deployments
rm -f "$DEP"/*.dodeploy "$DEP"/*.isdeploying "$DEP"/*.deployed "$DEP"/*.failed "$DEP"/*.undeployed "$DEP"/*.pending
touch "$DEP"/government-vdb.xml.dodeploy && chmod 777 "$DEP"

langkah "4/9 sumber data (PostgreSQL, MySQL)"
compose setup/data-source/docker-compose.yaml up -d >/dev/null
tunggu "PostgreSQL (init.sql)" 300 docker exec datasources-pgsql psql -U postgres -d kemensos -tAc \
  "SELECT 1 FROM schema_monitor.ddl_event_log LIMIT 1"
tunggu "MySQL (init.sql)" 300 docker exec datasources-mysql sh -c \
  'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SELECT 1 FROM dukcapil.ddl_event_log LIMIT 1"'

langkah "5/9 Teiid dan Ontop"
compose setup/data-federation/docker-compose.yaml up -d >/dev/null
tunggu "VDB government (ACTIVE)" 300 teiid_aktif
compose setup/vkg-system/docker-compose.yaml up -d >/dev/null
tunggu "endpoint SPARQL" 240 curl -sf -G http://localhost:8080/sparql --data-urlencode 'query=ASK { ?s ?p ?o }'

langkah "6/9 Kafka dan Debezium"
compose setup/ascam/schema-monitor/docker-compose.yaml up -d >/dev/null
tunggu "Kafka Connect" 240 curl -sf "$CONNECT/connectors"
for k in postgres mysql; do
  curl -sf -X POST -H 'Content-Type: application/json' \
    --data @"setup/ascam/schema-monitor/register-$k.json" "$CONNECT/connectors" >/dev/null
  tunggu "$k-connector (RUNNING)" 120 konektor_jalan "$k-connector"
done

langkah "7/9 Knowledge Service"
naik setup/ascam/knowledge/docker-compose.yaml "basis data dan Knowledge Service"
tunggu "API Knowledge" 120 curl -sf "$KNOWLEDGE/ready"

langkah "8/9 agen Ontop, Event Normalizer, Executor, konsol"
naik setup/ascam/ontop-agent/docker-compose.yaml "agen Ontop"
export ASCAM_ORCH_GROUP_ID="ascam-orchestrator-$(date +%Y%m%dT%H%M%S)" ASCAM_ORCH_OFFSET_RESET=latest
naik setup/ascam/orchestrator/docker-compose.yaml "Event Normalizer"
naik setup/ascam/executor/docker-compose.yaml "Executor"
naik setup/ascam/ui/docker-compose.yaml "konsol administrator"

langkah "9/9 sinkronisasi awal dan uji rantai"
TOKEN="$(grep '^ui:' "$SECRETS/knowledge_api_tokens" | cut -d: -f2)"
OBDF_ID="$(curl -sf -H "Authorization: Bearer $TOKEN" "$KNOWLEDGE/api/v1/obdf" \
  | python3 -c 'import json,sys; print(next(o["id"] for o in json.load(sys.stdin) if o["name"]=="bansos"))')"
curl -sf -X POST -H "Authorization: Bearer $TOKEN" -H 'X-ASCAM-User: start.sh' "$KNOWLEDGE/api/v1/obdf/$OBDF_ID/sync" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); c=d.get("counts") or {}; print("  versi %s: %s tabel, %s kolom, %s TriplesMap" % (d.get("version_no"), c.get("tables"), c.get("columns"), c.get("triples_maps")))'
python3 experiments/f6/preflight.py

echo; echo "Selesai. Konsol: http://127.0.0.1:18400 (admin; sandi di $SECRETS/ui_admin_password)"
