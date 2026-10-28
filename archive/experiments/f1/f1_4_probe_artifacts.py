"""
F1.4a — Penyelidikan: dari mana isi spesifikasi VDB dapat diambil secara jarak jauh?

Kandidat (hanya MEMBACA; tidak ada perubahan pada Teiid):
  1. Operasi manajemen subsystem Teiid (daftar lengkap dan deskripsi parameternya).
  2. Deployment WildFly `government-vdb.xml`: atribut dan operasi (mis. read-content).
  3. Operasi Teiid `get-vdb` (DDL per model) dan, bila ada, `get-schema`.
  4. Tabel sistem SYSADMIN (mis. SYSADMIN.VDBResources) lewat transport ODBC.
Nama operasi dan parameter DITANYAKAN ke server (read-operation-names,
read-operation-description), bukan diasumsikan dari dokumentasi.
"""
import json
import os
from datetime import datetime

import httpx
import psycopg

TEIID = os.getenv('TEIID_HOST', 'data-federation-teiid')
MGMT = f'http://{TEIID}:9990/management'
AUTH = httpx.DigestAuth(os.getenv('MGMT_USER', 'admin'), os.getenv('MGMT_PASSWORD', 'Password12345_'))
ODBC = dict(host=TEIID, port=35432, user='user1', password=os.getenv('TEIID_PASSWORD', 'Password12345_'),
            dbname='government', sslmode='disable', gssencmode='disable', connect_timeout=10)
DEPLOYMENT = os.getenv('VDB_DEPLOYMENT', 'government-vdb.xml')
REPORT = {}
client = httpx.Client(auth=AUTH, timeout=30)


def op(payload):
    r = client.post(MGMT, json=payload)
    try:
        return r.json()
    except ValueError:
        return {'outcome': f'http {r.status_code}', 'text': r.text[:300]}


def short(value, n=400):
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= n else text[:n] + f'... ({len(text)} karakter)'


def section(title):
    print(f'\n== {title}')


def main():
    teiid = [{'subsystem': 'teiid'}]
    section('1) operasi subsystem Teiid')
    names = op({'operation': 'read-operation-names', 'address': teiid}).get('result', [])
    print('  ', ', '.join(sorted(names)))
    REPORT['teiid_operations'] = names
    for name in ('get-vdb', 'get-schema', 'list-vdbs'):
        if name in names:
            desc = op({'operation': 'read-operation-description', 'name': name, 'address': teiid})
            params = {k: {'type': str(v.get('type')), 'required': v.get('required')}
                      for k, v in (desc.get('result', {}).get('request-properties') or {}).items()}
            print(f'   {name}: parameter {params}')
            REPORT.setdefault('teiid_op_params', {})[name] = params

    section(f'2) deployment WildFly {DEPLOYMENT}')
    dep = [{'deployment': DEPLOYMENT}]
    res = op({'operation': 'read-resource', 'address': dep, 'include-runtime': True})
    print('   atribut:', short({k: v for k, v in (res.get('result') or {}).items()
                                if k in ('content', 'managed', 'persistent', 'enabled', 'status', 'owner')}))
    dep_ops = op({'operation': 'read-operation-names', 'address': dep}).get('result', [])
    print('   operasi:', ', '.join(sorted(dep_ops)))
    REPORT['deployment'] = {'resource': res.get('result'), 'operations': dep_ops}
    if 'read-content' in dep_ops:
        for params in ({}, {'path': DEPLOYMENT}):
            out = op({'operation': 'read-content', 'address': dep, **params})
            print(f'   read-content {params or "(tanpa path)"}: {out.get("outcome")} '
                  f'{short(out.get("failure-description") or out.get("result"), 200)}')
            REPORT.setdefault('read_content', []).append({'params': params, 'response': out})

    section('3) get-vdb: DDL per model')
    vdb = op({'operation': 'get-vdb', 'address': teiid, 'vdb-name': 'government', 'vdb-version': '1'})
    result = vdb.get('result') or {}
    for model in result.get('models', []):
        for md in model.get('metadatas', []):
            text = md.get('metadata') or ''
            print(f"   model {model.get('model-name')}: {md.get('metadata-type')}, {len(text)} karakter; "
                  f"awal: {short(text.strip(), 90)}")
    REPORT['get_vdb_keys'] = sorted(result)
    REPORT['get_vdb'] = result
    if 'get-schema' in names:
        params = REPORT['teiid_op_params']['get-schema']
        request = {'operation': 'get-schema', 'address': teiid, 'vdb-name': 'government', 'vdb-version': '1'}
        if 'model-name' in params:
            request['model-name'] = 'dukcapil'
        out = op(request)
        print(f'   get-schema {request.get("model-name", "")}: {out.get("outcome")} '
              f'{short(out.get("result") or out.get("failure-description"), 300)}')
        REPORT['get_schema'] = out

    section('4) tabel sistem SYSADMIN lewat ODBC')
    with psycopg.connect(**ODBC) as conn, conn.cursor() as cur:
        cur.execute('SELECT "Name" FROM SYS.Tables WHERE "SchemaName" = \'SYSADMIN\' ORDER BY "Name"')
        tables = [r[0] for r in cur.fetchall()]
        print('   tabel:', ', '.join(tables))
        REPORT['sysadmin_tables'] = tables
        if 'VDBResources' in tables:
            cur.execute('SELECT "Name" FROM SYS.Columns WHERE "SchemaName" = \'SYSADMIN\' '
                        'AND "TableName" = \'VDBResources\' ORDER BY "Position"')
            cols = [r[0] for r in cur.fetchall()]
            print('   kolom VDBResources:', cols)
            cur.execute('SELECT "resourcePath" FROM SYSADMIN.VDBResources')
            paths = [r[0] for r in cur.fetchall()]
            print('   resourcePath:', paths)
            REPORT['vdb_resources'] = {'columns': cols, 'paths': paths}
            for path in paths:
                if path.endswith('vdb.xml') or path.endswith('-vdb.xml'):
                    cur.execute('SELECT "contents" FROM SYSADMIN.VDBResources WHERE "resourcePath" = %s', (path,))
                    raw = cur.fetchone()[0]
                    text = bytes(raw).decode('utf-8', 'replace') if raw is not None else ''
                    print(f'   isi {path}: {len(text)} karakter; awal: {short(text.strip(), 120)}')
                    REPORT['vdb_resources']['content'] = text


if __name__ == '__main__':
    try:
        main()
    finally:
        out = f"/out/f1_4a_{datetime.now().strftime('%Y%m%dT%H%M%S')}.json"
        os.makedirs('/out', exist_ok=True)
        with open(out, 'w') as fh:
            json.dump(REPORT, fh, indent=2, default=str)
        print(f'\nlaporan: results/f1/{os.path.basename(out)}')
