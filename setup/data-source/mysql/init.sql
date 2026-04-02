-- ============================================================
-- DATA SOURCE 2 — MySQL (Dukcapil / Kependudukan)
-- ============================================================

-- master_wilayah
CREATE TABLE master_wilayah (
    wilayah_id  INT PRIMARY KEY AUTO_INCREMENT,
    provinsi    VARCHAR(100),
    kabupaten   VARCHAR(100),
    kecamatan   VARCHAR(100),
    desa        VARCHAR(100)
);

-- master_keluarga
CREATE TABLE master_keluarga (
    no_kk       CHAR(16) PRIMARY KEY,
    wilayah_id  INT,
    created_at  DATETIME DEFAULT NOW(),
    FOREIGN KEY (wilayah_id) REFERENCES master_wilayah(wilayah_id)
);

-- master_penduduk
CREATE TABLE master_penduduk (
    nik             CHAR(16) PRIMARY KEY,
    no_kk           CHAR(16),
    nama            VARCHAR(255),
    tanggal_lahir   DATE,
    pekerjaan       VARCHAR(100),
    penghasilan     NUMERIC(15,2),
    status_hidup    VARCHAR(20),
    created_at      DATETIME DEFAULT NOW(),
    FOREIGN KEY (no_kk) REFERENCES master_keluarga(no_kk)
);

-- riwayat_perubahan_data
CREATE TABLE riwayat_perubahan_data (
    riwayat_id      INT PRIMARY KEY AUTO_INCREMENT,
    nik             CHAR(16),
    kolom_diubah    VARCHAR(100),
    nilai_lama      VARCHAR(500),
    nilai_baru      VARCHAR(500),
    diubah_pada     DATETIME DEFAULT NOW(),
    diubah_oleh     VARCHAR(100),
    FOREIGN KEY (nik) REFERENCES master_penduduk(nik)
);

-- ============================================================
-- INSERT DATA
-- ============================================================

-- master_wilayah
INSERT INTO master_wilayah (provinsi, kabupaten, kecamatan, desa) VALUES
('Sulawesi Selatan', 'Kota Makassar',   'Tamalate',       'Parang Tambung'),
('Sulawesi Selatan', 'Kota Makassar',   'Rappocini',      'Banta-Bantaeng'),
('Sulawesi Selatan', 'Kota Makassar',   'Makassar',       'Lariang Bangi'),
('Sulawesi Selatan', 'Kota Makassar',   'Ujung Pandang',  'Lajangiru'),
('Sulawesi Selatan', 'Kota Makassar',   'Manggala',       'Tamangapa'),
('Sulawesi Selatan', 'Kab. Gowa',       'Somba Opu',      'Sungguminasa'),
('Sulawesi Selatan', 'Kab. Gowa',       'Pallangga',      'Pallangga'),
('Sulawesi Selatan', 'Kab. Maros',      'Turikale',       'Turikale'),
('Sulawesi Selatan', 'Kota Parepare',   'Bacukiki',       'Lompoe'),
('Sulawesi Selatan', 'Kab. Bone',       'Tanete Riattang','Watampone');

-- master_keluarga
INSERT INTO master_keluarga (no_kk, wilayah_id, created_at) VALUES
('7371010101800001', 1,  '2010-05-01 00:00:00'),
('7371010203750002', 2,  '2008-03-15 00:00:00'),
('7371030405820003', 3,  '2012-07-20 00:00:00'),
('7371050607900004', 4,  '2015-11-10 00:00:00'),
('7371070809850005', 1,  '2011-02-28 00:00:00'),
('7371091011780006', 5,  '2009-09-05 00:00:00'),
('7371111213920007', 6,  '2016-04-12 00:00:00'),
('7371131415880008', 7,  '2013-08-22 00:00:00'),
('7371151617960009', 2,  '2018-01-30 00:00:00'),
('7371171819830010', 8,  '2010-12-11 00:00:00');

