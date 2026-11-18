# Evaluasi manual ASCAM pada NPD dengan lapisan validasi (branch `feature/npd_v2`)

Dokumen ini adalah prosedur evaluasi manual untuk ke-21 skenario v01–v21. Setiap skenario
menguji satu aturan Plan di paper. Untuk setiap skenario tersedia:
- DDL yang dijalankan di sumber;
- apa yang dicek di console, artefak, dan Teiid;
- query SPARQL sebelum dan sesudah perubahan, beserta query SQL pembanding untuk menghitung
  jawaban yang benar langsung dari sumber.

Alasan setiap elemen lapisan validasi ada di [`npd-v2-modifications.md`](npd-v2-modifications.md).

**Status hasil yang diharapkan.** Keputusan dan aksi setiap skenario sudah diperiksa oleh
`test_case_study_npd_v2.py` atas artefak di repo. Artefak hasil setiap rencana yang dieksekusi
juga lolos `ontop validate`. Yang belum pernah dijalankan adalah Teiid, Monitor, dan data NPD
yang sebenarnya. Karena itu jumlah baris tidak saya tulis sebagai angka. Jumlah itu harus sama
dengan hasil query SQL pembanding, yang Anda jalankan sendiri. Hasil yang berbeda dari dokumen ini
adalah temuan dan perlu dicatat.

## 0. Persiapan

### 0.1 Satu skenario, satu lab bersih

Jalankan `./start.sh` sebelum **setiap** skenario. Skrip ini membuat ulang semua kontainer dan
volume, lalu memuat ulang dump NPD, jadi butuh beberapa menit. Ada dua alasan kenapa ini perlu.
Pertama, DDL kebalikan (misalnya `ADD COLUMN` setelah `DROP`) juga ditangkap Monitor sebagai
perubahan skema baru. Kedua, `DROP COLUMN` menghapus datanya, sehingga kolom yang ditambahkan
kembali tidak berisi data NPD lagi.

Lapisan validasi sudah ada di artefak yang di-commit. `start.sh` tidak perlu menjalankan
`validation.py`.

### 0.2 Fungsi bantu

Jalankan sekali di terminal dari root repo:

```bash
# SQL ke sumber. Nama NPD mixed-case: di PostgreSQL wajib diberi tanda kutip ganda.
pg() { docker exec datasources-pgsql psql -U postgres -d reporting -c "$1"; }
my() { docker exec datasources-mysql mysql -umysql -pmysql registry -e "$1"; }

# SQL ke Teiid lewat ODBC (port 35432). Argumen kedua = versi VDB (default 1).
tq() {
  PGPASSWORD="$(grep -v '^#' setup/ascam/knowledge/secrets/obdf_teiid_user_password | head -n1)" \
  psql "host=127.0.0.1 port=35432 dbname=npd.${2:-1} user=user1 sslmode=disable" -c "$1"
}

# SPARQL ke endpoint Ontop. Prefiks npdv dan npdx sudah disertakan.
sq() {
  curl -s -G http://localhost:8080/sparql -H 'Accept: application/sparql-results+json' \
    --data-urlencode "query=PREFIX npdv: <http://sws.ifi.uio.no/vocab/npd-v2#>
PREFIX npdx: <https://github.com/rahilaode/data-federation-dev/npd-validation#>
$1" | python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    rows = json.loads(raw)["results"]["bindings"]
except Exception:
    print("GAGAL (bukan hasil SPARQL):", raw[:300]); sys.exit(1)
for r in rows[:20]:
    print(" | ".join(v["value"] for v in r.values()))
print(f"({len(rows)} baris)")'
}

state() { cat setup/vkg-system/slots/state.json; }
```

Nama database Teiid `npd.N` dipakai karena Teiid menerima versi VDB sebagai bagian dari nama VDB
untuk akses ODBC (Teiid Documents, *VDB Versioning*, hlm. 62). Selalu cek versi aktif dengan
`state()` sebelum memakai `tq`.

Hampir semua query SPARQL di bawah memakai `COUNT`, karena tabel NPD berisi ratusan sampai ribuan
baris. Setiap `COUNT` punya query SQL pembanding. Kedua angka harus sama.

### 0.3 Tempat melihat hasil kerja ASCAM

