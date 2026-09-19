"""
Mengembalikan OBDF ke kondisi dasar agar eksperimen dapat diulang.

Setiap run evaluasi harus berangkat dari keadaan yang sama. Skrip ini:
  1. mengembalikan artefak OBDA (mapping.ttl, ontology_file.ttl) ke isi di git;
  2. mengembalikan koneksi Teiid ke VDB versi 1 dan menghapus versi yang lebih baru;
  3. menghapus kolom uji pada sumber (bawaan: kemensos.penerima_manfaat.email);
  4. memuat ulang Ontop lalu memverifikasi endpoint menjawab;
  5. menyinkronkan Knowledge sehingga versi spesifikasi aktif mencerminkan keadaan dasar.

Catatan: riwayat di Knowledge (event, rencana, eksekusi) TIDAK dihapus, karena merupakan
jejak eksperimen. Gunakan `--kolom nama` untuk kolom uji lain, dan `--tanpa-sumber` bila
sumber tidak perlu diubah.

Dijalankan dari host: `python3 experiments/reset_obdf.py`
"""
import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE = 'http://127.0.0.1:18000'
AGENT = 'http://127.0.0.1:18100'
TEIID = 'http://localhost:9990/management'
SECRETS = ROOT / 'setup/ascam/knowledge/secrets'
ARTEFAK = ['setup/vkg-system/config/mapping.ttl', 'setup/vkg-system/config/ontology_file.ttl']


def rahasia(berkas: str, klien: str | None = None) -> str:
    for baris in (SECRETS / berkas).read_text().splitlines():
        baris = baris.strip()
        if not baris or baris.startswith('#'):
            continue
        if klien and ':' in baris:
            if baris.split(':', 1)[0] == klien:
                return baris.split(':', 1)[1]
            continue
        return baris.split(':', 1)[1] if ':' in baris else baris
    raise RuntimeError(f'rahasia {klien or berkas} tidak ditemukan')


def api(base: str, path: str, bearer: str | None = None, metode: str = 'GET', body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {'Content-Type': 'application/json'}
    if bearer:
        headers['Authorization'] = f'Bearer {bearer}'
    request = urllib.request.Request(f'{base}{path}', data=data, method=metode, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as exc:
        return {'_http': exc.code, 'detail': exc.read()[:200].decode('utf-8', 'replace')}
    except Exception as exc:                        # noqa: BLE001
        return {'_error': f'{type(exc).__name__}: {exc}'}


def teiid(operasi: dict, pengguna: str, sandi: str):
    manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    manager.add_password(None, TEIID, pengguna, sandi)
    opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(manager))
    request = urllib.request.Request(TEIID, data=json.dumps(operasi).encode(), method='POST',
                                     headers={'Content-Type': 'application/json'})
    try:
        with opener.open(request, timeout=60) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return {'outcome': 'failed', 'failure-description': exc.read()[:200].decode('utf-8', 'replace')}


def psql(perintah: str) -> str:
    hasil = subprocess.run(['docker', 'exec', 'datasources-pgsql', 'psql', '-U', 'postgres',
                            '-d', 'kemensos', '-tAc', perintah], capture_output=True, text=True)
    return (hasil.stdout or hasil.stderr).strip()


def judul(teks: str) -> None:
    print(f'\n===== {teks} =====')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--kolom', default='email')
    parser.add_argument('--tabel', default='penerima_manfaat')
    parser.add_argument('--vdb', default='government')
    parser.add_argument('--versi-dasar', default='1')
    parser.add_argument('--tanpa-sumber', action='store_true')
    args = parser.parse_args()

    ui = rahasia('knowledge_api_tokens', 'ui')
    token_agen = rahasia('ontop_agent_api_tokens')
    sandi_teiid = rahasia('obdf_teiid_mgmt_password')

    judul('1) artefak OBDA dikembalikan ke isi di git')
    subprocess.run(['git', 'checkout', '--', *ARTEFAK], cwd=ROOT, check=False)
    print(' ', subprocess.run(['git', 'status', '--short', *ARTEFAK], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip() or 'bersih')

    judul('2) versi VDB dikembalikan')
    daftar = teiid({'operation': 'read-attribute', 'address': [{'subsystem': 'teiid'}],
                    'name': 'vdb-names'}, 'admin', sandi_teiid)
    versi = teiid({'operation': 'list-vdbs', 'address': [{'subsystem': 'teiid'}]},
                  'admin', sandi_teiid)
    aktif = [(v.get('name'), str(v.get('version')), v.get('connection-type'), v.get('status'))
             for v in (versi.get('result') or []) if v.get('name') == args.vdb]
    print('  sebelum:', aktif or daftar)
    teiid({'operation': 'change-vdb-connection-type', 'address': [{'subsystem': 'teiid'}],
           'vdb-name': args.vdb, 'vdb-version': args.versi_dasar, 'connection-type': 'ANY'},
          'admin', sandi_teiid)
    for nama, nomor, _, _ in aktif:
        if nomor != args.versi_dasar:
            deployment = f'{nama}-{nomor}-vdb.xml'
            for operasi in ('undeploy', 'remove'):
                teiid({'operation': operasi, 'address': [{'deployment': deployment}]},
                      'admin', sandi_teiid)
            print(f'  versi {nomor} dihapus ({deployment})')
    versi = teiid({'operation': 'list-vdbs', 'address': [{'subsystem': 'teiid'}]},
                  'admin', sandi_teiid)
    print('  sesudah:', [(v.get('name'), str(v.get('version')), v.get('connection-type'),
                          v.get('status')) for v in (versi.get('result') or [])])

    if not args.tanpa_sumber:
        judul('3) kolom uji pada sumber dihapus')
        ada = psql("SELECT count(*) FROM information_schema.columns "
                   f"WHERE table_name='{args.tabel}' AND column_name='{args.kolom}'")
        print(' ', psql(f'ALTER TABLE public.{args.tabel} DROP COLUMN {args.kolom}')
              if ada == '1' else 'kolom sudah tidak ada')

    judul('4) Ontop dimuat ulang')
    hasil = api(AGENT, '/api/v1/reload', token_agen, 'POST')
    print(f"  ok={hasil.get('ok')} total={hasil.get('total_ms')} ms {hasil.get('error') or ''}")
    artefak = api(AGENT, '/api/v1/artifacts', token_agen)
    for a in artefak if isinstance(artefak, list) else []:
        print(f"  artefak {a['kind']:9s} {a['sha256'][:16]}")

    judul('5) Knowledge disinkronkan')
    sync = api(KNOWLEDGE, '/api/v1/obdf/1/sync', ui, 'POST')
    print(f"  berubah={sync.get('changed')} versi={sync.get('version_no')} "
          f"drift={sync.get('drift_detected')} masalah={len(sync.get('issues') or [])}")
    versi_aktif = api(KNOWLEDGE, '/api/v1/obdf/1/versions?limit=1', ui)
    for v in versi_aktif if isinstance(versi_aktif, list) else []:
        print(f"  versi {v['version_no']} {v['status']} (VDB v{v['teiid_vdb_version']}, "
              f"{v['teiid_connection_type']})")
    print('\nOBDF siap untuk run berikutnya.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
