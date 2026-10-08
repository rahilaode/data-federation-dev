#!/usr/bin/env bash
# Menyalakan lapisan OBDA blue-green (ADR-0022): proxy dan instance Ontop aktif dinyalakan;
# instance siaga hanya DIBUAT (tidak dinyalakan) agar agen ASCAM dapat menyalakannya nanti.
# Opsi diteruskan ke init-bluegreen.sh, mis. --reset untuk membangun ulang slot.
set -euo pipefail
cd "$(dirname "$0")"
if docker compose version >/dev/null 2>&1; then DC=(docker compose); else DC=(docker-compose); fi

./init-bluegreen.sh "$@"
AKTIF="$(python3 -c 'import json; print(json.load(open("slots/state.json"))["active"])')"
if [ "$AKTIF" = blue ]; then SIAGA=green; else SIAGA=blue; fi

"${DC[@]}" up -d --remove-orphans "ontop-$AKTIF" ontop-proxy
"${DC[@]}" up --no-start "ontop-$SIAGA"
docker stop "vkg-system-ontop-$SIAGA" >/dev/null 2>&1 || true
echo "  Ontop aktif: $AKTIF; siaga: $SIAGA (dibuat, tidak dinyalakan)"
