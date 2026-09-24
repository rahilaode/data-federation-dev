"""
F6 — Analisis hasil evaluasi, disusun mengikuti proposal §3.10–3.11.

  1. Skenario baseline B001–B003 (§3.10.1): status HTTP, pesan galat, dan hasil per kueri.
  2. Skenario perlakuan A001–A003 (§3.10.2): keberhasilan, keputusan D11, harapan teoretis.
  3. C_safe (§3.11.1): konfigurasi Debezium, N_log, N_prod.
  4. C_FMI (§3.11.2): Δt_adapt terhadap batas 60 detik, N_manual (keputusan vs perbaikan),
     dekomposisi waktu per langkah.
  5. Semantic preservation (§3.11.3): PreservationRatio per skenario dan per kueri.
  6. Perbandingan baseline dan perlakuan.
  7. Anomali.

Pemakaian:
  python3 experiments/f6/analisis.py                    # direktori hasil terbaru
  python3 experiments/f6/analisis.py results/f6/<waktu> > results/analisis-<waktu>.md
"""
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LANGKAH = ['deploy_vdb', 'validate', 'switch', 'reload_ontop', 'verify', 'sync']
BATAS_DT = 60_000


def kuartil(nilai: list) -> str:
    nilai = [v for v in nilai if isinstance(v, (int, float))]
    if not nilai:
        return '-'
    if len(nilai) < 4:
        return f'{int(statistics.median(nilai))}'
    q = statistics.quantiles(sorted(nilai), n=4)
    return f'{int(statistics.median(nilai))} ({int(q[0])}–{int(q[2])})'


def muat(direktori: Path, pola: str) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(direktori.glob(pola))]


def total_langkah(run: dict) -> int | None:
    nilai = [v for v in (run.get('langkah') or {}).values() if isinstance(v, int)]
    return sum(nilai) if nilai else None