| Yang dicek | Di mana |
|---|---|
| Event DDL tertangkap | Console http://127.0.0.1:18400 → *Schema changes*. Login `admin`; password di `setup/ascam/knowledge/secrets/ui_admin_password` |
| Keputusan, pola, alasan, aksi | *Plans and executions* (detail plan) |
| Notifikasi | *Notifications* |
| Usulan ADD yang menunggu persetujuan | *Approvals*. Pratinjau di form menyebut apakah property mendapat `rdfs:domain` |
| Mapping dan ontologi aktif | `git diff setup/vkg-system/config/`. Folder ini mencerminkan instance Ontop yang aktif. Tambahan ASCAM ada di blok `# ascam:begin …`; penghapusan POM mengubah triples map-nya langsung |
| Σ di Teiid | `tq "…" N` |
| Versi VDB dan instance aktif | `state()` |

### 0.4 Alur pemeriksaan untuk setiap skenario

1. `./start.sh`, lalu `state()`. Hasilnya harus blue, versi 1.
2. Jalankan query SPARQL **sebelum** dan query SQL pembandingnya. Catat keduanya.
3. Jalankan DDL. Untuk MySQL (`my`), tunggu sekitar 10 detik, karena monitor MySQL membandingkan
   snapshot kolom setiap 10 detik.
4. Di console, cek event, keputusan, pola, alasan, dan aksi.
5. Kalau keputusannya `hitl`, setujui di *Approvals* dengan usulan bawaan. Lalu isi kolom baru
   dengan `UPDATE` yang tertulis di skenario. `UPDATE` bukan DDL, jadi tidak ditangkap Monitor.
6. Kalau ada eksekusi, tunggu sampai statusnya selesai. Lalu cek:
   - `state()`: versi naik ke 2 dan warna berganti;
   - `git diff setup/vkg-system/config/`;
   - Σ dengan `tq … 2`.
7. Jalankan query SPARQL **sesudah** dan cocokkan dengan yang diharapkan.
8. Untuk skenario `notify`, pastikan `state()` tetap versi 1 dan `git diff` kosong.

> [GAMBAR] Untuk Bab IV, simpan tangkapan layar detail plan (keputusan, alasan, aksi) setiap
> skenario. Untuk skenario `hitl`, simpan juga form *Approvals*.

---

## 1. Contoh Preliminaries di NPD (v01–v04)

Elemen yang dipakai:
- tabel `registry.company`;
- view `services.v_current_operator`:
  `SELECT cmpNpdidCompany, cmpOrgNumberBrReg AS orgNumber FROM company WHERE cmpLicenceOperCurrent = 'Y'`;
- triples map `Validation:CurrentOperator` dengan a₁ (`npdx:CurrentOperator`) dan a₂ (`npdx:orgNumber`).

Query yang dipakai di keempat skenario:

| Nama | SPARQL | SQL pembanding (`my "…"`) |
|---|---|---|
| OPERATOR | `SELECT (COUNT(DISTINCT ?c) AS ?n) WHERE { ?c a npdx:CurrentOperator }` | `SELECT COUNT(*) FROM company WHERE cmpLicenceOperCurrent = 'Y'` |
| ORGNUM | `SELECT (COUNT(*) AS ?n) WHERE { ?c a npdx:CurrentOperator ; npdx:orgNumber ?o }` | `SELECT COUNT(*) FROM company WHERE cmpLicenceOperCurrent = 'Y' AND cmpOrgNumberBrReg IS NOT NULL` |
| ORGNUM_LIST | `SELECT ?c ?o WHERE { ?c npdx:orgNumber ?o } ORDER BY ?c LIMIT 10` | `SELECT cmpNpdidCompany, cmpOrgNumberBrReg FROM company WHERE cmpLicenceOperCurrent = 'Y' AND cmpOrgNumberBrReg IS NOT NULL ORDER BY 1 LIMIT 10` (urutan IRI dan angka bisa berbeda; cocokkan isinya) |
| COMPANY | `SELECT (COUNT(DISTINCT ?c) AS ?n) WHERE { ?c a npdv:Company }` | `SELECT COUNT(*) FROM company` |
| WELLBORE | `SELECT (COUNT(DISTINCT ?w) AS ?n) WHERE { ?w a npdv:Wellbore }` | (kontrol, cukup dicatat) |

### v01. Drop `company.cmpOrgNumberBrReg` (Example 2, P-002)

```bash
my 'ALTER TABLE company DROP COLUMN cmpOrgNumberBrReg'
```

