-- Tabel 1: program_bansos
CREATE TABLE program_bansos (
    program_id      SERIAL PRIMARY KEY,
    nama_program    VARCHAR(255) NOT NULL,
    tipe_program    VARCHAR(100),         -- e.g., "PKH", "BPNT", "BST"
    nominal         NUMERIC(15,2),
    periode_mulai   DATE,
    periode_selesai DATE
);

-- Tabel 2: penerima_manfaat (versi Kemensos — kolom berbeda dari master_penduduk)
CREATE TABLE penerima_manfaat (
    penerima_id     SERIAL PRIMARY KEY,
    nik             CHAR(16) NOT NULL,
    nama_lengkap    VARCHAR(255),         -- beda nama kolom vs "nama" di source 2
    tgl_lahir       DATE,                 -- beda nama kolom vs "tanggal_lahir"
    status_ekonomi  VARCHAR(50),          -- berbeda konsep vs "penghasilan" di source 2
    no_kartu_keluarga CHAR(16),           -- beda nama vs "no_kk"
    aktif           BOOLEAN DEFAULT TRUE
);

-- Tabel 3: transaksi_bansos
CREATE TABLE transaksi_bansos (
    transaksi_id    SERIAL PRIMARY KEY,
    program_id      INT REFERENCES program_bansos(program_id),
    penerima_id     INT REFERENCES penerima_manfaat(penerima_id),
    periode         VARCHAR(7),           -- format "YYYY-MM"
    status          VARCHAR(50),          -- "TERSALURKAN", "GAGAL", "PENDING"
    created_at      TIMESTAMP DEFAULT NOW()
);

-- Tabel 4: eligibility_check
CREATE TABLE eligibility_check (
    check_id        SERIAL PRIMARY KEY,
    program_id      INT REFERENCES program_bansos(program_id),
    penerima_id     INT REFERENCES penerima_manfaat(penerima_id),
    status_eligible VARCHAR(20),          -- "ELIGIBLE", "TIDAK ELIGIBLE"
    alasan          TEXT,
    validated_at    TIMESTAMP,
    validated_by    VARCHAR(100)
);


-- ============================================================
-- DATA SOURCE 1 — PostgreSQL (Kemensos / Bansos)
-- ============================================================

-- program_bansos
INSERT INTO program_bansos (nama_program, tipe_program, nominal, periode_mulai, periode_selesai) VALUES
('Program Keluarga Harapan',        'PKH',  300000,  '2024-01-01', '2024-12-31'),
('Bantuan Pangan Non Tunai',        'BPNT', 200000,  '2024-01-01', '2024-12-31'),
('Bantuan Sosial Tunai',            'BST',  600000,  '2024-03-01', '2024-06-30'),
('PKH Pendidikan Anak',             'PKH',  450000,  '2024-01-01', '2024-12-31'),
('Subsidi Sembako Lansia',          'BPNT', 250000,  '2024-06-01', '2024-12-31');

-- penerima_manfaat
INSERT INTO penerima_manfaat (nik, nama_lengkap, tgl_lahir, status_ekonomi, no_kartu_keluarga, aktif) VALUES
('7371010101800001', 'Siti Rahma Wati',       '1980-01-01', 'Sangat Miskin',   '7371010101800001', TRUE),
('7371010203750002', 'Baharuddin Lallo',      '1975-03-02', 'Miskin',          '7371010203750002', TRUE),
('7371030405820003', 'Hasnah Binti Ahmad',    '1982-04-05', 'Hampir Miskin',   '7371030405820003', TRUE),
('7371050607900004', 'Ridwan Saputra',        '1990-06-07', 'Sangat Miskin',   '7371050607900004', TRUE),
('7371070809850005', 'Nurhayati Mansur',      '1985-08-09', 'Miskin',          '7371070809850005', TRUE),
('7371091011780006', 'Kamaruddin Tahir',      '1978-10-11', 'Hampir Miskin',   '7371091011780006', TRUE),
('7371111213920007', 'Fatmawati Darwis',      '1992-12-13', 'Miskin',          '7371111213920007', TRUE),
('7371131415880008', 'Syamsuddin Rahim',      '1988-02-14', 'Sangat Miskin',   '7371131415880008', TRUE),
('7371151617960009', 'Rahmawati Sulaiman',    '1996-04-16', 'Miskin',          '7371151617960009', TRUE),
('7371171819830010', 'Muh. Arif Hidayat',     '1983-06-18', 'Hampir Miskin',   '7371171819830010', TRUE);

