-- Step 3 of 4: schema monitor of the source `registry` (ADR-0017). Taken from the
-- social-assistance case study (archive/bansos/data-source/mysql/init.sql); for NPD every table
-- is monitored and the database is `registry`. The column snapshot is taken here, after the
-- dump was loaded and split.
USE registry;

-- ============================================================
-- PART 3: DDL CAPTURE SYSTEM
-- ============================================================

-- ------------------------------------------------------------
-- 3.1 Reference table: the tables that are monitored
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
  COMMENT='Tables that may be monitored (regulated).';

-- Every table of the source is monitored (ADR-0027: all 45 NPD tables of `registry`).
INSERT IGNORE INTO captured_tables_ref (db_name, table_name) VALUES
('registry', 'apaAreaGross'),
('registry', 'apaAreaNet'),
('registry', 'baaArea'),
('registry', 'bsns_arr_area'),
('registry', 'bsns_arr_area_area_poly_hst'),
('registry', 'bsns_arr_area_licensee_hst'),
('registry', 'bsns_arr_area_operator'),
('registry', 'bsns_arr_area_transfer_hst'),
('registry', 'company'),
('registry', 'company_reserves'),
('registry', 'discovery'),
('registry', 'discovery_reserves'),
('registry', 'dscArea'),
('registry', 'facility_fixed'),
('registry', 'facility_moveable'),
('registry', 'fclPoint'),
('registry', 'field'),
('registry', 'field_activity_status_hst'),
('registry', 'field_description'),
('registry', 'field_investment_yearly'),
('registry', 'field_licensee_hst'),
('registry', 'field_operator_hst'),
('registry', 'field_owner_hst'),
('registry', 'field_reserves'),
('registry', 'fldArea'),
('registry', 'licence'),
('registry', 'licence_area_poly_hst'),
('registry', 'licence_licensee_hst'),
('registry', 'licence_oper_hst'),
('registry', 'licence_petreg_licence'),
('registry', 'licence_petreg_licence_licencee'),
('registry', 'licence_petreg_licence_oper'),
('registry', 'licence_petreg_message'),
('registry', 'licence_phase_hst'),
('registry', 'licence_task'),
('registry', 'licence_transfer_hst'),
('registry', 'pipLine'),
('registry', 'prlArea'),
('registry', 'prlAreaSplitByBlock'),
('registry', 'tuf_operator_hst'),
('registry', 'tuf_owner_hst'),
('registry', 'tuf_petreg_licence'),
('registry', 'tuf_petreg_licence_licencee'),
('registry', 'tuf_petreg_licence_oper'),
('registry', 'tuf_petreg_message');


-- ------------------------------------------------------------
-- 3.2 Main log table
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
    is_regulated TINYINT(1) DEFAULT 1,
    captured_at  DATETIME   NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Log of ALTER TABLE events: ADD COLUMN, DROP COLUMN, RENAME COLUMN only.';


-- ------------------------------------------------------------
-- 3.3 Event capture table
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ddl_event_capture (
    id           BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    username     VARCHAR(128) NOT NULL DEFAULT 'mysql',
    db_name      VARCHAR(128) NOT NULL DEFAULT 'registry',
    table_name   VARCHAR(256) NOT NULL,
    alter_type   VARCHAR(32)  NOT NULL,
    column_name  VARCHAR(256),
    ddl_command  TEXT         NOT NULL,
    captured_at  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Entry point of DDL events, filled automatically by the Event Scheduler.';


-- ------------------------------------------------------------
-- 3.4 Column snapshot table
--
--     The ordinal_pos and column_type columns are what makes
--     automatic RENAME detection work:
--       If a column disappears (A) and a new column appears (B)
--       in the same table, with the SAME ordinal_pos AND column_type,
--       it is recorded as RENAME COLUMN A → B.
--       If no pair matches, separate DROP / ADD events are recorded.
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
  COMMENT='Column structure snapshot for ADD/DROP/RENAME detection by the Event Scheduler.';

-- Initial snapshot
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
-- PART 4: TRIGGERS
-- ============================================================

-- ------------------------------------------------------------
-- 4.1 BEFORE INSERT ddl_event_capture
--     Fill username with CURRENT_USER() when it is empty
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
        SET NEW.db_name = 'registry';
    END IF;
END$$
DELIMITER ;


-- ------------------------------------------------------------
-- 4.2 AFTER INSERT ddl_event_capture → forward to ddl_event_log
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
-- PART 5: STORED PROCEDURES
-- ============================================================

-- ------------------------------------------------------------
-- 5.1 sp_capture_alter_column — manual insert (optional fallback)
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
-- 5.2 sp_detect_ddl_changes — called by the Event Scheduler every 10 seconds
--
--     Automatic RENAME COLUMN detection:
--       1. Collect the columns that disappeared → tmp_dropped
--       2. Collect the columns that appeared    → tmp_added
--       3. Each pair (same table, same ordinal_pos, same column_type)
--          → recorded as RENAME COLUMN 'old -> new'
--       4. What is left in tmp_dropped → recorded as DROP COLUMN
--       5. What is left in tmp_added   → recorded as ADD COLUMN
--       6. Sync the snapshot so that the next poll is accurate
-- ------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_detect_ddl_changes;

DELIMITER $$
CREATE PROCEDURE sp_detect_ddl_changes()
BEGIN

    -- Temporary table: columns that disappeared (DROP candidates or RENAME old names)
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

    -- Temporary table: new columns (ADD candidates or RENAME new names)
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

    -- ── RENAME COLUMN: a DROP + ADD pair with the same ordinal_pos & column_type
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

    -- ── plain DROP COLUMN: gone, with no RENAME pair
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

    -- ── plain ADD COLUMN: appeared, with no RENAME pair
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

    -- ── Sync the snapshot: remove old names (drop / rename old)
    DELETE s FROM ddl_column_snapshot s
    INNER JOIN tmp_dropped d
        ON  d.db_name     = s.db_name
        AND d.table_name  = s.table_name
        AND d.column_name = s.column_name;

    -- ── Sync the snapshot: add new names (add / rename new)
    INSERT IGNORE INTO ddl_column_snapshot
        (db_name, table_name, column_name, column_type, ordinal_pos, snapped_at)
    SELECT db_name, table_name, column_name, column_type, ordinal_pos, NOW()
    FROM tmp_added;

    -- ── Drop the temporary tables
    DROP TEMPORARY TABLE IF EXISTS tmp_dropped;
    DROP TEMPORARY TABLE IF EXISTS tmp_added;

END$$
DELIMITER ;


-- ============================================================
-- PART 6: EVENT SCHEDULER
--   Runs sp_detect_ddl_changes automatically every 10 seconds
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
-- PART 7: GRANT PRIVILEGES
-- ============================================================

GRANT SELECT, RELOAD, SHOW DATABASES, REPLICATION SLAVE, REPLICATION CLIENT 
ON *.* TO 'mysql'@'%';
GRANT EXECUTE ON PROCEDURE registry.sp_capture_alter_column TO 'mysql'@'%';
GRANT EXECUTE ON PROCEDURE registry.sp_detect_ddl_changes   TO 'mysql'@'%';
GRANT EVENT   ON registry.*                                  TO 'mysql'@'%';
FLUSH PRIVILEGES;


-- ============================================================
