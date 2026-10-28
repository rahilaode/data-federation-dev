"""
F0.8 — Uji kelayakan: penyesuaian view Teiid lewat pernyataan yang DITAMBAHKAN ke metadata DDL
VDB, saat kolom foreign table yang diteruskan view dihapus (ADR-0023).

Pertanyaan:
  1. Apakah Teiid 16 menerima `ALTER VIEW v AS <kueri baru>` di metadata DDL VDB bila kueri
     baru memproyeksikan kolom LEBIH SEDIKIT? Reference Guide menyatakan ALTER VIEW tidak boleh
     mengubah informasi kolom (teiid-documents, *Schema object DDL*, hlm. 356–357), tetapi tidak
     jelas apakah itu berlaku bagi view yang kolomnya diturunkan dari kueri.
  2. Bila tidak, apakah `DROP VIEW v; CREATE VIEW v AS ...` (BNF *drop table*, hlm. 819)
     dapat dipakai sebagai cadangan?
  3. Apakah view bertingkat harus ikut disesuaikan (perambatan), dan apakah view berkolom inline
     memang ditolak?

Definisi baru dibentuk dengan fungsi yang SAMA dengan Knowledge (ascam_knowledge.viewsql)
dari Body yang dibaca di SYSADMIN.Views, sehingga hasilnya berlaku untuk rencana ASCAM.

Seluruh perubahan hanya pada VDB uji `f0av` (versi 1..7) lewat management API; skema sumber
tidak diubah dan VDB government tidak disentuh. VDB uji dihapus di akhir.
Jalankan lewat experiments/f0/f0_8.sh.
"""
import json
import os
import sys
import time
from datetime import datetime

import psycopg
import requests
from requests.auth import HTTPDigestAuth

sys.path.insert(0, os.getenv('KNOWLEDGE_SRC', '/knowledge'))
from ascam_knowledge.viewsql import RewriteError, remove_projection   # noqa: E402

TEIID = os.getenv('TEIID_HOST', 'data-federation-teiid')
MGMT = f"http://{TEIID}:{os.getenv('MGMT_PORT', '9990')}/management"
AUTH = HTTPDigestAuth(os.getenv('MGMT_USER', 'admin'), os.environ['MGMT_PASSWORD'])
ODBC = dict(host=TEIID, port=int(os.getenv('TEIID_ODBC_PORT', '35432')),
            user=os.getenv('TEIID_USER', 'user1'), password=os.environ['TEIID_PASSWORD'],
            sslmode='disable', gssencmode='disable', connect_timeout=10)
OUT = os.getenv('OUT_DIR', '/out')
VDB = 'f0av'
KOLOM = 'no_kartu_keluarga'
REPORT: dict = {'kasus': {}}
DEPLOYED: list[str] = []

PHYSICAL = '''CREATE FOREIGN TABLE "penerima_manfaat" (
  "penerima_id" integer not null primary key,
  "no_kartu_keluarga" varchar(16),
  "aktif" boolean
) OPTIONS(UPDATABLE 'FALSE');'''
DROP = f'ALTER FOREIGN TABLE "penerima_manfaat" DROP COLUMN "{KOLOM}";'

# View pass-through (dengan WHERE pada kolom lain) dan view bertingkat di atasnya
VIEWS_A = '''CREATE VIEW v_penerima_aktif AS
  SELECT penerima_id, no_kartu_keluarga FROM p.penerima_manfaat WHERE aktif = TRUE;
CREATE VIEW v_penerima_publik AS
  SELECT penerima_id, no_kartu_keluarga FROM v.v_penerima_aktif;'''
# View berkolom inline
VIEWS_B = '''CREATE VIEW v_penerima_inline (penerima_id integer, no_kartu_keluarga string) AS
  SELECT penerima_id, no_kartu_keluarga FROM p.penerima_manfaat;'''


def vdb_xml(version: int, physical_extra: str, views: str, views_extra: str) -> bytes:
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
{views}
{views_extra}
    ]]></metadata>
  </model>
