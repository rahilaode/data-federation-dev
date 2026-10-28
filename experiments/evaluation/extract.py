"""
Extracts the evaluation results into a table per run and a summary per scenario.

Input : a result directory of evaluate.py (default: the latest directory in results/evaluation)
Output: <directory>/summary/
  runs.csv       one row per treatment run (decision, time per step, preservation)
  baseline.csv   one row per query per baseline run (HTTP status, rows, error)
  summary.json   statistics per scenario: median, quartiles, min, max; success;
                 PreservationRatio; baseline ExecutionPreserved; Kafka audit (C_safe)

Quartiles use statistics.quantiles(n=4) (the default 'exclusive' method), as analyze.py does,
so the numbers are the same as in the analysis report.

Usage (from the repository root):
  python3 experiments/evaluation/extract.py
  python3 experiments/evaluation/extract.py results/evaluation/20260924T141111
"""
import csv
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# restart (ADR-0001): ... switch, reload_ontop, verify; blue-green (ADR-0022): ... start_ontop,
# verify, switch. A step that a strategy does not have is left empty.
STEPS = ['deploy_vdb', 'validate', 'start_ontop', 'reload_ontop', 'verify', 'switch', 'sync']
# Measured components of Δt_adapt (sync happens after t_end); missing ones are ignored
DT_COMPONENTS = ['deploy_vdb', 'validate', 'start_ontop', 'reload_ontop', 'verify', 'switch']
OPERATION = {'a001': 'ADD', 'a002': 'DROP', 'a003': 'RENAME',
             'a004': 'ADD', 'a005': 'DROP', 'a006': 'RENAME',
             'a007': 'DROP (view)', 'a008': 'DROP (view)', 'a009': 'DROP (view expression)'}
DBMS = {'a001': 'PostgreSQL', 'a002': 'PostgreSQL', 'a006': 'PostgreSQL',
        'a003': 'MySQL', 'a004': 'MySQL', 'a005': 'MySQL',
        'a007': 'PostgreSQL', 'a008': 'MySQL', 'a009': 'PostgreSQL'}
# Valid outcomes without an execution: 'notified' for the scenario that must not be adapted
# (A009, ADR-0024); 'rejected' is kept so that older results (A009 plan rejected) still read.
VALID_OUTCOMES = {'succeeded', 'notified', 'rejected'}
NO_EXECUTION = {'notified', 'rejected'}


def stats(values: list) -> dict | None:
    values = sorted(v for v in values if isinstance(v, (int, float)))
    if not values:
        return None
    result = {'n': len(values), 'median': statistics.median(values), 'min': values[0], 'max': values[-1]}
    if len(values) >= 4:
        q1, _, q3 = statistics.quantiles(values, n=4)
        result.update(q1=q1, q3=q3)
    else:
        result.update(q1=None, q3=None)
    return result


def pick_directory() -> Path:
    if len(sys.argv) > 1:
        d = Path(sys.argv[1])
        return d if d.is_absolute() else ROOT / d
    candidates = sorted(p for p in (ROOT / 'results/evaluation').glob('*') if p.is_dir())
    if not candidates:
        sys.exit('no result directory in results/evaluation')
    return candidates[-1]


