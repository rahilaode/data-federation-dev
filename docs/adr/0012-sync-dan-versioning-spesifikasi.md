# ADR-0012: Proses sync dan pembentukan versi spesifikasi

- **Status:** Diterima
- **Tanggal:** 2026-09-18
- **Fase MAPE-K:** Knowledge
- **Terkait:** P2 dan P3 (docs/knowledge/README.md), ADR-0003, ADR-0006, ADR-0009, ADR-0011

## Konteks

Knowledge adalah model runtime OBDF yang terhubung kausal dengan sistemnya. Sync harus
mengubah keadaan OBDF yang sedang berjalan menjadi **satu versi spesifikasi**, dapat dijalankan
berulang kali tanpa membanjiri riwayat, dan tetap meninggalkan jejak ketika gagal.

## Keputusan

1. **Satu sync = satu snapshot konsisten.** Seluruh tabel sistem Teiid dibaca dalam satu koneksi
   ODBC; model dan pemetaan sumber dari `get-vdb`; isi berkas VDB lewat `read-content`
   (diverifikasi SHA-1); ℳ dan 𝒯 dari agen Ontop (diverifikasi SHA-256).
2. **Versi baru hanya dibuat bila isi berubah.** Sidik jari `content_digest` dihitung dari
   Σ_S ternormalisasi ditambah SHA-256 setiap artefak. Bila sama dengan versi aktif, sync
   berakhir tanpa versi baru; `ops.sync_run` tetap mencatat kejadiannya.
3. **Aktivasi atomik.** Versi dibuat sebagai `candidate`, diisi, lalu diaktifkan; versi aktif
   sebelumnya menjadi `superseded` dalam transaksi yang sama, sehingga invarian "satu versi aktif
   per OBDF" tidak pernah dilanggar.
4. **Drift.** Bila sudah ada versi aktif dan isinya berbeda, sync menandai `drift_detected`:
   spesifikasi OBDF berubah di luar ASCAM.
5. **Kegagalan tetap tercatat.** Bila sync gagal, transaksi permintaan di-rollback, lalu satu
   baris `ops.sync_run` berstatus `failed` beserta galatnya ditulis dan di-commit terpisah, dan
   API menjawab 502. Tidak ada versi setengah jadi yang tersisa.
6. **Temuan dicatat sebagai masalah konsistensi**, bukan sebagai kegagalan sync:
   `vdb_validity_error` (dari `validity-errors` Teiid), `artifact_hash_mismatch`,
   `artifact_unavailable`, `artifact_unparsed`, `view_body_unparsed`, `source_system_unresolved`.
7. **Klien dapat disuntik** (parameter `clients`), sehingga sync diuji tanpa Teiid, Ontop,
   maupun Docker.

## Bukti

10 uji sync (total 60 pada Knowledge Service): versi pertama terbentuk dengan hitungan objek yang
benar; `NameInSource` dinormalisasi menjadi nama kolom di sumber; `source_schema`/`source_table`
terurai dari `NameInSource`; peran dependensi view terbedakan menjadi `projection`, `predicate`,
dan `table` (bukti F0.7); sync kedua tanpa perubahan tidak membuat versi; perubahan kolom
membuat versi 2 aktif dan versi 1 `superseded` dengan `parent_id` yang benar dan sidik jari
berbeda; isi versi yang sudah disegel tidak dapat diubah (SQLSTATE 55000); galat validitas VDB
dan sumber yang tidak terpetakan menjadi masalah konsistensi; ketidakcocokan hash artefak
terdeteksi; isi artefak dapat diambil kembali; sync yang gagal menghasilkan 502, mencatat
`sync_run` berstatus `failed`, dan tidak meninggalkan versi; klien agen menolak isi yang tidak
cocok dengan sidik jarinya; sidik jari Σ_S stabil dan peka terhadap perubahan.

## Konsekuensi

- Riwayat versi hanya bertambah saat spesifikasi benar-benar berubah, sehingga linimasa versi
  di UI bermakna.
- `content_digest` ikut tidak dapat diubah setelah versi disegel (migrasi 0004).
- Struktur ℳ dan 𝒯 (TriplesMap, term map, entitas ontologi) serta lineage kolom belum diisi pada
  tahap ini; artefak dan seluruh triple-nya sudah tersimpan sebagai dasar pengisian tersebut.
