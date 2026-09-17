#!/usr/bin/env python3
"""
Runner eksperimen ASCAM: skenario baseline (B00x, tanpa ASCAM) dan perlakuan
(A00x, dengan ASCAM), berulang, pada satu commit yang sama.

CATATAN: skrip ini dibuat untuk mesin adaptasi ITERASI 1 (mapping .obda, kontainer
ascam-adaptive-engine) yang kini dipensiunkan (ADR-0010). Hasilnya tersimpan di results/
sebagai bukti iterasi pertama. Versi untuk arsitektur baru (Knowledge, Orchestrator,
Executor, mapping R2RML) disusun ulang pada fase evaluasi.

Prosedur tiap run (sama dengan prosedur manual yang telah divalidasi):
  1. pastikan artefak F bersih (git), lalu ./run.sh (stack dari nol)
  2. tunggu stack sehat (4 task Debezium RUNNING, Ontop menjawab, ASCAM mendengarkan)
  3. baseline: hentikan kontainer ASCAM
  4. rekam kueri SEBELUM perubahan skema
  5. catat t_start (ns), eksekusi DDL di sumber, catat t_ddl_done
  6. perlakuan: tunggu entri baru di adaptation_log.jsonl
     baseline : tunggu BASELINE_WAIT detik
  7. rekam kueri SESUDAH; untuk skenario ADD, isi kolom baru dengan DML lalu kueri lagi
  8. simpan bukti (log, artefak, diff semantik ontologi), tulis run.json
  9. kembalikan artefak F (git restore)

Pemakaian (dari root repository):
  python3 experiments/run_experiment.py A003 --reps 1
  python3 experiments/run_experiment.py A001 A002 A003 B001 B002 B003 --reps 5 --shuffle --seed 42
Hasil: results/runs/<skenario>/run<NN>_<waktu>/
"""
import argparse
import json
import random
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUERIES = ROOT / 'experiments' / 'queries'
OUT = ROOT / 'results' / 'runs'
ENDPOINT = 'http://localhost:8080/sparql'
ASCAM = 'ascam-adaptive-engine'
ARTIFACTS = {
    'vdb': 'setup/data-federation/deployments/government-vdb.xml',
    'mapping': 'setup/vkg-system/config/mapping.obda',
    'ontology': 'setup/vkg-system/config/ontology_file.ttl',
}
HEALTH_TIMEOUT = 300      # detik
ADAPT_TIMEOUT = 240       # detik
BASELINE_WAIT = 15        # detik; > interval polling MySQL (10 s)

PG = ['docker', 'exec', 'datasources-pgsql', 'psql', '-U', 'postgres', '-d', 'kemensos', '-c']
MY = ['docker', 'exec', 'datasources-mysql', 'mysql', '-umysql', '-pmysql', 'dukcapil', '-e']

_ADD = dict(src=PG, ddl='ALTER TABLE public.penerima_manfaat ADD COLUMN email varchar(100);',
            queries=['a001_existing', 'a001_new_property'],
            dml="UPDATE penerima_manfaat SET email='siti.rahma@example.com' WHERE penerima_id=1;",
            dml_query='a001_new_property', pattern='P-001')
_DROP = dict(src=PG, ddl='ALTER TABLE public.program_bansos DROP COLUMN tipe_program;',
             queries=['a002_optional', 'a002_required'], pattern='P-002')
_RENAME = dict(src=MY, ddl='ALTER TABLE master_penduduk RENAME COLUMN tanggal_lahir TO tgl_lahir_ktp;',
               queries=['a003_rename'], pattern='P-003')
SCENARIOS = {
    'A001': {**_ADD, 'ascam': True},  'B001': {**_ADD, 'ascam': False},
    'A002': {**_DROP, 'ascam': True}, 'B002': {**_DROP, 'ascam': False},
    'A003': {**_RENAME, 'ascam': True}, 'B003': {**_RENAME, 'ascam': False},
}


# ── utilitas ────────────────────────────────────────────────
def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}', flush=True)


def sh(cmd, check=True, capture=True, **kw):
    r = subprocess.run(cmd, text=True, capture_output=capture, **kw)
    if check and r.returncode != 0:
        raise RuntimeError(f'gagal ({r.returncode}): {" ".join(map(str, cmd))}\n{r.stderr}')
    return r


def git(*args):
    return sh(['git', '-C', str(ROOT), *args]).stdout.strip()


