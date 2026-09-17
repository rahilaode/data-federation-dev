#!/usr/bin/env bash
# F0.5 — Uji kelayakan: mapping R2RML pada Ontop 4.1.1 dan `ontop validate`.
#
# Pertanyaan yang diuji:
#   1. Apakah `ontop validate` menerima mapping yang dipakai sekarang (.obda) dan
#      mapping R2RML hasil tulisan tangan (mapping.ttl)?
#   2. Apakah `ontop mapping to-r2rml` dapat mengonversi .obda ke R2RML?
#   3. Apakah ketiga mapping (obda, ttl tulisan tangan, ttl hasil konversi) menghasilkan
#      JAWABAN yang sama untuk kueri uji dan kueri sidik jari graf?
#   4. Kesalahan apa yang ditangkap `ontop validate` (kolom tidak ada; predikat tidak
#      dideklarasikan di ontologi)?
#   5. Dapatkah validasi diarahkan ke versi VDB tertentu (property JDBC `version`),
#      sehingga M' bisa divalidasi terhadap Σ'_S sebelum koneksi dipindahkan?
#
# Semua perintah dijalankan di kontainer Ontop sementara (image yang sama dengan
# endpoint); endpoint yang berjalan, artefak OBDF, dan VDB tidak diubah.
#
# Pemakaian (dari root repository, stack sedang berjalan):
#   experiments/f0/f0_5_ontop_r2rml.sh
set -uo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
CFG="$ROOT/setup/vkg-system/config"
JDBC="$ROOT/setup/vkg-system/jdbc"
QDIR="$ROOT/experiments"
OUT="$ROOT/results/f0/f0_5_$(date +%Y%m%dT%H%M%S)"
WORK="$OUT/work"
mkdir -p "$WORK"
exec > >(tee "$OUT/output.txt") 2>&1
IMAGE=ontop/ontop-endpoint:4.1.1
ONT=/opt/ontop/input/ontology_file.ttl
PROPS=/opt/ontop/input/government.docker.properties

ontop_cli() {  # menjalankan CLI Ontop di kontainer sementara; stdout+stderr ke layar
  # CLI memakai logback langsung (bukan Spring Boot), sehingga konfigurasi log
  # diberikan lewat -Dlogback.configurationFile, bukan -Dlogging.config.
  docker run --rm --network ascam-networks \
    -v "$CFG:/opt/ontop/input:ro" -v "$JDBC:/opt/ontop/jdbc:ro" \
    -v "$WORK:/work" -v "$QDIR:/q:ro" \
    --entrypoint java "$IMAGE" \
    -cp '/opt/ontop/lib/*:/opt/ontop/jdbc/*' -Dlogback.configurationFile=/opt/ontop/log/logback.xml \
    it.unibz.inf.ontop.cli.Ontop "$@"
}

validate() {  # $1 label, sisanya argumen tambahan
  local label=$1; shift
  local t0 t1 rc
  t0=$(date +%s.%N)
  ontop_cli validate -t "$ONT" -p "$PROPS" "$@" > "$WORK/validate_$label.log" 2>&1
  rc=$?
  t1=$(date +%s.%N)
  printf '   %-34s exit=%d  (%.1f s)  %s\n' "$label" "$rc" \
    "$(python3 -c "print(float('$t1') - float('$t0'))")" \
    "$(grep -E 'Validation completed|ERROR' "$WORK/validate_$label.log" | head -1 | cut -c1-110)"
  if [ "$rc" -ne 0 ]; then
    grep -E -i 'exception|caused by|error' "$WORK/validate_$label.log" | grep -v '^\s*at ' | head -4 | cut -c1-200 | sed 's/^/        /'
  fi
}

echo "== 0) persiapan varian mapping"
cp "$CFG/mapping.ttl" "$WORK/mapping_manual.ttl"
python3 - "$WORK" <<'PY'
import sys, pathlib
work = pathlib.Path(sys.argv[1])
src = (work / 'mapping_manual.ttl').read_text()
cases = {
    # kolom yang tidak ada di Σ_S
    'mapping_bad_column.ttl': ('rr:column "status_ekonomi"', 'rr:column "kolom_tidak_ada"'),
    # predikat yang tidak dideklarasikan di ontologi
    'mapping_bad_predicate.ttl': ('rr:predicate bansos:statusEkonomi', 'rr:predicate bansos:propertiTidakAda'),
}
for name, (old, new) in cases.items():
    assert src.count(old) == 1, f'{old!r} tidak ditemukan tepat satu kali'
    (work / name).write_text(src.replace(old, new))
    print(f'   {name}: {old} -> {new}')
