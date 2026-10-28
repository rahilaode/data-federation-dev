#!/usr/bin/env bash
# Verifikasi rantai Monitor -> Kafka -> Orchestrator -> Knowledge (F3b, ADR-0016/0017).
#
# Tahap 0 memastikan prasyarat berjalan: stack schema-monitor (Kafka, Kafka Connect) dan
# konektor Debezium untuk kedua sumber. Tanpa konektor, DDL tidak pernah sampai ke Kafka
# sehingga seluruh rantai tampak "diam" tanpa galat.
#
# Bagian 4-5 melakukan DDL NYATA pada kemensos.penerima_manfaat (menambah lalu menghapus
# kolom `email`) untuk menguji alur ujung ke ujung. Artefak OBDF tidak diubah: Executor
# belum ada.
#
# Pemakaian (dari root repository):
#   experiments/f3/verify_orchestrator.sh            # lengkap
#   experiments/f3/verify_orchestrator.sh --no-ddl   # tanpa DDL nyata
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT" || exit 1
K=setup/ascam/knowledge
SM=setup/ascam/schema-monitor
API=http://127.0.0.1:18000/api/v1
ORCH=http://127.0.0.1:18200
CONNECT=http://localhost:8083
DDL=1
[ "${1:-}" = "--no-ddl" ] && DDL=0

TOKEN="$(grep '^ui:' "$K/secrets/knowledge_api_tokens" | cut -d: -f2)"
AUTH="Authorization: Bearer $TOKEN"

judul() { printf '\n===== %s =====\n' "$1"; }

# ── tahap 0: prasyarat ─────────────────────────────────────────────────────────
judul "0) prasyarat: Kafka, Kafka Connect, dan konektor Debezium"
for nama in ascam-sm-kafka ascam-sm-connector datasources-pgsql datasources-mysql; do
  if ! docker ps --format '{{.Names}}' | grep -qx "$nama"; then
    echo "  $nama tidak berjalan -> menghidupkan stack schema-monitor"
    docker compose -f "$SM/docker-compose.yaml" up -d 2>&1 | tail -2
    break
  fi
done

echo "  menunggu Kafka Connect siap..."
for _ in $(seq 1 60); do
  curl -sf "$CONNECT/connectors" >/dev/null 2>&1 && break
  sleep 2
done
TERDAFTAR="$(curl -sf "$CONNECT/connectors" || echo '[]')"
for pasangan in "postgres-connector:$SM/register-postgres.json" "mysql-connector:$SM/register-mysql.json"; do
  konektor="${pasangan%%:*}"
  berkas="${pasangan#*:}"
  if ! echo "$TERDAFTAR" | grep -q "\"$konektor\""; then
    echo "  $konektor belum terdaftar -> mendaftarkan dari $berkas"
    curl -s -X POST -H 'Accept:application/json' -H 'Content-Type:application/json' \
      --data @"$berkas" "$CONNECT/connectors" -o /dev/null -w "    HTTP %{http_code}\n"
    sleep 5
  fi
done
for konektor in postgres-connector mysql-connector; do
  curl -s "$CONNECT/connectors/$konektor/status" | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("    status tidak terbaca"); raise SystemExit
tugas = [t.get("state") for t in d.get("tasks", [])]
print("    %-18s konektor=%s tugas=%s" % (d.get("name"), d.get("connector", {}).get("state"), tugas))
for t in d.get("tasks", []):
    if t.get("trace"):
        print("      galat:", t["trace"].splitlines()[0][:160])'
done

# ── tahap 1: uji otomatis ──────────────────────────────────────────────────────
judul "1) uji otomatis"
"$K/service/scripts/test.sh" 2>&1 | tail -1
docker run --rm -v "$ROOT/setup/ascam/orchestrator/service:/src:ro" python:3.12-slim sh -c \
  'cp -r /src /w && cd /w && pip install -q --root-user-action=ignore ".[test]" && python -m pytest -q' 2>&1 | tail -1

