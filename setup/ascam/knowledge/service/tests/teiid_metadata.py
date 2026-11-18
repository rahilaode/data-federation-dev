"""Teiid runtime metadata (SYS.*, SYSADMIN.Views, SYSADMIN.Usage, SYSADMIN.MatViews) built from a
VDB file as Teiid would report it, for the case-study tests over the real artifacts.

The columns of a view without a column list are those of its query (`*` expanded from the
foreign table), an inline column list names the columns, and a UNION contributes the sources of
every branch. Every view column has column-level usage rows for the source columns it reads, and
the view itself has object-level rows for its tables and for every column it refers to. That
Teiid accepts the VDB is checked on the running stack.
"""
import re
from datetime import datetime
from xml.dom import minidom

import sqlglot
from sqlglot import exp

TABLE = re.compile(r'CREATE FOREIGN TABLE "([^"]+)" \((.*?)\n\s*\) OPTIONS\((.*?)\);', re.S)
COLUMN = re.compile(r'"([^"]+)" (\w+)(?:\([^)]*\))?( NOT NULL)?(?: OPTIONS\(NAMEINSOURCE \'([^\']+)\'\))?$')
VIEW = re.compile(r'CREATE VIEW "([^"]+)"\s*(\([^)]*\))?\s*(OPTIONS \([^)]*\))?\s*AS (.*?);\s*$',
                  re.S | re.M)


def models(vdb_xml: str) -> list[tuple[str, bool, str]]:
    out = []
    for model in minidom.parseString(vdb_xml).getElementsByTagName('model'):
        text = ''.join(n.data for m in model.getElementsByTagName('metadata') for n in m.childNodes
                       if n.nodeType in (n.CDATA_SECTION_NODE, n.TEXT_NODE))
        text = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
        out.append((model.getAttribute('name'),
                    (model.getAttribute('type') or 'PHYSICAL').upper() != 'VIRTUAL', text))
    return out


def _usage(uid, schema, name, element, uses_uid, uses_schema, uses_name, uses_element):
    return {'UID': uid, 'object_type': 'Column' if element else 'View', 'SchemaName': schema,
            'Name': name, 'ElementName': element, 'Uses_UID': uses_uid,
            'Uses_object_type': 'Column' if uses_element else 'Table', 'Uses_SchemaName': uses_schema,
            'Uses_Name': uses_name, 'Uses_ElementName': uses_element}


def _column(schema, table, name, position, uid, data_type='string', nis=None, not_null=False):
    return {'SchemaName': schema, 'TableName': table, 'Name': name, 'Position': position,
            'NameInSource': nis, 'DataType': data_type, 'NullType': 'No Nulls' if not_null else 'Nullable',
            'Length': 0, 'Precision': 0, 'Scale': 0, 'UID': uid}


def metadata(vdb_xml: str, vdb_name: str) -> dict:
    tables, columns, keys, views, usage, matviews, uid = [], [], [], [], [], [], {}
    table_columns: dict[tuple[str, str], list[str]] = {}

    def new_uid(key, prefix):
        uid[key] = f'{prefix}{len(uid) + 1}'
        return uid[key]

    for model, physical, ddl in models(vdb_xml):
        if not physical:
            continue
        for name, body, options in TABLE.findall(ddl):
            nis = re.search(r"NAMEINSOURCE '([^']+)'", options)
            tables.append({'SchemaName': model, 'Name': name, 'Type': 'Table', 'IsPhysical': True,
                           'NameInSource': nis[1] if nis else None, 'UID': new_uid((model, name), 't')})
            position = 0
            for line in (b.strip().rstrip(',') for b in body.strip().split('\n')):
                if line.startswith('PRIMARY KEY'):
                    for p, key in enumerate(re.findall(r'"([^"]+)"', line), 1):
                        keys.append({'SchemaName': model, 'TableName': name, 'Name': key,
                                     'KeyName': 'PK', 'KeyType': 'Primary', 'Position': p})
                    continue
                m = COLUMN.match(line)
                assert m, line
                position += 1
                columns.append(_column(model, name, m[1], position, new_uid((model, name, m[1]), 'c'),
                                       m[2], m[4], bool(m[3])))
                table_columns.setdefault((model, name), []).append(m[1])

    for model, physical, ddl in models(vdb_xml):
        if physical:
            continue
        for name, inline, options, body in VIEW.findall(ddl):
            body = ' '.join(body.split())
            views.append({'SchemaName': model, 'Name': name, 'Body': body})
            if options and 'MATERIALIZED' in options.upper():
                matviews.append({'SchemaName': model, 'Name': name})
            table_uid = new_uid((model, name), 't')
            tables.append({'SchemaName': model, 'Name': name, 'Type': 'Table', 'IsPhysical': False,
                           'NameInSource': None, 'UID': table_uid})
            tree = sqlglot.parse_one(body)
            branches = list(tree.find_all(exp.Select))
            outputs: list[str] = []
            sources_by_position: list[list[tuple[str, str, str]]] = []
            for b, select in enumerate(branches):
                source = select.find(exp.Table)
                src = (source.db, source.name)
                position = 0
                for item in select.expressions:
                    inner = item.this if isinstance(item, exp.Alias) else item
                    if isinstance(inner, exp.Star) or (isinstance(inner, exp.Column)
                                                       and isinstance(inner.this, exp.Star)):
                        expanded = [(c, [(src[0], src[1], c)]) for c in table_columns[src]]
                    else:
                        expanded = [(item.alias_or_name,
                                     [(src[0], src[1], c.name) for c in item.find_all(exp.Column)])]
                    for column, reads in expanded:
                        if b == 0:
                            outputs.append(column)
                            sources_by_position.append([])
                        sources_by_position[position].extend(reads)
                        position += 1
            if inline:                                   # the inline list names the columns
                outputs = re.findall(r'"([^"]+)"', inline)
            for position, column in enumerate(outputs, 1):
                columns.append(_column(model, name, column, position, new_uid((model, name, column), 'c')))
                table_columns.setdefault((model, name), []).append(column)
                for s_model, s_table, s_column in sources_by_position[position - 1]:
                    usage.append(_usage(uid[(model, name, column)], model, name, column,
                                        uid[(s_model, s_table, s_column)], s_model, s_table, s_column))
            referenced = set()
            for select in branches:
                source = select.find(exp.Table)
                src = (source.db, source.name)
                usage.append(_usage(table_uid, model, name, None, uid[src], src[0], src[1], None))
                for c in select.find_all(exp.Column):
                    if not isinstance(c.this, exp.Star):
                        referenced.add((src, c.name))
            for (src, c) in sorted(referenced):
                usage.append(_usage(table_uid, model, name, None, uid[(src[0], src[1], c)],
                                    src[0], src[1], c))
    return {
        'virtual_databases': [{'Name': vdb_name, 'Version': '1',
                               'LoadingTimestamp': datetime(2026, 11, 18, 1, 0),
                               'ActiveTimestamp': datetime(2026, 11, 18, 1, 0, 1)}],
        'schemas': [{'Name': m, 'IsPhysical': f} for m, f, _ in models(vdb_xml)],
        'tables': tables, 'columns': columns, 'keys': keys, 'views': views, 'usage': usage,
        'matviews': matviews, 'procedures': [], 'triggers': [],
    }
