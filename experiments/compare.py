#!/usr/bin/env python3
"""
Membandingkan dua answer set SPARQL (format JSON W3C).
Pemakaian: python3 experiments/compare.py <sebelum.json> <sesudah.json>
Perbandingan dilakukan pada multiset baris (variabel -> nilai), bukan teks
berkas, sehingga tidak terpengaruh format/whitespace keluaran endpoint.
Exit code 0 bila identik.
"""
import json, sys
from collections import Counter

def rows(path):
    data = json.load(open(path, encoding='utf-8'))
    vars_ = data['head']['vars']
    out = Counter()
    for b in data['results']['bindings']:
        out[tuple((v, b[v]['value'] if v in b else None) for v in vars_)] += 1
    return vars_, out

(va, a), (vb, b) = rows(sys.argv[1]), rows(sys.argv[2])
print(f'sebelum: {sum(a.values())} baris | sesudah: {sum(b.values())} baris')
if va != vb:
    print(f'variabel berbeda: {va} vs {vb}')
only_a, only_b = a - b, b - a
for r in only_a: print('  hanya sebelum :', dict(r))
for r in only_b: print('  hanya sesudah :', dict(r))
same = va == vb and not only_a and not only_b
print('IDENTIK' if same else 'BERBEDA')
sys.exit(0 if same else 1)
