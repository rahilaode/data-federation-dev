# Running example: validasi logika Plan ASCAM

Branch `feature/running-example` memakai kode framework dan arsitektur yang sama dengan
`feature/npd`. Yang berbeda hanya data sumber, VDB, mapping, ontologi, dan skrip
menjalankan/skenario. Datanya kecil dan dibuat dari running example di paper (tabel `person`,
view `v_active`, triples map `m` dengan assertion `a1` dan `a2`), lalu ditambah satu tabel kecil
untuk setiap cabang Plan, sehingga setiap cabang bisa diuji sendiri tanpa saling mengganggu.

## 1. Menjalankan dan menghentikan

```bash
./start.sh                 # semua kontainer dan volume dibuat ulang, data dimuat dari setup/data-source
./stop.sh                  # hentikan semua; volume (data sumber, Knowledge) disimpan
./stop.sh --volumes        # hentikan dan hapus volume
```

Satu skenario dengan harness evaluasi (keputusan, alasan, aksi, dan jawaban SPARQL sebelum dan
sesudah dicatat di `results/evaluation/<waktu>/`):

```bash
python3 experiments/evaluation/evaluate.py --mode treatment --scenario r01 --runs 1
python3 experiments/evaluation/evaluate.py --mode both --runs 1 --baseline-runs 1   # semua skenario
python3 experiments/evaluation/analyze.py
```