-- eligibility_check
INSERT INTO eligibility_check (program_id, penerima_id, status_eligible, alasan, validated_at, validated_by) VALUES
(1, 1, 'ELIGIBLE',       'Memenuhi kriteria pendapatan',       '2024-01-10 09:00:00', 'admin_kemensos'),
(1, 2, 'ELIGIBLE',       'Memenuhi kriteria pendapatan',       '2024-01-10 09:15:00', 'admin_kemensos'),
(1, 3, 'TIDAK ELIGIBLE', 'Pendapatan di atas batas',           '2024-01-10 09:30:00', 'admin_kemensos'),
(2, 1, 'ELIGIBLE',       'Termasuk kategori keluarga miskin',  '2024-01-11 10:00:00', 'admin_kemensos'),
(2, 4, 'ELIGIBLE',       'Termasuk kategori keluarga miskin',  '2024-01-11 10:20:00', 'admin_kemensos'),
(2, 5, 'ELIGIBLE',       'Termasuk kategori keluarga miskin',  '2024-01-11 10:40:00', 'admin_kemensos'),
(3, 6, 'TIDAK ELIGIBLE', 'Tidak masuk periode program',        '2024-03-01 08:00:00', 'admin_kemensos'),
(3, 7, 'ELIGIBLE',       'Memenuhi semua kriteria',            '2024-03-01 08:30:00', 'admin_kemensos'),
(4, 8, 'ELIGIBLE',       'Ada tanggungan anak sekolah',        '2024-01-12 11:00:00', 'admin_kemensos'),
(5, 9, 'ELIGIBLE',       'Lansia dengan penghasilan rendah',   '2024-06-05 14:00:00', 'admin_kemensos');

-- transaksi_bansos
INSERT INTO transaksi_bansos (program_id, penerima_id, periode, status, created_at) VALUES
(1, 1, '2024-01', 'TERSALURKAN', '2024-01-15 10:00:00'),
(1, 1, '2024-02', 'TERSALURKAN', '2024-02-15 10:00:00'),
(1, 1, '2024-03', 'TERSALURKAN', '2024-03-15 10:00:00'),
(1, 2, '2024-01', 'TERSALURKAN', '2024-01-15 10:30:00'),
(1, 2, '2024-02', 'GAGAL',       '2024-02-15 10:30:00'),
(1, 2, '2024-03', 'TERSALURKAN', '2024-03-15 10:30:00'),
(2, 1, '2024-01', 'TERSALURKAN', '2024-01-20 09:00:00'),
(2, 4, '2024-01', 'TERSALURKAN', '2024-01-20 09:30:00'),
(2, 5, '2024-01', 'PENDING',     '2024-01-20 10:00:00'),
(3, 7, '2024-03', 'TERSALURKAN', '2024-03-05 11:00:00'),
(4, 8, '2024-01', 'TERSALURKAN', '2024-01-25 14:00:00'),
(4, 8, '2024-02', 'TERSALURKAN', '2024-02-25 14:00:00'),
(5, 9, '2024-06', 'TERSALURKAN', '2024-06-10 09:00:00'),
(5, 9, '2024-07', 'PENDING',     '2024-07-10 09:00:00'),
(1, 10,'2024-01', 'TERSALURKAN', '2024-01-15 11:00:00');


-- ============================================================
-- ============================================================
-- PostgreSQL: DDL Trigger - Hanya Monitor ALTER TABLE
--   - ADD COLUMN
--   - DROP COLUMN
--   - RENAME COLUMN
-- ============================================================

CREATE SCHEMA IF NOT EXISTS schema_monitor;

-- ============================================================
-- 1. TABEL REFERENSI
-- ============================================================

CREATE TABLE IF NOT EXISTS schema_monitor.captured_tables_ref (
    id          SERIAL PRIMARY KEY,
    schema_name VARCHAR(128) NOT NULL DEFAULT 'public',
    table_name  VARCHAR(128) NOT NULL,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE,
    description TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_captured_tables UNIQUE (schema_name, table_name)
);

COMMENT ON TABLE schema_monitor.captured_tables_ref
    IS 'Daftar tabel yang diizinkan untuk di-monitor (regulasi).';

-- Seed contoh
INSERT INTO schema_monitor.captured_tables_ref (schema_name, table_name, description)
VALUES
    ('public', 'program_bansos ',    'Tabel program bantuan sosial'),
    ('public', 'penerima_manfaat',   'Tabel data penerima manfaat bansos'),
    ('public', 'transaksi_bansos',   'Tabel transaksi penyaluran bansos'),
    ('public', 'eligibility_check',  'Tabel hasil cek kelayakan penerima bansos')
ON CONFLICT DO NOTHING;


-- ============================================================
-- 2. TABEL EVENT LOG
-- ============================================================

