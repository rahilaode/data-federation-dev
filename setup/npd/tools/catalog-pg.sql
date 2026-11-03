-- Column catalog of the source "reporting" (PostgreSQL), input of gen_vdb.py.
-- table, ordinal, column, type, char length, precision, scale, nullable, pk position, max length (text only)
SELECT c.table_name, c.ordinal_position, c.column_name, c.data_type,
       coalesce(c.character_maximum_length::text, ''), coalesce(c.numeric_precision::text, ''),
       coalesce(c.numeric_scale::text, ''), c.is_nullable, coalesce(k.ordinal_position::text, '')
FROM information_schema.columns c
JOIN information_schema.tables t USING (table_schema, table_name)
LEFT JOIN (SELECT ku.table_name, ku.column_name, ku.ordinal_position
           FROM information_schema.table_constraints tc
           JOIN information_schema.key_column_usage ku USING (constraint_schema, constraint_name)
           WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = 'public') k
       ON k.table_name = c.table_name AND k.column_name = c.column_name
WHERE c.table_schema = 'public' AND t.table_type = 'BASE TABLE'
ORDER BY c.table_name, c.ordinal_position;