Harness mengembalikan sumber dan OBDF ke keadaan dasar sebelum setiap run
(`experiments/reset_obdf.py`), menyetujui ADD secara otomatis dengan usulan ASCAM, dan hanya
mengamati skenario yang dinotifikasi. Untuk mencoba manual, jalankan DDL di kolom "DDL" tabel
skenario lewat `docker exec -it datasources-pgsql psql -U postgres -d reporting` (S1) atau
`docker exec -it datasources-mysql mysql -umysql -pmysql registry` (S2), lalu lihat rencananya di
console (http://127.0.0.1:18400).

Pemeriksaan tanpa Docker (keputusan dan aksi setiap skenario atas artefak di repo):

```bash
cd setup/ascam/knowledge/service
ASCAM_KNOWLEDGE_DB_URL=postgresql+psycopg://...:5432/ascam_knowledge_test \
  python3 -m pytest tests/test_case_study_example.py
```

## 2. Sumber data

| Sumber | Tabel | Isi | Untuk |
|---|---|---|---|
| S1 `reporting` (PostgreSQL) | `person(id, active, first_name, nickname)` | Ana (aktif), Budi (aktif), Cici (tidak aktif) | Example 1 paper; r01–r04, r08, r15 |
| | `contact(id, phone, phone2, email, fax)` | 2 baris | r06, r07 |
| | `product(id, label, price, discount)` | 2 baris | r09 |
| | `note`, `memo`, `doc` `(id, body)` | 2 baris masing-masing | r10, r11, r12 |
| | `staff(id, city)` | 2 baris | r05 |
| | `city(id, name)` | 2 baris | r13 |
| | `account(id, owner)` | 2 baris | r14 |
| | `event(id, kind)` | 2 baris | r17 |
| | `audit(id, action)` | 2 baris | r16 |
| | `tool(id, label)` | 2 baris (tanpa kolom `weight`) | r18 |
| S2 `registry` (MySQL) | `member(member_id, nama, panggilan, phone)` | Dewi, Eko | r19–r21; `ex:nickname` dan `ex:phone` diisi dari dua sumber |

Semua tabel dipantau oleh schema monitor (`captured_tables_ref`).

## 3. Federasi, mapping, dan ontologi

VDB `example` (`setup/data-federation/deployments/example-vdb.xml`):

* model `src` (S1) dan `reg` (S2): foreign table tanpa `NAMEINSOURCE`, jadi src(ĉ) = ĉ. Satu
  pengecualian: `staff.town_legacy` membaca kolom sumber `town`, yang tidak ada (Σ tertinggal dari
  sumber, seperti setelah drop yang dulu hanya dinotifikasi). Kolom ini tidak dipetakan.
* model `v` (Σ_V):

| View | Definisi | Peran |
|---|---|---|
| `v_active` | `SELECT id, first_name AS name FROM src.person WHERE active = TRUE` | Example 1 paper |
| `v_net_price` | `SELECT id, price * (1 - discount) AS net_price FROM src.product` | expression |
| `v_note_body` | `SELECT body FROM src.note` | akan kehilangan semua kolom |
| `v_memo` | `("id" integer, "body" string) AS SELECT id, body FROM src.memo` | kolom inline |
| `v_doc_cache` | `OPTIONS (MATERIALIZED 'TRUE') AS SELECT id, body FROM src.doc` | materialized (internal) |
| `v_city_all` | `SELECT * FROM src.city` | `SELECT *` biasa (aman untuk ADD) |
| `v_events` | `SELECT * FROM src.event UNION ALL SELECT id, kind FROM src.event` | `SELECT *` dalam UNION |

Mapping (`setup/vkg-system/config/mapping.ttl`), namespace `ex: = http://example.org/`:

| Triples map | Logical table | Subjek, kelas | Assertion property |
|---|---|---|---|
| `TM_ActivePerson` (= m di paper) | `SELECT * FROM v.v_active` | `ex:person/{id}`, ex:ActivePerson | a2: ex:name ← name |
| `TM_PersonNick` | `SELECT id, nickname FROM src.person` | `ex:person/{id}`, ex:Person | ex:nickname ← nickname |
| `TM_Contact` | `SELECT id, phone, phone2 FROM src.contact` | `ex:contact/{id}`, ex:Contact | ex:phone ← phone, ex:phone ← phone2 |
| `TM_ContactEmail` | `SELECT id, email, fax FROM src.contact` | `ex:contact/{id}`, tanpa kelas | ex:email ← email (fax: projection only) |
| `TM_Product` | `SELECT * FROM v.v_net_price` | `ex:product/{id}`, ex:Product | ex:netPrice ← net_price |
| `TM_City` | `rr:tableName "src.city"` | `ex:city/{id}`, ex:City | ex:cityName ← name |
| `TM_Account` | `rr:tableName "src.account"` | `ex:account/{id}`, ex:Account | ex:owner ← owner |
| `TM_Customer` | `SELECT * FROM src.account` | `ex:customer/{id}`, ex:Customer | (tidak ada) |
| `TM_AccountRecord` | `rr:tableName "src.account"` | `ex:record/{id}`, tanpa kelas | ex:owner ← owner |
| `TM_Event` | `SELECT * FROM v.v_events` | `ex:event/{id}`, ex:Event | ex:kind ← kind |
| `TM_Tool` | `SELECT id, label FROM src.tool` | `ex:tool/{id}`, ex:Tool | ex:label ← label |
| `TM_Note`, `TM_Memo`, `TM_Doc` | `SELECT id, body FROM src.<tabel>` | `ex:<tabel>/{id}`, kelas sendiri | ex:noteBody, ex:memoBody, ex:docBody |
| `TM_Staff` | `SELECT id, city FROM src.staff` | `ex:staff/{id}`, ex:Staff | ex:city ← city |
| `TM_Member` (S2) | `SELECT member_id, nama, panggilan, phone FROM reg.member` | `ex:member/{member_id}`, ex:Member | ex:fullName, ex:nickname, ex:phone |

Ontologi (`setup/vkg-system/config/ontology_file.ttl`) mendeklarasikan semua kelas dan data
property. Hanya `ex:weight` yang punya domain (ex:Tool) dan range (xsd:decimal), dan property ini
sudah di-deprecate lewat blok ASCAM, seolah-olah kolom `tool.weight` pernah di-drop oleh P-002.
Deprecation ditulis sebagai blok ASCAM karena deprecation yang ditulis tangan tidak dicabut ASCAM
(P6).

## 4. Skenario dan hasil yang diharapkan

Kode r01–r21 dipakai harness (`experiments/evaluation/scenarios.py`, `queries.py`) dan tes
Knowledge (`test_case_study_example.py`).

| Kode | DDL | Cabang Plan | Keputusan | Aksi yang diharapkan | Jawaban SPARQL sesudahnya |
|---|---|---|---|---|---|
| r01 | `ALTER TABLE person DROP COLUMN first_name` | P-002, Example ex:dependents: W = {v_active.name}, ℳ_c = {a2} | auto | drop ĉ; ALTER VIEW v_active → `SELECT "id" … WHERE "active" = TRUE`; hapus POM ex:name (object `name`) dari TM_ActivePerson; deprecate ex:name | ex:name kosong; ex:ActivePerson tetap Ana dan Budi |
| r02 | `… RENAME COLUMN first_name TO given_name` | P-003, repair (a) di Example ex:repairs | auto | `set_name_in_source` given_name | identik |
| r03 | `… DROP COLUMN active` | notifikasi: weakest link predicate (WHERE v_active) | notify | tidak ada | query lewat v_active gagal sampai diperbaiki manual |
| r04 | `… DROP COLUMN id` | notifikasi: primary key, role IRI template | notify | tidak ada | query person gagal |
| r05 | `ALTER TABLE staff RENAME COLUMN city TO town` | notifikasi: c′ sudah dibaca `town_legacy` | notify | tidak ada | ex:city gagal |
| r06 | `ALTER TABLE contact DROP COLUMN phone` | P-002, satu map mengisi ex:phone dari dua kolom (ADR-0031) | auto | hapus hanya POM `phone`; tulis ulang query TM_Contact; ex:phone dipertahankan | ex:phone berkurang (phone2 dan member), tidak kosong |
| r07 | `… DROP COLUMN fax` | P-002, projection only: rw_c | auto | tulis ulang query TM_ContactEmail; tidak ada POM yang dihapus | ex:email identik |
| r08 | `ALTER TABLE person DROP COLUMN nickname` | P-002, property juga diisi S2 | auto | hapus POM ex:nickname dari TM_PersonNick; ex:nickname dipertahankan | ex:nickname tinggal dari member |
| r09 | `ALTER TABLE product DROP COLUMN discount` | notifikasi: expression di v_net_price | notify | tidak ada | ex:netPrice gagal |
| r10 | `ALTER TABLE note DROP COLUMN body` | notifikasi: v_note_body kehilangan semua kolom | notify | tidak ada | ex:noteBody gagal |
| r11 | `ALTER TABLE memo DROP COLUMN body` | notifikasi: v_memo punya kolom inline | notify | tidak ada | ex:memoBody gagal |
| r12 | `ALTER TABLE doc DROP COLUMN body` | notifikasi: v_doc_cache materialized | notify | tidak ada | ex:docBody gagal |
| r13 | `ALTER TABLE city ADD COLUMN population INTEGER` | P-001, X = {TM_City}; v_city_all (`SELECT *` biasa) bukan kondisi notifikasi | hitl | ex:population (domain ex:City, range xsd:integer); POM baru di TM_City | ex:population berisi setelah diisi |
| r14 | `ALTER TABLE account ADD COLUMN status VARCHAR(20)` | P-001, beberapa kelas: usulan ex:Account; X_C = {TM_Account, TM_AccountRecord} | hitl | POM baru di dua map itu, TM_Customer tidak | ex:status untuk account dan record |
| r15 | `ALTER TABLE person ADD COLUMN birth_date DATE` | P-001, X = ∅: triples map baru dari TM_PersonNick | hitl | `add_triples_map` dengan `SELECT id, nickname, "birth_date" FROM src.person`, template sama, tanpa kelas; ex:birthDate domain ex:Person | ex:birthDate berisi |
| r16 | `ALTER TABLE audit ADD COLUMN actor VARCHAR(20)` | P-001, X_C = ∅: hanya Σ berubah | hitl | hanya `add_column` | tidak ada property baru |
| r17 | `ALTER TABLE event ADD COLUMN severity VARCHAR(10)` | notifikasi (ADR-0032): v_events menerima c lewat `*` dalam UNION | notify | tidak ada | query lama tetap jalan |
| r18 | `ALTER TABLE tool ADD COLUMN weight NUMERIC(6,2)` | P-001, reuse: ex:weight deprecated, domain dan range sama | hitl | map baru dari TM_Tool; deprecation dicabut | ex:weight berisi |
| r19 | `ALTER TABLE member RENAME COLUMN nama TO nama_lengkap` (MySQL) | P-003 di MySQL | auto | `set_name_in_source` nama_lengkap | identik |
| r20 | `ALTER TABLE member ADD COLUMN email VARCHAR(50)` (MySQL) | P-001 di MySQL, X = ∅, property aktif ex:email dipakai ulang | hitl | map baru dari TM_Member; ex:email domain ex:Member | ex:email untuk member |
| r21 | `ALTER TABLE member DROP COLUMN panggilan` (MySQL) | P-002 di MySQL, ex:nickname tetap diisi S1 | auto | hapus POM ex:nickname dari TM_Member; ex:nickname dipertahankan | ex:nickname tinggal dari person |

## 5. Apa yang sudah diperiksa tanpa Docker

* `test_case_study_example.py` (Knowledge): keputusan, pola, alasan, dan aksi ke-21 skenario atas
  artefak di repo. Semua sesuai tabel di atas.
* Ontop 4.1.1 di atas salinan federasi di PostgreSQL (skema `src`, `reg`, `v`): `ontop validate`
  lulus, dan semua query dasar skenario menjawab (Example 1: Ana dan Budi, Cici tidak). Untuk
  skenario yang dieksekusi (P-001 dan P-002), rencana diterapkan dengan editor Executor lalu
  query dijalankan lagi; hasilnya sesuai kolom terakhir tabel di atas.
* Yang belum bisa diperiksa tanpa stack: penerimaan VDB oleh Teiid (termasuk materialized view
  internal dan kolom `town_legacy`), P-003 (bergantung pada `NAMEINSOURCE` Teiid), Monitor, dan
  waktu adaptasi. Semua itu diperiksa dengan `./start.sh` dan harness di atas.
