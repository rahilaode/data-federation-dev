#!/usr/bin/env python3
"""
Merangkum semua results/runs/*/*/run.json menjadi:
  results/runs/summary.csv   (satu baris per run)
  ringkasan per skenario di layar (n, rerata, simpangan baku, min, maks)
Pemakaian: python3 experiments/aggregate.py [--commit <hash>]
"""
import argparse
import csv
import json
import statistics as st
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS = ROOT / 'results' / 'runs'
METRICS = ['dt_adapt_eksp', 'dt_ddl_to_capture', 'capture_to_kafka', 'analyze_plan',
           'vdb_redeploy', 'ontop_reload', 'verify']

ap = argparse.ArgumentParser()
ap.add_argument('--commit', help='hanya run dari commit ini (awal hash cukup)')
args = ap.parse_args()

records = []
for f in sorted(RUNS.glob('*/*/run.json')):
    r = json.loads(f.read_text())
    if args.commit and not r['commit'].startswith(args.commit):
        continue
    ad = r.get('adaptation') or {}
    dur = ad.get('durations', {})
    row = {'scenario': r['scenario'], 'rep': r['rep'], 'commit': r['commit'][:7],
           'with_ascam': r['with_ascam'], 'ascam_status': ad.get('status'),
           'dt_adapt_eksp': r.get('dt_adapt_eksp'), 'dt_ddl_to_capture': r.get('dt_ddl_to_capture'),
           **{k: dur.get(k) for k in METRICS[2:]},
           'artifacts_changed': len(r.get('artifacts_changed', [])),
           'ontology_semdiff': r.get('ontology_semdiff'), 'run_dir': str(f.parent.relative_to(ROOT))}
    for q, v in r['before'].items():
        row[f'{q}_before_http'] = v['http']; row[f'{q}_before_rows'] = v['rows']
    for q, v in r['after'].items():
        row[f'{q}_after_http'] = v['http']; row[f'{q}_after_rows'] = v['rows']
        row[f'{q}_identical'] = r['identical'].get(q)
    if 'after_dml' in r:
        row['after_dml_http'] = r['after_dml']['http']; row['after_dml_rows'] = r['after_dml']['rows']
    records.append(row)

if not records:
    raise SystemExit('tidak ada run.json yang cocok')

fields = []
for row in records:
    fields += [k for k in row if k not in fields]
out = RUNS / 'summary.csv'
with out.open('w', newline='') as fh:
    w = csv.DictWriter(fh, fieldnames=fields)
    w.writeheader(); w.writerows(records)
print(f'{len(records)} run -> {out.relative_to(ROOT)}\n')

def fmt(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    if not vals:
        return '-'
    sd = st.stdev(vals) if len(vals) > 1 else 0.0
    return f'{st.mean(vals):7.3f} ± {sd:6.3f} [{min(vals):.3f}–{max(vals):.3f}]'

for sc in sorted({r['scenario'] for r in records}):
    rs = [r for r in records if r['scenario'] == sc]
    commits = sorted({r['commit'] for r in rs})
    print(f'== {sc}  (n={len(rs)}, commit={",".join(commits)})')
    if rs[0]['with_ascam']:
        ok = sum(r['ascam_status'] == 'SUCCESS' for r in rs)
        print(f'   status SUCCESS: {ok}/{len(rs)}')
        for m in METRICS:
            print(f'   {m:18s} {fmt([r.get(m) for r in rs])}')
    for k in sorted(k for k in rs[0] if k.endswith(('_after_http', '_identical', 'after_dml_rows'))):
        vals = [r.get(k) for r in rs]
        print(f'   {k:32s} {dict((str(v), vals.count(v)) for v in set(vals))}')
    print()
