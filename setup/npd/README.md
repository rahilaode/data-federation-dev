# NPD case study: inputs and generators

The case study of ASCAM is the NPD benchmark (Lanti, Rezk, Xiao, Calvanese, *The NPD Benchmark:
Reality Check for OBDA Systems*, EDBT 2015; <https://github.com/ontop/npd-benchmark>, Apache-2.0):
data of the Norwegian Petroleum Directorate, its OWL 2 QL ontology, its R2RML mapping (1,177
TriplesMaps), and 31 SPARQL queries. NPD ships as one database; this folder splits it into the
two sources of the federation and generates every artifact the lab needs from it (ADR-0027).

## Fetching the inputs

```bash
setup/npd/fetch-npd.sh
```

Sparse clone of `ontop/npd-benchmark` at commit `5b1eeb39c36c0dd5c69c835fd93d204d19be30dd` into
`setup/npd/npd-benchmark/` (ignored by git); every file is checked against its SHA-256. Only the
two dumps, the ontology, the PostgreSQL mapping, and the queries are fetched. `start.sh` and
`restart-ascam.sh` run it; when the clone exists it only checks out the commit and verifies the files again.

## The split

| Source | DBMS | Tables | Rows | Rule (`tools/split.py`) |
|---|---|---:|---:|---|
| `reporting` | PostgreSQL | 25 | 184,275 | Data the operators report: `wellbore_*`, `wlbPoint`, `strat_*`, `seis_*`, `sea*`, `field_production_*` |
| `registry` | MySQL | 45 | 72,996 | Everything else: licences, Petroleum Register, companies, fields, discoveries, facilities, TUF, reserves, investments |

The rule follows the Norwegian systems that hold the data (well, seismic, and production reports
versus the Petroleum Register); it is a choice of this thesis, not part of NPD. Of the 89 foreign
keys of the dump, 19 stay in `reporting`, 59 in `registry`, and 11 cross the sources and are
dropped. Of the 1,177 TriplesMaps, 684 read only `reporting`, 476 only `registry`, and 17 both;
11 of the 31 queries (06, 10, 11, 12, 17, 18, 19, 28, 29, 30, 31) need both sources.

## Generators (`tools/`)

Run from `setup/npd`. The outputs are committed, so these are needed only when the inputs or the
conventions change.

| Script | Output |
|---|---|
| `gen_split.py` | `setup/data-source/{pgsql,mysql}/02-split-*.sql`, from `npd-tables.txt` and `npd-fkeys.txt` |
| `gen_vdb.py tools/catalog/reporting.tsv tools/catalog/registry.tsv` | `setup/data-federation/deployments/npd-vdb.xml`: explicit DDL, primary keys only, quoted `NAMEINSOURCE` for `reporting` (ADR-0029), `clob` above 4,000 characters, and the virtual model `services` |
| `gen_mapping.py npd-benchmark/mappings/postgres/npd-v2-ql.r2rml` | `setup/vkg-system/config/mapping.ttl`: every table qualified with its Teiid model (`"reporting"."wellbore_mud" AS wellbore_mud`), because Ontop 4.1.1 knows a Teiid table only by `<model>.<table>`; seven TriplesMaps pointed at the `services` views |
| `gen_ontology.py npd-benchmark/ontology/npd-v2-ql.owl ../vkg-system/config/ontology_file.ttl` | The ontology as Turtle (ASCAM reads and edits Turtle); the script checks that the graph is unchanged (13,448 triples) |

`tools/catalog/*.tsv` are the column catalogs of the split sources, made by running
`catalog-pg.sql` on `reporting` and `catalog-mysql.sql` on `registry`:

```bash
docker exec -i datasources-pgsql psql -U postgres -d reporting -AtF $'\t' < tools/catalog-pg.sql > tools/catalog/reporting.tsv
docker exec -i datasources-mysql mysql -uroot -pmysql -N -B registry < tools/catalog-mysql.sql > tools/catalog/registry.tsv
```

`services.py` holds the three views of the scenarios A007–A009 and the TriplesMaps that read
them. Against the original mapping on one PostgreSQL database, the 31 NPD queries give
identical answers and six of the seven redirected predicates identical triples; the values of
`npdv:coreIntervalBottom` (8,718 triples) differ in their last digits for 410 triples, because
the view computes the feet-to-metres conversion in exact NUMERIC where Ontop used double
precision (ADR-0027).

`compare_endpoints.py <reference> <tested> <queries dir>` runs the 31 NPD queries on two SPARQL
endpoints and compares the answers as multisets, for example NPD on one PostgreSQL database
against the federation.
