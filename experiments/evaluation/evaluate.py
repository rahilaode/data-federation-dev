"""
ASCAM evaluation harness (thesis proposal §3.10–3.11).

Two modes share the queries and the restore procedure, so any difference in behaviour can be
attributed to ASCAM alone (§3.10.2, p. 104):

  baseline   The Executor is paused for the whole run: the DDL is applied to the source with no
             adaptation, then Q_k runs and its HTTP status, error message and result are
             recorded (§3.10.1).
  treatment  The MAPE-K loop runs (ADD is approved by the evaluator, ADR-0021; an unsafe
             DROP/RENAME is only notified, ADR-0024). The D11 decision, Δt_adapt, the step
             breakdown, and the PreservationRatio over Q_k are recorded (§3.11.2–3.11.3).
             Every Kafka message during the treatment is audited for C_safe (§3.11.1).

Every run starts from the same base state (experiments/reset_obdf.py).

Examples:
  python3 experiments/evaluation/evaluate.py --mode both --runs 20 --baseline-runs 5
  python3 experiments/evaluation/evaluate.py --mode baseline --scenario a002 --baseline-runs 1
Results: results/evaluation/<time>/{baseline-*.json, run-*.json, kafka-*.json, debezium-config.json}
Analysis: python3 experiments/evaluation/analyze.py
"""
import argparse
import json
import os
import platform
import random
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import queries                                                         # noqa: E402
from preflight import check, chain_test                                # noqa: E402
from scenarios import SCENARIOS                                        # noqa: E402

KNOWLEDGE = 'http://127.0.0.1:18000'
# Random delay before the DDL. Without it every run starts right after the MySQL monitor scan
# that saw the restore, so the DDL always lands at the same phase of that 10-second cycle and
# MySQL detection is locked at 5.3-7.3 s. A delay drawn from U(0, MAX_DELAY) spreads the DDL
# phase over the whole MySQL scan cycle and the Executor polling interval (5 s).
MAX_DELAY = 10.0
RNG = random.Random()
COMMIT = None
EXECUTOR = 'http://127.0.0.1:18300'
SECRETS = ROOT / 'setup/ascam/knowledge/secrets'
UI = None


# ── helpers ───────────────────────────────────────────────────────────────────
def token(client: str) -> str:
    for line in (SECRETS / 'knowledge_api_tokens').read_text().splitlines():
        if line.strip().startswith(f'{client}:'):
            return line.strip().split(':', 1)[1]
    raise RuntimeError(f'no token for {client}')


