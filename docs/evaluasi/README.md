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

**Temuan evaluasi 20260922T150904.** Enam dari 60 run perlakuan tidak tercatat selesai. Lima
di antaranya (A002 run 9; A003 run 1, 3, 8, 17) disebabkan cacat implementasi: Knowledge
meng-commit setelah respons, sehingga `/finish` kadang mendahului commit `/sync` dan ditolak
409, lalu Executor meninggalkan eksekusi berstatus `running` (ADR-0008 dan ADR-0020, revisi
F6). Adaptasi pada kelima run itu sebenarnya selesai sampai sinkronisasi. Satu run (A003 run
12) tidak menghasilkan event karena monitor MySQL tidak mencatat penggantian nama pemulihan
maupun perlakuan; penyebabnya diuji dengan `experiments/f6/uji_monitor_mysql.py`. Karena hasil
tersebut diperoleh dengan kode yang cacat, evaluasi perlakuan dijalankan ulang setelah perbaikan.

**Uji monitor MySQL (3 ulangan per jeda).** Kolom diganti nama lalu dikembalikan setelah jeda W:

| Jeda W (s) | Perubahan tercatat | Siklus dengan keduanya hilang | Latensi deteksi, median (min–maks) s |
|---:|---:|---:|---:|
| 2 | 0/6 | 3/3 | – |
| 5 | 4/6 | 1/3 | 4,8 (2,3–7,4) |
| 12 | 6/6 | 0/3 | 5,1 (0,1–7,2) |
| 25 | 6/6 | 0/3 | 4,8 (−0,1–9,7) |

Perubahan yang dikembalikan sebelum pemindaian berikutnya (interval 10 s) tidak terlihat, dan
selalu hilang berpasangan. Dalam operasi, skema akhir tetap sejalan dengan OBDF sehingga yang
terlewat hanya inkonsistensi sesaat. Dalam evaluasi, OBDF direset di luar siklus MAPE-K,
sehingga pemulihan yang tidak terdeteksi membuat cuplikan monitor dan OBDF tidak lagi sejalan.
Harness kini mewajibkan event pemulihan tiba (batas 60 s) sebelum DDL perlakuan, dan
menghentikan evaluasi bila tidak.

**Koreksi (evaluasi 20260924T072348).** Pemeriksaan itu semula tidak pernah aktif untuk A003:
klien MySQL tidak mencetak apa pun saat `ALTER` berhasil, dan harness memperlakukan keluaran
kosong sama dengan `'bersih'` (tidak ada perubahan). Akibatnya DDL perlakuan dijalankan sekitar
9–10 s setelah pemulihan, yaitu selama reset OBDF saja, sehingga keduanya dapat jatuh dalam satu
jendela pemindaian dan saling meniadakan. Ini menjelaskan A003 run 12 pada evaluasi sebelumnya
dan A003 run 1 dan 2 pada evaluasi ini (kueri `tanggal_lahir` sebelum DDL mengembalikan HTTP 200,
DDL perlakuan berhasil, tetapi `ddl_event_log` tidak memuat baris apa pun antara 00:30:06 dan
00:58:46). Kontrak pemulihan kini eksplisit: `'bersih'` hanya berarti tidak ada DDL, dan setiap
jalur yang menjalankan DDL mengembalikan penanda `dipulihkan: …`. Swa-uji memeriksa kedua arah.


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

## Rancangan 3 x 2 (operator x DBMS)

Setiap operator diuji pada kedua sumber agar pengaruh operator tidak tercampur dengan
mekanisme deteksi DBMS (event trigger PostgreSQL versus polling MySQL):

| | PostgreSQL | MySQL |
|---|---|---|
| ADD | A001 `penerima_manfaat.email` | A004 `master_wilayah.kode_pos` |
| DROP | A002 `program_bansos.tipe_program` (proyeksi eksplisit) | A005 `master_penduduk.status_hidup` (`SELECT *`) |
| RENAME | A006 `program_bansos.nama_program` (proyeksi eksplisit) | A003 `master_penduduk.tanggal_lahir` (`SELECT *`) |

