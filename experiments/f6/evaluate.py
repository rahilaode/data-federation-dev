"""
F6 — Harness evaluasi ASCAM (proposal §3.10–3.11).

Dua mode dengan kueri dan prosedur pemulihan yang sama, sehingga perbedaan perilaku
sepenuhnya dapat diatributkan pada ASCAM (§3.10.2, hlm. 104):

  baseline   Executor dijeda sepanjang run: DDL diterapkan pada sumber tanpa adaptasi, lalu
             Q_k dijalankan dan dicatat status HTTP, pesan galat, dan hasilnya (§3.10.1).
  perlakuan  Siklus MAPE-K berjalan (A001 disetujui evaluator, ADR-0021). Dicatat keputusan
             D11, Δt_adapt, dekomposisi langkah, dan PreservationRatio atas Q_k (§3.11.2–3.11.3).
             Seluruh pesan Kafka selama perlakuan diaudit untuk C_safe (§3.11.1).

Setiap run berangkat dari kondisi dasar yang sama (experiments/reset_obdf.py).

Contoh:
  python3 experiments/f6/evaluate.py --mode keduanya --ulangan 20 --ulangan-baseline 5
  python3 experiments/f6/evaluate.py --mode baseline --skenario a002 --ulangan-baseline 1
Hasil: results/f6/<waktu>/{baseline-*.json, run-*.json, kafka-*.json, konfigurasi-debezium.json}
Analisis: python3 experiments/f6/analisis.py
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
F6 = Path(__file__).resolve().parent
sys.path.insert(0, str(F6))
import kueri                                                           # noqa: E402
from preflight import periksa, uji_rantai                              # noqa: E402
from skenario import SKENARIO                                          # noqa: E402

KNOWLEDGE = 'http://127.0.0.1:18000'
EXECUTOR = 'http://127.0.0.1:18300'
SECRETS = ROOT / 'setup/ascam/knowledge/secrets'
UI = None


# ── utilitas ──────────────────────────────────────────────────────────────────
def token(klien: str) -> str:
    for baris in (SECRETS / 'knowledge_api_tokens').read_text().splitlines():
        if baris.strip().startswith(f'{klien}:'):
            return baris.strip().split(':', 1)[1]
    raise RuntimeError(f'token {klien} tidak ada')


def api(path: str, metode: str = 'GET', body=None, base: str = KNOWLEDGE, bearer=None):
    data = json.dumps(body).encode() if body is not None else None
    # Evaluator bertindak sebagai administrator bernama; tercatat demikian di jejak audit
    headers = {'Content-Type': 'application/json', 'X-ASCAM-User': 'evaluator'}
    bearer = UI if bearer is None else bearer
    if bearer:
        headers['Authorization'] = f'Bearer {bearer}'
    request = urllib.request.Request(f'{base}{path}', data=data, method=metode, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as exc:
        return {'_http': exc.code, 'detail': exc.read()[:200].decode('utf-8', 'replace')}
    except Exception as exc:                                           # noqa: BLE001
        return {'_error': f'{type(exc).__name__}: {exc}'}


def tunggu(kondisi, batas_detik: float, jeda: float = 1.0):
    batas = time.perf_counter() + batas_detik
    while time.perf_counter() < batas:
        hasil = kondisi()
        if hasil:
            return hasil
        time.sleep(jeda)
    return None


def reset() -> None:
    subprocess.run([sys.executable, 'experiments/reset_obdf.py', '--tanpa-sumber'],
                   cwd=ROOT, capture_output=True, text=True)


def kendali(aksi: str) -> None:
    api(f'/control/{aksi}', 'POST', {}, base=EXECUTOR, bearer='')


def tenang(batas: float = 180.0) -> bool:
    return bool(tunggu(lambda: not any(e['status'] == 'running'
                                       for e in api('/api/v1/obdf/1/executions?limit=10')), batas))


def netralkan_rencana() -> list[int]:
    """Rencana yang belum dieksekusi ditandai usang (dari pemulihan atau run baseline)."""
    dinetralkan = []
    for status in ('approved', 'pending_approval'):
        for plan in api(f'/api/v1/obdf/1/plans?status={status}&limit=50'):
            api(f"/api/v1/plans/{plan['id']}/supersede", 'POST',
                {'note': 'rencana dari langkah pemulihan atau run baseline'})
            dinetralkan.append(plan['id'])
    return dinetralkan


def id_event() -> set[int]:
    return {e['id'] for e in api('/api/v1/obdf/1/events?limit=200')}


def ms_antara(awal_iso: str, akhir_iso: str | None) -> int | None:
    if not akhir_iso:
        return None
    a = datetime.fromisoformat(awal_iso)
    b = datetime.fromisoformat(akhir_iso.replace('Z', '+00:00'))
    return int((b - a).total_seconds() * 1000)


def siapkan(kode: str) -> dict:
    """Kondisi dasar: Executor dijeda, sumber dipulihkan, event pemulihan ditunggu lalu rencananya
    dinetralkan, OBDF direset. Langkah pemulihan juga perubahan skema (ancaman validitas 1)."""
    sk = SKENARIO[kode]
    kendali('pause')
    tenang()
    if sk.siapkan:
        sk.siapkan()
    sebelum = id_event()
    if sk.pulihkan() not in ('bersih', ''):
        tunggu(lambda: next((e for e in api('/api/v1/obdf/1/events?limit=20')
                             if e['id'] not in sebelum), None), 20.0)
        time.sleep(2)
    dinetralkan = netralkan_rencana()
    reset()
    versi = api('/api/v1/obdf/1/versions?limit=1')
    return {'rencana_dinetralkan': dinetralkan,
            'versi_dasar': versi[0]['version_no'] if isinstance(versi, list) and versi else None}


# ── mode baseline (§3.10.1) ───────────────────────────────────────────────────
def satu_baseline(kode: str, nomor: int, jeda_amati: float) -> dict:
    sk = SKENARIO[kode]
    catatan = {'mode': 'baseline', 'skenario': kode, 'kode_baseline': 'b' + kode[1:],
               'judul': sk.judul, 'run': nomor, 'mulai': datetime.now(timezone.utc).isoformat()}
    catatan.update(siapkan(kode))                  # Executor tetap dijeda: tanpa adaptasi
    catatan['jawaban_sebelum'] = kueri.jalankan_stabil(kode)
    catatan['ddl'] = sk.terapkan()
    time.sleep(jeda_amati)                         # beri waktu perubahan berlaku di sumber
    if sk.isi_data:
        catatan['isi_data'] = sk.isi_data()
    catatan['jawaban_sesudah'] = kueri.jalankan_himpunan(kode)
    catatan['penilaian'] = kueri.nilai(kode, catatan['jawaban_sebelum'], catatan['jawaban_sesudah'])
    catatan['artefak_tidak_berubah'] = subprocess.run(
        ['git', 'status', '--short', 'setup/vkg-system/config'], cwd=ROOT,
        capture_output=True, text=True).stdout.strip() == ''
    catatan['selesai'] = datetime.now(timezone.utc).isoformat()
    return catatan


# ── mode perlakuan (§3.10.2) ──────────────────────────────────────────────────
def satu_perlakuan(kode: str, nomor: int, batas: float) -> dict:
    sk = SKENARIO[kode]
    catatan = {'mode': 'perlakuan', 'skenario': kode, 'judul': sk.judul,
               'pola_diharapkan': sk.pola, 'keputusan_diharapkan': sk.keputusan, 'run': nomor,
               'mulai': datetime.now(timezone.utc).isoformat()}
    catatan.update(siapkan(kode))
    catatan['jawaban_sebelum'] = kueri.jalankan_stabil(kode)
    event_awal = id_event()
    eksekusi_awal = {e['id'] for e in api('/api/v1/obdf/1/executions?limit=50')}
    kendali('resume')

    t0_wall = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    catatan['t_start'] = t0_wall
    catatan['ddl'] = sk.terapkan()

    event = tunggu(lambda: next((e for e in api('/api/v1/obdf/1/events?limit=20')
                                 if e['id'] not in event_awal), None), batas)
    catatan['deteksi_ms'] = int((time.perf_counter() - t0) * 1000) if event else None
    if event is None:
        catatan['hasil'] = 'event tidak diterima'
        return catatan
    catatan['event'] = {'id': event['id'], 'status': event['status'], 'structured': event['structured']}

    rencana = next((p for p in api('/api/v1/obdf/1/plans?limit=5') if p['event_id'] == event['id']), None)
    catatan['keputusan'] = rencana['decision'] if rencana else None
    catatan['pola'] = rencana['pattern'] if rencana else None
    catatan['sesuai_harapan'] = catatan['keputusan'] == sk.keputusan and catatan['pola'] == sk.pola

    # HITL (ADR-0021): satu persetujuan = satu keputusan manual; penyuntingan artefak tetap nol
    catatan['n_manual'] = 0
    catatan['keputusan_ms'] = 0
    if rencana and rencana['status'] == 'pending_approval':
        t_rencana = time.perf_counter()
        hasil = api(f"/api/v1/plans/{rencana['id']}/approve", 'POST',
                    {'note': 'persetujuan evaluator (protokol F6)'})
        catatan['n_manual'] = 1
        catatan['keputusan_ms'] = int((time.perf_counter() - t_rencana) * 1000)
        catatan['disetujui_oleh'] = hasil.get('decided_by')

    eksekusi = tunggu(lambda: next((e for e in api('/api/v1/obdf/1/executions?limit=5')
                                    if e['id'] not in eksekusi_awal and e['status'] != 'running'),
                                   None), batas)
    catatan['adaptasi_ms'] = int((time.perf_counter() - t0) * 1000) if eksekusi else None
    if eksekusi is None:
        catatan['hasil'] = 'eksekusi tidak selesai'
        return catatan
    langkah = {s['name']: s for s in eksekusi['steps']}
    catatan['eksekusi'] = {'id': eksekusi['id'], 'status': eksekusi['status'],
                           'timings': eksekusi['timings'], 'failure': eksekusi['failure']}
    catatan['langkah'] = {n: s['detail'].get('duration_ms') for n, s in langkah.items()}
    catatan['status_langkah'] = {n: s['status'] for n, s in langkah.items()}
    catatan['hasil'] = eksekusi['status']
    # Δt_adapt (pers. 3.16): t_start = DDL dieksekusi; t_end = artefak termodifikasi dan siap
    # dipakai, yaitu saat verifikasi SPARQL pasca-muat-ulang selesai.
    catatan['dt_adapt_ms'] = ms_antara(t0_wall, (langkah.get('verify') or {}).get('finished_at'))
    catatan['dt_adapt_mesin_ms'] = (catatan['dt_adapt_ms'] - catatan['keputusan_ms']
                                    if catatan['dt_adapt_ms'] is not None else None)

    if sk.isi_data:
        catatan['isi_data'] = sk.isi_data()
    catatan['jawaban_sesudah'] = kueri.jalankan_himpunan(kode)
    catatan['penilaian'] = kueri.nilai(kode, catatan['jawaban_sebelum'], catatan['jawaban_sesudah'])
    catatan['harapan'] = kueri.sesuai_harapan(kode, catatan['jawaban_sesudah'], catatan['penilaian'])
    catatan['selesai'] = datetime.now(timezone.utc).isoformat()
    return catatan


# ── audit C_safe (§3.11.1) ────────────────────────────────────────────────────
def audit_kafka(perintah: str, keluaran: Path) -> dict:
    """Menjalankan audit_kafka.py di kontainer yang tersambung ke jaringan Kafka."""
    skrip = ('pip install -q --root-user-action=ignore kafka-python-ng >/dev/null 2>&1 && '
             f'python /f6/audit_kafka.py {perintah}')
    hasil = subprocess.run(['docker', 'run', '--rm', '--network', 'ascam-networks',
                            '-v', f'{F6}:/f6:ro', '-v', f'{keluaran}:/out:ro',
                            'python:3.12-slim', 'sh', '-c', skrip],
                           capture_output=True, text=True)
    try:
        return json.loads(hasil.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {'_galat': (hasil.stderr or hasil.stdout)[-500:]}


def periksa_konfigurasi_debezium() -> dict:
    """Langkah pertama prosedur ego sektoral (§3.10.3 butir 1a)."""
    hasil = {}
    for nama in ('register-postgres.json', 'register-mysql.json'):
        konfigurasi = json.loads((ROOT / 'setup/ascam/schema-monitor' / nama).read_text())['config']
        daftar = [t.strip() for t in konfigurasi.get('table.include.list', '').split(',') if t.strip()]
        hasil[nama] = {'table.include.list': daftar,
                       'hanya_ddl_event_log': bool(daftar) and all(
                           t.endswith('.ddl_event_log') for t in daftar)}
    return hasil


# ── utama ─────────────────────────────────────────────────────────────────────
def main() -> int:
    global UI
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=['perlakuan', 'baseline', 'keduanya'], default='keduanya')
    parser.add_argument('--skenario', action='append', choices=sorted(SKENARIO))
    parser.add_argument('--ulangan', type=int, default=20, help='ulangan per skenario perlakuan')
    parser.add_argument('--ulangan-baseline', type=int, default=5, help='ulangan per skenario baseline')
    parser.add_argument('--batas', type=float, default=180.0, help='batas tunggu per tahap (detik)')
    parser.add_argument('--jeda-amati', type=float, default=15.0,
                        help='jeda sesudah DDL sebelum kueri baseline (detik)')
    parser.add_argument('--lewati-periksa', action='store_true')
    args = parser.parse_args()
    UI = token('ui')
    daftar = args.skenario or sorted(SKENARIO)

    if not args.lewati_periksa:
        print('===== pemeriksaan awal =====')
        siap, masalah = periksa()
        rantai_ok, keterangan = uji_rantai()
        if not siap or not rantai_ok:
            print('\nEvaluasi dibatalkan:')
            for m in masalah + ([] if rantai_ok else [f'rantai event terputus: {keterangan}']):
                print(f'  - {m}')
            return 1

    keluaran = ROOT / 'results/f6' / datetime.now().strftime('%Y%m%dT%H%M%S')
    keluaran.mkdir(parents=True, exist_ok=True)
    print(f'Hasil akan ditulis ke {keluaran.relative_to(ROOT)}')
    (keluaran / 'konfigurasi-debezium.json').write_text(
        json.dumps(periksa_konfigurasi_debezium(), indent=2))
    tunggu(lambda: api('/health', base=EXECUTOR, bearer='').get('state') == 'running', 60)

    try:
        if args.mode in ('baseline', 'keduanya'):
            print('\n######## BASELINE (Executor dijeda, tanpa adaptasi) ########')
            for kode in daftar:
                for nomor in range(1, args.ulangan_baseline + 1):
                    print(f'\n--- b{kode[1:]} run {nomor}/{args.ulangan_baseline}')
                    c = satu_baseline(kode, nomor, args.jeda_amati)
                    (keluaran / f'baseline-{kode}-{nomor:02d}.json').write_text(
                        json.dumps(c, indent=2, ensure_ascii=False))
                    for nama, h in c['jawaban_sesudah'].items():
                        print(f"    {nama:22s} HTTP {h['status']} baris={h['n']} "
                              f"{(h['galat'] or '')[:90]}")

        if args.mode in ('perlakuan', 'keduanya'):
            print('\n######## PERLAKUAN (siklus MAPE-K ASCAM) ########')
            awal = audit_kafka('offset', keluaran)
            (keluaran / 'kafka-awal.json').write_text(json.dumps(awal, indent=2))
            print(f'  titik awal audit Kafka: {len(awal)} topik {sorted(awal)}')
            for kode in daftar:
                for nomor in range(1, args.ulangan + 1):
                    print(f'\n--- {kode} run {nomor}/{args.ulangan}')
                    c = satu_perlakuan(kode, nomor, args.batas)
                    (keluaran / f'run-{kode}-{nomor:02d}.json').write_text(
                        json.dumps(c, indent=2, ensure_ascii=False))
                    p = (c.get('penilaian') or {})
                    print(f"    hasil={c.get('hasil')} keputusan={c.get('keputusan')} "
                          f"deteksi={c.get('deteksi_ms')} ms dt_adapt={c.get('dt_adapt_ms')} ms "
                          f"preservation={p.get('preservation_ratio')}% harapan={c.get('harapan')}")
            hasil_audit = audit_kafka('audit /out/kafka-awal.json', keluaran)
            (keluaran / 'kafka-audit.json').write_text(json.dumps(hasil_audit, indent=2))
            print(f"\n  audit Kafka: N_log={hasil_audit.get('N_log')} N_prod={hasil_audit.get('N_prod')} "
                  f"C_safe={hasil_audit.get('C_safe')}")
    finally:
        kendali('pause')
        tenang()
        netralkan_rencana()
        for kode in daftar:
            SKENARIO[kode].pulihkan()
        reset()
        kendali('resume')
    print(f'\nSelesai. Analisis: python3 experiments/f6/analisis.py {keluaran.relative_to(ROOT)}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