-- master_penduduk
INSERT INTO master_penduduk (nik, no_kk, nama, tanggal_lahir, pekerjaan, penghasilan, status_hidup, created_at) VALUES
('7371010101800001', '7371010101800001', 'Siti Rahma Wati',       '1980-01-01', 'Buruh Harian',     850000,  'HIDUP', '2010-05-01 00:00:00'),
('7371010203750002', '7371010203750002', 'Baharuddin Lallo',      '1975-03-02', 'Petani',           1200000, 'HIDUP', '2008-03-15 00:00:00'),
('7371030405820003', '7371030405820003', 'Hasnah Binti Ahmad',    '1982-04-05', 'Pedagang Kecil',   1800000, 'HIDUP', '2012-07-20 00:00:00'),
('7371050607900004', '7371050607900004', 'Ridwan Saputra',        '1990-06-07', 'Buruh Harian',     750000,  'HIDUP', '2015-11-10 00:00:00'),
('7371070809850005', '7371070809850005', 'Nurhayati Mansur',      '1985-08-09', 'Ibu Rumah Tangga', 0,       'HIDUP', '2011-02-28 00:00:00'),
('7371091011780006', '7371091011780006', 'Kamaruddin Tahir',      '1978-10-11', 'Nelayan',          1500000, 'HIDUP', '2009-09-05 00:00:00'),
('7371111213920007', '7371111213920007', 'Fatmawati Darwis',      '1992-12-13', 'Buruh Pabrik',     1100000, 'HIDUP', '2016-04-12 00:00:00'),
('7371131415880008', '7371131415880008', 'Syamsuddin Rahim',      '1988-02-14', 'Tukang Ojek',      900000,  'HIDUP', '2013-08-22 00:00:00'),
('7371151617960009', '7371151617960009', 'Rahmawati Sulaiman',    '1996-04-16', 'Asisten RT',       800000,  'HIDUP', '2018-01-30 00:00:00'),
('7371171819830010', '7371171819830010', 'Muh. Arif Hidayat',     '1983-06-18', 'Kuli Bangunan',    1300000, 'HIDUP', '2010-12-11 00:00:00'),
('7371010101800011', '7371010101800001', 'Ahmad Fauzi Wati',      '2005-03-15', 'Pelajar',          0,       'HIDUP', '2010-05-01 00:00:00'),
('7371010101800012', '7371010101800001', 'Nur Alya Wati',         '2008-07-22', 'Pelajar',          0,       'HIDUP', '2010-05-01 00:00:00'),
('7371010203750013', '7371010203750002', 'Hasriani Lallo',        '1977-09-10', 'Ibu Rumah Tangga', 0,       'HIDUP', '2008-03-15 00:00:00'),
('7371050607900014', '7371050607900004', 'Dewi Saputra',          '1993-11-05', 'Pedagang Kecil',   950000,  'HIDUP', '2015-11-10 00:00:00'),
('7371131415880015', '7371131415880008', 'Andi Rahmat Rahim',     '2010-05-30', 'Pelajar',          0,       'HIDUP', '2013-08-22 00:00:00');

-- riwayat_perubahan_data
INSERT INTO riwayat_perubahan_data (nik, kolom_diubah, nilai_lama, nilai_baru, diubah_pada, diubah_oleh) VALUES
('7371010203750002', 'pekerjaan',   'Buruh Harian',      'Petani',            '2022-03-10 08:30:00', 'operator_dukcapil'),
('7371010203750002', 'penghasilan', '850000',            '1200000',           '2022-03-10 08:30:00', 'operator_dukcapil'),
('7371030405820003', 'nama',        'Hasnah Ahmad',      'Hasnah Binti Ahmad','2021-06-15 10:00:00', 'operator_dukcapil'),
('7371070809850005', 'penghasilan', '500000',            '0',                 '2023-01-20 09:15:00', 'operator_dukcapil'),
('7371091011780006', 'pekerjaan',   'Buruh Harian',      'Nelayan',           '2020-08-05 14:00:00', 'operator_dukcapil'),
('7371111213920007', 'penghasilan', '950000',            '1100000',           '2023-07-01 11:30:00', 'operator_dukcapil'),
('7371151617960009', 'pekerjaan',   'Pengangguran',      'Asisten RT',        '2024-02-14 13:00:00', 'operator_dukcapil'),
('7371131415880015', 'status_hidup','HIDUP',             'HIDUP',             '2023-09-01 10:00:00', 'operator_dukcapil');



-- ============================================================
-- ============================================================
-- MySQL: DDL Capture - Hanya Monitor ALTER TABLE
--   - ADD COLUMN
--   - DROP COLUMN
--   - RENAME COLUMN
-- ============================================================
-- Catatan: MySQL tidak mendukung DDL trigger native.
-- Solusi: Stored Procedure wrapper + DML trigger pada tabel event.
-- ============================================================