def api(path: str, method: str = 'GET', body=None, base: str = KNOWLEDGE, bearer=None):
    data = json.dumps(body).encode() if body is not None else None
    # The evaluator acts as a named administrator and is recorded as such in the audit trail
    headers = {'Content-Type': 'application/json', 'X-ASCAM-User': 'evaluator'}
    bearer = UI if bearer is None else bearer
    if bearer:
        headers['Authorization'] = f'Bearer {bearer}'
    request = urllib.request.Request(f'{base}{path}', data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as exc:
        return {'_http': exc.code, 'detail': exc.read()[:200].decode('utf-8', 'replace')}
    except Exception as exc:                                           # noqa: BLE001
        return {'_error': f'{type(exc).__name__}: {exc}'}


def wait_for(condition, timeout_s: float, interval: float = 1.0):
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(interval)
    return None


def reset() -> None:
    subprocess.run([sys.executable, 'experiments/reset_obdf.py', '--keep-sources'],
                   cwd=ROOT, capture_output=True, text=True)


def control(action: str) -> None:
    api(f'/control/{action}', 'POST', {}, base=EXECUTOR, bearer='')


def quiet(timeout: float = 180.0) -> bool:
    return bool(wait_for(lambda: not any(e['status'] == 'running'
                                         for e in api('/api/v1/obdf/1/executions?limit=10')), timeout))


def supersede_plans() -> list[int]:
    """Plans that were not executed (from a restore step or a baseline run) are marked superseded."""
    superseded = []
    for status in ('approved', 'pending_approval'):
        for plan in api(f'/api/v1/obdf/1/plans?status={status}&limit=50'):
            api(f"/api/v1/plans/{plan['id']}/supersede", 'POST',
                {'note': 'plan from a restore step or a baseline run'})
            superseded.append(plan['id'])
    return superseded


def event_ids() -> set[int]:
    return {e['id'] for e in api('/api/v1/obdf/1/events?limit=200')}


def ms_between(start_iso: str, end_iso: str | None) -> int | None:
    if not end_iso:
        return None
    a = datetime.fromisoformat(start_iso)
    b = datetime.fromisoformat(end_iso.replace('Z', '+00:00'))
    return int((b - a).total_seconds() * 1000)


def restore_all() -> tuple[bool, list[str]]:
    """Restores the columns of ALL scenarios, not only the one about to run.

    In the 3 x 2 design two scenarios can share a table (A002 and A006 on program_bansos, A003
    and A005 on master_penduduk). If each run restored only its own column, what another
    scenario left behind would leak into the next run: in evaluation 20260930T091536 the A002
    and A006 neighbour queries already failed BEFORE the treatment DDL.
    Every restore waits for its own event (see the note on the MySQL monitor in prepare)."""
    detected, restored = True, []
    for other_code, s in SCENARIOS.items():
        if s.prepare:
            s.prepare()
        before = event_ids()
        # Contract: restore() returns 'clean' ONLY when no DDL was run.
        if s.restore() != 'clean':
            restored.append(other_code)
            arrived = wait_for(lambda: next((e for e in api('/api/v1/obdf/1/events?limit=20')
                                             if e['id'] not in before), None), 60.0)
            detected = detected and arrived is not None
            time.sleep(2)
    return detected, restored


def prepare(code: str) -> dict:
    """Base state: the Executor is paused, ALL scenario tables are restored and their restore
    events are awaited, their plans are superseded, then the OBDF is reset. The restore event
    MUST arrive before the treatment DDL: the MySQL monitor compares a snapshot every 10
    seconds, and a change that is undone before the next scan is not seen
    (probe_mysql_monitor.py; anomaly in A003 run 12 of evaluation 20260922T150904). The restore
    step is itself a schema change (threat to validity 1)."""
    control('pause')
    quiet()
    restore_detected, restored = restore_all()
    superseded = supersede_plans()
    reset()
    version = api('/api/v1/obdf/1/versions?limit=1')
    return {'superseded_plans': superseded, 'restore_detected': restore_detected,
            'restored_scenarios': restored,
            'base_version': version[0]['version_no'] if isinstance(version, list) and version else None}


def git(*arguments: str) -> str:
    return subprocess.run(['git', *arguments], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def evaluation_metadata(args, seed: int) -> dict:
    """Identity of the evaluated code and machine. Only tracked files are checked: result files
    and the untracked nohup log do not change the code that runs."""
    def read(path: str) -> str:
        try:
            return Path(path).read_text()
        except OSError:
            return ''
    cpu = next((b.split(':', 1)[1].strip() for b in read('/proc/cpuinfo').splitlines()
                if b.startswith('model name')), platform.processor())
    mem_kb = next((int(b.split()[1]) for b in read('/proc/meminfo').splitlines()
                   if b.startswith('MemTotal:')), None)
    return {'commit': git('rev-parse', 'HEAD'),
            'branch': git('rev-parse', '--abbrev-ref', 'HEAD'),
            'tracked_changes': git('status', '--porcelain', '--untracked-files=no'),
            'arguments': vars(args), 'delay_seed': seed, 'max_delay_s': MAX_DELAY,
            'started': datetime.now(timezone.utc).isoformat(),
            'machine': {'cpu': cpu, 'n_cpu': os.cpu_count(),
                        'ram_gib': round(mem_kb / 1048576, 1) if mem_kb else None,
                        'os': platform.platform(), 'python': platform.python_version()}}


def random_delay(record: dict) -> None:
    delay = round(RNG.uniform(0.0, MAX_DELAY), 3)
    record['random_delay_s'] = delay
    time.sleep(delay)


class AvailabilityMonitor(threading.Thread):
    """Sends a light query to the SPARQL endpoint at a fixed interval during an adaptation.

    Measures the ADR-0022 claim that the endpoint is not interrupted by an adaptation: every
    probe is recorded as success or failure, then summarised as the percentage of successful
    probes and the longest failure interval. Probes run one after another (not in parallel), so
    the extra load on Ontop is small."""

    def __init__(self, interval: float = 0.5, timeout: float = 5.0):
        super().__init__(daemon=True)
        self.interval, self.timeout = interval, timeout
        self.probe: list[tuple[float, bool]] = []
        self._stop_event = threading.Event()

    def run(self) -> None:
        start = time.perf_counter()
        while not self._stop_event.is_set():
            result = queries.run(queries.AVAILABILITY, timeout=self.timeout)
            self.probe.append((time.perf_counter() - start, bool(result['ok'])))
            self._stop_event.wait(self.interval)

    def summary(self) -> dict:
        self._stop_event.set()
        self.join(timeout=self.timeout + 2)
        # A failure interval runs from the first failed probe to the next successful one (an
        # upper bound on how long the endpoint did not answer); an interval that has not
        # recovered is counted up to the last probe.
        failed, longest, first = 0, 0.0, None
        for t, ok in self.probe:
            if not ok:
                failed += 1
                first = t if first is None else first
            elif first is not None:
                longest, first = max(longest, t - first), None
        if first is not None:
            longest = max(longest, self.probe[-1][0] - first)
        n = len(self.probe)
        return {'n_probe': n, 'n_failed': failed,
                'availability_percent': round(100.0 * (n - failed) / n, 2) if n else None,
                'longest_outage_ms': int(longest * 1000),
                'probe_interval_s': self.interval}


class SetupFailed(RuntimeError):
    """The monitor did not detect the restore: its snapshot is out of step with the reset OBDF,
    so this run and the runs after it are not valid."""


# ── baseline mode (§3.10.1) ───────────────────────────────────────────────────
def one_baseline(code: str, number: int, observe_delay: float) -> dict:
    sc = SCENARIOS[code]
    record = {'mode': 'baseline', 'scenario': code, 'baseline_code': 'b' + code[1:],
              'title': sc.title, 'run': number, 'commit': COMMIT,
              'started': datetime.now(timezone.utc).isoformat()}
    record.update(prepare(code))                   # the Executor stays paused: no adaptation
    if not record['restore_detected']:
        record['outcome'] = 'setup incomplete'
        return record
    record['answers_before'] = queries.run_stable(code)
    random_delay(record)                           # same procedure as the treatment
    record['ddl'] = sc.apply()
    time.sleep(observe_delay)                      # give the change time to take effect at the source
    if sc.fill_data:
        record['fill_data'] = sc.fill_data()
    record['answers_after'] = queries.run_set(code)
    record['assessment'] = queries.score(code, record['answers_before'], record['answers_after'])
    record['artifacts_unchanged'] = subprocess.run(
        ['git', 'status', '--short', 'setup/vkg-system/config'], cwd=ROOT,
        capture_output=True, text=True).stdout.strip() == ''
    record['finished'] = datetime.now(timezone.utc).isoformat()
    return record


# ── treatment mode (§3.10.2) ──────────────────────────────────────────────────
def one_treatment(code: str, number: int, timeout: float) -> dict:
    sc = SCENARIOS[code]
    record = {'mode': 'treatment', 'scenario': code, 'title': sc.title,
              'expected_pattern': sc.pattern, 'expected_decision': sc.decision, 'run': number,
              'commit': COMMIT, 'started': datetime.now(timezone.utc).isoformat()}
    record.update(prepare(code))
    if not record['restore_detected']:
        record['outcome'] = 'setup incomplete'
        return record
    record['answers_before'] = queries.run_stable(code)
    initial_events = event_ids()
    initial_executions = {e['id'] for e in api('/api/v1/obdf/1/executions?limit=50')}
    control('resume')
    random_delay(record)                           # before t0: detection is still measured from the DDL

    monitor = AvailabilityMonitor()
    monitor.start()
    t0_wall = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    record['t_start'] = t0_wall
    try:
        return _treatment_running(sc, record, t0, t0_wall, initial_events, initial_executions, timeout)
    finally:
        record['availability'] = monitor.summary()


def _treatment_running(sc, record: dict, t0: float, t0_wall: str, initial_events: set,
                       initial_executions: set, timeout: float) -> dict:
    code = sc.code
    record['ddl'] = sc.apply()

    event = wait_for(lambda: next((e for e in api('/api/v1/obdf/1/events?limit=20')
                                   if e['id'] not in initial_events), None), timeout)
    record['detection_ms'] = int((time.perf_counter() - t0) * 1000) if event else None
    if event is None:
        record['outcome'] = 'no event received'
        return record
    record['event'] = {'id': event['id'], 'status': event['status'], 'structured': event['structured']}

    plan = next((p for p in api('/api/v1/obdf/1/plans?limit=5') if p['event_id'] == event['id']), None)
    record['decision'] = plan['decision'] if plan else None
    record['pattern'] = plan['pattern'] if plan else None
    record['as_expected'] = record['decision'] == sc.decision and record['pattern'] == sc.pattern
    record['reasons'] = plan['reasons'] if plan else None
    if sc.reasons_contain:
        # a non-automatic decision must come from the reason it was designed for, not another one
        record['reasons_match'] = any(all(p in str(r) for p in sc.reasons_contain)
                                      for r in (record['reasons'] or []))
        record['as_expected'] = record['as_expected'] and record['reasons_match']

    # HITL (ADR-0021): one approval is one manual decision; artifact edits stay at zero.
    # A notification (ADR-0024) waits for no decision.
    record['n_manual'] = 0
    record['decision_ms'] = 0
    if plan and plan['status'] == 'notified':
        return _observe_notification(sc, record, plan, initial_executions)
    if plan and plan['status'] == 'pending_approval':
        t_plan = time.perf_counter()
        result = api(f"/api/v1/plans/{plan['id']}/approve", 'POST',
                     {'note': 'evaluator approval (evaluation protocol)'})
        record['n_manual'] = 1
        record['decision_ms'] = int((time.perf_counter() - t_plan) * 1000)
        record['approved_by'] = result.get('decided_by')

    execution = wait_for(lambda: next((e for e in api('/api/v1/obdf/1/executions?limit=5')
                                       if e['id'] not in initial_executions and e['status'] != 'running'),
                                      None), timeout)
    record['adaptation_ms'] = int((time.perf_counter() - t0) * 1000) if execution else None
    if execution is None:
        record['outcome'] = 'execution not finished'
        return record
    steps = {s['name']: s for s in execution['steps']}
    record['execution'] = {'id': execution['id'], 'status': execution['status'],
                           'timings': execution['timings'], 'failure': execution['failure']}
    record['steps'] = {n: s['detail'].get('duration_ms') for n, s in steps.items()}
    record['step_status'] = {n: s['status'] for n, s in steps.items()}
    record['outcome'] = execution['status']
    # Δt_adapt (eq. 3.16): t_start = the DDL runs; t_end = the artifacts are modified, verified,
    # and serving queries. With blue-green (ADR-0022) verification happens BEFORE the switch, so
    # t_end is the end of the switch step; with the restart strategy, the end of the verify step
    # after the reload.
    last = 'switch' if 'start_ontop' in steps else 'verify'
    record['t_end_step'] = last
    record['dt_adapt_ms'] = ms_between(t0_wall, (steps.get(last) or {}).get('finished_at'))
    record['dt_adapt_machine_ms'] = (record['dt_adapt_ms'] - record['decision_ms']
                                     if record['dt_adapt_ms'] is not None else None)

    if sc.fill_data:
        record['fill_data'] = sc.fill_data()
    record['answers_after'] = queries.run_set(code)
    record['assessment'] = queries.score(code, record['answers_before'], record['answers_after'])
    record['expectations'] = queries.expectations(code, record['answers_after'], record['assessment'])
    record['finished'] = datetime.now(timezone.utc).isoformat()
    return record


def _observe_notification(sc, record: dict, plan: dict, initial_executions: set,
                          observe: float = 20.0) -> dict:
    """A DROP/RENAME that is not proven safe (A009, ADR-0024): ASCAM builds no action and only
    notifies the administrator. The record shows that the plan holds no action, the notification
    names the affected elements, no execution runs, and the OBDF version does not change; the
    queries afterwards show the state a person has to repair."""
    code = sc.code
    time.sleep(observe)                             # time for the Executor to run it by mistake
    new = [e for e in api('/api/v1/obdf/1/executions?limit=10') if e['id'] not in initial_executions]
    version = api('/api/v1/obdf/1/versions?limit=1')
    messages = [n['message'] for n in api('/api/v1/obdf/1/notifications?limit=20')
                if n['plan_id'] == plan['id']]
    record['notifications'] = messages
    record['affected'] = (plan.get('impact') or {}).get('affected')
    record['new_executions'] = [{'id': e['id'], 'status': e['status']} for e in new]
    record['version_after'] = version[0]['version_no'] if isinstance(version, list) and version else None
    record['outcome'] = 'notified' if not new else 'executed_despite_notification'
    record['adaptation_ms'] = record['dt_adapt_ms'] = record['dt_adapt_machine_ms'] = None
    record['answers_after'] = queries.run_set(code)
    record['assessment'] = queries.score(code, record['answers_before'], record['answers_after'])
    record['expectations'] = {**queries.expectations(code, record['answers_after'], record['assessment']),
                              'no_actions': not plan.get('actions'),
                              'notification_names_view': bool(sc.view) and any(sc.view in p for p in messages),
                              'not_executed': not new,
                              'version_unchanged': record['version_after'] == record.get('base_version')}
    record['finished'] = datetime.now(timezone.utc).isoformat()
    return record


# ── C_safe audit (§3.11.1) ────────────────────────────────────────────────────
def kafka_audit(command: str, output: Path) -> dict:
    """Runs kafka_audit.py in a container attached to the Kafka network."""
    script = ('pip install -q --root-user-action=ignore kafka-python-ng >/dev/null 2>&1 && '
              f'python /harness/kafka_audit.py {command}')
    result = subprocess.run(['docker', 'run', '--rm', '--network', 'ascam-networks',
                             '-v', f'{HERE}:/harness:ro', '-v', f'{output}:/out:ro',
                             'python:3.12-slim', 'sh', '-c', script],
                            capture_output=True, text=True)
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {'_error': (result.stderr or result.stdout)[-500:]}


def check_debezium_config() -> dict:
    """First step of the sectoral-ego procedure (§3.10.3 item 1a)."""
    result = {}
    for name in ('register-postgres.json', 'register-mysql.json'):
        config = json.loads((ROOT / 'setup/ascam/schema-monitor' / name).read_text())['config']
        tables = [t.strip() for t in config.get('table.include.list', '').split(',') if t.strip()]
        result[name] = {'table.include.list': tables,
                        'only_ddl_event_log': bool(tables) and all(
                            t.endswith('.ddl_event_log') for t in tables)}
    return result


# ── main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    global UI
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['treatment', 'baseline', 'both'], default='both')
    parser.add_argument('--scenario', action='append', choices=sorted(SCENARIOS))
    parser.add_argument('--runs', type=int, default=20, help='runs per treatment scenario')
    parser.add_argument('--baseline-runs', type=int, default=5, help='runs per baseline scenario')
    parser.add_argument('--timeout', type=float, default=180.0, help='wait limit per stage (seconds)')
    parser.add_argument('--observe-delay', type=float, default=15.0,
                        help='wait after the DDL before the baseline queries (seconds)')
    parser.add_argument('--skip-checks', action='store_true')
    parser.add_argument('--seed', type=int, default=None,
                        help='seed of the random delay before the DDL (default: from the clock, then recorded)')
    parser.add_argument('--allow-dirty', action='store_true',
                        help='run even if tracked files differ from the commit')
    args = parser.parse_args()
    global COMMIT
    seed = args.seed if args.seed is not None else int(time.time())
    RNG.seed(seed)
    COMMIT = git('rev-parse', 'HEAD')
    dirty = git('status', '--porcelain', '--untracked-files=no')
    if dirty and not args.allow_dirty:
        print('Evaluation cancelled: tracked files differ from commit '
              f'{COMMIT[:7]}, so the evaluated code cannot be identified:\n{dirty}')
        return 1
    UI = token('ui')
    selected = args.scenario or sorted(SCENARIOS)

    if not args.skip_checks:
        print('===== preflight checks =====')
        ready, problems = check()
        # the chain test sends a real DDL; there is no point if the ASCAM components are not running
        chain_ok, detail = chain_test() if ready else (True, '')
        if not ready or not chain_ok:
            print('\nEvaluation cancelled:')
            for m in problems + ([] if chain_ok else [f'event chain broken: {detail}']):
                print(f'  - {m}')
            return 1

    output = ROOT / 'results/evaluation' / datetime.now().strftime('%Y%m%dT%H%M%S')
    output.mkdir(parents=True, exist_ok=True)
    print(f'Results go to {output.relative_to(ROOT)} (commit {COMMIT[:7]}, seed {seed})')
    (output / 'metadata.json').write_text(
        json.dumps(evaluation_metadata(args, seed), indent=2, ensure_ascii=False))
    (output / 'debezium-config.json').write_text(
        json.dumps(check_debezium_config(), indent=2))
    wait_for(lambda: api('/health', base=EXECUTOR, bearer='').get('state') == 'running', 60)

    try:
        if args.mode in ('baseline', 'both'):
            print('\n######## BASELINE (Executor paused, no adaptation) ########')
            for code in selected:
                for number in range(1, args.baseline_runs + 1):
                    print(f'\n--- b{code[1:]} run {number}/{args.baseline_runs}')
                    c = one_baseline(code, number, args.observe_delay)
                    (output / f'baseline-{code}-{number:02d}.json').write_text(
                        json.dumps(c, indent=2, ensure_ascii=False))
                    if c.get('outcome') == 'setup incomplete':
                        raise SetupFailed(f'b{code[1:]} run {number}')
                    for name, h in c['answers_after'].items():
                        print(f"    {name:22s} HTTP {h['status']} rows={h['n']} "
                              f"{(h['error'] or '')[:90]}")

        if args.mode in ('treatment', 'both'):
            print('\n######## TREATMENT (ASCAM MAPE-K loop) ########')
            start = kafka_audit('offset', output)
            (output / 'kafka-start.json').write_text(json.dumps(start, indent=2))
            print(f'  Kafka audit starting point: {len(start)} topics {sorted(start)}')
            for code in selected:
                for number in range(1, args.runs + 1):
                    print(f'\n--- {code} run {number}/{args.runs}')
                    c = one_treatment(code, number, args.timeout)
                    (output / f'run-{code}-{number:02d}.json').write_text(
                        json.dumps(c, indent=2, ensure_ascii=False))
                    if c.get('outcome') == 'setup incomplete':
                        raise SetupFailed(f'{code} run {number}')
                    p = (c.get('assessment') or {})
                    print(f"    outcome={c.get('outcome')} decision={c.get('decision')} "
                          f"detection={c.get('detection_ms')} ms dt_adapt={c.get('dt_adapt_ms')} ms "
                          f"preservation={p.get('preservation_ratio')}% expectations={c.get('expectations')}")
            audit_result = kafka_audit('audit /out/kafka-start.json', output)
            (output / 'kafka-audit.json').write_text(json.dumps(audit_result, indent=2))
            print(f"\n  Kafka audit: N_log={audit_result.get('N_log')} N_prod={audit_result.get('N_prod')} "
                  f"C_safe={audit_result.get('C_safe')}")
    except SetupFailed as exc:
        print(f'\nEVALUATION STOPPED at {exc}: the monitor did not detect the source restore within 60 s.'
              '\n  Check the monitor (preflight.py) and run again; the runs already saved remain valid.')
    finally:
        control('pause')
        quiet()
        supersede_plans()
        for code in selected:
            SCENARIOS[code].restore()
        reset()
        control('resume')
    print(f'\nDone. Analysis: python3 experiments/evaluation/analyze.py {output.relative_to(ROOT)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