Penilaian mengikuti pola skenario (P-001, P-002, P-003), bukan kodenya, sehingga identik di
kedua DBMS. A005 tidak memiliki kueri kontrol lintas sumber karena satu-satunya kueri lintas
sumber pada mapping menyentuh `master_penduduk`; kontrolnya memakai `master_keluarga`.

## Konektor tanpa pemutaran ulang (snapshot.mode = no_data)

**Temuan 2026-09-30.** Setelah lingkungan dinyalakan ulang, konektor MySQL gagal dengan galat
1236 karena posisi binlog yang diingatnya (`mysql-bin.000003`) sudah tidak ada di server, yang
kini hanya menyimpan `mysql-bin.000006` dan `000007`. Pada saat yang sama, konektor PostgreSQL,
yang memakai mode bawaan `initial`, kehilangan offset dan membaca ulang seluruh
`ddl_event_log`; Knowledge yang baru direset memproses DDL lama itu (event 149–153) dan
mengadaptasi OBDF ke versi 2 terhadap riwayat yang sudah basi.

**Keputusan.** Kedua konektor memakai `snapshot.mode = no_data`: skema tabel direkam, tetapi
baris lama tidak diterbitkan, sehingga hanya DDL setelah pendaftaran yang sampai ke ASCAM.
Konektor PostgreSQL tetap memakai `publication.autocreate.mode = filtered`.
`experiments/reset_konektor.py` memulihkan keadaan yang sudah rusak: menghentikan konektor,
menghapus offset-nya, menghapus topik schema history MySQL, serta replication slot dan
publikasi PostgreSQL, tanpa mengubah data sumber maupun isi `ddl_event_log`.

## Kontaminasi antarskenario pada tabel bersama

**Temuan evaluasi 20260930T091536 (tidak sahih).** Pada rancangan 3 x 2, A002 dan A006 memakai
`program_bansos`, sedangkan A003 dan A005 memakai `master_penduduk`. Setiap run hanya memulihkan
kolom skenarionya sendiri, sehingga sisa perubahan skenario lain terbawa: setelah B006,
`nama_program` bernama `judul_program`, sehingga kueri tetangga A002 gagal di 20 dari 20 run;
setelah A002, `tipe_program` hilang, sehingga kueri tetangga A006 dan B006 gagal. Kegagalan itu
sudah terjadi SEBELUM DDL perlakuan, jadi bukan akibat adaptasi. Waktu A002 juga tercemar
(IQR 22,6–30,0 s, maksimum 39,1 s).

**Keputusan.** Persiapan setiap run memulihkan kolom semua skenario (`pulihkan_semua`), masing-
masing menunggu event pemulihannya sendiri, dan mencatat skenario yang dipulihkan
(`skenario_dipulihkan`). `analisis.py` melaporkan run yang kuerinya sudah gagal sebelum DDL
(bagian 8), karena kondisi awal setiap run harus bersih. Direktori hasil yang tercemar disimpan
sebagai jejak, tetapi tidak dipakai untuk paper.

## Skenario view (A007–A009, ADR-0023)

Studi kasus diberi model virtual `layanan` berisi tiga view yang dibaca TriplesMap baru.
Kolomnya dipilih yang **tidak disentuh A001–A006** dan tidak dibaca TriplesMap lain, sehingga
dampak yang teramati hanya berasal dari jalur lewat view dan enam skenario lama tidak berubah.
Property yang dipakai (`noKartuKeluarga`, `kolomDiubah`, `diubahOleh`) sudah ada di ontologi tetapi
belum dipakai mapping; hanya `tahunBerakhir` dan kelas `PenerimaAktif` yang ditambahkan. Foreign
table `riwayat_perubahan_data` serta kolom `periode_mulai`/`periode_selesai` ditambahkan ke VDB
karena ada di sumber (dan dipantau monitor) tetapi sebelumnya tidak dideklarasikan.

| Skenario | View | Perubahan | DBMS | Keputusan yang diharapkan |
|---|---|---|---|---|
| A007 | `v_penerima_aktif`: `SELECT penerima_id, no_kartu_keluarga … WHERE aktif = TRUE` | `DROP COLUMN no_kartu_keluarga` | PostgreSQL | otomatis: proyeksi view dikecilkan, proyeksi eksplisit mapping ditulis ulang |
| A008 | `v_riwayat_perubahan`: `SELECT riwayat_id, kolom_diubah, diubah_oleh AS petugas …` | `DROP COLUMN diubah_oleh` | MySQL | otomatis: kolom beralias dibuang dari view; mapping `SELECT *` atas view |
| A009 | `v_program_berakhir`: `SELECT program_id, YEAR(periode_selesai) AS tahun_berakhir …` | `DROP COLUMN periode_selesai` | PostgreSQL | HITL dengan alasan "dipakai dalam ekspresi … layanan.v_program_berakhir" |