-- CREATE DATABASE IF NOT EXISTS schema_monitor;
-- USE schema_monitor;


-- ============================================================
-- 1. TABEL REFERENSI
-- ============================================================

CREATE TABLE IF NOT EXISTS captured_tables_ref (
    id          INT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    db_name     VARCHAR(128) NOT NULL,
    table_name  VARCHAR(128) NOT NULL,
    is_active   TINYINT(1)   NOT NULL DEFAULT 1,
    description TEXT,
    created_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_captured_tables UNIQUE (db_name, table_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Daftar tabel yang diizinkan untuk di-monitor (regulasi).';

-- Seed contoh
INSERT IGNORE INTO captured_tables_ref (db_name, table_name, description)
VALUES
    ('dukcapil', 'master_wilayah', 'Tabel master wilayah (provinsi, kabupaten, kecamatan, desa)'),
    ('dukcapil', 'master_keluarga', 'Tabel master keluarga (no_kk, wilayah_id)'),
    ('dukcapil', 'master_penduduk', 'Tabel master penduduk (nik, no_kk, nama, dll)'),
    ('dukcapil', 'riwayat_perubahan_data', 'Tabel riwayat perubahan data penduduk');


-- ============================================================
-- 2. TABEL EVENT LOG
-- ============================================================

CREATE TABLE IF NOT EXISTS ddl_event_log (
    id           BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    username     VARCHAR(128) NOT NULL DEFAULT (CURRENT_USER()),
    db_name      VARCHAR(128),
    table_name   VARCHAR(256),
    object_tag   VARCHAR(512),      -- e.g. 'myapp.orders'
    command_tag  VARCHAR(64),       -- selalu 'ALTER TABLE'
    alter_type   VARCHAR(32),       -- 'ADD COLUMN' | 'DROP COLUMN' | 'RENAME COLUMN'
    column_name  VARCHAR(256),      -- nama kolom yang terlibat
    ddl_command  TEXT,              -- teks DDL lengkap
    is_regulated TINYINT(1) DEFAULT 0,
    captured_at  DATETIME   NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Log event ALTER TABLE: hanya ADD COLUMN, DROP COLUMN, RENAME COLUMN.';


-- ============================================================
-- 3. STORED PROCEDURE: sp_capture_alter_column
--    Dipanggil oleh aplikasi/migration tool setelah setiap
--    ALTER TABLE yang mengandung ADD/DROP/RENAME COLUMN.
-- ============================================================

DELIMITER $$

DROP PROCEDURE IF EXISTS sp_capture_alter_column $$

CREATE PROCEDURE sp_capture_alter_column(
    IN p_db_name     VARCHAR(128),   -- nama database, e.g. 'myapp'
    IN p_table_name  VARCHAR(256),   -- nama tabel, e.g. 'orders'
    IN p_alter_type  VARCHAR(32),    -- 'ADD COLUMN' | 'DROP COLUMN' | 'RENAME COLUMN'
    IN p_column_name VARCHAR(256),   -- nama kolom yang terlibat
    IN p_ddl_command TEXT            -- teks DDL lengkap
)
BEGIN
    DECLARE v_is_regulated TINYINT DEFAULT 0;
    DECLARE v_upper_type   VARCHAR(32);

    SET v_upper_type := UPPER(TRIM(p_alter_type));

    -- Validasi: hanya proses 3 jenis operasi yang dimonitor
    IF v_upper_type NOT IN ('ADD COLUMN', 'DROP COLUMN', 'RENAME COLUMN') THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'sp_capture_alter_column: alter_type tidak dikenali. Gunakan ADD COLUMN | DROP COLUMN | RENAME COLUMN';
    END IF;

    -- Cek tabel referensi
    SELECT COUNT(*) INTO v_is_regulated
    FROM captured_tables_ref
    WHERE is_active  = 1
      AND db_name    = p_db_name
      AND table_name = p_table_name;

    -- Simpan ke log
    INSERT INTO ddl_event_log
        (username, db_name, table_name, object_tag,
         command_tag, alter_type, column_name, ddl_command, is_regulated)
    VALUES
        (CURRENT_USER(),
         p_db_name,
         p_table_name,
         CONCAT(p_db_name, '.', p_table_name),
         'ALTER TABLE',
         v_upper_type,
         p_column_name,
         p_ddl_command,
         v_is_regulated);
END $$

DELIMITER ;


-- ============================================================
-- 4. TABEL EVENT (titik masuk Debezium CDC)
--    Dibuat di database target (myapp).
--    Aplikasi INSERT ke sini; trigger mem-forward ke log resmi.
-- ============================================================

-- USE myapp;  -- ganti ke database target Anda

CREATE TABLE IF NOT EXISTS ddl_event_capture (
    id           BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    username     VARCHAR(128) NOT NULL DEFAULT (CURRENT_USER()),
    db_name      VARCHAR(128) NOT NULL,
    table_name   VARCHAR(256) NOT NULL,
    alter_type   VARCHAR(32)  NOT NULL,   -- 'ADD COLUMN' | 'DROP COLUMN' | 'RENAME COLUMN'
    column_name  VARCHAR(256),
    ddl_command  TEXT         NOT NULL,
    captured_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Titik masuk event DDL dari aplikasi / Debezium CDC.';


-- ============================================================
-- 5. DML TRIGGER: AFTER INSERT pada ddl_event_capture
--    Hanya meneruskan baris dengan alter_type yang dimonitor.
-- ============================================================

DELIMITER $$

DROP TRIGGER IF EXISTS trg_after_insert_ddl_event $$

CREATE TRIGGER trg_after_insert_ddl_event
AFTER INSERT ON ddl_event_capture
FOR EACH ROW
BEGIN
    DECLARE v_upper_type   VARCHAR(32);
    DECLARE v_is_regulated TINYINT DEFAULT 0;

    SET v_upper_type := UPPER(TRIM(NEW.alter_type));

    -- Filter ketat: hanya 3 jenis operasi
    IF v_upper_type IN ('ADD COLUMN', 'DROP COLUMN', 'RENAME COLUMN') THEN

        -- Cek referensi
        SELECT COUNT(*) INTO v_is_regulated
        FROM captured_tables_ref
        WHERE is_active  = 1
          AND db_name    = NEW.db_name
          AND table_name = NEW.table_name;

        -- Forward ke log resmi
        INSERT INTO ddl_event_log
            (username, db_name, table_name, object_tag,
             command_tag, alter_type, column_name, ddl_command, is_regulated)
        VALUES
            (NEW.username,
             NEW.db_name,
             NEW.table_name,
             CONCAT(NEW.db_name, '.', NEW.table_name),
             'ALTER TABLE',
             v_upper_type,
             NEW.column_name,
             NEW.ddl_command,
             v_is_regulated);

    END IF;
    -- Selain 3 jenis di atas: diabaikan, tidak dicatat
END $$

DELIMITER ;


-- ============================================================
-- 6. CONTOH PEMAKAIAN
-- ============================================================

-- Setelah: ALTER TABLE myapp.orders ADD COLUMN phone VARCHAR(20);
-- CALL sp_capture_alter_column(
--     'myapp', 'orders', 'ADD COLUMN', 'phone',
--     'ALTER TABLE myapp.orders ADD COLUMN phone VARCHAR(20)'
-- );

-- Setelah: ALTER TABLE myapp.orders DROP COLUMN fax;
-- CALL sp_capture_alter_column(
--     'myapp', 'orders', 'DROP COLUMN', 'fax',
--     'ALTER TABLE myapp.orders DROP COLUMN fax'
-- );

-- Setelah: ALTER TABLE myapp.orders RENAME COLUMN old_col TO new_col;
-- CALL sp_capture_alter_column(
--     'myapp', 'orders', 'RENAME COLUMN', 'old_col -> new_col',
--     'ALTER TABLE myapp.orders RENAME COLUMN old_col TO new_col'
-- );

-- ============================================================
-- 7. CONTOH QUERY MONITORING
-- ============================================================

-- Semua event
-- SELECT * FROM ddl_event_log ORDER BY captured_at DESC;

-- Hanya tabel yang diregulasi
-- SELECT alter_type, table_name, column_name, ddl_command, captured_at
-- FROM   ddl_event_log
-- WHERE  is_regulated = 1
-- ORDER  BY captured_at DESC;

-- Rekapitulasi per jenis operasi
-- SELECT alter_type, COUNT(*) AS total
-- FROM   ddl_event_log
-- GROUP  BY alter_type;