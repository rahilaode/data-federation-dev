-- =============================================================================
-- Tiga basis data Dinas Sosial provinsi dengan skema IDENTIK.
-- Syarat multi-source model Teiid: semua sumber memakai metadata fisik yang sama
-- (teiid-documents, bagian "MultiSource Models", hlm. 366).
-- Dijalankan sekali oleh image postgres saat volume data masih kosong.
-- =============================================================================

CREATE DATABASE dinsos_sulsel;
CREATE DATABASE dinsos_jabar;
CREATE DATABASE dinsos_dki;

-- ── Sulawesi Selatan ──────────────────────────────────────────────────────────
\connect dinsos_sulsel
CREATE TABLE penerima_manfaat (
    penerima_id   SERIAL PRIMARY KEY,
    nik           CHAR(16) NOT NULL,
    nama_lengkap  VARCHAR(255),
    email         VARCHAR(100)
);
INSERT INTO penerima_manfaat (nik, nama_lengkap, email) VALUES
    ('7371010101900001', 'Andi Pratama',   'andi@contoh.id'),
    ('7371010101900002', 'Rahmawati',      'rahma@contoh.id');

-- ── Jawa Barat ────────────────────────────────────────────────────────────────
\connect dinsos_jabar
CREATE TABLE penerima_manfaat (
    penerima_id   SERIAL PRIMARY KEY,
    nik           CHAR(16) NOT NULL,
    nama_lengkap  VARCHAR(255),
    email         VARCHAR(100)
);
INSERT INTO penerima_manfaat (nik, nama_lengkap, email) VALUES
    ('3273010101900003', 'Budi Santoso',   'budi@contoh.id'),
    ('3273010101900004', 'Siti Nurhaliza', 'siti@contoh.id');

-- ── DKI Jakarta ───────────────────────────────────────────────────────────────
\connect dinsos_dki
CREATE TABLE penerima_manfaat (
    penerima_id   SERIAL PRIMARY KEY,
    nik           CHAR(16) NOT NULL,
    nama_lengkap  VARCHAR(255),
    email         VARCHAR(100)
);
INSERT INTO penerima_manfaat (nik, nama_lengkap, email) VALUES
    ('3171010101900005', 'Citra Lestari',  'citra@contoh.id');
