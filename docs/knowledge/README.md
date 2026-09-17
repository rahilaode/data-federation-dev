# Rancangan Komponen Knowledge ASCAM (F1)

> Status: **draf untuk ditinjau**. Tabel dan kolom di bawah adalah model logis; tipe
> fisik dan indeks ditetapkan saat implementasi migrasi (F1.2).

## 1. Peran dan prinsip

Knowledge adalah **model runtime OBDF** (models@run.time): representasi diri sistem yang
terhubung secara kausal dengan sistem tersebut (Blair, Bencomo & France, 2009). Pada
MAPE-K, Knowledge dipakai bersama oleh seluruh fase (Kephart & Chess, 2003). Konsekuensinya
bagi ASCAM:

| Prinsip | Wujud dalam rancangan |
|---|---|
| **P1. Analyze dan Plan hanya membaca Knowledge** | Seluruh Σ_S, ℳ, 𝒯, dan keterkaitannya tersedia tanpa menghubungi OBDF |
| **P2. Terhubung kausal** | Setiap adaptasi dan sync menghasilkan versi spesifikasi baru |
| **P3. Riwayat tidak diubah (append-only)** | Versi spesifikasi, event, rencana, dan eksekusi tidak pernah di-`UPDATE` isinya; hanya status yang berpindah |
| **P4. Generik** | Tidak ada nama tabel/kolom/kelas studi kasus di skema; satu instalasi dapat mengelola lebih dari satu OBDF |
| **P5. Batasan penelitian eksplisit** | Adaptasi otomatis hanya untuk kolom yang menjadi nilai DatatypeProperty. Peran lain (template IRI, join, filter, view) tetap **dicatat** agar dapat dideteksi dan dieskalasi (D11) |
| **P6. Kredensial aman** | Rahasia dienkripsi di aplikasi, kunci berada di luar basis data (D4) |
| **P7. Satu pemilik** | Hanya Knowledge Service yang mengakses PostgreSQL (D1) |

## 2. Pembagian skema PostgreSQL

| Skema | Isi | Siklus hidup |
|---|---|---|
| `registry` | OBDF yang dikelola, target (Teiid, Ontop Agent, Kafka), kredensial, sumber data, pengaturan ASCAM, hasil uji koneksi | Dapat diubah administrator |
| `spec` | **Versi spesifikasi OBDF** beserta isi artefak dan model ternormalisasi Σ_S, ℳ, 𝒯, serta lineage kolom | Immutable per versi |
| `ops` | Jejak MAPE-K: event skema, rencana adaptasi, eksekusi, validasi, audit | Append-only |

### Strategi versioning: snapshot per versi

Setiap versi spesifikasi (`spec.spec_version`) memiliki **salinan lengkap** baris Σ_S, ℳ,
dan 𝒯-nya sendiri, bukan sekadar selisih. Alasan:

- spesifikasi OBDF berukuran kecil (puluhan–ratusan baris per versi), sehingga biaya
  penyimpanan tidak signifikan;
- kueri "keadaan pada versi N" dan perbandingan dua versi menjadi sederhana dan tidak
  memerlukan rekonstruksi riwayat;
- rollback cukup memindahkan penanda versi aktif, sejalan dengan versi VDB (ADR-0004).

Pola ini mengikuti gagasan penyimpanan perubahan sebagai rekaman yang tidak diubah
(event sourcing; Fowler, 2005), dengan snapshot sebagai proyeksi yang siap dibaca.

## 3. Model logis

### 3.1 `registry` — konfigurasi dan target

