"""
F6 — Harness evaluasi ASCAM.

Menjalankan skenario perubahan skema berulang kali pada OBDF yang sebenarnya, dan mencatat
untuk setiap run: waktu deteksi, keputusan D11, waktu adaptasi beserta dekomposisinya per
langkah, hasil verifikasi, serta kesetaraan jawaban SPARQL sebelum dan sesudah adaptasi.

Setiap run berangkat dari kondisi dasar yang sama (experiments/reset_obdf.py).

Contoh:
  python3 experiments/f6/evaluate.py --skenario a001 --ulangan 3
  python3 experiments/f6/evaluate.py --ulangan 20            # ketiga skenario
Hasil: results/f6/<waktu>/run-*.json, ringkasan.csv, dan ringkasan.md
"""
import argparse
import csv
import json
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from skenario import SKENARIO                                      # noqa: E402

KNOWLEDGE = 'http://127.0.0.1:18000'
EXECUTOR = 'http://127.0.0.1:18300'
SPARQL = 'http://localhost:8080/sparql'
SECRETS = ROOT / 'setup/ascam/knowledge/secrets'
LANGKAH = ['deploy_vdb', 'validate', 'switch', 'reload_ontop', 'verify', 'sync']


def token(klien: str) -> str:
    for baris in (SECRETS / 'knowledge_api_tokens').read_text().splitlines():
        if baris.strip().startswith(f'{klien}:'):
            return baris.strip().split(':', 1)[1]
    raise RuntimeError(f'token {klien} tidak ada')


UI = None