def main() -> int:
    directory = pick_directory()
    runs = [json.loads(p.read_text()) for p in sorted(directory.glob('run-*.json'))]
    baseline = [json.loads(p.read_text()) for p in sorted(directory.glob('baseline-*.json'))]
    if not runs and not baseline:
        sys.exit(f'no run files in {directory}')
    out = directory / 'summary'
    out.mkdir(exist_ok=True)

    # ── runs.csv ──────────────────────────────────────────────────────────────
    columns = ['scenario', 'operation', 'run', 'outcome', 'decision', 'pattern', 'as_expected',
               'expectations_met', 'n_manual', 'decision_ms', 'detection_ms', 'dt_adapt_ms',
               'dt_adapt_machine_ms', 'adaptation_ms', *[f'{n}_ms' for n in STEPS],
               'metric', 'preservation_ratio', 'availability_percent', 'longest_outage_ms']
    with open(out / 'runs.csv', 'w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for r in runs:
            steps = r.get('steps') or {}
            assessment = r.get('assessment') or {}
            expectations = r.get('expectations')
            writer.writerow({
                'scenario': r['scenario'], 'operation': OPERATION.get(r['scenario'], ''), 'run': r['run'],
                'outcome': r.get('outcome'), 'decision': r.get('decision'), 'pattern': r.get('pattern'),
                'as_expected': r.get('as_expected'),
                'expectations_met': all(expectations.values()) if expectations else None,
                'n_manual': r.get('n_manual'), 'decision_ms': r.get('decision_ms'),
                'detection_ms': r.get('detection_ms'), 'dt_adapt_ms': r.get('dt_adapt_ms'),
                'dt_adapt_machine_ms': r.get('dt_adapt_machine_ms'), 'adaptation_ms': r.get('adaptation_ms'),
                **{f'{n}_ms': steps.get(n) for n in STEPS},
                'metric': assessment.get('metric'), 'preservation_ratio': assessment.get('preservation_ratio'),
                'availability_percent': (r.get('availability') or {}).get('availability_percent'),
                'longest_outage_ms': (r.get('availability') or {}).get('longest_outage_ms'),
            })

    # ── baseline.csv ──────────────────────────────────────────────────────────
    with open(out / 'baseline.csv', 'w', newline='', encoding='utf-8') as file:
        writer = csv.writer(file)
        writer.writerow(['scenario', 'run', 'query', 'kind', 'status_before', 'rows_before',
                         'status_after', 'rows_after', 'error_after'])
        for r in baseline:
            if not r.get('assessment'):
                continue
            for name, value in r['assessment']['per_query'].items():
                after = r['answers_after'][name]
                error = ' '.join((after.get('error') or '').split())[:200]
                writer.writerow([r['baseline_code'], r['run'], name, value['kind'],
                                 r['answers_before'][name]['status'], value['rows_before'],
                                 after['status'], value['rows_after'], error])

    # ── summary.json ──────────────────────────────────────────────────────────
    summary = {'source': str(directory.relative_to(ROOT)), 'scenarios': {}}
    for code in sorted({r['scenario'] for r in runs} | {r['scenario'] for r in baseline}):
        every = [r for r in runs if r['scenario'] == code]
        ok = [r for r in every if r.get('outcome') in VALID_OUTCOMES]
        s = {'operation': OPERATION.get(code, code), 'dbms': DBMS.get(code), 'runs': len(every),
             'succeeded': len(ok)}
        if every:
            s['decision_as_expected'] = sum(1 for r in every if r.get('as_expected'))
            s['expectations_met'] = sum(1 for r in ok if r.get('expectations') and all(r['expectations'].values()))
            s['decisions'] = sorted({r.get('decision') for r in ok if r.get('decision')})
            s['n_manual_decisions'] = sorted({r.get('n_manual', 0) for r in ok})
            s['times_ms'] = {
                'detection': stats([r.get('detection_ms') for r in ok]),
                'dt_adapt': stats([r.get('dt_adapt_ms') for r in ok]),
                'dt_adapt_machine': stats([r.get('dt_adapt_machine_ms') for r in ok]),
                **{n: stats([(r.get('steps') or {}).get(n) for r in ok]) for n in STEPS},
            }
            w = s['times_ms']
            components = [n for n in DT_COMPONENTS if w.get(n)]
            if w['dt_adapt'] and w['detection'] and components:
                # Gap from detection to the start of the execution (ingestion, planning, pick-up),
                # derived from the medians
                s['times_ms']['derived_gap_median'] = (
                    w['dt_adapt']['median'] - w['detection']['median']
                    - sum(w[n]['median'] for n in components))
            s['under_60s'] = sum(1 for r in ok if (r.get('dt_adapt_ms') or 1e9) < 60_000)
            ratio = [r['assessment']['preservation_ratio'] for r in ok if r.get('assessment')]
            s['preservation'] = {
                'metric': ok[0]['assessment']['metric'] if ok and ok[0].get('assessment') else None,
                'mean': statistics.mean(ratio) if ratio else None,
                'runs_100': sum(1 for v in ratio if v == 100.0)}
            available = [r['availability'] for r in every if (r.get('availability') or {}).get('n_probe')]
            s['availability'] = {
                'runs_without_failed_probe': sum(1 for k in available if k['n_failed'] == 0),
                'runs_monitored': len(available),
                'percent': stats([k['availability_percent'] for k in available]),
                'longest_outage_ms': stats([k['longest_outage_ms'] for k in available])}
            s['anomalies'] = [{'run': r['run'], 'outcome': r.get('outcome')} for r in every
                              if r.get('outcome') not in VALID_OUTCOMES]
        base = [r for r in baseline if r['scenario'] == code and r.get('assessment')]
        if base:
            ep = [100 * sum(v['execution_preserved'] for v in r['assessment']['per_query'].values())
                  / len(r['assessment']['per_query']) for r in base]
            s['baseline'] = {'runs': len(base), 'execution_preserved_mean': statistics.mean(ep)}
        summary['scenarios'][code] = s

    audit = directory / 'kafka-audit.json'
    if audit.exists():
        a = json.loads(audit.read_text())
        summary['c_safe'] = {k: a.get(k) for k in ('N_log', 'N_prod', 'C_safe', 'per_topic', 'counts')}
    (out / 'summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    # ── short summary on screen ───────────────────────────────────────────────
    print(f'Source: {summary["source"]}')
    for code, s in summary['scenarios'].items():
        w = s.get('times_ms', {})
        dt = w.get('dt_adapt') or {}
        print(f"  {code} {s['operation']:6s} succeeded {s['succeeded']}/{s['runs']}"
              f"  median Δt_adapt {dt.get('median', 0) / 1000:.2f} s"
              f" (IQR {(dt.get('q1') or 0) / 1000:.2f}–{(dt.get('q3') or 0) / 1000:.2f})"
              f"  max {dt.get('max', 0) / 1000:.2f} s")
    if 'c_safe' in summary:
        c = summary['c_safe']
        print(f"  C_safe: N_log={c['N_log']} N_prod={c['N_prod']} met={c['C_safe']}")
    print(f'Written to {out.relative_to(ROOT)}/: runs.csv, baseline.csv, summary.json')
    return 0


if __name__ == '__main__':
    sys.exit(main())
