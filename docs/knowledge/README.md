# Rancangan Komponen Knowledge ASCAM — versi 2

> Status: **disetujui (K1–K8)**; diimplementasikan pada F1.2 (ADR-0007). Model logis; tipe fisik,
> indeks, dan constraint ditetapkan pada migrasi (F1.2). Perubahan utama dari v1:
> dependensi view Teiid (ADR-0006), graf lineage ujung-ke-ujung, kepemilikan elemen,
> kebijakan (penamaan dan pemetaan tipe), masalah konsistensi, dan read model untuk UI.

## 1. Peran dan prinsip

Knowledge adalah **model runtime OBDF** (models@run.time; Blair dkk., 2009) yang dipakai
bersama oleh seluruh fase MAPE-K (Kephart & Chess, 2003).

| Prinsip | Wujud |
|---|---|
| **P1. Analyze dan Plan hanya membaca Knowledge** | Σ_S, ℳ, 𝒯, dan graf dependensinya tersedia tanpa menghubungi OBDF |
| **P2. Terhubung kausal** | Setiap sync dan adaptasi menghasilkan versi spesifikasi baru |
| **P3. Riwayat tidak diubah** | Versi, event, rencana, dan eksekusi bersifat append-only; hanya status yang berpindah. Baris versi tidak dihapus (tombstone); retensi menghapus isi versi dan menandainya `purged` |
| **P4. Generik** | Tidak ada nama objek studi kasus di skema; satu instalasi dapat mengelola beberapa OBDF |
| **P5. Merepresentasikan semua, mengadaptasi terbatas** | Semua pola pemakaian kolom dicatat; adaptasi otomatis hanya pada sel "otomatis" di §5 |
| **P6. Kepemilikan elemen** | Setiap elemen artefak ditandai `managed_by` (`human`/`ascam`) dan `introduced_in_version`. **ASCAM hanya menambah, dan hanya menghapus yang ia tambahkan sendiri** |
| **P7. Kredensial aman** | Rahasia dienkripsi di aplikasi; kunci di luar basis data (D4) |
| **P8. Satu pemilik basis data** | Hanya Knowledge Service yang mengakses PostgreSQL (D1) |

## 2. Graf dependensi

```text
kolom sumber ──(NameInSource)──▶ kolom foreign table ──(SYSADMIN.Usage)──▶ kolom view (bertingkat)
                                        │                                        │
                                        └──────────────┬─────────────────────────┘
                                                       ▼
                        logical column R2RML ──▶ term map ──▶ predikat ──▶ entitas 𝒯
```

Setiap tepi disimpan per versi spesifikasi. `spec.column_usage` adalah hasil penelusuran
graf yang dimaterialisasi saat sync, sehingga Orchestrator dan UI tidak perlu menelusuri
ulang untuk kasus umum.

## 3. Skema PostgreSQL

| Skema | Isi | Siklus hidup |
|---|---|---|
| `registry` | OBDF, target, kredensial, sumber, kebijakan (pengaturan, penamaan, pemetaan tipe), uji koneksi | Diubah administrator |
| `spec` | Versi spesifikasi, artefak, Σ_S, dependensi Teiid, ℳ, 𝒯, triple generik, lineage, masalah konsistensi | Immutable per versi |
| `ops` | Sync, event, rencana dan aksinya, eksekusi dan langkahnya, validasi, notifikasi, audit | Append-only |

**Versioning: snapshot per versi.** Setiap versi memiliki salinan lengkap baris Σ_S, ℳ,
dan 𝒯. Spesifikasi OBDF berukuran kecil, keadaan pada versi mana pun dapat dibaca langsung,
dan rollback cukup memindahkan penanda versi aktif, sejalan dengan versi VDB (ADR-0004).
Penyimpanan perubahan sebagai rekaman yang tidak diubah mengikuti gagasan event sourcing
(Fowler, 2005), dengan snapshot sebagai proyeksi siap baca.

## 4. Model logis

### 4.1 `registry`

