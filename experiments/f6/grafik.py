"""
Menggambar dekomposisi waktu adaptasi Δt_adapt per operator dari ringkasan.json.

Batang bertumpuk memuat median tiap komponen, dari eksekusi DDL sampai verifikasi selesai:
deteksi, jeda sebelum eksekusi (turunan: median Δt_adapt dikurangi median komponen lain),
deploy/switch VDB, validasi, restart Ontop, dan verifikasi. Garis galat menunjukkan rentang
interkuartil Δt_adapt, dan angka di ujung batang adalah mediannya. Warna abu-abu dan arsiran
dipilih agar tetap terbaca bila dicetak hitam-putih.

Prasyarat: jalankan ekstrak.py lebih dulu, dan pasang matplotlib
  python3 -m pip install --user matplotlib

Pemakaian (dari root repository):
  python3 experiments/f6/grafik.py                                   # direktori hasil terbaru
  python3 experiments/f6/grafik.py results/f6/20260924T141111
Keluaran: <direktori>/ringkasan/fig_adaptation_time.{png,svg,pdf}
"""
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import rcParams  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
URUTAN = ['a003', 'a002', 'a001']          # dari bawah ke atas: ADD tampil paling atas
LABEL = {'a001': 'ADD (A001)', 'a002': 'DROP (A002)', 'a003': 'RENAME (A003)'}

# (label legenda, kunci di ringkasan.json, warna isi, arsiran); label diawali "_" = tanpa legenda
KOMPONEN = [
    ('Detection', 'deteksi', '#ffffff', '////'),
    ('Ingestion and planning', 'jeda_turunan_median', '#e6e6e6', ''),
    ('Deploy / switch VDB', 'deploy_vdb', '#000000', ''),
    ('Validate', 'validate', '#a6a6a6', ''),
    ('_switch', 'switch', '#000000', ''),             # < 5 ms, digabung pada entri deploy/switch
    ('Restart Ontop', 'reload_ontop', '#595959', ''),
    ('Verify', 'verify', '#ffffff', '....'),
]


def pilih_direktori() -> Path:
    if len(sys.argv) > 1:
        d = Path(sys.argv[1])
        return d if d.is_absolute() else ROOT / d
    kandidat = sorted(p for p in (ROOT / 'results/f6').glob('*') if p.is_dir())
    if not kandidat:
        sys.exit('tidak ada direktori hasil di results/f6')
    return kandidat[-1]


def median_detik(waktu: dict, kunci: str) -> float:
    nilai = waktu.get(kunci)
    if isinstance(nilai, dict):
        return nilai['median'] / 1000
    return (nilai or 0) / 1000                         # jeda_turunan_median berupa angka


def main() -> int:
    direktori = pilih_direktori()
    berkas = direktori / 'ringkasan' / 'ringkasan.json'
    if not berkas.exists():
        sys.exit(f'{berkas.relative_to(ROOT)} belum ada; jalankan ekstrak.py lebih dulu')
    ringkasan = json.loads(berkas.read_text())['skenario']
    kode = [k for k in URUTAN if k in ringkasan and ringkasan[k].get('berhasil')]
    if not kode:
        sys.exit('tidak ada skenario perlakuan yang berhasil untuk digambar')

    rcParams.update({'font.family': 'serif',
                     'font.serif': ['Times New Roman', 'Liberation Serif', 'DejaVu Serif'],
                     'font.size': 8, 'axes.linewidth': 0.6, 'hatch.linewidth': 0.5})
    fig, ax = plt.subplots(figsize=(6.1, 0.35 + 0.53 * len(kode)), dpi=300)

    label_y = [LABEL[k] for k in kode]
    kiri = [0.0] * len(kode)
    for label, kunci, warna, arsir in KOMPONEN:
        nilai = [max(median_detik(ringkasan[k]['waktu_ms'], kunci), 0.0) for k in kode]
        ax.barh(label_y, nilai, left=kiri, color=warna, edgecolor='black', linewidth=0.5,
                hatch=arsir, height=0.55, label=label)
        kiri = [a + b for a, b in zip(kiri, nilai)]

    batas_kanan = 0.0
    for i, k in enumerate(kode):
        dt = ringkasan[k]['waktu_ms']['dt_adapt']
        lo, hi, med = dt['q1'] / 1000, dt['q3'] / 1000, dt['median'] / 1000
        ax.plot([lo, hi], [i, i], color='black', linewidth=0.8)
        for x in (lo, hi):
            ax.plot([x, x], [i - 0.12, i + 0.12], color='black', linewidth=0.8)
        ax.text(max(hi, med) + 0.4, i, f'{med:.1f} s', va='center', fontsize=7.5)
        batas_kanan = max(batas_kanan, hi, med)

    ax.set_xlim(0, batas_kanan * 1.15)                 # ruang untuk label median
    ax.set_xlabel('Time from DDL execution (s)')
    ax.xaxis.grid(True, linewidth=0.3, color='#bbbbbb')
    ax.set_axisbelow(True)
    for sisi in ('top', 'right'):
        ax.spines[sisi].set_visible(False)
    ax.legend(ncol=6, fontsize=6.6, frameon=False, loc='upper center',
              bbox_to_anchor=(0.46, -0.32 if len(kode) >= 3 else -0.45),
              handlelength=1.5, columnspacing=0.9, handletextpad=0.4)
    fig.tight_layout()

    keluar = direktori / 'ringkasan'
    for ekstensi in ('png', 'svg', 'pdf'):
        fig.savefig(keluar / f'fig_adaptation_time.{ekstensi}', dpi=300, bbox_inches='tight')
    print(f'Grafik ditulis ke {keluar.relative_to(ROOT)}/fig_adaptation_time.{{png,svg,pdf}}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
