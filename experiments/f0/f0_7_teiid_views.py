"""
F0.7 — Uji kelayakan: view Teiid, dependensi kolom, dan dampak perubahan kolom
foreign table terhadap view.

Dasar: Teiid Reference Guide, "SYSADMIN schema" (SYSADMIN.Usage, SYSADMIN.Views,
SYSADMIN.MatViews) dan "Schema object DDL" (CREATE VIEW, ALTER TABLE).

Pertanyaan yang diuji:
  1. Kolom apa yang tersedia di SYSADMIN.Usage dan SYSADMIN.Views pada Teiid 16?
  2. Apakah Usage membedakan dependensi tingkat kolom (proyeksi/ekspresi) dan
     tingkat tabel (WHERE, JOIN)? Bagaimana dengan view bertingkat dan SELECT *?
  3. Dapatkah definisi view (Body) dianalisis dengan sqlglot untuk membedakan
     kolom pass-through dan kolom ekspresi?
  4. Apa yang terjadi pada VDB bila kolom foreign table yang dipakai view:
     di-DROP (proyeksi / ekspresi / WHERE / JOIN), di-RENAME lewat NAMEINSOURCE (D8),
     di-RENAME lewat RENAME COLUMN? Bagaimana view SELECT * bereaksi terhadap ADD/DROP?

Seluruh perubahan hanya pada metadata VDB uji (f0w, versi 1..8) lewat management API;
skema sumber tidak diubah dan VDB government tidak disentuh. VDB uji dihapus di akhir.
"""
import json
import os
import time
from datetime import datetime

import psycopg
import requests
import sqlglot
from sqlglot import exp
from requests.auth import HTTPDigestAuth

TEIID = os.getenv('TEIID_HOST', 'data-federation-teiid')
MGMT = f"http://{TEIID}:{os.getenv('MGMT_PORT', '9990')}/management"
AUTH = HTTPDigestAuth(os.getenv('MGMT_USER', 'admin'), os.getenv('MGMT_PASSWORD', 'Password12345_'))
ODBC = dict(host=TEIID, port=int(os.getenv('TEIID_ODBC_PORT', '35432')),
            user=os.getenv('TEIID_USER', 'user1'), password=os.getenv('TEIID_PASSWORD', 'Password12345_'),
            sslmode='disable', gssencmode='disable', connect_timeout=10)
OUT = os.getenv('OUT_DIR', '/out')
VDB = 'f0w'
REPORT = {}
DEPLOYED = []

# Model fisik: dua foreign table. kolom_ekstra hanya ada di metadata (tidak pernah dikueri datanya).
PHYSICAL = '''CREATE FOREIGN TABLE "program_bansos" ("program_id" integer not null primary key,
  "nama_program" varchar(100), "tipe_program" varchar(50), "nominal" integer, "kolom_ekstra" varchar(10))
  OPTIONS(UPDATABLE 'FALSE');
CREATE FOREIGN TABLE "transaksi_bansos" ("transaksi_id" integer not null primary key,
  "program_id" integer, "penerima_id" integer, "status" varchar(50))
  OPTIONS(UPDATABLE 'FALSE');'''

# Model virtual: empat pola pemakaian kolom.
VIRTUAL = '''CREATE VIEW "program_ringkas" AS
  SELECT pb.program_id, pb.nama_program, UCASE(pb.tipe_program) AS tipe_upper
  FROM p.program_bansos AS pb WHERE pb.nominal > 0;
CREATE VIEW "program_semua" AS SELECT * FROM p.program_bansos;
CREATE VIEW "transaksi_program" AS
  SELECT t.transaksi_id, t.status, pr.nama_program
  FROM p.transaksi_bansos AS t INNER JOIN p.program_bansos AS pr ON t.program_id = pr.program_id;
CREATE VIEW "program_bertingkat" AS SELECT program_id, tipe_upper FROM v.program_ringkas;'''

CHANGES = {  # versi: (keterangan, pernyataan yang DITAMBAHKAN ke model fisik)
    2: ('DROP kolom ekspresi view (tipe_program)', 'ALTER FOREIGN TABLE "program_bansos" DROP COLUMN "tipe_program";'),
    3: ('DROP kolom WHERE view (nominal)', 'ALTER FOREIGN TABLE "program_bansos" DROP COLUMN "nominal";'),
    4: ('DROP kolom JOIN view (transaksi_bansos.program_id)', 'ALTER FOREIGN TABLE "transaksi_bansos" DROP COLUMN "program_id";'),
    5: ('DROP kolom yang hanya dipakai view SELECT * (kolom_ekstra)', 'ALTER FOREIGN TABLE "program_bansos" DROP COLUMN "kolom_ekstra";'),
    6: ('ADD kolom baru (kolom_baru) — apakah view SELECT * ikut berubah?', 'ALTER FOREIGN TABLE "program_bansos" ADD COLUMN "kolom_baru" varchar(10);'),
    7: ('RENAME lewat NAMEINSOURCE (D8) pada tipe_program',
        'ALTER FOREIGN TABLE "program_bansos" ALTER COLUMN "tipe_program" OPTIONS (SET NAMEINSOURCE \'"tipe_program_baru"\');'),
    8: ('RENAME COLUMN Teiid tipe_program -> tipe', 'ALTER FOREIGN TABLE "program_bansos" RENAME COLUMN "tipe_program" TO "tipe";'),
}