```mermaid
erDiagram
    OBDF_INSTANCE ||--o{ TARGET : memiliki
    OBDF_INSTANCE ||--o{ SOURCE_SYSTEM : memiliki
    OBDF_INSTANCE ||--o{ SETTING : dikonfigurasi
    OBDF_INSTANCE ||--o{ NAMING_POLICY : dikonfigurasi
    OBDF_INSTANCE ||--o{ TYPE_MAPPING : dikonfigurasi
    CREDENTIAL ||--o{ TARGET : dipakai
    TARGET ||--o{ CONNECTION_CHECK : diuji

    OBDF_INSTANCE {
        bigint id PK
        text name UK
        text description
        timestamptz created_at
    }
    TARGET {
        bigint id PK
        bigint obdf_id FK
        text kind "teiid_mgmt, teiid_odbc, ontop_sparql, ontop_agent, kafka"
        text name
        jsonb endpoint "host, port, path, tls"
        bigint credential_id FK
        boolean enabled
    }
    CREDENTIAL {
        bigint id PK
        text username
        bytea secret_ciphertext "dienkripsi aplikasi"
        int key_version
        timestamptz rotated_at
    }
    SOURCE_SYSTEM {
        bigint id PK
        bigint obdf_id FK
        text logical_name "nilai field source pada event"
        text dbms "postgresql, mysql"
        text database_name
        text kafka_topic
        text identifier_case "aturan normalisasi nama"
    }
    SETTING {
        bigint obdf_id FK
        text key
        jsonb value "kebijakan otomatis atau HITL, timeout, retensi versi"
    }
    NAMING_POLICY {
        bigint obdf_id FK
        text property_iri_template "mis. prefix + camelCase kolom"
        text on_collision "qualify_with_class, hitl"
        text label_language
    }
    TYPE_MAPPING {
        bigint id PK
        bigint obdf_id FK
        text dbms
        text native_type
        text teiid_type
        text xsd_datatype
        boolean owl2ql_compatible
    }
    CONNECTION_CHECK {
        bigint id PK
        bigint target_id FK
        timestamptz checked_at
        boolean ok
        int latency_ms
        text detail
    }
```

### 4.2 `spec` — versi, artefak, triple, dan konsistensi

```mermaid
erDiagram
    SPEC_VERSION ||--o{ ARTIFACT : menyimpan
    SPEC_VERSION ||--o| SPEC_VERSION : turunan_dari
    ARTIFACT ||--o{ RDF_TRIPLE : diurai_menjadi
    SPEC_VERSION ||--o{ CONSISTENCY_ISSUE : memiliki

    SPEC_VERSION {
        bigint id PK
        bigint obdf_id FK
        int version_no "berurutan per OBDF"
        bigint parent_id FK
        text origin "sync, adaptation, rollback, manual"
        text status "candidate, active, superseded, rejected, rolled_back, purged"
        text teiid_vdb_name
        text teiid_vdb_version
        text teiid_connection_type "BY_VERSION, ANY, NONE"
        bigint plan_id FK "bila origin = adaptation"
        timestamptz created_at
    }
    ARTIFACT {
        bigint id PK
        bigint spec_version_id FK
        text kind "vdb_xml, r2rml, ontology"
        text content "isi lengkap berkas"
        text sha256
    }
    RDF_TRIPLE {
        bigint artifact_id FK
        text subject
        text predicate
        text object
        text object_kind "iri, literal, bnode"
        text datatype
        text lang
    }
    CONSISTENCY_ISSUE {
        bigint id PK
        bigint spec_version_id FK
        text code "lihat daftar kode"
        text severity "error, warning, info"
        text subject_kind
        text subject_ref
        text message
        text detected_by "sync, ascam_vocabulary, ontop_validate, teiid_status"
    }
```

`RDF_TRIPLE` menyimpan ℳ dan 𝒯 sebagai triple (label blank node dikanonisasi per artefak)
agar diff semantik antarversi dapat dihitung di SQL dan ditampilkan di UI.

**Kode masalah konsistensi (awal):** `predicate_undeclared`, `predicate_kind_mismatch`,
`predicate_deprecated_in_use`, `logical_column_unresolved`, `teiid_column_missing`,
`sql_unparsed`, `view_body_unparsed`, `vdb_validity_error`, `owl2ql_profile_violation`,
`domain_conflict`, `name_collision`, `drift_detected`, `unused_property`.

### 4.3 `spec` — Σ_S dan dependensi Teiid

Sumber: `SYS.Schemas`, `SYS.Tables`, `SYS.Columns`, `SYS.KeyColumns` (ADR-0003),
`SYSADMIN.Usage`, `SYSADMIN.Views`, `SYSADMIN.MatViews`, `SYSADMIN.StoredProcedures`,
`SYSADMIN.Triggers` (ADR-0006), `get-vdb` (ADR-0004).

