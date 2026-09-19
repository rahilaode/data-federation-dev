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
     Bila logical table menyebut kolom itu **secara eksplisit** pada `rr:sqlQuery`, rencana
     memuat tindakan `rewrite_logical_table` untuk mengeluarkannya dari daftar proyeksi;
     tanpa itu kueri masih merujuk kolom yang sudah hilang dan `ontop validate` menolak
     (temuan evaluasi skenario A002). `SELECT *` tidak memerlukan tindakan ini.
   - **ADD** → otomatis sesuai kebijakan `adaptation.add_column`; HITL bila nama kolom sudah ada
     di tabel Teiid, atau kebijakan penamaan/namespace belum diatur.
4. **Property bersama tidak di-deprecate.** Bila predikat yang terdampak masih dipakai kolom
   lain, rencana mencantumkan `keep_property`, bukan `deprecate_property`.
5. **TriplesMap sasaran ditentukan Knowledge, bukan ditebak Executor.** Rencana menyebut IRI
   TriplesMap untuk setiap tindakan mapping. Untuk ADD, hanya TriplesMap yang **otomatis**
   memuat kolom baru yang dipilih, yaitu logical table berupa `rr:tableName` atau kueri
   `SELECT *`; TriplesMap dengan daftar kolom eksplisit tidak dipilih, dan bila tidak ada yang
   memenuhi, keputusan menjadi HITL. Untuk DROP, TriplesMap diambil dari lineage. Aturan ini
   lahir dari kegagalan nyata pada F4c: pemetaan yang ditempelkan ke TriplesMap dengan kolom
   eksplisit membuat `ontop validate` menolak artefak.
6. **Bentrok jenis entitas (punning).** Bila IRI property yang dihasilkan sudah dipakai sebagai
   object property atau kelas, penambahan sebagai data property ditolak Ontop
   ("Object and Data property name sets are not disjoint"), sehingga kebijakan `on_collision`
   diterapkan atau keputusan menjadi HITL.
7. **Bentrok domain pada ADD.** Bila IRI property yang dihasilkan sudah ada dengan domain
   berbeda, kebijakan `on_collision` menentukan: `qualify_with_class` membentuk IRI baru
   berkualifikasi kelas, atau `hitl`. Ini mencegah inferensi keliru akibat semantik
   `rdfs:domain`.
8. **Kondisi yang membuat seluruh keputusan konservatif**: model multi-source, tabel yang dirujuk
   prosedur atau trigger, dan adanya logical table dengan SQL yang tidak dapat diurai.
9. **Objek yang tidak dikenal diabaikan** (`ignored`), bukan digagalkan: sumber tak terdaftar,
   tabel tak difederasikan, atau kolom yang tidak ada pada spesifikasi aktif.
10. Keluaran memuat **rencana tindakan** per artefak (`vdb`, `r2rml`, `ontology`) yang kelak
   dieksekusi Executor; analisis sendiri tidak mengubah apa pun.

## Bukti

21 uji dampak (total 114 pada Knowledge Service), mencakup: RENAME otomatis beserta tindakan
`SET NAMEINSOURCE` dan nama Teiid yang tidak berubah; RENAME bentrok nama → HITL; DROP
`literal_value` otomatis beserta penghapusan predicate-object map dan deprecation property;
DROP predikat bersama → `keep_property`; DROP kolom kunci primer dan template IRI → HITL; DROP
kolom yang hanya dipakai `WHERE` di dalam view → HITL; jalur melewati ekspresi → HITL; ADD
otomatis dengan IRI property hasil kebijakan penamaan dan domain dari kelas TriplesMap; ADD
mengikuti kebijakan `hitl`; ADD nama kolom yang sudah ada → HITL; bentrok domain dengan dua
kebijakan berbeda; sumber, tabel, dan kolom tak dikenal → `ignored`; SQL tak terurai → HITL; pemilihan TriplesMap
hanya pada yang mengekspos kolom baru (regresi F4c), HITL bila tidak ada, penolakan IRI yang sudah
dipakai object property beserta penerapan kebijakan bentrok nama, dan penyebutan TriplesMap pada
tindakan penghapusan; penulisan ulang logical table untuk kolom yang diproyeksikan eksplisit dan
ketiadaannya untuk kolom hasil `SELECT *`.

## Konsekuensi

- Orchestrator menjadi tipis: menerima event, memanggil analisis, lalu menyimpan rencana dan
  mengeksekusinya bila `auto`.
- Aturan D11 diuji sebagai satu kesatuan, sehingga perubahan aturan selalu disertai uji.
- Rencana tindakan masih bersifat deskriptif; bentuk pernyataan `ALTER` dan suntingan artefak
  ditetapkan saat Executor dibangun.
