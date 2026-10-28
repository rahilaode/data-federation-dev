#!/usr/bin/env bash
# Starts the blue-green OBDA layer (ADR-0022): the proxy and the active Ontop instance are started;
# the standby instance is only CREATED (not started), so that the ASCAM agent can start it later.
# Options are passed on to init-bluegreen.sh, e.g. --reset to rebuild the slots.
set -euo pipefail
cd "$(dirname "$0")"
if docker compose version >/dev/null 2>&1; then DC=(docker compose); else DC=(docker-compose); fi

./init-bluegreen.sh "$@"
ACTIVE="$(python3 -c 'import json; print(json.load(open("slots/state.json"))["active"])')"
if [ "$ACTIVE" = blue ]; then STANDBY=green; else STANDBY=blue; fi

"${DC[@]}" up -d --remove-orphans "ontop-$ACTIVE" ontop-proxy
"${DC[@]}" up --no-start "ontop-$STANDBY"
docker stop "vkg-system-ontop-$STANDBY" >/dev/null 2>&1 || true
echo "  active Ontop: $ACTIVE; standby: $STANDBY (created, not started)"
