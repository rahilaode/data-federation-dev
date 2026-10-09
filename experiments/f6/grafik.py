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
# dari bawah ke atas; pasangan PostgreSQL/MySQL tiap operator berdampingan, ADD paling atas
URUTAN = ['a008', 'a007', 'a003', 'a006', 'a005', 'a002', 'a004', 'a001']
# Penomoran paper: A001-A003 PostgreSQL, A004-A006 MySQL, sehingga harness a006 = A003 dan a003 = A006.
PAPER = {'a001': 'A001', 'a002': 'A002', 'a006': 'A003', 'a004': 'A004', 'a005': 'A005', 'a003': 'A006'}
LABEL = {'a001': 'ADD, PostgreSQL (A001)', 'a004': 'ADD, MySQL (A004)',
         'a002': 'DROP, PostgreSQL (A002)', 'a005': 'DROP, MySQL (A005)',
         'a006': 'RENAME, PostgreSQL (A003)', 'a003': 'RENAME, MySQL (A006)',
         'a007': 'DROP via view, PostgreSQL (A007)', 'a008': 'DROP via view, MySQL (A008)'}
# A009 tidak digambar: rencananya ditolak sehingga tidak memiliki Δt_adapt.

# (label legenda, kunci di ringkasan.json, warna isi, arsiran); label diawali "_" = tanpa legenda
# Strategi restart (ADR-0001): Ontop tunggal dimuat ulang sesudah peralihan VDB.
KOMPONEN_RESTART = [
    ('Detection', 'deteksi', '#ffffff', '////'),
    ('Ingestion and planning', 'jeda_turunan_median', '#e6e6e6', ''),
    ('Deploy / switch VDB', 'deploy_vdb', '#000000', ''),
    ('Validate', 'validate', '#a6a6a6', ''),
    ('_switch', 'switch', '#000000', ''),             # < 5 ms, digabung pada entri deploy/switch
    ('Restart Ontop', 'reload_ontop', '#595959', ''),
    ('Verify', 'verify', '#ffffff', '....'),
]
# Strategi blue-green (ADR-0022): instance siaga dinyalakan dan diverifikasi sebelum peralihan.
KOMPONEN_BLUEGREEN = [
    ('Detection', 'deteksi', '#ffffff', '////'),
    ('Ingestion and planning', 'jeda_turunan_median', '#e6e6e6', ''),
    ('Deploy VDB', 'deploy_vdb', '#000000', ''),
    ('Validate', 'validate', '#a6a6a6', ''),
    ('Start standby Ontop', 'start_ontop', '#595959', ''),
    ('Verify', 'verify', '#ffffff', '....'),
    ('Switch', 'switch', '#d9d9d9', 'xxxx'),
]


def komponen_untuk(ringkasan: dict, kode: list[str]) -> list[tuple]:
    bluegreen = any((ringkasan[k]['waktu_ms'].get('start_ontop') or {}) for k in kode)
    return KOMPONEN_BLUEGREEN if bluegreen else KOMPONEN_RESTART


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
    kode = [k for k in URUTAN if k in ringkasan and ringkasan[k].get('berhasil')
            and (ringkasan[k].get('waktu_ms') or {}).get('dt_adapt')]
    if not kode:
        sys.exit('tidak ada skenario perlakuan yang berhasil untuk digambar')

    rcParams.update({'font.family': 'serif',
                     'font.serif': ['Times New Roman', 'Liberation Serif', 'DejaVu Serif'],
                     'font.size': 8, 'axes.linewidth': 0.6, 'hatch.linewidth': 0.5})
    fig, ax = plt.subplots(figsize=(6.1, 0.35 + 0.53 * len(kode)), dpi=300)

    label_y = [LABEL[k] for k in kode]
    kiri = [0.0] * len(kode)
    for label, kunci, warna, arsir in komponen_untuk(ringkasan, kode):
        nilai = [max(median_detik(ringkasan[k]['waktu_ms'], kunci), 0.0) for k in kode]
        ax.barh(label_y, nilai, left=kiri, color=warna, edgecolor='black', linewidth=0.5,
                hatch=arsir, height=0.55, label=label)
        kiri = [a + b for a, b in zip(kiri, nilai)]

    # Tanpa garis interkuartil dan tanpa angka di batang: nilainya dicantumkan di keterangan gambar.
    batas_kanan = max(ringkasan[k]['waktu_ms']['dt_adapt']['median'] / 1000 for k in kode)
    ax.set_xlim(0, (int(batas_kanan / 5) + 1) * 5)
    ax.set_xlabel('Time from DDL execution (s)')
    ax.xaxis.grid(True, linewidth=0.3, color='#bbbbbb')
    ax.set_axisbelow(True)
    for sisi in ('top', 'right'):
        ax.spines[sisi].set_visible(False)
    ax.legend(ncol=4, fontsize=6.6, frameon=False, loc='upper center',
              bbox_to_anchor=(0.46, -0.62 / (0.53 * len(kode))),   # jarak tetap di bawah sumbu
              handlelength=1.5, columnspacing=0.9, handletextpad=0.4)
    fig.tight_layout()

    keluar = direktori / 'ringkasan'
    for ekstensi in ('png', 'svg', 'pdf'):
        fig.savefig(keluar / f'fig_adaptation_time.{ekstensi}', dpi=300, bbox_inches='tight')
    print('Nilai untuk keterangan gambar (median, detik):')
    for k in reversed(kode):
        w = ringkasan[k]['waktu_ms']
        d = lambda n: median_detik(w, n)
        print(f"  {PAPER.get(k, k)}: total {d('dt_adapt'):.1f}; detection {d('deteksi'):.1f}; "
              f"ingestion and planning {d('jeda_turunan_median'):.1f}; validate {d('validate'):.1f}; "
              f"restart Ontop {d('reload_ontop'):.1f}; start standby Ontop {d('start_ontop'):.1f}; "
              f"verify {d('verify'):.1f}; "
              f"deploy and switch {d('deploy_vdb') + d('switch'):.2f}")
    print(f'Grafik ditulis ke {keluar.relative_to(ROOT)}/fig_adaptation_time.{{png,svg,pdf}}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
