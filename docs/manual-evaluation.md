# Evaluasi manual running example

Panduan ini untuk menjalankan ke-21 skenario running example satu per satu dengan tangan. Untuk
setiap skenario ada tiga hal: DDL yang dijalankan di sumber, apa yang dicek untuk memvalidasi
logika Plan, dan query SPARQL beserta jawaban yang diharapkan. Desain skenario dan alasannya ada
di [`running-example.md`](running-example.md). Dokumen ini hanya berisi langkah kerjanya.

Angka "sebelum" dan "sesudah" di bawah berasal dari dua sumber. Keputusan dan aksi Plan berasal
dari tes Knowledge (`test_case_study_example.py`). Jumlah baris SPARQL berasal dari uji Ontop
4.1.1 di atas salinan federasi di PostgreSQL. Teiid, Monitor, dan P-003 belum pernah dijalankan
di stack sungguhan. Jadi kalau hasil Anda berbeda, catat perbedaannya karena itu temuan.

## 0. Persiapan

### 0.1 Satu skenario, satu lab bersih

Jalankan `./start.sh` sebelum **setiap** skenario. Mengembalikan kolom dengan DDL kebalikan
(misalnya `ADD COLUMN` lagi setelah `DROP`) juga perubahan skema. Monitor akan menangkapnya dan
ASCAM akan membuat rencana baru, sehingga skenario berikutnya tidak lagi mulai dari keadaan dasar.

```bash
./start.sh            # sekitar beberapa menit; selesai kalau endpoint sudah menjawab
```

### 0.2 Fungsi bantu

Jalankan sekali di terminal dari root repo.

```bash
# SQL ke sumber
pg() { docker exec datasources-pgsql psql -U postgres -d reporting -c "$1"; }
my() { docker exec datasources-mysql mysql -umysql -pmysql registry -e "$1"; }

# SQL ke Teiid lewat ODBC (port 35432). Argumen kedua = versi VDB (default 1).
tq() {
  PGPASSWORD="$(grep -v '^#' setup/ascam/knowledge/secrets/obdf_teiid_user_password | head -n1)" \
  psql "host=127.0.0.1 port=35432 dbname=example.${2:-1} user=user1 sslmode=disable" -c "$1"
}

# SPARQL ke endpoint Ontop (lewat proxy blue-green). Mencetak baris lalu jumlahnya.
sq() {
  curl -s -G http://localhost:8080/sparql -H 'Accept: application/sparql-results+json' \
    --data-urlencode "query=PREFIX ex: <http://example.org/>
$1" | python3 -c '
import json, sys
raw = sys.stdin.read()
try:
    rows = json.loads(raw)["results"]["bindings"]
except Exception:
    print("GAGAL (bukan hasil SPARQL):", raw[:300]); sys.exit(1)
for r in rows:
    print(" | ".join(v["value"] for v in r.values()))
print(f"({len(rows)} baris)")'
}

# Versi VDB yang sedang dilayani Ontop dan warna instance yang aktif
state() { cat setup/vkg-system/slots/state.json; }
```

Nama database `example.N` dipakai karena Teiid menerima versi VDB sebagai bagian dari nama VDB
untuk akses ODBC (Teiid Documents, bagian *VDB Versioning*, hlm. 62). Tanpa versi, koneksi
diarahkan ke VDB menurut *connection type*-nya (default `BY_VERSION`), jadi belum tentu ke versi
terbaru. Karena itu selalu cek `state()` dulu, lalu pakai versi itu di `tq`.

### 0.3 Tempat melihat hasil kerja ASCAM

| Yang dicek | Di mana |
|---|---|
| Event DDL tertangkap | Console http://127.0.0.1:18400 → *Schema changes* |
| Keputusan (auto, hitl, notify), pola (P-001, P-002, P-003), alasan, aksi | *Plans and executions* (detail plan) |
| Notifikasi | *Notifications* |
| Usulan ADD yang menunggu persetujuan | *Approvals* |
| Langkah eksekusi dan statusnya | *Plans and executions* (detail eksekusi) |
| Mapping dan ontologi aktif | `git diff setup/vkg-system/config/` (folder ini selalu mencerminkan instance aktif). Perubahan dari ASCAM berada di antara `# ascam:begin …` dan `# ascam:end …`, kecuali penghapusan POM yang langsung mengubah triples map-nya |
| Σ di Teiid | `tq "…" N` |
| Versi VDB dan instance aktif | `state()` |

