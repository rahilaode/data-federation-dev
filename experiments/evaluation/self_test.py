"""
Self-test of the evaluation harness, with no Docker and no OBDF.

Checks that every module really LOADS (not just compiles), that every scenario has all its
attributes, and that the analysis runs end to end over synthetic results built with the same
scoring functions as a real evaluation.

Run from the repository root:  python3 experiments/evaluation/self_test.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))


def main() -> int:
    import analyze  # noqa: F401
    import evaluate  # noqa: F401
    import queries
    import scenarios

    for code, sc in scenarios.SCENARIOS.items():
        for attribute in ('source', 'table', 'column', 'pattern', 'decision', 'apply', 'restore'):
            assert getattr(sc, attribute), f'{code}: attribute {attribute} is empty'
        assert code in queries.Q and len(queries.Q[code]) >= 3, f'{code}: query set too small'
        kinds = {j for j, _ in queries.Q[code].values()}
        assert {'affected', 'neighbour', 'control'} <= kinds, f'{code}: query kinds incomplete'
    print(f'modules loaded; {len(scenarios.SCENARIOS)} scenarios, each with its Q_k')

    # In the base state a restore must send no DDL and must return 'clean'; otherwise the
    # harness waits for a restore event that never comes.
    base_state = {'email': '0', 'tipe_program': '1', 'tgl_lahir_ktp': '0',
                  'kode_pos': '0', 'status_hidup': '1', 'judul_program': '0',
                  'no_kartu_keluarga': '1', 'diubah_oleh': '1', 'periode_selesai': '1'}
    for code, sc in scenarios.SCENARIOS.items():
        commands: list[str] = []

        def fake(sql, commands=commands):
            commands.append(sql)
            if 'information_schema' in sql:
                return next(v for k, v in base_state.items() if k in sql)
            return 'CHANGED'
        original = (scenarios.pg, scenarios.my)
        scenarios.pg = scenarios.my = fake
        try:
            result = sc.restore()
        finally:
            scenarios.pg, scenarios.my = original
        ddl = [q for q in commands if q.lstrip().upper().startswith(('ALTER', 'UPDATE'))]
        assert result == 'clean' and not ddl, f'{code}: restore in the base state -> {result!r} {ddl}'
    print('a restore in the base state sends no DDL for any scenario')

    # After a treatment, on the other hand, the restore MUST run DDL and must not read as
    # "nothing changed", even when the client (like MySQL) prints nothing.
    treated_state = {'email': '1', 'tipe_program': '0', 'tgl_lahir_ktp': '1',
                     'kode_pos': '1', 'status_hidup': '0', 'judul_program': '1',
                     'no_kartu_keluarga': '0', 'diubah_oleh': '0', 'periode_selesai': '0'}
    for code, sc in scenarios.SCENARIOS.items():
        commands = []

        def fake(sql, commands=commands):
            commands.append(sql)
            if 'information_schema' in sql:
                return next(v for k, v in treated_state.items() if k in sql)
            return ''                                   # mimics an ALTER that succeeds with no output
        original = (scenarios.pg, scenarios.my)
        scenarios.pg = scenarios.my = fake
        try:
            result = sc.restore()
        finally:
            scenarios.pg, scenarios.my = original
        ddl = [q for q in commands if q.lstrip().upper().startswith('ALTER')]
        assert ddl and result not in ('clean', ''), f'{code}: restore after the treatment -> {result!r}'
    print('a restore after the treatment always runs DDL and never reads as clean')

    # Preparing a run restores the columns of ALL scenarios (not only the one that runs),
    # because in the 3 x 2 design two scenarios can share a table (evaluation 20260930T091536).
    called = []
    original = {k: (s.restore, s.prepare) for k, s in scenarios.SCENARIOS.items()}
    original_ids = evaluate.event_ids
    try:
        for k, s in scenarios.SCENARIOS.items():
            s.restore = (lambda k=k: called.append(k) or 'clean')
            s.prepare = None
        evaluate.event_ids = lambda: set()
        ok, restored = evaluate.restore_all()
    finally:
        for k, s in scenarios.SCENARIOS.items():
            s.restore, s.prepare = original[k]
        evaluate.event_ids = original_ids
    assert sorted(called) == sorted(scenarios.SCENARIOS) and ok and restored == [], called
    print('preparing a run restores the columns of every scenario, not only the one that runs')

    config = evaluate.check_debezium_config()
    assert all(v['only_ddl_event_log'] for v in config.values()), config
    print('the Debezium configuration watches ddl_event_log only')

    def ok(n, prefix='x'):
        return {'status': 200, 'ok': True, 'error': None, 'n': n,
                'rows': [f'{prefix}{i}' for i in range(n)], 'duration_ms': 1}

    def failed(message):
        return {'status': 500, 'ok': False, 'error': message, 'n': None, 'rows': None, 'duration_ms': 1}

    temp = Path(tempfile.mkdtemp(dir=ROOT / 'results'))
    try:
        for code in scenarios.SCENARIOS:
            pattern = scenarios.SCENARIOS[code].pattern
            affected = queries.affected(code)
            before = {q: ok(0 if (q == affected and pattern == 'P-001') else 4) for q in queries.Q[code]}
            broken = dict(before)
            if pattern != 'P-001':
                broken[affected] = failed('column does not exist')
            (temp / f'baseline-{code}-01.json').write_text(json.dumps({
                'mode': 'baseline', 'scenario': code, 'baseline_code': 'b' + code[1:],
                'title': code, 'run': 1,
                'answers_before': before, 'answers_after': broken,
                'assessment': queries.score(code, before, broken), 'artifacts_unchanged': True}))
            after = dict(before)
            if pattern == 'P-001':
                after[affected] = ok(3, 'e')
            if pattern == 'P-002':
                after[affected] = ok(0)
            not_adapted = scenarios.SCENARIOS[code].decision == "notify"
            if not_adapted:                             # A009: not adapted, the view stays broken
                after = dict(broken)
            assessment = queries.score(code, before, after)
            expectations = queries.expectations(code, after, assessment)
            assert all(expectations.values()), (code, expectations)
            (temp / f'run-{code}-01.json').write_text(json.dumps({
                'mode': 'treatment', 'scenario': code, 'title': code, 'run': 1,
                'outcome': 'notified' if not_adapted else 'succeeded',
                'availability': {'n_probe': 10, 'n_failed': 0, 'availability_percent': 100.0,
                                 'longest_outage_ms': 0, 'probe_interval_s': 0.5},
                'decision': scenarios.SCENARIOS[code].decision, 'pattern': scenarios.SCENARIOS[code].pattern,
                'as_expected': True, 'n_manual': 0, 'decision_ms': 0, 'detection_ms': 1,
                'dt_adapt_ms': None if not_adapted else 1, 'dt_adapt_machine_ms': None if not_adapted else 1,
                'steps': {} if not_adapted else {'validate': 1},
                'answers_before': before, 'answers_after': after, 'assessment': assessment,
                'expectations': expectations}))
        result = subprocess.run([sys.executable, str(HERE / 'analyze.py'), str(temp)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        for section in ('## 1.', '## 2.', '## 4.', '### SPARQL endpoint availability', '## 5.', '## 6.', '## 7.'):
            assert section in result.stdout, f'section {section} is missing'
        assert 'None.' in result.stdout.split('## 7.')[1], result.stdout.split('## 7.')[1][:600]
        result = subprocess.run([sys.executable, str(HERE / 'extract.py'), str(temp)],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        brief = json.loads((temp / 'summary' / 'summary.json').read_text())['scenarios']
        assert all(v['succeeded'] == 1 for v in brief.values()), brief
        print('extract.py counts the notified run (A009) as a valid outcome')
        print('the analysis runs end to end over synthetic results; theoretical expectations are met')
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    print('\nHarness self-test PASSED.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