```mermaid
erDiagram
    SPEC_VERSION ||--o{ TEIID_MODEL : memuat
    TEIID_MODEL ||--o{ TEIID_MODEL_SOURCE : bersumber
    SOURCE_SYSTEM ||--o{ TEIID_MODEL_SOURCE : dipetakan
    TEIID_MODEL ||--o{ TEIID_TABLE : berisi
    TEIID_TABLE ||--o{ TEIID_COLUMN : berisi
    TEIID_TABLE ||--o| TEIID_VIEW : didefinisikan
    TEIID_VIEW ||--o{ TEIID_VIEW_COLUMN : mengklasifikasi
    TEIID_COLUMN ||--o| TEIID_VIEW_COLUMN : adalah
    TEIID_TABLE ||--o{ TEIID_DEPENDENCY : bergantung
    TEIID_MODEL ||--o{ TEIID_ROUTINE : berisi
    SPEC_VERSION ||--o{ TEIID_ALTER : menambahkan

    TEIID_MODEL {
        bigint id PK
        bigint spec_version_id FK
        text name
        text model_type "physical, virtual"
        boolean visible
    }
    TEIID_MODEL_SOURCE {
        bigint model_id FK
        text source_name
        text translator
        text jndi_name
        bigint source_system_id FK
    }
    TEIID_TABLE {
        bigint id PK
        bigint model_id FK
        text uid "UID Teiid"
        text name
        text kind "foreign, view, materialized_view"
        text name_in_source
        text source_schema "hasil normalisasi"
        text source_table "hasil normalisasi"
        text managed_by
    }
    TEIID_COLUMN {
        bigint id PK
        bigint table_id FK
        text uid
        text name "nama di Teiid"
        text name_in_source
        text source_column "nama efektif di sumber, dinormalisasi"
        int position "tidak dijamin berurutan"
        text data_type
        text native_type
        boolean nullable
        int length
        int precision
        int scale
        boolean in_primary_key
        text managed_by
        bigint introduced_in_version FK
    }
    TEIID_VIEW {
        bigint table_id PK
        text body "SYSADMIN.Views.Body"
        text parse_status "ok, failed"
        boolean uses_star
    }
    TEIID_VIEW_COLUMN {
        bigint column_id PK
        text expression_kind "passthrough, expression, star, unknown"
        text expression_sql
    }
    TEIID_DEPENDENCY {
        bigint id PK
        bigint spec_version_id FK
        bigint dependent_table_id FK
        bigint dependent_column_id FK "kosong = tingkat view"
        bigint used_table_id FK
        bigint used_column_id FK "kosong = tingkat tabel"
        text derived_role "projection, predicate"
    }
    TEIID_ROUTINE {
        bigint id PK
        bigint model_id FK
        text kind "stored_procedure, trigger"
        text name
        text body
    }
    TEIID_ALTER {
        bigint id PK
        bigint spec_version_id FK
        bigint model_id FK
        int seq
        text statement "ALTER yang ditambahkan ASCAM (ADR-0002)"
        bigint plan_id FK
    }
```

`derived_role = predicate` diberikan bila kolom dirujuk pada tingkat view tetapi tidak
menjadi sumber kolom view mana pun (terbukti untuk `WHERE` dan `JOIN` pada F0.7).

### 4.4 `spec` — ℳ (R2RML)

Sumber: `mapping.ttl` sebagai graf RDF (rdflib); `rr:sqlQuery` dianalisis dengan `sqlglot`
(proyeksi, alias, ekspresi, `WHERE`, `JOIN`, `GROUP BY`, subkueri, `UNION`, `*`).

