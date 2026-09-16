#!/usr/bin/env bash
# Eksekusi satu berkas kueri SPARQL ke endpoint Ontop.
# Pemakaian : experiments/sparql.sh <kueri.rq> <hasil.json>
# Keluaran  : body JSON ke <hasil.json>; status HTTP + timestamp ke stderr.
# Exit code : 0 bila HTTP 200, selain itu 1 (kueri pengujian tidak valid / sistem gagal).
set -euo pipefail
[ $# -eq 2 ] || { echo "Pemakaian: $0 <kueri.rq> <hasil.json>" >&2; exit 2; }
ENDPOINT="${ONTOP_ENDPOINT:-http://localhost:8080/sparql}"

# Penjaga: karakter yang tidak sah di dalam IRI SPARQL (mis. backslash
# yang disisipkan shell saat paste) membuat kueri gagal di-parse.
if grep -qE '<[^>]*[\\ {}|^`"][^>]*>' "$1"; then
  echo "ERROR: $1 berisi IRI dengan karakter tidak sah (cek dengan: cat -A $1)" >&2
  exit 2
fi

code=$(curl -s -G "$ENDPOINT" --data-urlencode "query@$1" \
        -H 'Accept: application/sparql-results+json' -o "$2" -w '%{http_code}')
echo "$(date +%Y-%m-%dT%H:%M:%S.%N) $1 -> HTTP $code" >&2
[ "$code" = "200" ]
