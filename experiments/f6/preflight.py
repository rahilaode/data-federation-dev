"""
F6 — Pemeriksaan awal sebelum evaluasi.

Evaluasi hanya bermakna bila seluruh rantai hidup: monitor pada sumber, konektor Debezium,
Kafka, Orchestrator, Knowledge, agen, dan Executor. Pemeriksaan ditutup dengan uji rantai
nyata: satu DDL pada tabel khusus uji, lalu menunggu event yang sesuai muncul di Knowledge.
Tabel uji tidak difederasikan, sehingga Knowledge akan menandainya `ignored` — yang justru
membuktikan rantainya utuh tanpa mengubah OBDF.

Dijalankan sendiri: `python3 experiments/f6/preflight.py`
"""
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE = 'http://127.0.0.1:18000'
ORCHESTRATOR = 'http://127.0.0.1:18200'
EXECUTOR = 'http://127.0.0.1:18300'
AGENT = 'http://127.0.0.1:18100'
CONNECT = 'http://localhost:8083'
SECRETS = ROOT / 'setup/ascam/knowledge/secrets'
KONTAINER = ['ascam-sm-kafka', 'ascam-sm-connector', 'datasources-pgsql', 'datasources-mysql',
             'data-federation-teiid', 'vkg-system-ontop-teiid', 'ascam-knowledge-service',
             'ascam-ontop-agent', 'ascam-orchestrator']
PROBE = 'ascam_probe'


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
    raise RuntimeError(f'rahasia {klien or berkas} tidak ditemukan')


def http(base: str, path: str, bearer: str | None = None):
    headers = {'Authorization': f'Bearer {bearer}'} if bearer else {}
    try:
        with urllib.request.urlopen(urllib.request.Request(f'{base}{path}', headers=headers),
                                    timeout=30) as response:
            return json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as exc:
        return {'_http': exc.code}
    except Exception as exc:                                       # noqa: BLE001
        return {'_error': f'{type(exc).__name__}'}


def pg(sql: str) -> str:
    hasil = subprocess.run(['docker', 'exec', 'datasources-pgsql', 'psql', '-U', 'postgres',
                            '-d', 'kemensos', '-tAc', sql], capture_output=True, text=True)
    return (hasil.stdout or hasil.stderr).strip()


def berjalan() -> set[str]:
    hasil = subprocess.run(['docker', 'ps', '--format', '{{.Names}}'], capture_output=True,
                           text=True)
    return set(hasil.stdout.split())


def periksa(diam: bool = False) -> tuple[bool, list[str]]:
    masalah: list[str] = []
    lapor = (lambda *a: None) if diam else print

    aktif = berjalan()
    hilang = [n for n in KONTAINER if n not in aktif]
    lapor(f"  kontainer: {len(KONTAINER) - len(hilang)}/{len(KONTAINER)} berjalan"
          f"{' | tidak berjalan: ' + ', '.join(hilang) if hilang else ''}")
    if hilang:
        masalah.append(f'kontainer tidak berjalan: {", ".join(hilang)}')

    for konektor in ('postgres-connector', 'mysql-connector'):
        status = http(CONNECT, f'/connectors/{konektor}/status')
        keadaan = (status.get('connector') or {}).get('state')
        tugas = [t.get('state') for t in status.get('tasks', [])]
        lapor(f'  konektor {konektor}: {keadaan} tugas={tugas}')
        if keadaan != 'RUNNING' or tugas != ['RUNNING']:
            masalah.append(f'konektor {konektor} tidak RUNNING ({keadaan}, {tugas})')

    orch = http(ORCHESTRATOR, '/health')
    lapor(f"  orchestrator: {orch.get('state')} topik={len(orch.get('topics') or [])} "
          f"gagal={orch.get('failures')}")
    if orch.get('state') != 'running':
        masalah.append(f"orchestrator tidak berjalan ({orch.get('state') or orch}); "
                       f"galat terakhir: {orch.get('last_error')}")

    exe = http(EXECUTOR, '/health')
    lapor(f"  executor: {exe.get('state')}")
    agen = http(AGENT, '/health', token('ontop_agent_api_tokens'))
    lapor(f"  agen: {agen.get('status')} artefak={agen.get('artifacts')}")
    if agen.get('status') != 'ok':
        masalah.append('agen Ontop tidak sehat')

    versi = http(KNOWLEDGE, '/api/v1/obdf/1/versions?limit=1', token('knowledge_api_tokens', 'ui'))
    if isinstance(versi, list) and versi:
        lapor(f"  knowledge: versi aktif {versi[0]['version_no']} "
              f"(VDB v{versi[0]['teiid_vdb_version']})")
    else:
        masalah.append('Knowledge tidak menjawab daftar versi')
    return not masalah, masalah


def uji_rantai(batas: float = 60.0, diam: bool = False) -> tuple[bool, str]:
    """DDL nyata pada tabel uji, lalu menunggu event sampai ke Knowledge."""
    lapor = (lambda *a: None) if diam else print
    ui = token('knowledge_api_tokens', 'ui')
    sebelum = {e['id'] for e in http(KNOWLEDGE, '/api/v1/obdf/1/events?limit=200', ui)}
    pg(f'CREATE TABLE IF NOT EXISTS public.{PROBE} (id serial primary key)')
    kolom = f'uji_{int(time.time())}'
    pg(f'ALTER TABLE public.{PROBE} ADD COLUMN {kolom} varchar(10)')
    mulai = time.perf_counter()
    while time.perf_counter() - mulai < batas:
        for peristiwa in http(KNOWLEDGE, '/api/v1/obdf/1/events?limit=20', ui):
            if peristiwa['id'] in sebelum:
                continue
            struktur = peristiwa.get('structured') or {}
            if struktur.get('table') == PROBE:
                jeda = int((time.perf_counter() - mulai) * 1000)
                lapor(f"  rantai utuh: event {peristiwa['id']} diterima {jeda} ms "
                      f"(status {peristiwa['status']})")
                pg(f'DROP TABLE IF EXISTS public.{PROBE}')
                return True, f'{jeda} ms'
        time.sleep(1)
    pg(f'DROP TABLE IF EXISTS public.{PROBE}')
    return False, f'tidak ada event dalam {int(batas)} s'


def main() -> int:
    print('===== pemeriksaan komponen =====')
    ok, masalah = periksa()
    print('\n===== uji rantai DDL -> Kafka -> Orchestrator -> Knowledge =====')
    rantai_ok, keterangan = uji_rantai()
    if not rantai_ok:
        masalah.append(f'rantai event terputus: {keterangan}')
        print(f'  GAGAL: {keterangan}')
    if masalah:
        print('\nMasalah yang harus diperbaiki sebelum evaluasi:')
        for m in masalah:
            print(f'  - {m}')
        print('\nPerbaikan umum:')
        print('  docker compose -f setup/ascam/schema-monitor/docker-compose.yaml up -d')
        print('  docker compose -f setup/ascam/orchestrator/docker-compose.yaml restart')
        print('  experiments/f3/verify_orchestrator.sh --no-ddl   # mendaftarkan ulang konektor')
        return 1
    print('\nSemua siap untuk evaluasi.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