```mermaid
erDiagram
    SPEC_VERSION ||--o{ TRIPLES_MAP : memuat
    TRIPLES_MAP ||--o{ LOGICAL_SOURCE : membaca
    TEIID_TABLE ||--o{ LOGICAL_SOURCE : dibaca
    TRIPLES_MAP ||--o{ LOGICAL_COLUMN : mengekspos
    LOGICAL_COLUMN ||--o{ LOGICAL_COLUMN_SOURCE : berasal
    TEIID_COLUMN ||--o{ LOGICAL_COLUMN_SOURCE : menjadi
    TRIPLES_MAP ||--o{ SQL_REFERENCE : merujuk
    TEIID_COLUMN ||--o{ SQL_REFERENCE : dirujuk
    TRIPLES_MAP ||--o{ PREDICATE_OBJECT_MAP : memiliki
    TRIPLES_MAP ||--o{ TERM_MAP : memiliki
    PREDICATE_OBJECT_MAP ||--o{ TERM_MAP : memiliki
    TERM_MAP ||--o{ TERM_MAP_COLUMN : memakai
    LOGICAL_COLUMN ||--o{ TERM_MAP_COLUMN : dipakai
    TERM_MAP ||--o{ JOIN_CONDITION : memiliki
    TRIPLES_MAP ||--o{ SUBJECT_CLASS : menegaskan

    TRIPLES_MAP {
        bigint id PK
        bigint spec_version_id FK
        text iri
        text logical_table_kind "table, sql_query"
        text table_name
        text sql_query
        text sql_parse_status "ok, failed, not_applicable"
        text managed_by
    }
    LOGICAL_SOURCE {
        bigint triples_map_id FK
        bigint teiid_table_id FK "foreign table atau view"
        text alias
    }
    LOGICAL_COLUMN {
        bigint id PK
        bigint triples_map_id FK
        text name "nama yang dilihat term map"
        text expression_kind "passthrough, expression, star"
        text expression_sql
    }
    LOGICAL_COLUMN_SOURCE {
        bigint logical_column_id FK
        bigint teiid_column_id FK
    }
    SQL_REFERENCE {
        bigint triples_map_id FK
        bigint teiid_column_id FK
        text clause "where, join, group_by, having, order_by, subquery"
    }
    PREDICATE_OBJECT_MAP {
        bigint id PK
        bigint triples_map_id FK
        text signature "tanda tangan struktural, pengganti label blank node"
        text managed_by
        bigint introduced_in_version FK
    }
    TERM_MAP {
        bigint id PK
        bigint triples_map_id FK
        bigint pom_id FK "kosong untuk subject map"
        text position "subject, predicate, object, graph"
        text value_kind "constant, column, template, parent_triples_map"
        text value
        text term_type "iri, literal, blank"
        text datatype
        text language
    }
    TERM_MAP_COLUMN {
        bigint term_map_id FK
        bigint logical_column_id FK
        int template_position
    }
    JOIN_CONDITION {
        bigint id PK
        bigint term_map_id FK
        text child_column
        text parent_column
        text parent_triples_map_iri
    }
    SUBJECT_CLASS {
        bigint triples_map_id FK
        text class_iri
    }
```

### 4.5 `spec` — 𝒯 (ontologi)

Sumber: `ontology_file.ttl` sebagai graf RDF. Seluruh entitas dicatat untuk validasi
kosakata dan penelusuran; perubahan otomatis terbatas pada DatatypeProperty.

```mermaid
erDiagram
    SPEC_VERSION ||--o{ ONTOLOGY : memuat
    ONTOLOGY ||--o{ ONT_IMPORT : mengimpor
    ONTOLOGY ||--o{ ONT_ENTITY : mendeklarasikan
    ONT_ENTITY ||--o{ ONT_DOMAIN : memiliki
    ONT_ENTITY ||--o{ ONT_RANGE : memiliki
    ONT_ENTITY ||--o{ ONT_PROPERTY_RELATION : memiliki
    ONT_ENTITY ||--o{ ONT_ANNOTATION : diberi

    ONTOLOGY {
        bigint id PK
        bigint spec_version_id FK
        text iri
        text version_iri
        text version_info
    }
    ONT_IMPORT {
        bigint ontology_id FK
        text imported_iri
    }
    ONT_ENTITY {
        bigint id PK
        bigint ontology_id FK
        text iri
        text kind "class, datatype_property, object_property, annotation_property"
        text declared_in "local, imported"
        boolean deprecated
        boolean functional
        text managed_by
        bigint introduced_in_version FK
    }
    ONT_DOMAIN {
        bigint entity_id FK
        text class_expression "IRI atau bentuk kompleks (mis. union)"
        boolean is_simple
    }
    ONT_RANGE {
        bigint entity_id FK
        text range_iri
        boolean owl2ql_compatible
    }
    ONT_PROPERTY_RELATION {
        bigint entity_id FK
        text relation "sub_property_of, equivalent_property, inverse_of, disjoint_with"
        text other_iri
    }
    ONT_ANNOTATION {
        bigint entity_id FK
        text property_iri "rdfs:label, rdfs:comment, skos:changeNote, dcterms:created"
        text value
        text lang
        text managed_by
    }
```

