#!/usr/bin/env bash
# F0.8 — uji kelayakan ALTER VIEW pada Teiid 16 (lihat f0_8_teiid_alter_view.py).
# Dijalankan dari host saat Teiid menyala: ./experiments/f0/f0_8.sh
# Hanya membuat dan menghapus VDB uji f0av; VDB government dan data sumber tidak disentuh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SECRETS="$ROOT/setup/ascam/knowledge/secrets"
mkdir -p "$ROOT/results/f0"
docker run --rm --network ascam-networks \
  -e MGMT_PASSWORD="$(head -n1 "$SECRETS/obdf_teiid_mgmt_password")" \
  -e TEIID_PASSWORD="$(head -n1 "$SECRETS/obdf_teiid_user_password")" \
  -v "$ROOT/experiments/f0:/f0:ro" \
  -v "$ROOT/setup/ascam/knowledge/service/src:/knowledge:ro" \
  -v "$ROOT/results/f0:/out" \
  python:3.12-slim sh -c \
  'pip install -q --root-user-action=ignore "psycopg[binary]>=3.1,<4" requests "sqlglot>=25" >/dev/null 2>&1 && python /f0/f0_8_teiid_alter_view.py'