**Yang dicek**
- Plan: `auto`, `P-002`, model `registry`. Ada empat aksi, berurutan:
  1. `drop_column` company.cmpOrgNumberBrReg;
  2. `alter_view` v_current_operator dengan body
     `SELECT "cmpNpdidCompany" FROM "registry"."company" WHERE "cmpLicenceOperCurrent" = 'Y'`.
     WHERE harus tetap ada;
  3. `remove_predicate_object_map` `npdx:orgNumber` pada `Validation:CurrentOperator`, object
     `"orgNumber"`;
  4. `deprecate_property npdx:orgNumber`.

  Inilah W = {orgNumber}, ℳ_c = {a₂}, dan orphaned di paper. Tidak boleh ada aksi pada map NPD,
  karena tidak ada map NPD yang membaca kolom ini.
- `state()`: versi 2.
- `tq "SELECT * FROM services.v_current_operator LIMIT 3" 2` hanya mengembalikan `cmpNpdidCompany`.
- `git diff setup/vkg-system/config/`:
  - di `mapping.ttl`, `Validation:CurrentOperator` masih punya `rr:class npdx:CurrentOperator`
    tetapi tidak punya POM `npdx:orgNumber`;
  - di `ontology_file.ttl`, ada blok `# ascam:begin deprecate …npd-validation#orgNumber`.

**SPARQL**

| Query | Sebelum | Sesudah |
|---|---|---|
| ORGNUM | = SQL ORGNUM | 0, tanpa error |
| OPERATOR | = SQL OPERATOR | sama dengan sebelum (a₁ tetap) |
| COMPANY | = SQL COMPANY | sama |

### v02. Rename `company.cmpOrgNumberBrReg → cmpOrgNumber` (Example 3 (a), P-003)

```bash
my 'ALTER TABLE company RENAME COLUMN cmpOrgNumberBrReg TO cmpOrgNumber'
```

**Yang dicek**
- Plan: `auto`, `P-003`. Hanya ada satu aksi: `set_name_in_source` company.cmpOrgNumberBrReg →
  `cmpOrgNumber`. Tidak ada aksi pada view, mapping, atau ontologi. Inilah alasan repair (a)
  minimal.
- `state()`: versi 2.
- `tq "SELECT \"Name\",\"NameInSource\" FROM SYS.Columns WHERE \"SchemaName\"='registry' AND \"TableName\"='company'" 2`:
  kolom Teiid tetap bernama `cmpOrgNumberBrReg`, sedangkan NameInSource-nya `cmpOrgNumber`.
- `git diff setup/vkg-system/config/`: kosong.

**SPARQL**

| Query | Sebelum | Sesudah |
|---|---|---|
| ORGNUM_LIST | 10 baris | 10 baris yang **identik** |
| ORGNUM | = SQL ORGNUM | sama. Sesudah rename, SQL pembandingnya memakai `cmpOrgNumber` |

### v03. Drop `company.cmpLicenceOperCurrent` (kolom di WHERE)

```bash
my 'ALTER TABLE company DROP COLUMN cmpLicenceOperCurrent'
```

**Yang dicek**
- Plan: `notify`, tanpa aksi.
- Alasan menyebut:
  - `Validation:CurrentOperator … sql_predicate`;
  - Mapping:00414 dan 00415 (`sql_predicate`, karena map NPD memakai kolom ini di WHERE);
  - `used in WHERE/JOIN of view services.v_current_operator` (weakest link predicate).
- `state()` tetap versi 1. `git diff` kosong.

**SPARQL**

| Query | Sesudah | Alasan |
|---|---|---|
| OPERATOR | **gagal** | Σ masih mendeklarasikan kolom, tetapi sumbernya sudah tidak punya |
| `SELECT (COUNT(*) AS ?n) WHERE { ?c npdv:isCurrentLicenceOperator ?b }` | **gagal** | Map NPD 00414/00415 memakai kolom di WHERE |
| COMPANY | sama dengan sebelum | Kontrol: map `npdv:Company` tidak membaca kolom ini |

### v04. Drop `company.cmpNpdidCompany` (key dan IRI template)

```bash
my 'ALTER TABLE company DROP COLUMN cmpNpdidCompany'
```

**Yang dicek**
- Plan: `notify`, tanpa aksi.
- Alasan menyebut `part of the primary key` dan `iri_template`. Map yang disebut mencakup
  `Validation:CurrentOperator` dan puluhan map NPD atas `company`.
- `state()` tetap versi 1.

**SPARQL**

| Query | Sesudah |
|---|---|
| COMPANY | **gagal** |
| OPERATOR | **gagal** |
| WELLBORE | sama dengan sebelum (kontrol di sumber lain) |

---

## 2. Kondisi notifikasi lain di Tabel 1 (v05–v11)

Semua skenario di bagian ini harus memberi `notify` tanpa aksi. Akibatnya:
- `state()` tetap versi 1;
- `git diff setup/vkg-system/config/` kosong;
- Σ tidak berubah, sehingga query yang melewati kolom yang berubah gagal sampai ada perbaikan
  manual.

