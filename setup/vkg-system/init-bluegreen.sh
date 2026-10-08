#!/usr/bin/env bash
# Menyiapkan slot blue-green Ontop dan upstream proxy (ADR-0022).
#   slots/blue, slots/green : salinan mapping, ontologi, dan properti (URL JDBC dikunci versi)
#   slots/state.json        : warna aktif dan versi VDB yang dilayaninya
#   proxy/runtime/upstream.conf : instance yang dilayani proxy
# Slot yang sudah ada tidak diubah, kecuali dengan --reset (dipakai start.sh).
# Pemakaian: setup/vkg-system/init-bluegreen.sh [--reset] [--vdb-version N]
set -euo pipefail
cd "$(dirname "$0")"
RESET=0; VERSI=1
while [ $# -gt 0 ]; do
  case "$1" in
    --reset) RESET=1 ;;
    --vdb-version) VERSI="$2"; shift ;;
    *) echo "opsi tidak dikenal: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ "$RESET" = 1 ]; then rm -rf slots proxy/runtime; fi
if [ -f slots/state.json ]; then
  echo "  slot blue-green sudah ada (aktif: $(python3 -c 'import json; print(json.load(open("slots/state.json"))["active"])'))"
  exit 0
fi
mkdir -p slots/blue slots/green proxy/runtime
for warna in blue green; do
  cp config/mapping.ttl config/ontology_file.ttl "slots/$warna/"
  sed -E "s|^(\s*jdbc\.url\s*=\s*[^;[:space:]]*)(;version=[^;[:space:]]*)?|\1;version=$VERSI|" \
    config/government.docker.properties > "slots/$warna/government.docker.properties"
done
printf '{\n  "active": "blue",\n  "vdb_version": "%s"\n}\n' "$VERSI" > slots/state.json
printf '# dikelola agen ASCAM (ADR-0022); jangan diubah manual\nset $ontop_upstream http://vkg-system-ontop-blue:8080;\n' \
  > proxy/runtime/upstream.conf
chmod -R a+rwX slots proxy/runtime
echo "  slot blue-green dibuat: aktif blue, VDB versi $VERSI"
