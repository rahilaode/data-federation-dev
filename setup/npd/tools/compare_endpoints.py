"""Runs the NPD benchmark queries on two SPARQL endpoints and compares the answers.

Usage: python3 tools/compare_endpoints.py <reference endpoint> <tested endpoint> <queries dir> [timeout s]
e.g.   python3 tools/compare_endpoints.py http://localhost:28081/sparql http://localhost:26082/sparql queries

Answers are compared as multisets of rows (SPARQL results in CSV, header included). A pair that
differs only in the digits of numbers (e.g. AVG computed by Teiid instead of PostgreSQL) is
reported as "equal up to precision" (relative difference below 1e-9).
"""
import csv, io, sys, time, urllib.parse, urllib.request
from collections import Counter
from pathlib import Path

ref_url, test_url, qdir = sys.argv[1:4]
timeout = float(sys.argv[4]) if len(sys.argv) > 4 else 600
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def ask(url, query):
    req = urllib.request.Request(url, data=urllib.parse.urlencode({'query': query}).encode(),
                                 headers={'Accept': 'text/csv', 'Content-Type': 'application/x-www-form-urlencoded'})
    t = time.time()
    try:
        with opener.open(req, timeout=timeout) as r:
            rows = list(csv.reader(io.StringIO(r.read().decode('utf-8'))))
        return rows[0] if rows else [], rows[1:], time.time() - t, None
    except Exception as e:  # noqa: BLE001
        return None, None, time.time() - t, str(e)[:120]

def norm(v):
    try:
        return ('n', float(v))
    except ValueError:
        return ('s', v)

def close(a, b):
    if len(a) != len(b):
        return False
    ka = sorted(tuple(norm(v) for v in r) for r in a)
    kb = sorted(tuple(norm(v) for v in r) for r in b)
    for ra, rb in zip(ka, kb):
        for (ta, va), (tb, vb) in zip(ra, rb):
            if ta != tb or (ta == 's' and va != vb) or (ta == 'n' and abs(va - vb) > 1e-9 * max(1, abs(va), abs(vb))):
                return False
    return True

queries = sorted(Path(qdir).glob('*.rq'))
if not queries:
    sys.exit(f'no *.rq files in {qdir} (path to the queries folder?)')
bad = 0
print(f'{"query":6} {"rows ref":>9} {"rows test":>9} {"s ref":>7} {"s test":>7}  verdict')
for q in queries:
    text = q.read_text()
    h1, r1, t1, e1 = ask(ref_url, text)
    h2, r2, t2, e2 = ask(test_url, text)
    if e1 or e2:
        verdict = f'ERROR ref={e1} test={e2}'; bad += bool(e2 and not e1)
    elif h1 != h2:
        verdict = 'DIFFERENT header'; bad += 1
    elif Counter(map(tuple, r1)) == Counter(map(tuple, r2)):
        verdict = 'equal'
    elif close(r1, r2):
        verdict = 'equal up to precision'
    else:
        verdict = 'DIFFERENT'; bad += 1
    n1 = len(r1) if r1 is not None else '-'; n2 = len(r2) if r2 is not None else '-'
    print(f'{q.stem:6} {n1:>9} {n2:>9} {t1:7.1f} {t2:7.1f}  {verdict}', flush=True)
print('all answers equal' if not bad else f'{bad} queries differ or fail only on the tested endpoint')
sys.exit(1 if bad else 0)