Login console memakai user `admin`. Password-nya ada di
`setup/ascam/knowledge/secrets/ui_admin_password`.

### 0.4 Alur pemeriksaan untuk setiap skenario

1. `./start.sh`. Cek `state()` (harus blue, versi 1).
2. Jalankan query SPARQL **sebelum**. Catat hasilnya dan cocokkan dengan kolom "sebelum".
3. Jalankan DDL. Untuk MySQL, tunggu sekitar 10 detik karena monitor MySQL membandingkan snapshot
   skema secara berkala.
4. Cek di console: event muncul, lalu keputusan, pola, alasan, dan aksi plan.
5. Kalau keputusannya `hitl`, setujui di *Approvals* dengan usulan bawaan. Lalu isi kolom baru
   dengan `UPDATE` yang tertulis di skenario. `UPDATE` bukan DDL, jadi tidak ditangkap monitor.
6. Kalau ada eksekusi, tunggu statusnya selesai. Cek `state()` (versi naik ke 2 dan warna
   berganti), `git diff setup/vkg-system/config/`, dan Σ dengan `tq`.
7. Jalankan query SPARQL **sesudah** dan cocokkan dengan kolom "sesudah".

### 0.5 Jawaban query di keadaan dasar

Pakai tabel ini untuk langkah 2. Semua angka sudah diuji, kecuali yang ditandai *.

| Nama | Query | Baris |
|---|---|---|
| ACTIVE_NAME | `SELECT * WHERE { ?p a ex:ActivePerson ; ex:name ?n }` | 2 (Ana, Budi) |
| ACTIVE | `SELECT * WHERE { ?p a ex:ActivePerson }` | 2 |
| PERSON | `SELECT * WHERE { ?p a ex:Person }` | 3 |
| NICKNAME | `SELECT * WHERE { ?s ex:nickname ?n }` | 5 (Nana, Bud, Ci dari S1; Wi, Ko dari S2) |
| PHONE | `SELECT * WHERE { ?s ex:phone ?x }` | 5 (0811-1, 0812-1, 0811-2 dari S1; 0855-1, 0855-2 dari S2) |
| EMAIL | `SELECT * WHERE { ?s ex:email ?e }` | 2 |
| CONTACT | `SELECT * WHERE { ?c a ex:Contact }` | 2 |
| NET_PRICE | `SELECT * WHERE { ?p ex:netPrice ?x }` | 2 (nilainya 9000 dan 25000) |
| CITY_NAME | `SELECT * WHERE { ?c a ex:City ; ex:cityName ?n }` | 2 |
| MEMBER_NAME | `SELECT * WHERE { ?m a ex:Member ; ex:fullName ?n }` | 2 (Dewi, Eko) |
| OWNER | `SELECT * WHERE { ?a ex:owner ?o }` | 4 (account/1, account/2, record/1, record/2) |
| ACCOUNT | `SELECT * WHERE { ?a a ex:Account }` | 2 |
| CUSTOMER | `SELECT * WHERE { ?c a ex:Customer }` | 2 |
| KIND | `SELECT * WHERE { ?e ex:kind ?k }` | 2 |
| STAFF_CITY | `SELECT * WHERE { ?s ex:city ?c }` | 2 |
| LABEL | `SELECT * WHERE { ?t ex:label ?l }` | 2 |
| NOTE_BODY, MEMO_BODY, DOC_BODY | `SELECT * WHERE { ?x ex:noteBody ?b }` (memoBody, docBody) | 2 masing-masing * |

Kontrol yang dijalankan di semua skenario adalah CITY_NAME dan MEMBER_NAME. Keduanya harus tetap
2 baris, kecuali di r13 yang memang mengubah tabel city.

---

