# Evaluation

This document describes how ASCAM is evaluated: the scenarios, the procedure of one run, what is
measured, how to run it, what the result files contain, and the threats to validity found along
the way. All tools are in `experiments/evaluation/`. Section numbers such as §3.10 refer to the
thesis proposal.

## Scenarios

The case study is the NPD benchmark (ADR-0027), split into two sources: `reporting` (PostgreSQL,
25 tables: wellbores, seismic surveys, stratigraphy, production) and `registry` (MySQL, 45
tables: licences, fields, discoveries, companies, facilities). Nine schema changes are applied
to them. A001–A006 form a 3 × 2 design (operator × DBMS), so the effect of an operator is not
mixed up with the detection mechanism of the DBMS (an event trigger in PostgreSQL, polling every
10 s in MySQL). A007–A009 change columns that the mapping reads through Teiid views of the
service model `services` (ADR-0023); these three views and the seven TriplesMaps that read them
are the only part of the case study that is not NPD as published.

| Scenario | Change on the source | DBMS | Pattern | Expected decision |
|---|---|---|---|---|
| A001 | `ADD COLUMN "wlbMudPh"` on `wellbore_mud` | PostgreSQL | P-001 | `hitl` (ADR-0021); a new TriplesMap is proposed (ADR-0027) |
| A002 | `DROP COLUMN "wlbLotMudDencity"` on `wellbore_casing_and_lot` | PostgreSQL | P-002 | `auto` |
| A003 | `RENAME COLUMN "wlbDrillingOperator"` on `wellbore_exploration_all` (join column of a cross-source TriplesMap) | PostgreSQL | P-003 | `auto` |
| A004 | `ADD COLUMN cmpCountryCode` on `company` | MySQL | P-001 | `hitl`; a new TriplesMap is proposed |
| A005 | `DROP COLUMN dscDiscoveryYear` on `discovery` | MySQL | P-002 | `auto` |
| A006 | `RENAME COLUMN cmpLongName` on `company` (company names; target of the wells → company join) | MySQL | P-003 | `auto` |
| A007 | `DROP COLUMN "wlbMudViscosityAtMD"`, read through the pass-through view `v_wellbore_mud` | PostgreSQL | P-002 | `auto` (the view is narrowed with `ALTER VIEW`) |
| A008 | `DROP COLUMN prfInvestmentsMillNOK`, read through the aliased view `v_field_investment` | MySQL | P-002 | `auto` (the aliased column is removed from the view; the mapping runs `SELECT *` over it) |
| A009 | `DROP COLUMN "wlbCoreIntervalBottom"`, used in the expression `"wlbCoreIntervalBottom" * 0.3048` of `v_wellbore_core_ft` | PostgreSQL | P-002 | `notify` (ADR-0024; negative control) |

The paper uses the same scenario codes; the harness writes them in lower case (`a001`), and
the baseline run of a scenario carries the code `b` + number (for example `b002`).

The ADD scenarios use names that are not in NPD; A001 and A004 fill three rows of the new
column after the change, so that the new query has something to return. The view scenarios use
columns that A001–A006 do not touch and that no TriplesMap outside the view reads, so what is
observed comes only from the path through the view. `v_wellbore_core_ft` (A009) is not invented
for the test: it holds the feet-to-metres conversion that NPD's own TriplesMap
`NPD:Manual10:2` computes in its logical table, moved into a Teiid view.

A009 is the negative control. Its plan cannot be complete, because the view definition has to be
rewritten by a person. ASCAM therefore builds no actions and only notifies the administrator; the
harness records that no execution ran, that the OBDF version did not change, and that the
affected query still fails as in the baseline. The decision only counts as correct when the plan
reasons contain both `expression` and `services.v_wellbore_core_ft` (`reasons_contain` in
`scenarios.py`), not just when `decision = notify`.

**First case study.** The evaluation of 28 October 2026 (run `20261028T214208`, commit
`a1e9b0a`) used nine scenarios of the same design on a social-assistance OBDF (PostgreSQL
`kemensos`, MySQL `dukcapil`); its artifacts are in `archive/bansos/`. Results of that run use an
older numbering: their `a003`/`b003` is the MySQL rename (A006 in that study) and their
`a006`/`b006` the PostgreSQL rename; `convert_results.py` renumbers them.

## Modes

Both modes use the same query set Q_k and the same restore procedure, so any difference in
behaviour can be attributed to ASCAM (§3.10.2, p. 104).

| Mode | What happens | Reference |
|---|---|---|
| Baseline B001–B009 | The Executor is paused for the whole run. The DDL is applied with no adaptation; Q_k runs before and after; HTTP status, error message, and result are recorded | §3.10.1, pp. 101–103 |
| Treatment A001–A009 | The MAPE-K loop runs. ADD plans are approved by the evaluator (ADR-0021); the D11 decision, Δt_adapt, the step breakdown, endpoint availability, and the PreservationRatio are recorded; all Kafka messages are audited | §3.10.2–3.11 |

## Query set Q_k

