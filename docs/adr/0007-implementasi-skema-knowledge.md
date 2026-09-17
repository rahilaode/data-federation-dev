# ADR-0007: Implementasi skema Knowledge dan penegakan integritas di basis data

- **Status:** Diterima
- **Tanggal:** 2026-09-17
- **Fase MAPE-K:** Knowledge
- **Terkait:** rancangan Knowledge v2 (`docs/knowledge/README.md`), D1, D3, D4

## Konteks

Rancangan Knowledge v2 menetapkan snapshot per versi (P2), riwayat yang tidak diubah (P3),
dan kredensial aman (P7). Prinsip tersebut perlu ditegakkan secara teknis, bukan hanya
diandalkan pada disiplin kode aplikasi, karena Knowledge dipakai beberapa layanan dan UI.

## Keputusan

1. **Model SQLAlchemy 2 adalah sumber tunggal struktur tabel.** Migrasi Alembic dihasilkan
   dari model lalu dilengkapi SQL integritas; `alembic check` pada pengujian memastikan
   model dan migrasi tidak menyimpang.
2. **Nilai enumerasi disimpan sebagai `TEXT` + `CHECK`**, bukan tipe `ENUM` PostgreSQL yang
   sulit diubah lewat migrasi. Nama constraint mengikuti *naming convention* tetap.
3. **Setiap tabel `spec` membawa `spec_version_id`**, dan rujukan antartabel `spec` memakai
   foreign key komposit `(spec_version_id, id)`. Rujukan lintas versi ditolak basis data.
4. **Isi versi hanya dapat diubah selama status `candidate`** (trigger
   `spec.guard_sealed_rows` pada 30 tabel isi). Versi baru wajib berstatus `candidate`;
   atribut versi yang telah disegel tidak dapat diubah (kecuali `note` dan
   `teiid_connection_type`); transisi status dibatasi:
   `candidate→active|rejected`, `active→superseded|rolled_back`, `superseded→active`,
   `superseded|rejected|rolled_back→purged`. Hanya satu versi `active` per OBDF
   (indeks unik parsial).
5. **Baris versi tidak pernah dihapus (tombstone).** Retensi memakai
   `spec.purge_version(id)`: menghapus isi versi lalu mengubah status menjadi `purged`.
   Alasan: versi lama selalu menjadi induk versi berikutnya dan dirujuk jejak `ops`
   (rencana, eksekusi, validasi); menghapus barisnya akan memutus riwayat. Purge hanya
   diizinkan bagi anggota pemilik skema yang menyetel `ascam.allow_purge = 'on'`.
6. **Skema `ops` bersifat append-only** (baris tidak dihapus kecuali purge oleh pemilik) dan
   `ops.audit_log` tidak dapat diubah.
7. **Pemisahan role (D4):** `ascam_owner` memiliki skema dan menjalankan migrasi;
   `ascam_app` hanya memiliki hak DML dan eksekusi fungsi. Kata sandi keduanya berasal dari
   Docker secrets; URL basis data dibangun dari berkas rahasia, bukan variabel lingkungan.

## Bukti

Pengujian (`setup/ascam/knowledge/service/tests/`, 23 uji) terhadap PostgreSQL 16:
naik–turun–naik migrasi dan `alembic check`; jumlah tabel per skema (8/31/9) dan trigger;
penyegelan versi (INSERT/UPDATE/DELETE ditolak setelah `active`); rujukan lintas versi
ditolak; satu versi aktif; enam jalur transisi status; atribut versi imutabel; baris versi
tidak dapat dihapus; purge menghapus isi, mempertahankan tombstone dan rujukan `parent_id`,
dan tidak menyentuh versi lain; role aplikasi tidak dapat melakukan purge meskipun menyetel
`ascam.allow_purge`; `ops` append-only dan `audit_log` imutabel; `event_uid` unik;
constraint `CHECK`; FK melingkar `spec_version.plan_id`; view `spec.active_version`.
Pemeriksaan manual: role aplikasi dapat melakukan DML dan membaca view, tetapi ditolak saat
membuat tabel.

## Konsekuensi

- Kesalahan aplikasi yang melanggar P2/P3 gagal di basis data, bukan diam-diam merusak riwayat.
- Menambah satu tabel `spec` di migrasi berikutnya **wajib** menyertakan `spec_version_id`
  dan trigger `trg_guard_sealed`; uji `test_every_spec_table_is_versioned` dan
  `test_integrity_triggers_installed` akan gagal bila terlewat.
- Pengaman purge bukan pengganti pengelolaan hak akses: variabel sesi dapat disetel siapa
  pun, sehingga pembatasan sesungguhnya ada pada pemeriksaan keanggotaan pemilik skema.
- Sesi uji yang menguji role aplikasi dilewati bila role pengujian tidak dapat membuat role.

## Referensi

- SQLAlchemy 2.0 — Constraint naming conventions: https://docs.sqlalchemy.org/en/20/core/constraints.html#configuring-constraint-naming-conventions
- Alembic — `alembic check`: https://alembic.sqlalchemy.org/en/latest/autogenerate.html
- PostgreSQL 16 — Trigger functions (PL/pgSQL): https://www.postgresql.org/docs/16/plpgsql-trigger.html
- Docker Official Image `postgres` — `POSTGRES_PASSWORD_FILE` dan `/docker-entrypoint-initdb.d`: https://hub.docker.com/_/postgres
