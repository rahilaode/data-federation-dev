# ADR-0018: Penulisan artefak OBDA, pencadangan, dan muat ulang Ontop oleh agen

- **Status:** Diterima
- **Tanggal:** 2026-09-18
- **Fase MAPE-K:** Execute
- **Terkait:** ADR-0001, ADR-0004, ADR-0005, ADR-0011

## Konteks

Executor harus mengganti ℳ dan 𝒯 pada host Ontop lalu memuat ulang endpoint, dan harus dapat
mengembalikan keadaan semula bila verifikasi gagal (ADR-0004 langkah 6). Ontop membaca kedua
artefak sebagai berkas biasa, tanpa antarmuka untuk menggantinya.

## Keputusan

1. **Penulisan atomik.** Isi baru ditulis ke berkas sementara di direktori yang sama lalu
   dipindahkan dengan `os.replace`, sehingga Ontop tidak pernah membaca berkas setengah tertulis.
2. **Setiap penulisan menyimpan cadangan isi lama** di `ascam-backups/` dengan nama yang memuat
   jenis artefak, cap waktu, dan sidik jari isi. Pemulihan memakai `backup_id`, dan pemulihan itu
   sendiri juga dicadangkan.
3. **Penguncian optimistis.** Pemanggil dapat menyertakan `expected_sha256`; bila isi saat ini
   berbeda, penulisan ditolak dengan 409. Ini mencegah Executor menimpa perubahan yang dilakukan
   pihak lain di antara pembacaan dan penulisan.
4. **Jenis artefak tetap dibatasi** pada `r2rml` dan `ontology`. Berkas properti Ontop tidak dapat
   ditulis maupun dipulihkan lewat API, dan `backup_id` divalidasi agar tidak dapat menunjuk
   berkas di luar direktori cadangan.
5. **Muat ulang** dilakukan dengan me-restart kontainer Ontop (ADR-0001), dan **kesiapan diukur
   dari endpoint SPARQL yang menjawab**, bukan dari status kontainer. Hasilnya melaporkan waktu
   berhenti, waktu siap, dan totalnya, sehingga dapat masuk dekomposisi Δt_adapt.
6. **Retensi cadangan** memakai `prune(keep)` per jenis artefak.

## Bukti

11 uji tambahan (total 23 pada agen): penulisan mengganti isi, menghasilkan sidik jari yang benar,
menyimpan cadangan isi lama, dan tidak meninggalkan berkas sementara; `expected_sha256` yang
kedaluwarsa ditolak 409 tanpa menimpa isi; pemulihan mengembalikan isi persis dan ikut
dicadangkan; `backup_id` yang tidak dikenal, menunjuk ke luar direktori, atau merujuk berkas
properti ditolak; penulisan ke artefak tak dikelola ditolak dan kredensial tidak bocor; retensi
menyisakan cadangan terbaru; muat ulang menunggu endpoint menjawab, melaporkan kegagalan restart,
dan melaporkan endpoint yang tidak kunjung siap.

## Konsekuensi

- Volume artefak pada kontainer agen kini **baca-tulis**; agen menjadi satu-satunya komponen yang
  boleh menulis artefak OBDA.
- Cadangan menumpuk di host Ontop bila tidak dipangkas; retensi dijalankan Executor setelah
  adaptasi berhasil.
- Muat ulang tetap menyebabkan endpoint tidak melayani selama beberapa detik; blue-green di
  lapisan Ontop dicatat sebagai pekerjaan lanjutan (ADR-0001).