def vdb_xml(version, physical_extra=''):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<vdb name="{VDB}" version="{version}">
  <model visible="true" name="p">
    <source name="p" translator-name="postgresql" connection-jndi-name="java:/pgsql-kemensos"/>
    <metadata type="DDL"><![CDATA[
{PHYSICAL}
{physical_extra}
    ]]></metadata>
  </model>
  <model visible="true" type="VIRTUAL" name="v">
    <metadata type="DDL"><![CDATA[
{VIRTUAL}
    ]]></metadata>
  </model>
</vdb>
'''.encode()


def op(payload):
    r = requests.post(MGMT, json=payload, auth=AUTH, timeout=120)
    try:
        body = r.json()
    except ValueError:
        body = None
    return body if isinstance(body, dict) else {'outcome': 'http-error', 'text': (r.text or '')[:200]}


def deploy(version, extra=''):
    dep = f'{VDB}-{version}-vdb.xml'
    r = requests.post(f'{MGMT}/add-content', files={'file': (dep, vdb_xml(version, extra))}, auth=AUTH, timeout=60)
    r.raise_for_status()
    res = op({'operation': 'add', 'address': [{'deployment': dep}],
              'content': [{'hash': {'BYTES_VALUE': r.json()['result']['BYTES_VALUE']}}], 'enabled': True})
    DEPLOYED.append(dep)
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 60:
        g = op({'operation': 'get-vdb', 'address': [{'subsystem': 'teiid'}],
                'vdb-name': VDB, 'vdb-version': str(version)})
        result = g.get('result') or {}
        if result.get('status') in ('ACTIVE', 'FAILED'):
            errors = [f"[{m.get('model-name')}] {e.get('message')}"
                      for m in result.get('models', []) for e in m.get('validity-errors', [])
                      if e.get('severity') == 'ERROR']
            return res.get('outcome'), result['status'], errors
        time.sleep(0.1)
    return res.get('outcome'), 'TIMEOUT', []


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def sys_cols(cur, schema, table):
    cur.execute('SELECT "Name" FROM SYS.Columns WHERE "SchemaName" = %s AND "TableName" = %s ORDER BY "Position"',
                (schema, table))
    return [r[0] for r in cur.fetchall()]


def query(cur, schema, table, wanted, where=''):
    have = sys_cols(cur, schema, table)
    cols = [c for c in wanted if c in have] if wanted else have
    cur.execute(f'SELECT {", ".join(quote(c) for c in cols)} FROM {schema}.{table} {where}')
    return have, cols, [dict(zip(cols, r)) for r in cur.fetchall()]


def view_columns(cur):
    cur.execute('SELECT "TableName", "Name" FROM SYS.Columns WHERE "SchemaName" = \'v\' '
                'ORDER BY "TableName", "Position"')
    out = {}
    for t, c in cur.fetchall():
        out.setdefault(t, []).append(c)
    return out


def classify_view(body):
    """Klasifikasi kolom view dari Body dengan sqlglot (tanpa regex)."""
    try:
        tree = sqlglot.parse_one(body)
    except Exception as exc:                      # noqa: BLE001
        return {'parse': f'GAGAL: {exc}'}
    select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    if select is None:
        return {'parse': 'bukan SELECT'}
    cols = {}
    for e in select.expressions:
        if isinstance(e, exp.Star) or (isinstance(e, exp.Column) and isinstance(e.this, exp.Star)):
            cols['*'] = 'star'
            continue
        inner = e.this if isinstance(e, exp.Alias) else e
        kind = 'passthrough' if isinstance(inner, exp.Column) else 'expression'
        cols[e.alias_or_name] = f"{kind} <- {sorted({c.sql() for c in inner.find_all(exp.Column)})}"
    where = select.args.get('where')
    joins = select.args.get('joins') or []
    return {
        'parse': 'OK',
        'columns': cols,
        'where_columns': sorted({c.sql() for c in where.find_all(exp.Column)}) if where else [],
        'join_columns': sorted({c.sql() for j in joins for c in j.find_all(exp.Column)}),
    }


def main():
    print('\n== 1) VDB dasar (v1): foreign table + empat view')
    outcome, status, errors = deploy(1)
    print(f'   deploy v1: {outcome}/{status} {errors}')
    REPORT['v1'] = {'outcome': outcome, 'status': status, 'errors': errors}
    if status != 'ACTIVE':
        return
    with psycopg.connect(dbname=f'{VDB}.1', **ODBC) as conn, conn.cursor() as cur:
        cur.execute('SELECT "Name" FROM SYS.Columns WHERE "SchemaName" = \'SYSADMIN\' '
                    'AND "TableName" = \'Usage\' ORDER BY "Position"')
        usage_cols = [r[0] for r in cur.fetchall()]
        cur.execute('SELECT "Name" FROM SYS.Columns WHERE "SchemaName" = \'SYSADMIN\' '
                    'AND "TableName" = \'Views\' ORDER BY "Position"')
        views_cols = [r[0] for r in cur.fetchall()]
        print(f'   kolom SYSADMIN.Usage : {usage_cols}')
        print(f'   kolom SYSADMIN.Views : {views_cols}')
        REPORT['sysadmin_columns'] = {'Usage': usage_cols, 'Views': views_cols}

        cur.execute('SELECT "Name", "Type", "IsPhysical" FROM SYS.Tables WHERE "SchemaName" = \'v\' ORDER BY "Name"')
        print(f'   SYS.Tables skema v   : {cur.fetchall()}')
        print(f'   kolom view (SYS)     : {view_columns(cur)}')

        print('\n== 2) SYSADMIN.Usage untuk skema v')
        wanted = ['object_type', 'Name', 'ElementName', 'Uses_object_type',
                  'Uses_SchemaName', 'Uses_Name', 'Uses_ElementName']
        _, cols, rows = query(cur, 'SYSADMIN', 'Usage', wanted)
        cur.execute('SELECT "UID" FROM SYS.Tables WHERE "SchemaName" = \'v\'')
        view_uids = {r[0] for r in cur.fetchall()}
        cur.execute('SELECT "UID" FROM SYS.Columns WHERE "SchemaName" = \'v\'')
        view_uids |= {r[0] for r in cur.fetchall()}
        cur.execute(f'SELECT {", ".join(quote(c) for c in ["UID"] + cols)} FROM SYSADMIN.Usage')
        usage = [dict(zip(['UID'] + cols, r)) for r in cur.fetchall() if r[0] in view_uids]
        for u in usage:
            user = f"{u.get('Name')}.{u.get('ElementName') or '*'}"
            used = f"{u.get('Uses_SchemaName')}.{u.get('Uses_Name')}.{u.get('Uses_ElementName') or '*'}"
            print(f"   {u.get('object_type', '?'):7s} {user:38s} <- {u.get('Uses_object_type', '?'):7s} {used}")
        REPORT['usage'] = usage

        print('\n== 3) SYSADMIN.Views dan klasifikasi Body dengan sqlglot')
        _, _, views = query(cur, 'SYSADMIN', 'Views', ['SchemaName', 'Name', 'Body'], "WHERE \"SchemaName\" = 'v'")
        REPORT['views'] = {}
        for v in views:
            body = v['Body'] if isinstance(v['Body'], str) else str(v['Body'])
            cls = classify_view(body)
            print(f"   {v['Name']}: {body.strip()[:110]}")
            print(f"      -> {cls}")
            REPORT['views'][v['Name']] = {'body': body, 'classification': cls}

        cur.execute('SELECT count(*) FROM v.program_ringkas')
        print(f'\n   data view program_ringkas terbaca: {cur.fetchone()[0]} baris')

    print('\n== 4) dampak perubahan kolom foreign table (tiap perubahan = versi baru)')
    REPORT['changes'] = {}
    for version, (label, stmt) in CHANGES.items():
        outcome, status, errors = deploy(version, stmt)
        info = {'label': label, 'outcome': outcome, 'status': status, 'errors': errors}
        line = f'   v{version} {label:62s} {outcome}/{status}'
        if status == 'ACTIVE':
            try:
                with psycopg.connect(dbname=f'{VDB}.{version}', **ODBC) as conn, conn.cursor() as cur:
                    info['view_columns'] = view_columns(cur)
                    info['program_semua'] = info['view_columns'].get('program_semua')
                    if version == 7:
                        cur.execute('SELECT "Name", "NameInSource" FROM SYS.Columns WHERE "SchemaName" = \'p\' '
                                    'AND "TableName" = \'program_bansos\' ORDER BY "Position"')
                        info['physical_columns'] = cur.fetchall()
            except Exception as exc:              # noqa: BLE001
                info['query_error'] = str(exc)[:200]
            line += f"  | program_semua={info.get('program_semua')}"
        print(line)
        for e in errors:
            print(f'        {e[:220]}')
        if 'physical_columns' in info:
            print(f"        kolom fisik: {info['physical_columns']}")
        REPORT['changes'][version] = info


def cleanup():
    print('\n== pembersihan')
    for dep in list(DEPLOYED):
        op({'operation': 'undeploy', 'address': [{'deployment': dep}]})
        r = op({'operation': 'remove', 'address': [{'deployment': dep}]})
        print(f"   {dep}: {r.get('outcome')}")
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
            path = os.path.join(OUT, f"f0_7_{datetime.now().strftime('%Y%m%dT%H%M%S')}")
            os.makedirs(path, exist_ok=True)
            with open(os.path.join(path, 'report.json'), 'w') as fh:
                json.dump(REPORT, fh, indent=2, default=str)
            print(f'\nlaporan: results/f0/{os.path.basename(path)}/report.json')
