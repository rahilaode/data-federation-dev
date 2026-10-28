#!/usr/bin/env bash
# =============================================================================
# restart-ascam.sh — brings up the OBDF and every ASCAM component, with ASCAM
# in a clean state.
#
#   OBDF (PostgreSQL, MySQL, Teiid, Ontop)   NOT changed: only started when it
#                                            is down. Source data, VDB, mapping,
#                                            and ontology are left as they are.
#   Kafka and Debezium                       Started when down; the connectors
#                                            are registered when missing.
#   ASCAM (Knowledge, Ontop Agent,           RESET every time: the Knowledge
#   Orchestrator, Executor, console)         database is deleted and rebuilt,
#                                            then synced from the running
#                                            OBDF.
#
# The Orchestrator uses a new Kafka consumer group that starts from the LATEST
# message, so old DDL in the topics is not treated as a new change by the
# freshly reset Knowledge.
#
# Usage (from the repository root):
#   ./restart-ascam.sh                    start and reset ASCAM
#   ./restart-ascam.sh --chain-test       + event chain test with a test DDL on
#                                         the source (creates, then drops,
#                                         the ascam_probe table)
#   ./restart-ascam.sh --executor-paused  start the Executor paused
#
# To rebuild the lab from scratch (this DELETES the source data), use start.sh.
# =============================================================================
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

CHAIN_TEST=0
EXECUTOR_PAUSED=0
for arg in "$@"; do
  case "$arg" in
    --chain-test) CHAIN_TEST=1 ;;
    --executor-paused) EXECUTOR_PAUSED=1 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (see --help)"; exit 2 ;;
  esac
done

SECRETS=setup/ascam/knowledge/secrets
KNOWLEDGE=http://127.0.0.1:18000
UI=http://127.0.0.1:18400
TEIID_MGMT=http://localhost:19990/management
SPARQL=http://localhost:8080/sparql
CONNECT=http://localhost:8083
CURRENT_STEP="setup"

trap 'echo; echo "FAILED at step: $CURRENT_STEP"; echo "Check the logs of the container involved with: docker logs <container-name>"; exit 1' ERR

heading() { CURRENT_STEP="$1"; printf '\n==== %s ====\n' "$1"; }
info() { printf '  %s\n' "$*"; }
# The lab uses `docker-compose` (a separate binary); `docker compose` (the plugin) is only used
# when that binary is missing. `--wait` only exists in Compose v2, so it is checked first.
if command -v docker-compose >/dev/null 2>&1; then COMPOSE=(docker-compose); else COMPOSE=(docker compose); fi
compose() { "${COMPOSE[@]}" -f "$1" "${@:2}"; }
if "${COMPOSE[@]}" up --help 2>/dev/null | grep -q -- '--wait'; then WAIT=(--wait); else WAIT=(); fi

wait_for() {  # $1 description, $2 limit in seconds, the rest is a command that must succeed
  local name="$1" limit="$2" i=0
  shift 2
  until "$@" >/dev/null 2>&1; do
    i=$((i + 2))
    if [ "$i" -ge "$limit" ]; then
      echo "  $name not ready after ${limit} seconds"
      return 1
    fi
    sleep 2
  done
  info "$name ready"
}

teiid_vdb_active() {
  local password
  password="$(head -n1 "$SECRETS/obdf_teiid_mgmt_password")"
  curl -sf --digest -u "admin:$password" -H 'Content-Type: application/json' \
    -d '{"operation":"list-vdbs","address":[{"subsystem":"teiid"}]}' "$TEIID_MGMT" \
    | python3 -c 'import json,sys; r=json.load(sys.stdin).get("result") or []; sys.exit(0 if any(v.get("vdb-name")=="government" and v.get("status")=="ACTIVE" for v in r) else 1)'
}
sparql_ready() { curl -sf -G "$SPARQL" --data-urlencode 'query=ASK { ?s ?p ?o }' -H 'Accept: application/sparql-results+json'; }
connect_ready() { curl -sf "$CONNECT/connectors"; }
knowledge_ready() { curl -sf "$KNOWLEDGE/ready"; }
container_ready() {  # healthy when the container has a healthcheck, running when it does not
  local status
  status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$1" 2>/dev/null)"
  [ "$status" = healthy ] || [ "$status" = running ]
}
error_logs() { for k in "$@"; do echo "  --- docker logs $k (last 40 lines)"; docker logs --tail 40 "$k" 2>&1 | sed 's/^/    /'; done; }
# The output is stored first and matched afterwards: `docker ps | grep -q` under pipefail can
# fail spuriously, because docker gets SIGPIPE when grep -q exits early.
container_exists() { local all; all="$(docker ps -a --format '{{.Names}}')"; grep -qx "$1" <<< "$all"; }

