#!/usr/bin/env bash
# =============================================================================
# uji_ulang.sh — restart menyeluruh lalu evaluasi F6 dan analisisnya, satu perintah.
#
#   1. sumber data dikembalikan ke kondisi dasar (pulihkan_sumber.py)
#   2. OBDF dan ASCAM dinyalakan, Knowledge direset (jalankan.sh)
#   3. evaluasi baseline + perlakuan (evaluate.py)
#   4. analisis hasil (analisis.py), disimpan juga ke berkas
#
# Pemakaian (dari root repository):
#   ./uji_ulang.sh            20 ulangan perlakuan, 5 ulangan baseline (angka paper)
#   ./uji_ulang.sh 2 1        ulangan sedikit, untuk uji coba
# Seluruh keluaran juga ditulis ke log-uji-<waktu>.txt.
# =============================================================================
set -Eeuo pipefail
cd "$(dirname "$0")"

ULANGAN="${1:-20}"
BASELINE="${2:-5}"
LOG="log-uji-$(date +%Y%m%dT%H%M%S).txt"
exec > >(tee "$LOG") 2>&1

echo "==== [1/4] pemulihan sumber data ===="
python3 experiments/f6/pulihkan_sumber.py

echo; echo "==== [2/4] restart OBDF dan ASCAM ===="
./jalankan.sh

echo; echo "==== [3/4] evaluasi: $ULANGAN ulangan perlakuan, $BASELINE ulangan baseline ===="
SEBELUM="$(ls -1 results/f6 2>/dev/null | tail -n 1 || true)"
python3 experiments/f6/evaluate.py --mode keduanya --ulangan "$ULANGAN" --ulangan-baseline "$BASELINE"
HASIL="$(ls -1 results/f6 | tail -n 1)"
if [ "$HASIL" = "$SEBELUM" ]; then
  echo "evaluasi tidak menghasilkan folder baru"; exit 1
fi

echo; echo "==== [4/4] analisis results/f6/$HASIL ===="
python3 experiments/f6/analisis.py "results/f6/$HASIL" | tee "results/f6/$HASIL/analisis.md"

echo
echo "Selesai. Kirim berkas ini: results/f6/$HASIL/analisis.md  (log lengkap: $LOG)"
