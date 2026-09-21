# Protokol evaluasi ASCAM

Dokumen ini menjelaskan cara evaluasi dijalankan, apa yang diukur, dan ancaman terhadap
validitas hasilnya. Semua alat berada di `experiments/f6/`.

## Skenario

| Kode | Perubahan pada sumber | DBMS | Pola | Keputusan D11 yang diharapkan | Jawaban SPARQL yang diharapkan |
|---|---|---|---|---|---|
| A001 | `ADD COLUMN email` pada `penerima_manfaat` | PostgreSQL | P-001 | otomatis | identik (kolom baru belum berisi data) |
| A002 | `DROP COLUMN tipe_program` pada `program_bansos` | PostgreSQL | P-002 | otomatis | berubah (predikat `tipeProgram` hilang) |
| A003 | `RENAME COLUMN tanggal_lahir` pada `master_penduduk` | MySQL | P-003 | otomatis | identik (diserap `NAMEINSOURCE`) |

## Prosedur satu run

1. Executor dijeda (`POST /control/pause`) dan ditunggu sampai tidak ada eksekusi berjalan.
2. Kondisi sumber dipulihkan (kolom dikembalikan, data kolom yang dihapus disalin kembali).
3. Event yang timbul dari pemulihan ditunggu sampai tiba, lalu rencana yang terbentuk darinya
   ditandai `superseded` (lihat ancaman validitas 1).
4. OBDF direset (`experiments/reset_obdf.py`): artefak OBDA dari git, VDB kembali ke versi 1,
   Ontop dimuat ulang, Knowledge disinkronkan.
5. Cuplikan jawaban dasar diambil dari tiga kueri tetap, diulang bila endpoint belum menjawab.
6. Executor dilanjutkan, lalu perubahan skema skenario diterapkan pada sumber (t₀).
7. Dicatat: waktu event diterima Knowledge, keputusan D11, dan eksekusi hingga selesai.
8. Cuplikan jawaban sesudah diambil dan dibandingkan dengan cuplikan dasar.

Pemeriksaan awal (`experiments/f6/preflight.py`) dijalankan sebelum run pertama: seluruh
kontainer, konektor Debezium, layanan ASCAM, dan satu uji rantai DDL nyata.

## Metrik

| Metrik | Definisi |
|---|---|
| Keberhasilan | Eksekusi berakhir `succeeded` |
| Ketepatan D11 | Keputusan dan pola sama dengan harapan skenario |
| Kesetaraan jawaban | Cuplikan sesudah sama/berbeda sesuai harapan; run yang salah satu cuplikannya gagal diambil dicatat **tidak dapat dibandingkan**, bukan berbeda |
| Deteksi | t₀ sampai event diterima Knowledge (monitor, Debezium, Kafka, Orchestrator) |
| Kerja adaptasi | Jumlah durasi langkah `deploy_vdb`, `validate`, `switch`, `reload_ontop`, `verify`, `sync` |
| Jeda penjadwalan | Ujung ke ujung dikurangi deteksi dan kerja adaptasi; terutama interval polling Executor (5 detik) |
| Ujung ke ujung | t₀ sampai eksekusi selesai |

Kerja adaptasi adalah sifat sistem, sedangkan jeda penjadwalan adalah parameter konfigurasi;
keduanya dilaporkan terpisah.

## Ancaman terhadap validitas

1. **Langkah pemulihan adalah perubahan skema.** Mengembalikan kolom di antara run dideteksi
   ASCAM sebagai perubahan baru dan, tanpa pengendalian, dieksekusi otomatis — termasuk
   memuat ulang Ontop tepat saat cuplikan dasar diambil. Pada evaluasi pertama hal ini membuat
   3 dari 20 run A003 gagal mengambil cuplikan dasar (galat koneksi), sehingga salah
   diklasifikasikan sebagai "tidak identik". Dampaknya paling besar pada MySQL karena waktu
   deteksinya bervariasi 1–10 detik. Diatasi dengan menjeda Executor dan menetralkan rencana
   dari langkah pemulihan (prosedur langkah 1–3).
2. **Interval polling monitor MySQL** membuat waktu deteksi A003 jauh lebih bervariasi daripada
   PostgreSQL; perbandingan antar-DBMS harus mempertimbangkan mekanisme monitor, bukan hanya
   ASCAM.
3. **Satu mesin pengujian (WSL2, Docker Desktop).** Waktu absolut bergantung perangkat keras;
   yang dapat digeneralisasi adalah proporsi antarlangkah dan perbandingan antarskenario.
4. **Data uji kecil.** Waktu `validate` dan `reload_ontop` akan bertambah seiring ukuran ℳ dan 𝒯,
   bukan ukuran data; waktu kueri verifikasi bertambah seiring ukuran data.
5. **Reset infrastruktur mengubah identitas pesan.** Menghentikan seluruh kontainer dengan
   `down -v` membuat ulang topik Kafka dan basis data sumber. Kunci idempotensi yang semula
   berbasis offset Kafka bertabrakan dengan event lama, sehingga DDL baru diperlakukan sebagai
   duplikat tanpa jejak. Diperbaiki dengan menurunkan kunci dari DDL itu sendiri (ADR-0017).
   Evaluasi harus dijalankan pada infrastruktur yang tidak dibuat ulang di tengah jalan, dan
   pemeriksaan awal wajib dijalankan setelah setiap reset infrastruktur.
6. **Kueri regresi terbatas** (tiga kueri tetap): kesetaraan jawaban diukur pada cakupan kueri
   tersebut, bukan pada seluruh kemungkinan kueri.
