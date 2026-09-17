# ADR-0005: Mapping R2RML sebagai artefak ℳ dan strategi validasi sebelum aktivasi

- **Status:** Diterima; dilaksanakan pada F1.4c (ADR-0010)
- **Tanggal:** 2026-09-17
- **Fase MAPE-K:** Plan (pembangkitan ℳ′), Execute (validasi sebelum aktivasi), Knowledge (sync ℳ)
- **Terkait:** batasan artefak (R2RML dan ontologi dalam Turtle), D10 (validasi konsistensi), ADR-0004 langkah 4

## Konteks

Batasan penelitian menetapkan mapping dalam R2RML (sintaks Turtle). Endpoint Ontop saat
ini masih memakai `mapping.obda`, sedangkan `mapping.ttl` (R2RML tulisan tangan) belum
diuji. Migrasi hanya dapat dibenarkan bila tidak mengubah jawaban OBDF. Di sisi lain,
ADR-0004 membutuhkan cara memvalidasi ℳ′ dan 𝒯′ terhadap versi VDB baru sebelum
koneksi dipindahkan.

CLI Ontop 4.1.1 menyediakan `validate` dan `mapping to-r2rml`
(`client/cli/.../Ontop.java`). `validate` (`OntopValidate.java`) memeriksa: ontologi
dapat dimuat; himpunan nama kelas, object property, dan data property saling lepas;
lalu `loadSpecification()` (pemuatan mapping beserta metadata basis data). Kegagalan
menghasilkan kode keluar 1.

## Uji kelayakan F0.5

Skrip: `experiments/f0/f0_5_ontop_r2rml.sh` (kontainer CLI Ontop 4.1.1 sementara,
terhubung ke Teiid yang sedang berjalan).

**Konversi dan validasi**

| Kasus | Hasil |
|---|---|
| `mapping to-r2rml` dari `.obda` | Berhasil, 311 baris, 8 `rr:TriplesMap` (IRI `<urn:MAP-...>`, `rr:R2RMLView` + `rr:sqlQuery`) |
| `validate` `.obda` / `mapping.ttl` / hasil konversi | Lolos (4,7–5,5 s per pemanggilan) |
| Kolom tidak ada (`rr:column "kolom_tidak_ada"`) | **Tertangkap**: `InvalidMappingSourceQueriesException` — placeholder tidak muncul di kueri sumber |
| Predikat tidak dideklarasikan di ontologi | **Tidak tertangkap** (lolos tanpa peringatan) |
| `--db-url ...;version=1` | Lolos |
| `--db-url ...;version=9` | Gagal; penyebab akar `TEIID40046 VDB "government" version "9" does not exist` (dibungkus `TEIID20018`) |

**Kesetaraan jawaban** (`ontop query`, keluaran CSV dibandingkan sebagai multiset baris)

| Kueri | Baris (.obda) | `mapping.ttl` | Hasil konversi |
|---|---|---|---|
| Jumlah triple per predikat (`eq_predicates`) | 36 | sama | sama |
| Jumlah instance per kelas (`eq_classes`) | 7 | sama | sama |
| `a001_existing` | 10 | sama | sama |
| `a002_optional` | 5 | sama | sama |
| `a003_rename` | 15 | sama | sama |

Semua pemanggilan juga menampilkan peringatan OWLAPI bahwa
`DataPropertyRange(bansos:aktif, xsd:boolean)` tidak termasuk OWL 2 QL.

## Keputusan

1. **ℳ adalah `mapping.ttl` (R2RML tulisan tangan).** Dipilih dibanding hasil konversi
   karena IRI TriplesMap bermakna (`<#MapProgram>`) dan strukturnya mudah dibaca;
   hasil konversi dipakai sebagai pembanding kesetaraan. Endpoint Ontop dipindahkan ke
   `mapping.ttl` bersamaan dengan Executor baru yang menulis R2RML (bukan sebelumnya).
2. **Validasi berlapis sebelum aktivasi Σ′_S/ℳ′/𝒯′:**
   - (a) `ontop validate -m ℳ′ -t 𝒯′ --db-url 'jdbc:teiid:<vdb>@mm://...;version=<N+1>'`
     — menjamin setiap placeholder ℳ′ ada di kueri sumber dan kueri sumber valid
     terhadap Σ′_S versi baru;
   - (b) **validator ASCAM** yang melengkapi Ontop: setiap predikat pada ℳ′ harus
     dideklarasikan di 𝒯′ dengan jenis yang sesuai (DatatypeProperty untuk objek
     literal) dan tidak ber-`owl:deprecated true`; pelanggaran profil OWL 2 QL yang
     dilaporkan Ontop diteruskan sebagai peringatan.
   Kegagalan (a) atau (b) menghentikan eksekusi sebelum connection type diubah
   (ADR-0004), sehingga OBDF tetap melayani dengan versi lama.
3. Penyebab kegagalan dibaca dari rantai `Caused by`, bukan hanya pesan tingkat atas.
4. Kesetaraan migrasi dibuktikan dengan kueri sidik jari graf dan kueri skenario;
   pengujian yang sama dipakai sebagai uji regresi saat ℳ berubah.

## Konsekuensi

- **Positif:** migrasi ke R2RML terbukti tidak mengubah jawaban pada kueri uji dan sidik
  jari graf; ℳ′ yang merujuk kolom tak ada tertangkap sebelum memengaruhi pengguna.
- **Biaya waktu:** satu pemanggilan `validate` ±5 s (didominasi start JVM). Biaya ini
  menambah Δt_adapt tetapi terjadi **sebelum** pemindahan koneksi, sehingga tidak
  menambah waktu tidak tersedianya OBDF. Dicatat dan diukur pada evaluasi.
- **Keterbatasan:** kesetaraan dibuktikan pada instans data saat ini (jumlah triple per
  predikat dan per kelas), bukan secara formal untuk semua instans basis data.
  Validator Ontop tidak memeriksa kosakata terhadap ontologi — celah ini ditutup
  validator ASCAM.
- **Temuan untuk perbaikan ontologi:** `xsd:boolean` pada range `bansos:aktif` di luar
  profil OWL 2 QL.

## Referensi

- Ontop CLI: https://ontop-vkg.org/guide/cli
- Source Ontop 4.1.1: https://github.com/ontop/ontop/tree/ontop-4.1.1/client/cli/src/main/java/it/unibz/inf/ontop/cli
- W3C R2RML: https://www.w3.org/TR/r2rml/
- Hasil uji: `results/f0/f0_5_20260917T093959/` (lokal, tidak di-commit).
