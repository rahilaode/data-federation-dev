"""
Membangun snapshot Σ_S dari metadata runtime Teiid.

Sumber: `get-vdb` (model dan pemetaan sumber), tabel `SYS.*` (tabel, kolom, kunci),
`SYSADMIN.Views` (definisi view), `SYSADMIN.Usage` (dependensi), `SYSADMIN.MatViews`,
`StoredProcedures`, dan `Triggers` (ADR-0003, ADR-0006, ADR-0009).

Klasifikasi kolom view memakai sqlglot atas `Body` (pass-through, ekspresi, star); kolom yang
dirujuk view tetapi bukan sumber kolom view mana pun diberi peran `predicate` (bukti F0.7).
"""
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp


def strip_ident(value: str) -> str:
    value = value.strip()
    for pair in ('""', '``', "''", '[]'):
        if len(value) >= 2 and value[0] == pair[0] and value[-1] == pair[1]:
            return value[1:-1]
    return value


def split_name_in_source(name_in_source: str | None) -> tuple[str | None, str | None]:
    """'`db`.`tabel`' atau 'public.tabel' -> (skema, tabel); tanpa titik -> (None, nama)."""
    if not name_in_source:
        return None, None
    parts, current, quote = [], '', None
    for ch in name_in_source:
        if quote:
            if ch == quote:
                quote = None
            else:
                current += ch
        elif ch in '"`\'':
            quote = ch
        elif ch == '.':
            parts.append(current)
            current = ''
        else:
            current += ch
    parts.append(current)
    parts = [p for p in parts if p != '']
    if len(parts) >= 2:
        return parts[-2], parts[-1]
    return None, (parts[0] if parts else None)


def normalize(value: str | None, identifier_case: str) -> str | None:
    if value is None:
        return None
    return value.lower() if identifier_case == 'lower' else value


@dataclass
class ViewInfo:
    body: str
    parse_status: str
    uses_star: bool
    column_kinds: dict[str, str] = field(default_factory=dict)
    predicate_columns: list[str] = field(default_factory=list)


def classify_view(body: str) -> ViewInfo:
    try:
        tree = sqlglot.parse_one(body)
        select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
        if select is None:
            raise ValueError('bukan SELECT')
    except Exception:                                   # noqa: BLE001 — Body tak terurai
        return ViewInfo(body=body, parse_status='failed', uses_star=False)

    kinds, uses_star = {}, False
    for item in select.expressions:
        inner = item.this if isinstance(item, exp.Alias) else item
        if isinstance(item, exp.Star) or isinstance(inner, exp.Star):
            uses_star = True
            continue
        kinds[item.alias_or_name] = 'passthrough' if isinstance(inner, exp.Column) else 'expression'
    predicate = set()
    for clause in ('where', 'group', 'having', 'order'):
        node = select.args.get(clause)
        if node is not None:
            predicate |= {c.name for c in node.find_all(exp.Column)}
    for join in select.args.get('joins') or []:
        predicate |= {c.name for c in join.find_all(exp.Column)}
    return ViewInfo(body=body, parse_status='ok', uses_star=uses_star, column_kinds=kinds,
                    predicate_columns=sorted(predicate))


