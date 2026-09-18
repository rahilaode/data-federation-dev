#!/usr/bin/env bash
# Verifikasi rantai Monitor -> Kafka -> Orchestrator -> Knowledge (F3b, ADR-0016/0017).
#
# Bagian 4 melakukan DDL NYATA pada tabel studi kasus (menambah lalu menghapus kolom
# `email` pada kemensos.penerima_manfaat) untuk menguji alur ujung ke ujung; kolom tersebut
# dikembalikan pada bagian 5. Tidak ada artefak OBDF yang diubah: Executor belum ada.
#
# Pemakaian (dari root repository):
#   experiments/f3/verify_orchestrator.sh            # lengkap
#   experiments/f3/verify_orchestrator.sh --no-ddl   # tanpa DDL nyata
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT" || exit 1
K=setup/ascam/knowledge
API=http://127.0.0.1:18000/api/v1
ORCH=http://127.0.0.1:18200
DDL=1
[ "${1:-}" = "--no-ddl" ] && DDL=0

TOKEN="$(grep '^ui:' "$K/secrets/knowledge_api_tokens" | cut -d: -f2)"
AUTH="Authorization: Bearer $TOKEN"

judul() { printf '\n===== %s =====\n' "$1"; }

judul "1) uji otomatis"
"$K/service/scripts/test.sh" 2>&1 | tail -1
docker run --rm -v "$ROOT/setup/ascam/orchestrator/service:/src:ro" python:3.12-slim sh -c \
  'cp -r /src /w && cd /w && pip install -q --root-user-action=ignore ".[test]" && python -m pytest -q' 2>&1 | tail -1

judul "2) jalankan Knowledge dan Orchestrator"
docker compose -f "$K/docker-compose.yaml" up -d --build --wait 2>&1 | tail -2
docker compose -f setup/ascam/orchestrator/docker-compose.yaml up -d --build --wait 2>&1 | tail -2
sleep 5
curl -s "$ORCH/health" | python3 -m json.tool
echo "-- log terakhir Orchestrator"
docker logs ascam-orchestrator 2>&1 | tail -5

judul "3) event yang sudah diproses"
curl -s -H "$AUTH" "$API/obdf/1/events?limit=10" | python3 -c '
import json, sys
rows = json.load(sys.stdin)
if not rows:
    print("  belum ada event")
for e in rows:
    s = e["structured"] or {}
    alasan = e["ignore_reason"] or ""
    print(f"  event {e[\"id\"]:3d} {e[\"status\"]:8s} {str(s.get(\"operation\")):7s} "
          f"{s.get(\"schema\")}.{s.get(\"table\")}.{s.get(\"column\")} {alasan}")'

rencana() {
  curl -s -H "$AUTH" "$API/obdf/1/plans?limit=${1:-3}" | python3 -c '
import json, sys
rows = json.load(sys.stdin)
if not rows:
    print("  belum ada rencana")
for p in rows:
    print(f"  rencana {p[\"id\"]}: {p[\"decision\"]}/{p[\"status\"]} ({p[\"pattern\"]})")
    if p["reasons"]:
        print(f"      alasan: {p[\"reasons\"]}")
    for a in p["actions"]:
        print(f"      {a[\"seq\"]}. {a[\"artifact\"]:8s} {a[\"operation\"]:28s} {a[\"params\"]}")'
}

statistik() {
  curl -s "$ORCH/health" | python3 -c '
import json, sys
d = json.load(sys.stdin)
kunci = ("state", "messages", "events_sent", "events_planned", "events_ignored", "duplicates", "failures")
print("  statistik:", {k: d[k] for k in kunci}, "| galat terakhir:", d["last_error"])'
}

if [ "$DDL" = "1" ]; then
  judul "4) uji ujung ke ujung: ADD COLUMN pada tabel yang difederasikan"
  docker exec datasources-pgsql psql -U postgres -d kemensos -q -c \
    "ALTER TABLE public.penerima_manfaat ADD COLUMN email VARCHAR(100);"
  echo "  DDL dijalankan; menunggu pemrosesan..."
  sleep 12
  rencana 2
  statistik

  judul "5) kembalikan kondisi sumber: DROP COLUMN"
  docker exec datasources-pgsql psql -U postgres -d kemensos -q -c \
    "ALTER TABLE public.penerima_manfaat DROP COLUMN email;"
  sleep 12
  rencana 2
  statistik
else
  judul "4) rencana terkini (tanpa DDL)"
  rencana 5
  statistik
fi

judul "6) ringkasan"
echo "  kolom email pada sumber:"
docker exec datasources-pgsql psql -U postgres -d kemensos -tAc \
  "SELECT count(*) FROM information_schema.columns WHERE table_name='penerima_manfaat' AND column_name='email'"
echo "  (0 = sudah dikembalikan seperti semula)"
git -C "$ROOT" status --short
