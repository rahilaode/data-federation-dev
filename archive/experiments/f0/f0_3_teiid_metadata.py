"""
F0.3 — Uji kelayakan: membaca Σ_S dari tabel sistem Teiid dengan driver Python
lewat transport ODBC (emulasi protokol PostgreSQL, port 35432).

Pertanyaan yang diuji:
  1. Driver Python mana yang kompatibel: psycopg2 (interpolasi di sisi klien)
     dan/atau psycopg 3 (parameter dikirim ke server).
  2. Apakah struktur Σ_S lengkap (skema, tabel, kolom, kunci) dan status VDB
     dapat dibaca dari SYS.* (Teiid Reference Guide, "System schema").
  3. Berapa lama satu kali "sync" Σ_S.

Dijalankan di kontainer Python sementara pada jaringan ascam-networks, sehingga
tidak ada perubahan pada kontainer ASCAM. Keluaran: ringkasan di layar dan
snapshot JSON di /out.
"""
import json
import os
import sys
import time
from datetime import datetime

HOST = os.getenv('TEIID_HOST', 'data-federation-teiid')
PORT = int(os.getenv('TEIID_ODBC_PORT', '35432'))
USER = os.getenv('TEIID_USER', 'user1')
PASSWORD = os.getenv('TEIID_PASSWORD', 'Password12345_')
VDB = os.getenv('TEIID_VDB', 'government')
OUT = os.getenv('OUT_DIR', '/out')

SYSTEM_SCHEMAS = ('SYS', 'SYSADMIN', 'pg_catalog')

EXCLUDE = "('SYS', 'SYSADMIN', 'pg_catalog')"

# Kolom yang diinginkan per tabel sistem. Nama kolom TIDAK diasumsikan dari
# dokumentasi saja: skrip menanyakan dulu kolom yang benar-benar ada di versi
# Teiid yang berjalan (SYS.Columns juga mendeskripsikan tabel SYS itu sendiri),
# lalu hanya meminta kolom yang tersedia. Pada uji pertama, 'ElementLength'
# (tercantum di Reference Guide) ternyata tidak ada di Teiid 16.
WANTED = {
    'VirtualDatabases': ['Name', 'Version', 'LoadingTimestamp', 'ActiveTimestamp'],
    'Schemas': ['Name', 'IsPhysical'],
    'Tables': ['SchemaName', 'Name', 'Type', 'NameInSource', 'IsPhysical'],
    'Columns': ['SchemaName', 'TableName', 'Name', 'Position', 'NameInSource', 'DataType',
                'NullType', 'Length', 'ElementLength', 'Precision', 'Scale'],
    'KeyColumns': ['SchemaName', 'TableName', 'Name', 'KeyName', 'KeyType', 'Position'],
}
FILTER = {
    'VirtualDatabases': '',
    'Schemas': f" WHERE Name NOT IN {EXCLUDE} ORDER BY Name",
    'Tables': f" WHERE SchemaName NOT IN {EXCLUDE} ORDER BY SchemaName, Name",
    'Columns': f" WHERE SchemaName NOT IN {EXCLUDE} ORDER BY SchemaName, TableName, Position",
    'KeyColumns': f" WHERE SchemaName NOT IN {EXCLUDE} ORDER BY SchemaName, TableName, KeyName, Position",
}
SNAPSHOT_KEY = {'VirtualDatabases': 'vdb', 'Schemas': 'schemas', 'Tables': 'tables',
                'Columns': 'columns', 'KeyColumns': 'keys'}


def quote_ident(name):
    """Identifier Teiid bertanda kutip ganda; kutip di dalam nama digandakan."""
    return '"' + name.replace('"', '""') + '"'


def available_columns(cur, sys_table):
    # literal konstan (bukan masukan pengguna), sengaja tanpa parameter
    cur.execute("SELECT Name FROM SYS.Columns WHERE SchemaName = 'SYS' "
                f"AND TableName = '{sys_table}' ORDER BY Position")
    return [r[0] for r in cur.fetchall()]


PARAM_QUERY = "SELECT Name, NameInSource FROM SYS.Columns WHERE SchemaName = %s AND TableName = %s ORDER BY Position"


def rows_as_dicts(cur):
    names = [d[0] for d in cur.description]
    return [dict(zip(names, (v.isoformat() if hasattr(v, 'isoformat') else v for v in r)))
            for r in cur.fetchall()]