# ── tahap 2: layanan ASCAM ─────────────────────────────────────────────────────
judul "2) jalankan Knowledge dan Orchestrator"
docker compose -f "$K/docker-compose.yaml" up -d --build --wait 2>&1 | tail -2
docker compose -f setup/ascam/orchestrator/docker-compose.yaml up -d --build --wait 2>&1 | tail -2
sleep 5
curl -s "$ORCH/health" | python3 -m json.tool

daftar_event() {
  curl -s -H "$AUTH" "$API/obdf/1/events?limit=${1:-10}" | python3 -c '
import json, sys
rows = json.load(sys.stdin)
if not rows:
    print("  belum ada event")
for e in rows:
    s = e["structured"] or {}
    objek = "%s.%s.%s" % (s.get("schema"), s.get("table"), s.get("column"))
    print("  event %3d %-8s %-7s %-45s %s" % (e["id"], e["status"], str(s.get("operation")),
                                              objek, e["ignore_reason"] or ""))'
}

daftar_rencana() {
  curl -s -H "$AUTH" "$API/obdf/1/plans?limit=${1:-3}" | python3 -c '
import json, sys
rows = json.load(sys.stdin)
if not rows:
    print("  belum ada rencana")
for p in rows:
    print("  rencana %d: %s/%s (%s)" % (p["id"], p["decision"], p["status"], p["pattern"]))
    if p["reasons"]:
        print("      alasan: %s" % (p["reasons"],))
    for a in p["actions"]:
        print("      %d. %-8s %-28s %s" % (a["seq"], a["artifact"], a["operation"], a["params"]))'
}

statistik() {
  curl -s "$ORCH/health" | python3 -c '
import json, sys
d = json.load(sys.stdin)
kunci = ("state", "messages", "events_sent", "events_planned", "events_ignored",
         "duplicates", "failures")
print("  statistik:", {k: d[k] for k in kunci}, "| galat terakhir:", d["last_error"])'
}

jumlah_event() {
  curl -s -H "$AUTH" "$API/obdf/1/events?limit=500" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))'
}

tunggu_event() {   # $1 = jumlah event sebelum DDL, $2 = batas detik
  local sebelum="$1" batas="${2:-90}" i=0
  while [ "$i" -lt "$batas" ]; do
    [ "$(jumlah_event)" -gt "$sebelum" ] && { echo "  event baru diterima setelah ${i}s"; return 0; }
    sleep 3
    i=$((i + 3))
  done
  echo "  TIDAK ada event baru setelah ${batas}s (periksa status konektor pada tahap 0)"
  return 1
}

judul "3) event yang sudah diproses"
daftar_event 10

if [ "$DDL" = "1" ]; then
  judul "4) uji ujung ke ujung: ADD COLUMN pada tabel yang difederasikan"
  SEBELUM="$(jumlah_event)"
  docker exec datasources-pgsql psql -U postgres -d kemensos -q -c \
    "ALTER TABLE public.penerima_manfaat ADD COLUMN email VARCHAR(100);"
  tunggu_event "$SEBELUM" 90
  daftar_event 3
  daftar_rencana 2
  statistik

  judul "5) kembalikan kondisi sumber: DROP COLUMN"
  SEBELUM="$(jumlah_event)"
  docker exec datasources-pgsql psql -U postgres -d kemensos -q -c \
    "ALTER TABLE public.penerima_manfaat DROP COLUMN email;"
  tunggu_event "$SEBELUM" 90
  daftar_rencana 2
  statistik
else
  judul "4) rencana terkini (tanpa DDL)"
  daftar_rencana 5
  statistik
fi

judul "6) ringkasan"
echo -n "  kolom email pada sumber (0 = sudah seperti semula): "
docker exec datasources-pgsql psql -U postgres -d kemensos -tAc \
  "SELECT count(*) FROM information_schema.columns WHERE table_name='penerima_manfaat' AND column_name='email'"
git -C "$ROOT" status --short
