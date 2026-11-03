-- Column catalog of the source "registry" (MySQL), input of gen_vdb.py. Same columns as catalog-pg.sql;
-- the type is COLUMN_TYPE minus its length, so 'bigint unsigned' stays recognisable.
SELECT c.TABLE_NAME, c.ORDINAL_POSITION, c.COLUMN_NAME,
       TRIM(REGEXP_REPLACE(c.COLUMN_TYPE, '\\([^)]*\\)', '')),
       IFNULL(c.CHARACTER_MAXIMUM_LENGTH, ''), IFNULL(c.NUMERIC_PRECISION, ''), IFNULL(c.NUMERIC_SCALE, ''),
       c.IS_NULLABLE, IFNULL(k.ORDINAL_POSITION, '')
FROM information_schema.COLUMNS c
JOIN information_schema.TABLES t ON t.TABLE_SCHEMA = c.TABLE_SCHEMA AND t.TABLE_NAME = c.TABLE_NAME
LEFT JOIN information_schema.KEY_COLUMN_USAGE k
       ON k.TABLE_SCHEMA = c.TABLE_SCHEMA AND k.TABLE_NAME = c.TABLE_NAME
      AND k.COLUMN_NAME = c.COLUMN_NAME AND k.CONSTRAINT_NAME = 'PRIMARY'
WHERE c.TABLE_SCHEMA = DATABASE() AND t.TABLE_TYPE = 'BASE TABLE'
ORDER BY c.TABLE_NAME, c.ORDINAL_POSITION;