def main() -> int:
    if len(sys.argv) > 1:
        direktori = Path(sys.argv[1])
        direktori = direktori if direktori.is_absolute() else ROOT / direktori
    else:
        kandidat = sorted(p for p in (ROOT / 'results/f6').glob('*') if p.is_dir())
        if not kandidat:
            print('tidak ada hasil evaluasi di results/f6')
            return 1
        direktori = kandidat[-1]
    baseline = muat(direktori, 'baseline-*.json')
    perlakuan = muat(direktori, 'run-*.json')
    print(f'# Analisis evaluasi ASCAM\n\nSumber: {direktori.relative_to(ROOT)} '
          f'({len(baseline)} run baseline, {len(perlakuan)} run perlakuan)\n')

    # ── 1. baseline ──────────────────────────────────────────────────────────────
    if baseline:
        print('## 1. Skenario baseline tanpa ASCAM (§3.10.1)\n')
        for kode in sorted({r['skenario'] for r in baseline}):
            semua = [r for r in baseline if r['skenario'] == kode]
            runs = [r for r in semua if r.get('penilaian')]
            if not runs:
                print(f"### B{kode[1:]}: tidak ada run yang tuntas ({len(semua)} run berhenti)\n")
                continue
            print(f"### B{kode[1:]}: {runs[0]['judul']} ({len(runs)} run"
                  f"{f', {len(semua) - len(runs)} tidak tuntas' if len(semua) > len(runs) else ''})\n")
            print('| Kueri | Jenis | Sebelum (HTTP, baris) | Sesudah (HTTP, baris) | Pesan galat sesudah |')
            print('|---|---|---|---|---|')
            for nama, jenis in ((n, v['jenis']) for n, v in runs[0]['penilaian']['per_kueri'].items()):
                a = Counter((r['jawaban_sebelum'][nama]['status'], r['jawaban_sebelum'][nama]['n']) for r in runs)
                b = Counter((r['jawaban_sesudah'][nama]['status'], r['jawaban_sesudah'][nama]['n']) for r in runs)
                galat = next((r['jawaban_sesudah'][nama]['galat'] for r in runs
                              if r['jawaban_sesudah'][nama]['galat']), '')
                galat = ' '.join((galat or '').split())[:110]
                print(f"| {nama} | {jenis} | {', '.join(f'{s}, {n}' for (s, n), _ in a.most_common())} | "
                      f"{', '.join(f'{s}, {n}' for (s, n), _ in b.most_common())} | {galat or '-'} |")
            ep = [r['penilaian']['per_kueri'] for r in runs]
            rasio = statistics.mean(100 * sum(v['execution_preserved'] for v in p.values()) / len(p) for p in ep)
            utuh = sum(1 for r in runs if r.get('artefak_tidak_berubah'))
            print(f'\nExecutionPreserved rata-rata tanpa ASCAM: {rasio:.1f} %; artefak OBDF tidak '
                  f'berubah pada {utuh}/{len(runs)} run.\n')

    tidak_tuntas = [r for r in baseline if not r.get('penilaian')]
    if tidak_tuntas:
        print('Run baseline tidak tuntas: ' + ', '.join(
            f"B{r['skenario'][1:]} run {r['run']} ({r.get('hasil')})" for r in tidak_tuntas) + '\n')
    if not perlakuan:
        return 0

    # ── 2. perlakuan ─────────────────────────────────────────────────────────────
    skenario = sorted({r['skenario'] for r in perlakuan})
    print('## 2. Skenario perlakuan dengan ASCAM (§3.10.2)\n')
    print('| Skenario | Run | Berhasil | Keputusan D11 sesuai | Harapan teoretis terpenuhi |')
    print('|---|---:|---:|---:|---:|')
    for kode in skenario:
        runs = [r for r in perlakuan if r['skenario'] == kode]
        ok = [r for r in runs if r.get('hasil') == 'succeeded']
        benar = sum(1 for r in runs if r.get('sesuai_harapan'))
        harapan = sum(1 for r in ok if r.get('harapan') and all(r['harapan'].values()))
        print(f'| {kode.upper()} | {len(runs)} | {len(ok)}/{len(runs)} | {benar}/{len(runs)} | '
              f'{harapan}/{len(ok)} |')
    print()
    for kode in skenario:
        contoh = next((r['harapan'] for r in perlakuan if r['skenario'] == kode and r.get('harapan')), {})
        print(f"- {kode.upper()}: {', '.join(contoh) or '-'}")

    # ── 3. C_safe ────────────────────────────────────────────────────────────────
    print('\n## 3. Otonomi data: C_safe (§3.11.1)\n')
    konfigurasi = direktori / 'konfigurasi-debezium.json'
    if konfigurasi.exists():
        for nama, isi in json.loads(konfigurasi.read_text()).items():
            print(f"- `{nama}`: table.include.list = {isi['table.include.list']} "
                  f"({'hanya ddl_event_log' if isi['hanya_ddl_event_log'] else 'MEMUAT tabel lain'})")
    audit = direktori / 'kafka-audit.json'
    if audit.exists():
        a = json.loads(audit.read_text())
        if '_galat' in a:
            print(f"\nAudit Kafka gagal: {a['_galat']}")
        else:
            print(f"\n| N_log | N_prod | Perubahan skema | Lain | C_safe |\n|---:|---:|---:|---:|---|")
            print(f"| {a['N_log']} | {a['N_prod']} | {a['hitung']['perubahan_skema']} | "
                  f"{a['hitung']['lain'] + a['hitung']['tombstone']} | "
                  f"{'terpenuhi' if a['C_safe'] else 'TIDAK terpenuhi'} |")
            print('\nPer topik: ' + '; '.join(f'{t}: {v}' for t, v in a['per_topik'].items()))
            print(f"\nKolom yang dibawa pesan log: {', '.join(a['kolom_pesan_log'])}")
            if a['contoh_pesan_produksi']:
                print(f"\nPESAN PRODUKSI TERDETEKSI: {a['contoh_pesan_produksi'][:5]}")

    # ── 4. C_FMI ─────────────────────────────────────────────────────────────────
    print('\n## 4. Perbaikan mapping yang lebih cepat: C_FMI (§3.11.2)\n')
    print('| Skenario | Δt_adapt (ms) | Δt_adapt mesin (ms) | Maks (ms) | < 60 detik | '
          'N_manual keputusan | N_manual perbaikan |')
    print('|---|---:|---:|---:|---:|---:|---:|')
    for kode in skenario:
        ok = [r for r in perlakuan if r['skenario'] == kode and r.get('hasil') == 'succeeded']
        dt = [r.get('dt_adapt_ms') for r in ok if r.get('dt_adapt_ms') is not None]
        mesin = [r.get('dt_adapt_mesin_ms') for r in ok if r.get('dt_adapt_mesin_ms') is not None]
        manual = sorted({r.get('n_manual', 0) for r in ok})
        print(f"| {kode.upper()} | {kuartil(dt)} | {kuartil(mesin)} | {max(dt) if dt else '-'} | "
              f"{sum(1 for v in dt if v < BATAS_DT)}/{len(dt)} | {', '.join(map(str, manual)) or '-'} | 0 |")
    print('\nΔt_adapt = t_end − t_start (pers. 3.16): t_start saat DDL dieksekusi, t_end saat verifikasi '
          'SPARQL pasca-muat-ulang selesai. Δt_adapt mesin tidak memuat jeda keputusan administrator.\n')
    print('| Skenario | Deteksi (ms) | ' + ' | '.join(LANGKAH) + ' |')
    print('|---|---:|' + '---:|' * len(LANGKAH))
    for kode in skenario:
        ok = [r for r in perlakuan if r['skenario'] == kode and r.get('hasil') == 'succeeded']
        sel = [kuartil([(r.get('langkah') or {}).get(n) for r in ok]) for n in LANGKAH]
        print(f"| {kode.upper()} | {kuartil([r.get('deteksi_ms') for r in ok])} | " + ' | '.join(sel) + ' |')

    # ── 5. semantic preservation ─────────────────────────────────────────────────
    print('\n## 5. Semantic preservation (§3.11.3)\n')
    print('| Skenario | Metrik | PreservationRatio rata-rata | Run dengan 100 % |')
    print('|---|---|---:|---:|')
    for kode in skenario:
        ok = [r for r in perlakuan if r['skenario'] == kode and r.get('penilaian')]
        if not ok:
            continue
        rasio = [r['penilaian']['preservation_ratio'] for r in ok]
        print(f"| {kode.upper()} | {ok[0]['penilaian']['metrik']} | {statistics.mean(rasio):.1f} % | "
              f"{sum(1 for v in rasio if v == 100.0)}/{len(ok)} |")
    print()
    for kode in skenario:
        ok = [r for r in perlakuan if r['skenario'] == kode and r.get('penilaian')]
        if not ok:
            continue
        metrik = ok[0]['penilaian']['metrik']
        print(f'**{kode.upper()}** per kueri ({metrik}):')
        for nama, v in ok[0]['penilaian']['per_kueri'].items():
            nilai = sum(r['penilaian']['per_kueri'][nama][metrik] for r in ok)
            baris = Counter((r['penilaian']['per_kueri'][nama]['baris_sebelum'],
                             r['penilaian']['per_kueri'][nama]['baris_sesudah']) for r in ok).most_common(1)[0][0]
            print(f'- {nama} ({v["jenis"]}): {nilai}/{len(ok)}; baris sebelum → sesudah: {baris[0]} → {baris[1]}')
        print()

    # ── 6. perbandingan ──────────────────────────────────────────────────────────
    if baseline:
        print('## 6. Perbandingan baseline dan perlakuan\n')
        print('| Operasi | ExecutionPreserved tanpa ASCAM | ExecutionPreserved dengan ASCAM | '
              'ResultPreserved dengan ASCAM |')
        print('|---|---:|---:|---:|')
        for kode in skenario:
            def rata(runs, kunci):
                p = [r['penilaian']['per_kueri'] for r in runs if r.get('penilaian')]
                return (statistics.mean(100 * sum(v[kunci] for v in x.values()) / len(x) for x in p)
                        if p else None)
            b = rata([r for r in baseline if r['skenario'] == kode], 'execution_preserved')
            ep = rata([r for r in perlakuan if r['skenario'] == kode], 'execution_preserved')
            rp = rata([r for r in perlakuan if r['skenario'] == kode], 'result_preserved')
            f = lambda v: '-' if v is None else f'{v:.1f} %'               # noqa: E731
            print(f'| {kode.upper()} / B{kode[1:]} | {f(b)} | {f(ep)} | {f(rp)} |')

    # ── 7. anomali ───────────────────────────────────────────────────────────────
    anomali = [r for r in perlakuan if r.get('hasil') != 'succeeded' or not r.get('sesuai_harapan')
               or not (r.get('harapan') and all(r['harapan'].values()))]
    print(f'\n## 7. Anomali ({len(anomali)} dari {len(perlakuan)} run perlakuan)\n')
    for r in anomali:
        print(f"- {r['skenario']} run {r['run']}: hasil={r.get('hasil')}, keputusan={r.get('keputusan')}, "
              f"harapan={r.get('harapan')}, kegagalan={(r.get('eksekusi') or {}).get('failure')}")
    if not anomali:
        print('Tidak ada.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