## 1. P-002 (DROP yang diadaptasi)

### r01. Drop `person.first_name` (Example 1 di paper)

**DDL**
```bash
pg 'ALTER TABLE public.person DROP COLUMN first_name'
```

**Yang dicek**
- Plan: `auto`, `P-002`. Ada empat aksi, berurutan:
  1. `drop_column` src.person.first_name.
  2. `alter_view` v_active dengan body `SELECT "id" FROM "src"."person" WHERE "active" = TRUE`
     dan `removed_columns = [name]`. WHERE harus tetap ada.
  3. `remove_predicate_object_map` ex:name pada TM_ActivePerson (object `name`).
  4. `deprecate_property` ex:name.
- Ini adalah W = {v_active.name} dan ℳ_c = {a2} pada Example di paper. Assertion kelas (a1)
  tidak boleh tersentuh.
- `state()`: versi 2.
- `tq` versi 2:
  ```bash
  tq "SELECT \"Name\" FROM SYS.Columns WHERE \"SchemaName\"='src' AND \"TableName\"='person'" 2   # tanpa first_name
  tq "SELECT \"Name\" FROM SYS.Columns WHERE \"SchemaName\"='v' AND \"TableName\"='v_active'" 2   # hanya id
  ```
- Mapping: TM_ActivePerson masih ada, masih punya `rr:class ex:ActivePerson`, tetapi tidak punya
  POM ex:name. Logical table tetap `SELECT * FROM v.v_active` karena `SELECT *` tidak menyebut
  kolom.
- Ontologi: blok `# ascam:begin deprecate http://example.org/name` dengan
  `ex:name owl:deprecated true`.

**SPARQL**

| Query | Sebelum | Sesudah |
|---|---|---|
| ACTIVE_NAME | 2 | 0, tanpa error |
| ACTIVE | 2 | 2 (Ana dan Budi tetap ActivePerson) |
| NICKNAME | 5 | 5 |

Poin validasinya: query lama tetap jalan, jawabannya kosong karena datanya memang hilang, dan
kelas tidak ikut hilang.

### r06. Drop `contact.phone` (satu map mengisi ex:phone dari dua kolom)

**DDL**
```bash
pg 'ALTER TABLE public.contact DROP COLUMN phone'
```

**Yang dicek**
- Plan: `auto`, `P-002`. Aksinya:
  - `drop_column`.
  - `rewrite_logical_table` TM_Contact (kolom phone).
  - `remove_predicate_object_map` ex:phone pada TM_Contact dengan object `phone`.
  - `keep_property` ex:phone dengan alasan masih dipakai mapping lain.
- Tidak boleh ada `deprecate_property` (ADR-0031).
- Mapping: TM_Contact menjadi `SELECT id, phone2 FROM src.contact` dan hanya punya POM ex:phone
  dari `phone2`.
- Ontologi: tidak berubah.

**SPARQL**

| Query | Sebelum | Sesudah |
|---|---|---|
| PHONE | 5 | 3 (0812-1, 0855-1, 0855-2) |
| CONTACT | 2 | 2 |
| EMAIL | 2 | 2 |

### r07. Drop `contact.fax` (kolom hanya diproyeksikan)

**DDL**
```bash
pg 'ALTER TABLE public.contact DROP COLUMN fax'
```

**Yang dicek**
- Plan: `auto`, `P-002`. Aksinya hanya `drop_column` dan `rewrite_logical_table` TM_ContactEmail.
  Tidak ada POM yang dihapus dan tidak ada deprecation. Ini rw_c di paper.
- Mapping: TM_ContactEmail menjadi `SELECT id, email FROM src.contact`.

**SPARQL**

| Query | Sebelum | Sesudah |
|---|---|---|
| EMAIL | 2 | 2, isinya identik |
| PHONE | 5 | 5 |

### r08. Drop `person.nickname` (property juga diisi S2)

**DDL**
```bash
pg 'ALTER TABLE public.person DROP COLUMN nickname'
```