def sparql(query_text, timeout=60):
    """Kembalikan (http_status, body_text)."""
    url = ENDPOINT + '?' + urllib.parse.urlencode({'query': query_text})
    req = urllib.request.Request(url, headers={'Accept': 'application/sparql-results+json'})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode('utf-8')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', errors='replace')
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        return None, str(e)


def rows(body):
    data = json.loads(body)
    vars_ = data['head']['vars']
    return vars_, sorted((tuple((v, b[v]['value'] if v in b else None) for v in vars_)
                          for b in data['results']['bindings']), key=repr)


def run_query(name, dest: Path):
    text = (QUERIES / f'{name}.rq').read_text(encoding='utf-8')
    status, body = sparql(text)
    dest.write_text(body, encoding='utf-8')
    try:
        n = len(rows(body)[1]) if status == 200 else None
    except (ValueError, KeyError):
        n = None
    return {'http': status, 'rows': n, 'file': dest.name}


def compare(before: Path, after: Path):
    try:
        va, a = rows(before.read_text()); vb, b = rows(after.read_text())
    except (ValueError, KeyError, TypeError):
        return None
    return va == vb and a == b


def ascam_log_lines():
    r = sh(['docker', 'exec', ASCAM, 'sh', '-c',
            'cat /app/state/adaptation_log.jsonl 2>/dev/null || true'])
    return [l for l in r.stdout.splitlines() if l.strip()]


# ── tahapan ─────────────────────────────────────────────────
def ensure_clean_artifacts():
    dirty = git('status', '--porcelain', '--untracked-files=no')
    if dirty:
        raise SystemExit(f'Berkas terlacak git tidak bersih, hentikan dulu:\n{dirty}')


def restore_artifacts():
    sh(['git', '-C', str(ROOT), 'restore', *ARTIFACTS.values()])


def wait_healthy():
    deadline = time.time() + HEALTH_TIMEOUT
    while time.time() < deadline:
        ok_conn = ok_ontop = ok_ascam = False
        try:
            with urllib.request.urlopen('http://localhost:8083/connectors?expand=status', timeout=5) as r:
                st = json.load(r)
            states = [t['state'] for c in st.values() for t in [c['status']['connector'], *c['status']['tasks']]]
            ok_conn = len(states) == 4 and all(s == 'RUNNING' for s in states)
        except Exception:
            pass
        status, _ = sparql('ASK { ?s ?p ?o }', timeout=5)
        ok_ontop = status == 200
        r = sh(['docker', 'logs', ASCAM], check=False)
        ok_ascam = 'Mendengarkan event DDL' in (r.stdout + r.stderr)
        if ok_conn and ok_ontop and ok_ascam:
            return True
        time.sleep(5)
    raise RuntimeError('stack tidak sehat dalam batas waktu')


