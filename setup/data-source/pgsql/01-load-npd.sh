#!/bin/bash
# Step 1 of 4 (runs once, on the first start of an empty volume).
# Loads the FULL NPD dump into the database "reporting"; 02-split-reporting.sql then removes
# the tables that belong to the other source. Warnings "nonstandard use of \\ in a string
# literal" are expected: the dump sets standard_conforming_strings=off. Any error stops the
# load (ON_ERROR_STOP) and the container exits.
set -eo pipefail
echo "[npd] loading the PostgreSQL dump into $POSTGRES_DB ..."
psql -v ON_ERROR_STOP=1 -q --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" -f /npd/npd.psql
