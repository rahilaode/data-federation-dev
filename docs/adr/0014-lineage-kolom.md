# ADR-0014: Lineage kolom sebagai dasar analisis dampak

- **Status:** Diterima
- **Tanggal:** 2026-09-18
- **Fase MAPE-K:** Knowledge (sync), Analyze (analisis dampak), Plan (matriks D11)
- **Terkait:** ADR-0006, ADR-0012, ADR-0013, rancangan Knowledge §4.6 dan §5

## Konteks

Orchestrator harus menjawab satu pertanyaan dengan cepat: *bila kolom sumber X berubah, apa
yang terdampak dan bolehkah ditangani otomatis?* Jawaban itu memerlukan jalur lengkap
kolom sumber → kolom foreign table → kolom view (mungkin bertingkat) → kolom logical table →
term map → predikat → entitas ontologi.

## Keputusan

1. **Hasil penelusuran dimaterialisasi** pada `spec.column_usage` saat sync, satu baris per
   pemakaian kolom foreign table, sehingga Analyze dan UI tidak perlu menelusuri ulang.
2. **Peran (`role`)** mengikuti matriks D11: `literal_value`, `iri_template`, `join_key`,
   `sql_predicate`, `dynamic_predicate`, `projection_only`.
3. **Jalur (`path`) dan tepi terlemah (`weakest_link`).** Setiap tepi diberi jenis; urutan
   kelemahannya: `direct` → `passthrough` → `star` → `expression` → `predicate`. Nilai terlemah
   pada jalur disimpan, karena satu tepi `expression` atau `predicate` sudah cukup membuat
   perubahan tidak dapat ditangani otomatis. Sifat kolom logical table (ekspresi SQL atau hasil
   `SELECT *`) ikut diperhitungkan, bukan hanya tepi view.
4. **Kolom yang dipakai view di klausa `WHERE`/`JOIN`** dicatat sebagai `sql_predicate` bagi
   setiap TriplesMap yang membaca view tersebut, dengan tepi terlemah `predicate`. Tanpa ini,
   DROP pada kolom seperti itu akan lolos sebagai "tidak dipakai", padahal membuat VDB gagal
   (bukti F0.7).
5. **Pemeriksaan kosakata** yang tidak dilakukan `ontop validate` (temuan F0.5) dijalankan di
   sini: `predicate_undeclared`, `predicate_kind_mismatch` (objek literal memakai object
   property), `predicate_deprecated_in_use`, dan `unused_property` (informatif).
6. **Predikat ditautkan ke entitas ontologi** pada versi yang sama, sehingga Plan dapat
   memeriksa apakah sebuah property masih dipakai di tempat lain sebelum men-deprecate-nya.

## Bukti

Empat uji lineage (total 72 pada Knowledge Service): peran literal, template IRI subjek,
`join_key`, dan `sql_predicate` terbentuk dengan predikat yang benar; kolom ekspresi ditandai
`expression` dan hasil `SELECT *` ditandai `star`; TriplesMap yang membaca view menelusuri
kolom view pass-through sampai ke kolom foreign table beserta jalurnya; kolom yang hanya
dipakai `WHERE` di dalam view terdeteksi dengan tepi terlemah `predicate`; predikat yang tidak
dideklarasikan, property deprecated yang masih dipakai, property yang tidak terpakai, dan objek
literal yang memakai object property semuanya dilaporkan sebagai masalah konsistensi.

## Konsekuensi

- `spec.column_usage` menjadi masukan langsung matriks D11; keputusan otomatis atau HITL
  tinggal membaca `role` dan `weakest_link`.
- Lineage dihitung ulang setiap kali versi baru dibuat, sehingga selalu konsisten dengan Σ_S,
  ℳ, dan 𝒯 pada versi tersebut.
- Perubahan bentuk data yang disimpan menaikkan `CONTENT_VERSION` pada proses sync, sehingga
  versi baru terbentuk meski OBDF tidak berubah. Ini memperbaiki kelemahan yang ditemukan pada
  pengujian F1.5b: sidik jari isi harus mencakup seluruh atribut yang disimpan **dan** versi
  format isinya.
