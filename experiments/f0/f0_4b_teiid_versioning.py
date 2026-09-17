"""
F0.4b — Uji kelayakan: penggantian Σ_S secara blue-green memakai versi VDB Teiid.

Dasar: Teiid Reference Guide, "VDB Versioning" (connection type NONE, BY_VERSION,
ANY; skenario deployment dan rollback) dan "Teiid Management CLI"
(get-vdb, change-vdb-connection-type).

Pertanyaan yang diuji:
  1. Di mana Teiid melaporkan penyebab VDB berstatus FAILED?
  2. Bila versi baru FAILED, apakah koneksi tanpa nomor versi tetap dilayani versi lama?
  3. Versi baru yang ACTIVE (BY_VERSION): apakah koneksi tanpa versi tetap ke versi lama,
     dan apakah versi baru bisa diakses dengan nama "vdb.versi" lewat ODBC?
  4. Setelah versi baru diberi connection type ANY: ke mana koneksi baru tanpa versi,
     dan apakah koneksi lama yang sudah terbuka tetap berjalan?
  5. Bila dua versi sama-sama ANY: versi mana yang dipilih?
  6. Berapa lama operasi change-vdb-connection-type?

Semua VDB uji bernama f0v / f0x, hanya membaca kemensos.program_bansos,
dan dihapus di akhir. VDB government tidak disentuh.
"""
import json
import os
import time
from datetime import datetime

import psycopg
import requests
from requests.auth import HTTPDigestAuth

TEIID = os.getenv('TEIID_HOST', 'data-federation-teiid')
MGMT = f"http://{TEIID}:{os.getenv('MGMT_PORT', '9990')}/management"
AUTH = HTTPDigestAuth(os.getenv('MGMT_USER', 'admin'), os.getenv('MGMT_PASSWORD', 'Password12345_'))
ODBC = dict(host=TEIID, port=int(os.getenv('TEIID_ODBC_PORT', '35432')),
            user=os.getenv('TEIID_USER', 'user1'), password=os.getenv('TEIID_PASSWORD', 'Password12345_'),
            sslmode='disable', gssencmode='disable', connect_timeout=10)
OUT = os.getenv('OUT_DIR', '/out')
REPORT = {}
DEPLOYED = []

BASE = ("CREATE FOREIGN TABLE \"program_bansos\" (\"program_id\" integer not null primary key, "
        "\"nama_program\" varchar(100)) OPTIONS(UPDATABLE 'FALSE');")
V2 = BASE + '\nALTER FOREIGN TABLE "program_bansos" ADD COLUMN "kolom_v2" varchar(10);'
V3 = V2 + '\nALTER FOREIGN TABLE "program_bansos" ADD COLUMN "kolom_v3" varchar(10);'
BAD = BASE + '\nALTER FOREIGN TABLE "tabel_tidak_ada" ADD COLUMN "x" varchar(10);'


def vdb_xml(name, version, ddl):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<vdb name="{name}" version="{version}">
  <model visible="true" name="p">
    <source name="p" translator-name="postgresql" connection-jndi-name="java:/pgsql-kemensos"/>
    <metadata type="DDL"><![CDATA[
{ddl}
    ]]></metadata>
  </model>
