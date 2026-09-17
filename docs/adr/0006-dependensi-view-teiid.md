# ADR-0006: Dependensi view Teiid diambil dari SYSADMIN.Usage dan SYSADMIN.Views

- **Status:** Diterima
- **Tanggal:** 2026-09-17
- **Fase MAPE-K:** Knowledge (sync lineage), Analyze (analisis dampak)
- **Terkait:** D8, D11, ADR-0002, ADR-0003, ADR-0004

## Konteks

Mapping R2RML dapat merujuk view Teiid, dan view dapat memakai kolom foreign table
(langsung atau lewat view lain). Tanpa dependensi tingkat kolom, ASCAM tidak dapat
menentukan dampak perubahan kolom sumber terhadap view dan mapping. Teiid Reference Guide
("SYSADMIN schema") menyediakan `SYSADMIN.Usage` (dependensi objek dan kolom) dan
`SYSADMIN.Views` (definisi view).

## Uji kelayakan F0.7

Skrip: `experiments/f0/f0_7_teiid_views.py`. VDB uji `f0w` dengan dua foreign table dan
empat view: `program_ringkas` (pass-through, ekspresi `UCASE`, `WHERE`), `program_semua`
(`SELECT *`), `transaksi_program` (`JOIN`), `program_bertingkat` (view di atas view).
Setiap perubahan diuji sebagai versi VDB baru lewat management API.

**Metadata runtime Teiid 16 (berbeda dari dokumentasi):**

- `SYSADMIN.Usage` memiliki kolom `VDBName, UID, object_type, SchemaName, Name,
  ElementName, Uses_UID, Uses_object_type, Uses_SchemaName, Uses_Name, Uses_ElementName`;
  `SchemaName` objek pemakai tidak tercantum di Reference Guide.
- `SYSADMIN.Views`: `VDBName, SchemaName, Name, Body, UID`.
- View tercatat di `SYS.Tables` dengan `Type = 'Table'` dan `IsPhysical = false`.

**Semantik `SYSADMIN.Usage` yang teramati:**

| Baris | Arti | Contoh |
|---|---|---|
| `object_type = View`, `ElementName` kosong | Objek/kolom yang dirujuk **di mana pun** dalam definisi view (proyeksi, `WHERE`, `JOIN`) | `program_ringkas ← program_bansos.nominal` (hanya di `WHERE`); `transaksi_program ← t.program_id`, `pr.program_id` (hanya di `JOIN`) |
| `object_type = Column` | Kolom sumber dari **ekspresi pembentuk kolom view** | `program_ringkas.tipe_upper ← program_bansos.tipe_program` |
| View `SELECT *` | Satu entri tingkat kolom per kolom hasil ekspansi | `program_semua.kolom_ekstra ← program_bansos.kolom_ekstra` |
| View bertingkat | Merujuk view dan kolom view lain | `program_bertingkat.tipe_upper ← program_ringkas.tipe_upper` |

`Usage` tidak membedakan kolom pass-through dan kolom ekspresi. Analisis `Body` dengan
`sqlglot` berhasil membedakannya untuk keempat view (pass-through, ekspresi, kolom `WHERE`,
kolom `JOIN`, dan `*`).

**Dampak perubahan kolom foreign table:**

| Versi | Perubahan | Status VDB | Pengamatan |
|---|---|---|---|
| v2 | DROP kolom yang dipakai ekspresi view | FAILED | `TEIID31118` pada view; kegagalan merambat ke view bertingkat (`TEIID31071`) |
| v3 | DROP kolom yang hanya dipakai `WHERE` | FAILED | idem |
| v4 | DROP kolom yang hanya dipakai `JOIN` | FAILED | idem pada `transaksi_program` |
| v5 | DROP kolom yang hanya dipakai view `SELECT *` | ACTIVE | kolom hilang dari view `SELECT *` |
| v6 | ADD kolom | ACTIVE | kolom baru muncul pada view `SELECT *` |
| v7 | RENAME lewat `SET NAMEINSOURCE` (D8) | ACTIVE | semua view utuh; `NameInSource` terisi |
| v8 | RENAME lewat `RENAME COLUMN` | FAILED | view yang merujuk nama lama rusak |

## Keputusan

1. Knowledge menyimpan dependensi Teiid dari `SYSADMIN.Usage` (tingkat objek dan kolom)
   dan definisi view dari `SYSADMIN.Views`, per versi spesifikasi.
2. Setiap kolom view diklasifikasi dari `Body` dengan `sqlglot`: `passthrough`,
   `expression`, `star`, atau `unknown` (bila `Body` gagal di-parse). Kolom yang dirujuk
   view tetapi tidak menjadi sumber kolom view mana pun diberi peran `predicate`
   (`WHERE`, `JOIN`, dan klausa lain).
3. View dikenali dari `IsPhysical = false` dan keberadaannya di `SYSADMIN.Views`,
   bukan dari `SYS.Tables.Type`.
4. Analisis dampak menelusuri dependensi secara rekursif dari kolom foreign table ke
   kolom view hingga pemakaian di ℳ. Aturan untuk view:
   - DROP kolom yang dipakai view **selain** lewat `SELECT *` → HITL (VDB akan FAILED);
   - DROP kolom yang hanya dipakai lewat `SELECT *` → dampak diteruskan ke konsumen
     kolom view tersebut;
   - ADD kolom → view `SELECT *` ikut bertambah; view lain tidak;
   - RENAME → D8, view tidak tersentuh.
5. Pesan `validity-errors` VDB (termasuk kegagalan berantai) disimpan pada hasil
   validasi dan ditampilkan di UI.

## Konsekuensi

- Dependensi diperoleh dari Teiid sendiri (bukan parser DDL ASCAM); parser `sqlglot`
  hanya dipakai untuk klasifikasi kolom view.
- Reference Guide menyatakan dependensi tingkat kolom belum ditelusuri melalui tabel
  sementara atau *common table*; view semacam itu diperlakukan konservatif.
- View `SELECT *` membuat perubahan skema merambat tanpa mengubah definisi view; hal ini
  harus diperhitungkan saat ADD dan DROP.
- Keputusan D8 terbukti menjaga seluruh view tetap valid, sedangkan `RENAME COLUMN`
  merusak view.

## Referensi

- Teiid Reference Guide (`teiid-documents.pdf`): "SYSADMIN schema" (hlm. 566–569),
  "Schema object DDL" (hlm. 352–356).
- Hasil uji: `results/f0/f0_7_20260917T072202/` (lokal, tidak di-commit).