**Yang dicek**
- Plan: `auto`, `P-002`. Aksinya:
  - `drop_column`.
  - `rewrite_logical_table` TM_PersonNick.
  - `remove_predicate_object_map` ex:nickname pada TM_PersonNick.
  - `keep_property` ex:nickname.
- Mapping: TM_PersonNick menjadi `SELECT id FROM src.person` dan tetap punya `rr:class ex:Person`.
- Ontologi: tidak berubah. ex:nickname tidak di-deprecate karena TM_Member masih mengisinya.

**SPARQL**

| Query | Sebelum | Sesudah |
|---|---|---|
| NICKNAME | 5 | 2 (Wi, Ko dari S2) |
| PERSON | 3 | 3 |
| ACTIVE_NAME | 2 | 2 |

### r21. Drop `member.panggilan` di MySQL

**DDL**
```bash
my 'ALTER TABLE member DROP COLUMN panggilan'
```

**Yang dicek**
- Event datang dari sumber `registry`. Tunggu sekitar 10 detik.
- Plan: `auto`, `P-002`, model `reg`. Aksinya:
  - `drop_column`.
  - `rewrite_logical_table` TM_Member.
  - `remove_predicate_object_map` ex:nickname pada TM_Member dengan object `panggilan`.
  - `keep_property` ex:nickname.
- Mapping: query TM_Member tidak lagi menyebut `panggilan`. POM ex:fullName dan ex:phone tetap.

**SPARQL**

| Query | Sebelum | Sesudah |
|---|---|---|
| NICKNAME | 5 | 3 (Nana, Bud, Ci dari S1) |
| MEMBER_NAME | 2 | 2 |

---

## 2. P-003 (RENAME yang diadaptasi)

P-003 belum pernah diuji di luar tes Knowledge, karena perilakunya bergantung pada `NAMEINSOURCE`
di Teiid. Skenario ini yang paling penting untuk dicek di stack sungguhan.

### r02. Rename `person.first_name` → `given_name` (repair (a) di paper)

**DDL**
```bash
pg 'ALTER TABLE public.person RENAME COLUMN first_name TO given_name'
```

**Yang dicek**
- Plan: `auto`, `P-003`. Hanya ada satu aksi, `set_name_in_source` src.person.first_name →
  `given_name`. Tidak ada aksi pada mapping, ontologi, maupun view. Inilah kenapa repair (a)
  minimal: satu perubahan pada ĉ, berapa pun view dan mapping yang membacanya.
- `state()`: versi 2.
- `tq` versi 2:
  ```bash
  tq "SELECT \"Name\",\"NameInSource\" FROM SYS.Columns WHERE \"SchemaName\"='src' AND \"TableName\"='person'" 2
  ```
  Kolom Teiid tetap bernama `first_name`, sedangkan NameInSource-nya menunjuk `given_name`.
- `git diff setup/vkg-system/config/`: kosong.

**SPARQL**

| Query | Sebelum | Sesudah |
|---|---|---|
| ACTIVE_NAME | 2 (Ana, Budi) | 2, isinya identik |
| ACTIVE | 2 | 2 |

### r19. Rename `member.nama` → `nama_lengkap` di MySQL

**DDL**
```bash
my 'ALTER TABLE member RENAME COLUMN nama TO nama_lengkap'
```

**Yang dicek**
- Plan: `auto`, `P-003`, model `reg`, satu aksi `set_name_in_source` → `nama_lengkap`.
- `tq` versi 2:
  ```bash
  tq "SELECT \"Name\",\"NameInSource\" FROM SYS.Columns WHERE \"SchemaName\"='reg' AND \"TableName\"='member'" 2
  ```
- Mapping dan ontologi: tidak berubah.

**SPARQL**

| Query | Sebelum | Sesudah |
|---|---|---|
| MEMBER_NAME | 2 (Dewi, Eko) | 2, isinya identik |
| NICKNAME | 5 | 5 |

---

## 3. Notifikasi (DROP dan RENAME yang tidak diadaptasi)

