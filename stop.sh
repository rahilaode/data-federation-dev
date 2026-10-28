#!/usr/bin/env bash
# stop.sh — stops ASCAM and the OBDF in reverse dependency order.
# ASCAM goes first, so that it does not react to components that are shutting down.
#
#   ./stop.sh             stop every container; volumes (source data, Knowledge) are kept
#   ./stop.sh --volumes   also delete the volumes; the next start must be ./start.sh
set -uo pipefail
cd "$(dirname "$0")"

DOWN=(down --remove-orphans)
case "${1:-}" in
  --volumes) DOWN+=(-v) ;;
  "") ;;
  *) echo "unknown option: $1 (use --volumes or nothing)"; exit 2 ;;
esac

if command -v docker-compose >/dev/null 2>&1; then DC=(docker-compose); else DC=(docker compose); fi
for f in setup/ascam/ui setup/ascam/executor setup/ascam/orchestrator setup/ascam/ontop-agent \
         setup/ascam/knowledge setup/ascam/schema-monitor setup/vkg-system setup/data-federation \
         setup/data-source; do
  echo "==> $f"
  "${DC[@]}" -f "$f/docker-compose.yaml" "${DOWN[@]}" 2>&1 | sed 's/^/  /'
done
