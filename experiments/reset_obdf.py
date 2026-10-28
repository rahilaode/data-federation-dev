"""
Returns the OBDF to its base state so that an experiment can be repeated.

Every evaluation run must start from the same state. This script:
  1. restores the OBDA artifacts (mapping.ttl, ontology_file.ttl) to their content in git;
  2. points the Teiid connection back at VDB version 1 and removes newer versions;
  3. drops the test column on the source (default: kemensos.penerima_manfaat.email);
  4. reloads Ontop and checks that the endpoint answers (blue-green: the active instance is
     rebuilt from the artifacts in git and pinned to the base VDB version, ADR-0022);
  5. syncs Knowledge so that the active specification version reflects the base state.

Note: the history in Knowledge (events, plans, executions) is NOT deleted, because it is the
record of the experiment. Use `--column name` for another test column, and `--keep-sources` when
the sources must not be changed.

Run on the host: `python3 experiments/reset_obdf.py`
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE = 'http://127.0.0.1:18000'
AGENT = 'http://127.0.0.1:18100'
# The WildFly management port is mapped to 19990 on the host (setup/data-federation/docker-compose.yaml)
TEIID = os.getenv('ASCAM_TEIID_MGMT', 'http://localhost:19990/management')
SECRETS = ROOT / 'setup/ascam/knowledge/secrets'
ARTIFACTS = ['setup/vkg-system/config/mapping.ttl', 'setup/vkg-system/config/ontology_file.ttl']


def secret(file: str, client: str | None = None) -> str:
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


def api(base: str, path: str, bearer: str | None = None, method: str = 'GET', body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {'Content-Type': 'application/json'}
    if bearer:
        headers['Authorization'] = f'Bearer {bearer}'
    request = urllib.request.Request(f'{base}{path}', data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as exc:
        return {'_http': exc.code, 'detail': exc.read()[:200].decode('utf-8', 'replace')}
    except Exception as exc:                        # noqa: BLE001
        return {'_error': f'{type(exc).__name__}: {exc}'}


def teiid(operation: dict, user: str, password: str):
    manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    manager.add_password(None, TEIID, user, password)
    opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(manager))
    request = urllib.request.Request(TEIID, data=json.dumps(operation).encode(), method='POST',
                                     headers={'Content-Type': 'application/json'})
    try:
        with opener.open(request, timeout=60) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return {'outcome': 'failed', 'failure-description': exc.read()[:200].decode('utf-8', 'replace')}


def psql(command: str) -> str:
    result = subprocess.run(['docker', 'exec', 'datasources-pgsql', 'psql', '-U', 'postgres',
                             '-d', 'kemensos', '-tAc', command], capture_output=True, text=True)
    return (result.stdout or result.stderr).strip()


def heading(text: str) -> None:
    print(f'\n===== {text} =====')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--column', default='email')
    parser.add_argument('--table', default='penerima_manfaat')
    parser.add_argument('--vdb', default='government')
    parser.add_argument('--base-version', default='1')
    parser.add_argument('--keep-sources', action='store_true')
    parser.add_argument('--user', default=os.getenv('ASCAM_TEIID_USER', 'admin'))
    args = parser.parse_args()

    ui = secret('knowledge_api_tokens', 'ui')
    agent_token = secret('ontop_agent_api_tokens')
    teiid_password = secret('obdf_teiid_mgmt_password')

    heading('1) OBDA artifacts restored to their content in git')
    subprocess.run(['git', 'checkout', '--', *ARTIFACTS], cwd=ROOT, check=False)
    print(' ', subprocess.run(['git', 'status', '--short', *ARTIFACTS], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip() or 'clean')

    heading('2) VDB version restored')
    versions = teiid({'operation': 'list-vdbs', 'address': [{'subsystem': 'teiid'}]},
                     args.user, teiid_password)
    if versions.get('outcome') != 'success':
        print(f'  CANNOT reach Teiid at {TEIID}: '
              f"{str(versions.get('failure-description') or versions)[:200]}")
        print('  set ASCAM_TEIID_MGMT if the management port is different')
        return 1
    # The list-vdbs result keys are vdb-name and vdb-version (see connectors.py in Knowledge)
    active = [(v.get('vdb-name'), str(v.get('vdb-version')), v.get('connection-type'), v.get('status'))
              for v in (versions.get('result') or []) if isinstance(v, dict)]
    print('  before:', active)
    teiid({'operation': 'change-vdb-connection-type', 'address': [{'subsystem': 'teiid'}],
           'vdb-name': args.vdb, 'vdb-version': args.base_version, 'connection-type': 'ANY'},
          args.user, teiid_password)
    for name, number, _, _ in active:
        if name == args.vdb and number != args.base_version:
            deployment = f'{name}-{number}-vdb.xml'
            for operation in ('undeploy', 'remove'):
                teiid({'operation': operation, 'address': [{'deployment': deployment}]},
                      args.user, teiid_password)
            print(f'  version {number} removed ({deployment})')
    versions = teiid({'operation': 'list-vdbs', 'address': [{'subsystem': 'teiid'}]},
                     args.user, teiid_password)
    print('  after:', [(v.get('vdb-name'), str(v.get('vdb-version')), v.get('connection-type'),
                        v.get('status')) for v in (versions.get('result') or [])
                       if isinstance(v, dict)])

    if not args.keep_sources:
        heading('3) test column dropped on the source')
        exists = psql("SELECT count(*) FROM information_schema.columns "
                      f"WHERE table_name='{args.table}' AND column_name='{args.column}'")
        print(' ', psql(f'ALTER TABLE public.{args.table} DROP COLUMN {args.column}')
              if exists == '1' else 'column already gone')

    heading('4) Ontop reloaded')
    # Blue-green (ADR-0022): the active instance is rebuilt from the canonical artifacts and
    # pinned to the base version; the standby instance is stopped. An agent without blue-green
    # answers 409, and the old reload is used instead (ADR-0001).
    result = api(AGENT, '/api/v1/bluegreen/reset', agent_token, 'POST', {'vdb_version': args.base_version})
    if result.get('_http') == 409:
        result = api(AGENT, '/api/v1/reload', agent_token, 'POST')
        print(f"  ok={result.get('ok')} total={result.get('total_ms')} ms {result.get('error') or ''}")
    else:
        print(f"  ok={result.get('ok')} instance={result.get('color')} "
              f"duration={result.get('duration_ms')} ms {result.get('error') or ''}")
    artifacts = api(AGENT, '/api/v1/artifacts', agent_token)
    for a in artifacts if isinstance(artifacts, list) else []:
        print(f"  artifact {a['kind']:9s} {a['sha256'][:16]}")

    heading('5) Knowledge synced')
    sync = api(KNOWLEDGE, '/api/v1/obdf/1/sync', ui, 'POST')
    print(f"  changed={sync.get('changed')} version={sync.get('version_no')} "
          f"drift={sync.get('drift_detected')} issues={len(sync.get('issues') or [])}")
    active_version = api(KNOWLEDGE, '/api/v1/obdf/1/versions?limit=1', ui)
    for v in active_version if isinstance(active_version, list) else []:
        print(f"  version {v['version_no']} {v['status']} (VDB v{v['teiid_vdb_version']}, "
              f"{v['teiid_connection_type']})")
    print('\nThe OBDF is ready for the next run.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
