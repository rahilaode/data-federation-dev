"""
Recovers Debezium connectors that lost their read position, WITHOUT replaying ddl_event_log.

Symptoms handled (found on 2026-09-30):
  - the MySQL connector is FAILED with error 1236 "Could not find first log file name in binary
    log index file": the binlog position it remembers no longer exists on the server;
  - a connector with snapshot.mode=initial that lost its offsets reads all of ddl_event_log
    again, and ASCAM adapts the OBDF to old DDL.

Steps: stop both connectors, delete their offsets (Kafka Connect REST, KIP-875), delete the
connectors, delete the MySQL schema history topic, then drop the PostgreSQL replication slot and
publication so that they are recreated from the current position. restart-ascam.sh registers the
connectors again with snapshot.mode=no_data, so only DDL after the registration is published.

Source data and the content of ddl_event_log are NOT changed. Run from the repository root:
  python3 experiments/reset_connectors.py
  ./restart-ascam.sh --chain-test
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

CONNECT = 'http://localhost:8083'
CONNECTORS = ['postgres-connector', 'mysql-connector']
HISTORY_TOPIC = 'schema-monitor-dukcapil'
SLOT, PUBLICATION = 'kemensos_slot', 'dbz_publication'


def rest(method: str, path: str) -> tuple[int, str]:
    req = urllib.request.Request(CONNECT + path, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def status(name: str) -> str | None:
    code, body = rest('GET', f'/connectors/{name}/status')
    return json.loads(body)['connector']['state'] if code == 200 else None


def docker(*args: str) -> str:
    result = subprocess.run(['docker', *args], capture_output=True, text=True)
    return (result.stdout + result.stderr).strip()


def main() -> int:
    code, _ = rest('GET', '/connectors')
    if code != 200:
        sys.exit(f'Kafka Connect does not answer at {CONNECT}')

    for name in CONNECTORS:
        if status(name) is None:
            print(f'{name}: not registered, skipped')
            continue
        print(f'{name}: stopping …', rest('PUT', f'/connectors/{name}/stop')[0])
        for _ in range(30):
            if status(name) == 'STOPPED':
                break
            time.sleep(1)
        code, body = rest('DELETE', f'/connectors/{name}/offsets')
        if code not in (200, 204):
            sys.exit(f'{name}: offsets could not be deleted ({code}): {body[:300]}\n'
                     'Stop here; do not register it again with the old offsets.')
        print(f'{name}: offsets deleted')
        print(f'{name}: deleted', rest('DELETE', f'/connectors/{name}')[0])

    print('schema history topic:', docker('exec', 'ascam-sm-kafka', '/kafka/bin/kafka-topics.sh',
                                          '--bootstrap-server', 'localhost:9092', '--delete',
                                          '--topic', HISTORY_TOPIC) or 'deleted')

    psql = ['exec', 'datasources-pgsql', 'psql', '-U', 'postgres', '-d', 'kemensos', '-tAc']
    for _ in range(15):                                   # a slot can only be dropped when it is not active
        active = docker(*psql, f"SELECT active FROM pg_replication_slots WHERE slot_name='{SLOT}'")
        if active in ('', 'f'):
            break
        time.sleep(2)
    if active:
        print('replication slot:', docker(*psql, f"SELECT pg_drop_replication_slot('{SLOT}')"))
    print('publication:', docker(*psql, f'DROP PUBLICATION IF EXISTS {PUBLICATION}'))

    print('\nDone. Continue with: ./restart-ascam.sh --chain-test')
    print('Check afterwards that both connectors are RUNNING and that the publication holds only ddl_event_log:')
    print("  docker exec datasources-pgsql psql -U postgres -d kemensos -c "
          "\"SELECT * FROM pg_publication_tables\"")
    return 0


if __name__ == '__main__':
    sys.exit(main())
