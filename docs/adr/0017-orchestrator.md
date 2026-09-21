# ADR-0017: Orchestrator sebagai penormal event, bukan pengambil keputusan

- **Status:** Diterima
- **Tanggal:** 2026-09-18
- **Fase MAPE-K:** Monitor dan Analyze (jembatan)
- **Terkait:** D1, D7, ADR-0015, ADR-0016

## Konteks

Monitor skema di setiap sumber menulis baris `ddl_event_log`, yang dikirim Debezium ke Kafka.
Bentuk pesannya berbeda antar-sumber dan tidak seluruhnya sesuai kebutuhan analisis.

Bukti dari pemeriksaan pesan nyata (F3-probe) dan dari definisi monitor:

| Temuan | Konsekuensi |
|---|---|
| PostgreSQL memakai `schema_name`, MySQL memakai `db_name` | Normalisasi harus menyatukan keduanya |
| RENAME hanya menyimpan nama kolom **lama** | Nama baru harus diambil dari teks `ddl_command` |
| Satu baris log dapat memuat **beberapa pernyataan** DDL sekaligus | Satu pesan dapat menghasilkan beberapa event |
| Jenis ALTER lain ikut tertangkap | Perlu jalur khusus agar tetap terlihat, bukan dibuang |

## Keputusan

1. **Orchestrator hanya menormalkan dan meneruskan.** Analisis dampak dan keputusan adaptasi
   tetap di Knowledge (ADR-0015, ADR-0016), sehingga aturan hanya ada di satu tempat.
2. **`ddl_command` diurai dengan sqlglot** memakai dialek sesuai DBMS sumber. Setiap pernyataan
   kolom menjadi satu event. Pernyataan yang tidak didukung dikirim sebagai operasi `other`
   sehingga muncul di antrean HITL. Bila penguraian gagal, event dibentuk dari baris log apa
   adanya dan RENAME tanpa nama baru akan dieskalasi Knowledge.
3. **`event_uid` deterministik yang diturunkan dari DDL itu sendiri**: UUID versi 5 dari nama
   sumber, `id` baris log monitor, waktu tangkap (`captured_at`), dan indeks pernyataan.
   Pengiriman ulang tidak menghasilkan rencana ganda. *Revisi F6:* rancangan awal memakai
   topik, partisi, dan offset Kafka. Ketika seluruh kontainer dinyalakan ulang dengan
   `down -v`, topik dibuat ulang dan offset kembali ke 0, sehingga DDL baru mendapat uid yang
   sama dengan event lama dan diperlakukan sebagai duplikat — tanpa galat maupun jejak. Offset
   hanya dipakai sebagai cadangan bila baris log tidak memuat identitas. Knowledge kini menandai
   uid yang sama dengan isi berbeda sebagai `conflict` dan mencatatnya di audit.
4. **Offset di-commit hanya setelah event terkirim** (at-least-once). Bila Knowledge tidak dapat
   dihubungi, konsumen mundur ke offset pesan yang gagal dan mencoba lagi.
5. **Konfigurasi runtime berasal dari Knowledge**: nama OBDF, daftar sumber beserta topiknya, dan
   alamat broker Kafka. Tidak ada duplikasi konfigurasi di Orchestrator.
6. **Penyaringan relevansi dilakukan Knowledge**, bukan Orchestrator. Penanda `is_regulated` dari
   sumber tetap disimpan pada `raw`, tetapi keputusan relevansi memakai registri Knowledge agar
   konsisten dengan sisa sistem.
7. **Penyiapan diulang sampai berhasil.** Knowledge dapat sedang restart ketika Orchestrator
   start; penyiapan yang hanya dicoba sekali membuat pekerja mati permanen meski Knowledge
   kemudian sehat (ditemukan pada F6). Hal yang sama berlaku untuk Executor.
8. **Pesan yang tidak menjadi event dicatat beserta alasannya** (`skipped`, `last_skipped`,
   dan peringatan di log), bukan dibuang tanpa jejak. Offset tetap maju karena pesan semacam
   itu bukan kegagalan yang dapat diulang. Temuan F6: setelah seluruh kontainer dinyalakan
   ulang, Orchestrator menerima pesan tetapi tidak menghasilkan event dan tidak meninggalkan
   jejak apa pun.
9. **`/health`** menampilkan status pekerja, topik, dan pencacah (pesan, event terkirim, rencana,
   diabaikan, duplikat, kegagalan) untuk healthcheck dan dasbor UI.

## Bukti

30 uji: ADD, DROP, dan RENAME dari pesan PostgreSQL; baris MySQL dengan `db_name` dan dialeknya;
DDL multi-pernyataan menghasilkan beberapa event dengan uid berbeda; pernyataan tak didukung
menjadi `other` beserta teksnya; DDL tak terurai jatuh ke baris log; uid deterministik, berversi 5, tetap berbeda
untuk DDL berbeda meski offset sama setelah topik dibuat ulang, dan tetap sama untuk DDL yang
dikirim ulang dari offset lain; tabrakan uid dicacah; pesan bukan INSERT, tanpa `after`, atau rusak diabaikan; envelope Debezium dengan
pembungkus `schema` tetap terbaca; pengiriman berhasil menaikkan offset; kegagalan tidak
menaikkan offset dan memundurkan konsumen ke pesan yang gagal; topik asing dilewati; sumber tanpa
topik ditolak saat penyiapan; pemilihan token per klien dari berkas token bersama; penyiapan
yang gagal diulang sampai Knowledge tersedia dan pesan tetap diproses setelah pulih; `/health`
melaporkan statistik.

## Konsekuensi

- Menambah satu komponen yang harus berjalan, tetapi menjaga Knowledge sebagai satu-satunya
  pemilik aturan dan data.
- Ketergantungan pada sqlglot untuk membaca DDL sumber; kegagalan penguraian tidak menghentikan
  alur karena ada jalur cadangan.
- Keterbatasan monitor PostgreSQL (mengambil teks DDL dari sesi aktif, sehingga DDL
  multi-pernyataan hanya menghasilkan satu baris log) kini tertangani di sisi normalisasi;
  perbaikan di sisi monitor dicatat sebagai pekerjaan lanjutan.