### 4.6 `spec` — lineage hasil penelusuran

```mermaid
erDiagram
    TEIID_COLUMN ||--o{ COLUMN_USAGE : digunakan
    TRIPLES_MAP ||--o{ COLUMN_USAGE : menggunakan
    ONT_ENTITY ||--o{ COLUMN_USAGE : menjadi_predikat

    COLUMN_USAGE {
        bigint id PK
        bigint spec_version_id FK
        bigint foreign_column_id FK "kolom foreign table (ujung sumber)"
        bigint triples_map_id FK
        bigint term_map_id FK "kosong bila hanya di SQL"
        text role "literal_value, iri_template, join_key, sql_predicate, dynamic_predicate, projection_only"
        text predicate_iri
        bigint predicate_entity_id FK
        jsonb path "urutan kolom view yang dilalui"
        text weakest_link "passthrough, expression, star, predicate"
    }
```

`weakest_link` merangkum jenis tepi terlemah pada jalur (mis. satu tepi `expression` di view
membuat seluruh jalur tidak dapat disesuaikan otomatis saat DROP).

### 4.7 `ops` — jejak MAPE-K dan operasi

```mermaid
erDiagram
    SYNC_RUN ||--o| SPEC_VERSION : menghasilkan
    SOURCE_SYSTEM ||--o{ SCHEMA_EVENT : menghasilkan
    SCHEMA_EVENT ||--o{ ADAPTATION_PLAN : dianalisis
    ADAPTATION_PLAN ||--o{ PLAN_ACTION : terdiri
    ADAPTATION_PLAN ||--o{ EXECUTION : dijalankan
    EXECUTION ||--o{ EXECUTION_STEP : terdiri
    EXECUTION ||--o{ VALIDATION : diverifikasi
    ADAPTATION_PLAN ||--o{ NOTIFICATION : memicu

    SYNC_RUN {
        bigint id PK
        bigint obdf_id FK
        text trigger "startup, manual, scheduled, post_adaptation"
        text status
        boolean drift_detected
        jsonb drift_summary
        timestamptz started_at
        timestamptz finished_at
    }
    SCHEMA_EVENT {
        bigint id PK
        uuid event_uid UK "D7"
        bigint source_system_id FK
        jsonb raw_message
        jsonb structured "event terformalisasi"
        timestamptz captured_at
        timestamptz received_at
        text status "received, planned, ignored, duplicate, failed"
        text ignore_reason
    }
    ADAPTATION_PLAN {
        bigint id PK
        bigint event_id FK
        bigint base_spec_version_id FK "optimistic concurrency"
        text pattern
        text decision "auto, hitl"
        text status "pending_approval, approved, rejected, superseded, executed"
        jsonb impact "baris COLUMN_USAGE dan dependensi terdampak"
        jsonb reasons "alasan HITL"
        text approved_by
        timestamptz approved_at
    }
    PLAN_ACTION {
        bigint id PK
        bigint plan_id FK
        int seq
        text artifact "vdb, r2rml, ontology"
        text operation
        jsonb params
    }
    EXECUTION {
        bigint id PK
        bigint plan_id FK
        bigint candidate_spec_version_id FK
        text status "running, succeeded, failed, rolled_back"
        jsonb timings "dekomposisi Δt_adapt"
        jsonb failure
        timestamptz started_at
        timestamptz finished_at
    }
    EXECUTION_STEP {
        bigint execution_id FK
        int seq
        text name "deploy_vdb, validate, switch, reload_ontop, verify, rollback"
        text status
        timestamptz started_at
        timestamptz finished_at
        jsonb detail
    }
    VALIDATION {
        bigint id PK
        bigint execution_id FK
        bigint spec_version_id FK
        text validator "teiid_status, ontop_validate, ascam_vocabulary, sparql_regression"
        boolean passed
        jsonb details "termasuk validity-errors Teiid"
    }
    NOTIFICATION {
        bigint id PK
        bigint plan_id FK
        text severity
        text message
        boolean acknowledged
    }
```