| Kode | DDL | Kata kunci alasan | Query yang harus gagal | Kontrol yang harus tetap jalan |
|---|---|---|---|---|
| v05 | `my 'ALTER TABLE company RENAME COLUMN cmpGroup TO cmpGroupName'` | `already used`, `cmpGroupName` | `SELECT (COUNT(*) AS ?n) WHERE { ?c npdv:companyGroup ?g }` | COMPANY |
| v06 | `pg 'ALTER TABLE public."wellbore_npdid_overview" DROP COLUMN "wlbWell"'` | `iri_template` (tanpa `primary key`) | `SELECT (COUNT(DISTINCT ?w) AS ?n) WHERE { ?w a npdv:Well }` | COMPANY |
| v07 | `pg 'ALTER TABLE public."wellbore_core" DROP COLUMN "wlbCoreIntervalBottom"'` | `expression`, `v_wellbore_core_ft` | `SELECT (COUNT(*) AS ?n) WHERE { ?c npdv:coreIntervalBottom ?v }` | COMPANY |
| v08 | `pg 'ALTER TABLE public."wlbPoint" DROP COLUMN "wlbField"'` | `v_point_field`, `lose all` | (lihat catatan) | `SELECT (COUNT(*) AS ?n) WHERE { ?w npdx:drillingFacility ?f }` |
| v09 | `my 'ALTER TABLE pipLine DROP COLUMN pipNameCurrentOperator'` | `v_pipeline_operator`, `inline` | `SELECT (COUNT(*) AS ?n) WHERE { ?p npdx:currentOperatorName ?o }` | COMPANY |
| v10 | `my 'ALTER TABLE field_description DROP COLUMN fldDescriptionText'` | `materialized`, `v_field_text_cache` | (lihat catatan) | COMPANY |
| v11 | `my 'ALTER TABLE field_activity_status_hst ADD COLUMN fldStatusNote VARCHAR(100)'` | `v_field_status`, `UNION` | tidak ada | `tq "SELECT COUNT(*) FROM services.v_field_status"` dan COMPANY |

Catatan per skenario:

- **v05.** Sesudah rename, kolom Teiid `cmpGroup` gagal karena sumbernya sudah tidak punya kolom
  itu. Sebaliknya, kolom Teiid `cmpGroupName` sekarang menemukan kolom sumbernya.
  `tq "SELECT \"cmpGroupName\" FROM registry.company LIMIT 3"` seharusnya berhasil, padahal sebelum
  DDL query yang sama gagal. Kalau ASCAM mengubah NameInSource `cmpGroup` menjadi `cmpGroupName`,
  dua kolom Teiid akan membaca kolom sumber yang sama. Inilah alasan notifikasinya.
- **v06.** `wlbWell` bukan bagian primary key (`wlbNpdidWellbore`), tetapi dipakai template IRI
  `npdv:Well`. Ini membedakan baris "IRI template" dari baris "primary key" di Tabel 1.
- **v08.** Tidak ada mapping yang membaca `v_point_field`, jadi SPARQL tidak terpengaruh.
  Bukti dampaknya ada di Teiid: `tq "SELECT * FROM services.v_point_field LIMIT 3"` gagal sesudah
  DDL. Kontrolnya `npdx:drillingFacility`, yang membaca tabel yang sama tetapi kolom lain.
- **v09.** Kolom inline tidak bisa diubah dengan `ALTER VIEW` (Teiid Documents, hlm. 357).
- **v10.** Jalankan query `SELECT (COUNT(*) AS ?n) WHERE { ?f npdx:descriptionText ?t }`
  **sebelum** DDL. Query ini memuat cache materialisasi internal (hlm. 129). Sesudah DDL, query yang
  sama kemungkinan masih menjawab dari cache yang sudah basi. Ini menunjukkan kenapa view
  materialized tidak boleh diadaptasi otomatis: datanya tidak lagi mencerminkan sumber. Lalu
  jalankan `tq "CALL SYSADMIN.refreshMatView(viewname=>'services.v_field_text_cache', invalidate=>true)"`.
  Refresh ini seharusnya gagal karena kolom sumbernya sudah tidak ada. Catat hasil keduanya.
  Perilaku ini belum pernah diuji.
