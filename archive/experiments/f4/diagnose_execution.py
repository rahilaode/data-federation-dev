"""
F4c — Diagnostik eksekusi rencana adaptasi.

Menampilkan apa yang benar-benar terjadi pada percobaan eksekusi terakhir: langkah-langkahnya,
hasil setiap validasi (termasuk keluaran `ontop validate`), status VDB pada Teiid, dan kondisi
artefak OBDA di host Ontop. Hanya MEMBACA.

Dijalankan dari host: `python3 experiments/f4/diagnose_execution.py`
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE = 'http://127.0.0.1:18000'
AGENT = 'http://127.0.0.1:18100'
EXECUTOR = 'http://127.0.0.1:18300'
SECRETS = ROOT / 'setup/ascam/knowledge/secrets'


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
    raise RuntimeError(f'token {klien} tidak ada di {berkas}')


def ambil(base: str, path: str, bearer: str | None = None, metode: str = 'GET', body=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {'Content-Type': 'application/json'}
    if bearer:
        headers['Authorization'] = f'Bearer {bearer}'
    request = urllib.request.Request(f'{base}{path}', data=data, method=metode, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as exc:
        return {'_http': exc.code, 'detail': exc.read()[:300].decode('utf-8', 'replace')}
    except Exception as exc:                        # noqa: BLE001
        return {'_error': f'{type(exc).__name__}: {exc}'}


def judul(teks: str) -> None:
    print(f'\n===== {teks} =====')


def main() -> int:
    ui = token('knowledge_api_tokens', 'ui')
    agen = token('ontop_agent_api_tokens')

    judul('1) status Executor')
    print(' ', json.dumps(ambil(EXECUTOR, '/health'), indent=2)[:600])

    judul('2) eksekusi terakhir beserta langkahnya')
    eksekusi = ambil(KNOWLEDGE, '/api/v1/obdf/1/executions?limit=5', ui)
    if isinstance(eksekusi, dict):
        print(' ', eksekusi)
        return 1
    for e in eksekusi:
        print(f"  eksekusi {e['id']} rencana {e['plan_id']}: {e['status']} "
              f"| timings={e['timings']} | gagal={e['failure']}")
        for s in e['steps']:
            print(f"      {s['seq']}. {s['name']:12s} {s['status']:9s} {json.dumps(s['detail'])[:220]}")

    judul('3) hasil validasi (termasuk keluaran ontop validate)')
    versi = ambil(KNOWLEDGE, '/api/v1/obdf/1/versions?limit=1', ui)
    print('  versi aktif:', [(v['version_no'], v['status'], v['teiid_vdb_version'],
                              v['teiid_connection_type']) for v in versi])
    for e in eksekusi[:2]:
        detail = ambil(KNOWLEDGE, f"/api/v1/plans/{e['plan_id']}", ui)
        print(f"  rencana {e['plan_id']}: {detail.get('status')} ({detail.get('pattern')})")
    print('  (rincian validasi ada pada tabel ops.validation; lihat kueri di bawah)')

    judul('4) status VDB pada Teiid')
    targets = ambil(KNOWLEDGE, '/api/v1/obdf/1/targets', ui)
    mgmt = next((t for t in targets if t['kind'] == 'teiid_mgmt'), None)
    if mgmt:
        hasil = ambil(KNOWLEDGE, f"/api/v1/targets/{mgmt['id']}/check", ui, metode='POST', body={})
        fakta = (hasil.get('detail') or {}).get('facts', {})
        for v in fakta.get('vdbs', []):
            print(f"  VDB {v['name']} v{v['version']}: {v['status']} ({v['connection_type']})")

    judul('5) artefak di host Ontop')
    for a in ambil(AGENT, '/api/v1/artifacts', agen):
        if isinstance(a, dict) and 'kind' in a:
            print(f"  {a['kind']:9s} {a['size']:7d} bita  {a['sha256'][:16]}")
    cadangan = ambil(AGENT, '/api/v1/backups', agen)
    print(f'  cadangan: {len(cadangan) if isinstance(cadangan, list) else cadangan}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