def try_driver(label, connect):
    print(f'\n== driver: {label}')
    result = {'driver': label}
    try:
        t0 = time.perf_counter()
        conn = connect()
        result['connect_s'] = round(time.perf_counter() - t0, 3)
    except Exception as exc:                      # noqa: BLE001 — uji kelayakan
        print(f'   KONEKSI GAGAL: {type(exc).__name__}: {str(exc).strip()[:300]}')
        result['error'] = f'connect: {exc}'
        return result, None
    snapshot = {}
    try:
        conn.autocommit = True
        cur = conn.cursor()
        t0 = time.perf_counter()
        result['sys_columns'] = {}
        for sys_table, wanted in WANTED.items():
            have = available_columns(cur, sys_table)
            result['sys_columns'][sys_table] = have
            cols = [c for c in wanted if c in have]
            missing = [c for c in wanted if c not in have]
            if missing:
                print(f'   SYS.{sys_table}: kolom tidak tersedia -> {missing}')
            # Semua identifier diberi tanda kutip ganda: sebagian nama kolom sistem
            # adalah kata kunci Teiid (mis. PRECISION ada di bagian "Reserved words"
            # SQLParser.jj), dan identifier bertanda kutip memakai "..." (QUOTED_ID).
            select_list = ', '.join(quote_ident(c) for c in cols)
            cur.execute(f"SELECT {select_list} FROM SYS.{sys_table}{FILTER[sys_table]}")
            snapshot[SNAPSHOT_KEY[sys_table]] = rows_as_dicts(cur)
            result.setdefault('queries_ok', []).append(sys_table)
        result['sync_s'] = round(time.perf_counter() - t0, 3)
        print(f'   koneksi {result["connect_s"]} s | sync Σ_S {result["sync_s"]} s')
        try:
            cur.execute(PARAM_QUERY, ('dukcapil', 'master_penduduk'))
            result['param_query'] = f'OK ({len(cur.fetchall())} baris)'
        except Exception as exc:                  # noqa: BLE001
            result['param_query'] = f'GAGAL: {str(exc).strip()[:200]}'
        print(f'   kueri berparameter: {result["param_query"]}')
    except Exception as exc:                      # noqa: BLE001
        print(f'   KUERI GAGAL setelah {result.get("queries_ok", [])}: '
              f'{type(exc).__name__}: {str(exc).strip()[:300]}')
        result['error'] = f'query: {exc}'
        snapshot = None
    finally:
        conn.close()
    return result, snapshot


def connect_psycopg2(dbname=VDB):
    import psycopg2
    return psycopg2.connect(host=HOST, port=PORT, user=USER, password=PASSWORD,
                            dbname=dbname, sslmode='disable', gssencmode='disable',
                            connect_timeout=10)


def connect_psycopg3(dbname=VDB):
    import psycopg
    return psycopg.connect(host=HOST, port=PORT, user=USER, password=PASSWORD,
                           dbname=dbname, sslmode='disable', gssencmode='disable',
                           connect_timeout=10)


def main():
    results, snapshot = [], None
    for label, fn in (('psycopg2', connect_psycopg2), ('psycopg3', connect_psycopg3)):
        res, snap = try_driver(label, fn)
        results.append(res)
        snapshot = snapshot or snap

    print('\n== VDB yang tidak ada (penanganan galat)')
    try:
        connect_psycopg2('vdb_tidak_ada').close()
        print('   tidak ada galat (tidak diharapkan)')
    except Exception as exc:                      # noqa: BLE001
        print(f'   {type(exc).__name__}: {str(exc).strip()[:200]}')

    if snapshot is None:
        print('\nTidak ada driver yang berhasil membaca Σ_S.')
        sys.exit(1)

    print('\n== kolom SYS.Columns pada versi Teiid ini')
    print('   ' + ', '.join(results[0].get('sys_columns', {}).get('Columns', [])
                          or results[1].get('sys_columns', {}).get('Columns', [])))

    print('\n== ringkasan Σ_S VDB', VDB)
    for v in snapshot['vdb']:
        print(f"   VDB {v['Name']} v{v['Version']} | loading {v['LoadingTimestamp']} | active {v['ActiveTimestamp']}")
    print('   skema :', ', '.join(f"{s['Name']}(fisik={s['IsPhysical']})" for s in snapshot['schemas']))
    for t in snapshot['tables']:
        cols = [c for c in snapshot['columns']
                if c['SchemaName'] == t['SchemaName'] and c['TableName'] == t['Name']]
        pk = [k['Name'] for k in snapshot['keys'] if k['SchemaName'] == t['SchemaName']
              and k['TableName'] == t['Name'] and k['KeyType'] == 'Primary']
        desc = ', '.join(f"{c['Name']}:{c['DataType']}" + (f"<-{c['NameInSource']}" if c['NameInSource'] else '')
                         for c in cols)
        print(f"   {t['SchemaName']}.{t['Name']} [{t['Type']}] PK={pk}\n      {desc}")

    stamp = datetime.now().strftime('%Y%m%dT%H%M%S')
    path = os.path.join(OUT, f'f0_3_{stamp}')
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, f'sigma_s_{VDB}.json'), 'w') as fh:
        json.dump(snapshot, fh, indent=2, default=str)
    with open(os.path.join(path, 'drivers.json'), 'w') as fh:
        json.dump(results, fh, indent=2, default=str)
    print(f'\nsnapshot: results/f0/f0_3_{stamp}/')


if __name__ == '__main__':
    main()