- **v11.** Di sumber, kolomnya bertambah. Tetapi Σ tidak berubah, jadi `v_field_status` tetap
  bekerja dengan tujuh kolom. Kalau ASCAM menambahkan kolom itu ke foreign table, cabang pertama
  (`SELECT *`) akan punya delapan kolom, sedangkan cabang kedua tujuh. Lihat temuan 6.1 di dokumen
  modifikasi tentang kenapa paper perlu menyebut syarat "a branch that does not select *".

---

## 3. P-002 lainnya (v12–v15)

### v12. Drop `wlbPoint.wlbProductionFacility` (hanya rw_c)

```bash
pg 'ALTER TABLE public."wlbPoint" DROP COLUMN "wlbProductionFacility"'
```

**Yang dicek**
- Plan: `auto`, `P-002`. Hanya dua aksi: `drop_column` dan `rewrite_logical_table` pada
  `Validation:PointFacility`. Tidak ada POM yang dihapus dan tidak ada deprecation.
- Di `mapping.ttl`, query `Validation:PointFacility` tidak lagi menyebut `wlbProductionFacility`.

**SPARQL**

| Query | SQL pembanding (`pg "…"`) | Sebelum = sesudah |
|---|---|---|
| `SELECT (COUNT(*) AS ?n) WHERE { ?w npdx:drillingFacility ?f }` | `SELECT COUNT(*) FROM (SELECT "wlbNpdidWellbore","wlbDrillingFacility" FROM public."wlbPoint" WHERE "wlbDrillingFacility" IS NOT NULL UNION SELECT "wlbNpdidWellbore","wlbDrillingFacility" FROM public."wellbore_shallow_all" WHERE "wlbDrillingFacility" IS NOT NULL) x` | jumlah **sama** |

### v13. Drop `wlbPoint.wlbDrillingFacility` (property tetap diisi)

```bash
pg 'ALTER TABLE public."wlbPoint" DROP COLUMN "wlbDrillingFacility"'
```

**Yang dicek**
- Plan: `auto`, `P-002`. Aksinya:
  - `drop_column`;
  - `rewrite_logical_table` dan `remove_predicate_object_map npdx:drillingFacility`, keduanya pada
    `Validation:PointFacility`;
  - `keep_property npdx:drillingFacility`. Tidak boleh ada `deprecate_property`.
- Di `ontology_file.ttl` tidak ada blok deprecate.

**SPARQL**

| Query | Sebelum | Sesudah |
|---|---|---|
| `SELECT (COUNT(*) AS ?n) WHERE { ?w npdx:drillingFacility ?f }` | = SQL v12 | = `pg 'SELECT COUNT(*) FROM (SELECT DISTINCT "wlbNpdidWellbore","wlbDrillingFacility" FROM public."wellbore_shallow_all" WHERE "wlbDrillingFacility" IS NOT NULL) x'`, yaitu lebih sedikit tetapi **tidak** nol |

### v14. Drop `wellbore_mud.wlbMudViscosityAtMD` (view pass-through, A007)

```bash
pg 'ALTER TABLE public."wellbore_mud" DROP COLUMN "wlbMudViscosityAtMD"'
```

**Yang dicek**
- Plan: `auto`, `P-002`. Aksinya:
  - `drop_column`;
  - `alter_view v_wellbore_mud` tanpa kolom itu;
  - `rewrite_logical_table` dan `remove_predicate_object_map npdv:mudViscosity` pada
    Mapping:00346;
  - `deprecate_property npdv:mudViscosity`.

**SPARQL**

| Query | Sebelum | Sesudah |
|---|---|---|
| `SELECT (COUNT(*) AS ?n) WHERE { ?m npdv:mudViscosity ?v }` | = `pg 'SELECT COUNT(*) FROM public."wellbore_mud"'` | 0, tanpa error |
| `SELECT (COUNT(*) AS ?n) WHERE { ?m npdv:mudWeight ?v }` | sama dengan baris di atas | **sama** (kolom lain di view yang sama) |

### v15. Drop `field_investment_yearly.prfInvestmentsMillNOK` (alias di view, A008)

```bash
my 'ALTER TABLE field_investment_yearly DROP COLUMN prfInvestmentsMillNOK'
```

**Yang dicek**
- Plan: `auto`, `P-002`. Aksinya:
  - `alter_view v_field_investment` (tanpa `investmentMillNOK`);
  - `remove_predicate_object_map npdv:investmentNOK` dengan object `"investmentMillNOK"`, yaitu
    nama alias di view;
  - **tanpa** `rewrite_logical_table`, karena map membaca view dengan `SELECT *`;
  - `deprecate_property npdv:investmentNOK`.

**SPARQL**