</vdb>
'''.encode()


def op(payload, timeout=120):
    r = requests.post(MGMT, json=payload, auth=AUTH, timeout=timeout)
    try:
        body = r.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        body = {'outcome': 'http-error', 'text': (r.text or '')[:200]}
    return body


def deploy(name, version, ddl):
    dep = f'{name}-{version}-vdb.xml'
    r = requests.post(f'{MGMT}/add-content', files={'file': (dep, vdb_xml(name, version, ddl))},
                      auth=AUTH, timeout=60)
    r.raise_for_status()
    h = r.json()['result']['BYTES_VALUE']
    t0 = time.perf_counter()
    res = op({'operation': 'add', 'address': [{'deployment': dep}],
              'content': [{'hash': {'BYTES_VALUE': h}}], 'enabled': True})
    DEPLOYED.append(dep)
    status, raw = wait_status(name, version)
    return res.get('outcome'), status, round(time.perf_counter() - t0, 3), raw


def undeploy(name, version):
    dep = f'{name}-{version}-vdb.xml'
    op({'operation': 'undeploy', 'address': [{'deployment': dep}]})
    r = op({'operation': 'remove', 'address': [{'deployment': dep}]})
    if dep in DEPLOYED:
        DEPLOYED.remove(dep)
    return r.get('outcome')


def get_vdb(name, version):
    return op({'operation': 'get-vdb', 'address': [{'subsystem': 'teiid'}],
               'vdb-name': name, 'vdb-version': str(version)})


def wait_status(name, version, timeout=60):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        res = get_vdb(name, version)
        status = (res.get('result') or {}).get('status')
        if status in ('ACTIVE', 'FAILED'):
            return status, res.get('result')
        time.sleep(0.1)
    return 'TIMEOUT', None


def set_type(name, version, ctype):
    t0 = time.perf_counter()
    res = op({'operation': 'change-vdb-connection-type', 'address': [{'subsystem': 'teiid'}],
              'vdb-name': name, 'vdb-version': str(version), 'connection-type': ctype})
    return res.get('outcome'), round(time.perf_counter() - t0, 3), res.get('failure-description')


def served_version(dbname):
    """Versi VDB yang melayani koneksi BARU dengan nama database `dbname`."""
    try:
        with psycopg.connect(dbname=dbname, **ODBC) as conn, conn.cursor() as cur:
            cur.execute('SELECT "Version" FROM SYS.VirtualDatabases')
            version = cur.fetchone()[0]
            cur.execute('SELECT "Name" FROM SYS.Columns WHERE "TableName" = \'program_bansos\' ORDER BY "Position"')
            cols = [r[0] for r in cur.fetchall()]
            return f'v{version} {cols}'
    except Exception as exc:                      # noqa: BLE001 — uji kelayakan
        return f'GALAT: {str(exc).strip().splitlines()[0][:160]}'


def conn_query(conn):
    if conn is None:
        return '-'
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT "Version" FROM SYS.VirtualDatabases')
            v = cur.fetchone()[0]
            cur.execute('SELECT count(*) FROM p.program_bansos')
            return f'v{v}, {cur.fetchone()[0]} baris'
    except Exception as exc:                      # noqa: BLE001
        return f'GALAT: {str(exc).strip().splitlines()[0][:160]}'


def show(label, value, key):
    print(f'   {label:58s} {value}')
    REPORT[key] = value


def main():
    print('\n== 1) di mana penyebab VDB FAILED dilaporkan?')
    outcome, status, dt, raw = deploy('f0x', 1, BAD)
    print(f'   deploy f0x v1 (DDL tidak valid): outcome {outcome}, status {status}')
    raw_s = json.dumps(raw, default=str)
    print(f'   kunci hasil get-vdb: {sorted(raw.keys()) if isinstance(raw, dict) else raw}')
    print(f'   isi (dipotong): {raw_s[:900]}')
    REPORT['failed_get_vdb'] = raw
    undeploy('f0x', 1)

    print('\n== 2) versi lama tetap melayani bila versi baru FAILED')
    outcome, status, dt, _ = deploy('f0v', 1, BASE)
    show('deploy f0v v1', f'{outcome}/{status} dalam {dt} s', 'v1_deploy')
    show('koneksi baru "f0v"', served_version('f0v'), 'q2_before')
    outcome, status, dt, _ = deploy('f0v', 2, BAD)
    show('deploy f0v v2 (tidak valid)', f'{outcome}/{status} dalam {dt} s', 'v2_bad_deploy')
    show('koneksi baru "f0v" sesudah v2 FAILED', served_version('f0v'), 'q2_after_bad')
    show('hapus v2 tidak valid', undeploy('f0v', 2), 'v2_bad_removed')

    print('\n== 3) versi baru ACTIVE dengan connection type bawaan (BY_VERSION)')
    try:
        old_conn = psycopg.connect(dbname='f0v', **ODBC)
    except Exception as exc:                      # noqa: BLE001
        old_conn = None
        print(f'   koneksi lama gagal dibuka: {exc}')
    show('koneksi lama dibuka sebelum v2', conn_query(old_conn) if old_conn else '-', 'old_conn_before')
    outcome, status, dt, _ = deploy('f0v', 2, V2)
    show('deploy f0v v2 (valid)', f'{outcome}/{status} dalam {dt} s', 'v2_deploy')
    show('koneksi baru "f0v"', served_version('f0v'), 'q3_unversioned')
    show('koneksi baru "f0v.2"', served_version('f0v.2'), 'q3_versioned')

    print('\n== 4) v2 diberi connection type ANY')
    show('change-vdb-connection-type v2 ANY', set_type('f0v', 2, 'ANY'), 'v2_any')
    show('koneksi baru "f0v"', served_version('f0v'), 'q4_unversioned')
    show('koneksi lama (dibuka sebelum v2)', conn_query(old_conn), 'old_conn_after_switch')
    show('v1 diberi NONE', set_type('f0v', 1, 'NONE'), 'v1_none')
    show('koneksi baru "f0v.1" (diharapkan ditolak)', served_version('f0v.1'), 'q4_v1_versioned')
    show('koneksi lama sesudah v1 NONE', conn_query(old_conn), 'old_conn_after_none')
    show('undeploy v1', undeploy('f0v', 1), 'v1_removed')
    show('koneksi lama sesudah v1 di-undeploy', conn_query(old_conn), 'old_conn_after_undeploy')
    try:
        if old_conn:
            old_conn.close()
    except Exception:                             # noqa: BLE001
        pass
    show('koneksi baru "f0v" sesudah v1 dihapus', served_version('f0v'), 'q4_after_v1_removed')

    print('\n== 5) dua versi sama-sama ANY')
    outcome, status, dt, _ = deploy('f0v', 3, V3)
    show('deploy f0v v3 (BY_VERSION)', f'{outcome}/{status} dalam {dt} s', 'v3_deploy')
    show('koneksi baru "f0v" (v2 ANY, v3 BY_VERSION)', served_version('f0v'), 'q5_v2any_v3by')
    show('v3 diberi ANY', set_type('f0v', 3, 'ANY'), 'v3_any')
    show('koneksi baru "f0v" (v2 dan v3 ANY)', served_version('f0v'), 'q5_both_any')
    show('v2 diberi NONE', set_type('f0v', 2, 'NONE'), 'v2_none')
    show('koneksi baru "f0v" (v2 NONE, v3 ANY)', served_version('f0v'), 'q5_v3_only')
    show('rollback: v3 NONE, v2 ANY', (set_type('f0v', 3, 'NONE'), set_type('f0v', 2, 'ANY')), 'rollback_ops')
    show('koneksi baru "f0v" sesudah rollback', served_version('f0v'), 'q5_after_rollback')


def cleanup():
    print('\n== pembersihan')
    for dep in list(DEPLOYED):
        name, version = dep[:-len('-vdb.xml')].rsplit('-', 1)
        try:
            print(f'   {dep}: {undeploy(name, version)}')
        except Exception as exc:                  # noqa: BLE001
            print(f'   {dep}: gagal dibersihkan ({exc})')
    deps = op({'operation': 'read-children-names', 'child-type': 'deployment'}).get('result')
    print(f'   deployment sesudah uji: {deps}')
    REPORT['deployments_after'] = deps


if __name__ == '__main__':
    try:
        main()
    finally:
        try:
            cleanup()
        finally:
            path = os.path.join(OUT, f"f0_4b_{datetime.now().strftime('%Y%m%dT%H%M%S')}")
            os.makedirs(path, exist_ok=True)
            with open(os.path.join(path, 'report.json'), 'w') as fh:
                json.dump(REPORT, fh, indent=2, default=str)
            print(f'\nlaporan: results/f0/{os.path.basename(path)}/report.json')
