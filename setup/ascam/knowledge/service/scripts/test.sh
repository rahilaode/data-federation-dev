#!/usr/bin/env bash
# Menjalankan uji Knowledge terhadap PostgreSQL 16 SEMENTARA (bukan basis data Knowledge).
# Semua kontainer dan jaringan uji dihapus di akhir.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
TAG="ascam-knowledge-test-$$"
PASS="$(python3 -c 'import secrets; print(secrets.token_urlsafe(16))')"

cleanup() {
  docker rm -f "$TAG-db" >/dev/null 2>&1 || true
  docker network rm "$TAG-net" >/dev/null 2>&1 || true
}
trap cleanup EXIT

docker network create "$TAG-net" >/dev/null
docker run -d --name "$TAG-db" --network "$TAG-net" \
  -e POSTGRES_USER=ascam_owner -e POSTGRES_PASSWORD="$PASS" -e POSTGRES_DB=ascam_test \
  postgres:16-alpine >/dev/null
until docker exec "$TAG-db" pg_isready -U ascam_owner -d ascam_test >/dev/null 2>&1; do sleep 1; done
sleep 2   # image postgres me-restart server sekali setelah inisialisasi

docker run --rm --network "$TAG-net" -v "$HERE:/src:ro" \
  -e ASCAM_KNOWLEDGE_DB_URL="postgresql+psycopg://ascam_owner:$PASS@$TAG-db:5432/ascam_test" \
  python:3.12-slim sh -c '
    cp -r /src /work && cd /work &&
    pip install -q --root-user-action=ignore ".[test]" &&
    python -m pytest -q -rs "$@"
  ' -- "$@"
