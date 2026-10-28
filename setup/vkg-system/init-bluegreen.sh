#!/usr/bin/env bash
# Prepares the blue-green Ontop slots and the proxy upstream (ADR-0022).
#   slots/blue, slots/green     : copies of the mapping, ontology, and properties (JDBC URL pinned to a version)
#   slots/state.json            : the active colour and the VDB version it serves
#   proxy/runtime/upstream.conf : the instance the proxy serves
# Existing slots are left alone, except with --reset (used by start.sh).
# Usage: setup/vkg-system/init-bluegreen.sh [--reset] [--vdb-version N]
set -euo pipefail
cd "$(dirname "$0")"
RESET=0; VERSION=1
while [ $# -gt 0 ]; do
  case "$1" in
    --reset) RESET=1 ;;
    --vdb-version) VERSION="$2"; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

if [ "$RESET" = 1 ]; then rm -rf slots proxy/runtime; fi
if [ -f slots/state.json ]; then
  echo "  blue-green slots already exist (active: $(python3 -c 'import json; print(json.load(open("slots/state.json"))["active"])'))"
  exit 0
fi
mkdir -p slots/blue slots/green proxy/runtime
for colour in blue green; do
  cp config/mapping.ttl config/ontology_file.ttl "slots/$colour/"
  sed -E "s|^(\s*jdbc\.url\s*=\s*[^;[:space:]]*)(;version=[^;[:space:]]*)?|\1;version=$VERSION|" \
    config/government.docker.properties > "slots/$colour/government.docker.properties"
done
printf '{\n  "active": "blue",\n  "vdb_version": "%s"\n}\n' "$VERSION" > slots/state.json
printf '# managed by the ASCAM agent (ADR-0022); do not edit by hand\nset $ontop_upstream http://vkg-system-ontop-blue:8080;\n' \
  > proxy/runtime/upstream.conf
chmod -R a+rwX slots proxy/runtime
echo "  blue-green slots created: blue active, VDB version $VERSION"
