"""
F6 — Analisis hasil evaluasi.

Menghitung statistik per skenario (keberhasilan, ketepatan keputusan D11, waktu deteksi dan
adaptasi beserta dekomposisinya) dan MEMBEDAH setiap anomali: run yang gagal, keputusan yang
tidak sesuai harapan, dan jawaban SPARQL yang berbeda padahal seharusnya identik.

Waktu adaptasi ujung ke ujung mencakup deteksi dan jeda penjadwalan Executor; keduanya
dipisahkan agar dapat dilaporkan apa adanya.

Dijalankan dari host:
  python3 experiments/f6/analisis.py                      # direktori hasil terbaru
  python3 experiments/f6/analisis.py results/f6/2026...   # direktori tertentu

Simpan keluarannya di luar results/f6 agar tidak tertukar dengan direktori hasil, misalnya:
  python3 experiments/f6/analisis.py > results/analisis-evaluasi.md
"""
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LANGKAH = ['deploy_vdb', 'validate', 'switch', 'reload_ontop', 'verify', 'sync']
# Harapan kesetaraan jawaban per skenario: RENAME harus identik, DROP harus berubah,
# ADD identik selama kolom baru belum berisi data.
IDENTIK_DIHARAPKAN = {'a001': True, 'a002': False, 'a003': True}


def kuartil(nilai: list[float]) -> str:
    if not nilai:
        return '-'
    urut = sorted(nilai)
    if len(urut) < 4:
        return f'{int(statistics.median(urut))}'
    q1, q3 = statistics.quantiles(urut, n=4)[0], statistics.quantiles(urut, n=4)[2]
    return f'{int(statistics.median(urut))} ({int(q1)}–{int(q3)})'


def muat(direktori: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(direktori.glob('run-*.json'))]


def total_langkah(run: dict) -> int | None:
    langkah = run.get('langkah') or {}
    nilai = [v for v in langkah.values() if isinstance(v, int)]
    return sum(nilai) if nilai else None


def bedah_jawaban(run: dict) -> list[str]:
    """Kueri mana yang berbeda, dan seperti apa bedanya."""
    sebelum, sesudah = run.get('jawaban_sebelum') or {}, run.get('jawaban_sesudah') or {}
    catatan = []
    if not sesudah:
        return ['jawaban sesudah tidak terekam (run tidak selesai)']
    for kunci in sorted(set(sebelum) | set(sesudah)):
        a, b = sebelum.get(kunci), sesudah.get(kunci)
        if a == b:
            continue
        if a is None or b is None or isinstance(a, dict) or isinstance(b, dict):
            # dict = kueri gagal dijalankan, None = cuplikan tidak terambil
            catatan.append(f'{kunci}: tidak dapat dibandingkan (sebelum={type(a).__name__}, '
                           f'sesudah={type(b).__name__}) {a if isinstance(a, dict) else b}')
            continue
        hanya_sebelum = [x for x in a if x not in b]
        hanya_sesudah = [x for x in b if x not in a]
        catatan.append(f'{kunci}: {len(a)} -> {len(b)} baris')
        for baris in hanya_sebelum[:3]:
            catatan.append(f'    hilang : {baris[:150]}')
        for baris in hanya_sesudah[:3]:
            catatan.append(f'    muncul : {baris[:150]}')
    return catatan


