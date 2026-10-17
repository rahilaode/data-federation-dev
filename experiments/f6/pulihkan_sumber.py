"""
Mengembalikan kolom sumber SEMUA skenario F6 ke kondisi dasar, tanpa menjalankan evaluasi.

Dipakai bila run sebelumnya berhenti di tengah (misalnya terputus sebelum blok `finally`
evaluate.py) sehingga sumber tertinggal dalam keadaan sesudah DDL: kolom hasil ADD masih ada,
atau kolom hasil DROP/RENAME belum kembali. Pemeriksaan awal lalu menolak evaluasi karena
kueri dasar view (A007-A009) gagal.

Pemulihan menghasilkan DDL yang ikut ditangkap monitor. Karena itu jalankan ./jalankan.sh
SESUDAH skrip ini: Knowledge direset dan Orchestrator mulai dari pesan terbaru, sehingga DDL
pemulihan tidak diperlakukan sebagai perubahan baru.

Pemakaian (dari root repository):
  python3 experiments/f6/pulihkan_sumber.py
  ./jalankan.sh
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from skenario import SKENARIO  # noqa: E402


def main() -> int:
    for kode, s in SKENARIO.items():
        hasil = s.pulihkan()
        print(f'{kode}: {hasil}')
        if s.siapkan:
            # membuat tabel cadangan bila belum ada (hanya bila kolomnya sudah kembali)
            print(f'{kode}: cadangan {s.siapkan() or "ada"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