Untuk semua skenario di bagian ini, yang dicek sama:
- Plan: `notify`, tanpa aksi. Notifikasi muncul di *Notifications*.
- Alasannya menyebut elemen yang benar. Kata kunci yang dicek tes tercantum di tiap skenario.
- Tidak ada eksekusi. `state()` tetap blue versi 1. `git diff setup/vkg-system/config/` kosong.
- Σ di Teiid tidak berubah: kolom yang di-drop masih terdaftar di `SYS.Columns` versi 1. Akibatnya
  Σ dan sumber sudah tidak sinkron. Query yang melewati kolom itu gagal sampai ada perbaikan
  manual. Ini perilaku yang diharapkan dari notifikasi, bukan kesalahan ASCAM.

Query "affected" harus **gagal** (fungsi `sq` mencetak `GAGAL`). Query kontrol harus tetap jalan.
Untuk query tetangga yang tidak disebut, cukup catat hasilnya. Teiid bisa saja membuang kolom yang
tidak dipakai dari subquery sehingga query itu tetap berhasil, dan perilaku ini belum diuji.

| Kode | DDL | Kata kunci alasan | Query yang harus gagal | Kontrol |
|---|---|---|---|---|
| r03 | `pg 'ALTER TABLE public.person DROP COLUMN active'` | `WHERE`, `v.v_active` (kolom dipakai predikat, weakest link) | ACTIVE_NAME | PERSON = 3, CITY_NAME, MEMBER_NAME |
| r04 | `pg 'ALTER TABLE public.person DROP COLUMN id'` | `primary key`, `iri_template` | `SELECT * WHERE { ?p a ex:Person ; ex:nickname ?n }` | CITY_NAME, MEMBER_NAME |
| r05 | `pg 'ALTER TABLE public.staff RENAME COLUMN city TO town'` | `already used`, `town_legacy` (c′ sudah dibaca kolom lain) | STAFF_CITY | `SELECT * WHERE { ?s a ex:Staff }`, CITY_NAME |
| r09 | `pg 'ALTER TABLE public.product DROP COLUMN discount'` | `expression`, `v.v_net_price` | NET_PRICE | CITY_NAME, MEMBER_NAME |
| r10 | `pg 'ALTER TABLE public.note DROP COLUMN body'` | `v.v_note_body`, `lose all` | `SELECT * WHERE { ?n ex:noteBody ?b }` | CITY_NAME, MEMBER_NAME |
| r11 | `pg 'ALTER TABLE public.memo DROP COLUMN body'` | `v.v_memo`, `inline` | `SELECT * WHERE { ?m ex:memoBody ?b }` | CITY_NAME, MEMBER_NAME |
| r12 | `pg 'ALTER TABLE public.doc DROP COLUMN body'` | `materialized`, `v.v_doc_cache` | `SELECT * WHERE { ?d ex:docBody ?b }` | CITY_NAME, MEMBER_NAME |

Catatan per skenario:
- **r05.** Sesudah rename, kolom Teiid `staff.town_legacy` (NameInSource `town`) justru menemukan
  kolom sumbernya. Kalau ASCAM mengubah NameInSource `city` menjadi `town`, dua kolom Teiid akan
  membaca satu kolom sumber yang sama. Itulah alasan notifikasinya. Kalau mau melihatnya:
  `tq "SELECT \"town_legacy\" FROM src.staff"` sesudah DDL seharusnya berhasil (belum diuji).
- **r12.** Pastikan dulu Teiid menerima `v_doc_cache` sebagai materialized view internal di
  keadaan dasar: `tq "SELECT * FROM v.v_doc_cache"` sebelum DDL harus mengembalikan 2 baris.
  Hal ini belum pernah diuji. Kalau gagal, catat sebagai temuan pada lab, bukan pada Plan.
- **r04.** Sebelum DDL, Ontop membangun IRI `ex:person/{id}`. Sesudah DDL, sumber tidak lagi
  punya `id`. Query ACTIVE_NAME (lewat v_active) juga akan gagal.

---

## 4. P-001 (ADD)