@dataclass
class SigmaSnapshot:
    vdb: dict[str, Any]
    models: list[dict]
    tables: list[dict]
    columns: list[dict]
    views: dict[str, ViewInfo]                 # kunci: "skema.tabel"
    dependencies: list[dict]
    routines: list[dict]

    def digest(self) -> str:
        """Sidik jari isi Σ_S; dipakai untuk mendeteksi perubahan tanpa membandingkan baris."""
        payload = {
            'vdb': {k: str(v) for k, v in self.vdb.items() if k in ('name', 'version', 'connection_type')},
            'models': sorted((m['name'], m['model_type'], m['visible'],
                              tuple(sorted((s['source_name'], s['translator'], s['jndi_name'])
                                           for s in m['sources']))) for m in self.models),
            'tables': sorted((t['model'], t['name'], t['kind'], t['name_in_source'] or '') for t in self.tables),
            'columns': sorted((c['model'], c['table'], c['name'], c['name_in_source'] or '',
                               c['data_type'], bool(c['nullable']), c['position'],
                               bool(c['in_primary_key'])) for c in self.columns),
            'views': sorted((k, v.parse_status, v.uses_star, tuple(sorted(v.column_kinds.items())))
                            for k, v in self.views.items()),
            'dependencies': sorted((d['dependent'], d['dependent_column'] or '', d['used'],
                                    d['used_column'] or '', d['derived_role']) for d in self.dependencies),
            'routines': sorted((r['model'], r['kind'], r['name']) for r in self.routines),
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def _key(schema: str, name: str) -> str:
    return f'{schema}.{name}'


def build(metadata: dict[str, list[dict]], vdb_info: dict,
          identifier_case_by_model: dict[str, str],
          default_schema_by_model: dict[str, str | None] | None = None) -> SigmaSnapshot:
    default_schema_by_model = default_schema_by_model or {}
    vdb_row = (metadata.get('virtual_databases') or [{}])[0]
    vdb = {'name': vdb_info.get('vdb-name') or vdb_row.get('Name'),
           'version': str(vdb_info.get('vdb-version') or vdb_row.get('Version') or ''),
           'connection_type': vdb_info.get('connection-type'),
           'status': vdb_info.get('status'),
           'loading_timestamp': vdb_row.get('LoadingTimestamp'),
           'active_timestamp': vdb_row.get('ActiveTimestamp')}

    models = []
    for model in vdb_info.get('models', []):
        models.append({
            'name': model.get('model-name'),
            'model_type': (model.get('model-type') or 'PHYSICAL').lower(),
            'visible': bool(model.get('visible', True)),
            'metadata_status': model.get('metadata-status'),
            'validity_errors': [e for e in (model.get('validity-errors') or [])
                                if (e or {}).get('severity') == 'ERROR'],
            'sources': [{'source_name': s.get('source-name'), 'translator': s.get('translator-name'),
                         'jndi_name': s.get('jndi-name')}
                        for s in (model.get('source-mappings') or [])],
        })
    known_models = {m['name'] for m in models}

    views_raw = {_key(v['SchemaName'], v['Name']): v.get('Body') or ''
                 for v in metadata.get('views', [])}
    matviews = {_key(v.get('SchemaName'), v.get('Name')) for v in metadata.get('matviews', [])
                if v.get('Name')}

    tables, uid_index = [], {}
    for row in metadata.get('tables', []):
        schema, name = row['SchemaName'], row['Name']
        if schema not in known_models:
            continue
        key = _key(schema, name)
        kind = ('materialized_view' if key in matviews
                else 'view' if key in views_raw or not row.get('IsPhysical', True)
                else 'foreign')
        case = identifier_case_by_model.get(schema, 'preserve')
        src_schema, src_table = split_name_in_source(row.get('NameInSource'))
        tables.append({'model': schema, 'name': name, 'kind': kind, 'uid': row.get('UID'),
                       'name_in_source': row.get('NameInSource'),
                       # tabel tanpa NAMEINSOURCE memakai skema bawaan koneksi sumber
                       'source_schema': normalize(src_schema or default_schema_by_model.get(schema), case),
                       'source_table': normalize(src_table or name, case)})
        if row.get('UID'):
            uid_index[row['UID']] = ('table', key, None)

    primary = {(k['SchemaName'], k['TableName'], k['Name'])
               for k in metadata.get('keys', []) if k.get('KeyType') == 'Primary'}
    columns = []
    for row in metadata.get('columns', []):
        schema, table, name = row['SchemaName'], row['TableName'], row['Name']
        if schema not in known_models:
            continue
        case = identifier_case_by_model.get(schema, 'preserve')
        columns.append({
            'model': schema, 'table': table, 'name': name, 'uid': row.get('UID'),
            'name_in_source': row.get('NameInSource'),
            'source_column': normalize(strip_ident(row.get('NameInSource') or name), case),
            'position': row.get('Position'), 'data_type': row.get('DataType'),
            'nullable': row.get('NullType') != 'No Nulls', 'length': row.get('Length'),
            'precision': row.get('Precision'), 'scale': row.get('Scale'),
            'in_primary_key': (schema, table, name) in primary,
        })
        if row.get('UID'):
            uid_index[row['UID']] = ('column', _key(schema, table), name)

    views = {key: classify_view(body) for key, body in views_raw.items()}

    dependencies = []
    projection_pairs = set()
    for row in metadata.get('usage', []):
        user = uid_index.get(row.get('UID'))
        used = uid_index.get(row.get('Uses_UID'))
        if not user or not used:
            continue
        dependent_table, dependent_column = user[1], user[2]
        used_table, used_column = used[1], used[2]
        if dependent_column and used_column:
            projection_pairs.add((dependent_table, used_table, used_column))
        dependencies.append({'dependent': dependent_table, 'dependent_column': dependent_column,
                             'used': used_table, 'used_column': used_column})
    for dep in dependencies:
        if dep['dependent_column']:
            dep['derived_role'] = 'projection'
        elif dep['used_column'] is None:
            dep['derived_role'] = 'table'
        elif (dep['dependent'], dep['used'], dep['used_column']) in projection_pairs:
            dep['derived_role'] = 'projection'
        else:
            dep['derived_role'] = 'predicate'

    routines = []
    for kind, rows in (('stored_procedure', metadata.get('procedures') or []),
                       ('trigger', metadata.get('triggers') or [])):
        for row in rows:
            schema = row.get('SchemaName')
            if schema not in known_models:
                continue
            routines.append({'model': schema, 'kind': kind,
                             'name': row.get('Name') or row.get('ProcedureName') or row.get('TriggerName'),
                             'table_name': row.get('TableName'),
                             'body': row.get('Body') or row.get('Definition')})

    return SigmaSnapshot(vdb=vdb, models=models, tables=tables, columns=columns, views=views,
                         dependencies=dependencies, routines=routines)
