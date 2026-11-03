#!/bin/bash
# Fetches the inputs of the NPD case study from the NPD Benchmark repository
# (https://github.com/ontop/npd-benchmark, Apache-2.0; Lanti et al., EDBT 2015) at a fixed commit,
# and checks every file against its SHA-256. Only these files are fetched (sparse checkout):
#   data/postgres/npd.psql              dump loaded into the PostgreSQL source (reporting)
#   data/mysql/original_npd/npd.mysql   dump loaded into the MySQL source (registry)
#   ontology/npd-v2-ql.owl              ontology (RDF/XML); tools/gen_ontology.py writes it as Turtle
#   mappings/postgres/npd-v2-ql.r2rml   mapping; tools/gen_mapping.py qualifies it for Teiid
#   queries/01.rq ... 31.rq             the 31 benchmark queries
# Usage: setup/npd/fetch-npd.sh            (writes setup/npd/npd-benchmark, ignored by git)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DIR="$HERE/npd-benchmark"
COMMIT=5b1eeb39c36c0dd5c69c835fd93d204d19be30dd
if [ ! -d "$DIR/.git" ]; then
  git clone --quiet --filter=blob:none --no-checkout https://github.com/ontop/npd-benchmark "$DIR"
fi
git -C "$DIR" sparse-checkout set --no-cone /data/postgres/npd.psql /data/mysql/original_npd/npd.mysql \
  /ontology/npd-v2-ql.owl /mappings/postgres/npd-v2-ql.r2rml '/queries/*.rq'
git -C "$DIR" -c advice.detachedHead=false checkout --quiet "$COMMIT"
cd "$DIR"
sha256sum --quiet -c - <<'SUMS'
de318f9a8e0a0f8530c730fa9775c8fbb3027046ea95ea57488407121de807a3  data/postgres/npd.psql
1b237bb77ec2929afa3861b91045495a7b3d08495d328b3d4c605fb3aa639b1d  data/mysql/original_npd/npd.mysql
0436de7c28f8fb8a0392dbe808d63d6f1be4dc6da9ee1500ecf0f1952e6e783a  ontology/npd-v2-ql.owl
bede96f0040d2a57ebb02403033a185ea46c866d8b7123c21775fa27cee81fe9  mappings/postgres/npd-v2-ql.r2rml
SUMS
[ "$(cat queries/*.rq | sha256sum | cut -d' ' -f1)" = 3a12ff8257f01961e586c78ccaea22923483a2aeb23c7e3710dc465c5af7488f ] \
  || { echo "queries/*.rq do not match the expected checksum" >&2; exit 1; }
echo "NPD Benchmark $COMMIT in $DIR: checksums OK"
