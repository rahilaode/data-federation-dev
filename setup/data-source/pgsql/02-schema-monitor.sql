-- Step 2 of 2: schema monitor of the source `reporting` (ADR-0017), as on the NPD branch:
-- every table of the running example is monitored, and the table name comes from the catalog.
-- ============================================================
-- ============================================================
-- PostgreSQL: DDL trigger that monitors ALTER TABLE only
--   - ADD COLUMN
--   - DROP COLUMN
--   - RENAME COLUMN
-- ============================================================

CREATE SCHEMA IF NOT EXISTS schema_monitor;

-- ============================================================
-- 1. REFERENCE TABLE
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
    IS 'Tables that may be monitored (regulated).';

-- Every table of the running example is monitored.
INSERT INTO schema_monitor.captured_tables_ref (schema_name, table_name)
VALUES
    ('public', 'person'),
    ('public', 'contact'),
    ('public', 'product'),
    ('public', 'note'),
    ('public', 'memo'),
    ('public', 'doc'),
    ('public', 'staff'),
    ('public', 'city'),
    ('public', 'account'),
    ('public', 'event'),
    ('public', 'audit'),
    ('public', 'tool')
ON CONFLICT DO NOTHING;


-- ============================================================
-- 2. EVENT LOG TABLE
-- ============================================================

CREATE TABLE IF NOT EXISTS schema_monitor.ddl_event_log (
    id           BIGSERIAL PRIMARY KEY,
    username     VARCHAR(128) NOT NULL DEFAULT CURRENT_USER,
    schema_name  VARCHAR(128),
    table_name   VARCHAR(256),
    object_tag   VARCHAR(512),       -- e.g. 'public.orders'
    command_tag  VARCHAR(64),        -- always 'ALTER TABLE'
    alter_type   VARCHAR(32),        -- 'ADD COLUMN' | 'DROP COLUMN' | 'RENAME COLUMN'
    column_name  VARCHAR(256),       -- name of the column involved
    ddl_command  TEXT,               -- full DDL text
    is_regulated BOOLEAN DEFAULT TRUE,
    captured_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE schema_monitor.ddl_event_log
    IS 'Log of ALTER TABLE events: ADD COLUMN, DROP COLUMN, RENAME COLUMN only.';

-- The log needs REPLICA IDENTITY FULL so that its changes can be captured with wal2json
ALTER TABLE schema_monitor.ddl_event_log REPLICA IDENTITY FULL;

-- ============================================================
-- 3. EVENT TRIGGER FUNCTION
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
    -- Take the DDL text from the current session
    SELECT query INTO v_ddl
    FROM pg_stat_activity
    WHERE pid = pg_backend_pid();

    v_upper_ddl := upper(coalesce(v_ddl, ''));

    -- Only continue if it contains a monitored column operation
    -- Ignore other ALTER TABLE forms (ADD CONSTRAINT, SET DEFAULT, etc.)
    IF v_upper_ddl NOT LIKE '%ADD COLUMN%'
   AND v_upper_ddl NOT LIKE '%DROP COLUMN%'
   AND v_upper_ddl NOT LIKE '%RENAME COLUMN%' THEN
        RETURN;
    END IF;

    -- Work out the kind of operation
    IF    v_upper_ddl LIKE '%RENAME COLUMN%' THEN v_alter_type := 'RENAME COLUMN';
    ELSIF v_upper_ddl LIKE '%ADD COLUMN%'    THEN v_alter_type := 'ADD COLUMN';
    ELSIF v_upper_ddl LIKE '%DROP COLUMN%'   THEN v_alter_type := 'DROP COLUMN';
    END IF;

    FOR v_obj IN
        SELECT * FROM pg_event_trigger_ddl_commands()
        WHERE command_tag = 'ALTER TABLE'
    LOOP
        v_schema_name := v_obj.schema_name;
        -- the table name from the catalog: object_identity quotes mixed-case names
        -- (public."wlbPoint"), which would not match captured_tables_ref
        SELECT c.relname INTO v_table_name FROM pg_class c WHERE c.oid = v_obj.objid;

        -- Extract the column name: the first token after the operation keyword
        v_column_name := trim(
            split_part(
                regexp_replace(v_ddl, '\s+', ' ', 'g'),
                v_alter_type || ' ',
                2
            )
        );
        v_column_name := split_part(v_column_name, ' ', 1);
        -- a delimited identifier ("wlbLotMudDencity") is logged without its quotes; the
        -- normaliser parses ddl_command itself and only falls back to this column
        v_column_name := replace(trim(both '"' from regexp_replace(v_column_name, '[;,]+$', '')),
                                 '""', '"');

        -- Only tables listed (and active) in the reference table are monitored;
        -- changes to any other table are not logged and never leave the source.
        SELECT EXISTS (
            SELECT 1 FROM schema_monitor.captured_tables_ref
            WHERE is_active   = TRUE
              AND schema_name = v_schema_name
              AND table_name  = v_table_name
        ) INTO v_is_regulated;

        IF NOT v_is_regulated THEN
            CONTINUE;
        END IF;

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
-- 4. EVENT TRIGGER REGISTRATION
-- ============================================================

DROP EVENT TRIGGER IF EXISTS trg_monitor_alter_column;

CREATE EVENT TRIGGER trg_monitor_alter_column
    ON ddl_command_end
    WHEN TAG IN ('ALTER TABLE')
    EXECUTE FUNCTION schema_monitor.fn_capture_alter_column();



