#!/usr/bin/env bash
# Membuat berkas rahasia Knowledge (sekali saja). Berkas tidak di-commit.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)/secrets"
mkdir -p "$DIR"
chmod 700 "$DIR"
for name in knowledge_db_owner_password knowledge_db_app_password; do
  if [ ! -s "$DIR/$name" ]; then
    python3 -c "import secrets; print(secrets.token_urlsafe(32))" > "$DIR/$name"
    chmod 644 "$DIR/$name"   # dibaca proses non-root di dalam kontainer
    echo "dibuat: secrets/$name"
  fi
done
