"""
F0.4 — Uji kelayakan: deploy VDB lewat WildFly HTTP management API (keputusan D9).

Pertanyaan yang diuji:
  1. Apakah VDB dapat di-deploy dan diganti dari host lain lewat HTTP (tanpa folder
     bersama), dengan autentikasi Digest ManagementRealm?
  2. Berapa lama operasi deploy dan penggantian (full-replace-deployment)
     sampai VDB berstatus ACTIVE?
  3. Apakah hasil operasi WildFly ("outcome") cukup untuk menilai keberhasilan,
     atau status VDB harus diperiksa terpisah (/subsystem=teiid:get-vdb)?
  4. Apakah kredensial yang salah ditolak?

Rujukan:
  - WildFly Core 11.1.1: DomainApiCheckHandler (PATH "/management",
    "/management/add-content"), ModelDescriptionConstants (full-replace-deployment,
    undeploy, hash).
  - Teiid Reference Guide, "Teiid Management CLI" (get-vdb, list-vdbs) dan catatan
    rilis tentang status VDB (LOADING, ACTIVE, FAILED, REMOVED).

VDB uji (f0d, f0e) hanya membaca tabel kemensos.program_bansos; tidak ada perubahan
skema sumber dan VDB government tidak disentuh. VDB uji dihapus di akhir.
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

DDL_V1 = ("CREATE FOREIGN TABLE program_bansos (program_id integer not null primary key, "
          "nama_program varchar(100)) OPTIONS(UPDATABLE 'FALSE');")
DDL_V2 = DDL_V1 + '\nALTER FOREIGN TABLE "program_bansos" ADD COLUMN "kolom_uji" varchar(10);'
DDL_BAD = DDL_V1 + '\nALTER FOREIGN TABLE "tabel_tidak_ada" ADD COLUMN "x" varchar(10);'


def vdb_xml(name, ddl):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<vdb name="{name}" version="1">
  <model visible="true" name="p">
    <source name="p" translator-name="postgresql" connection-jndi-name="java:/pgsql-kemensos"/>
    <metadata type="DDL"><![CDATA[
{ddl}
    ]]></metadata>
  </model>
</vdb>
'''.encode()


def op(payload, auth=AUTH, timeout=120):
    r = requests.post(MGMT, json=payload, auth=auth, timeout=timeout)
    try:
        body = r.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):   # mis. 401 dari lapisan autentikasi (bukan JSON operasi)
        body = {'outcome': 'http-error', 'text': (r.text or '')[:200]}
    body['_http'] = r.status_code
    return body


def upload(content, filename):
    r = requests.post(f'{MGMT}/add-content', files={'file': (filename, content)}, auth=AUTH, timeout=60)
    r.raise_for_status()
    return r.json()['result']['BYTES_VALUE']


def vdb_status(name):
    res = op({'operation': 'get-vdb', 'address': [{'subsystem': 'teiid'}],
              'vdb-name': name, 'vdb-version': '1'})
    if res.get('outcome') != 'success':
        return None, res.get('failure-description')
    r = res.get('result') or {}
    errors = {k: v for k, v in r.items() if 'valid' in k.lower() or 'error' in k.lower()}
    return r.get('status'), errors


def wait_status(name, want=('ACTIVE', 'FAILED'), timeout=60):
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        status, errors = vdb_status(name)
        if status in want:
            return status, errors, round(time.perf_counter() - t0, 3)
        time.sleep(0.1)
    return 'TIMEOUT', None, round(time.perf_counter() - t0, 3)


def odbc(vdb, sql):
    try:
        with psycopg.connect(dbname=vdb, **ODBC) as conn:
            with conn.cursor() as cur:
                cur.execute(sql)
                return [list(r) for r in cur.fetchall()]
    except Exception as exc:                      # noqa: BLE001 — uji kelayakan
        return f'GALAT: {str(exc).strip()[:200]}'


def step(title):
    print(f'\n== {title}')


def main():
    step('1) server dan autentikasi')
    res = op({'operation': 'read-attribute', 'name': 'server-state'})
    print(f"   server-state: {res.get('result')} (HTTP {res['_http']})")
    ver = op({'operation': 'read-attribute', 'name': 'product-version'})
    print(f"   product-version: {ver.get('result')}")
    bad = op({'operation': 'read-attribute', 'name': 'server-state'},
             auth=HTTPDigestAuth('admin', 'password-salah'))
    print(f"   kredensial salah -> HTTP {bad['_http']}")
    deps = op({'operation': 'read-children-names', 'child-type': 'deployment'})
    print(f"   deployment saat ini: {deps.get('result')}")
    REPORT['server'] = {'state': res.get('result'), 'version': ver.get('result'),
                        'wrong_credentials_http': bad['_http'], 'deployments_before': deps.get('result')}

    step('2) deploy VDB baru lewat HTTP (f0d)')
    t0 = time.perf_counter()
    h1 = upload(vdb_xml('f0d', DDL_V1), 'f0d-vdb.xml')
    t_up = time.perf_counter()
    res = op({'operation': 'add', 'address': [{'deployment': 'f0d-vdb.xml'}],
              'content': [{'hash': {'BYTES_VALUE': h1}}], 'enabled': True})
    t_op = time.perf_counter()
    status, errors, t_wait = wait_status('f0d')
    print(f"   upload {t_up - t0:.3f} s | operasi add {t_op - t_up:.3f} s -> {res.get('outcome')} "
          f"| status {status} setelah {t_wait} s")
    rows = odbc('f0d', 'SELECT program_id, nama_program FROM p.program_bansos ORDER BY program_id')
    print(f"   kueri ODBC: {rows if isinstance(rows, str) else f'{len(rows)} baris'}")
    act1 = odbc('f0d', 'SELECT "ActiveTimestamp" FROM SYS.VirtualDatabases')
    REPORT['deploy'] = {'upload_s': round(t_up - t0, 3), 'op_s': round(t_op - t_up, 3),
                        'outcome': res.get('outcome'), 'status': status, 'wait_active_s': t_wait,
                        'query': rows if isinstance(rows, str) else len(rows), 'active_ts': str(act1)}

    step('3) ganti isi VDB (full-replace-deployment): tambah kolom lewat ALTER')
    t0 = time.perf_counter()
    h2 = upload(vdb_xml('f0d', DDL_V2), 'f0d-vdb.xml')
    t_up = time.perf_counter()
    res = op({'operation': 'full-replace-deployment', 'name': 'f0d-vdb.xml',
              'content': [{'hash': {'BYTES_VALUE': h2}}], 'enabled': True})
    t_op = time.perf_counter()
    status, errors, t_wait = wait_status('f0d')
    cols = odbc('f0d', "SELECT \"Name\" FROM SYS.Columns WHERE \"TableName\" = 'program_bansos' ORDER BY \"Position\"")
    act2 = odbc('f0d', 'SELECT "ActiveTimestamp" FROM SYS.VirtualDatabases')
    print(f"   upload {t_up - t0:.3f} s | operasi replace {t_op - t_up:.3f} s -> {res.get('outcome')} "
          f"| status {status} setelah {t_wait} s")
    print(f"   kolom sesudah replace: {cols}")
    print(f"   ActiveTimestamp: {act1} -> {act2}")
    REPORT['replace'] = {'upload_s': round(t_up - t0, 3), 'op_s': round(t_op - t_up, 3),
                         'outcome': res.get('outcome'), 'status': status, 'wait_active_s': t_wait,
                         'columns': cols, 'active_ts_after': str(act2)}

    step('4) VDB dengan DDL tidak valid (f0e): apakah "outcome" saja cukup?')
    h3 = upload(vdb_xml('f0e', DDL_BAD), 'f0e-vdb.xml')
    t0 = time.perf_counter()
    res = op({'operation': 'add', 'address': [{'deployment': 'f0e-vdb.xml'}],
              'content': [{'hash': {'BYTES_VALUE': h3}}], 'enabled': True})
    t_op = time.perf_counter() - t0
    status, errors, t_wait = wait_status('f0e')
    print(f"   operasi add {t_op:.3f} s -> outcome {res.get('outcome')}")
    if res.get('outcome') != 'success':
        print(f"   failure-description: {str(res.get('failure-description'))[:300]}")
    print(f"   status VDB: {status} | galat: {str(errors)[:300]}")
    REPORT['invalid'] = {'outcome': res.get('outcome'),
                         'failure': str(res.get('failure-description'))[:500],
                         'status': status, 'errors': str(errors)[:500]}

    step('5) daftar VDB menurut Teiid')
    lst = op({'operation': 'list-vdbs', 'address': [{'subsystem': 'teiid'}]})
    summary = [(v.get('vdb-name'), v.get('vdb-version'), v.get('status'))
               for v in (lst.get('result') or []) if isinstance(v, dict)]
    print(f"   {summary}")
    REPORT['list_vdbs'] = summary


def cleanup():
    step('pembersihan')
    for name in ('f0d-vdb.xml', 'f0e-vdb.xml'):
        try:
            u = op({'operation': 'undeploy', 'address': [{'deployment': name}]})
            r = op({'operation': 'remove', 'address': [{'deployment': name}]})
            print(f"   {name}: undeploy {u.get('outcome')}, remove {r.get('outcome')}")
        except Exception as exc:                  # noqa: BLE001 — pembersihan tetap lanjut
            print(f"   {name}: pembersihan gagal ({exc})")
    deps = op({'operation': 'read-children-names', 'child-type': 'deployment'})
    print(f"   deployment sesudah uji: {deps.get('result')}")
    REPORT['deployments_after'] = deps.get('result')


if __name__ == '__main__':
    try:
        main()
    finally:
        try:
            cleanup()
        finally:
            path = os.path.join(OUT, f"f0_4_{datetime.now().strftime('%Y%m%dT%H%M%S')}")
            os.makedirs(path, exist_ok=True)
            with open(os.path.join(path, 'report.json'), 'w') as fh:
                json.dump(REPORT, fh, indent=2, default=str)
            print(f'\nlaporan: results/f0/{os.path.basename(path)}/report.json')
