"""Generates the Mermaid ER diagrams of the Knowledge Base from the PostgreSQL catalogue.

The diagrams in docs/erd/ are not drawn by hand: they come from this script, run on a database
created by the Alembic migrations of the Knowledge Service, so they match the schema exactly.

Usage: generate_erd.py <dsn> <out_dir>
  python3 docs/erd/generate_erd.py 'host=127.0.0.1 port=55432 user=ascam_owner dbname=ascam_knowledge' docs/erd
Writes one .mmd per schema (registry, spec, ops) and catalogue.json with every table, column,
key, and foreign key. A relationship is drawn one-to-zero-or-one when the foreign key covers the
primary key or a unique key of the referencing table, and one-to-many otherwise; a nullable
foreign key makes the parent side optional. Render the SVGs with the Mermaid CLI:
  mmdc -i docs/erd/erd-spec.mmd -o docs/erd/erd-spec.svg
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import psycopg

dsn, out = sys.argv[1], Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
SCHEMAS = ('registry', 'spec', 'ops')

with psycopg.connect(dsn) as conn, conn.cursor() as cur:
    cur.execute("""
        SELECT c.table_schema, c.table_name, c.column_name, c.ordinal_position,
               CASE WHEN c.data_type = 'USER-DEFINED' THEN c.udt_name
                    WHEN c.data_type = 'ARRAY' THEN substr(c.udt_name, 2) || '[]'
                    WHEN c.data_type = 'character varying' THEN 'varchar'
                    WHEN c.data_type = 'timestamp with time zone' THEN 'timestamptz'
                    ELSE c.data_type END,
               c.is_nullable = 'YES'
          FROM information_schema.columns c
          JOIN information_schema.tables t USING (table_schema, table_name)
         WHERE c.table_schema = ANY(%s) AND t.table_type = 'BASE TABLE'
         ORDER BY 1, 2, 4""", (list(SCHEMAS),))
    columns = defaultdict(list)
    for schema, table, col, _, typ, nullable in cur.fetchall():
        columns[(schema, table)].append({'name': col, 'type': typ, 'nullable': nullable})

    cur.execute("""
        SELECT n.nspname, r.relname, con.contype, con.conname,
               ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(n, i)
                     JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.n ORDER BY k.i),
               fn.nspname, fr.relname,
               ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(n, i)
                     JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.n ORDER BY k.i)
          FROM pg_constraint con
          JOIN pg_class r ON r.oid = con.conrelid
          JOIN pg_namespace n ON n.oid = r.relnamespace
          LEFT JOIN pg_class fr ON fr.oid = con.confrelid
          LEFT JOIN pg_namespace fn ON fn.oid = fr.relnamespace
         WHERE n.nspname = ANY(%s) AND con.contype IN ('p', 'u', 'f')
         ORDER BY 1, 2, 4""", (list(SCHEMAS),))
    pk, uk, uniques, fks = defaultdict(set), defaultdict(set), defaultdict(list), []
    for schema, table, kind, name, cols, fschema, ftable, fcols in cur.fetchall():
        key = (schema, table)
        if kind == 'p':
            pk[key].update(cols)
        elif kind == 'u':
            uk[key].update(cols)
            uniques[key].append(set(cols))
        else:
            fks.append({'from': f'{schema}.{table}', 'columns': cols, 'to': f'{fschema}.{ftable}',
                        'ref_columns': fcols, 'name': name,
                        'nullable': any(c['nullable'] for c in columns[key] if c['name'] in cols)})

    cur.execute("""SELECT table_schema || '.' || table_name FROM information_schema.views
                    WHERE table_schema = ANY(%s) ORDER BY 1""", (list(SCHEMAS),))
    views = [r[0] for r in cur.fetchall()]

fk_cols = defaultdict(set)
for fk in fks:
    fk_cols[tuple(fk['from'].split('.'))].update(fk['columns'])


def entity(schema, table):
    return f'{schema}_{table}'


catalogue = {'tables': {}, 'foreign_keys': fks, 'views': views}
for schema in SCHEMAS:
    tables = sorted(t for (s, t) in columns if s == schema)
    lines = ['erDiagram']
    external = set()
    for table in tables:
        key = (schema, table)
        lines.append(f'    {entity(schema, table)}["{schema}.{table}"] {{')
        for c in columns[key]:
            marks = [m for m, on in (('PK', c['name'] in pk[key]), ('FK', c['name'] in fk_cols[key]),
                                     ('UK', c['name'] in uk[key] and c['name'] not in pk[key])) if on]
            typ = c['type'].replace(' ', '_').replace('[]', '_array')
            note = '' if c['nullable'] else ' "not null"'
            lines.append(f'        {typ} {c["name"]}{" " + ",".join(marks) if marks else ""}{note}')
        lines.append('    }')
        catalogue['tables'][f'{schema}.{table}'] = {
            'columns': columns[key], 'primary_key': sorted(pk[key]), 'unique': sorted(uk[key])}
    for fk in fks:
        fs, ft = fk['from'].split('.')
        ts, tt = fk['to'].split('.')
        if fs != schema and ts != schema:
            continue
        if fs != schema:
            continue                     # drawn in the diagram of the referencing schema
        if ts != schema:
            external.add((ts, tt))
        child_keys = [pk[(fs, ft)]] + uniques[(fs, ft)]
        single = any(k and k <= set(fk['columns']) for k in child_keys)
        card = ('|o' if fk['nullable'] else '||') + ('--o|' if single else '--o{')
        lines.append(f'    {entity(ts, tt)} {card} {entity(fs, ft)} : "{",".join(fk["columns"])}"')
    for ts, tt in sorted(external):
        lines.insert(1, f'    {entity(ts, tt)}["{ts}.{tt}"]')
    (out / f'erd-{schema}.mmd').write_text('\n'.join(lines) + '\n')

(out / 'catalogue.json').write_text(json.dumps(catalogue, indent=2))
print(f'{len(catalogue["tables"])} tables, {len(fks)} foreign keys, views: {views}')
