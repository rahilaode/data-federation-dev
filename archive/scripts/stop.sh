#!/usr/bin/env bash
# Menghentikan seluruh stack eksperimen dan menghapus volume-nya.
# Urutan dibalik dari run.sh: ASCAM dihentikan lebih dulu agar tidak
# bereaksi terhadap komponen lain yang sedang dimatikan.
cd "$(dirname "$0")"
if command -v docker-compose >/dev/null 2>&1; then DC=(docker-compose); else DC=(docker compose); fi

"${DC[@]}" -f ./setup/ascam/adaptive-engine/docker-compose.yaml down -v
"${DC[@]}" -f ./setup/ascam/schema-monitor/docker-compose.yaml down -v
"${DC[@]}" -f ./setup/vkg-system/docker-compose.yaml down -v
"${DC[@]}" -f ./setup/data-federation/docker-compose.yaml down -v
"${DC[@]}" -f ./setup/data-source/docker-compose.yaml down -v