def one_run(sc_id, rep, commit):
    sc = SCENARIOS[sc_id]
    stamp = datetime.now().strftime('%Y%m%dT%H%M%S')
    d = OUT / sc_id / f'run{rep:02d}_{stamp}'
    d.mkdir(parents=True)
    res = {'scenario': sc_id, 'rep': rep, 'commit': commit, 'pattern': sc['pattern'],
           'with_ascam': sc['ascam'], 'ddl': sc['ddl'], 'started': stamp}

    ensure_clean_artifacts()
    log(f'{sc_id} run{rep:02d}: ./run.sh')
    with open(d / 'run_sh.log', 'w') as fh:
        sh([str(ROOT / 'run.sh')], capture=False, stdout=fh, stderr=subprocess.STDOUT, cwd=ROOT)
    wait_healthy()
    if not sc['ascam']:
        sh(['docker', 'stop', ASCAM])
        log('ASCAM dihentikan (baseline)')
    n_log_before = len(ascam_log_lines()) if sc['ascam'] else 0

    res['before'] = {q: run_query(q, d / f'{q}_before.json') for q in sc['queries']}

    log(f'DDL: {sc["ddl"]}')
    t_start = time.time_ns() / 1e9
    ddl = sh([*sc['src'], sc['ddl']], check=False)
    t_ddl_done = time.time_ns() / 1e9
    res.update(t_start=t_start, t_ddl_done=t_ddl_done, ddl_rc=ddl.returncode,
               ddl_output=(ddl.stdout + ddl.stderr).strip())

    if sc['ascam']:
        deadline, record = time.time() + ADAPT_TIMEOUT, None
        while time.time() < deadline:
            lines = ascam_log_lines()
            if len(lines) > n_log_before:
                record = json.loads(lines[-1]); break
            time.sleep(1)
        res['adaptation'] = record
        if record:
            ts = record['timestamps']
            last = ts.get('t_verified', ts.get('t_ontop_ready', ts.get('t_end')))
            res['dt_adapt_eksp'] = round(last - t_start, 3)
            if 't_source' in ts:
                res['dt_ddl_to_capture'] = round(ts['t_source'] - t_start, 3)
        else:
            res['adaptation_timeout'] = True
        log(f'adaptasi: {record["status"] if record else "TIMEOUT"}')
    else:
        time.sleep(BASELINE_WAIT)

    res['after'] = {q: run_query(q, d / f'{q}_after.json') for q in sc['queries']}
    res['identical'] = {q: compare(d / f'{q}_before.json', d / f'{q}_after.json') for q in sc['queries']}

    if sc.get('dml'):
        sh([*sc['src'], sc['dml']], check=False)
        q = sc['dml_query']
        res['after_dml'] = run_query(q, d / f'{q}_after_dml.json')

    # bukti
    if sc['ascam']:
        lg = sh(['docker', 'logs', ASCAM], check=False)
        (d / 'ascam.log').write_text(lg.stdout + lg.stderr)
    for k, rel in ARTIFACTS.items():
        shutil.copy(ROOT / rel, d / f'{k}_after{Path(rel).suffix}')
    changed = git('diff', '--name-only', '--', *ARTIFACTS.values()).splitlines()
    res['artifacts_changed'] = changed
    if sc['ascam'] and ARTIFACTS['ontology'] in changed:
        before_ttl = d / 'ontology_before.ttl'
        before_ttl.write_text(git('show', f'HEAD:{ARTIFACTS["ontology"]}') + '\n')
        for src, dst in ((ROOT / 'experiments/semdiff.py', '/tmp/semdiff.py'),
                         (before_ttl, '/tmp/before.ttl'), (d / 'ontology_after.ttl', '/tmp/after.ttl')):
            sh(['docker', 'cp', str(src), f'{ASCAM}:{dst}'])
        sd = sh(['docker', 'exec', ASCAM, 'python', '/tmp/semdiff.py', '/tmp/before.ttl', '/tmp/after.ttl'],
                check=False).stdout
        (d / 'ontology_semdiff.txt').write_text(sd)
        res['ontology_semdiff'] = sd.splitlines()[0] if sd else None

    restore_artifacts()
    res['finished'] = datetime.now().strftime('%Y%m%dT%H%M%S')
    (d / 'run.json').write_text(json.dumps(res, indent=2, ensure_ascii=False))
    log(f'{sc_id} run{rep:02d} selesai -> {d.relative_to(ROOT)}')
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('scenarios', nargs='+', choices=sorted(SCENARIOS))
    ap.add_argument('--reps', type=int, default=1)
    ap.add_argument('--shuffle', action='store_true', help='acak urutan skenario di tiap ulangan')
    ap.add_argument('--seed', type=int, default=None)
    args = ap.parse_args()

    ensure_clean_artifacts()
    commit = git('rev-parse', 'HEAD')
    seed = args.seed if args.seed is not None else random.randrange(10**6)
    rng = random.Random(seed)
    plan = []
    for rep in range(1, args.reps + 1):
        order = list(args.scenarios)
        if args.shuffle:
            rng.shuffle(order)
        plan += [(s, rep) for s in order]
    OUT.mkdir(parents=True, exist_ok=True)
    meta = {'commit': commit, 'seed': seed, 'shuffle': args.shuffle, 'plan': plan,
            'started_utc': datetime.now(timezone.utc).isoformat()}
    (OUT / f'plan_{datetime.now().strftime("%Y%m%dT%H%M%S")}.json').write_text(json.dumps(meta, indent=2))
    log(f'commit {commit[:7]} | {len(plan)} run | seed {seed}')

    failures = 0
    for i, (s, rep) in enumerate(plan, 1):
        log(f'==== [{i}/{len(plan)}] {s} ulangan {rep} ====')
        try:
            one_run(s, rep, commit)
        except Exception as exc:      # satu run gagal tidak menghentikan seluruh rangkaian
            failures += 1
            log(f'RUN GAGAL: {exc}')
            try:
                restore_artifacts()
            except Exception:
                pass
    log(f'selesai: {len(plan) - failures} berhasil, {failures} gagal')
    sys.exit(1 if failures else 0)


if __name__ == '__main__':
    main()