| Query | Sebelum | Sesudah |
|---|---|---|
| `SELECT (COUNT(*) AS ?n) WHERE { ?f npdv:investmentNOK ?v }` | = `my 'SELECT COUNT(*) FROM field_investment_yearly WHERE prfInvestmentsMillNOK IS NOT NULL'` | 0, tanpa error |
| COMPANY | | sama |

---

## 4. P-003 di PostgreSQL (v16)

### v16. Rename `wellbore_exploration_all.wlbDrillingOperator → wlbDrillingCompany` (A003)

```bash
pg 'ALTER TABLE public."wellbore_exploration_all" RENAME COLUMN "wlbDrillingOperator" TO "wlbDrillingCompany"'
```

**Yang dicek**
- Plan: `auto`, `P-003`, satu aksi `set_name_in_source` → `wlbDrillingCompany`.
- `tq "SELECT \"Name\",\"NameInSource\" FROM SYS.Columns WHERE \"SchemaName\"='reporting' AND \"TableName\"='wellbore_exploration_all' AND \"Name\"='wlbDrillingOperator'" 2`:
  NameInSource bernilai `"wlbDrillingCompany"`, dengan tanda kutip, karena nama PostgreSQL ini
  mixed-case (ADR-0029).
- `git diff setup/vkg-system/config/`: kosong.

**SPARQL**

| Query | Sebelum = sesudah |
|---|---|
| `SELECT (COUNT(*) AS ?n) WHERE { ?w npdv:drillingOperatorCompany ?c }` | jumlah **sama**. Kolom ini menghubungkan sumur (PostgreSQL) dengan perusahaan (MySQL) |
| `SELECT ?w ?c WHERE { ?w npdv:drillingOperatorCompany ?c } ORDER BY ?w LIMIT 10` | 10 baris **identik** |

---

## 5. P-001 (v17–v21)

Semua ADD yang diadaptasi diputuskan `hitl`, jadi plan menunggu di *Approvals*. Sebelum
disetujui, `state()` masih versi 1 dan `git diff` masih kosong. Setujui dengan usulan bawaan,
lalu jalankan `UPDATE` pengisi data. Tanpa `UPDATE`, kolom baru bernilai NULL dan property
barunya menjawab 0 baris.

### v17. Add `wellbore_npdid_overview.wlbEntryYear INTEGER` (domain aman, reuse)

```bash
pg 'ALTER TABLE public."wellbore_npdid_overview" ADD COLUMN "wlbEntryYear" INTEGER'
# setelah disetujui: tahun entry dari data eksplorasi NPD
pg 'UPDATE public."wellbore_npdid_overview" o SET "wlbEntryYear" = EXTRACT(YEAR FROM e."wlbEntryDate")::int FROM public."wellbore_exploration_all" e WHERE e."wlbNpdidWellbore" = o."wlbNpdidWellbore"'
```

**Yang dicek**
- Sebelum DDL, `ontology_file.ttl` punya blok `# ascam:begin deprecate …npd-v2#wlbEntryYear`.
- Plan: `hitl`, `P-001`.
  - Kelas yang ditawarkan hanya `npdx:OverviewWellbore`. Usulan IRI `npdv:wlbEntryYear`.
  - Alasan menyebut `is deprecated` dan `withdraws the deprecation`, dan **tidak** menyebut
    `selects *`. View `v_wellbore_overview` adalah `SELECT *` biasa dan bukan kondisi notifikasi.
  - Ada dua `add_predicate_object_map`, pada `Validation:OverviewWellbore` dan
    `Validation:OverviewWellboreType`. `OverviewWellboreType` tidak berkelas, tetapi template-nya
    sama dengan map berkelas yang membaca seluruh tabel (R2).
  - `add_datatype_property` dengan domain `npdx:OverviewWellbore`. Pratinjau di *Approvals*
    menulis "The property gets rdfs:domain OverviewWellbore".
- Sesudah disetujui:
  - `state()` versi 2;
  - `tq "SELECT \"wlbEntryYear\" FROM services.v_wellbore_overview LIMIT 3" 2` berhasil, karena
    view `SELECT *` meneruskan kolom baru;
  - di `ontology_file.ttl`, blok deprecate diganti `# ascam:begin reactivate …wlbEntryYear`, dan
    deklarasinya (domain, range) tidak diduplikasi;
  - di `mapping.ttl`, ada dua blok POM dengan `rr:column "\"wlbEntryYear\""` (ber-delimiter,
    ADR-0034).

**SPARQL**