PY

echo; echo "== 1) konversi .obda -> R2RML (ontop mapping to-r2rml)"
ontop_cli mapping to-r2rml -i /opt/ontop/input/mapping.obda -t "$ONT" -p "$PROPS" \
  -o /work/mapping_from_obda.ttl > "$WORK/to_r2rml.log" 2>&1
echo "   exit=$?  hasil: $(wc -l < "$WORK/mapping_from_obda.ttl" 2>/dev/null || echo 0) baris"

echo; echo "== 2) validasi"
validate obda              -m /opt/ontop/input/mapping.obda
validate ttl_manual        -m /work/mapping_manual.ttl
validate ttl_from_obda     -m /work/mapping_from_obda.ttl
validate bad_column        -m /work/mapping_bad_column.ttl
validate bad_predicate     -m /work/mapping_bad_predicate.ttl
echo "   -- validasi terhadap versi VDB tertentu (property JDBC 'version')"
validate vdb_version_1     -m /work/mapping_manual.ttl --db-url 'jdbc:teiid:government@mm://teiid:31000;version=1'
validate vdb_version_9     -m /work/mapping_manual.ttl --db-url 'jdbc:teiid:government@mm://teiid:31000;version=9'

echo; echo "== 3) kesetaraan jawaban (ontop query, keluaran CSV)"
QUERIES="f0/queries_equiv/eq_predicates.rq f0/queries_equiv/eq_classes.rq queries/a001_existing.rq queries/a002_optional.rq queries/a003_rename.rq"
for m in obda:/opt/ontop/input/mapping.obda manual:/work/mapping_manual.ttl converted:/work/mapping_from_obda.ttl; do
  label=${m%%:*}; path=${m#*:}
  for q in $QUERIES; do
    qn=$(basename "$q" .rq)
    ontop_cli query -m "$path" -t "$ONT" -p "$PROPS" -q "/q/$q" -o "/work/ans_${label}_${qn}.csv" \
      > "$WORK/query_${label}_${qn}.log" 2>&1 || echo "   GAGAL: $label $qn (lihat work/query_${label}_${qn}.log)"
  done
done
python3 - "$WORK" $QUERIES <<'PY'
import csv, sys, pathlib
work = pathlib.Path(sys.argv[1])
for q in sys.argv[2:]:
    qn = pathlib.Path(q).stem
    answers = {}
    for label in ('obda', 'manual', 'converted'):
        f = work / f'ans_{label}_{qn}.csv'
        if not f.exists():
            answers[label] = None
            continue
        rows = list(csv.reader(f.open()))
        answers[label] = (rows[0] if rows else [], sorted(map(tuple, rows[1:])))
    base = answers['obda']
    parts = []
    for label in ('manual', 'converted'):
        a = answers[label]
        if base is None or a is None:
            parts.append(f'{label}: tidak tersedia')
        else:
            parts.append(f"{label}: {'SAMA' if a == base else 'BERBEDA'}")
    n = len(base[1]) if base else '-'
    print(f'   {qn:18s} baris(obda)={n:<4} ' + ' | '.join(parts))
    if base and answers['manual'] and answers['manual'] != base:
        only_obda = set(base[1]) - set(answers['manual'][1])
        only_man = set(answers['manual'][1]) - set(base[1])
        for r in sorted(only_obda)[:5]:
            print(f'        hanya obda  : {r}')
        for r in sorted(only_man)[:5]:
            print(f'        hanya manual: {r}')
PY

echo; echo "== 4) cuplikan hasil konversi to-r2rml (20 baris pertama)"
head -20 "$WORK/mapping_from_obda.ttl" 2>/dev/null | sed 's/^/   /'
echo; echo "hasil tersimpan di: ${OUT#"$ROOT"/}"
