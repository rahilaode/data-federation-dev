"""
Mengekstrak hasil evaluasi F6 menjadi tabel per run dan ringkasan per skenario.

Masukan : direktori hasil evaluate.py (bawaan: direktori terbaru di results/f6)
Keluaran: <direktori>/ringkasan/
  runs.csv        satu baris per run perlakuan (keputusan, waktu per langkah, preservation)
  baseline.csv    satu baris per kueri per run baseline (status HTTP, baris, galat)
  ringkasan.json  statistik per skenario: median, kuartil, min, maks; keberhasilan;
                  PreservationRatio; ExecutionPreserved baseline; audit Kafka (C_safe)

Kuartil memakai statistics.quantiles(n=4) (metode bawaan 'exclusive'), sama dengan
analisis.py, sehingga angkanya identik dengan laporan analisis.

Pemakaian (dari root repository):
  python3 experiments/f6/ekstrak.py
  python3 experiments/f6/ekstrak.py results/f6/20260924T141111
"""
import csv
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LANGKAH = ['deploy_vdb', 'validate', 'switch', 'reload_ontop', 'verify', 'sync']
# Komponen Δt_adapt: dari DDL sampai verifikasi selesai (sync terjadi sesudah t_end)
KOMPONEN_DT = ['deploy_vdb', 'validate', 'switch', 'reload_ontop', 'verify']
OPERASI = {'a001': 'ADD', 'a002': 'DROP', 'a003': 'RENAME'}


def statistik(nilai: list) -> dict | None:
    nilai = sorted(v for v in nilai if isinstance(v, (int, float)))
    if not nilai:
        return None
    hasil = {'n': len(nilai), 'median': statistics.median(nilai), 'min': nilai[0], 'max': nilai[-1]}
    if len(nilai) >= 4:
        q1, _, q3 = statistics.quantiles(nilai, n=4)
        hasil.update(q1=q1, q3=q3)
    else:
        hasil.update(q1=None, q3=None)
    return hasil


def pilih_direktori() -> Path:
    if len(sys.argv) > 1:
        d = Path(sys.argv[1])
        return d if d.is_absolute() else ROOT / d
    kandidat = sorted(p for p in (ROOT / 'results/f6').glob('*') if p.is_dir())
    if not kandidat:
        sys.exit('tidak ada direktori hasil di results/f6')
    return kandidat[-1]