Semua ADD yang diadaptasi diputuskan `hitl`, jadi plan menunggu di *Approvals*. Langkahnya:
jalankan DDL, cek usulannya, setujui dengan usulan bawaan, lalu jalankan `UPDATE` pengisi data.
Tanpa `UPDATE` kolom barunya NULL dan query property baru menjawab 0 baris. Itu benar secara
semantik, tetapi tidak membuktikan apa-apa.

Sebelum disetujui, cek bahwa belum ada perubahan apa pun: `state()` masih versi 1 dan
`git diff setup/vkg-system/config/` kosong.

### r13. Add `city.population` (X ≠ ∅, `SELECT *` biasa bukan kondisi notifikasi)

**DDL dan data**
```bash
pg 'ALTER TABLE public.city ADD COLUMN population INTEGER'
# setelah disetujui:
pg 'UPDATE public.city SET population = id * 1000'
```

**Yang dicek**
- Plan: `hitl`, `P-001`. Usulannya:
  - IRI `ex:population`, domain `ex:City`, range `xsd:integer`.
  - Map yang terkena hanya TM_City.
- Alasan **tidak** menyebut `selects *`. View `v_city_all` (`SELECT * FROM src.city`) tidak memicu
  notifikasi karena bisa menerima kolom baru (ADR-0032).
- Aksinya:
  - `add_column` src.city.population INT.
  - `add_datatype_property` ex:population.
  - `add_predicate_object_map` pada TM_City.
- Sesudah disetujui:
  - `state()` versi 2.
  - Ontologi punya blok `# ascam:begin property http://example.org/population` (domain ex:City,
    range xsd:integer).
  - Mapping punya blok `# ascam:begin pom http://example.org/population on …TM_City` dengan
    `rr:column "population"`.
  - `tq "SELECT \"Name\" FROM SYS.Columns WHERE \"SchemaName\"='src' AND \"TableName\"='city'" 2`
    memuat population.
  - `tq "SELECT * FROM v.v_city_all" 2` juga memuat population (belum diuji).

**SPARQL**

| Query | Sebelum | Sesudah |
|---|---|---|
| `SELECT * WHERE { ?c ex:population ?x }` | 0 | 2 (1000, 2000) |
| CITY_NAME | 2 | 2 |
| MEMBER_NAME | 2 | 2 |

### r14. Add `account.status` (tabel dipetakan ke beberapa kelas)

**DDL dan data**
```bash
pg 'ALTER TABLE public.account ADD COLUMN status VARCHAR(20)'
# setelah disetujui:
pg "UPDATE public.account SET status = 'open'"
```

**Yang dicek**
- Plan: `hitl`, `P-001`. Alasannya menyebut `more than one class`.
- Pilihan kelas yang ditawarkan adalah ex:Account dan ex:Customer. Usulannya ex:Account.
- Ada dua aksi `add_predicate_object_map`, pada TM_Account dan TM_AccountRecord. Inilah
  X_C = {m ∈ X | 𝒞_m = ∅ atau C ∈ 𝒞_m}. TM_Customer tidak ikut karena kelasnya ex:Customer.
- `safe_domains` kosong, dan alasannya menyebut `gets no rdfs:domain` serta TM_AccountRecord
  (ADR-0033). Map itu tidak punya kelas dan membangun IRI `record/{id}`. Dengan
  `rdfs:domain ex:Account`, Ontop akan menyimpulkan bahwa setiap record adalah ex:Account.
- Di form *Approvals*, pratinjau menulis bahwa property ini **tidak** mendapat rdfs:domain.
- Sesudah disetujui: blok property ex:status tanpa `rdfs:domain` (hanya range xsd:string). Blok
  POM ada di dua map tersebut.

**SPARQL**

| Query | Sebelum | Sesudah |
|---|---|---|
| `SELECT * WHERE { ?a ex:status ?s }` | 0 | 4 (account/1, account/2, record/1, record/2) |
| `SELECT * WHERE { ?c a ex:Customer ; ex:status ?s }` | 0 | 0 |
| OWNER | 4 | 4 |
| CUSTOMER | 2 | 2 |
| ACCOUNT | 2 | 2 (query kelas lama tidak berubah) |