CREATE TABLE IF NOT EXISTS schema_monitor.ddl_event_log (
    id           BIGSERIAL PRIMARY KEY,
    username     VARCHAR(128) NOT NULL DEFAULT CURRENT_USER,
    schema_name  VARCHAR(128),
    table_name   VARCHAR(256),
    object_tag   VARCHAR(512),       -- e.g. 'public.orders'
    command_tag  VARCHAR(64),        -- selalu 'ALTER TABLE'
    alter_type   VARCHAR(32),        -- 'ADD COLUMN' | 'DROP COLUMN' | 'RENAME COLUMN'
    column_name  VARCHAR(256),       -- nama kolom yang terlibat
    ddl_command  TEXT,               -- teks DDL lengkap
    is_regulated BOOLEAN DEFAULT FALSE,
    captured_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE schema_monitor.ddl_event_log
    IS 'Log event ALTER TABLE: hanya ADD COLUMN, DROP COLUMN, RENAME COLUMN.';


-- ============================================================
-- 3. FUNGSI EVENT TRIGGER
-- ============================================================

CREATE OR REPLACE FUNCTION schema_monitor.fn_capture_alter_column()
RETURNS event_trigger
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
    v_obj          RECORD;
    v_ddl          TEXT;
    v_upper_ddl    TEXT;
    v_alter_type   VARCHAR(32);
    v_column_name  VARCHAR(256);
    v_is_regulated BOOLEAN;
    v_table_name   VARCHAR(256);
    v_schema_name  VARCHAR(128);
BEGIN
    -- Ambil teks DDL dari sesi aktif
    SELECT query INTO v_ddl
    FROM pg_stat_activity
    WHERE pid = pg_backend_pid();

    v_upper_ddl := upper(coalesce(v_ddl, ''));

    -- Hanya proses jika mengandung operasi kolom yang dimonitor
    -- Abaikan ALTER TABLE lainnya (ADD CONSTRAINT, SET DEFAULT, dll.)
    IF v_upper_ddl NOT LIKE '%ADD COLUMN%'
   AND v_upper_ddl NOT LIKE '%DROP COLUMN%'
   AND v_upper_ddl NOT LIKE '%RENAME COLUMN%' THEN
        RETURN;
    END IF;

    -- Tentukan jenis operasi
    IF    v_upper_ddl LIKE '%RENAME COLUMN%' THEN v_alter_type := 'RENAME COLUMN';
    ELSIF v_upper_ddl LIKE '%ADD COLUMN%'    THEN v_alter_type := 'ADD COLUMN';
    ELSIF v_upper_ddl LIKE '%DROP COLUMN%'   THEN v_alter_type := 'DROP COLUMN';
    END IF;

    FOR v_obj IN
        SELECT * FROM pg_event_trigger_ddl_commands()
        WHERE command_tag = 'ALTER TABLE'
    LOOP
        v_schema_name := v_obj.schema_name;
        v_table_name  := split_part(v_obj.object_identity, '.', 2);

        -- Ekstrak nama kolom: ambil token pertama setelah keyword operasi
        v_column_name := trim(
            split_part(
                regexp_replace(v_ddl, '\s+', ' ', 'g'),
                v_alter_type || ' ',
                2
            )
        );
        v_column_name := split_part(v_column_name, ' ', 1);

        -- Cek apakah tabel ini terdaftar di referensi
        SELECT EXISTS (
            SELECT 1 FROM schema_monitor.captured_tables_ref
            WHERE is_active   = TRUE
              AND schema_name = v_schema_name
              AND table_name  = v_table_name
        ) INTO v_is_regulated;

        INSERT INTO schema_monitor.ddl_event_log
            (username, schema_name, table_name, object_tag,
             command_tag, alter_type, column_name, ddl_command, is_regulated)
        VALUES
            (SESSION_USER,
             v_schema_name,
             v_table_name,
             v_obj.object_identity,
             'ALTER TABLE',
             v_alter_type,
             v_column_name,
             v_ddl,
             v_is_regulated);
    END LOOP;
END;
$$;


-- ============================================================
-- 4. REGISTRASI EVENT TRIGGER
-- ============================================================

DROP EVENT TRIGGER IF EXISTS trg_monitor_alter_column;

CREATE EVENT TRIGGER trg_monitor_alter_column
    ON ddl_command_end
    WHEN TAG IN ('ALTER TABLE')
    EXECUTE FUNCTION schema_monitor.fn_capture_alter_column();


-- ============================================================
-- 5. CONTOH QUERY MONITORING
-- ============================================================

-- Semua event yang ter-capture
-- SELECT * FROM schema_monitor.ddl_event_log ORDER BY captured_at DESC;

-- Hanya tabel yang diregulasi
-- SELECT alter_type, table_name, column_name, ddl_command, captured_at
-- FROM   schema_monitor.ddl_event_log
-- WHERE  is_regulated = TRUE
-- ORDER  BY captured_at DESC;

-- Rekapitulasi per jenis operasi
-- SELECT alter_type, COUNT(*) AS total
-- FROM   schema_monitor.ddl_event_log
-- GROUP  BY alter_type;