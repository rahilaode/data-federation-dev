#!/usr/bin/env bash
# Menghentikan seluruh stack eksperimen dan menghapus volume-nya.
# Urutan dibalik dari run.sh: ASCAM dihentikan lebih dulu agar tidak
# bereaksi terhadap komponen lain yang sedang dimatikan.
cd "$(dirname "$0")"

docker compose -f ./setup/ascam/adaptive-engine/docker-compose.yaml down -v
docker compose -f ./setup/ascam/schema-monitor/docker-compose.yaml down -v
docker compose -f ./setup/vkg-system/docker-compose.yaml down -v
docker compose -f ./setup/data-federation/docker-compose.yaml down -v
docker compose -f ./setup/data-source/docker-compose.yaml down -v
