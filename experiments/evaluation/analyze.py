"""
Analysis of the evaluation results, laid out after the thesis proposal §3.10–3.11.

  1. Baseline scenarios B001–B009 (§3.10.1): HTTP status, error message, and result per query.
  2. Treatment scenarios A001–A009 (§3.10.2): success, D11 decision, theoretical expectation.
  3. C_safe (§3.11.1): Debezium configuration, N_log, N_prod.
  4. C_FMI (§3.11.2): Δt_adapt against the 60-second limit, N_manual (decision vs. repair),
     time breakdown per step.
  5. Semantic preservation (§3.11.3): PreservationRatio per scenario and per query.
  6. Baseline compared with treatment.
  7. Anomalies.

Usage:
  python3 experiments/evaluation/analyze.py                     # latest result directory
  python3 experiments/evaluation/analyze.py results/evaluation/<time> > results/analysis-<time>.md
"""
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# restart (ADR-0001): ... switch, reload_ontop, verify; blue-green (ADR-0022): ... start_ontop,
# verify, switch. A step that a strategy does not have is left empty.
STEPS = ['deploy_vdb', 'validate', 'start_ontop', 'reload_ontop', 'verify', 'switch', 'sync']
DT_LIMIT = 60_000
# Valid outcomes without an execution: 'notified' for the scenario that must not be adapted
# (A009, ADR-0024); 'rejected' is kept so that older results (A009 plan rejected) still read.
VALID_OUTCOMES = {'succeeded', 'notified', 'rejected'}
NO_EXECUTION = {'notified', 'rejected'}


def quartiles(values: list) -> str:
    values = [v for v in values if isinstance(v, (int, float))]
    if not values:
        return '-'
    if len(values) < 4:
        return f'{int(statistics.median(values))}'
    q = statistics.quantiles(sorted(values), n=4)
    return f'{int(statistics.median(values))} ({int(q[0])}–{int(q[2])})'


def upper_bound(k: int, n: int, alpha: float = 0.05) -> float:
    """One-sided (1 − alpha) Clopper-Pearson upper bound for a failure proportion k/n:
    the p for which P(X ≤ k | n, p) = alpha, found by bisection (no scipy)."""
    from math import comb
    if k >= n:
        return 1.0
    cdf = lambda p: sum(comb(n, i) * p ** i * (1 - p) ** (n - i) for i in range(k + 1))  # noqa: E731
    lo, hi = k / n, 1.0
    for _ in range(100):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if cdf(mid) > alpha else (lo, mid)
    return hi


def load(directory: Path, pattern: str) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(directory.glob(pattern))]


def step_total(run: dict) -> int | None:
    values = [v for v in (run.get('steps') or {}).values() if isinstance(v, int)]
    return sum(values) if values else None