</vdb>
'''.encode()


def op(payload: dict) -> dict:
    r = requests.post(MGMT, json=payload, auth=AUTH, timeout=120)
    try:
        body = r.json()
    except ValueError:
        body = None
    return body if isinstance(body, dict) else {'outcome': 'http-error', 'text': (r.text or '')[:200]}


def deploy(version: int, xml: bytes) -> tuple[str, list[str]]:
    dep = f'{VDB}-{version}-vdb.xml'
    r = requests.post(f'{MGMT}/add-content', files={'file': (dep, xml)}, auth=AUTH, timeout=60)
    r.raise_for_status()
    op({'operation': 'add', 'address': [{'deployment': dep}],
        'content': [{'hash': {'BYTES_VALUE': r.json()['result']['BYTES_VALUE']}}], 'enabled': True})
    DEPLOYED.append(dep)
    mulai = time.perf_counter()
    while time.perf_counter() - mulai < 90:
        g = op({'operation': 'get-vdb', 'address': [{'subsystem': 'teiid'}],
                'vdb-name': VDB, 'vdb-version': str(version)})
        result = g.get('result') or {}
        if result.get('status') in ('ACTIVE', 'FAILED'):
            errors = [f"[{m.get('model-name')}] {e.get('message')}"
                      for m in result.get('models', []) for e in m.get('validity-errors', [])
                      if e.get('severity') == 'ERROR']
            return result['status'], errors
        time.sleep(0.2)
    return 'TIMEOUT', []


def amati(version: int) -> dict:
    """Kolom view yang terlihat klien dan jumlah baris yang terbaca lewat view."""
    info: dict = {'kolom_view': {}, 'baris': {}}
    with psycopg.connect(dbname=f'{VDB}.{version}', **ODBC) as conn, conn.cursor() as cur:
        cur.execute('SELECT "TableName", "Name" FROM SYS.Columns WHERE "SchemaName" = \'v\' '
                    'ORDER BY "TableName", "Position"')
        for tabel, kolom in cur.fetchall():
            info['kolom_view'].setdefault(tabel, []).append(kolom)
        for tabel in info['kolom_view']:
            try:
                cur.execute(f'SELECT count(*) FROM v."{tabel}"')
                info['baris'][tabel] = cur.fetchone()[0]
            except Exception as exc:                                  # noqa: BLE001
                conn.rollback()
                info['baris'][tabel] = f'GALAT: {str(exc)[:160]}'
    return info


def bodies(version: int) -> dict[str, str]:
    with psycopg.connect(dbname=f'{VDB}.{version}', **ODBC) as conn, conn.cursor() as cur:
        cur.execute('SELECT "Name", "Body" FROM SYSADMIN.Views WHERE "SchemaName" = \'v\'')
        return {nama: str(body) for nama, body in cur.fetchall()}


def kasus(version: int, label: str, harapan: str, xml: bytes, tambahan: list[str]) -> str:
    status, errors = deploy(version, xml)
    info = {'label': label, 'harapan': harapan, 'status': status, 'errors': errors,
            'pernyataan_tambahan': tambahan}
    if status == 'ACTIVE':
        try:
            info.update(amati(version))
        except Exception as exc:                                      # noqa: BLE001
            info['galat_kueri'] = str(exc)[:200]
    REPORT['kasus'][version] = info
    tanda = 'sesuai' if status == harapan else 'BERBEDA'
    print(f'  v{version} {label:58s} {status:7s} (harapan {harapan}, {tanda})')
    for e in errors:
        print(f'       {e[:220]}')
    if info.get('kolom_view'):
        print(f"       kolom view: {info['kolom_view']}")
        print(f"       baris terbaca: {info['baris']}")
    return status


def main() -> None:
    print('== F0.8: penyesuaian view Teiid lewat metadata DDL VDB')
    status = kasus(1, 'dasar: view pass-through + view bertingkat', 'ACTIVE',
                   vdb_xml(1, '', VIEWS_A, ''), [])
    if status != 'ACTIVE':
        print('  VDB dasar gagal; uji dihentikan')
        return
    asli = bodies(1)
    REPORT['body_sysadmin'] = asli
    baru = {}
    for nama in ('v_penerima_aktif', 'v_penerima_publik'):
        try:
            baru[nama] = remove_projection(asli[nama], {KOLOM})
        except RewriteError as exc:
            print(f'  penulisan ulang {nama} gagal: {exc}')
            REPORT['galat_penulisan_ulang'] = str(exc)
            return
    REPORT['body_baru'] = baru
    for nama in baru:
        print(f'  Body {nama}:\n     lama: {asli[nama].strip()}\n     baru: {baru[nama].strip()}')

    alter = [f'ALTER VIEW "{n}" AS {baru[n].strip().rstrip(";")};' for n in baru]
    recreate = [s for n in baru for s in (f'DROP VIEW "{n}";',
                                          f'CREATE VIEW "{n}" AS {baru[n].strip().rstrip(";")};')]

    kasus(2, 'DROP kolom tanpa penyesuaian view (kontrol)', 'FAILED',
          vdb_xml(2, DROP, VIEWS_A, ''), [DROP])
    REPORT['alter_view_diterima'] = kasus(
        3, 'DROP kolom + ALTER VIEW pada kedua view', 'ACTIVE',
        vdb_xml(3, DROP, VIEWS_A, '\n'.join(alter)), [DROP, *alter]) == 'ACTIVE'
    REPORT['recreate_diterima'] = kasus(
        4, 'DROP kolom + DROP VIEW/CREATE VIEW pada kedua view', 'ACTIVE',
        vdb_xml(4, DROP, VIEWS_A, '\n'.join(recreate)), [DROP, *recreate]) == 'ACTIVE'
    hanya_dasar = [alter[0]]
    kasus(5, 'DROP kolom + ALTER VIEW hanya view dasar (tanpa perambatan)', 'FAILED',
          vdb_xml(5, DROP, VIEWS_A, hanya_dasar[0]), [DROP, *hanya_dasar])
    if kasus(6, 'dasar: view berkolom inline', 'ACTIVE', vdb_xml(6, '', VIEWS_B, ''), []) == 'ACTIVE':
        inline = remove_projection(bodies(6)['v_penerima_inline'], {KOLOM}).strip().rstrip(';')
        pernyataan = f'ALTER VIEW "v_penerima_inline" AS {inline};'
        kasus(7, 'DROP kolom + ALTER VIEW pada view berkolom inline', 'FAILED',
              vdb_xml(7, DROP, VIEWS_B, pernyataan), [DROP, pernyataan])

    if REPORT['alter_view_diterima']:
        saran = 'alter'
    elif REPORT['recreate_diterima']:
        saran = 'recreate'
    else:
        saran = None
    REPORT['saran_ASCAM_EXEC_VIEW_STATEMENT'] = saran
    print(f"\n  ALTER VIEW diterima           : {REPORT['alter_view_diterima']}")
    print(f"  DROP/CREATE VIEW diterima     : {REPORT['recreate_diterima']}")
    print(f'  ASCAM_EXEC_VIEW_STATEMENT     : {saran or "tidak ada bentuk yang diterima"}')


def cleanup() -> None:
    print('\n== pembersihan')
    for dep in list(DEPLOYED):
        op({'operation': 'undeploy', 'address': [{'deployment': dep}]})
        r = op({'operation': 'remove', 'address': [{'deployment': dep}]})
        print(f"   {dep}: {r.get('outcome')}")
    REPORT['deployment_sesudah_uji'] = op({'operation': 'read-children-names',
                                           'child-type': 'deployment'}).get('result')
    print(f"   deployment sesudah uji: {REPORT['deployment_sesudah_uji']}")


if __name__ == '__main__':
    try:
        main()
    finally:
        try:
            cleanup()
        finally:
            path = os.path.join(OUT, f"f0_8_{datetime.now().strftime('%Y%m%dT%H%M%S')}")
            os.makedirs(path, exist_ok=True)
            with open(os.path.join(path, 'report.json'), 'w') as fh:
                json.dump(REPORT, fh, indent=2, default=str)
            print(f'\nlaporan: results/f0/{os.path.basename(path)}/report.json')
