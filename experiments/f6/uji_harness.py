"""
Swa-uji harness evaluasi, tanpa Docker maupun OBDF.

Memastikan seluruh modul benar-benar dapat DIMUAT (bukan sekadar lolos kompilasi), setiap
skenario memiliki atribut lengkap, dan analisis berjalan ujung ke ujung atas hasil tiruan yang
dibentuk memakai fungsi penilaian yang sama dengan evaluasi sungguhan.

Jalankan dari root repository:  python3 experiments/f6/uji_harness.py
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

F6 = Path(__file__).resolve().parent
ROOT = F6.parents[1]
sys.path.insert(0, str(F6))


def main() -> int:
    import analisis  # noqa: F401
    import evaluate  # noqa: F401
    import kueri
    import skenario

    for kode, sk in skenario.SKENARIO.items():
        for atribut in ('sumber', 'tabel', 'kolom', 'pola', 'keputusan', 'terapkan', 'pulihkan'):
            assert getattr(sk, atribut), f'{kode}: atribut {atribut} kosong'
        assert kode in kueri.Q and len(kueri.Q[kode]) >= 3, f'{kode}: himpunan kueri kurang'
        jenis = {j for j, _ in kueri.Q[kode].values()}
        assert {'terdampak', 'tetangga', 'kontrol'} <= jenis, f'{kode}: jenis kueri tidak lengkap'
    print(f'modul termuat; {len(skenario.SKENARIO)} skenario lengkap dengan Q_k')

    # Pada kondisi dasar, pemulihan tidak boleh mengirim DDL dan harus mengembalikan 'bersih';
    # selain itu harness menunggu event pemulihan yang tidak akan pernah datang.
    kondisi_dasar = {'email': '0', 'tipe_program': '1', 'tgl_lahir_ktp': '0'}
    for kode, sk in skenario.SKENARIO.items():
        perintah: list[str] = []

        def palsu(sql, perintah=perintah):
            perintah.append(sql)
            if 'information_schema' in sql:
                return next(v for k, v in kondisi_dasar.items() if k in sql)
            return 'DIUBAH'
        asli = (skenario.pg, skenario.my)
        skenario.pg = skenario.my = palsu
        try:
            hasil = sk.pulihkan()
        finally:
            skenario.pg, skenario.my = asli
        ddl = [q for q in perintah if q.lstrip().upper().startswith(('ALTER', 'UPDATE'))]
        assert hasil == 'bersih' and not ddl, f'{kode}: pemulihan pada kondisi dasar -> {hasil!r} {ddl}'
    print('pemulihan pada kondisi dasar tidak mengirim DDL untuk setiap skenario')

    # Sebaliknya, setelah perlakuan pemulihan HARUS menjalankan DDL dan tidak boleh terbaca sebagai
    # "tidak ada perubahan", termasuk ketika klien (seperti MySQL) tidak mencetak apa pun.
    kondisi_perlakuan = {'email': '1', 'tipe_program': '0', 'tgl_lahir_ktp': '1'}
    for kode, sk in skenario.SKENARIO.items():
        perintah = []

        def palsu(sql, perintah=perintah):
            perintah.append(sql)
            if 'information_schema' in sql:
                return next(v for k, v in kondisi_perlakuan.items() if k in sql)
            return ''                                   # meniru ALTER yang berhasil tanpa keluaran
        asli = (skenario.pg, skenario.my)
        skenario.pg = skenario.my = palsu
        try:
            hasil = sk.pulihkan()
        finally:
            skenario.pg, skenario.my = asli
        ddl = [q for q in perintah if q.lstrip().upper().startswith('ALTER')]
        assert ddl and hasil not in ('bersih', ''), f'{kode}: pemulihan setelah perlakuan -> {hasil!r}'
    print('pemulihan setelah perlakuan selalu menjalankan DDL dan tidak terbaca sebagai bersih')

    konfigurasi = evaluate.periksa_konfigurasi_debezium()
    assert all(v['hanya_ddl_event_log'] for v in konfigurasi.values()), konfigurasi
    print('konfigurasi Debezium hanya memantau ddl_event_log')

    def ok(n, isi='x'):
        return {'status': 200, 'ok': True, 'galat': None, 'n': n,
                'hasil': [f'{isi}{i}' for i in range(n)], 'durasi_ms': 1}

    def gagal(pesan):
        return {'status': 500, 'ok': False, 'galat': pesan, 'n': None, 'hasil': None, 'durasi_ms': 1}

    sementara = Path(tempfile.mkdtemp(dir=ROOT / 'results'))
    try:
        for kode in skenario.SKENARIO:
            sebelum = {q: ok(0 if q == 'email' else 4) for q in kueri.Q[kode]}
            rusak = dict(sebelum)
            terdampak = next(q for q, (j, _) in kueri.Q[kode].items() if j == 'terdampak')
            if kode != 'a001':
                rusak[terdampak] = gagal('kolom tidak ada')
            (sementara / f'baseline-{kode}-01.json').write_text(json.dumps({
                'mode': 'baseline', 'skenario': kode, 'judul': kode, 'run': 1,
                'jawaban_sebelum': sebelum, 'jawaban_sesudah': rusak,
                'penilaian': kueri.nilai(kode, sebelum, rusak), 'artefak_tidak_berubah': True}))
            sesudah = dict(sebelum)
            if kode == 'a001':
                sesudah['email'] = ok(3, 'e')
            if kode == 'a002':
                sesudah['tipe_program'] = ok(0)
            penilaian = kueri.nilai(kode, sebelum, sesudah)
            harapan = kueri.sesuai_harapan(kode, sesudah, penilaian)
            assert all(harapan.values()), (kode, harapan)
            (sementara / f'run-{kode}-01.json').write_text(json.dumps({
                'mode': 'perlakuan', 'skenario': kode, 'judul': kode, 'run': 1, 'hasil': 'succeeded',
                'keputusan': skenario.SKENARIO[kode].keputusan, 'pola': skenario.SKENARIO[kode].pola,
                'sesuai_harapan': True, 'n_manual': 0, 'keputusan_ms': 0, 'deteksi_ms': 1,
                'dt_adapt_ms': 1, 'dt_adapt_mesin_ms': 1, 'langkah': {'validate': 1},
                'jawaban_sebelum': sebelum, 'jawaban_sesudah': sesudah, 'penilaian': penilaian,
                'harapan': harapan}))
        hasil = subprocess.run([sys.executable, str(F6 / 'analisis.py'), str(sementara)],
                               capture_output=True, text=True)
        assert hasil.returncode == 0, hasil.stderr
        for bagian in ('## 1.', '## 2.', '## 4.', '## 5.', '## 6.', '## 7.'):
            assert bagian in hasil.stdout, f'bagian {bagian} tidak muncul'
        print('analisis berjalan ujung ke ujung atas hasil tiruan; harapan teoretis terpenuhi')
    finally:
        shutil.rmtree(sementara, ignore_errors=True)
    print('\nSwa-uji harness LOLOS.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