> **Kenapa ACCOUNT harus tetap 2.** Sebelum ADR-0033, P-001 menulis `ex:status rdfs:domain
> ex:Account`. Dalam uji Ontop, `?a a ex:Account` lalu naik dari 2 menjadi 4 karena `record/1`
> dan `record/2` disimpulkan sebagai ex:Account. Sekarang domain hanya ditulis bila setiap subjek
> yang mendapat property sudah anggota kelasnya. Kalau ACCOUNT menjadi 4 di stack Anda, aturan
> ADR-0033 tidak bekerja dan itu temuan.

### r15. Add `person.birth_date` (X = ∅, triples map baru)

**DDL dan data**
```bash
pg 'ALTER TABLE public.person ADD COLUMN birth_date DATE'
# setelah disetujui:
pg "UPDATE public.person SET birth_date = DATE '2000-01-01'"
```

**Yang dicek**
- Plan: `hitl`, `P-001`. Tidak ada `add_predicate_object_map`. Yang ada `add_triples_map` dengan:
  - IRI `ascam-person-birth_date`.
  - Query `SELECT id, nickname, "birth_date" FROM src.person`, turunan dari TM_PersonNick.
  - Template `http://example.org/person/{id}`, tanpa kelas.
  - Property ex:birthDate dengan domain ex:Person dan range xsd:date.
- Map baru dibuat karena logical table TM_PersonNick menyebut kolomnya satu per satu. Kolom baru
  tidak masuk ke map mana pun (X = ∅).
- Sesudah disetujui: blok `# ascam:begin triples_map …ascam-person-birth_date` ada di mapping.

**SPARQL**

| Query | Sebelum | Sesudah |
|---|---|---|
| `SELECT * WHERE { ?p ex:birthDate ?d }` | 0 | 3 |
| PERSON | 3 | 3 |
| NICKNAME | 5 | 5 |
| ACTIVE_NAME | 2 | 2 |

### r16. Add `audit.actor` (X_C = ∅, hanya Σ yang berubah)

**DDL dan data**
```bash
pg 'ALTER TABLE public.audit ADD COLUMN actor VARCHAR(20)'
# setelah disetujui:
pg "UPDATE public.audit SET actor = 'system'"
```

**Yang dicek**
- Plan: `hitl`, `P-001`. Aksinya hanya `add_column`. Alasannya menyebut `only the VDB column`.
- Tidak ada usulan IRI, karena tidak ada triples map yang membaca tabel audit.
- Sesudah disetujui:
  - `state()` versi 2.
  - `tq "SELECT * FROM src.audit" 2` memuat actor.
  - `git diff setup/vkg-system/config/` tetap kosong.

**SPARQL**

| Query | Sebelum | Sesudah |
|---|---|---|
| `SELECT * WHERE { ?a ex:actor ?x }` | 0 | 0 (memang tidak dipetakan) |
| PERSON | 3 | 3 |

### r17. Add `event.severity` (notifikasi: `SELECT *` di dalam UNION)

**DDL**
```bash
pg 'ALTER TABLE public.event ADD COLUMN severity VARCHAR(10)'
```

**Yang dicek**
- Plan: `notify`, tanpa aksi. Alasannya menyebut `v.v_events` dan `UNION` (ADR-0032).
- Kalau `severity` ditambahkan ke foreign table, cabang pertama UNION (`SELECT *`) akan punya tiga
  kolom, sedangkan cabang kedua tetap dua. View itu menjadi tidak valid. Karena itu tidak ada
  perubahan apa pun.
- `state()` tetap versi 1. Config tidak berubah.

**SPARQL**

| Query | Sebelum | Sesudah |
|---|---|---|
| KIND | 2 | 2 (query lama tetap jalan) |
| `SELECT * WHERE { ?e ex:severity ?s }` | 0 | 0 |

### r18. Add `tool.weight` (reuse property yang di-deprecate)

**DDL dan data**
```bash
pg 'ALTER TABLE public.tool ADD COLUMN weight NUMERIC(6,2)'
# setelah disetujui:
pg 'UPDATE public.tool SET weight = 1.5'
```

