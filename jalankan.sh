#!/usr/bin/env bash
# =============================================================================
# jalankan.sh — menyalakan OBDF dan seluruh komponen ASCAM dengan ASCAM dalam
# keadaan bersih.
#
#   OBDF (PostgreSQL, MySQL, Teiid, Ontop)   TIDAK diubah: hanya dinyalakan bila
#                                            mati. Data sumber, VDB, mapping, dan
#                                            ontologi dibiarkan apa adanya.
#   Kafka dan Debezium                       Dinyalakan bila mati; konektor
#                                            didaftarkan bila belum ada.
#   ASCAM (Knowledge, Ontop Agent,           DIRESET setiap kali: basis data
#   Orchestrator, Executor, konsol)          Knowledge dihapus dan dibangun ulang,
#                                            lalu disinkronkan dari OBDF yang
#                                            sedang berjalan.
#
# Orchestrator memakai grup konsumen Kafka baru yang mulai dari pesan TERBARU,
# sehingga DDL lama di topik tidak diperlakukan sebagai perubahan baru oleh
# Knowledge yang baru direset.
#
# Pemakaian (dari root repository):
#   ./jalankan.sh                  menyalakan dan mereset ASCAM
#   ./jalankan.sh --uji-rantai     + uji rantai event dengan DDL uji pada sumber
#                                  (membuat lalu menghapus tabel ascam_probe)
#   ./jalankan.sh --executor-dijeda  Executor dinyalakan dalam keadaan dijeda
#
# Untuk membangun ulang lab dari nol (MENGHAPUS data sumber), pakai run.sh.
# =============================================================================
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

UJI_RANTAI=0
EXECUTOR_DIJEDA=0
for arg in "$@"; do
  case "$arg" in
    --uji-rantai) UJI_RANTAI=1 ;;
    --executor-dijeda) EXECUTOR_DIJEDA=1 ;;
    -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
    *) echo "opsi tidak dikenal: $arg (lihat --help)"; exit 2 ;;
  esac
done

SECRETS=setup/ascam/knowledge/secrets
KNOWLEDGE=http://127.0.0.1:18000
UI=http://127.0.0.1:18400
TEIID_MGMT=http://localhost:19990/management
SPARQL=http://localhost:8080/sparql
CONNECT=http://localhost:8083
LANGKAH_SAAT_INI="persiapan"

trap 'echo; echo "GAGAL pada langkah: $LANGKAH_SAAT_INI"; echo "Periksa log kontainer terkait dengan: docker logs <nama-kontainer>"; exit 1' ERR

judul() { LANGKAH_SAAT_INI="$1"; printf '\n==== %s ====\n' "$1"; }
info() { printf '  %s\n' "$*"; }
compose() { docker compose -f "$1" "${@:2}"; }

tunggu() {  # $1 deskripsi, $2 batas detik, sisanya perintah yang harus berhasil
  local nama="$1" batas="$2" i=0
  shift 2
  until "$@" >/dev/null 2>&1; do
    i=$((i + 2))
    if [ "$i" -ge "$batas" ]; then
      echo "  $nama belum siap setelah ${batas} detik"
      return 1
    fi
    sleep 2
  done
  info "$nama siap"
}

teiid_vdb_aktif() {
  local sandi
  sandi="$(head -n1 "$SECRETS/obdf_teiid_mgmt_password")"
  curl -sf --digest -u "admin:$sandi" -H 'Content-Type: application/json' \
    -d '{"operation":"list-vdbs","address":[{"subsystem":"teiid"}]}' "$TEIID_MGMT" \
    | python3 -c 'import json,sys; r=json.load(sys.stdin).get("result") or []; sys.exit(0 if any(v.get("vdb-name")=="government" and v.get("status")=="ACTIVE" for v in r) else 1)'
}
sparql_siap() { curl -sf -G "$SPARQL" --data-urlencode 'query=ASK { ?s ?p ?o }' -H 'Accept: application/sparql-results+json'; }
connect_siap() { curl -sf "$CONNECT/connectors"; }
knowledge_siap() { curl -sf "$KNOWLEDGE/ready"; }
# Keluaran disimpan dulu, lalu dicocokkan: `docker ps | grep -q` di bawah pipefail dapat
# gagal palsu karena docker menerima SIGPIPE ketika grep -q keluar lebih awal.
kontainer_ada() { local semua; semua="$(docker ps -a --format '{{.Names}}')"; grep -qx "$1" <<< "$semua"; }

