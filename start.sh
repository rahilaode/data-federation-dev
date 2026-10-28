#!/usr/bin/env bash
# start.sh — clean restart of the whole OBDF and ASCAM, in dependency order.
# WARNING: every container and volume is removed, source data included (rebuilt from init.sql).
# Works with docker-compose v1 and Compose v2: the --wait option is not used.
set -Eeuo pipefail
cd "$(dirname "$0")"
trap 'echo; echo "FAILED at step: $STEP"; exit 1' ERR

if command -v docker-compose >/dev/null 2>&1; then DC=(docker-compose); else DC=(docker compose); fi
compose() { "${DC[@]}" -f "$1" "${@:2}"; }
SECRETS=setup/ascam/knowledge/secrets
KNOWLEDGE=http://127.0.0.1:18000
CONNECT=http://localhost:8083
STEP=setup
step() { STEP="$1"; printf '\n==> %s\n' "$1"; }

wait_for() {  # $1 name, $2 limit in seconds, the rest is a command that must succeed
  local name="$1" limit="$2" i=0; shift 2
  until "$@" >/dev/null 2>&1; do
    i=$((i + 3)); [ "$i" -ge "$limit" ] && { echo "  $name not ready after $limit s"; return 1; }
    sleep 3
  done
  echo "  $name ready"
}
healthy() {  # every container of compose file $1 is healthy, running, or exited with code 0
  local id s ids
  ids="$(compose "$1" ps -q)"; [ -n "$ids" ] || return 1
  for id in $ids; do
    s="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}:{{.State.ExitCode}}{{end}}' "$id")"
    case "$s" in healthy|running:0|exited:0) ;; *) return 1 ;; esac
  done
}
up() {  # $1 compose file, $2 display name; build if needed, then wait until healthy
  compose "$1" up -d --build >/dev/null
  wait_for "$2" 300 healthy "$1"
}
teiid_active() {
  curl -sf --digest -u "admin:$(head -n1 "$SECRETS/obdf_teiid_mgmt_password")" -H 'Content-Type: application/json' \
    -d '{"operation":"list-vdbs","address":[{"subsystem":"teiid"}]}' http://localhost:19990/management \
    | python3 -c 'import json,sys; r=json.load(sys.stdin).get("result") or []; sys.exit(0 if any(v.get("vdb-name")=="government" and v.get("status")=="ACTIVE" for v in r) else 1)'
}
connector_running() {
  curl -sf "$CONNECT/connectors/$1/status" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d["tasks"] and all(t["state"]=="RUNNING" for t in d["tasks"]) else 1)'
}

step "1/9 prerequisites (Compose: ${DC[*]})"
docker info >/dev/null
docker network create ascam-networks >/dev/null 2>&1 || true
setup/ascam/knowledge/scripts/init-secrets.sh | sed 's/^/  /'

step "2/9 taking every component down, volumes included"
for d in ui executor orchestrator ontop-agent knowledge schema-monitor; do
  [ -f "setup/ascam/$d/docker-compose.yaml" ] && compose "setup/ascam/$d/docker-compose.yaml" down -v --remove-orphans >/dev/null 2>&1 || true
done
for d in vkg-system data-federation data-source; do
  compose "setup/$d/docker-compose.yaml" down -v --remove-orphans >/dev/null 2>&1 || true
done

step "3/9 restoring the mapping, the ontology, and the VDB deployment marker"
git checkout -- setup/vkg-system/config/mapping.ttl setup/vkg-system/config/ontology_file.ttl
DEP=setup/data-federation/deployments
rm -f "$DEP"/*.dodeploy "$DEP"/*.isdeploying "$DEP"/*.deployed "$DEP"/*.failed "$DEP"/*.undeployed "$DEP"/*.pending
touch "$DEP"/government-vdb.xml.dodeploy && chmod 777 "$DEP"
rm -rf setup/vkg-system/slots setup/vkg-system/proxy/runtime    # blue-green slots are recreated (ADR-0022)

step "4/9 data sources (PostgreSQL, MySQL)"
compose setup/data-source/docker-compose.yaml up -d >/dev/null
wait_for "PostgreSQL (init.sql)" 300 docker exec datasources-pgsql psql -U postgres -d kemensos -tAc \
  "SELECT 1 FROM schema_monitor.ddl_event_log LIMIT 1"
wait_for "MySQL (init.sql)" 300 docker exec datasources-mysql sh -c \
  'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "SELECT 1 FROM dukcapil.ddl_event_log LIMIT 1"'

step "5/9 Teiid and Ontop (proxy + active blue-green instance)"
compose setup/data-federation/docker-compose.yaml up -d >/dev/null
wait_for "VDB government (ACTIVE)" 300 teiid_active
setup/vkg-system/up.sh --reset | sed 's/^/  /'
wait_for "SPARQL endpoint (through the proxy)" 240 curl -sf -G http://localhost:8080/sparql --data-urlencode 'query=ASK { ?s ?p ?o }'

step "6/9 Kafka and Debezium"
compose setup/ascam/schema-monitor/docker-compose.yaml up -d >/dev/null
wait_for "Kafka Connect" 240 curl -sf "$CONNECT/connectors"
for k in postgres mysql; do
  curl -sf -X POST -H 'Content-Type: application/json' \
    --data @"setup/ascam/schema-monitor/register-$k.json" "$CONNECT/connectors" >/dev/null
  wait_for "$k-connector (RUNNING)" 120 connector_running "$k-connector"
done

step "7/9 Knowledge Service"
up setup/ascam/knowledge/docker-compose.yaml "database and Knowledge Service"
wait_for "Knowledge API" 120 curl -sf "$KNOWLEDGE/ready"

step "8/9 Ontop agent, Event Normalizer, Executor, console"
up setup/ascam/ontop-agent/docker-compose.yaml "Ontop agent"
export ASCAM_ORCH_GROUP_ID="ascam-orchestrator-$(date +%Y%m%dT%H%M%S)" ASCAM_ORCH_OFFSET_RESET=latest
up setup/ascam/orchestrator/docker-compose.yaml "Event Normalizer"
up setup/ascam/executor/docker-compose.yaml "Executor"
up setup/ascam/ui/docker-compose.yaml "administrator console"

step "9/9 initial sync and chain test"
TOKEN="$(grep '^ui:' "$SECRETS/knowledge_api_tokens" | cut -d: -f2)"
OBDF_ID="$(curl -sf -H "Authorization: Bearer $TOKEN" "$KNOWLEDGE/api/v1/obdf" \
  | python3 -c 'import json,sys; print(next(o["id"] for o in json.load(sys.stdin) if o["name"]=="bansos"))')"
curl -sf -X POST -H "Authorization: Bearer $TOKEN" -H 'X-ASCAM-User: start.sh' "$KNOWLEDGE/api/v1/obdf/$OBDF_ID/sync" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); c=d.get("counts") or {}; print("  version %s: %s tables, %s columns, %s TriplesMaps" % (d.get("version_no"), c.get("tables"), c.get("columns"), c.get("triples_maps")))'
python3 experiments/evaluation/preflight.py

echo; echo "Done. Console: http://127.0.0.1:18400 (admin; password in $SECRETS/ui_admin_password)"