def main() -> int:
    if len(sys.argv) > 1:
        direktori = Path(sys.argv[1])
        if not direktori.is_absolute():
            direktori = ROOT / direktori
    else:
        kandidat = sorted(p for p in (ROOT / 'results/f6').glob('*') if p.is_dir())
        if not kandidat:
            print('tidak ada hasil evaluasi di results/f6')
            return 1
        direktori = kandidat[-1]
    runs = muat(direktori)
    if not runs:
        print(f'tidak ada berkas run-*.json di {direktori}')
        return 1
    print(f'# Analisis evaluasi ASCAM\n\nSumber: {direktori.relative_to(ROOT)} '
          f'({len(runs)} run)\n')

    skenario = sorted({r['skenario'] for r in runs})
    print('## Keberhasilan dan ketepatan keputusan\n')
    print('| Skenario | Run | Berhasil | Keputusan D11 benar | Jawaban sesuai harapan |')
    print('|---|---:|---:|---:|---:|')
    for kode in skenario:
        bagian = [r for r in runs if r['skenario'] == kode]
        berhasil = [r for r in bagian if r.get('hasil') == 'succeeded']
        benar = [r for r in bagian if r.get('sesuai_harapan')]
        sesuai = [r for r in berhasil if r.get('jawaban_identik') is IDENTIK_DIHARAPKAN.get(kode)]
        print(f'| {kode} | {len(bagian)} | {len(berhasil)}/{len(bagian)} | '
              f'{len(benar)}/{len(bagian)} | {len(sesuai)}/{len(berhasil)} |')

    print('\n## Waktu, median (kuartil 1–3), milidetik\n')
    print('| Skenario | Deteksi | Kerja adaptasi | Ujung ke ujung | Jeda penjadwalan |')
    print('|---|---:|---:|---:|---:|')
    for kode in skenario:
        bagian = [r for r in runs if r['skenario'] == kode and r.get('hasil') == 'succeeded']
        deteksi = [r['deteksi_ms'] for r in bagian if r.get('deteksi_ms')]
        kerja = [total_langkah(r) for r in bagian if total_langkah(r)]
        ujung = [r['adaptasi_ms'] for r in bagian if r.get('adaptasi_ms')]
        jeda = [r['adaptasi_ms'] - (r.get('deteksi_ms') or 0) - (total_langkah(r) or 0)
                for r in bagian if r.get('adaptasi_ms') and total_langkah(r)]
        print(f'| {kode} | {kuartil(deteksi)} | {kuartil(kerja)} | {kuartil(ujung)} | '
              f'{kuartil(jeda)} |')

    print('\n## Dekomposisi kerja adaptasi, median (kuartil 1–3), milidetik\n')
    print('| Skenario | ' + ' | '.join(LANGKAH) + ' |')
    print('|---|' + '---:|' * len(LANGKAH))
    for kode in skenario:
        bagian = [r for r in runs if r['skenario'] == kode and r.get('hasil') == 'succeeded']
        sel = []
        for nama in LANGKAH:
            nilai = [(r.get('langkah') or {}).get(nama) for r in bagian]
            sel.append(kuartil([v for v in nilai if isinstance(v, int)]))
        print(f'| {kode} | ' + ' | '.join(sel) + ' |')

    anomali = [r for r in runs
               if r.get('hasil') != 'succeeded' or not r.get('sesuai_harapan')
               or r.get('jawaban_identik') is not IDENTIK_DIHARAPKAN.get(r['skenario'])]
    print(f'\n## Anomali ({len(anomali)} dari {len(runs)} run)\n')
    if not anomali:
        print('Tidak ada.')
    for run in anomali:
        print(f"### {run['skenario']} run {run['run']}: hasil={run.get('hasil')}, "
              f"keputusan={run.get('keputusan')} ({run.get('pola')}), "
              f"identik={run.get('jawaban_identik')}")
        eksekusi = run.get('eksekusi') or {}
        if eksekusi:
            print(f"  status eksekusi {eksekusi.get('id')}: {eksekusi.get('status')}")
            if eksekusi.get('failure'):
                print(f"  kegagalan: {json.dumps(eksekusi['failure'])[:400]}")
        if run.get('status_langkah'):
            print(f"  langkah: {run['status_langkah']}")
        if run.get('event'):
            print(f"  event: {json.dumps(run['event'].get('structured'))[:200]}")
        for baris in bedah_jawaban(run):
            print(f'  {baris}')
        print()
    return 0


if __name__ == '__main__':
    sys.exit(main())