`ops.audit_log` (aktor, aksi, objek, waktu, rincian) mencatat setiap tindakan lewat UI,
termasuk persetujuan HITL; konsep entitas–aktivitas–agen sejalan dengan W3C PROV-O.

## 5. Matriks keputusan (D11 v2)

Diterapkan pada **setiap baris** jalur dampak kolom foreign table yang terdampak. Rencana
bersifat `auto` hanya bila semua baris bernilai otomatis.

| Kondisi | ADD | RENAME (D8) | DROP |
|---|---|---|---|
| Kolom tidak dipakai ℳ maupun view | Σ′_S (+P-001 bila kebijakan) | Σ′_S | Σ′_S |
| `literal_value`, jalur langsung atau hanya lewat `star` | P-001 untuk TriplesMap pada tabel atau view `SELECT *` | otomatis | P-002 (deprecate hanya bila pemakaian predikat terakhir) |
| `projection_only` | – | otomatis | otomatis (hapus dari proyeksi SQL) |
| `iri_template`, `join_key`, `sql_predicate`, `dynamic_predicate` | – | otomatis | **HITL** |
| Jalur melewati view dengan `expression` atau `predicate` | tidak merambat → notifikasi | otomatis | **HITL** (VDB akan FAILED, F0.7) |
| Kolom kunci primer | – | otomatis | **HITL** |
| Tabel pada model multi-source, materialized view, prosedur/trigger yang merujuk | **HITL** | **HITL** | **HITL** |
| Bentrok nama Teiid (ADR-0002) | nama alternatif | nama alternatif | – |
| ADD: IRI property sudah ada dengan domain kelas lain | kebijakan `qualify_with_class` atau HITL | – | – |
| Property sudah deprecated dan kolom muncul lagi | IRI baru sesuai kebijakan atau HITL | – | – |
| Entitas 𝒯 berasal dari `owl:imports`, atau penghapusan elemen `managed_by = human` diperlukan | **HITL** | **HITL** | **HITL** |
| SQL logical table atau `Body` view gagal di-parse | **HITL** | otomatis | **HITL** |
| Event duplikat / tabel tidak difederasikan / ALTER tidak didukung atau multi-aksi | diabaikan / diabaikan / HITL | idem | idem |
| Versi aktif berubah sejak rencana dibuat | rencana disusun ulang | idem | idem |

## 6. Kebutuhan pembaca

**Orchestrator** (Analyze dan Plan):

- `resolve_event(event)` → kolom foreign table (lewat `source_system`, `source_schema`,
  `source_table`, `source_column`) atau alasan diabaikan;
- `impact(foreign_column)` → baris `column_usage` beserta dependensi view dan routine,
  jumlah pemakaian tiap predikat, entitas 𝒯 terkait;
- kebijakan (`setting`, `naming_policy`, `type_mapping`), nomor versi berikutnya,
  pemeriksaan bentrok nama dan domain.

**Web UI** (read model sebagai view SQL, diakses lewat Knowledge API):

| Halaman | Sumber |
|---|---|
| Dasbor OBDF dan target | `registry.*`, `connection_check` terbaru, versi aktif, event terakhir |
| Linimasa versi dan diff | `spec_version`, `artifact` (diff teks), `rdf_triple` (diff triple), Σ_S antarversi |
| Lineage explorer | `teiid_dependency`, `column_usage`, `ont_entity` |
| Konsistensi | `consistency_issue` per versi |
| Antrean HITL | `adaptation_plan` berstatus `pending_approval`, beserta `impact` dan `reasons` |
| Eksekusi | `execution`, `execution_step`, `validation` (termasuk dekomposisi Δt) |
| Kebijakan | `setting`, `naming_policy`, `type_mapping` |
| Audit dan notifikasi | `ops.audit_log`, `notification` |

## 7. Pemetaan terhadap ERD proposal