**Yang dicek**
- Sebelum DDL, ontologi punya blok `# ascam:begin deprecate http://example.org/weight`.
- Plan: `hitl`, `P-001`. Alasannya menyebut `deprecated` dan `reuses`. Usulannya IRI ex:weight
  (bukan IRI baru), domain ex:Tool, range xsd:decimal. Ini sama dengan domain dan range yang sudah
  ada.
- Aksinya `add_triples_map` `ascam-tool-weight` dengan query
  `SELECT id, label, "weight" FROM src.tool`.
- Sesudah disetujui:
  - Blok deprecate hilang, diganti blok `# ascam:begin reactivate http://example.org/weight`.
    Inilah "reuse withdraws any deprecation".
  - Deklarasi ex:weight (domain, range) tidak diduplikasi.

**SPARQL**

| Query | Sebelum | Sesudah |
|---|---|---|
| `SELECT * WHERE { ?t ex:weight ?w }` | 0 | 2 (1.5) |
| LABEL | 2 | 2 |

### r20. Add `member.email` di MySQL (property aktif ex:email dipakai ulang)

**DDL dan data**
```bash
my 'ALTER TABLE member ADD COLUMN email VARCHAR(50)'
# setelah disetujui:
my "UPDATE member SET email = 'member@example.org'"
```

**Yang dicek**
- Plan: `hitl`, `P-001`, model `reg`. Alasannya menyebut `already exists`, karena IRI usulan
  ex:email sudah ada di ontologi dan sudah diisi TM_ContactEmail.
- Aksinya `add_triples_map` `ascam-member-email` dengan query
  `SELECT member_id, nama, panggilan, phone, "email" FROM reg.member` dan template
  `http://example.org/member/{member_id}`.
- Sesudah disetujui, ontologi **tidak** mendapat domain baru untuk ex:email. Deklarasi lama tidak
  diubah karena property-nya sudah ada.

**SPARQL**

| Query | Sebelum | Sesudah |
|---|---|---|
| `SELECT * WHERE { ?m a ex:Member ; ex:email ?e }` | 0 | 2 |
| EMAIL | 2 | 4 (2 contact dan 2 member) |
| `SELECT * WHERE { ?m a ex:Member }` | 2 | 2 |
| CONTACT | 2 | 2 |

Bandingkan dengan r14. Karena ex:email tidak diberi domain, tidak ada kelas yang ikut berubah.

---

## 5. Lembar catatan

Salin tabel ini untuk mencatat hasil Anda.

| Kode | Event muncul | Keputusan | Pola | Alasan sesuai | Aksi sesuai | Versi VDB | Config sesuai | SPARQL sesuai | Catatan |
|---|---|---|---|---|---|---|---|---|---|
| r01 | | | | | | | | | |
| r02 | | | | | | | | | |
| r03 | | | | | | | | | |
| r04 | | | | | | | | | |
| r05 | | | | | | | | | |
| r06 | | | | | | | | | |
| r07 | | | | | | | | | |
| r08 | | | | | | | | | |
| r09 | | | | | | | | | |
| r10 | | | | | | | | | |
| r11 | | | | | | | | | |
| r12 | | | | | | | | | |
| r13 | | | | | | | | | |
| r14 | | | | | | | | | |
| r15 | | | | | | | | | |
| r16 | | | | | | | | | |
| r17 | | | | | | | | | |
| r18 | | | | | | | | | |
| r19 | | | | | | | | | |
| r20 | | | | | | | | | |
| r21 | | | | | | | | | |

## Rujukan

- Teiid Documents (berkas `teiid-documents.pdf` di project), bagian *VDB Versioning*, hlm. 62:
  versi VDB sebagai bagian dari nama VDB untuk akses ODBC, dan *connection type* `BY_VERSION`.
- `docs/running-example.md`: desain data, VDB, mapping, ontologi, dan skenario.
- `docs/design-decisions.md`: ADR-0022 (blue-green), ADR-0027 (P-001 membuat triples map baru),
  ADR-0031 (P-002 hanya menghapus assertion yang membaca kolom), ADR-0032 (`SELECT *` pada ADD).
