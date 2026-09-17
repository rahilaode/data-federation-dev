# ASCAM Knowledge

Implementasi komponen Knowledge (rancangan: `docs/knowledge/README.md`; keputusan: ADR-0007).

```
knowledge/
├── docker-compose.yaml      knowledge-db (PostgreSQL 16) + knowledge-migrate (Alembic)
├── db-init/                 pembuatan role aplikasi ascam_app saat volume pertama kali dibuat
├── scripts/init-secrets.sh  membuat kata sandi acak di secrets/ (tidak di-commit)
└── service/
    ├── src/ascam_knowledge/ model SQLAlchemy (registry, spec, ops) dan konfigurasi koneksi
    ├── migrations/          migrasi Alembic (0001: skema awal + integritas)
    ├── tests/               uji migrasi dan integritas
    └── scripts/test.sh      menjalankan uji terhadap PostgreSQL sementara
```

## Menjalankan

```bash
setup/ascam/knowledge/scripts/init-secrets.sh          # sekali saja
docker compose -f setup/ascam/knowledge/docker-compose.yaml up -d --build
docker logs ascam-knowledge-migrate                     # harus berakhir dengan "Running upgrade -> 0001"
```

Basis data dapat diinspeksi dari host lewat `127.0.0.1:55432`
(pengguna `ascam_owner`, kata sandi di `secrets/knowledge_db_owner_password`).

## Menguji

```bash
setup/ascam/knowledge/service/scripts/test.sh
```

Uji menghapus dan membuat ulang skema, sehingga dijalankan pada PostgreSQL sementara,
bukan pada `ascam-knowledge-db`.

## Menambah tabel di skema `spec`

Tabel baru wajib memiliki `spec_version_id`, memakai FK komposit untuk rujukan antartabel
`spec`, dan mendapat trigger `trg_guard_sealed` di migrasinya. Uji
`test_every_spec_table_is_versioned` dan `test_integrity_triggers_installed` menjaga aturan ini.