def main() -> int:
    if len(sys.argv) > 1:
        directory = Path(sys.argv[1])
        directory = directory if directory.is_absolute() else ROOT / directory
    else:
        candidates = sorted(p for p in (ROOT / 'results/evaluation').glob('*') if p.is_dir())
        if not candidates:
            print('no evaluation results in results/evaluation')
            return 1
        directory = candidates[-1]
    baseline = load(directory, 'baseline-*.json')
    treatment = load(directory, 'run-*.json')
    print(f'# ASCAM evaluation analysis\n\nSource: {directory.relative_to(ROOT)} '
          f'({len(baseline)} baseline runs, {len(treatment)} treatment runs)\n')

    # ── 1. baseline ──────────────────────────────────────────────────────────────
    if baseline:
        print('## 1. Baseline scenarios without ASCAM (§3.10.1)\n')
        for code in sorted({r['scenario'] for r in baseline}):
            every = [r for r in baseline if r['scenario'] == code]
            runs = [r for r in every if r.get('assessment')]
            if not runs:
                print(f"### B{code[1:]}: no run completed ({len(every)} runs stopped)\n")
                continue
            print(f"### B{code[1:]}: {runs[0]['title']} ({len(runs)} runs"
                  f"{f', {len(every) - len(runs)} incomplete' if len(every) > len(runs) else ''})\n")
            print('| Query | Kind | Before (HTTP, rows) | After (HTTP, rows) | Error message after |')
            print('|---|---|---|---|---|')
            for name, kind in ((n, v['kind']) for n, v in runs[0]['assessment']['per_query'].items()):
                a = Counter((r['answers_before'][name]['status'], r['answers_before'][name]['n']) for r in runs)
                b = Counter((r['answers_after'][name]['status'], r['answers_after'][name]['n']) for r in runs)
                error = next((r['answers_after'][name]['error'] for r in runs
                              if r['answers_after'][name]['error']), '')
                error = ' '.join((error or '').split())[:110]
                print(f"| {name} | {kind} | {', '.join(f'{s}, {n}' for (s, n), _ in a.most_common())} | "
                      f"{', '.join(f'{s}, {n}' for (s, n), _ in b.most_common())} | {error or '-'} |")
            ep = [r['assessment']['per_query'] for r in runs]
            ratio = statistics.mean(100 * sum(v['execution_preserved'] for v in p.values()) / len(p) for p in ep)
            intact = sum(1 for r in runs if r.get('artifacts_unchanged'))
            print(f'\nMean ExecutionPreserved without ASCAM: {ratio:.1f} %; OBDF artifacts unchanged '
                  f'in {intact}/{len(runs)} runs.\n')

    incomplete = [r for r in baseline if not r.get('assessment')]
    if incomplete:
        print('Incomplete baseline runs: ' + ', '.join(
            f"B{r['scenario'][1:]} run {r['run']} ({r.get('outcome')})" for r in incomplete) + '\n')
    if not treatment:
        return 0

    # ── 2. treatment ─────────────────────────────────────────────────────────────
    scenarios = sorted({r['scenario'] for r in treatment})
    print('## 2. Treatment scenarios with ASCAM (§3.10.2)\n')
    print('| Scenario | Runs | Succeeded | D11 decision as expected | Theoretical expectation met |')
    print('|---|---:|---:|---:|---:|')
    for code in scenarios:
        runs = [r for r in treatment if r['scenario'] == code]
        ok = [r for r in runs if r.get('outcome') in VALID_OUTCOMES]
        correct = sum(1 for r in runs if r.get('as_expected'))
        met = sum(1 for r in ok if r.get('expectations') and all(r['expectations'].values()))
        print(f'| {code.upper()} | {len(runs)} | {len(ok)}/{len(runs)} | {correct}/{len(runs)} | '
              f'{met}/{len(ok)} |')
    print()
    for code in scenarios:
        sample = next((r['expectations'] for r in treatment if r['scenario'] == code and r.get('expectations')), {})
        print(f"- {code.upper()}: {', '.join(sample) or '-'}")

    # ── 3. C_safe ────────────────────────────────────────────────────────────────
    print('\n## 3. Data autonomy: C_safe (§3.11.1)\n')
    config = directory / 'debezium-config.json'
    if config.exists():
        for name, body in json.loads(config.read_text()).items():
            print(f"- `{name}`: table.include.list = {body['table.include.list']} "
                  f"({'only ddl_event_log' if body['only_ddl_event_log'] else 'INCLUDES other tables'})")
    audit = directory / 'kafka-audit.json'
    if audit.exists():
        a = json.loads(audit.read_text())
        if '_error' in a:
            print(f"\nKafka audit failed: {a['_error']}")
        else:
            print(f"\n| N_log | N_prod | Schema changes | Other | C_safe |\n|---:|---:|---:|---:|---|")
            print(f"| {a['N_log']} | {a['N_prod']} | {a['counts']['schema_change']} | "
                  f"{a['counts']['other'] + a['counts']['tombstone']} | "
                  f"{'met' if a['C_safe'] else 'NOT met'} |")
            print('\nPer topic: ' + '; '.join(f'{t}: {v}' for t, v in a['per_topic'].items()))
            print(f"\nColumns carried by log messages: {', '.join(a['log_message_columns'])}")
            if a['production_message_samples']:
                print(f"\nPRODUCTION MESSAGES DETECTED: {a['production_message_samples'][:5]}")

    # ── 4. C_FMI ─────────────────────────────────────────────────────────────────
    print('\n## 4. Faster mapping repair: C_FMI (§3.11.2)\n')
    print('| Scenario | Δt_adapt (ms) | Machine Δt_adapt (ms) | Max (ms) | < 60 s | '
          'N_manual decisions | N_manual repairs |')
    print('|---|---:|---:|---:|---:|---:|---:|')
    for code in scenarios:
        ok = [r for r in treatment if r['scenario'] == code and r.get('outcome') in VALID_OUTCOMES]
        dt = [r.get('dt_adapt_ms') for r in ok if r.get('dt_adapt_ms') is not None]
        machine = [r.get('dt_adapt_machine_ms') for r in ok if r.get('dt_adapt_machine_ms') is not None]
        manual = sorted({r.get('n_manual', 0) for r in ok})
        print(f"| {code.upper()} | {quartiles(dt)} | {quartiles(machine)} | {max(dt) if dt else '-'} | "
              f"{sum(1 for v in dt if v < DT_LIMIT)}/{len(dt)} | {', '.join(map(str, manual)) or '-'} | 0 |")
    print('\nΔt_adapt = t_end − t_start (eq. 3.16): t_start is when the DDL runs; t_end is when the new '
          'artifacts serve queries and have been verified, that is, the end of the switch step with '
          'blue-green (ADR-0022) or the end of the verify step after the reload with the restart '
          'strategy. Machine Δt_adapt leaves out the time the administrator took to decide.\n')
    print('| Scenario | Detection (ms) | ' + ' | '.join(STEPS) + ' |')
    print('|---|---:|' + '---:|' * len(STEPS))
    for code in scenarios:
        ok = [r for r in treatment if r['scenario'] == code and r.get('outcome') in VALID_OUTCOMES]
        cells = [quartiles([(r.get('steps') or {}).get(n) for r in ok]) for n in STEPS]
        print(f"| {code.upper()} | {quartiles([r.get('detection_ms') for r in ok])} | " + ' | '.join(cells) + ' |')

    # ── 4b. endpoint availability ────────────────────────────────────────────────
    monitored = [r for r in treatment if (r.get('availability') or {}).get('n_probe')]
    if monitored:
        print('\n### SPARQL endpoint availability during adaptation (ADR-0022)\n')
        print('Sequential probes about every 0.5 s from the DDL until the execution ends, using a '
              'query that no scenario touches.\n')
        print('| Scenario | Monitored runs | Runs with no failed probe | Availability (median, min) | '
              'Longest outage, max (ms) |')
        print('|---|---:|---:|---:|---:|')
        for code in scenarios:
            k = [r['availability'] for r in monitored if r['scenario'] == code]
            if not k:
                continue
            percent = [x['availability_percent'] for x in k]
            print(f"| {code.upper()} | {len(k)} | {sum(1 for x in k if x['n_failed'] == 0)} | "
                  f"{statistics.median(percent):.1f} % ({min(percent):.1f} %) | "
                  f"{max(x['longest_outage_ms'] for x in k)} |")

    # ── 5. semantic preservation ─────────────────────────────────────────────────
    print('\n## 5. Semantic preservation (§3.11.3)\n')
    print('| Scenario | Metric | Mean PreservationRatio | Runs at 100 % |')
    print('|---|---|---:|---:|')
    for code in scenarios:
        ok = [r for r in treatment if r['scenario'] == code and r.get('assessment')]
        if not ok:
            continue
        ratio = [r['assessment']['preservation_ratio'] for r in ok]
        print(f"| {code.upper()} | {ok[0]['assessment']['metric']} | {statistics.mean(ratio):.1f} % | "
              f"{sum(1 for v in ratio if v == 100.0)}/{len(ok)} |")
    print()
    for code in scenarios:
        ok = [r for r in treatment if r['scenario'] == code and r.get('assessment')]
        if not ok:
            continue
        metric = ok[0]['assessment']['metric']
        print(f'**{code.upper()}** per query ({metric}):')
        for name, v in ok[0]['assessment']['per_query'].items():
            value = sum(r['assessment']['per_query'][name][metric] for r in ok)
            rows = Counter((r['assessment']['per_query'][name]['rows_before'],
                            r['assessment']['per_query'][name]['rows_after']) for r in ok).most_common(1)[0][0]
            print(f'- {name} ({v["kind"]}): {value}/{len(ok)}; rows before → after: {rows[0]} → {rows[1]}')
        print()

    # ── 6. comparison ────────────────────────────────────────────────────────────
    if baseline:
        print('## 6. Baseline compared with treatment\n')
        # EP, RP and PR for both modes. PR uses RP for P-003 and EP for the other patterns
        # (queries.score), so PR without ASCAM is computed by the same rule.
        print('| Operation | EP without | RP without | PR without | EP with | RP with | PR with |')
        print('|---|---:|---:|---:|---:|---:|---:|')
        for code in scenarios:
            def mean(runs, key):
                p = [r['assessment']['per_query'] for r in runs if r.get('assessment')]
                return (statistics.mean(100 * sum(v[key] for v in x.values()) / len(x) for x in p)
                        if p else None)
            base = [r for r in baseline if r['scenario'] == code]
            test = [r for r in treatment if r['scenario'] == code]
            b_ep, b_rp = mean(base, 'execution_preserved'), mean(base, 'result_preserved')
            ep, rp = mean(test, 'execution_preserved'), mean(test, 'result_preserved')
            rename = any(r.get('assessment', {}).get('metric') == 'result_preserved' for r in test)
            b_pr, pr = (b_rp, rp) if rename else (b_ep, ep)
            f = lambda v: '-' if v is None else f'{v:.1f} %'               # noqa: E731
            print(f'| {code.upper()} / B{code[1:]} | {f(b_ep)} | {f(b_rp)} | {f(b_pr)} | '
                  f'{f(ep)} | {f(rp)} | {f(pr)} |')

    # ── 7. anomalies ─────────────────────────────────────────────────────────────
    anomalies = [r for r in treatment if r.get('outcome') not in VALID_OUTCOMES or not r.get('as_expected')
                 or not (r.get('expectations') and all(r['expectations'].values()))]
    # ── reliability ──────────────────────────────────────────────────────────────
    if treatment:
        print('\n### Reliability (one-sided 95 % Clopper-Pearson upper bound on the failure probability)\n')
        print('| Group | Failed / n | Upper bound |')
        print('|---|---:|---:|')
        executed = [r for r in treatment if r.get('outcome') not in NO_EXECUTION]
        groups = [('D11 decision (all runs)', [r for r in treatment], lambda r: r.get('as_expected')),
                  ('execution (executed runs)', executed, lambda r: r.get('outcome') == 'succeeded')]
        groups += [(f'execution {k.upper()}', [r for r in executed if r['scenario'] == k],
                    lambda r: r.get('outcome') == 'succeeded')
                   for k in scenarios if any(r['scenario'] == k for r in executed)]
        for name, runs, success in groups:
            failed = sum(1 for r in runs if not success(r))
            print(f'| {name} | {failed}/{len(runs)} | {100 * upper_bound(failed, len(runs)):.1f} % |')

    print(f'\n## 7. Anomalies ({len(anomalies)} of {len(treatment)} treatment runs)\n')
    for r in anomalies:
        print(f"- {r['scenario']} run {r['run']}: outcome={r.get('outcome')}, decision={r.get('decision')}, "
              f"expectations={r.get('expectations')}, failure={(r.get('execution') or {}).get('failure')}")
    if not anomalies:
        print('None.')

    # The start state of every run must be clean: a query that already fails BEFORE the DDL
    # points at changes left by another scenario (evaluation 20260930T091536), so that run is
    # not valid.
    tainted = [r for r in baseline + treatment
               if any(not v.get('ok') for v in (r.get('answers_before') or {}).values())]
    print(f'\n## 8. Runs with a tainted start state ({len(tainted)} of '
          f'{len(baseline) + len(treatment)} runs)\n')
    if not tainted:
        print('None: every query succeeded before the DDL ran.')
    for r in tainted:
        failed = [n for n, v in r['answers_before'].items() if not v.get('ok')]
        print(f"- {r['mode']} {r['scenario']} run {r['run']}: failed before the DDL: {', '.join(failed)}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
