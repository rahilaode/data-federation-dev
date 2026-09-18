# ADR-0015: Analisis dampak dan keputusan adaptasi (matriks D11) berada di Knowledge

- **Status:** Diterima
- **Tanggal:** 2026-09-18
- **Fase MAPE-K:** Analyze dan Plan
- **Terkait:** D11, ADR-0002, ADR-0006, ADR-0013, ADR-0014

## Konteks

Orchestrator harus memutuskan, untuk setiap event skema, apakah adaptasi dapat dijalankan
otomatis atau perlu persetujuan administrator (HITL), dan tindakan apa yang harus dilakukan.
Keputusan itu bergantung sepenuhnya pada isi versi spesifikasi aktif (Σ_S, ℳ, 𝒯, lineage) yang
sudah berada di Knowledge.

## Keputusan

1. **Analisis dampak diletakkan di Knowledge Service** sebagai operasi **read-only**
   (`POST /api/v1/obdf/{id}/impact`), bukan di Orchestrator. Alasannya: seluruh data yang
   dibutuhkan ada di sana, satu sumber aturan memudahkan pengujian, dan UI dapat memakai
   endpoint yang sama untuk menampilkan pratinjau dampak sebelum administrator menyetujui.
2. **Pemetaan event ke Σ_S**: sumber dicocokkan dengan `registry.source_system`, skema memakai
   `default_schema` bila event tidak menyebutnya, lalu tabel dan kolom dicocokkan dengan
   `source_table`/`source_column` (nama efektif di sumber). Satu tabel sumber yang diekspos
   beberapa model menghasilkan beberapa target dalam satu keputusan.
3. **Aturan keputusan** (matriks D11, berdasar bukti uji kelayakan):
   - **RENAME** → otomatis; tindakan berupa `SET NAMEINSOURCE`, sehingga ℳ, 𝒯, dan view tidak
     berubah (F0.7 v7). HITL bila nama sumber baru bentrok dengan kolom lain di tabel yang sama.
   - **DROP** → otomatis hanya bila seluruh pemakaian berperan `literal_value` atau
     `projection_only` dan tepi terlemahnya bukan `expression`/`predicate`. HITL bila kolom
     bagian kunci primer, dipakai sebagai template IRI, kunci join, klausa SQL, atau predikat
     dinamis, dan bila jalurnya melewati ekspresi view atau klausa view (F0.7 v2–v4).
   - **ADD** → otomatis sesuai kebijakan `adaptation.add_column`; HITL bila nama kolom sudah ada
     di tabel Teiid, atau kebijakan penamaan/namespace belum diatur.
4. **Property bersama tidak di-deprecate.** Bila predikat yang terdampak masih dipakai kolom
   lain, rencana mencantumkan `keep_property`, bukan `deprecate_property`.
5. **Bentrok domain pada ADD.** Bila IRI property yang dihasilkan sudah ada dengan domain
   berbeda, kebijakan `on_collision` menentukan: `qualify_with_class` membentuk IRI baru
   berkualifikasi kelas, atau `hitl`. Ini mencegah inferensi keliru akibat semantik
   `rdfs:domain`.
6. **Kondisi yang membuat seluruh keputusan konservatif**: model multi-source, tabel yang dirujuk
   prosedur atau trigger, dan adanya logical table dengan SQL yang tidak dapat diurai.
7. **Objek yang tidak dikenal diabaikan** (`ignored`), bukan digagalkan: sumber tak terdaftar,
   tabel tak difederasikan, atau kolom yang tidak ada pada spesifikasi aktif.
8. Keluaran memuat **rencana tindakan** per artefak (`vdb`, `r2rml`, `ontology`) yang kelak
   dieksekusi Executor; analisis sendiri tidak mengubah apa pun.

## Bukti

15 uji dampak (total 87 pada Knowledge Service), mencakup: RENAME otomatis beserta tindakan
`SET NAMEINSOURCE` dan nama Teiid yang tidak berubah; RENAME bentrok nama → HITL; DROP
`literal_value` otomatis beserta penghapusan predicate-object map dan deprecation property;
DROP predikat bersama → `keep_property`; DROP kolom kunci primer dan template IRI → HITL; DROP
kolom yang hanya dipakai `WHERE` di dalam view → HITL; jalur melewati ekspresi → HITL; ADD
otomatis dengan IRI property hasil kebijakan penamaan dan domain dari kelas TriplesMap; ADD
mengikuti kebijakan `hitl`; ADD nama kolom yang sudah ada → HITL; bentrok domain dengan dua
kebijakan berbeda; sumber, tabel, dan kolom tak dikenal → `ignored`; SQL tak terurai → HITL.

## Konsekuensi

- Orchestrator menjadi tipis: menerima event, memanggil analisis, lalu menyimpan rencana dan
  mengeksekusinya bila `auto`.
- Aturan D11 diuji sebagai satu kesatuan, sehingga perubahan aturan selalu disertai uji.
- Rencana tindakan masih bersifat deskriptif; bentuk pernyataan `ALTER` dan suntingan artefak
  ditetapkan saat Executor dibangun.
