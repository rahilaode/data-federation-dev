# Adaptive Mapping Engine - Simulasi SMO dengan Teiid VDB + OBDA Mapping

> **Konteks:** Simulasi cara kerja Pattern Library dalam framework ASCAM menggunakan studi kasus database Bansos, dengan arsitektur Teiid VDB descriptor (`vdb.xml`) + OBDA mapping (`.obda`) di atas ontologi OWL/RDF.

---

## Daftar Isi

- [Arsitektur Stack](#arsitektur-stack)
- [Baseline: Mapping Awal](#baseline-mapping-awal)
  - [vdb.xml](#vdbxml)
  - [bansos.obda](#bansasobda)
- [Cara Kerja Pattern Library](#cara-kerja-pattern-library)
- [Simulasi SMO](#simulasi-smo)
  - [SMO 1 - ADD COLUMN](#smo-1--add-column)
  - [SMO 2 - DROP COLUMN](#smo-2--drop-column)
  - [SMO 3 - RENAME COLUMN](#smo-3--rename-column)
  - [SMO 4 - MODIFY COLUMN](#smo-4--modify-column)
- [Ringkasan Dampak per Lapisan](#ringkasan-dampak-per-lapisan)

---

## Arsitektur Stack

```
SPARQL Query
      │
      ▼
┌─────────────┐
│  Ontop/OBDA │  ← file .obda (mapping ontologi → virtual view)
│   Layer     │
└──────┬──────┘
       │ query ke virtual table
       ▼
┌─────────────┐
│  Teiid VDB  │  ← file vdb.xml (virtual views / transformasi SQL)
│   Layer     │
└──────┬──────┘
       │ query ke physical table
       ▼
┌─────────────┐
│  Physical   │  ← PostgreSQL/MySQL (database bansos asli)
│  Database   │
└─────────────┘
```

Ketika schema fisik berubah, Pattern Library harus mengupdate **dua file sekaligus**:
- `vdb.xml` → virtual view di Teiid
- `.obda` → mapping dari ontologi ke virtual view

---

## Baseline: Mapping Awal

### vdb.xml

```xml
<?xml version="1.0" encoding="UTF-8"?>
<vdb name="bansos-federation" version="1">

  <data-role name="bansos-source">
    <source name="bansos_db"
            translator-name="postgresql"
            connection-jndi-name="java:/bansos-ds"/>
  </data-role>

  <!-- Virtual Model: Penduduk -->
  <model name="vm_penduduk" type="VIRTUAL">
    <metadata type="DDL"><![CDATA[
      CREATE VIEW v_penduduk AS
        SELECT nik, no_kk, nama, tanggal_lahir, pekerjaan,
               penghasilan, status_hidup, created_at
        FROM bansos_db.master_penduduk;
    ]]></metadata>
  </model>

  <!-- Virtual Model: Wilayah -->
  <model name="vm_wilayah" type="VIRTUAL">
    <metadata type="DDL"><![CDATA[
      CREATE VIEW v_wilayah AS
        SELECT wilayah_id, provinsi, kabupaten, kecamatan, desa
        FROM bansos_db.master_wilayah;
    ]]></metadata>
  </model>

  <!-- Virtual Model: Keluarga -->
  <model name="vm_keluarga" type="VIRTUAL">
    <metadata type="DDL"><![CDATA[
      CREATE VIEW v_keluarga AS
        SELECT k.no_kk, k.wilayah_id,
               w.provinsi, w.kabupaten, w.kecamatan, w.desa,
               k.created_at
        FROM bansos_db.master_keluarga k
        JOIN bansos_db.master_wilayah w ON k.wilayah_id = w.wilayah_id;
    ]]></metadata>
  </model>

  <!-- Virtual Model: Program Bansos -->
  <model name="vm_program_bansos" type="VIRTUAL">
    <metadata type="DDL"><![CDATA[
      CREATE VIEW v_program_bansos AS
        SELECT program_id, nama_program, tipe_program,
               CAST(nominal AS BIGINT) AS nominal,
               periode_mulai, periode_selesai
        FROM bansos_db.master_program_bansos;
    ]]></metadata>
  </model>

  <!-- Virtual Model: Eligibility -->
  <model name="vm_eligibility" type="VIRTUAL">
    <metadata type="DDL"><![CDATA[
      CREATE VIEW v_eligibility AS
        SELECT e.eligibility_id, e.program_id, e.nik,
               e.status_eligible, e.alasan,
               e.validated_at, e.validated_by
        FROM bansos_db.eligibility e;
    ]]></metadata>
  </model>

  <!-- Virtual Model: Transaksi -->
  <model name="vm_transaksi" type="VIRTUAL">
    <metadata type="DDL"><![CDATA[
      CREATE VIEW v_transaksi AS
        SELECT transaksi_id, prorgram_id, nik,
               periode, status, created_at
        FROM bansos_db.transaksi_bansos;
    ]]></metadata>
  </model>

</vdb>
```

### bansos.obda

```
[PrefixDeclaration]
:       http://data.go.id/bansos#
ex:     http://data.go.id/ontology/bansos/
xsd:    http://www.w3.org/2001/XMLSchema#
owl:    http://www.w3.org/2002/07/owl#

[MappingDeclaration] @collection [[

  mappingId   penduduk-mapping
  target      ex:penduduk/{nik} a ex:Penduduk ;
                ex:nik {nik}^^xsd:string ;
                ex:noKK {no_kk}^^xsd:string ;
                ex:nama {nama}^^xsd:string ;
                ex:tanggalLahir {tanggal_lahir}^^xsd:date ;
                ex:pekerjaan {pekerjaan}^^xsd:string ;
                ex:penghasilan {penghasilan}^^xsd:decimal ;
                ex:statusHidup {status_hidup}^^xsd:string ;
                ex:createdAt {created_at}^^xsd:dateTime .
  source      SELECT nik, no_kk, nama, tanggal_lahir, pekerjaan,
                     penghasilan, status_hidup, created_at
              FROM vm_penduduk.v_penduduk

  mappingId   wilayah-mapping
  target      ex:wilayah/{wilayah_id} a ex:Wilayah ;
                ex:provinsi {provinsi}^^xsd:string ;
                ex:kabupaten {kabupaten}^^xsd:string ;
                ex:kecamatan {kecamatan}^^xsd:string ;
                ex:desa {desa}^^xsd:string .
  source      SELECT wilayah_id, provinsi, kabupaten, kecamatan, desa
              FROM vm_wilayah.v_wilayah

  mappingId   keluarga-mapping
  target      ex:keluarga/{no_kk} a ex:Keluarga ;
                ex:noKK {no_kk}^^xsd:string ;
                ex:berlokasi ex:wilayah/{wilayah_id} ;
                ex:createdAt {created_at}^^xsd:dateTime .
  source      SELECT no_kk, wilayah_id, created_at
              FROM vm_keluarga.v_keluarga

  mappingId   program-bansos-mapping
  target      ex:program/{program_id} a ex:ProgramBansos ;
                ex:namaProgram {nama_program}^^xsd:string ;
                ex:tipeProgram {tipe_program}^^xsd:string ;
                ex:nominal {nominal}^^xsd:long ;
                ex:periodeMulai {periode_mulai}^^xsd:date ;
                ex:periodeSelesai {periode_selesai}^^xsd:date .
  source      SELECT program_id, nama_program, tipe_program,
                     nominal, periode_mulai, periode_selesai
              FROM vm_program_bansos.v_program_bansos

  mappingId   eligibility-mapping
  target      ex:eligibility/{eligibility_id} a ex:Eligibility ;
                ex:untukProgram ex:program/{program_id} ;
                ex:pendudukEligible ex:penduduk/{nik} ;
                ex:statusEligible {status_eligible}^^xsd:string ;
                ex:alasan {alasan}^^xsd:string ;
                ex:validatedAt {validated_at}^^xsd:dateTime ;
                ex:validatedBy {validated_by}^^xsd:string .
  source      SELECT eligibility_id, program_id, nik,
                     status_eligible, alasan, validated_at, validated_by
              FROM vm_eligibility.v_eligibility

  mappingId   transaksi-mapping
  target      ex:transaksi/{transaksi_id} a ex:TransaksiBansos ;
                ex:untukProgram ex:program/{prorgram_id} ;
                ex:penerimaBansos ex:penduduk/{nik} ;
                ex:periode {periode}^^xsd:string ;
                ex:status {status}^^xsd:string ;
                ex:createdAt {created_at}^^xsd:dateTime .
  source      SELECT transaksi_id, prorgram_id, nik,
                     periode, status, created_at
              FROM vm_transaksi.v_transaksi

]]
```

---

## Cara Kerja Pattern Library

Pattern Library bekerja dalam **dua fase berurutan** setiap kali ada change event:

```
Change Event masuk (dari Schema Monitor)
       │
       ▼
  FASE A: Update vdb.xml
  (perbaiki virtual view di Teiid)
       │
       ▼
  Validasi: apakah view baru masih
  mengekspos kolom yang dibutuhkan OBDA?
       │
       ▼
  FASE B: Update .obda
  (perbaiki mapping source query)
       │
       ▼
  Mapping final valid 
```

**Format Change Event standar:**
```json
{
  "smo_type": "RENAME_COLUMN",
  "source_id": "bansos_db",
  "table": "master_penduduk",
  "old_column": "status_hidup",
  "new_column": "kondisi_hidup",
  "timestamp": "2025-01-10T08:00:00Z"
}
```

---

## Simulasi SMO

---

### SMO 1 - ADD COLUMN

**Skenario:** Tabel `master_wilayah` menambah kolom `kode_pos VARCHAR(10)`

**Change Event:**
```json
{
  "smo_type": "ADD_COLUMN",
  "table": "master_wilayah",
  "column": "kode_pos",
  "datatype": "VARCHAR(10)",
  "nullable": true
}
```

**FASE A - Update vdb.xml:**
```xml
<!-- SEBELUM -->
CREATE VIEW v_wilayah AS
  SELECT wilayah_id, provinsi, kabupaten, kecamatan, desa
  FROM bansos_db.master_wilayah;

<!-- SESUDAH -->
CREATE VIEW v_wilayah AS
  SELECT wilayah_id, provinsi, kabupaten, kecamatan, desa,
         kode_pos                    -- ← DITAMBAHKAN
  FROM bansos_db.master_wilayah;
```

**FASE B - Update .obda:**
```
<!-- SEBELUM -->
target    ex:wilayah/{wilayah_id} a ex:Wilayah ;
            ...
            ex:desa {desa}^^xsd:string .
source    SELECT wilayah_id, provinsi, kabupaten, kecamatan, desa
          FROM vm_wilayah.v_wilayah

<!-- SESUDAH -->
target    ex:wilayah/{wilayah_id} a ex:Wilayah ;
            ...
            ex:desa {desa}^^xsd:string ;
            ex:kodePos {kode_pos}^^xsd:string .   -- ← DITAMBAHKAN
source    SELECT wilayah_id, provinsi, kabupaten, kecamatan, desa,
                 kode_pos                          -- ← DITAMBAHKAN
          FROM vm_wilayah.v_wilayah
```

**Status:** Auto-repair, dua fase berurutan

---

### SMO 2 - DROP COLUMN

**Skenario:** Kolom `alasan` dihapus dari tabel `eligibility`

**Change Event:**
```json
{
  "smo_type": "DROP_COLUMN",
  "table": "eligibility",
  "column": "alasan"
}
```

**Pemeriksaan sebelum repair:**
```
→ Cek apakah "alasan" dipakai di URI subject template → TIDAK
→ Cek apakah "alasan" dipakai di join condition       → TIDAK
→ AMAN untuk auto-repair
```

**FASE A - Update vdb.xml:**
```xml
<!-- SEBELUM -->
CREATE VIEW v_eligibility AS
  SELECT e.eligibility_id, e.program_id, e.nik,
         e.status_eligible, e.alasan,        -- ← ADA
         e.validated_at, e.validated_by
  FROM bansos_db.eligibility e;

<!-- SESUDAH -->
CREATE VIEW v_eligibility AS
  SELECT e.eligibility_id, e.program_id, e.nik,
         e.status_eligible,                  -- alasan DIHAPUS
         e.validated_at, e.validated_by
  FROM bansos_db.eligibility e;
```

**FASE B - Update .obda:**
```
-- SEBELUM
target    ex:eligibility/{eligibility_id} a ex:Eligibility ;
            ...
            ex:statusEligible {status_eligible}^^xsd:string ;
            ex:alasan {alasan}^^xsd:string ;        -- ← ADA
            ex:validatedAt {validated_at}^^xsd:dateTime .
source    SELECT eligibility_id, program_id, nik,
                 status_eligible, alasan,           -- ← ADA
                 validated_at, validated_by
          FROM vm_eligibility.v_eligibility

-- SESUDAH
target    ex:eligibility/{eligibility_id} a ex:Eligibility ;
            ...
            ex:statusEligible {status_eligible}^^xsd:string ;
            -- ex:alasan DIHAPUS
            ex:validatedAt {validated_at}^^xsd:dateTime .
source    SELECT eligibility_id, program_id, nik,
                 status_eligible,                   -- alasan DIHAPUS
                 validated_at, validated_by
          FROM vm_eligibility.v_eligibility
```

**Status:** Auto-repair, dua fase berurutan

---

### SMO 3 - RENAME COLUMN

**Skenario:** Kolom `status_hidup` di `master_penduduk` di-rename menjadi `kondisi_hidup`

**Change Event:**
```json
{
  "smo_type": "RENAME_COLUMN",
  "table": "master_penduduk",
  "old_column": "status_hidup",
  "new_column": "kondisi_hidup"
}
```

**FASE A - Update vdb.xml (gunakan alias):**
```xml
<!-- SEBELUM -->
CREATE VIEW v_penduduk AS
  SELECT nik, no_kk, nama, tanggal_lahir, pekerjaan,
         penghasilan, status_hidup, created_at
  FROM bansos_db.master_penduduk;

<!-- SESUDAH  - alias menjaga nama kolom view tetap sama -->
CREATE VIEW v_penduduk AS
  SELECT nik, no_kk, nama, tanggal_lahir, pekerjaan,
         penghasilan,
         kondisi_hidup AS status_hidup,   -- ← alias, nama view TIDAK berubah
         created_at
  FROM bansos_db.master_penduduk;
```

> **Alias Trick:** Dengan menggunakan alias di Teiid, nama kolom yang diekspos ke OBDA tetap `status_hidup`. Layer OBDA tidak perlu tahu bahwa kolom fisik sudah berganti nama.

**FASE B - Update .obda: TIDAK PERLU DIUBAH**
```
-- Tetap sama karena view masih mengekspos "status_hidup"
-- Tidak ada perubahan apapun di file .obda
```

**Status:** Auto-repair, **hanya Fase A** - perubahan diisolasi di layer Teiid

---

### SMO 4 - MODIFY COLUMN

**Skenario:** Kolom `nominal` di `master_program_bansos` diubah dari `INT` menjadi `BIGINT`

**Change Event:**
```json
{
  "smo_type": "MODIFY_COLUMN",
  "table": "master_program_bansos",
  "column": "nominal",
  "old_datatype": "INT",
  "new_datatype": "BIGINT"
}
```



**FASE A - Update vdb.xml:**
```xml
<!-- SEBELUM -->
CREATE VIEW v_program_bansos AS
  SELECT program_id, nama_program, tipe_program,
         CAST(nominal AS BIGINT) AS nominal,   -- CAST tidak lagi diperlukan
         periode_mulai, periode_selesai
  FROM bansos_db.master_program_bansos;

<!-- SESUDAH  - CAST dihapus karena tipe di source sudah BIGINT -->
CREATE VIEW v_program_bansos AS
  SELECT program_id, nama_program, tipe_program,
         nominal,                              -- langsung pakai, tanpa CAST
         periode_mulai, periode_selesai
  FROM bansos_db.master_program_bansos;
```

**FASE B - Update .obda: TIDAK PERLU DIUBAH **
```
-- xsd:long sudah benar dari awal
-- view tetap mengekspos kolom "nominal" dengan tipe yang kompatibel
```

**Status:**  Auto-repair, **hanya Fase A** - view menyerap perubahan tipe

---


## Ringkasan Dampak per Lapisan

| SMO            | Tabel/Kolom                              | vdb.xml                        | .obda                  | Strategi Kunci                         |
|----------------|------------------------------------------|--------------------------------|------------------------|----------------------------------------|
| ADD COLUMN     | `master_wilayah` + `kode_pos`            |  Tambah kolom di SELECT       |  Tambah property      | Dua fase berurutan                     |
| DROP COLUMN    | `eligibility` - `alasan`                 |  Hapus kolom di SELECT        |  Hapus property       | Dua fase berurutan                     |
| RENAME COLUMN  | `master_penduduk`: `status_hidup` → `kondisi_hidup` |  Tambah alias di view |  Tidak perlu diubah  | **Alias trick** - isolasi di Teiid     |
| MODIFY COLUMN  | `master_program_bansos`: `nominal` INT→BIGINT |  Hapus CAST yang redundan |  Tidak perlu diubah | View menyerap perubahan tipe           |
| DROP TABLE     | `master_wilayah` dihapus                 |  Hapus view + NULL placeholder |  Hapus mapping + disable property | Cascade alert wajib  |

---

> **Catatan Arsitektur:** Teiid sebagai lapisan tengah berfungsi sebagai **buffer/isolator** perubahan. Untuk SMO RENAME dan MODIFY, perubahan dapat diserap sepenuhnya di level Teiid menggunakan alias dan CAST, tanpa menyentuh layer OBDA sama sekali. Ini mengurangi risiko kerusakan mapping ontologi secara signifikan.