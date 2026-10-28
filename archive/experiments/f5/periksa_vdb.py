"""
F5 — Memeriksa apakah adaptasi pada VDB benar-benar diterapkan di Teiid.

Membandingkan empat sudut pandang:
  1. Teiid (management API): versi VDB yang ter-deploy, status, dan connection type-nya;
  2. isi setiap versi (read-content): apakah memuat ALTER hasil adaptasi;
  3. Teiid lewat ODBC, seperti klien biasa (tanpa menyebut versi dan dengan versi eksplisit):
     kolom apa yang terlihat pada tabel yang diuji;
  4. berkas VDB di disk dan versi aktif di Knowledge.

Dijalankan dari host: `python3 experiments/f5/periksa_vdb.py [tabel] [kolom]`
(bawaan: penerima_manfaat email). Hanya MEMBACA.
"""
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SECRETS = ROOT / 'setup/ascam/knowledge/secrets'
TEIID = 'http://localhost:19990/management'
KNOWLEDGE = 'http://127.0.0.1:18000'
BERKAS_VDB = ROOT / 'setup/data-federation/deployments/government-vdb.xml'
TABEL = sys.argv[1] if len(sys.argv) > 1 else 'penerima_manfaat'
KOLOM = sys.argv[2] if len(sys.argv) > 2 else 'email'


def rahasia(nama: str, klien: str | None = None) -> str:
    for baris in (SECRETS / nama).read_text().splitlines():
        baris = baris.strip()
        if klien and baris.startswith(f'{klien}:'):
            return baris.split(':', 1)[1]
        if not klien and baris:
            return baris
    raise RuntimeError(f'rahasia {nama} tidak ditemukan')


def teiid(operasi: dict, mentah: bool = False):
    manager = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    manager.add_password(None, TEIID, 'admin', rahasia('obdf_teiid_mgmt_password'))
    opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(manager))
    url = TEIID + ('?useStreamAsResponse' if mentah else '')
    request = urllib.request.Request(url, data=json.dumps(operasi).encode(), method='POST',
                                     headers={'Content-Type': 'application/json'})
    with opener.open(request, timeout=30) as response:
        isi = response.read()
    return isi.decode('utf-8', 'replace') if mentah else json.loads(isi)


def knowledge(path: str):
    request = urllib.request.Request(f'{KNOWLEDGE}{path}', headers={
        'Authorization': f"Bearer {rahasia('knowledge_api_tokens', 'ui')}"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read())


def kolom_lewat_odbc(versi: str | None) -> str:
    """Kolom yang terlihat klien ODBC; dijalankan dari kontainer Knowledge (punya psycopg)."""
    dbname = 'government' if versi is None else f'government.{versi}'
    skrip = (
        'import psycopg,sys\n'
        f'c=psycopg.connect(host="data-federation-teiid",port=35432,dbname="{dbname}",'
        f'user="user1",password=sys.argv[1],sslmode="disable",gssencmode="disable")\n'
        'r=c.execute("SELECT \\"Name\\" FROM SYS.Columns WHERE \\"TableName\\"=%s ORDER BY \\"Position\\"",'
        f'("{TABEL}",)).fetchall()\n'
        'v=c.execute("SELECT \\"Version\\" FROM SYS.VirtualDatabases").fetchone()\n'
        'print("versi", v[0], "|", ", ".join(x[0] for x in r))\n')
    hasil = subprocess.run(['docker', 'exec', '-i', 'ascam-knowledge-service', 'python', '-',
                            rahasia('obdf_teiid_user_password')], input=skrip,
                           capture_output=True, text=True)
    return (hasil.stdout or hasil.stderr).strip().splitlines()[-1] if (hasil.stdout or hasil.stderr) else '-'


def judul(teks: str) -> None:
    print(f'\n===== {teks} =====')


def main() -> int:
    judul('1) versi VDB di Teiid')
    daftar = teiid({'operation': 'list-vdbs', 'address': [{'subsystem': 'teiid'}]})['result']
    versi_ada = []
    for v in daftar:
        if v.get('vdb-name') != 'government':
            continue
        versi_ada.append(str(v['vdb-version']))
        print(f"  government v{v['vdb-version']}: {v.get('status')} | connection type "
              f"{v.get('connection-type')}")

    judul(f'2) isi setiap versi: apakah memuat ALTER untuk {TABEL}.{KOLOM}')
    for nomor in versi_ada:
        vdb = teiid({'operation': 'get-vdb', 'address': [{'subsystem': 'teiid'}],
                     'vdb-name': 'government', 'vdb-version': nomor})['result']
        deployment = next((p['property-value'] for p in vdb.get('properties', [])
                           if p.get('property-name') == 'deployment-name'), None)
        if not deployment:
            print(f'  v{nomor}: nama deployment tidak diketahui')
            continue
        isi = teiid({'operation': 'read-content', 'address': [{'deployment': deployment}]},
                    mentah=True)
        alter = [b.strip() for b in isi.splitlines() if 'ALTER FOREIGN TABLE' in b]
        print(f'  v{nomor} (deployment {deployment}): {len(alter)} pernyataan ALTER')
        for b in alter:
            print(f'      {b}')

    judul(f'3) kolom {TABEL} yang terlihat klien ODBC')
    print(f'  tanpa menyebut versi (seperti DBeaver bawaan): {kolom_lewat_odbc(None)}')
    for nomor in versi_ada:
        print(f'  dengan versi {nomor} eksplisit              : {kolom_lewat_odbc(nomor)}')

    judul('4) berkas VDB di disk dan Knowledge')
    di_disk = BERKAS_VDB.read_text()
    print(f"  {BERKAS_VDB.relative_to(ROOT)}: memuat '{KOLOM}'? "
          f"{'ya' if KOLOM in di_disk else 'tidak'} (by design: versi baru hanya di Teiid)")
    oid = next(o['id'] for o in knowledge('/api/v1/obdf') if o['name'] == 'bansos')
    aktif = knowledge(f'/api/v1/obdf/{oid}/versions?limit=1')[0]
    artefak = knowledge(f"/api/v1/versions/{aktif['id']}/artifacts/vdb_xml")['content']
    print(f"  Knowledge versi aktif {aktif['version_no']}: VDB v{aktif['teiid_vdb_version']} "
          f"({aktif['teiid_connection_type']}); artefak memuat '{KOLOM}'? "
          f"{'ya' if KOLOM in artefak else 'tidak'}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