| Proposal (Tabel 3.21–3.23) | Rancangan v2 | Alasan |
|---|---|---|
| `vdb`, `teiid_model`, `teiid_object`, `teiid_column`, dan duplikatnya di Tabel 3.22 no. 13–16 | `spec.spec_version` (nama/versi VDB), `teiid_model`, `teiid_table`, `teiid_column` | Satu sumber kebenaran |
| `source_database`, `source_table`, `source_column`, `object_mapping`, `column_mapping` | `registry.source_system`, `teiid_model_source`, kolom `source_*` pada `teiid_table`/`teiid_column` | ASCAM tidak membaca skema sumber; pemetaan lewat `NameInSource` |
| `view_definition`, `object_dependency`, `column_dependency` | `teiid_view`, `teiid_view_column`, `teiid_dependency` | Diisi dari `SYSADMIN.Views` dan `SYSADMIN.Usage` (ADR-0006) |
| `foreign_key` | Ditunda | Belum dipakai analisis dampak |
| `R2RML_MAPPING` | `artifact` (kind = r2rml) + `rdf_triple` | Isi dan triple per versi |
| `TRIPLES_MAP`, `LOGICAL_TABLE` | `triples_map`, `logical_source` | Logical table bisa membaca beberapa tabel/view |
| `SUBJECT_MAP`, `PREDICATE_MAP`, `OBJECT_MAP`, `TERM_MAP` | `term_map` (`position`) | Satu tabel generik |
| `PREDICATE_OBJECT_MAP`, `REF_OBJECT_MAP`, `JOIN_CONDITION`, `CLASS_ASSERTION` | `predicate_object_map`, `term_map`, `join_condition`, `subject_class` | Setara, ditambah tanda tangan struktural |
| `LOGICAL_COLUMN`, `TERM_COLUMN_REFFERENCE`, `SQL_COLUMN_DEPENDENCY` | `logical_column`, `logical_column_source`, `term_map_column`, `sql_reference`, dan hasil `column_usage` | Dipisah per jenis tepi agar penelusuran eksplisit |
| `Ontology`, `Ontology_version` | `ontology`, `spec_version` | Versi mengikuti versi spesifikasi |
| `Ontology_resource`, `Owl_property` | `ont_entity` | Satu registri entitas |
| `Owl_property_domain`, `Owl_property_range`, `Owl_property_hierarchy`, `Owl_property_characteristic` | `ont_domain`, `ont_range`, `ont_property_relation`, `ont_entity.functional` | Setara; domain kompleks dan kompatibilitas OWL 2 QL dicatat |
| `Owl_datatype_restriction` | Ditunda | Belum dipakai pola adaptasi |
| (belum ada) | `registry.*`, `ont_import`, `ont_annotation`, `teiid_routine`, `teiid_alter`, `rdf_triple`, `consistency_issue`, `ops.*` | Konfigurasi, kepemilikan, versioning, konsistensi, dan jejak MAPE-K |

## 8. Implementasi

- Kode: `setup/ascam/knowledge/` (lihat README di folder tersebut). Keputusan implementasi
  dan buktinya: ADR-0007.
- Migrasi: SQLAlchemy 2 + Alembic. Driver: psycopg 3 (ADR-0003).
- Integritas P2/P3 ditegakkan dengan foreign key komposit dan trigger di basis data.
- Knowledge Service (FastAPI) sebagai satu-satunya pengakses basis data (F1.3); role
  `ascam_owner` (migrasi) dan `ascam_app` (DML).
- Sync diuji terhadap OBDF studi kasus dan dibandingkan dengan hasil F0.3, F0.5, dan F0.7 (F1.4).

## Referensi

- Blair, G., Bencomo, N., France, R. B. (2009). Models@run.time. *Computer*, 42(10), 22–27. https://doi.org/10.1109/MC.2009.326
- Kephart, J. O., Chess, D. M. (2003). The Vision of Autonomic Computing. *Computer*, 36(1), 41–50. https://doi.org/10.1109/MC.2003.1160055
- Fowler, M. (2005). Event Sourcing. https://martinfowler.com/eaaDev/EventSourcing.html
- W3C (2012). R2RML: RDB to RDF Mapping Language. https://www.w3.org/TR/r2rml/
- W3C (2012). OWL 2 Web Ontology Language Profiles. https://www.w3.org/TR/owl2-profiles/
- W3C (2013). PROV-O: The PROV Ontology. https://www.w3.org/TR/prov-o/
- Teiid Reference Guide (`teiid-documents.pdf`): "System schema" dan "SYSADMIN schema" (hlm. 554–569).
- Proposal tesis, Sub-bab 3.6 (hlm. 81–92), Tabel 3.21–3.23.
- ADR-0001 s.d. ADR-0006 (`docs/adr/`).
