#!/usr/bin/env bash
# Mengukur waktu stop dan start kontainer Ontop (lihat docs/adr/0001-reload-ontop.md).
# Pemakaian: experiments/measure_ontop_restart.sh [jumlah_ulangan=3]
# Keluaran : satu baris per ulangan + ringkasan; stack lain tidak disentuh.
set -euo pipefail
N="${1:-3}"
C=vkg-system-ontop-teiid
URL=http://localhost:8080/sparql

ready() {
  until [ "$(curl -s -o /dev/null -w '%{http_code}' -G "$URL" \
            --data-urlencode 'query=ASK {?s ?p ?o}')" = "200" ]; do sleep 0.2; done
}
elapsed() { python3 -c "import sys; print(f'{float(sys.argv[2]) - float(sys.argv[1]):.2f}')" "$1" "$2"; }

ready
echo "== hierarki proses di kontainer $C"
docker top "$C" -eo pid,ppid,comm
before=$(docker logs "$C" 2>&1 | grep -c 'Shutting down ExecutorService' || true)

for i in $(seq 1 "$N"); do
  t0=$(date +%s.%N); docker stop -t 10 "$C" >/dev/null
  t1=$(date +%s.%N); docker start "$C" >/dev/null; ready
  t2=$(date +%s.%N)
  echo "#$i stop=$(elapsed "$t0" "$t1") s | start->siap=$(elapsed "$t1" "$t2") s | total=$(elapsed "$t0" "$t2") s"
  sleep 3
done

after=$(docker logs "$C" 2>&1 | grep -c 'Shutting down ExecutorService' || true)
echo "shutdown Spring rapi : $((after - before))/$N"
echo "exit code terakhir   : $(docker inspect -f '{{.State.ExitCode}}' "$C")"
