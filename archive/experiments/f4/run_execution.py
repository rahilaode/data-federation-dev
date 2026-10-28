"""
F4c — Menjalankan satu adaptasi ujung ke ujung dengan pengamatan penuh.

Urutan:
  1. kolom `email` ditambahkan pada sumber PostgreSQL (DDL nyata) sehingga monitor menghasilkan
     event dan Knowledge membentuk rencana P-001;
  2. sidik jari graf dicatat sebelum adaptasi;
  3. Executor dinyalakan dan diamati sampai rencana dieksekusi;
  4. hasil setiap langkah, waktu, versi VDB, dan artefak ditampilkan;
  5. satu baris data diisi agar nilai kolom baru benar-benar terlihat lewat SPARQL.

Kondisi sumber TIDAK dikembalikan otomatis: gunakan `--bersihkan` bila ingin menghapus kolom
`email` beserta datanya setelah uji.

Dijalankan dari host: `python3 experiments/f4/run_execution.py`
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE = 'http://127.0.0.1:18000'
EXECUTOR = 'http://127.0.0.1:18300'
AGENT = 'http://127.0.0.1:18100'
SPARQL = 'http://localhost:8080/sparql'
SECRETS = ROOT / 'setup/ascam/knowledge/secrets'
EMAIL_IRI = 'http://bansos.go.id/ontology/email'


def token(berkas: str, klien: str | None = None) -> str:
    for baris in (SECRETS / berkas).read_text().splitlines():
        baris = baris.strip()
        if not baris or baris.startswith('#'):
            continue
        if klien and ':' in baris:
            if baris.split(':', 1)[0] == klien:
                return baris.split(':', 1)[1]
            continue
        return baris.split(':', 1)[1] if ':' in baris else baris
    raise RuntimeError(f'token {klien} tidak ditemukan')


UI = None


def api(path: str, metode: str = 'GET', body=None, base: str = KNOWLEDGE, bearer: str | None = None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {'Content-Type': 'application/json'}
    bearer = bearer if bearer is not None else UI
    if bearer:
        headers['Authorization'] = f'Bearer {bearer}'
    request = urllib.request.Request(f'{base}{path}', data=data, method=metode, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as exc:
        return {'_http': exc.code, 'detail': exc.read()[:300].decode('utf-8', 'replace')}
    except Exception as exc:                        # noqa: BLE001
        return {'_error': f'{type(exc).__name__}: {exc}'}


def psql(perintah: str) -> str:
    hasil = subprocess.run(['docker', 'exec', 'datasources-pgsql', 'psql', '-U', 'postgres',
                            '-d', 'kemensos', '-tAc', perintah],
                           capture_output=True, text=True)
    return (hasil.stdout or hasil.stderr).strip()


def sparql(kueri: str):
    url = f'{SPARQL}?{urllib.parse.urlencode({"query": kueri})}'
    request = urllib.request.Request(url, headers={'Accept': 'application/sparql-results+json'})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())['results']['bindings']
    except Exception as exc:                        # noqa: BLE001
        return {'_error': f'{type(exc).__name__}: {str(exc)[:160]}'}


def sidik_jari():
    hasil = sparql('SELECT ?p (COUNT(*) AS ?n) WHERE { ?s ?p ?o } GROUP BY ?p')
    if isinstance(hasil, dict):
        return hasil
    return {b['p']['value']: int(b['n']['value']) for b in hasil}


def judul(teks: str) -> None:
    print(f'\n===== {teks} =====')


def compose(*args: str, env: dict | None = None) -> None:
    perintah = ['docker', 'compose', '-f', 'setup/ascam/executor/docker-compose.yaml', *args]
    subprocess.run(perintah, cwd=ROOT, capture_output=True, text=True,
                   env={**__import__('os').environ, **(env or {})})


def main() -> int:
    global UI
    UI = token('knowledge_api_tokens', 'ui')
    bersihkan = '--bersihkan' in sys.argv

    judul('1) siapkan sumber: kolom email')
    ada = psql("SELECT count(*) FROM information_schema.columns "
               "WHERE table_name='penerima_manfaat' AND column_name='email'")
    if ada == '0':
        print(' ', psql("ALTER TABLE public.penerima_manfaat ADD COLUMN email VARCHAR(100)"))
        print('  kolom ditambahkan; menunggu event dari monitor...')
        time.sleep(12)
    else:
        print('  kolom email sudah ada di sumber')

    judul('2) keadaan sebelum adaptasi')
    sebelum = sidik_jari()
    print(f'  predikat pada graf: {len(sebelum)}')
    print(f"  predikat email ada: {EMAIL_IRI in sebelum}")
    versi = api('/api/v1/obdf/1/versions?limit=1')
    print(f"  versi spesifikasi aktif: {versi[0]['version_no']} (VDB v{versi[0]['teiid_vdb_version']})")
    rencana = [p for p in api('/api/v1/obdf/1/plans?status=approved&limit=10')]
    print(f"  rencana approved: {[(p['id'], p['pattern'], p['base_spec_version_id']) for p in rencana]}")
    if not rencana:
        print('  tidak ada rencana untuk dieksekusi')
        return 1

    judul('3) nyalakan Executor dan amati')
    compose('up', '-d', '--force-recreate', env={'ASCAM_EXEC_ENABLED': 'true'})
    batas = time.time() + 180
    terakhir = {}
    while time.time() < batas:
        kesehatan = api('/health', base=EXECUTOR, bearer='')
        if kesehatan.get('state') == 'running' and kesehatan != terakhir:
            terakhir = kesehatan
            print(f"  dijalankan={kesehatan['executed']} revert={kesehatan['rolled_back']} "
                  f"gagal={kesehatan['failed']} rencana_terakhir={kesehatan['last_plan_id']} "
                  f"status={kesehatan['last_status']}")
            if kesehatan['executed'] or kesehatan['rolled_back'] or kesehatan['failed']:
                if kesehatan['last_plan_id'] == max(p['id'] for p in rencana):
                    break
        time.sleep(3)

    judul('4) hasil eksekusi')
    for e in api('/api/v1/obdf/1/executions?limit=3'):
        print(f"  eksekusi {e['id']} rencana {e['plan_id']}: {e['status']}")
        for s in e['steps']:
            print(f"      {s['seq']}. {s['name']:12s} {s['status']:9s} "
                  f"{s['detail'].get('duration_ms', '?')} ms  "
                  f"{json.dumps({k: v for k, v in s['detail'].items() if k != 'duration_ms'})[:180]}")
        if e['failure']:
            print(f"      gagal: {json.dumps(e['failure'])[:300]}")
        print(f"      waktu: {e['timings']}")

    judul('5) keadaan sesudah adaptasi')
    sesudah = sidik_jari()
    print(f'  predikat pada graf: {len(sesudah) if isinstance(sesudah, dict) else sesudah}')
    hilang = sorted(set(sebelum) - set(sesudah)) if isinstance(sesudah, dict) else ['?']
    print(f'  predikat yang hilang: {hilang or "tidak ada"}')
    versi = api('/api/v1/obdf/1/versions?limit=2')
    for v in versi:
        print(f"  versi {v['version_no']} {v['status']} (VDB v{v['teiid_vdb_version']}, "
              f"{v['teiid_connection_type']})")
    for a in api('/api/v1/artifacts', base=AGENT, bearer=token('ontop_agent_api_tokens')):
        if isinstance(a, dict) and 'kind' in a:
            print(f"  artefak {a['kind']:9s} {a['sha256'][:16]}")

    judul('6) nilai kolom baru lewat SPARQL')
    print(' ', psql("UPDATE public.penerima_manfaat SET email = 'siti.rahma@contoh.id' "
                    "WHERE penerima_id = 1"))
    hasil = sparql('SELECT ?s ?email WHERE { ?s <%s> ?email }' % EMAIL_IRI)
    if isinstance(hasil, dict):
        print('  galat kueri:', hasil)
    else:
        for b in hasil[:5]:
            print(f"  {b['s']['value'].rsplit('/', 1)[-1]} -> {b['email']['value']}")
        if not hasil:
            print('  belum ada nilai email pada graf')

    if bersihkan:
        judul('7) kembalikan kondisi sumber')
        print(' ', psql('ALTER TABLE public.penerima_manfaat DROP COLUMN email'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