A009 adalah kontrol negatif. Rencananya tidak lengkap (definisi view harus dibuat ulang manusia),
sehingga evaluator **menolak** rencana alih-alih menyetujuinya; harness mencatat bahwa tidak ada
eksekusi, versi OBDF tidak berubah, dan kueri terdampak tetap gagal seperti baseline. Keputusan
dinilai sesuai hanya bila alasan HITL memuat teks yang dirancang (`alasan_memuat`), bukan sekadar
`decision = hitl`. Pada data contoh, kesepuluh baris `penerima_manfaat` bernilai `aktif = TRUE`,
sehingga `WHERE aktif = TRUE` pada A007 tidak menyaring baris; klausa itu ada untuk memastikan
kolom WHERE view tetap diperlakukan sebagai HITL (diuji di Knowledge, bukan di evaluasi).

**Prasyarat: uji kelayakan F0.8.** Reference Guide Teiid menyatakan `ALTER VIEW` tidak boleh
mengubah informasi kolom (teiid-documents, *Schema object DDL*, hlm. 356–357). Sebelum evaluasi,
`./experiments/f0/f0_8.sh` memeriksa pada VDB uji terpisah apakah Teiid 16 menerima
`ALTER VIEW` yang mengecilkan proyeksi, dan bila tidak, apakah `DROP VIEW` lalu `CREATE VIEW`
diterima. Hasilnya menentukan `ASCAM_EXEC_VIEW_STATEMENT` (`alter` atau `recreate`) pada Executor.

**Temuan uji coba 2026-10-09 (sebelum evaluasi).** Pada kondisi dasar, ketiga kueri lewat view
gagal (HTTP 500) walaupun view terbaca langsung lewat Teiid:

1. *A007, A009*: `TEIID30088 Unrelated order by column (… || CAST(v1.penerima_id AS STRING))
   cannot be used in … SELECT DISTINCT`. View yang kolomnya diturunkan dari kueri tidak memiliki
   kunci, sehingga Ontop 4.1.1 tidak dapat menjamin tidak ada tripel ganda dan menambahkan
   `SELECT DISTINCT`; `ORDER BY ?p` pada SPARQL diterjemahkan menjadi `ORDER BY` atas ekspresi IRI
   yang tidak diproyeksikan, dan Teiid menolaknya. Kunci pada view Teiid hanya dapat dideklarasikan
   bersama daftar kolom inline, padahal view berkolom inline tidak dapat dikecilkan dengan
   `ALTER VIEW` (F0.8 v7). Untuk evaluasi, kueri lewat view ditulis tanpa `ORDER BY`; urutan tidak
   memengaruhi penilaian karena answer set diurutkan oleh harness. Ini keterbatasan kombinasi
   Ontop–Teiid (bukan ASCAM) yang dicatat sebagai ancaman validitas.
2. *Rancangan awal A008* memetakan kolom `DATETIME` sebagai `xsd:dateTime`; reformulasi Ontop
   gagal dengan `UnsupportedOperationException: Not yet supported by Teiid`, yaitu adaptor Teiid
   pada Ontop 4.1.1 belum mendukung konversi itu. A008 dipindah ke `riwayat_perubahan_data.diubah_oleh`
   (`VARCHAR`), yang memang sudah memiliki kelas dan property di ontologi.

**Ketersediaan endpoint.** Setiap run perlakuan kini menjalankan probe berurutan setiap ±0,5 s
dari DDL sampai eksekusi selesai, dengan kueri yang tidak disentuh skenario mana pun
(`TransaksiBansos`). Dicatat persentase probe berhasil dan selang gagal terpanjang (dari probe
gagal pertama sampai probe berhasil berikutnya), untuk menguji klaim ADR-0022 bahwa adaptasi
tidak memutus endpoint.