Every scenario has five queries: one that touches the changed column (affected), one on another
column of the same table or view (neighbour), and three on other tables (control). Two controls
are the same in every scenario: `well_licence`, which joins exploration wells in `reporting` to
their licences in `registry` on a pair of columns no scenario touches, and `well_name`. Their
definitions are in `queries.py`; the full text is in [SPARQL queries](#sparql-queries).

| Scenario | Affected | Neighbour | Control |
|---|---|---|---|
| a001 | `mud_ph` | `mud_weight` | `company_name`, `well_licence`, `well_name` |
| a002 | `lot_mud_density` | `casing_type` | `company_name`, `well_licence`, `well_name` |
| a003 | `drilling_operator` | `well_total_depth` | `discovery_name`, `well_licence`, `well_name` |
| a004 | `country_code` | `company_short_name` | `mud_weight`, `well_licence`, `well_name` |
| a005 | `discovery_year` | `discovery_name` | `casing_type`, `well_licence`, `well_name` |
| a006 | `company_name` | `company_short_name` | `drilling_operator`, `well_licence`, `well_name` |
| a007 | `mud_viscosity` | `mud_yield_point` | `company_name`, `well_licence`, `well_name` |
| a008 | `investment` | `investment_year` | `mud_weight`, `well_licence`, `well_name` |
| a009 | `core_bottom` | `core_top` | `company_name`, `well_licence`, `well_name` |

The queries have no `ORDER BY`: for a view without a key Ontop 4.1.1 adds `SELECT DISTINCT`, with
which Teiid rejects `ORDER BY` over an IRI expression that is not projected (TEIID30088, see
[finding of 2026-10-09](#findings-before-the-final-evaluation)). Order does not matter, because the
harness sorts every answer set before comparing.

Beyond Q_k, the 31 queries of the NPD benchmark are a check on the case study itself: they were
answered on the base federation and, in an offline run (below), stayed identical after each of
the six adaptations that could be emulated.

## Procedure of one run

1. The Executor is paused and the harness waits until no execution is running.
2. The columns of **all** scenarios are restored, not only those of the scenario about to run,
   and every restore waits for its own event (at most 60 s). If a restore event does not arrive,
   the evaluation stops, because the monitor snapshot and the reset OBDF are out of step.
3. Plans that were not executed (from the restore or from a baseline run) are marked
   `superseded`.
4. The OBDF is reset (`experiments/reset_obdf.py --keep-sources`): artifacts from git, VDB
   version 1, blue-green slots rebuilt, Knowledge synced.
5. The base answers of Q_k are taken, retried while the endpoint does not answer.
6. A random delay drawn from U(0, 10 s) is waited (seeded; the seed is in `metadata.json`).
   Without it each run started right after the MySQL scan that saw the restore, so every DDL fell
   at the same phase of the 10-second cycle.
7. **Baseline:** the DDL is applied, the harness waits 15 s, fills data where the scenario needs it
   (A001, A004), and runs Q_k once.
   **Treatment:** the Executor is resumed before the delay, the availability probe starts, the DDL
   is applied (t_start), the event and plan are observed, an ADD plan is approved, the execution is
   awaited, data is filled where needed, and Q_k runs. For A009 the harness waits 20 s to make sure
   nothing executes.

## Metrics

| Metric | Definition | Reference |
|---|---|---|
| N_log, N_prod | Kafka messages that carry a `ddl_event_log` row and a production table row, from the first to the last treatment run. Production row values are never written to the result files | eqs. 3.13–3.14 |
| C_safe | N_prod = 0 and N_log > 0, plus a check that `table.include.list` holds only `ddl_event_log` | eq. 3.15 |
| Δt_adapt | t_end − t_start. t_start is when the DDL runs; t_end is the end of the `switch` step with blue-green (the new artifacts serve queries and have been verified), or the end of `verify` after the reload with the restart strategy | eq. 3.16, ADR-0022 |
| Machine Δt_adapt | Δt_adapt minus the time the evaluator took to approve (A001, A004) | ADR-0021 |
| N_manual | Split into decisions (1 for A001 and A004, 0 otherwise) and artifact repairs (always 0) | eqs. 3.17–3.18, revised |
| ResultPreserved (RP) | 1 if both executions succeed and the answer sets are identical | eq. 3.19 |
| ExecutionPreserved (EP) | 1 if the Ontop response after the change is not an error | eq. 3.20 |
| PreservationRatio (PR) | Mean of P_k(q) over Q_k × 100 %, with P_k = RP for P-003 and EP for the other patterns | eq. 3.21 |
| Availability | Share of successful probes (one light query about every 0.5 s, from just before the DDL to the end of the execution), and the longest outage (from the first failed probe to the next successful one) | ADR-0022 |
| Theoretical expectation | P-001: old queries still valid and the new query returns rows; P-002: the old query still runs and returns an empty answer set; P-003: identical answers; `notify`: the affected query fails, there are no actions, the notification names the view, nothing executed, the version is unchanged | §3.10.2, p. 104 |

**Repetitions and reliability.** EP, RP, and PR depend only on the decision and the resulting artifacts,
so they are expected to be the same in every run of a scenario. The repetitions measure timing
and bound the probability of a failure. A scenario runs 5 times in baseline (to show that the
baseline behaviour repeats) and 20 times in treatment. `analyze.py` reports a one-sided 95 %
Clopper–Pearson upper bound on the failure probability, 1 − 0.05^(1/n) when there is no
failure: 13.9 % for one scenario (n = 20), 1.85 % over the 160 executed runs (A001–A008), and
1.65 % over all 180 treatment runs. The bound assumes independent runs; pooled over scenarios it
bounds the mean failure probability, not that of every scenario.

## Running the evaluation

The whole stack must be up (`./start.sh`). Then:

```bash
python3 experiments/evaluation/self_test.py                     # harness self-test, no Docker needed
python3 experiments/evaluation/preflight.py                     # component checks and event chain test
python3 experiments/evaluation/evaluate.py --mode both --runs 1 --baseline-runs 1   # trial
nohup python3 experiments/evaluation/evaluate.py --mode both --runs 20 --baseline-runs 5 \
  > evaluation.log 2>&1 &                                        # full evaluation, several hours
tail -f evaluation.log
```

Or, in one command that also rebuilds the lab and runs the analysis: `./run-evaluation.sh`
(20 treatment and 5 baseline runs; `./run-evaluation.sh 2 1` for a trial).

Options of `evaluate.py`:

| Option | Default | Meaning |
|---|---|---|
| `--mode {treatment,baseline,both}` | `both` | Which modes to run |
| `--scenario aNNN` | all | Run one scenario; can be repeated |
| `--runs N` | 20 | Treatment runs per scenario |
| `--baseline-runs N` | 5 | Baseline runs per scenario |
| `--timeout S` | 180 | Wait limit per stage, in seconds |
| `--observe-delay S` | 15 | Wait after the DDL before the baseline queries |
| `--seed N` | clock | Seed of the random delay; recorded either way |
| `--skip-checks` | off | Skip the preflight checks |
| `--allow-dirty` | off | Run even when tracked files differ from the commit |

The evaluation refuses to start when tracked files differ from `HEAD`, because the result files
must name the commit that produced them. Do not switch branches or edit files while it runs.

After the evaluation:

```bash
python3 experiments/evaluation/analyze.py results/evaluation/<time> > results/evaluation/<time>/analysis.md
python3 experiments/evaluation/extract.py results/evaluation/<time>    # summary/runs.csv, baseline.csv, summary.json
python3 -m pip install --user matplotlib
python3 experiments/evaluation/plot.py results/evaluation/<time>       # summary/fig_adaptation_time.{png,svg,pdf}
```

### Result files

| File | Content |
|---|---|
| `metadata.json` | Commit, branch, tracked changes, arguments, delay seed, CPU, cores, RAM, OS, Python version |
| `debezium-config.json` | `table.include.list` of both connectors and whether it holds only `ddl_event_log` |
| `baseline-aNNN-RR.json` | One baseline run |
| `run-aNNN-RR.json` | One treatment run |
| `kafka-start.json` | End offsets of every partition before the first treatment run |
| `kafka-audit.json` | Classification of every Kafka message since `kafka-start.json` (`N_log`, `N_prod`, `C_safe`, counts per topic) |

Main keys of a treatment record: `scenario`, `run`, `commit`, `restore_detected`,
`random_delay_s`, `answers_before`, `ddl`, `detection_ms`, `event`, `decision`, `pattern`,
`as_expected`, `reasons`, `reasons_match` (A009), `n_manual`, `decision_ms`, `approved_by`,
`execution` (id, status, timings, failure), `steps` (duration per step, ms), `step_status`,
`outcome` (`succeeded`, `failed`, `rolled_back`, `notified`, or why the run stopped),
`dt_adapt_ms`, `dt_adapt_machine_ms`, `t_end_step`, `answers_after`, `assessment` (metric,
`preservation_ratio`, `per_query`), `expectations`, and `availability`. Each query answer holds
`status`, `ok`, `error`, `n`, the sorted `rows`, and `duration_ms`.

### Results of the Indonesian-language harness

The evaluation reported in the paper was run on branch `eval/final` (commit `a1e9b0a`), before the
code was translated. That harness lived in `experiments/f6/`, wrote to `results/f6/`, and used
Indonesian keys, file names, and query names. The scenarios, the SQL statements, and the SPARQL
queries are those of the first case study (`archive/bansos/`), apart from the names of the backup tables (`ascam_cadangan_*` instead of
`ascam_backup_*`) and the old numbering of A003 and A006. To analyse such a directory with the tools of this branch, convert it first; the
original is not modified:

```bash
python3 experiments/evaluation/convert_results.py results/f6/<time>    # writes results/evaluation/<time>
python3 experiments/evaluation/analyze.py results/evaluation/<time>
```

Text produced at run time by the services of that commit (plan reasons, notification messages)
stays in Indonesian. The converter was checked on synthetic results built with the old harness:
the old analysis and the new analysis of the converted files give the same numbers.

### Other tools

| Script | Use |
|---|---|
| `restore_sources.py` | Puts the source columns of every scenario back after an interrupted run; run `./restart-ascam.sh` afterwards |
| `probe_mysql_monitor.py` | Tests whether the MySQL polling monitor misses a rename that is undone between two scans |
| `kafka_audit.py` | Run by `evaluate.py` inside a container on the Kafka network |
| `experiments/reset_obdf.py` | Returns the OBDF to its base state (also used between runs) |
| `experiments/reset_connectors.py` | Recovers Debezium connectors that lost their position, without replaying `ddl_event_log` |

## Statements applied to the sources

These are the exact statements the harness sends, printed from `scenarios.py`. Each scenario also
writes a backup of the column before the run (DROP scenarios), fills three rows after an ADD, and
restores the column after the run.

| Scenario | Source | Statement applied to the source |
|---|---|---|
| A001 | PostgreSQL (reporting) | `ALTER TABLE public.wellbore_mud ADD COLUMN "wlbMudPh" NUMERIC(6,2)` |
| A002 | PostgreSQL (reporting) | `ALTER TABLE public."wellbore_casing_and_lot" DROP COLUMN "wlbLotMudDencity"` |
| A003 | PostgreSQL (reporting) | `ALTER TABLE public.wellbore_exploration_all RENAME COLUMN "wlbDrillingOperator" TO "wlbDrillingCompany"` |
| A004 | MySQL (registry) | `ALTER TABLE company ADD COLUMN cmpCountryCode VARCHAR(2)` |
| A005 | MySQL (registry) | `` ALTER TABLE `discovery` DROP COLUMN `dscDiscoveryYear` `` |
| A006 | MySQL (registry) | `ALTER TABLE company RENAME COLUMN cmpLongName TO cmpFullName` |
| A007 | PostgreSQL (reporting) | `ALTER TABLE public."wellbore_mud" DROP COLUMN "wlbMudViscosityAtMD"` |
| A008 | MySQL (registry) | `` ALTER TABLE `field_investment_yearly` DROP COLUMN `prfInvestmentsMillNOK` `` |
| A009 | PostgreSQL (reporting) | `ALTER TABLE public."wellbore_core" DROP COLUMN "wlbCoreIntervalBottom"` |

### Full statements per scenario

#### A001: ADD COLUMN wlbMudPh on wellbore_mud

PostgreSQL, database `reporting`; pattern P-001; expected decision `hitl`.

- change

  ```sql
  ALTER TABLE public.wellbore_mud ADD COLUMN "wlbMudPh" NUMERIC(6,2);
  ```

- data after the change

  ```sql
  UPDATE public.wellbore_mud SET "wlbMudPh" = 8.5 + wellbore_mud_id / 10.0 WHERE wellbore_mud_id <= 3;
  ```

- restore

  ```sql
  ALTER TABLE public.wellbore_mud DROP COLUMN "wlbMudPh";
  ```

#### A002: DROP COLUMN wlbLotMudDencity on wellbore_casing_and_lot

PostgreSQL, database `reporting`; pattern P-002; expected decision `auto`.

- before the first run

  ```sql
  CREATE TABLE IF NOT EXISTS public.ascam_backup_wlblotmuddencity AS SELECT "wellbore_casing_and_lot_id", "wlbLotMudDencity" FROM public."wellbore_casing_and_lot";
  ```

- change

  ```sql
  ALTER TABLE public."wellbore_casing_and_lot" DROP COLUMN "wlbLotMudDencity";
  ```

- restore

  ```sql
  ALTER TABLE public."wellbore_casing_and_lot" ADD COLUMN "wlbLotMudDencity" NUMERIC(13,6);
  UPDATE public."wellbore_casing_and_lot" t SET "wlbLotMudDencity" = c."wlbLotMudDencity" FROM public.ascam_backup_wlblotmuddencity c WHERE c."wellbore_casing_and_lot_id" = t."wellbore_casing_and_lot_id";
  SELECT count(*) FROM public."wellbore_casing_and_lot" WHERE "wlbLotMudDencity" IS NOT NULL;
  ```

#### A003: RENAME COLUMN wlbDrillingOperator on wellbore_exploration_all

PostgreSQL, database `reporting`; pattern P-003; expected decision `auto`.

- change

  ```sql
  ALTER TABLE public.wellbore_exploration_all RENAME COLUMN "wlbDrillingOperator" TO "wlbDrillingCompany";
  ```

- restore

  ```sql
  ALTER TABLE public.wellbore_exploration_all RENAME COLUMN "wlbDrillingCompany" TO "wlbDrillingOperator";
  ```

#### A004: ADD COLUMN cmpCountryCode on company

MySQL, database `registry`; pattern P-001; expected decision `hitl`.

- change

  ```sql
  ALTER TABLE company ADD COLUMN cmpCountryCode VARCHAR(2);
  ```

- data after the change

  ```sql
  UPDATE company SET cmpCountryCode = 'NO' ORDER BY cmpNpdidCompany LIMIT 3;
  ```

- restore

  ```sql
  ALTER TABLE company DROP COLUMN cmpCountryCode;
  ```

#### A005: DROP COLUMN dscDiscoveryYear on discovery

MySQL, database `registry`; pattern P-002; expected decision `auto`.

- before the first run

  ```sql
  CREATE TABLE IF NOT EXISTS ascam_backup_dscdiscoveryyear AS SELECT `dscNpdidDiscovery`, `dscDiscoveryYear` FROM `discovery`;
  ```

- change

  ```sql
  ALTER TABLE `discovery` DROP COLUMN `dscDiscoveryYear`;
  ```

- restore

  ```sql
  ALTER TABLE `discovery` ADD COLUMN `dscDiscoveryYear` INT;
  UPDATE `discovery` t JOIN ascam_backup_dscdiscoveryyear c ON c.`dscNpdidDiscovery` = t.`dscNpdidDiscovery` SET t.`dscDiscoveryYear` = c.`dscDiscoveryYear`;
  SELECT count(*) FROM `discovery` WHERE `dscDiscoveryYear` IS NOT NULL;
  ```

#### A006: RENAME COLUMN cmpLongName on company

MySQL, database `registry`; pattern P-003; expected decision `auto`.

- change

  ```sql
  ALTER TABLE company RENAME COLUMN cmpLongName TO cmpFullName;
  ```

- restore

  ```sql
  ALTER TABLE company RENAME COLUMN cmpFullName TO cmpLongName;
  ```

#### A007: DROP COLUMN wlbMudViscosityAtMD through view v_wellbore_mud

PostgreSQL, database `reporting`; pattern P-002; expected decision `auto`.

- before the first run

  ```sql
  CREATE TABLE IF NOT EXISTS public.ascam_backup_wlbmudviscosityatmd AS SELECT "wellbore_mud_id", "wlbMudViscosityAtMD" FROM public."wellbore_mud";
  ```

- change

  ```sql
  ALTER TABLE public."wellbore_mud" DROP COLUMN "wlbMudViscosityAtMD";
  ```

- restore

  ```sql
  ALTER TABLE public."wellbore_mud" ADD COLUMN "wlbMudViscosityAtMD" NUMERIC(13,6);
  UPDATE public."wellbore_mud" t SET "wlbMudViscosityAtMD" = c."wlbMudViscosityAtMD" FROM public.ascam_backup_wlbmudviscosityatmd c WHERE c."wellbore_mud_id" = t."wellbore_mud_id";
  SELECT count(*) FROM public."wellbore_mud" WHERE "wlbMudViscosityAtMD" IS NOT NULL;
  ```

#### A008: DROP COLUMN prfInvestmentsMillNOK through view v_field_investment

MySQL, database `registry`; pattern P-002; expected decision `auto`.

- before the first run

  ```sql
  CREATE TABLE IF NOT EXISTS ascam_backup_prfinvestmentsmillnok AS SELECT `prfNpdidInformationCarrier`, `prfYear`, `prfInvestmentsMillNOK` FROM `field_investment_yearly`;
  ```

- change

  ```sql
  ALTER TABLE `field_investment_yearly` DROP COLUMN `prfInvestmentsMillNOK`;
  ```

- restore

  ```sql
  ALTER TABLE `field_investment_yearly` ADD COLUMN `prfInvestmentsMillNOK` DECIMAL(13,6);
  UPDATE `field_investment_yearly` t JOIN ascam_backup_prfinvestmentsmillnok c ON c.`prfNpdidInformationCarrier` = t.`prfNpdidInformationCarrier` AND c.`prfYear` = t.`prfYear` SET t.`prfInvestmentsMillNOK` = c.`prfInvestmentsMillNOK`;
  SELECT count(*) FROM `field_investment_yearly` WHERE `prfInvestmentsMillNOK` IS NOT NULL;
  ```

#### A009: DROP COLUMN wlbCoreIntervalBottom used in a view expression

PostgreSQL, database `reporting`; pattern P-002; expected decision `notify`.

- before the first run

  ```sql
  CREATE TABLE IF NOT EXISTS public.ascam_backup_wlbcoreintervalbottom AS SELECT "wellbore_core_id", "wlbCoreIntervalBottom" FROM public."wellbore_core";
  ```

- change

  ```sql
  ALTER TABLE public."wellbore_core" DROP COLUMN "wlbCoreIntervalBottom";
  ```

- restore

  ```sql
  ALTER TABLE public."wellbore_core" ADD COLUMN "wlbCoreIntervalBottom" NUMERIC(13,6);
  UPDATE public."wellbore_core" t SET "wlbCoreIntervalBottom" = c."wlbCoreIntervalBottom" FROM public.ascam_backup_wlbcoreintervalbottom c WHERE c."wellbore_core_id" = t."wellbore_core_id";
  SELECT count(*) FROM public."wellbore_core" WHERE "wlbCoreIntervalBottom" IS NOT NULL;
  ```

## SPARQL queries

Every query is sent to `http://localhost:8080/sparql` with `Accept:
application/sparql-results+json`, printed here exactly as `queries.py` builds it. The vocabulary
is the NPD ontology (`npdv:` = `http://sws.ifi.uio.no/vocab/npd-v2#`); `npdv:wlbMudPh` and
`npdv:cmpCountryCode` are the properties ASCAM proposes for the two ADD scenarios (naming policy
of `obdf-npd.yaml`).

**`mud_ph`** (used by a001)

```sparql
PREFIX npdv: <http://sws.ifi.uio.no/vocab/npd-v2#>
SELECT ?m ?ph WHERE { ?m npdv:wlbMudPh ?ph }
```

**`mud_weight`** (used by a001, a004, a008)

```sparql
PREFIX npdv: <http://sws.ifi.uio.no/vocab/npd-v2#>
SELECT ?m ?w WHERE { ?m a npdv:WellboreDrillingMudSample ; npdv:mudWeight ?w }
```

**`company_name`** (used by a001, a002, a006, a007, a009)

```sparql
PREFIX npdv: <http://sws.ifi.uio.no/vocab/npd-v2#>
SELECT ?c ?n WHERE { ?c a npdv:Company ; npdv:name ?n }
```

**`well_licence`** (used by a001, a002, a003, a004, a005, a006, a007, a008, a009)

```sparql
PREFIX npdv: <http://sws.ifi.uio.no/vocab/npd-v2#>
SELECT ?w ?l WHERE { ?w npdv:explorationWellboreForLicence ?l }
```

**`well_name`** (used by a001, a002, a003, a004, a005, a006, a007, a008, a009)

```sparql
PREFIX npdv: <http://sws.ifi.uio.no/vocab/npd-v2#>
SELECT ?w ?n WHERE { ?w a npdv:ExplorationWellbore ; npdv:name ?n }
```

**`lot_mud_density`** (used by a002)

```sparql
PREFIX npdv: <http://sws.ifi.uio.no/vocab/npd-v2#>
SELECT ?c ?d WHERE { ?c npdv:LOTMudDensity ?d }
```

**`casing_type`** (used by a002, a005)

```sparql
PREFIX npdv: <http://sws.ifi.uio.no/vocab/npd-v2#>
SELECT ?c ?t WHERE { ?c a npdv:WellboreCasingAndLeakoffTest ; npdv:casingType ?t }
```

**`drilling_operator`** (used by a003, a006)

```sparql
PREFIX npdv: <http://sws.ifi.uio.no/vocab/npd-v2#>
SELECT ?w ?c WHERE { ?w npdv:drillingOperatorCompany ?c }
```

**`well_total_depth`** (used by a003)

```sparql
PREFIX npdv: <http://sws.ifi.uio.no/vocab/npd-v2#>
SELECT ?w ?d WHERE { ?w a npdv:ExplorationWellbore ; npdv:wellboreTotalDepth ?d }
```

**`discovery_name`** (used by a003, a005)

```sparql
PREFIX npdv: <http://sws.ifi.uio.no/vocab/npd-v2#>
SELECT ?d ?n WHERE { ?d a npdv:Discovery ; npdv:name ?n }
```

**`country_code`** (used by a004)

```sparql
PREFIX npdv: <http://sws.ifi.uio.no/vocab/npd-v2#>
SELECT ?c ?cc WHERE { ?c npdv:cmpCountryCode ?cc }
```

**`company_short_name`** (used by a004, a006)

```sparql
PREFIX npdv: <http://sws.ifi.uio.no/vocab/npd-v2#>
SELECT ?c ?s WHERE { ?c a npdv:Company ; npdv:shortName ?s }
```

**`discovery_year`** (used by a005)

```sparql
PREFIX npdv: <http://sws.ifi.uio.no/vocab/npd-v2#>
SELECT ?d ?y WHERE { ?d a npdv:Discovery ; npdv:discoveryYear ?y }
```

**`mud_viscosity`** (used by a007)

```sparql
PREFIX npdv: <http://sws.ifi.uio.no/vocab/npd-v2#>
SELECT ?m ?v WHERE { ?m npdv:mudViscosity ?v }
```

**`mud_yield_point`** (used by a007)

```sparql
PREFIX npdv: <http://sws.ifi.uio.no/vocab/npd-v2#>
SELECT ?m ?y WHERE { ?m npdv:mudYieldPoint ?y }
```

**`investment`** (used by a008)

```sparql
PREFIX npdv: <http://sws.ifi.uio.no/vocab/npd-v2#>
SELECT ?i ?x WHERE { ?i npdv:investmentNOK ?x }
```

**`investment_year`** (used by a008)

```sparql
PREFIX npdv: <http://sws.ifi.uio.no/vocab/npd-v2#>
SELECT ?i ?y WHERE { ?i a npdv:FieldInvestment ; npdv:productionYear ?y }
```

**`core_bottom`** (used by a009)

```sparql
PREFIX npdv: <http://sws.ifi.uio.no/vocab/npd-v2#>
SELECT ?c ?b WHERE { ?c npdv:coreIntervalBottom ?b }
```

**`core_top`** (used by a009)

```sparql
PREFIX npdv: <http://sws.ifi.uio.no/vocab/npd-v2#>
SELECT ?c ?t WHERE { ?c a npdv:WellboreCore ; npdv:coreIntervalTop ?t }
```

Availability probe, sent about every 0.5 s during each treatment run (no scenario touches the
licence table):

```sparql
PREFIX npdv: <http://sws.ifi.uio.no/vocab/npd-v2#>
SELECT ?l WHERE { ?l a npdv:ProductionLicence } LIMIT 1
```

## Offline check of the NPD adaptations

Before the evaluation on the running lab, the plans of the nine scenarios were checked without
Docker (2 November 2026). Knowledge decided each scenario on the NPD metadata
(`tests/test_case_study_npd.py`: `hitl` for A001 and A004, `auto` for A002, A003, A005–A008,
`notify` for A009). The Executor's editors applied each plan to the VDB, the mapping, and the
ontology. Ontop 4.1.1 then served the result over a PostgreSQL copy of the federation (schemas
`reporting`, `registry`, `services`; keys only, as in the VDB) with the DDL of the scenario
applied, and the 31 NPD queries and Q_k were compared with the base answers.

| Scenario | Affected query, rows before → after | Other queries of Q_k | 31 NPD queries |
|---|---|---|---|
| A001 | `mud_ph`: 0 → 3 | identical | identical |
| A002 | `lot_mud_density`: 5,254 → 0 | identical | identical |
| A004 | `country_code`: 0 → 3 | identical | identical |
| A005 | `discovery_year`: 421 → 0 | identical | identical |
| A007 | `mud_viscosity`: 27,682 → 0 | identical | identical |
| A008 | `investment`: 1,916 → 0 | identical | identical |

A003 and A006 need Teiid (`NAMEINSOURCE`), and A009 changes no artifact, so they are left to the
evaluation on the running lab. This check shows that the artifacts ASCAM writes are valid and
change only what they should; it does not measure time or availability.

## Results of the first case study (social assistance)

These results belong to the earlier case study (see Scenarios); the scenario codes and queries
of that run are not those listed above. Run `20261028T214208` on branch `eval/final` (commit `a1e9b0a`), 28 October 2026, 14:42–18:38 UTC,
on one Linux host with 72 logical CPUs (Intel Xeon E5-2695 v4, 2.1 GHz) and 125.5 GiB of memory;
45 baseline runs (9 × 5) and 180 treatment runs (9 × 20), random-delay seed 1793198525. Every
restore was detected, no run started from a tainted state, and there were no anomalies.
The table uses the new scenario codes; in the files of this run, A003 and A006 are stored as
`a006` and `a003` (see Renumbering under Scenarios).

| Scenario | EP / RP / PR without | EP / RP / PR with | Decision | Adaptation | Detection, median (s) | Δt_adapt, median (max) (s) |
|---|---|---|---|---|---:|---:|
| A001 | 100 / 100 / 100 | 100 / 80 / 100 | 20/20 `hitl` | 20/20 | 1.3 | 29.4 (32.1) |
| A002 | 80 / 80 / 80 | 100 / 80 / 100 | 20/20 `auto` | 20/20 | 1.3 | 29.1 (32.6) |
| A003 | 80 / 80 / 80 | 100 / 100 / 100 | 20/20 `auto` | 20/20 | 1.3 | 29.5 (31.9) |
| A004 | 100 / 100 / 100 | 100 / 80 / 100 | 20/20 `hitl` | 20/20 | 6.3 | 36.5 (40.8) |
| A005 | 80 / 80 / 80 | 100 / 80 / 100 | 20/20 `auto` | 20/20 | 5.3 | 34.0 (39.9) |
| A006 | 80 / 80 / 80 | 100 / 100 / 100 | 20/20 `auto` | 20/20 | 7.3 | 35.2 (41.4) |
| A007 | 80 / 80 / 80 | 100 / 80 / 100 | 20/20 `auto` | 20/20 | 1.3 | 32.1 (33.7) |
| A008 | 80 / 80 / 80 | 100 / 80 / 100 | 20/20 `auto` | 20/20 | 5.3 | 36.2 (41.2) |
| A009 | 80 / 80 / 80 | 80 / 80 / 80 | 20/20 `notify` | not adapted | 1.3 | – |

EP, RP, and PR were identical in every run of a scenario. Detection took 1.27–1.32 s on
PostgreSQL and 1.2–11.4 s on MySQL. Starting the standby Ontop instance (median 14.5–15.4 s) and
validating ℳ′ and 𝒯′ (8.3–8.7 s) make up most of Δt_adapt. All 11 905 availability probes
succeeded. Kafka carried 360 log messages (180 treatment changes and 180 restores) and no
production row (C_safe met). One-sided 95 % Clopper–Pearson upper bounds on the failure
probability: 1.7 % for decisions (0/180), 1.9 % for adaptations (0/160), 13.9 % for one scenario
(0/20).

## Threats to validity and findings

The findings below come from trial and earlier evaluation runs. Result directories that were
affected are kept as a record but are not used in the paper.

### Threats

1. **The restore step is itself a schema change.** Putting a column back between runs is seen by
   ASCAM as a new change and, left alone, is executed automatically, including an Ontop reload just
   when the base answers are taken. In the first evaluation this made 3 of 20 A006 runs fail to take
   their base answers (connection error), and they were wrongly classified as "not identical". The
   effect is largest on MySQL, whose detection time varies by 1–10 s. Handled by pausing the
   Executor and superseding the plans of the restore step (procedure steps 1–3).
2. **The MySQL monitor polls.** Detection on MySQL varies much more than on PostgreSQL, so a
   comparison between the DBMSs reflects the monitor mechanism, not only ASCAM.
3. **One test machine.** Absolute times depend on the hardware (`metadata.json` records it); what
   generalises is the share of each step and the comparison between scenarios.
4. **Size of the case study.** `validate` and the Ontop start-up grow with the size of ℳ and 𝒯
   (NPD: 1,177 TriplesMaps), not with the data; the verification queries grow with the data
   (257,271 rows over both sources). The verification is per predicate (ADR-0030), so Δt_adapt
   includes one `COUNT` per candidate predicate through Teiid.
5. **Resetting the infrastructure changes message identities.** `down -v` recreates the Kafka
   topics and the source databases. An idempotency key based on Kafka offsets then collides with old
   events, and new DDL is silently treated as a duplicate. Fixed by deriving the key from the DDL
   itself (ADR-0017). An evaluation must run on infrastructure that is not recreated halfway, and
   the preflight checks must run after every infrastructure reset.
6. **A fixed query set.** Answer equivalence is measured on the five queries of each scenario, not
   on every possible query.
7. **Ontop–Teiid limits on views.** Queries over views run without `ORDER BY` (see below); this is a
   limit of Ontop 4.1.1 with Teiid, not of ASCAM.
8. **The split of NPD is a modelling choice.** NPD ships as one database; which table goes to which
   source follows the Norwegian systems that own the data (ADR-0027), but another split would give
   other cross-source TriplesMaps. The three service views of A007–A009 are added to NPD.
9. **Renames cannot be checked offline.** `NAMEINSOURCE` exists only in Teiid, so A003 and A006
   are checked only on the running federation.

### Evaluation 20260922T150904

Six of 60 treatment runs were not recorded as finished. Five (A002 run 9; A006 runs 1, 3, 8, 17)
came from a defect: Knowledge committed after sending its response, so `/finish` sometimes arrived
before the commit of `/sync`, was refused with 409, and the Executor left the execution `running`
(ADR-0008 and ADR-0020, revisions F6). The adaptations themselves had finished, sync included. One
run (A006 run 12) produced no event, because the MySQL monitor recorded neither the restore rename
nor the treatment rename. Because these results came from defective code, the treatment runs were
repeated after the fix.

**MySQL monitor probe** (`probe_mysql_monitor.py`, 3 repeats per delay). A column is renamed and
renamed back after W seconds:

| Delay W (s) | Changes recorded | Cycles with both lost | Detection latency, median (min–max) s |
|---:|---:|---:|---:|
| 2 | 0/6 | 3/3 | – |
| 5 | 4/6 | 1/3 | 4.8 (2.3–7.4) |
| 12 | 6/6 | 0/3 | 5.1 (0.1–7.2) |
| 25 | 6/6 | 0/3 | 4.8 (−0.1–9.7) |

A change undone before the next scan (10 s interval) is not seen, and the two changes are always
lost together. In operation the final schema still matches the OBDF, so only a short-lived
inconsistency is missed. In the evaluation, however, the OBDF is reset outside the MAPE-K loop, so
an undetected restore leaves the monitor snapshot and the OBDF out of step. The harness therefore
requires the restore event to arrive (within 60 s) before the treatment DDL and stops the
evaluation when it does not.

### Evaluation 20260924T072348

That check never applied to A006 at first: the MySQL client prints nothing when `ALTER` succeeds,
and the harness read empty output as "clean" (no change). The treatment DDL then ran about 9–10 s
after the restore, only the duration of the OBDF reset, and both could fall into one scan window
and cancel out. This explains A006 run 12 of the previous evaluation and A006 runs 1 and 2 of this
one (the `tanggal_lahir` query returned HTTP 200 before the DDL, the treatment DDL succeeded, but
`ddl_event_log` held no row between 00:30:06 and 00:58:46). The restore contract is now explicit:
`'clean'` means only that no DDL ran, and every path that runs DDL returns `restored: …`. The
self-test checks both directions.

### Connectors without replay (`snapshot.mode = no_data`), 2026-09-30

After the environment was restarted, the MySQL connector failed with error 1236: the binlog
position it remembered (`mysql-bin.000003`) no longer existed on the server, which only kept
`mysql-bin.000006` and `000007`. At the same time the PostgreSQL connector, in the default mode
`initial`, lost its offsets and read all of `ddl_event_log` again; the freshly reset Knowledge
processed that old DDL (events 149–153) and adapted the OBDF to version 2 against stale history.

Both connectors now use `snapshot.mode = no_data`: the table schema is recorded but old rows are
not published, so only DDL after the registration reaches ASCAM. The PostgreSQL connector keeps
`publication.autocreate.mode = filtered`. `experiments/reset_connectors.py` repairs a broken state:
it stops the connectors, deletes their offsets, deletes the MySQL schema history topic and the
PostgreSQL replication slot and publication, and changes neither the source data nor
`ddl_event_log`.

### Evaluation 20260930T091536: scenarios sharing a table (not valid)

In the 3 × 2 design, A002 and A003 use `program_bansos`, and A006 and A005 use `master_penduduk`.
Each run restored only its own column, so changes left by another scenario carried over: after
B003, `nama_program` was still called `judul_program`, and the A002 neighbour query failed in 20 of
20 runs; after A002, `tipe_program` was missing, and the A003 and B003 neighbour queries failed.
Those failures happened BEFORE the treatment DDL, so they were not caused by an adaptation. The
A002 timings were affected too (IQR 22.6–30.0 s, maximum 39.1 s).

The preparation of every run now restores the columns of all scenarios (`restore_all`), each
waiting for its own restore event, and records which were restored (`restored_scenarios`).
`analyze.py` lists runs whose queries already failed before the DDL (section 8), because the start
state of every run must be clean.

### Findings before the final evaluation

**F0.8 as a prerequisite.** The Teiid Reference Guide says `ALTER VIEW` may not change column
information (teiid-documents, *Schema object DDL*, pp. 356–357). `archive/experiments/f0/f0_8.sh`
checked on a separate test VDB that Teiid 16 accepts an `ALTER VIEW` that narrows the projection of
a view whose columns come from its query, and that it refuses one on a view with inline columns
(ADR-0023). `ASCAM_EXEC_VIEW_STATEMENT` stays `alter`.

**Trial of 2026-10-09.** In the base state all three queries through views failed with HTTP 500,
although the views could be read directly through Teiid:

1. *A007, A009*: `TEIID30088 Unrelated order by column (… || CAST(v1.penerima_id AS STRING)) cannot
   be used in … SELECT DISTINCT`. A view whose columns come from a query has no key, so Ontop 4.1.1
   cannot rule out duplicate triples and adds `SELECT DISTINCT`; `ORDER BY ?p` is translated into
   an `ORDER BY` over an IRI expression that is not projected, and Teiid refuses it. A key can only be
   declared together with inline columns, and a view with inline columns cannot be narrowed by
   `ALTER VIEW` (F0.8 v7). The queries through views are therefore written without `ORDER BY`.
2. *The first design of A008* mapped a `DATETIME` column as `xsd:dateTime`; Ontop's reformulation
   failed with `UnsupportedOperationException: Not yet supported by Teiid`, that is, the Teiid
   adapter of Ontop 4.1.1 does not support that conversion. A008 was moved to
   `riwayat_perubahan_data.diubah_oleh` (`VARCHAR`), which already had a class and a property.

**Trial 20261009T032658.** A009 behaved as designed. A007 and A008 were decided automatically and
correctly, but their executions failed with `HTTPStatusError 500` on the base fingerprint query,
after `validate` (`failure.after_step`). The Executor took the base fingerprint from the active
instance AFTER the DDL, and the fingerprint query `?s ?p ?o` sweeps every mapping. View columns
are always nullable, so Ontop had to read the dropped column to filter `IS NOT NULL`, and the whole
query failed. The older scenarios passed because their columns are declared `not null` in the VDB.
Fix: when the full fingerprint fails on the old instance, it is taken per predicate (the list comes
from ℳ, ℳ′, and 𝒯); failing predicates are recorded, and the new instance is checked the same way.
Re-run 20261009T040048: A007, A008, and A002 (regression of the full-fingerprint path) `succeeded`
with PreservationRatio 100 %.

**Endpoint availability.** From then on every treatment run probes the endpoint about every 0.5 s
with a query no scenario touches (`TransaksiBansos`), to test the ADR-0022 claim that an adaptation
does not interrupt the endpoint.

**A009 after ADR-0024.** Before ADR-0024, A009 was decided `hitl` with three incomplete actions and
the evaluator rejected the plan in every run (older results record the outcome `rejected`, which
`analyze.py` and `extract.py` still accept). Since ADR-0024 it is `notify` with no actions.
