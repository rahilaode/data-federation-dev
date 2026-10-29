"""
Targeted test of the polling-based MySQL monitor (anomaly in run 12 of A006, then numbered A003).

The MySQL monitor compares a column snapshot (ddl_column_snapshot) with information_schema
every 10 seconds. Hypothesis: if a column is renamed and renamed back between two scans, no
difference is visible and both changes are lost.

The test uses a separate table `ascam_monitor_probe` that is registered for a while in
captured_tables_ref, so the case-study tables are not touched and ASCAM ignores its events (the
table is not federated). Every change is cleaned up at the end.

For every delay W the column is renamed (a -> b), W seconds pass, it is renamed back (b -> a),
and the test waits long enough for the next scan to happen for certain. It counts how many of
the two changes reached ddl_event_log, and their detection latency.

Run from the repository root:  python3 experiments/evaluation/probe_mysql_monitor.py [--repeats 3]
"""
import argparse
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone

TABLE = 'ascam_monitor_probe'
DELAYS = [2, 5, 12, 25]
SETTLE = 25                         # > 2 x polling interval


def mysql(sql: str) -> list[list[str]]:
    result = subprocess.run(
        ['docker', 'exec', '-i', 'datasources-mysql', 'sh', '-c',
         'mysql -N -B -uroot -p"$MYSQL_ROOT_PASSWORD" dukcapil 2>/dev/null'],
        input=sql, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)
    return [b.split('\t') for b in result.stdout.strip().splitlines() if b.strip()]


def last_log_id() -> int:
    rows = mysql('SELECT COALESCE(MAX(id), 0) FROM ddl_event_log;')
    return int(rows[0][0])


def log_since(first_id: int) -> list[dict]:
    rows = mysql(f"SELECT id, alter_type, column_name, captured_at FROM ddl_event_log "
                 f"WHERE id > {first_id} AND table_name = '{TABLE}' ORDER BY id;")
    return [{'id': int(b[0]), 'op': b[1], 'column': b[2],
             'time': datetime.fromisoformat(b[3]).replace(tzinfo=timezone.utc)} for b in rows]


def prepare() -> None:
    mysql(f"""
        DROP TABLE IF EXISTS {TABLE};
        CREATE TABLE {TABLE} (id INT PRIMARY KEY, col_a VARCHAR(20));
        DELETE FROM ddl_column_snapshot WHERE table_name = '{TABLE}';
        INSERT INTO ddl_column_snapshot (db_name, table_name, column_name, column_type, ordinal_pos)
            SELECT TABLE_SCHEMA, TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, ORDINAL_POSITION
            FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = 'dukcapil' AND TABLE_NAME = '{TABLE}';
        INSERT INTO captured_tables_ref (db_name, table_name, description)
            VALUES ('dukcapil', '{TABLE}', 'temporary monitor probe');
    """)
    time.sleep(SETTLE)              # let one scan run with no change


def clean_up() -> None:
    mysql(f"""
        DELETE FROM captured_tables_ref WHERE table_name = '{TABLE}';
        DELETE FROM ddl_column_snapshot WHERE table_name = '{TABLE}';
        DROP TABLE IF EXISTS {TABLE};
    """)


def rename(old: str, new: str) -> datetime:
    t = datetime.now(timezone.utc)
    mysql(f'ALTER TABLE {TABLE} RENAME COLUMN {old} TO {new};')
    return t


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--repeats', type=int, default=3)
    args = parser.parse_args()
    interval = mysql("SELECT INTERVAL_VALUE, INTERVAL_FIELD FROM information_schema.EVENTS "
                     "WHERE EVENT_NAME = 'evt_detect_ddl_changes';")
    print(f'MySQL monitor scan interval: {" ".join(interval[0]) if interval else "?"}')
    print(f'Estimated duration: {args.repeats * sum(w + 2 * SETTLE for w in DELAYS) // 60 + 1} minutes\n')

    result = {w: {'cycles': 0, 'recorded': 0, 'both_lost': 0, 'latency': []} for w in DELAYS}
    prepare()
    try:
        for repeat in range(1, args.repeats + 1):
            for w in DELAYS:
                first = last_log_id()
                t1 = rename('col_a', 'col_b')
                time.sleep(w)
                t2 = rename('col_b', 'col_a')
                time.sleep(SETTLE)
                log = [e for e in log_since(first) if e['op'] == 'RENAME COLUMN']
                forward = [e for e in log if e['column'].startswith('col_a')]
                back = [e for e in log if e['column'].startswith('col_b')]
                r = result[w]
                r['cycles'] += 1
                r['recorded'] += len(forward[:1]) + len(back[:1])
                r['both_lost'] += int(not forward and not back)
                if forward:
                    r['latency'].append((forward[0]['time'] - t1).total_seconds())
                if back:
                    r['latency'].append((back[0]['time'] - t2).total_seconds())
                print(f'  repeat {repeat}, delay {w:>2} s: forward={"yes" if forward else "LOST"}, '
                      f'back={"yes" if back else "LOST"}')
                time.sleep(SETTLE)
    finally:
        clean_up()

    print('\n| Delay (s) | Cycles | Changes recorded | Cycles with both lost | Detection latency (s) |')
    print('|---:|---:|---:|---:|---:|')
    for w, r in result.items():
        lat = r['latency']
        text = (f'{statistics.median(lat):.1f} ({min(lat):.1f}–{max(lat):.1f})' if lat else '-')
        print(f"| {w} | {r['cycles']} | {r['recorded']}/{2 * r['cycles']} | "
              f"{r['both_lost']} | {text} |")
    return 0


if __name__ == '__main__':
    sys.exit(main())
