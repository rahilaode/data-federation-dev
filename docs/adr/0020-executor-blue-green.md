# ADR-0020: Mesin eksekusi rencana adaptasi

- **Status:** Diterima
- **Tanggal:** 2026-09-18
- **Fase MAPE-K:** Execute
- **Terkait:** ADR-0004, ADR-0015, ADR-0016, ADR-0018, ADR-0019

## Konteks

Rencana adaptasi yang berstatus `approved` harus diterapkan ke OBDF tanpa membuat layanan
berhenti bila hasilnya ternyata salah, dan harus dapat dikembalikan bila verifikasi gagal.

## Keputusan

1. **Urutan eksekusi** mengikuti ADR-0004: deploy Σ′_S sebagai **versi VDB baru** → tulis dan
   validasi ℳ′ dan 𝒯′ → pindahkan koneksi → muat ulang Ontop → verifikasi → (bila perlu) revert.
2. **Titik aman sebelum pemindahan koneksi.** Selama VDB versi baru belum diberi
   `connection type ANY`, versi lama tetap melayani seluruh kueri. Kegagalan pada tahap deploy
   atau validasi hanya menghapus versi baru dan memulihkan artefak dari cadangan agen; pengguna
   OBDF tidak terpengaruh.
3. **Validasi terhadap versi baru.** `ontop validate` dijalankan dengan
   `jdbc:teiid:<vdb>@mm://host:31000;version=<N+1>`, sehingga ℳ′ diuji terhadap Σ′_S sebelum
   koneksi dipindahkan (kelayakannya dibuktikan pada F0.5).
4. **Verifikasi memakai sidik jari graf** (jumlah triple per predikat), dengan harapan berbeda
   per pola: `P-002` mengharuskan predikat sasaran hilang dan predikat lain tetap; `P-003`
   mengharuskan jawaban identik; `P-001` mengharuskan tidak ada predikat yang hilang, karena
   kolom baru boleh belum berisi data.
5. **Revert** mengembalikan `connection type` (versi baru `NONE`, versi lama `ANY`), memulihkan
   artefak dari cadangan, dan memuat ulang Ontop. Eksekusi ditandai `rolled_back`.
6. **Setelah berhasil**, versi VDB lama diberi `NONE` dan Knowledge disinkronkan, sehingga versi
   spesifikasi baru terbentuk dari keadaan OBDF yang sebenarnya (bukan dari asumsi Executor).
7. **Setiap langkah dilaporkan ke Knowledge** beserta durasinya, sehingga Δt_adapt dapat
   diuraikan per langkah dan eksekusi yang terhenti tetap terlihat.
8. **Rencana kedaluwarsa ditolak** sebelum apa pun disentuh: versi dasar rencana harus sama
   dengan versi aktif.
9. **Rencana yang versi dasarnya usang ditandai `superseded`**, bukan dibiarkan `approved`,
   agar tidak dicoba ulang setiap siklus (temuan evaluasi F6).
10. **Jeda dan lanjut** (`/control/pause`, `/control/resume`) menghentikan pengambilan rencana
    baru tanpa memutus eksekusi yang sedang berjalan; dipakai prosedur eksperimen.
11. **Rahasia tidak diambil dari Knowledge.** Alamat target berasal dari Knowledge, sedangkan
   kata sandi ManagementRealm dan token agen berasal dari Docker secret milik Executor sendiri,
   karena Knowledge tidak pernah mengembalikan rahasia lewat API (ADR-0008).

## Bukti

38 uji pada Executor (23 penyunting + 15 mesin dan pekerja): rencana DROP berjalan penuh dengan
enam langkah berurutan, VDB versi baru ter-deploy di samping versi lama, koneksi berpindah
`(2, ANY)` lalu `(1, NONE)`, validasi memakai URL berversi, dan sync dipanggil sekali; rencana
ADD memetakan tipe sumber ke tipe Teiid dari Knowledge serta menulis property dan pemetaan;
VDB `FAILED` menghentikan eksekusi sebelum artefak tersentuh dan membersihkan versi gagal;
validasi yang ditolak memulihkan kedua artefak dan menghapus versi baru; verifikasi yang gagal
memicu revert lengkap beserta muat ulang kedua; aturan verifikasi per pola diuji; kegagalan muat
ulang dilaporkan; rencana kedaluwarsa ditolak tanpa perubahan apa pun; konflik sidik jari saat
menulis artefak ditangani sebagai kegagalan; pekerja menjalankan rencana berurutan, tetap hidup
setelah kegagalan, dan mencatat pencacahnya.

## Konsekuensi

- OBDF tetap melayani selama penyiapan; jendela tidak melayani hanya selama muat ulang Ontop
  (±10 detik menurut F4a).
- Versi VDB lama tetap ter-deploy dengan `connection type NONE` sehingga revert cepat; pemangkasan
  versi lama dicatat sebagai pekerjaan lanjutan.
- Verifikasi berbasis sidik jari graf bersifat menyeluruh tetapi kasar; kueri regresi khusus per
  skenario dapat ditambahkan pada fase evaluasi.

## Ketahanan terhadap restart Teiid (F5)

Setelah adaptasi ADD pertama lewat konsol, VDB hasil adaptasi tampak "hilang". Penelusuran
(`experiments/f5/periksa_vdb.py`, garis waktu kontainer dan cadangan agen) menunjukkan bahwa
adaptasi sebenarnya berhasil, lalu seluruh lingkungan dibangun ulang **bersama volumenya**,
sehingga Teiid kembali hanya berisi versi 1 dari berkas di disk. Dua hal ditetapkan:

1. **Konten deployment runtime disimpan di volume.** WildFly menyimpan isi deployment yang
   diunggah lewat management API di `standalone/data`, sedangkan hanya `standalone/configuration`
   yang semula memakai volume. Volume `teiid-standalone-data` ditambahkan agar VDB hasil
   adaptasi tidak hilang ketika kontainer dibuat ulang tanpa menghapus volume.
2. **Connection type bertahan setelah restart.** Dokumentasi Teiid menyebut connection type
   sebagai properti yang dapat diubah lewat AdminAPI (teiid-documents.pdf, *VDB Versioning*,
   hlm. 62-63) tetapi tidak menyebut apakah perubahannya bertahan. Hipotesis bahwa perubahan itu
   hilang saat restart diuji (`experiments/f5/uji_ketahanan.py`) dan **tidak terbukti**: setelah
   `docker restart`, versi 1 tetap `NONE`, versi 2 tetap `ANY`, dan koneksi tanpa versi tetap
   melihat kolom hasil adaptasi.

Berkas `government-vdb.xml` di disk sengaja tidak diubah Executor: versi hasil adaptasi hidup di
Teiid (dan isinya tersimpan di Knowledge per versi spesifikasi). Membangun ulang lab dengan
`run.sh` mengembalikan seluruh OBDF, termasuk artefak mapping dan ontologi, ke kondisi dasar.