# ── 0. prerequisites ──────────────────────────────────────────────────────────
heading "0) prerequisites"
docker info >/dev/null 2>&1 || { echo "  Docker is not running"; exit 1; }
docker network create ascam-networks >/dev/null 2>&1 || true
info "network ascam-networks available"
setup/ascam/knowledge/scripts/init-secrets.sh | sed 's/^/  /'
if container_exists ascam-adaptive-engine; then
  docker rm -f ascam-adaptive-engine >/dev/null
  info "iteration-1 engine (ascam-adaptive-engine) removed; it is no longer used"
fi

# ── 1. OBDF: started when down, never changed ─────────────────────────────────
heading "1) OBDF: data sources, Teiid, Ontop (no change to data or configuration)"
compose setup/data-source/docker-compose.yaml up -d 2>&1 | sed 's/^/  /'

NEW_TEIID=0
container_exists data-federation-teiid || NEW_TEIID=1
if [ "$NEW_TEIID" = "1" ]; then
  # The Teiid container has never been created: the initial VDB must be deployed through a
  # marker, exactly as in start.sh. The VDB file itself is not changed.
  DEPLOY_DIR=setup/data-federation/deployments
  rm -f "$DEPLOY_DIR"/*.dodeploy "$DEPLOY_DIR"/*.isdeploying "$DEPLOY_DIR"/*.failed "$DEPLOY_DIR"/*.pending
  touch "$DEPLOY_DIR"/government-vdb.xml.dodeploy
  chmod 777 "$DEPLOY_DIR"
  info "new Teiid container: the initial VDB will be deployed"
fi
compose setup/data-federation/docker-compose.yaml up -d 2>&1 | sed 's/^/  /'
wait_for "VDB government (ACTIVE)" 240 teiid_vdb_active

# Blue-green OBDA layer (ADR-0022): existing slots are kept, so the instance that became active
# after the last adaptation keeps serving.
setup/vkg-system/up.sh 2>&1 | sed 's/^/  /'
wait_for "Ontop SPARQL endpoint (through the proxy)" 180 sparql_ready

# ── 2. Kafka and Debezium ─────────────────────────────────────────────────────
heading "2) Kafka and Debezium"
compose setup/ascam/schema-monitor/docker-compose.yaml up -d 2>&1 | sed 's/^/  /'
wait_for "Kafka Connect" 180 connect_ready
REGISTERED="$(curl -sf "$CONNECT/connectors" || echo '[]')"
for pair in "postgres-connector:setup/ascam/schema-monitor/register-postgres.json" \
            "mysql-connector:setup/ascam/schema-monitor/register-mysql.json"; do
  connector="${pair%%:*}"
  file="${pair#*:}"
  if grep -q "\"$connector\"" <<< "$REGISTERED"; then
    info "$connector already registered"
  else
    curl -sf -X POST -H 'Content-Type: application/json' --data @"$file" "$CONNECT/connectors" >/dev/null
    info "$connector registered"
  fi
done
sleep 3
for connector in postgres-connector mysql-connector; do
  curl -sf "$CONNECT/connectors/$connector/status" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("  %-18s connector=%s tasks=%s" % (d["name"], d["connector"]["state"], [t["state"] for t in d.get("tasks", [])]))'
done

# ── 3. ASCAM: full reset ──────────────────────────────────────────────────────
heading "3) ASCAM: reset Knowledge and the services"
for service in ui executor orchestrator ontop-agent; do
  compose "setup/ascam/$service/docker-compose.yaml" down --remove-orphans >/dev/null 2>&1 || true
done
compose setup/ascam/knowledge/docker-compose.yaml down -v --remove-orphans >/dev/null 2>&1 || true
info "ASCAM services stopped; Knowledge database deleted"

compose setup/ascam/knowledge/docker-compose.yaml up -d --build ${WAIT[@]+"${WAIT[@]}"} 2>&1 | tail -n 3 | sed 's/^/  /'
wait_for "Knowledge Service" 120 knowledge_ready || { error_logs ascam-knowledge-migrate ascam-knowledge-service; false; }
docker logs ascam-knowledge-migrate 2>&1 | tail -n 1 | sed 's/^/  migration: /'
docker logs ascam-knowledge-service 2>&1 | grep bootstrap | tail -n 1 | sed 's/^/  /' || true

compose setup/ascam/ontop-agent/docker-compose.yaml up -d --build ${WAIT[@]+"${WAIT[@]}"} 2>&1 | tail -n 1 | sed 's/^/  /'

GENERATION="$(date +%Y%m%dT%H%M%S)"
ASCAM_ORCH_GROUP_ID="ascam-orchestrator-$GENERATION" ASCAM_ORCH_OFFSET_RESET=latest \
  compose setup/ascam/orchestrator/docker-compose.yaml up -d --build ${WAIT[@]+"${WAIT[@]}"} 2>&1 | tail -n 1 | sed 's/^/  /'
info "Orchestrator: consumer group ascam-orchestrator-$GENERATION, starting from the latest message"

compose setup/ascam/executor/docker-compose.yaml up -d --build ${WAIT[@]+"${WAIT[@]}"} 2>&1 | tail -n 1 | sed 's/^/  /'
compose setup/ascam/ui/docker-compose.yaml up -d --build ${WAIT[@]+"${WAIT[@]}"} 2>&1 | tail -n 1 | sed 's/^/  /'
# without --wait (Compose v1) readiness is read from the healthcheck status of each container
for k in ascam-ontop-agent ascam-orchestrator ascam-executor ascam-ui; do
  wait_for "$k" 120 container_ready "$k" || { error_logs "$k"; false; }
done

# ── 4. Knowledge: initial sync from the running OBDF ──────────────────────────
heading "4) initial Knowledge sync"
TOKEN="$(grep '^ui:' "$SECRETS/knowledge_api_tokens" | cut -d: -f2)"
OBDF_ID="$(curl -sf -H "Authorization: Bearer $TOKEN" "$KNOWLEDGE/api/v1/obdf" \
  | python3 -c 'import json,sys; print(next(o["id"] for o in json.load(sys.stdin) if o["name"]=="bansos"))')"
curl -sf -X POST -H "Authorization: Bearer $TOKEN" -H 'X-ASCAM-User: restart-ascam.sh' \
  "$KNOWLEDGE/api/v1/obdf/$OBDF_ID/sync" | python3 -c '
import json, sys
d = json.load(sys.stdin)
c = d.get("counts") or {}
print("  specification version %s | %s tables, %s columns, %s TriplesMaps, %s ontology entities, %s lineage"
      % (d.get("version_no"), c.get("tables"), c.get("columns"), c.get("triples_maps"),
         c.get("entities"), c.get("column_usages")))
issues = d.get("issues") or []
errors = [m for m in issues if m.get("severity") == "error"]
print("  consistency issues: %d (%d errors)" % (len(issues), len(errors)))
for m in errors[:5]:
    print("    ERROR %s: %s" % (m["code"], m["subject_ref"]))'

if [ "$EXECUTOR_PAUSED" = "1" ]; then
  curl -sf -X POST http://127.0.0.1:18300/control/pause >/dev/null
  info "Executor paused; resume it from the console when ready"
fi

# ── 5. checks ─────────────────────────────────────────────────────────────────
heading "5) checks"
# A failed check does not stop the start-up: the services stay up and the problem is reported
# in the summary.
WARNING=0
if [ "$CHAIN_TEST" = "1" ]; then
  python3 experiments/evaluation/preflight.py || WARNING=1
else
  python3 experiments/evaluation/preflight.py --no-chain || WARNING=1
fi

# ── summary ───────────────────────────────────────────────────────────────────
CURRENT_STEP="summary"
cat <<EOF

==== ready ====
  Administrator console : $UI   (user: admin)
  Console password      : $SECRETS/ui_admin_password
  Knowledge API         : $KNOWLEDGE
  SPARQL endpoint       : $SPARQL
  Kafka UI              : http://localhost:8081

  ASCAM starts from a clean state. The OBDF and the source data are unchanged.
EOF
if [ "$WARNING" = "1" ]; then
  echo
  echo "  ATTENTION: the checks found a problem (see section 5). The services are still up."
  exit 1
fi
