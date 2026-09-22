# Protokol evaluasi ASCAM

Dokumen ini menjelaskan cara evaluasi dijalankan, apa yang diukur, dan ancaman terhadap
validitas hasilnya. Semua alat berada di `experiments/f6/`.

## Skenario

| Kode | Perubahan pada sumber | DBMS | Pola | Keputusan D11 yang diharapkan | Jawaban SPARQL yang diharapkan |
|---|---|---|---|---|---|
| A001 | `ADD COLUMN email` pada `penerima_manfaat` | PostgreSQL | P-001 | **HITL** (ADR-0021) | identik (kolom baru belum berisi data) |
| A002 | `DROP COLUMN tipe_program` pada `program_bansos` | PostgreSQL | P-002 | otomatis | berubah (predikat `tipeProgram` hilang) |
| A003 | `RENAME COLUMN tanggal_lahir` pada `master_penduduk` | MySQL | P-003 | otomatis | identik (diserap `NAMEINSOURCE`) |

## Mode evaluasi

Kedua mode memakai himpunan kueri Q_k dan prosedur pemulihan yang sama, sehingga perbedaan
perilaku sepenuhnya dapat diatributkan pada ASCAM (proposal §3.10.2, hlm. 104).

| Mode | Isi | Rujukan |
|---|---|---|
| Baseline B001–B003 | Executor dijeda sepanjang run; DDL diterapkan tanpa adaptasi; Q_k dijalankan sebelum dan sesudah; dicatat status HTTP, pesan galat, dan hasil | §3.10.1, hlm. 101–103 |
| Perlakuan A001–A003 | Siklus MAPE-K berjalan; A001 disetujui evaluator (ADR-0021); dicatat keputusan D11, Δt_adapt, dekomposisi langkah, PreservationRatio; seluruh pesan Kafka diaudit | §3.10.2–3.11 |

## Himpunan kueri Q_k

Setiap skenario memiliki kueri yang menyentuh kolom terdampak, kueri tetangga pada tabel yang
sama, dan kueri kontrol pada tabel lain (termasuk satu kueri lintas sumber). Definisinya ada di
`experiments/f6/kueri.py`.

| Skenario | Terdampak | Tetangga | Kontrol |
|---|---|---|---|
| A001 | `bansos:email` (setelah tiga baris diisi) | nama dan status ekonomi penerima | tautan penerima ke penduduk; jumlah per kelas; status transaksi |
| A002 | `bansos:tipeProgram` | nama dan nominal program | transaksi ke program; jumlah per kelas; status transaksi |
| A003 | `bansos:tanggalLahir` | nama dan pekerjaan penduduk | penerima ke nama penduduk (lintas sumber); jumlah per kelas; status transaksi |

## Prosedur satu run

1. Executor dijeda dan ditunggu sampai tidak ada eksekusi berjalan.
2. Kondisi sumber dipulihkan; event yang timbul dari pemulihan ditunggu, lalu rencananya
   ditandai `superseded` (ancaman validitas 1).
3. OBDF direset (`experiments/reset_obdf.py`).
4. Cuplikan dasar Q_k diambil (diulang bila endpoint belum menjawab).
5. **Baseline:** DDL diterapkan, jeda 15 detik, data diisi bila perlu, Q_k dijalankan sekali.
   **Perlakuan:** Executor dilanjutkan, DDL diterapkan (t_start), event dan rencana diamati,
   rencana HITL disetujui evaluator, eksekusi ditunggu sampai selesai, data diisi bila perlu,
   Q_k dijalankan.

## Metrik

| Metrik | Definisi | Rujukan |
|---|---|---|
| N_log, N_prod | Pesan Kafka yang membawa baris `ddl_event_log` dan baris tabel produksi, sejak run perlakuan pertama sampai terakhir. Nilai baris produksi tidak pernah disimpan di berkas hasil | pers. 3.13–3.14 |
| C_safe | N_prod = 0 ∧ N_log > 0, ditambah pemeriksaan `table.include.list` | pers. 3.15 |
| Δt_adapt | t_end − t_start; t_start saat DDL dieksekusi, t_end saat verifikasi SPARQL pasca-muat-ulang selesai (artefak termodifikasi dan siap dipakai) | pers. 3.16 |
| Δt_adapt mesin | Δt_adapt dikurangi jeda keputusan administrator (A001) | ADR-0021 |
| N_manual | Dipisah menjadi keputusan (A001 = 1) dan perbaikan artefak (selalu 0) | pers. 3.17–3.18, direvisi |
| ResultPreserved | 1 bila kedua eksekusi berhasil dan answer set identik (A003) | pers. 3.19 |
| ExecutionPreserved | 1 bila respons Ontop sesudah perubahan bukan galat (A001, A002) | pers. 3.20 |
| PreservationRatio | Rata-rata P_k(q) atas Q_k × 100 % | pers. 3.21 |

## Menjalankan

```bash
python3 experiments/f6/uji_harness.py                         # swa-uji tanpa Docker
python3 experiments/f6/evaluate.py --mode keduanya --ulangan 1 --ulangan-baseline 1   # uji coba
python3 experiments/f6/evaluate.py --mode keduanya --ulangan 20 --ulangan-baseline 5  # final
python3 experiments/f6/analisis.py > results/analisis-final.md
```

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
