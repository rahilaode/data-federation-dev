"""
Preflight checks before an evaluation.

An evaluation only means something when the whole chain is alive: the monitors on the sources,
the Debezium connectors, Kafka, the Orchestrator, Knowledge, the agent, and the Executor. The
checks end with a real chain test: one DDL on a table kept for this test, then a wait for the
matching event to appear in Knowledge. The test table is not federated, so Knowledge marks the
event `ignored`, which is exactly what shows that the chain is intact without changing the OBDF.

Run on its own: `python3 experiments/evaluation/preflight.py`
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE = 'http://127.0.0.1:18000'
ORCHESTRATOR = 'http://127.0.0.1:18200'
EXECUTOR = 'http://127.0.0.1:18300'
AGENT = 'http://127.0.0.1:18100'
CONNECT = 'http://localhost:8083'
SECRETS = ROOT / 'setup/ascam/knowledge/secrets'
CONTAINERS = ['ascam-sm-kafka', 'ascam-sm-connector', 'datasources-pgsql', 'datasources-mysql',
              'data-federation-teiid', 'vkg-system-ontop-teiid', 'ascam-knowledge-service',
              'ascam-ontop-agent', 'ascam-orchestrator']
PROBE = 'ascam_probe'


def token(file: str, client: str | None = None) -> str:
    for line in (SECRETS / file).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if client and ':' in line:
            if line.split(':', 1)[0] == client:
                return line.split(':', 1)[1]
            continue
        return line.split(':', 1)[1] if ':' in line else line
    raise RuntimeError(f'secret {client or file} not found')


def http(base: str, path: str, bearer: str | None = None):
    headers = {'Authorization': f'Bearer {bearer}'} if bearer else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(f'{base}{path}', headers=headers),
                                    timeout=30) as response:
            return json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as exc:
        return {'_http': exc.code}
    except Exception as exc:                                       # noqa: BLE001
        return {'_error': f'{type(exc).__name__}'}


def pg(sql: str) -> str:
    result = subprocess.run(['docker', 'exec', 'datasources-pgsql', 'psql', '-U', 'postgres',
                             '-d', 'kemensos', '-tAc', sql], capture_output=True, text=True)
    return (result.stdout or result.stderr).strip()


def running() -> set[str]:
    result = subprocess.run(['docker', 'ps', '--format', '{{.Names}}'], capture_output=True,
                            text=True)
    return set(result.stdout.split())


def check(quiet: bool = False) -> tuple[bool, list[str]]:
    problems: list[str] = []
    report = (lambda *a: None) if quiet else print

    up = running()
    missing = [n for n in CONTAINERS if n not in up]
    report(f"  containers: {len(CONTAINERS) - len(missing)}/{len(CONTAINERS)} running"
           f"{' | not running: ' + ', '.join(missing) if missing else ''}")
    if missing:
        problems.append(f'containers not running: {", ".join(missing)}')

    for connector in ('postgres-connector', 'mysql-connector'):
        status = http(CONNECT, f'/connectors/{connector}/status')
        state = (status.get('connector') or {}).get('state')
        tasks = [t.get('state') for t in status.get('tasks', [])]
        report(f'  connector {connector}: {state} tasks={tasks}')
        if state != 'RUNNING' or tasks != ['RUNNING']:
            problems.append(f'connector {connector} is not RUNNING ({state}, {tasks})')

    orch = http(ORCHESTRATOR, '/health')
    report(f"  orchestrator: {orch.get('state')} topics={len(orch.get('topics') or [])} "
           f"failures={orch.get('failures')}")
    if orch.get('state') != 'running':
        problems.append(f"orchestrator is not running ({orch.get('state') or orch}); "
                        f"last error: {orch.get('last_error')}")

    exe = http(EXECUTOR, '/health')
    report(f"  executor: {exe.get('state')}")
    agent = http(AGENT, '/health', token('ontop_agent_api_tokens'))
    report(f"  agent: {agent.get('status')} artifacts={agent.get('artifacts')}")

    # Blue-green OBDA layer (ADR-0022): exactly one Ontop instance (the active one) is running.
    bg = http(AGENT, '/api/v1/bluegreen', token('ontop_agent_api_tokens'))
    if bg.get('active'):
        live = sorted(n for n in up if n in ('vkg-system-ontop-blue', 'vkg-system-ontop-green'))
        report(f"  ontop blue-green: active {bg['active']} (VDB v{bg.get('vdb_version')}), "
               f"instances running {live}")
        if live != [bg['active_container']]:
            problems.append(f"Ontop instances running {live}, expected only {bg['active_container']}")
    if agent.get('status') != 'ok':
        problems.append('the Ontop agent is not healthy')

    # View case study (A007-A009): the predicates built from views of the `layanan` model must be
    # answered in the base state. If not, the OBDF runs with an old VDB or mapping: run start.sh.
    import queries
    for view_scenario in ('a007', 'a008', 'a009'):
        name = queries.affected(view_scenario)
        result = queries.run(queries.Q[view_scenario][name][1], timeout=60)
        error = ' '.join((result['error'] or '').split())[:400]
        report(f"  view {view_scenario} ({name}): HTTP {result['status']} rows={result['n']}"
               f"{' | ' + error if error else ''}")
        if not result['ok'] or not result['n']:
            from scenarios import SCENARIOS, _my_exists, _pg_exists
            sc = SCENARIOS[view_scenario]
            exists = (_pg_exists if sc.source == 'kemensos' else _my_exists)(sc.table, sc.column)
            if not exists:
                # left over from a run that stopped before the restore: the source is not in its base state
                problems.append(f'source column {sc.table}.{sc.column} ({view_scenario}) is missing; '
                                'run python3 experiments/evaluation/restore_sources.py, then ./restart-ascam.sh')
            else:
                problems.append(f'base query {view_scenario} ({name}) returns no rows; '
                                'the case-study views are not loaded (run start.sh)')

    version = http(KNOWLEDGE, '/api/v1/obdf/1/versions?limit=1', token('knowledge_api_tokens', 'ui'))
    if isinstance(version, list) and version:
        report(f"  knowledge: active version {version[0]['version_no']} "
               f"(VDB v{version[0]['teiid_vdb_version']})")
    else:
        problems.append('Knowledge does not answer the version list')
    return not problems, problems


def monitor_log(column: str) -> str:
    """Did the PostgreSQL monitor record the test DDL in its log table?"""
    return pg("SELECT count(*) FROM schema_monitor.ddl_event_log "
              f"WHERE table_name = '{PROBE}' AND column_name = '{column}'")


def orchestrator_messages() -> int | None:
    health = http(ORCHESTRATOR, '/health')
    return health.get('messages') if isinstance(health, dict) else None


def orchestrator_counters() -> dict:
    health = http(ORCHESTRATOR, '/health')
    if not isinstance(health, dict):
        return {}
    return {k: health.get(k) for k in ('duplicates', 'conflicts', 'skipped', 'failures')}


def diagnose(column: str, initial_messages: int | None) -> list[str]:
    """Points at the broken link: monitor, Debezium/Kafka, or Orchestrator."""
    findings = []
    logged = monitor_log(column)
    if logged != '1':
        findings.append(f'[monitor] the test DDL was NOT recorded in schema_monitor.ddl_event_log '
                        f'(result: {logged!r}); check the PostgreSQL event trigger')
        return findings
    findings.append('[monitor] the test DDL was recorded in ddl_event_log')
    final_messages = orchestrator_messages()
    if initial_messages is not None and final_messages is not None and final_messages > initial_messages:
        findings.append(f'[orchestrator] received {final_messages - initial_messages} new messages, but '
                        'no event was recorded in Knowledge')
        health = http(ORCHESTRATOR, '/health')
        findings.append(f'[orchestrator] counters: {orchestrator_counters()}')
        if isinstance(health, dict) and (health.get('duplicates') or health.get('conflicts')):
            findings.append('[orchestrator] Knowledge treats the message as a DUPLICATE; if a Kafka '
                            'topic was ever recreated, old offset-based uids can collide')
        skipped = health.get('last_skipped') if isinstance(health, dict) else None
        if skipped:
            findings.append(f"[orchestrator] message skipped: {skipped.get('reason')}")
            findings.append(f"[orchestrator] message excerpt: {str(skipped.get('excerpt'))[:240]}")
    else:
        findings.append('[debezium/kafka/orchestrator] the message did NOT reach the Orchestrator. '
                        'If the connectors are RUNNING, the Orchestrator consumer is probably stuck: '
                        'docker-compose -f setup/ascam/orchestrator/docker-compose.yaml restart')
    status = http(CONNECT, '/connectors/postgres-connector/status')
    for task in status.get('tasks', []):
        if task.get('trace'):
            findings.append(f"[debezium] task error: {task['trace'].splitlines()[0][:200]}")
    log = subprocess.run(['docker', 'logs', '--tail', '5', 'ascam-orchestrator'],
                         capture_output=True, text=True)
    for line in (log.stdout + log.stderr).strip().splitlines()[-5:]:
        findings.append(f'[orchestrator log] {line[:180]}')
    return findings


def chain_test(timeout: float = 60.0, quiet: bool = False) -> tuple[bool, str]:
    """A real DDL on the test table, then a wait for the event to reach Knowledge."""
    report = (lambda *a: None) if quiet else print
    ui = token('knowledge_api_tokens', 'ui')
    initial_messages = orchestrator_messages()
    initial = http(KNOWLEDGE, '/api/v1/obdf/1/events?limit=200', ui)
    if not isinstance(initial, list):
        return False, f'Knowledge does not answer: {initial}'
    before = {e['id'] for e in initial}
    pg(f'CREATE TABLE IF NOT EXISTS public.{PROBE} (id serial primary key)')
    # The monitor logs only tables listed in captured_tables_ref, so the test table is listed
    # for the duration of the test and removed again afterwards (see cleanup()).
    pg("INSERT INTO schema_monitor.captured_tables_ref (schema_name, table_name, description) "
       f"VALUES ('public', '{PROBE}', 'preflight chain test') ON CONFLICT DO NOTHING")
    column = f'probe_{int(time.time())}'
    pg(f'ALTER TABLE public.{PROBE} ADD COLUMN {column} varchar(10)')
    start = time.perf_counter()
    while time.perf_counter() - start < timeout:
        latest = http(KNOWLEDGE, '/api/v1/obdf/1/events?limit=20', ui)
        for event in latest if isinstance(latest, list) else []:
            if event['id'] in before:
                continue
            structured = event.get('structured') or {}
            if structured.get('table') == PROBE:
                delay = int((time.perf_counter() - start) * 1000)
                report(f"  chain intact: event {event['id']} received after {delay} ms "
                       f"(status {event['status']})")
                cleanup()
                return True, f'{delay} ms'
        time.sleep(1)
    for finding in diagnose(column, initial_messages):
        report(f'  {finding}')
    cleanup()
    return False, f'no event within {int(timeout)} s'


def cleanup() -> None:
    """Removes the test table and its entry in the reference table."""
    pg(f'DROP TABLE IF EXISTS public.{PROBE}')
    pg("DELETE FROM schema_monitor.captured_tables_ref "
       f"WHERE schema_name = 'public' AND table_name = '{PROBE}'")


def main() -> int:
    # --no-chain: check the components only, with no test DDL on the source (table ascam_probe)
    no_chain = '--no-chain' in sys.argv
    print('===== component checks =====')
    ok, problems = check()
    if no_chain:
        print('\n(chain test skipped: no DDL was run on the sources)')
    else:
        print('\n===== chain test DDL -> Kafka -> Orchestrator -> Knowledge =====')
        chain_ok, detail = chain_test()
        if not chain_ok:
            problems.append(f'event chain broken: {detail}')
            print(f'  FAILED: {detail}')
    if problems:
        print('\nProblems to fix before the evaluation:')
        for m in problems:
            print(f'  - {m}')
        print('\nCommon fixes:')
        print('  docker-compose -f setup/ascam/schema-monitor/docker-compose.yaml up -d')
        print('  docker-compose -f setup/ascam/orchestrator/docker-compose.yaml restart')
        print('  python3 experiments/reset_connectors.py && ./restart-ascam.sh   # registers the connectors again')
        return 1
    print('\nEverything is ready for the evaluation.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