# ── 0. prasyarat ──────────────────────────────────────────────────────────────
judul "0) prasyarat"
docker info >/dev/null 2>&1 || { echo "  Docker tidak berjalan"; exit 1; }
docker network create ascam-networks >/dev/null 2>&1 || true
info "jaringan ascam-networks tersedia"
setup/ascam/knowledge/scripts/init-secrets.sh | sed 's/^/  /'
if kontainer_ada ascam-adaptive-engine; then
  docker rm -f ascam-adaptive-engine >/dev/null
  info "mesin iterasi 1 (ascam-adaptive-engine) dihapus; tidak dipakai lagi"
fi

# ── 1. OBDF: dinyalakan bila mati, tidak diubah ───────────────────────────────
judul "1) OBDF: sumber data, Teiid, Ontop (tanpa perubahan data maupun konfigurasi)"
compose setup/data-source/docker-compose.yaml up -d 2>&1 | sed 's/^/  /'

TEIID_BARU=0
kontainer_ada data-federation-teiid || TEIID_BARU=1
if [ "$TEIID_BARU" = "1" ]; then
  # Kontainer Teiid belum pernah dibuat: VDB awal perlu diminta ter-deploy lewat marker,
  # persis seperti run.sh. Berkas VDB-nya sendiri tidak diubah.
  DEPLOY_DIR=setup/data-federation/deployments
  rm -f "$DEPLOY_DIR"/*.dodeploy "$DEPLOY_DIR"/*.isdeploying "$DEPLOY_DIR"/*.failed "$DEPLOY_DIR"/*.pending
  touch "$DEPLOY_DIR"/government-vdb.xml.dodeploy
  chmod 777 "$DEPLOY_DIR"
  info "kontainer Teiid baru: VDB awal akan di-deploy"
fi
compose setup/data-federation/docker-compose.yaml up -d 2>&1 | sed 's/^/  /'
tunggu "VDB government (ACTIVE)" 240 teiid_vdb_aktif

compose setup/vkg-system/docker-compose.yaml up -d 2>&1 | sed 's/^/  /'
tunggu "endpoint SPARQL Ontop" 180 sparql_siap

# ── 2. Kafka dan Debezium ─────────────────────────────────────────────────────
judul "2) Kafka dan Debezium"
compose setup/ascam/schema-monitor/docker-compose.yaml up -d 2>&1 | sed 's/^/  /'
tunggu "Kafka Connect" 180 connect_siap
TERDAFTAR="$(curl -sf "$CONNECT/connectors" || echo '[]')"
for pasangan in "postgres-connector:setup/ascam/schema-monitor/register-postgres.json" \
                "mysql-connector:setup/ascam/schema-monitor/register-mysql.json"; do
  konektor="${pasangan%%:*}"
  berkas="${pasangan#*:}"
  if grep -q "\"$konektor\"" <<< "$TERDAFTAR"; then
    info "$konektor sudah terdaftar"
  else
    curl -sf -X POST -H 'Content-Type: application/json' --data @"$berkas" "$CONNECT/connectors" >/dev/null
    info "$konektor didaftarkan"
  fi
done
sleep 3
for konektor in postgres-connector mysql-connector; do
  curl -sf "$CONNECT/connectors/$konektor/status" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("  %-18s konektor=%s tugas=%s" % (d["name"], d["connector"]["state"], [t["state"] for t in d.get("tasks", [])]))'
done

# ── 3. ASCAM: direset total ───────────────────────────────────────────────────
judul "3) ASCAM: reset Knowledge dan layanan"
for layanan in ui executor orchestrator ontop-agent; do
  compose "setup/ascam/$layanan/docker-compose.yaml" down --remove-orphans >/dev/null 2>&1 || true
done
compose setup/ascam/knowledge/docker-compose.yaml down -v --remove-orphans >/dev/null 2>&1 || true
info "layanan ASCAM dihentikan; basis data Knowledge dihapus"

compose setup/ascam/knowledge/docker-compose.yaml up -d --build --wait 2>&1 | tail -n 3 | sed 's/^/  /'
tunggu "Knowledge Service" 120 knowledge_siap
docker logs ascam-knowledge-migrate 2>&1 | tail -n 1 | sed 's/^/  migrasi: /'
docker logs ascam-knowledge-service 2>&1 | grep bootstrap | tail -n 1 | sed 's/^/  /' || true

compose setup/ascam/ontop-agent/docker-compose.yaml up -d --build --wait 2>&1 | tail -n 1 | sed 's/^/  /'

GENERASI="$(date +%Y%m%dT%H%M%S)"
ASCAM_ORCH_GROUP_ID="ascam-orchestrator-$GENERASI" ASCAM_ORCH_OFFSET_RESET=latest \
  compose setup/ascam/orchestrator/docker-compose.yaml up -d --build --wait 2>&1 | tail -n 1 | sed 's/^/  /'
info "Orchestrator: grup konsumen ascam-orchestrator-$GENERASI, mulai dari pesan terbaru"

compose setup/ascam/executor/docker-compose.yaml up -d --build --wait 2>&1 | tail -n 1 | sed 's/^/  /'
compose setup/ascam/ui/docker-compose.yaml up -d --build --wait 2>&1 | tail -n 1 | sed 's/^/  /'

# ── 4. Knowledge: sinkronisasi awal dari OBDF yang sedang berjalan ────────────
judul "4) sinkronisasi awal Knowledge"
TOKEN="$(grep '^ui:' "$SECRETS/knowledge_api_tokens" | cut -d: -f2)"
OBDF_ID="$(curl -sf -H "Authorization: Bearer $TOKEN" "$KNOWLEDGE/api/v1/obdf" \
  | python3 -c 'import json,sys; print(next(o["id"] for o in json.load(sys.stdin) if o["name"]=="bansos"))')"
curl -sf -X POST -H "Authorization: Bearer $TOKEN" -H 'X-ASCAM-User: jalankan.sh' \
  "$KNOWLEDGE/api/v1/obdf/$OBDF_ID/sync" | python3 -c '
import json, sys
d = json.load(sys.stdin)
c = d.get("counts") or {}
print("  versi spesifikasi %s | %s tabel, %s kolom, %s TriplesMap, %s entitas ontologi, %s lineage"
      % (d.get("version_no"), c.get("tables"), c.get("columns"), c.get("triples_maps"),
         c.get("entities"), c.get("column_usages")))
masalah = d.get("issues") or []
galat = [m for m in masalah if m.get("severity") == "error"]
print("  masalah konsistensi: %d (%d galat)" % (len(masalah), len(galat)))
for m in galat[:5]:
    print("    GALAT %s: %s" % (m["code"], m["subject_ref"]))'

if [ "$EXECUTOR_DIJEDA" = "1" ]; then
  curl -sf -X POST http://127.0.0.1:18300/control/pause >/dev/null
  info "Executor dijeda; lanjutkan dari konsol bila siap"
fi

# ── 5. pemeriksaan ────────────────────────────────────────────────────────────
judul "5) pemeriksaan"
# Kegagalan pemeriksaan tidak membatalkan penyalaan: layanan tetap menyala dan masalahnya
# dilaporkan pada ringkasan.
PERINGATAN=0
if [ "$UJI_RANTAI" = "1" ]; then
  python3 experiments/f6/preflight.py || PERINGATAN=1
else
  python3 experiments/f6/preflight.py --tanpa-rantai || PERINGATAN=1
fi

# ── ringkasan ─────────────────────────────────────────────────────────────────
LANGKAH_SAAT_INI="ringkasan"
cat <<EOF

==== siap ====
  Konsol administrator : $UI   (pengguna: admin)
  Kata sandi konsol    : $SECRETS/ui_admin_password
  Knowledge API        : $KNOWLEDGE
  Endpoint SPARQL      : $SPARQL
  Kafka UI             : http://localhost:8081

  ASCAM dimulai dari keadaan bersih. OBDF dan data sumber tidak diubah.
EOF
if [ "$PERINGATAN" = "1" ]; then
  echo
  echo "  PERHATIAN: pemeriksaan menemukan masalah (lihat bagian 5). Layanan tetap menyala."
  exit 1
fi
