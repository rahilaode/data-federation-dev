-- ============================================================
-- DATA SOURCE — MySQL (Dukcapil / Kependudukan)
-- Docker: image=debezium/example-mysql:2.0
--         MYSQL_DATABASE=dukcapil
--         MYSQL_USER=mysql / MYSQL_PASSWORD=mysql
--         MYSQL_ROOT_PASSWORD=mysql
-- ============================================================

USE dukcapil;

-- ============================================================
-- BAGIAN 1: TABEL BISNIS
-- ============================================================

CREATE TABLE IF NOT EXISTS master_wilayah (
    wilayah_id  INT PRIMARY KEY AUTO_INCREMENT,
    provinsi    VARCHAR(100),
    kabupaten   VARCHAR(100),
    kecamatan   VARCHAR(100),
    desa        VARCHAR(100)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS master_keluarga (
    no_kk       CHAR(16) PRIMARY KEY,
    wilayah_id  INT,
    created_at  DATETIME DEFAULT NOW(),
    FOREIGN KEY (wilayah_id) REFERENCES master_wilayah(wilayah_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS master_penduduk (
    nik             CHAR(16) PRIMARY KEY,
    no_kk           CHAR(16),
    nama            VARCHAR(255),
    tanggal_lahir   DATE,
    pekerjaan       VARCHAR(100),
    penghasilan     NUMERIC(15,2),
    status_hidup    VARCHAR(20),
    created_at      DATETIME DEFAULT NOW(),
    FOREIGN KEY (no_kk) REFERENCES master_keluarga(no_kk)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS riwayat_perubahan_data (
    riwayat_id      INT PRIMARY KEY AUTO_INCREMENT,
    nik             CHAR(16),
    kolom_diubah    VARCHAR(100),
    nilai_lama      VARCHAR(500),
    nilai_baru      VARCHAR(500),
    diubah_pada     DATETIME DEFAULT NOW(),
    diubah_oleh     VARCHAR(100),
    FOREIGN KEY (nik) REFERENCES master_penduduk(nik)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ============================================================
-- BAGIAN 2: INSERT DATA AWAL
-- ============================================================

INSERT INTO master_wilayah (provinsi, kabupaten, kecamatan, desa) VALUES
('Sulawesi Selatan', 'Kota Makassar', 'Tamalate',        'Parang Tambung'),
('Sulawesi Selatan', 'Kota Makassar', 'Rappocini',       'Banta-Bantaeng'),
('Sulawesi Selatan', 'Kota Makassar', 'Makassar',        'Lariang Bangi'),
('Sulawesi Selatan', 'Kota Makassar', 'Ujung Pandang',   'Lajangiru'),
('Sulawesi Selatan', 'Kota Makassar', 'Manggala',        'Tamangapa'),
('Sulawesi Selatan', 'Kab. Gowa',    'Somba Opu',        'Sungguminasa'),
('Sulawesi Selatan', 'Kab. Gowa',    'Pallangga',        'Pallangga'),
('Sulawesi Selatan', 'Kab. Maros',   'Turikale',         'Turikale'),
('Sulawesi Selatan', 'Kota Parepare','Bacukiki',          'Lompoe'),
('Sulawesi Selatan', 'Kab. Bone',    'Tanete Riattang',  'Watampone');

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

INSERT INTO master_penduduk (nik, no_kk, nama, tanggal_lahir, pekerjaan, penghasilan, status_hidup, created_at) VALUES
('7371010101800001', '7371010101800001', 'Siti Rahma Wati',    '1980-01-01', 'Buruh Harian',     850000,  'HIDUP', '2010-05-01 00:00:00'),
('7371010203750002', '7371010203750002', 'Baharuddin Lallo',   '1975-03-02', 'Petani',           1200000, 'HIDUP', '2008-03-15 00:00:00'),
('7371030405820003', '7371030405820003', 'Hasnah Binti Ahmad', '1982-04-05', 'Pedagang Kecil',   1800000, 'HIDUP', '2012-07-20 00:00:00'),
('7371050607900004', '7371050607900004', 'Ridwan Saputra',     '1990-06-07', 'Buruh Harian',     750000,  'HIDUP', '2015-11-10 00:00:00'),
('7371070809850005', '7371070809850005', 'Nurhayati Mansur',   '1985-08-09', 'Ibu Rumah Tangga', 0,       'HIDUP', '2011-02-28 00:00:00'),
('7371091011780006', '7371091011780006', 'Kamaruddin Tahir',   '1978-10-11', 'Nelayan',          1500000, 'HIDUP', '2009-09-05 00:00:00'),
('7371111213920007', '7371111213920007', 'Fatmawati Darwis',   '1992-12-13', 'Buruh Pabrik',     1100000, 'HIDUP', '2016-04-12 00:00:00'),
('7371131415880008', '7371131415880008', 'Syamsuddin Rahim',   '1988-02-14', 'Tukang Ojek',      900000,  'HIDUP', '2013-08-22 00:00:00'),
('7371151617960009', '7371151617960009', 'Rahmawati Sulaiman', '1996-04-16', 'Asisten RT',       800000,  'HIDUP', '2018-01-30 00:00:00'),
('7371171819830010', '7371171819830010', 'Muh. Arif Hidayat',  '1983-06-18', 'Kuli Bangunan',    1300000, 'HIDUP', '2010-12-11 00:00:00'),
('7371010101800011', '7371010101800001', 'Ahmad Fauzi Wati',   '2005-03-15', 'Pelajar',          0,       'HIDUP', '2010-05-01 00:00:00'),
('7371010101800012', '7371010101800001', 'Nur Alya Wati',      '2008-07-22', 'Pelajar',          0,       'HIDUP', '2010-05-01 00:00:00'),
('7371010203750013', '7371010203750002', 'Hasriani Lallo',     '1977-09-10', 'Ibu Rumah Tangga', 0,       'HIDUP', '2008-03-15 00:00:00'),
('7371050607900014', '7371050607900004', 'Dewi Saputra',       '1993-11-05', 'Pedagang Kecil',   950000,  'HIDUP', '2015-11-10 00:00:00'),
('7371131415880015', '7371131415880008', 'Andi Rahmat Rahim',  '2010-05-30', 'Pelajar',          0,       'HIDUP', '2013-08-22 00:00:00');

INSERT INTO riwayat_perubahan_data (nik, kolom_diubah, nilai_lama, nilai_baru, diubah_pada, diubah_oleh) VALUES
('7371010203750002', 'pekerjaan',   'Buruh Harian',      'Petani',             '2022-03-10 08:30:00', 'operator_dukcapil'),
('7371010203750002', 'penghasilan', '850000',            '1200000',            '2022-03-10 08:30:00', 'operator_dukcapil'),
('7371030405820003', 'nama',        'Hasnah Ahmad',      'Hasnah Binti Ahmad', '2021-06-15 10:00:00', 'operator_dukcapil'),
('7371070809850005', 'penghasilan', '500000',            '0',                  '2023-01-20 09:15:00', 'operator_dukcapil'),
('7371091011780006', 'pekerjaan',   'Buruh Harian',      'Nelayan',            '2020-08-05 14:00:00', 'operator_dukcapil'),
('7371111213920007', 'penghasilan', '950000',            '1100000',            '2023-07-01 11:30:00', 'operator_dukcapil'),
('7371151617960009', 'pekerjaan',   'Pengangguran',      'Asisten RT',         '2024-02-14 13:00:00', 'operator_dukcapil'),
('7371131415880015', 'status_hidup','HIDUP',             'HIDUP',              '2023-09-01 10:00:00', 'operator_dukcapil');


-- ============================================================
-- BAGIAN 3: DDL CAPTURE SYSTEM
-- ============================================================

-- ------------------------------------------------------------
-- 3.1 Tabel referensi: daftar tabel yang dimonitor
-- ------------------------------------------------------------
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

INSERT IGNORE INTO captured_tables_ref (db_name, table_name, description) VALUES
('dukcapil', 'master_wilayah',         'Tabel master wilayah (provinsi, kabupaten, kecamatan, desa)'),
('dukcapil', 'master_keluarga',        'Tabel master keluarga (no_kk, wilayah_id)'),
('dukcapil', 'master_penduduk',        'Tabel master penduduk (nik, no_kk, nama, dll)'),
('dukcapil', 'riwayat_perubahan_data', 'Tabel riwayat perubahan data penduduk');


-- ------------------------------------------------------------
-- 3.2 Tabel log utama
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ddl_event_log (
    id           BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    username     VARCHAR(128) NOT NULL DEFAULT 'mysql',
    db_name      VARCHAR(128),
    table_name   VARCHAR(256),
    object_tag   VARCHAR(512),
    command_tag  VARCHAR(64),
    alter_type   VARCHAR(32),
    column_name  VARCHAR(256),
    ddl_command  TEXT,
    is_regulated TINYINT(1) DEFAULT 0,
    captured_at  DATETIME   NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Log event ALTER TABLE: hanya ADD COLUMN, DROP COLUMN, RENAME COLUMN.';


-- ------------------------------------------------------------
-- 3.3 Tabel event capture
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ddl_event_capture (
    id           BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    username     VARCHAR(128) NOT NULL DEFAULT 'mysql',
    db_name      VARCHAR(128) NOT NULL DEFAULT 'dukcapil',
    table_name   VARCHAR(256) NOT NULL,
    alter_type   VARCHAR(32)  NOT NULL,
    column_name  VARCHAR(256),
    ddl_command  TEXT         NOT NULL,
    captured_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Titik masuk event DDL — diisi otomatis oleh Event Scheduler.';


-- ------------------------------------------------------------
-- 3.4 Tabel snapshot kolom
--
--     Kolom ordinal_pos dan column_type adalah kunci utama
--     untuk deteksi RENAME otomatis:
--       Jika ada kolom hilang (A) dan kolom baru muncul (B)
--       pada tabel yang sama, dengan ordinal_pos DAN column_type
--       yang identik → dicatat sebagai RENAME COLUMN A → B.
--       Jika tidak ada pasangan cocok → dicatat DROP / ADD terpisah.
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ddl_column_snapshot (
    id           BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    db_name      VARCHAR(128) NOT NULL,
    table_name   VARCHAR(128) NOT NULL,
    column_name  VARCHAR(256) NOT NULL,
    column_type  VARCHAR(256),
    ordinal_pos  INT UNSIGNED,
    snapped_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_snapshot (db_name, table_name, column_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Snapshot struktur kolom untuk deteksi ADD/DROP/RENAME oleh Event Scheduler.';

-- Isi snapshot awal
INSERT IGNORE INTO ddl_column_snapshot (db_name, table_name, column_name, column_type, ordinal_pos)
SELECT
    c.TABLE_SCHEMA,
    c.TABLE_NAME,
    c.COLUMN_NAME,
    c.COLUMN_TYPE,
    c.ORDINAL_POSITION
FROM information_schema.COLUMNS c
INNER JOIN captured_tables_ref r
    ON  r.db_name    = c.TABLE_SCHEMA
    AND r.table_name = c.TABLE_NAME
    AND r.is_active  = 1;


-- ============================================================
-- BAGIAN 4: TRIGGER
-- ============================================================

-- ------------------------------------------------------------
-- 4.1 BEFORE INSERT ddl_event_capture
--     Isi username dengan CURRENT_USER() jika kosong
-- ------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_before_insert_ddl_capture;

DELIMITER $$
CREATE TRIGGER trg_before_insert_ddl_capture
BEFORE INSERT ON ddl_event_capture
FOR EACH ROW
BEGIN
    IF NEW.username IS NULL OR NEW.username = '' THEN
        SET NEW.username = CURRENT_USER();
    END IF;
    IF NEW.db_name IS NULL OR NEW.db_name = '' THEN
        SET NEW.db_name = 'dukcapil';
    END IF;
END$$
DELIMITER ;


-- ------------------------------------------------------------
-- 4.2 AFTER INSERT ddl_event_capture → forward ke ddl_event_log
-- ------------------------------------------------------------
DROP TRIGGER IF EXISTS trg_after_insert_ddl_event;

DELIMITER $$
CREATE TRIGGER trg_after_insert_ddl_event
AFTER INSERT ON ddl_event_capture
FOR EACH ROW
BEGIN
    DECLARE v_upper_type   VARCHAR(32);
    DECLARE v_is_regulated TINYINT DEFAULT 0;

    SET v_upper_type := UPPER(TRIM(NEW.alter_type));

    IF v_upper_type IN ('ADD COLUMN', 'DROP COLUMN', 'RENAME COLUMN') THEN

        SELECT COUNT(*) INTO v_is_regulated
        FROM captured_tables_ref
        WHERE is_active  = 1
          AND db_name    = NEW.db_name
          AND table_name = NEW.table_name;

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
END$$
DELIMITER ;


-- ============================================================
-- BAGIAN 5: STORED PROCEDURE
-- ============================================================

-- ------------------------------------------------------------
-- 5.1 sp_capture_alter_column — insert manual (opsional/fallback)
-- ------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_capture_alter_column;

DELIMITER $$
CREATE PROCEDURE sp_capture_alter_column(
    IN p_db_name     VARCHAR(128),
    IN p_table_name  VARCHAR(256),
    IN p_alter_type  VARCHAR(32),
    IN p_column_name VARCHAR(256),
    IN p_ddl_command TEXT
)
BEGIN
    DECLARE v_is_regulated TINYINT DEFAULT 0;
    DECLARE v_upper_type   VARCHAR(32);

    SET v_upper_type := UPPER(TRIM(p_alter_type));

    IF v_upper_type NOT IN ('ADD COLUMN', 'DROP COLUMN', 'RENAME COLUMN') THEN
        SIGNAL SQLSTATE '45000'
            SET MESSAGE_TEXT = 'alter_type tidak dikenali. Gunakan ADD COLUMN | DROP COLUMN | RENAME COLUMN';
    END IF;

    SELECT COUNT(*) INTO v_is_regulated
    FROM captured_tables_ref
    WHERE is_active  = 1
      AND db_name    = p_db_name
      AND table_name = p_table_name;

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
END$$
DELIMITER ;


-- ------------------------------------------------------------
-- 5.2 sp_detect_ddl_changes — dipanggil Event Scheduler tiap 10 detik
--
--     Logika deteksi RENAME COLUMN otomatis:
--       1. Kumpulkan kolom yang hilang  → tmp_dropped
--       2. Kumpulkan kolom yang muncul  → tmp_added
--       3. Jika ada pasangan (tabel sama, ordinal_pos sama, column_type sama)
--          → catat sebagai RENAME COLUMN 'lama -> baru'
--       4. Sisa tmp_dropped tanpa pasangan → catat sebagai DROP COLUMN
--       5. Sisa tmp_added  tanpa pasangan → catat sebagai ADD COLUMN
--       6. Sync snapshot agar polling berikutnya akurat
-- ------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_detect_ddl_changes;

DELIMITER $$
CREATE PROCEDURE sp_detect_ddl_changes()
BEGIN

    -- Tabel sementara: kolom hilang (kandidat DROP atau nama-lama-RENAME)
    CREATE TEMPORARY TABLE IF NOT EXISTS tmp_dropped (
        db_name     VARCHAR(128),
        table_name  VARCHAR(128),
        column_name VARCHAR(256),
        column_type VARCHAR(256),
        ordinal_pos INT UNSIGNED
    );
    DELETE FROM tmp_dropped;

    INSERT INTO tmp_dropped (db_name, table_name, column_name, column_type, ordinal_pos)
    SELECT s.db_name, s.table_name, s.column_name, s.column_type, s.ordinal_pos
    FROM ddl_column_snapshot s
    INNER JOIN captured_tables_ref r
        ON  r.db_name    = s.db_name
        AND r.table_name = s.table_name
        AND r.is_active  = 1
    LEFT JOIN information_schema.COLUMNS c
        ON  c.TABLE_SCHEMA = s.db_name
        AND c.TABLE_NAME   = s.table_name
        AND c.COLUMN_NAME  = s.column_name
    WHERE c.COLUMN_NAME IS NULL;

    -- Tabel sementara: kolom baru (kandidat ADD atau nama-baru-RENAME)
    CREATE TEMPORARY TABLE IF NOT EXISTS tmp_added (
        db_name     VARCHAR(128),
        table_name  VARCHAR(128),
        column_name VARCHAR(256),
        column_type VARCHAR(256),
        ordinal_pos INT UNSIGNED
    );
    DELETE FROM tmp_added;

    INSERT INTO tmp_added (db_name, table_name, column_name, column_type, ordinal_pos)
    SELECT c.TABLE_SCHEMA, c.TABLE_NAME, c.COLUMN_NAME, c.COLUMN_TYPE, c.ORDINAL_POSITION
    FROM information_schema.COLUMNS c
    INNER JOIN captured_tables_ref r
        ON  r.db_name    = c.TABLE_SCHEMA
        AND r.table_name = c.TABLE_NAME
        AND r.is_active  = 1
    LEFT JOIN ddl_column_snapshot s
        ON  s.db_name     = c.TABLE_SCHEMA
        AND s.table_name  = c.TABLE_NAME
        AND s.column_name = c.COLUMN_NAME
    WHERE s.column_name IS NULL;

    -- ── RENAME COLUMN: pasangan DROP + ADD dengan ordinal_pos & column_type sama
    INSERT INTO ddl_event_capture
        (username, db_name, table_name, alter_type, column_name, ddl_command)
    SELECT
        'event_scheduler',
        d.db_name,
        d.table_name,
        'RENAME COLUMN',
        CONCAT(d.column_name, ' -> ', a.column_name),
        CONCAT('ALTER TABLE `', d.db_name, '`.`', d.table_name,
               '` RENAME COLUMN `', d.column_name, '` TO `', a.column_name, '`')
    FROM tmp_dropped d
    INNER JOIN tmp_added a
        ON  a.db_name     = d.db_name
        AND a.table_name  = d.table_name
        AND a.ordinal_pos = d.ordinal_pos
        AND a.column_type = d.column_type;

    -- ── DROP COLUMN murni: hilang tapi tidak ada pasangan RENAME
    INSERT INTO ddl_event_capture
        (username, db_name, table_name, alter_type, column_name, ddl_command)
    SELECT
        'event_scheduler',
        d.db_name,
        d.table_name,
        'DROP COLUMN',
        d.column_name,
        CONCAT('ALTER TABLE `', d.db_name, '`.`', d.table_name,
               '` DROP COLUMN `', d.column_name, '`')
    FROM tmp_dropped d
    WHERE NOT EXISTS (
        SELECT 1 FROM tmp_added a
        WHERE  a.db_name     = d.db_name
          AND  a.table_name  = d.table_name
          AND  a.ordinal_pos = d.ordinal_pos
          AND  a.column_type = d.column_type
    );

    -- ── ADD COLUMN murni: muncul tapi tidak ada pasangan RENAME
    INSERT INTO ddl_event_capture
        (username, db_name, table_name, alter_type, column_name, ddl_command)
    SELECT
        'event_scheduler',
        a.db_name,
        a.table_name,
        'ADD COLUMN',
        a.column_name,
        CONCAT('ALTER TABLE `', a.db_name, '`.`', a.table_name,
               '` ADD COLUMN `', a.column_name, '` ', a.column_type)
    FROM tmp_added a
    WHERE NOT EXISTS (
        SELECT 1 FROM tmp_dropped d
        WHERE  d.db_name     = a.db_name
          AND  d.table_name  = a.table_name
          AND  d.ordinal_pos = a.ordinal_pos
          AND  d.column_type = a.column_type
    );

    -- ── Sync snapshot: hapus nama lama (drop/rename-lama)
    DELETE s FROM ddl_column_snapshot s
    INNER JOIN tmp_dropped d
        ON  d.db_name     = s.db_name
        AND d.table_name  = s.table_name
        AND d.column_name = s.column_name;

    -- ── Sync snapshot: tambah nama baru (add/rename-baru)
    INSERT IGNORE INTO ddl_column_snapshot
        (db_name, table_name, column_name, column_type, ordinal_pos, snapped_at)
    SELECT db_name, table_name, column_name, column_type, ordinal_pos, NOW()
    FROM tmp_added;

    -- ── Bersihkan tabel sementara
    DROP TEMPORARY TABLE IF EXISTS tmp_dropped;
    DROP TEMPORARY TABLE IF EXISTS tmp_added;

END$$
DELIMITER ;


-- ============================================================
-- BAGIAN 6: EVENT SCHEDULER
--   Jalankan sp_detect_ddl_changes setiap 10 detik secara otomatis
-- ============================================================

SET GLOBAL event_scheduler = ON;

DROP EVENT IF EXISTS evt_detect_ddl_changes;

DELIMITER $$
CREATE EVENT evt_detect_ddl_changes
ON SCHEDULE EVERY 10 SECOND
STARTS NOW()
DO
BEGIN
    CALL sp_detect_ddl_changes();
END$$
DELIMITER ;


-- ============================================================
-- BAGIAN 7: GRANT PRIVILEGES
-- ============================================================

GRANT SELECT, RELOAD, SHOW DATABASES, REPLICATION SLAVE, REPLICATION CLIENT 
ON *.* TO 'mysql'@'%';
GRANT EXECUTE ON PROCEDURE dukcapil.sp_capture_alter_column TO 'mysql'@'%';
GRANT EXECUTE ON PROCEDURE dukcapil.sp_detect_ddl_changes   TO 'mysql'@'%';
GRANT EVENT   ON dukcapil.*                                  TO 'mysql'@'%';
FLUSH PRIVILEGES;


-- ============================================================
-- BAGIAN 8: CONTOH PEMAKAIAN (semua otomatis ~10 detik)
-- ============================================================

-- ── ADD COLUMN ────────────────────────────────────────────────
-- ALTER TABLE master_penduduk ADD COLUMN email VARCHAR(100);

-- ── DROP COLUMN ───────────────────────────────────────────────
-- ALTER TABLE master_penduduk DROP COLUMN email;

-- ── RENAME COLUMN (otomatis, tidak perlu CALL apapun) ─────────
--   Terdeteksi sebagai RENAME karena ordinal_pos & column_type sama
-- ALTER TABLE master_penduduk RENAME COLUMN tanggal_lahir TO ttl;


-- ============================================================
-- BAGIAN 9: QUERY MONITORING
-- ============================================================

-- Semua event yang tercatat di log
-- SELECT * FROM ddl_event_log ORDER BY captured_at DESC;

-- Event masuk di capture (sebelum forward ke log)
-- SELECT * FROM ddl_event_capture ORDER BY captured_at DESC;

-- Hanya tabel yang diregulasi
-- SELECT alter_type, table_name, column_name, ddl_command, captured_at
-- FROM   ddl_event_log
-- WHERE  is_regulated = 1
-- ORDER  BY captured_at DESC;

-- Rekapitulasi per jenis operasi
-- SELECT alter_type, COUNT(*) AS total
-- FROM   ddl_event_log
-- GROUP  BY alter_type;

-- Cek snapshot kolom saat ini
-- SELECT * FROM ddl_column_snapshot ORDER BY db_name, table_name, ordinal_pos;