```mermaid
erDiagram
    OBDF_INSTANCE ||--o{ TARGET : memiliki
    OBDF_INSTANCE ||--o{ SOURCE_SYSTEM : memiliki
    OBDF_INSTANCE ||--o{ SETTING : dikonfigurasi
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
    }
    SETTING {
        bigint obdf_id FK
        text key
        jsonb value "timeout, retensi versi, kebijakan HITL"
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

### 3.2 `spec` — versi dan artefak

```mermaid
erDiagram
    SPEC_VERSION ||--o{ ARTIFACT : menyimpan
    SPEC_VERSION ||--o| SPEC_VERSION : turunan_dari

    SPEC_VERSION {
        bigint id PK
        bigint obdf_id FK
        int version_no "berurutan per OBDF"
        bigint parent_id FK
        text origin "sync, adaptation, rollback, manual"
        text status "candidate, active, superseded, rejected, rolled_back"
        text teiid_vdb_name
        text teiid_vdb_version
        bigint event_id FK "bila origin = adaptation"
        timestamptz created_at
    }
    ARTIFACT {
        bigint id PK
        bigint spec_version_id FK
        text kind "vdb_xml, r2rml, ontology"
        text content "isi lengkap berkas (backup)"
        text sha256
    }
```

### 3.3 `spec` — Σ_S (Teiid)

Sumber data: tabel `SYS` Teiid (ADR-0003). Nama efektif di sumber = `name_in_source`
bila terisi, selain itu `name` (ADR-0002).

```mermaid
erDiagram
    SPEC_VERSION ||--o{ TEIID_MODEL : memuat
    TEIID_MODEL ||--o{ TEIID_TABLE : berisi
    TEIID_TABLE ||--o{ TEIID_COLUMN : berisi
    TEIID_TABLE ||--o| TEIID_VIEW : didefinisikan
    SOURCE_SYSTEM ||--o{ TEIID_MODEL : dipetakan_ke

    TEIID_MODEL {
        bigint id PK
        bigint spec_version_id FK
        text name
        text source_name
        text translator
        text jndi_name
        bigint source_system_id FK
        boolean is_physical
    }
    TEIID_TABLE {
        bigint id PK
        bigint model_id FK
        text name
        text name_in_source
        text kind "foreign, view"
    }
    TEIID_COLUMN {
        bigint id PK
        bigint table_id FK
        text name "nama di Teiid"
        text name_in_source "nama di sumber bila berbeda"
        int position "tidak dijamin berurutan"
        text data_type
        boolean nullable
        int length
        int precision
        int scale
        boolean is_primary_key
    }
    TEIID_VIEW {
        bigint table_id PK
        text definition "teks transformasi view"
    }
```

### 3.4 `spec` — ℳ (R2RML) dan lineage kolom

Sumber data: `mapping.ttl` dibaca sebagai graf RDF (rdflib); `rr:sqlQuery` dianalisis
dengan `sqlglot` untuk mendapatkan kolom yang diproyeksikan, di-alias, dan dipakai di
`WHERE`/`JOIN`.

```mermaid
erDiagram
    SPEC_VERSION ||--o{ TRIPLES_MAP : memuat
    TRIPLES_MAP ||--o{ PREDICATE_OBJECT_MAP : memiliki
    TRIPLES_MAP ||--o{ TERM_MAP : memiliki
    PREDICATE_OBJECT_MAP ||--o{ TERM_MAP : memiliki
    TERM_MAP ||--o{ JOIN_CONDITION : memiliki
    TRIPLES_MAP ||--o{ SUBJECT_CLASS : menegaskan
    TEIID_COLUMN ||--o{ COLUMN_USAGE : dipakai_oleh
    TERM_MAP ||--o{ COLUMN_USAGE : merujuk

    TRIPLES_MAP {
        bigint id PK
        bigint spec_version_id FK
        text iri
        text logical_table_kind "table, sql_query"
        text table_name
        text sql_query
    }
    PREDICATE_OBJECT_MAP {
        bigint id PK
        bigint triples_map_id FK
        text node_id "identitas node RDF"
    }
    TERM_MAP {
        bigint id PK
        bigint triples_map_id FK
        bigint pom_id FK "kosong untuk subject map"
        text position "subject, predicate, object"
        text value_kind "constant, column, template, parent_triples_map"
        text value
        text term_type "iri, literal, blank"
        text datatype
        text language
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
    COLUMN_USAGE {
        bigint id PK
        bigint spec_version_id FK
        bigint teiid_column_id FK
        bigint triples_map_id FK
        bigint term_map_id FK "kosong bila hanya di SQL"
        text logical_name "nama kolom di logical table (alias)"
        text role "literal_value, iri_template, join_key, sql_predicate, projection_only"
        text predicate_iri "untuk literal_value"
    }
```

`COLUMN_USAGE` adalah **inti analisis dampak**: satu baris per penggunaan kolom Teiid di
ℳ. Tabel ini menggantikan `LOGICAL_COLUMN`, `TERM_COLUMN_REFFERENCE`,
`SQL_COLUMN_DEPENDENCY`, dan `column_mapping` pada proposal. Nilai `role` menentukan sel
matriks D11:

| `role` | ADD | RENAME | DROP |
|---|---|---|---|
| `literal_value` | – | otomatis (D8) | otomatis (P-002) |
| `iri_template` | – | otomatis (D8) | HITL |
| `join_key` | – | otomatis (D8) | HITL |
| `sql_predicate` | – | otomatis (D8) | HITL |
| `projection_only` | – | otomatis (D8) | otomatis |
| kolom tanpa baris `COLUMN_USAGE` | Σ′_S (+ P-001 bila kebijakan menambah property) | Σ′_S | Σ′_S |
| tabel dirujuk `TEIID_VIEW` | – | otomatis (D8) | HITL (konservatif) |

### 3.5 `spec` — 𝒯 (ontologi)

Sumber data: `ontology_file.ttl` dibaca sebagai graf RDF. Seluruh entitas dicatat agar
validator kosakata (ADR-0005) dapat memeriksa predikat ℳ′; perubahan otomatis tetap
terbatas pada DatatypeProperty.

```mermaid
erDiagram
    SPEC_VERSION ||--o{ ONTOLOGY : memuat
    ONTOLOGY ||--o{ ONT_ENTITY : mendeklarasikan
    ONT_ENTITY ||--o{ ONT_DOMAIN : memiliki
    ONT_ENTITY ||--o{ ONT_RANGE : memiliki
    ONT_ENTITY ||--o{ ONT_SUBPROPERTY : memiliki

    ONTOLOGY {
        bigint id PK
        bigint spec_version_id FK
        text iri
        text version_info
    }
    ONT_ENTITY {
        bigint id PK
        bigint ontology_id FK
        text iri
        text kind "class, datatype_property, object_property, annotation_property"
        text label
        boolean deprecated
        boolean functional
    }
    ONT_DOMAIN {
        bigint entity_id FK
        text class_iri
    }
    ONT_RANGE {
        bigint entity_id FK
        text range_iri
    }
    ONT_SUBPROPERTY {
        bigint entity_id FK
        text super_property_iri
    }
```

### 3.6 `ops` — jejak MAPE-K

```mermaid
erDiagram
    SOURCE_SYSTEM ||--o{ SCHEMA_EVENT : menghasilkan
    SCHEMA_EVENT ||--o{ ADAPTATION_PLAN : dianalisis
    ADAPTATION_PLAN ||--o{ EXECUTION : dijalankan
    EXECUTION ||--o{ VALIDATION : diverifikasi
    SPEC_VERSION ||--o{ VALIDATION : divalidasi

    SCHEMA_EVENT {
        bigint id PK
        uuid event_uid UK "event_id (D7), mencegah pemrosesan ganda"
        bigint source_system_id FK
        jsonb raw_message "pesan Kafka"
        jsonb structured "event terformalisasi"
        timestamptz captured_at
        timestamptz received_at
        text status "received, planned, ignored, failed"
    }
    ADAPTATION_PLAN {
        bigint id PK
        bigint event_id FK
        bigint base_spec_version_id FK
        text pattern "P-001, P-002, P-003"
        text decision "auto, hitl"
        jsonb impact "baris COLUMN_USAGE terdampak"
        jsonb actions "ALTER, perubahan R2RML dan ontologi"
        text approved_by
        timestamptz approved_at
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
    VALIDATION {
        bigint id PK
        bigint execution_id FK
        bigint spec_version_id FK
        text validator "teiid_status, ontop_validate, ascam_vocabulary, sparql_regression"
        boolean passed
        jsonb details
        timestamptz at
    }
```

Tabel `ops.audit_log` (aktor, aksi, objek, waktu, rincian) mencatat setiap tindakan
administrator lewat UI, termasuk persetujuan HITL. Konsep entitas–aktivitas–agen pada
jejak ini sejalan dengan W3C PROV-O.

## 4. Pemetaan terhadap ERD proposal

| Proposal | Rancangan revisi | Alasan |
|---|---|---|
| `vdb`, `teiid_model`, `teiid_object`, `teiid_column` (Tabel 3.21) dan duplikatnya di Tabel 3.22 (no. 13–16) | `spec.teiid_model`, `spec.teiid_table`, `spec.teiid_column`; nama/versi VDB di `spec.spec_version` | Satu sumber kebenaran; versi VDB menjadi bagian versi spesifikasi |
| `source_database`, `source_table`, `source_column`, `object_mapping`, `column_mapping` | `registry.source_system` + `teiid_*.name_in_source` | ASCAM tidak membaca skema sumber secara langsung (ego sektoral); pemetaan sumber↔Teiid diwakili `NameInSource` (ADR-0002, ADR-0003) |
| `view_definition`, `object_dependency`, `column_dependency` | `spec.teiid_view` + aturan konservatif D11 | Dependensi kolom pada view tidak tersedia di tabel `SYS`; dieskalasi ke HITL |
| `foreign_key` | Ditunda | Belum dipakai analisis dampak pada batasan penelitian |
| `R2RML_MAPPING` | `spec.artifact` (kind = r2rml) | Isi berkas disimpan per versi |
| `TRIPLES_MAP`, `LOGICAL_TABLE` | `spec.triples_map` | Logical table melekat 1:1 pada triples map |
| `SUBJECT_MAP`, `PREDICATE_MAP`, `OBJECT_MAP`, `TERM_MAP` | `spec.term_map` (kolom `position`) | Satu tabel term map generik |
| `PREDICATE_OBJECT_MAP`, `REF_OBJECT_MAP`, `JOIN_CONDITION`, `CLASS_ASSERTION` | `spec.predicate_object_map`, `spec.term_map` (`value_kind` = parent_triples_map), `spec.join_condition`, `spec.subject_class` | Setara |
| `LOGICAL_COLUMN`, `TERM_COLUMN_REFFERENCE`, `SQL_COLUMN_DEPENDENCY` | `spec.column_usage` | Satu tabel lineage dengan `role` |
| `Ontology`, `Ontology_version` | `spec.ontology` + `spec.spec_version` | Versi ontologi mengikuti versi spesifikasi |
| `Ontology_resource`, `Owl_property` | `spec.ont_entity` | Satu registri entitas |
| `Owl_property_domain`, `Owl_property_range`, `Owl_property_hierarchy` | `spec.ont_domain`, `spec.ont_range`, `spec.ont_subproperty` | Setara |
| `Owl_property_characteristic` | `spec.ont_entity.functional` | Hanya karakteristik yang relevan bagi DatatypeProperty |
| `Owl_datatype_restriction` | Ditunda | Belum dipakai pola adaptasi |
| (belum ada) | `registry.*`, `ops.*` | Konfigurasi, kredensial, dan jejak MAPE-K |

## 5. Implementasi (F1.2 dan seterusnya)

- **Migrasi skema:** SQLAlchemy 2 + Alembic (riwayat migrasi terversi).
- **Driver:** psycopg 3 (ADR-0003).
- **Layanan:** Knowledge Service (FastAPI) sebagai satu-satunya pemilik basis data (D1),
  dengan role basis data terpisah per layanan (D4).
- **Pengujian:** skema diuji dengan PostgreSQL sementara; sync pertama diuji terhadap
  spesifikasi OBDF studi kasus dan dibandingkan dengan hasil F0.3 dan F0.5.

## Referensi

- Blair, G., Bencomo, N., France, R. B. (2009). Models@run.time. *Computer*, 42(10), 22–27. https://doi.org/10.1109/MC.2009.326
- Kephart, J. O., Chess, D. M. (2003). The Vision of Autonomic Computing. *Computer*, 36(1), 41–50. https://doi.org/10.1109/MC.2003.1160055
- Fowler, M. (2005). Event Sourcing. https://martinfowler.com/eaaDev/EventSourcing.html
- W3C (2013). PROV-O: The PROV Ontology. https://www.w3.org/TR/prov-o/
- W3C (2012). R2RML: RDB to RDF Mapping Language. https://www.w3.org/TR/r2rml/
- Proposal tesis, Sub-bab 3.6 (hlm. 81–92), Tabel 3.21–3.23.
- ADR-0002 s.d. ADR-0005 (`docs/adr/`).
