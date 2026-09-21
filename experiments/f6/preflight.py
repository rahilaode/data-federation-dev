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


def log_monitor(kolom: str) -> str:
    """Apakah monitor PostgreSQL mencatat DDL uji di tabel log-nya?"""
    return pg("SELECT count(*) FROM schema_monitor.ddl_event_log "
              f"WHERE table_name = '{PROBE}' AND column_name = '{kolom}'")


def pesan_orchestrator() -> int | None:
    kesehatan = http(ORCHESTRATOR, '/health')
    return kesehatan.get('messages') if isinstance(kesehatan, dict) else None


def pencacah_orchestrator() -> dict:
    kesehatan = http(ORCHESTRATOR, '/health')
    if not isinstance(kesehatan, dict):
        return {}
    return {k: kesehatan.get(k) for k in ('duplicates', 'conflicts', 'skipped', 'failures')}


def diagnosis(kolom: str, pesan_awal: int | None) -> list[str]:
    """Menunjuk mata rantai yang putus: monitor, Debezium/Kafka, atau Orchestrator."""
    temuan = []
    tercatat = log_monitor(kolom)
    if tercatat != '1':
        temuan.append(f'[monitor] DDL uji TIDAK tercatat di schema_monitor.ddl_event_log '
                      f'(hasil: {tercatat!r}); periksa event trigger PostgreSQL')
        return temuan
    temuan.append('[monitor] DDL uji tercatat di ddl_event_log')
    pesan_akhir = pesan_orchestrator()
    if pesan_awal is not None and pesan_akhir is not None and pesan_akhir > pesan_awal:
        temuan.append(f'[orchestrator] menerima {pesan_akhir - pesan_awal} pesan baru, tetapi '
                      'event tidak tercatat di Knowledge')
        kesehatan = http(ORCHESTRATOR, '/health')
        temuan.append(f'[orchestrator] pencacah: {pencacah_orchestrator()}')
        if isinstance(kesehatan, dict) and (kesehatan.get('duplicates') or kesehatan.get('conflicts')):
            temuan.append('[orchestrator] pesan dianggap DUPLIKAT oleh Knowledge; bila topik Kafka '
                          'pernah dibuat ulang, uid lama berbasis offset dapat bertabrakan')
        dilewati = kesehatan.get('last_skipped') if isinstance(kesehatan, dict) else None
        if dilewati:
            temuan.append(f"[orchestrator] pesan dilewati: {dilewati.get('alasan')}")
            temuan.append(f"[orchestrator] cuplikan pesan: {str(dilewati.get('cuplikan'))[:240]}")
    else:
        temuan.append('[debezium/kafka/orchestrator] pesan TIDAK sampai ke Orchestrator. '
                      'Bila konektor RUNNING, kemungkinan konsumen Orchestrator macet: '
                      'docker compose -f setup/ascam/orchestrator/docker-compose.yaml restart')
    status = http(CONNECT, '/connectors/postgres-connector/status')
    for tugas in status.get('tasks', []):
        if tugas.get('trace'):
            temuan.append(f"[debezium] galat tugas: {tugas['trace'].splitlines()[0][:200]}")
    log = subprocess.run(['docker', 'logs', '--tail', '5', 'ascam-orchestrator'],
                         capture_output=True, text=True)
    for baris in (log.stdout + log.stderr).strip().splitlines()[-5:]:
        temuan.append(f'[log orchestrator] {baris[:180]}')
    return temuan


def uji_rantai(batas: float = 60.0, diam: bool = False) -> tuple[bool, str]:
    """DDL nyata pada tabel uji, lalu menunggu event sampai ke Knowledge."""
    lapor = (lambda *a: None) if diam else print
    ui = token('knowledge_api_tokens', 'ui')
    pesan_awal = pesan_orchestrator()
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
    for temuan in diagnosis(kolom, pesan_awal):
        lapor(f'  {temuan}')
    pg(f'DROP TABLE IF EXISTS public.{PROBE}')
    return False, f'tidak ada event dalam {int(batas)} s'


def main() -> int:
    # --tanpa-rantai: hanya memeriksa komponen, tanpa DDL uji pada sumber (tabel ascam_probe)
    tanpa_rantai = '--tanpa-rantai' in sys.argv
    print('===== pemeriksaan komponen =====')
    ok, masalah = periksa()
    if tanpa_rantai:
        print('\n(uji rantai dilewati: tidak ada DDL yang dijalankan pada sumber)')
    else:
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
