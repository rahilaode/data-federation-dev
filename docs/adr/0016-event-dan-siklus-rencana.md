# ADR-0016: Penerimaan event skema dan siklus hidup rencana adaptasi

- **Status:** Diterima
- **Tanggal:** 2026-09-18
- **Fase MAPE-K:** Monitor (penerimaan), Analyze, Plan
- **Terkait:** D7, D11, ADR-0012, ADR-0015

## Konteks

Event perubahan skema datang dari monitor di setiap sumber lewat Kafka. Event tersebut harus
tercatat, dianalisis, dan menjadi rencana adaptasi yang dapat dieksekusi atau menunggu
persetujuan administrator. Semua itu harus tahan terhadap pesan ganda dan terhadap perubahan
spesifikasi yang terjadi di sela-sela proses.

## Keputusan

1. **Titik masuk tunggal**: `POST /api/v1/obdf/{id}/events` pada Knowledge Service. Orchestrator
   hanya menormalkan pesan Kafka menjadi event terformalisasi lalu mengirimkannya; aturan
   keputusan tetap di satu tempat (ADR-0015).
2. **Idempotensi berdasarkan `event_uid`.** Pesan yang sama dikirim ulang mengembalikan event dan
   rencana yang sudah ada, ditandai `duplicate`, tanpa membuat rencana kedua.
3. **Event memuat nama objek di SUMBER**, bukan nama objek Teiid. Pemetaan ke Σ_S memakai
   `source_schema`/`source_column` (ADR-0013), sehingga kolom yang namanya berbeda akibat
   `NAMEINSOURCE` tetap ditemukan.
4. **Siklus hidup rencana**: `auto` → langsung `approved`; `hitl` → `pending_approval` disertai
   notifikasi, lalu administrator menyetujui atau menolak. Keputusan mencatat aktor dan waktunya,
   dan notifikasi terkait ditandai sudah dibaca.
5. **Optimistic concurrency**: setiap rencana menyimpan versi spesifikasi dasarnya. Bila
   spesifikasi berubah sebelum rencana disetujui, persetujuan ditolak (HTTP 409) dan rencana
   ditandai `superseded`; penandaan itu di-commit terpisah agar tidak ikut hilang saat transaksi
   permintaan di-rollback.
6. **Event yang tidak relevan** (sumber tak terdaftar, tabel atau kolom tak difederasikan) dicatat
   berstatus `ignored` beserta alasannya, bukan dibuang diam-diam.

## Bukti

10 uji event (total 97 pada Knowledge Service): event otomatis menghasilkan rencana `approved`
beserta urutan tindakannya; pengiriman ulang bersifat idempoten; event HITL menunggu persetujuan,
dapat disetujui atau ditolak, dan keputusan kedua ditolak dengan 409; rencana yang kedaluwarsa
ditandai `superseded` dan tidak dapat disetujui; event yang tidak relevan tidak menghasilkan
rencana; event ADD menghasilkan rencana property dan mapping; riwayat event dan audit tercatat;
event yang memakai nama kolom Teiid (bukan nama kolom sumber) diabaikan — menegaskan keputusan
nomor 3.

## Konsekuensi

- Orchestrator menjadi komponen tipis: konsumsi Kafka, normalisasi, dan pengiriman event.
- Eksekusi rencana yang berstatus `approved` dilakukan Executor (F4); status `executed` dan
  `failed` diisi pada tahap tersebut.
- Bentuk pesan monitor per sumber perlu diperiksa langsung sebelum normalisasi ditulis
  (`experiments/f1/f3_probe_events.py`).
