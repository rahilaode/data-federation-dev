# ASCAM Knowledge

Implementasi komponen Knowledge. Rancangan: `docs/knowledge/README.md`.
Keputusan: ADR-0007 (skema dan integritas), ADR-0008 (Knowledge Service).

```
knowledge/
├── docker-compose.yaml       knowledge-db, knowledge-migrate, knowledge-service
├── config/obdf-bansos.yaml   konfigurasi deklaratif OBDF studi kasus (diterapkan saat start)
├── db-init/                  pembuatan role aplikasi ascam_app pada inisialisasi pertama
├── scripts/init-secrets.sh   membuat berkas rahasia di secrets/ (tidak di-commit)
└── service/
    ├── src/ascam_knowledge/  model, API (FastAPI), keamanan, uji koneksi, sync, CLI
    ├── migrations/           0001 skema awal + integritas; 0002 nama kredensial; 0003 hasil uji koneksi JSONB; 0004 sidik jari isi versi
    ├── tests/                uji migrasi, integritas, dan API
    └── scripts/test.sh       uji terhadap PostgreSQL sementara
```

## Menjalankan

```bash
setup/ascam/knowledge/scripts/init-secrets.sh        # aman dijalankan ulang (tidak menimpa)
docker compose -f setup/ascam/knowledge/docker-compose.yaml up -d --build
```

Layanan tersedia di `http://127.0.0.1:18000` (dokumentasi interaktif: `/docs`).
Token klien ada di `secrets/knowledge_api_tokens` (format `<klien>:<token>`).

```bash
TOKEN=$(grep '^ui:' setup/ascam/knowledge/secrets/knowledge_api_tokens | cut -d: -f2)
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/api/v1/obdf
```

## Uji koneksi target

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/api/v1/obdf/1/checks
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/api/v1/obdf/1/status
```

Hasil disimpan di `registry.connection_check` (tanpa rahasia) dan menjadi sumber dasbor UI.

## Sync spesifikasi OBDF

```bash
curl -s -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/api/v1/obdf/1/sync
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/api/v1/obdf/1/versions
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/api/v1/versions/1
curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:18000/api/v1/versions/1/artifacts/r2rml
```

Sync mengambil Σ_S dari Teiid (ODBC + management API) dan artefak ℳ/𝒯 dari Ontop Agent,
lalu menyimpannya sebagai satu versi spesifikasi bila isinya berubah (ADR-0012).

## Konfigurasi deklaratif

Dokumen `config/*.yaml` (versi 1) memuat OBDF, sumber, kredensial (hanya `secret_file`),
target, pengaturan, kebijakan penamaan, dan pemetaan tipe. Penerapan bersifat idempoten,
non-destruktif, dan atomik. Selain saat start, dapat diterapkan lewat
`POST /api/v1/config/apply` atau `ascam-knowledge apply-config <berkas>`.

Placeholder `property_iri_template`: `{namespace}`, `{column}`, `{column_camel}`, `{class_local}`.

## Menguji

```bash
setup/ascam/knowledge/service/scripts/test.sh
```

## Menambah tabel di skema `spec`

Tabel baru wajib memiliki `spec_version_id`, memakai FK komposit untuk rujukan antartabel
`spec`, dan mendapat trigger `trg_guard_sealed` di migrasinya. Uji
`test_every_spec_table_is_versioned` dan `test_integrity_triggers_installed` menjaga aturan ini.