| Query | SQL pembanding (`pg "…"`) | Sebelum | Sesudah |
|---|---|---|---|
| `SELECT (COUNT(*) AS ?n) WHERE { ?w npdv:wlbEntryYear ?y }` | `SELECT COUNT(*) FROM public."wellbore_npdid_overview" WHERE "wlbEntryYear" IS NOT NULL` | 0 | = SQL |
| `SELECT (COUNT(DISTINCT ?w) AS ?n) WHERE { ?w a npdx:OverviewWellbore }` | `SELECT COUNT(*) FROM public."wellbore_npdid_overview"` | = SQL | **sama**: domain aman, jadi tidak ada anggota kelas baru |

### v18. Add `facility_moveable.fclStatus VARCHAR(20)` (domain tidak aman, IRI baru)

```bash
my 'ALTER TABLE facility_moveable ADD COLUMN fclStatus VARCHAR(20)'
# setelah disetujui:
my "UPDATE facility_moveable SET fclStatus = 'IN SERVICE'"
```

**Yang dicek**
- Plan: `hitl`, `P-001`.
  - Kelas yang ditawarkan: `npdx:ForeignFlaggedFacility` dan `npdx:MoveableFacility`. Usulannya
    `ForeignFlaggedFacility`.
  - `safe_domains` kosong. Alasan menyebut `gets no rdfs:domain` dan `Validation:FacilityRecord`.
  - Alasan juga menyebut bahwa `npdv:fclStatus` sudah ada dengan domain lain, dan bahwa policy
    memakai IRI `npdv:fclStatus_ForeignFlaggedFacility` (ADR-0033, "Otherwise ASCAM proposes
    another name").
  - POM ditambahkan ke `ForeignFlaggedFacility` dan `FacilityRecord`, tetapi **tidak** ke
    `MoveableFacility`. Inilah X_C.
  - Pratinjau di *Approvals* menulis "The property gets **no rdfs:domain**".
- Sesudah disetujui:
  - blok property `npdv:fclStatus_ForeignFlaggedFacility` di ontologi **tanpa** `rdfs:domain`;
  - `npdv:fclStatus` tetap deprecated.

**SPARQL**

| Query | SQL pembanding (`my "…"`) | Sebelum | Sesudah |
|---|---|---|---|
| `SELECT (COUNT(*) AS ?n) WHERE { ?s npdv:fclStatus_ForeignFlaggedFacility ?v }` | `SELECT (SELECT COUNT(*) FROM facility_moveable WHERE fclNationName <> 'NORWAY') + (SELECT COUNT(*) FROM facility_moveable)` | 0 | = SQL (subjek `facility/…` yang berbendera asing ditambah semua subjek `record/facility/…`) |
| `SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE { ?s a npdx:ForeignFlaggedFacility }` | `SELECT COUNT(*) FROM facility_moveable WHERE fclNationName <> 'NORWAY'` | = SQL | **sama**. Ini pemeriksaan utama ADR-0033: subjek `record/…` tidak boleh menjadi anggota kelas |
| `SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE { ?s a npdx:MoveableFacility }` | `SELECT COUNT(*) FROM facility_moveable` | = SQL | sama |

> [GAMBAR] Bandingkan form *Approvals* v17 dan v18: yang satu menulis "gets rdfs:domain",
> yang lain "gets no rdfs:domain". Gambar ini menjelaskan aturan *safe* di P-001.

### v19. Add `wellbore_mud.wlbMudPh` (X = ∅, map baru, A001)

```bash
pg 'ALTER TABLE public."wellbore_mud" ADD COLUMN "wlbMudPh" NUMERIC(6,2)'
# setelah disetujui:
pg 'UPDATE public."wellbore_mud" SET "wlbMudPh" = 8.5'
```

**Yang dicek**
- Plan: `hitl`. Tidak ada `add_predicate_object_map`. Yang ada `add_triples_map` dengan query
  `SELECT "wellbore_mud_id", "wlbNpdidWellbore", "wlbMudPh" FROM "reporting"."wellbore_mud" AS "wellbore_mud"`
  dan template dari map berkelas `npdv:WellboreDrillingMudSample`.
- Domain `npdv:WellboreDrillingMudSample` aman, karena map baru menyalin query dan template map
  berkelas tersebut.

**SPARQL**

| Query | SQL pembanding | Sesudah |
|---|---|---|
| `SELECT (COUNT(*) AS ?n) WHERE { ?m npdv:wlbMudPh ?p }` | `pg 'SELECT COUNT(*) FROM public."wellbore_mud"'` | = SQL |
| `SELECT (COUNT(DISTINCT ?m) AS ?n) WHERE { ?m a npdv:WellboreDrillingMudSample }` | | sama dengan sebelum |

### v20. Add `company.cmpCountryCode` (X = ∅ di MySQL, A004)

```bash
my 'ALTER TABLE company ADD COLUMN cmpCountryCode VARCHAR(2)'
# setelah disetujui:
my "UPDATE company SET cmpCountryCode = 'NO'"
```

**Yang dicek:** plan `hitl`, dengan `add_triples_map`
`SELECT "cmpNpdidCompany", "cmpCountryCode" FROM "registry"."company" AS "company"` dan domain
`npdv:Company`.

**SPARQL**

| Query | Sesudah |
|---|---|
| `SELECT (COUNT(*) AS ?n) WHERE { ?c npdv:cmpCountryCode ?k }` | = SQL COMPANY |
| COMPANY | sama dengan sebelum |

### v21. Add `field_description.fldNote` (X_C = ∅, hanya Σ)

```bash
my 'ALTER TABLE field_description ADD COLUMN fldNote VARCHAR(20)'
```

**Yang dicek**
- Plan: `hitl`, hanya `add_column`. Alasan menyebut `only the VDB column`.
- Sesudah disetujui:
  - `state()` versi 2;
  - `tq "SELECT \"Name\" FROM SYS.Columns WHERE \"SchemaName\"='registry' AND \"TableName\"='field_description'" 2`
    memuat `fldNote`;
  - `git diff setup/vkg-system/config/` tetap kosong.
- SPARQL: tidak ada property baru. Kontrol COMPANY tetap sama.

---

## 6. Yang tidak diuji pada NPD

Tiga kondisi di baris "all" Tabel 1 tidak punya skenario NPD: model multi-source, trigger atau
procedure atas τ̂, dan logical table yang tidak bisa di-parse. Alasannya ada di bagian 5 dokumen
modifikasi. Ketiganya diperiksa oleh tes unit Knowledge (`test_impact.py`). Di Bab IV, tulis
sebagai batasan evaluasi.

## 7. Lembar catatan

| Kode | Event | Keputusan | Pola | Alasan sesuai | Aksi sesuai | Versi VDB | Config sesuai | SPARQL = SQL | Catatan |
|---|---|---|---|---|---|---|---|---|---|
| v01 | | | | | | | | | |
| v02 | | | | | | | | | |
| v03 | | | | | | | | | |
| v04 | | | | | | | | | |
| v05 | | | | | | | | | |
| v06 | | | | | | | | | |
| v07 | | | | | | | | | |
| v08 | | | | | | | | | |
| v09 | | | | | | | | | |
| v10 | | | | | | | | | |
| v11 | | | | | | | | | |
| v12 | | | | | | | | | |
| v13 | | | | | | | | | |
| v14 | | | | | | | | | |
| v15 | | | | | | | | | |
| v16 | | | | | | | | | |
| v17 | | | | | | | | | |
| v18 | | | | | | | | | |
| v19 | | | | | | | | | |
| v20 | | | | | | | | | |
| v21 | | | | | | | | | |

## 8. Catatan untuk Bab IV

- **Kelompokkan hasil menurut struktur Plan di paper.** Urutannya: contoh Preliminaries (v01–v04),
  kondisi notifikasi (v05–v11), P-002 (v12–v15), P-003 (v16), dan P-001 (v17–v21). Untuk setiap
  kelompok, sebutkan kalimat paper atau baris Tabel 1 yang diuji. Kolom "Bagian paper" di tabel 5
  dokumen modifikasi bisa dipakai langsung.
- **Laporkan dua jenis bukti secara terpisah.**
  - Kesesuaian keputusan dan aksi dengan aturan Plan: tes otomatis ditambah pengecekan Anda di
    console.
  - Kesesuaian jawaban SPARQL dengan isi sumber: angka SPARQL dibandingkan dengan angka SQL
    pembanding.

  Bukti kedua menunjukkan bahwa adaptasi tidak hanya valid secara sintaks, tetapi juga benar
  secara semantik.
- **Untuk skenario `notify`, kegagalan query adalah hasil yang diharapkan.** Hal ini menunjukkan
  dampak yang dicegah ASCAM, sekaligus batas otomasinya. Bandingkan dengan v01, yang dalam kasus
  serupa tetap menjawab tanpa error.
- **Laporkan temuan saat membangun lapisan validasi sebagai bagian dari hasil evaluasi**, bukan
  disembunyikan. Rinciannya ada di bagian 6 dokumen modifikasi:
  - dua cacat yang diperbaiki;
  - baris *add* Tabel 1 yang perlu dipersempit.