def main() -> int:
    direktori = pilih_direktori()
    runs = [json.loads(p.read_text()) for p in sorted(direktori.glob('run-*.json'))]
    baseline = [json.loads(p.read_text()) for p in sorted(direktori.glob('baseline-*.json'))]
    if not runs and not baseline:
        sys.exit(f'tidak ada berkas run di {direktori}')
    keluar = direktori / 'ringkasan'
    keluar.mkdir(exist_ok=True)

    # ── runs.csv ──────────────────────────────────────────────────────────────
    kolom = ['skenario', 'operasi', 'run', 'hasil', 'keputusan', 'pola', 'sesuai_harapan',
             'harapan_terpenuhi', 'n_manual', 'keputusan_ms', 'deteksi_ms', 'dt_adapt_ms',
             'dt_adapt_mesin_ms', 'adaptasi_ms', *[f'{n}_ms' for n in LANGKAH],
             'metrik', 'preservation_ratio']
    with open(keluar / 'runs.csv', 'w', newline='', encoding='utf-8') as berkas:
        tulis = csv.DictWriter(berkas, fieldnames=kolom)
        tulis.writeheader()
        for r in runs:
            langkah = r.get('langkah') or {}
            penilaian = r.get('penilaian') or {}
            harapan = r.get('harapan')
            tulis.writerow({
                'skenario': r['skenario'], 'operasi': OPERASI.get(r['skenario'], ''), 'run': r['run'],
                'hasil': r.get('hasil'), 'keputusan': r.get('keputusan'), 'pola': r.get('pola'),
                'sesuai_harapan': r.get('sesuai_harapan'),
                'harapan_terpenuhi': all(harapan.values()) if harapan else None,
                'n_manual': r.get('n_manual'), 'keputusan_ms': r.get('keputusan_ms'),
                'deteksi_ms': r.get('deteksi_ms'), 'dt_adapt_ms': r.get('dt_adapt_ms'),
                'dt_adapt_mesin_ms': r.get('dt_adapt_mesin_ms'), 'adaptasi_ms': r.get('adaptasi_ms'),
                **{f'{n}_ms': langkah.get(n) for n in LANGKAH},
                'metrik': penilaian.get('metrik'), 'preservation_ratio': penilaian.get('preservation_ratio'),
            })

    # ── baseline.csv ──────────────────────────────────────────────────────────
    with open(keluar / 'baseline.csv', 'w', newline='', encoding='utf-8') as berkas:
        tulis = csv.writer(berkas)
        tulis.writerow(['skenario', 'run', 'kueri', 'jenis', 'status_sebelum', 'baris_sebelum',
                        'status_sesudah', 'baris_sesudah', 'galat_sesudah'])
        for r in baseline:
            if not r.get('penilaian'):
                continue
            for nama, nilai in r['penilaian']['per_kueri'].items():
                sesudah = r['jawaban_sesudah'][nama]
                galat = ' '.join((sesudah.get('galat') or '').split())[:200]
                tulis.writerow([r['kode_baseline'], r['run'], nama, nilai['jenis'],
                                r['jawaban_sebelum'][nama]['status'], nilai['baris_sebelum'],
                                sesudah['status'], nilai['baris_sesudah'], galat])

    # ── ringkasan.json ────────────────────────────────────────────────────────
    ringkasan = {'sumber': str(direktori.relative_to(ROOT)), 'skenario': {}}
    for kode in sorted({r['skenario'] for r in runs} | {r['skenario'] for r in baseline}):
        semua = [r for r in runs if r['skenario'] == kode]
        ok = [r for r in semua if r.get('hasil') == 'succeeded']
        s = {'operasi': OPERASI.get(kode, kode), 'run': len(semua), 'berhasil': len(ok)}
        if semua:
            s['keputusan_sesuai'] = sum(1 for r in semua if r.get('sesuai_harapan'))
            s['harapan_terpenuhi'] = sum(1 for r in ok if r.get('harapan') and all(r['harapan'].values()))
            s['keputusan'] = sorted({r.get('keputusan') for r in ok if r.get('keputusan')})
            s['n_manual_keputusan'] = sorted({r.get('n_manual', 0) for r in ok})
            s['waktu_ms'] = {
                'deteksi': statistik([r.get('deteksi_ms') for r in ok]),
                'dt_adapt': statistik([r.get('dt_adapt_ms') for r in ok]),
                'dt_adapt_mesin': statistik([r.get('dt_adapt_mesin_ms') for r in ok]),
                **{n: statistik([(r.get('langkah') or {}).get(n) for r in ok]) for n in LANGKAH},
            }
            w = s['waktu_ms']
            if w['dt_adapt'] and w['deteksi'] and all(w[n] for n in KOMPONEN_DT):
                # Jeda deteksi -> awal eksekusi (ingestion, planning, pick-up), turunan dari median
                s['waktu_ms']['jeda_turunan_median'] = (
                    w['dt_adapt']['median'] - w['deteksi']['median']
                    - sum(w[n]['median'] for n in KOMPONEN_DT))
            s['di_bawah_60s'] = sum(1 for r in ok if (r.get('dt_adapt_ms') or 1e9) < 60_000)
            rasio = [r['penilaian']['preservation_ratio'] for r in ok if r.get('penilaian')]
            s['preservation'] = {
                'metrik': ok[0]['penilaian']['metrik'] if ok and ok[0].get('penilaian') else None,
                'rata_rata': statistics.mean(rasio) if rasio else None,
                'run_100': sum(1 for v in rasio if v == 100.0)}
            s['anomali'] = [{'run': r['run'], 'hasil': r.get('hasil')} for r in semua
                            if r.get('hasil') != 'succeeded']
        dasar = [r for r in baseline if r['skenario'] == kode and r.get('penilaian')]
        if dasar:
            ep = [100 * sum(v['execution_preserved'] for v in r['penilaian']['per_kueri'].values())
                  / len(r['penilaian']['per_kueri']) for r in dasar]
            s['baseline'] = {'run': len(dasar), 'execution_preserved_rata_rata': statistics.mean(ep)}
        ringkasan['skenario'][kode] = s

    audit = direktori / 'kafka-audit.json'
    if audit.exists():
        a = json.loads(audit.read_text())
        ringkasan['c_safe'] = {k: a.get(k) for k in ('N_log', 'N_prod', 'C_safe', 'per_topik', 'hitung')}
    (keluar / 'ringkasan.json').write_text(json.dumps(ringkasan, indent=2, ensure_ascii=False))

    # ── ringkasan singkat di layar ────────────────────────────────────────────
    print(f'Sumber: {ringkasan["sumber"]}')
    for kode, s in ringkasan['skenario'].items():
        w = s.get('waktu_ms', {})
        dt = w.get('dt_adapt') or {}
        print(f"  {kode} {s['operasi']:6s} berhasil {s['berhasil']}/{s['run']}"
              f"  Δt_adapt median {dt.get('median', 0) / 1000:.2f} s"
              f" (IQR {(dt.get('q1') or 0) / 1000:.2f}–{(dt.get('q3') or 0) / 1000:.2f})"
              f"  maks {dt.get('max', 0) / 1000:.2f} s")
    if 'c_safe' in ringkasan:
        c = ringkasan['c_safe']
        print(f"  C_safe: N_log={c['N_log']} N_prod={c['N_prod']} terpenuhi={c['C_safe']}")
    print(f'Ditulis ke {keluar.relative_to(ROOT)}/: runs.csv, baseline.csv, ringkasan.json')
    return 0


if __name__ == '__main__':
    sys.exit(main())