def api(path: str, metode: str = 'GET', body=None, base: str = KNOWLEDGE, bearer=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {'Content-Type': 'application/json'}
    bearer = UI if bearer is None else bearer
    if bearer:
        headers['Authorization'] = f'Bearer {bearer}'
    request = urllib.request.Request(f'{base}{path}', data=data, method=metode, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as exc:
        return {'_http': exc.code, 'detail': exc.read()[:200].decode('utf-8', 'replace')}
    except Exception as exc:                                       # noqa: BLE001
        return {'_error': f'{type(exc).__name__}: {exc}'}


def sparql(kueri: str):
    url = f'{SPARQL}?{urllib.parse.urlencode({"query": kueri})}'
    request = urllib.request.Request(url, headers={'Accept': 'application/sparql-results+json'})
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            hasil = json.loads(response.read())['results']['bindings']
        return sorted(json.dumps(b, sort_keys=True) for b in hasil)
    except Exception as exc:                                       # noqa: BLE001
        return {'_error': f'{type(exc).__name__}: {str(exc)[:160]}'}


def jawaban(kueri_list: list[str]) -> dict:
    return {f'q{i}': sparql(k) for i, k in enumerate(kueri_list, start=1)}


def reset(tanpa_sumber: bool = True) -> None:
    perintah = [sys.executable, 'experiments/reset_obdf.py']
    if tanpa_sumber:
        perintah.append('--tanpa-sumber')
    subprocess.run(perintah, cwd=ROOT, capture_output=True, text=True)


def executor(nyala: bool) -> None:
    subprocess.run(['docker', 'compose', '-f', 'setup/ascam/executor/docker-compose.yaml',
                    'up', '-d', '--force-recreate'], cwd=ROOT, capture_output=True, text=True,
                   env={**__import__('os').environ,
                        'ASCAM_EXEC_ENABLED': 'true' if nyala else 'false'})


def tunggu(kondisi, batas_detik: float, jeda: float = 1.0):
    batas = time.perf_counter() + batas_detik
    while time.perf_counter() < batas:
        hasil = kondisi()
        if hasil:
            return hasil
        time.sleep(jeda)
    return None


def satu_run(kode: str, nomor: int, batas: float) -> dict:
    sk = SKENARIO[kode]
    catatan = {'skenario': kode, 'judul': sk.judul, 'pola_diharapkan': sk.pola, 'run': nomor,
               'mulai': datetime.now(timezone.utc).isoformat()}

    if sk.siapkan:
        sk.siapkan()
    sk.pulihkan()
    reset()
    sebelum_versi = api('/api/v1/obdf/1/versions?limit=1')
    catatan['versi_sebelum'] = sebelum_versi[0]['version_no'] if sebelum_versi else None
    catatan['jawaban_sebelum'] = jawaban(sk.kueri)
    peristiwa_awal = {e['id'] for e in api('/api/v1/obdf/1/events?limit=200')}
    eksekusi_awal = {e['id'] for e in api('/api/v1/obdf/1/executions?limit=50')}

    t0 = time.perf_counter()
    catatan['ddl'] = sk.terapkan()

    peristiwa = tunggu(lambda: next(
        (e for e in api('/api/v1/obdf/1/events?limit=20') if e['id'] not in peristiwa_awal), None),
        batas)
    catatan['deteksi_ms'] = int((time.perf_counter() - t0) * 1000) if peristiwa else None
    catatan['event'] = {'id': peristiwa['id'], 'status': peristiwa['status'],
                        'structured': peristiwa['structured']} if peristiwa else None
    if peristiwa is None:
        catatan['hasil'] = 'event tidak diterima'
        return catatan

    rencana = next((p for p in api('/api/v1/obdf/1/plans?limit=5')
                    if p['impact'] and p['id']), None)
    catatan['keputusan'] = rencana['decision'] if rencana else None
    catatan['pola'] = rencana['pattern'] if rencana else None
    catatan['sesuai_harapan'] = (catatan['keputusan'] == sk.keputusan and catatan['pola'] == sk.pola)

    eksekusi = tunggu(lambda: next(
        (e for e in api('/api/v1/obdf/1/executions?limit=5')
         if e['id'] not in eksekusi_awal and e['status'] != 'running'), None), batas)
    catatan['adaptasi_ms'] = int((time.perf_counter() - t0) * 1000) if eksekusi else None
    if eksekusi is None:
        catatan['hasil'] = 'eksekusi tidak selesai'
        return catatan

    catatan['eksekusi'] = {'id': eksekusi['id'], 'status': eksekusi['status'],
                           'timings': eksekusi['timings'], 'failure': eksekusi['failure']}
    catatan['langkah'] = {s['name']: s['detail'].get('duration_ms') for s in eksekusi['steps']}
    catatan['status_langkah'] = {s['name']: s['status'] for s in eksekusi['steps']}
    catatan['hasil'] = eksekusi['status']

    catatan['jawaban_sesudah'] = jawaban(sk.kueri)
    catatan['jawaban_identik'] = catatan['jawaban_sebelum'] == catatan['jawaban_sesudah']
    prediket_sebelum = set(json.dumps(x) for x in catatan['jawaban_sebelum']['q1'])
    prediket_sesudah = set(json.dumps(x) for x in catatan['jawaban_sesudah']['q1'])
    catatan['predikat_sebelum'] = len(prediket_sebelum)
    catatan['predikat_sesudah'] = len(prediket_sesudah)
    sesudah_versi = api('/api/v1/obdf/1/versions?limit=1')
    catatan['versi_sesudah'] = sesudah_versi[0]['version_no'] if sesudah_versi else None
    catatan['selesai'] = datetime.now(timezone.utc).isoformat()
    return catatan


def ringkas(semua: list[dict], keluaran: Path) -> None:
    kolom = ['skenario', 'run', 'hasil', 'keputusan', 'pola', 'sesuai_harapan', 'deteksi_ms',
             'adaptasi_ms', 'jawaban_identik', 'predikat_sebelum', 'predikat_sesudah',
             *[f'langkah_{n}' for n in LANGKAH]]
    with (keluaran / 'ringkasan.csv').open('w', newline='') as fh:
        penulis = csv.DictWriter(fh, fieldnames=kolom, extrasaction='ignore')
        penulis.writeheader()
        for run in semua:
            baris = {k: run.get(k) for k in kolom}
            for nama in LANGKAH:
                baris[f'langkah_{nama}'] = (run.get('langkah') or {}).get(nama)
            penulis.writerow(baris)

    baris_md = ['# Ringkasan evaluasi ASCAM', '',
                f'Dibuat {datetime.now(timezone.utc).isoformat()}', '',
                '| Skenario | Run | Berhasil | Sesuai D11 | Deteksi (ms) | Adaptasi (ms) |',
                '|---|---:|---:|---:|---:|---:|']
    for kode in sorted({r['skenario'] for r in semua}):
        runs = [r for r in semua if r['skenario'] == kode]
        berhasil = [r for r in runs if r.get('hasil') == 'succeeded']
        deteksi = [r['deteksi_ms'] for r in runs if r.get('deteksi_ms')]
        adaptasi = [r['adaptasi_ms'] for r in berhasil if r.get('adaptasi_ms')]
        baris_md.append(
            f"| {kode} | {len(runs)} | {len(berhasil)} | "
            f"{sum(1 for r in runs if r.get('sesuai_harapan'))} | "
            f"{int(statistics.median(deteksi)) if deteksi else '-'} | "
            f"{int(statistics.median(adaptasi)) if adaptasi else '-'} |")
    baris_md += ['', '## Dekomposisi waktu adaptasi (median, ms)', '',
                 '| Skenario | ' + ' | '.join(LANGKAH) + ' |',
                 '|---|' + '---:|' * len(LANGKAH)]
    for kode in sorted({r['skenario'] for r in semua}):
        nilai = []
        for nama in LANGKAH:
            angka = [(r.get('langkah') or {}).get(nama) for r in semua
                     if r['skenario'] == kode and (r.get('langkah') or {}).get(nama)]
            nilai.append(str(int(statistics.median(angka))) if angka else '-')
        baris_md.append(f'| {kode} | ' + ' | '.join(nilai) + ' |')
    (keluaran / 'ringkasan.md').write_text('\n'.join(baris_md) + '\n')
    print('\n'.join(baris_md))


def main() -> int:
    global UI
    parser = argparse.ArgumentParser()
    parser.add_argument('--skenario', action='append', choices=sorted(SKENARIO))
    parser.add_argument('--ulangan', type=int, default=3)
    parser.add_argument('--batas', type=float, default=180.0, help='batas tunggu per tahap (detik)')
    args = parser.parse_args()
    UI = token('ui')
    daftar = args.skenario or sorted(SKENARIO)

    keluaran = ROOT / 'results/f6' / datetime.now().strftime('%Y%m%dT%H%M%S')
    keluaran.mkdir(parents=True, exist_ok=True)
    print(f'Hasil akan ditulis ke {keluaran.relative_to(ROOT)}')

    executor(True)
    semua = []
    try:
        for kode in daftar:
            for nomor in range(1, args.ulangan + 1):
                print(f'\n--- {kode} run {nomor}/{args.ulangan}')
                catatan = satu_run(kode, nomor, args.batas)
                semua.append(catatan)
                (keluaran / f'run-{kode}-{nomor:02d}.json').write_text(
                    json.dumps(catatan, indent=2, ensure_ascii=False))
                print(f"    hasil={catatan.get('hasil')} keputusan={catatan.get('keputusan')} "
                      f"deteksi={catatan.get('deteksi_ms')} ms "
                      f"adaptasi={catatan.get('adaptasi_ms')} ms "
                      f"identik={catatan.get('jawaban_identik')}")
            SKENARIO[kode].pulihkan()
            reset()
    finally:
        executor(False)
        ringkas(semua, keluaran)
    return 0


if __name__ == '__main__':
    sys.exit(main())
