"""
F5 — Uji ketahanan adaptasi VDB terhadap restart Teiid.

Hipotesis: perubahan connection type lewat management API tidak bertahan setelah Teiid
dinyalakan ulang. Bila benar, setelah restart kedua versi kembali BY_VERSION, dan koneksi
tanpa versi diarahkan ke versi PALING AWAL (teiid-documents.pdf, "VDB Versioning", hlm. 62-63),
sehingga Ontop membaca skema lama sementara mapping sudah merujuk kolom baru.

Prasyarat: satu adaptasi ADD sudah berhasil (VDB versi 2 berstatus ANY). Skrip ini MERESTART
kontainer data-federation-teiid, lalu membandingkan keadaan sebelum dan sesudahnya.

Jalankan dari root repository:  python3 experiments/f5/uji_ketahanan.py [tabel] [kolom]
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import periksa_vdb as p                                               # noqa: E402

SPARQL = 'http://localhost:8080/sparql'
PREDIKAT = f'http://bansos.go.id/ontology/{p.KOLOM}'


def keadaan() -> dict:
    daftar = p.teiid({'operation': 'list-vdbs', 'address': [{'subsystem': 'teiid'}]})['result']
    versi = {str(v['vdb-version']): (v.get('status'), v.get('connection-type'))
             for v in daftar if v.get('vdb-name') == 'government'}
    return {'versi': versi, 'odbc_tanpa_versi': p.kolom_lewat_odbc(None)}


def sparql() -> str:
    url = SPARQL + '?' + urllib.parse.urlencode(
        {'query': f'SELECT (COUNT(*) AS ?n) WHERE {{ ?s <{PREDIKAT}> ?o }}'})
    try:
        with urllib.request.urlopen(urllib.request.Request(
                url, headers={'Accept': 'application/sparql-results+json'}), timeout=90) as r:
            return f"HTTP {r.status}, {json.loads(r.read())['results']['bindings'][0]['n']['value']} triple"
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}: {exc.read()[:200].decode('utf-8', 'replace')}"
    except Exception as exc:                                           # noqa: BLE001
        return f'galat: {type(exc).__name__}'


def tampilkan(label: str, k: dict) -> None:
    print(f'  {label}')
    for nomor, (status, tipe) in sorted(k['versi'].items()):
        print(f'      government v{nomor}: {status}, connection type {tipe}')
    print(f"      ODBC tanpa versi melihat: {k['odbc_tanpa_versi']}")
    print(f'      SPARQL predikat {p.KOLOM}: {sparql()}')


def main() -> int:
    print('===== 1) sebelum restart =====')
    sebelum = keadaan()
    tampilkan('keadaan:', sebelum)
    if len(sebelum['versi']) < 2:
        print('\n  Hanya ada satu versi VDB. Jalankan adaptasi ADD sampai berhasil lebih dulu.')
        return 1

    print('\n===== 2) restart kontainer Teiid =====')
    subprocess.run(['docker', 'restart', 'data-federation-teiid'], check=True,
                   capture_output=True)
    mulai = time.time()
    while time.time() - mulai < 240:
        try:
            if any(s == 'ACTIVE' for s, _ in keadaan()['versi'].values()):
                break
        except Exception:                                             # noqa: BLE001
            pass
        time.sleep(3)
    print(f'  Teiid kembali menjawab setelah {int(time.time() - mulai)} detik')
    time.sleep(5)

    print('\n===== 3) sesudah restart =====')
    sesudah = keadaan()
    tampilkan('keadaan:', sesudah)

    print('\n===== 4) kesimpulan =====')
    hilang = sorted(set(sebelum['versi']) - set(sesudah['versi']))
    berubah = {n: (sebelum['versi'][n][1], sesudah['versi'][n][1])
               for n in sebelum['versi'] if n in sesudah['versi']
               and sebelum['versi'][n][1] != sesudah['versi'][n][1]}
    print(f'  versi yang hilang setelah restart     : {hilang or "tidak ada"}')
    print(f'  connection type yang berubah          : {berubah or "tidak ada"}')
    sama = sebelum['odbc_tanpa_versi'] == sesudah['odbc_tanpa_versi']
    print(f'  koneksi tanpa versi melihat skema sama: {"ya" if sama else "TIDAK"}')
    if berubah or hilang or not sama:
        print('  => hipotesis TERBUKTI: adaptasi VDB tidak tahan restart Teiid.')
    else:
        print('  => hipotesis TIDAK terbukti: adaptasi VDB bertahan setelah restart.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
