"""The running example (branch feature/running-example, docs/running-example.md): the plan of
ASCAM for each of the 21 scenarios, over the REAL artifacts in the repository.

The VDB, mapping, and ontology are read from setup/. The Teiid runtime metadata (SYS.*,
SYSADMIN.Views, SYSADMIN.Usage, SYSADMIN.MatViews) is built from that VDB as Teiid would report
it: the columns of a view without a column list are those of its query (`*` expanded), every
view column has column-level usage rows for the source columns it reads, and the view itself
has object-level rows for its tables and for every column it refers to. That Teiid accepts the
VDB is checked on the running stack (./start.sh).

Each test names the Plan branch of the paper that it checks.
"""
import pathlib
import re
from datetime import datetime
from xml.dom import minidom

import pytest
import sqlglot
import yaml
from sqlglot import exp

import fixtures_obdf as fx
from test_impact import impact
from test_sync import set_clients

REPO = pathlib.Path(__file__).resolve().parents[5]
VDB_XML = (REPO / 'setup/data-federation/deployments/example-vdb.xml').read_text()
MAPPING = (REPO / 'setup/vkg-system/config/mapping.ttl').read_text()
ONTOLOGY = (REPO / 'setup/vkg-system/config/ontology_file.ttl').read_text()
CONFIG = yaml.safe_load((REPO / 'setup/ascam/knowledge/config/obdf-example.yaml').read_text())
EX = 'http://example.org/'
XSD = 'http://www.w3.org/2001/XMLSchema#'

TABLE = re.compile(r'CREATE FOREIGN TABLE "([^"]+)" \((.*?)\n\s*\) OPTIONS\((.*?)\);', re.S)
COLUMN = re.compile(r'"([^"]+)" (\w+)(?:\([^)]*\))?( NOT NULL)?(?: OPTIONS\(NAMEINSOURCE \'([^\']+)\'\))?$')
VIEW = re.compile(r'CREATE VIEW "([^"]+)"\s*(\([^)]*\))?\s*(OPTIONS \([^)]*\))?\s*AS (.*?);\s*$', re.S | re.M)


def _models() -> list[tuple[str, bool, str, list[dict]]]:
    out = []
    for model in minidom.parseString(VDB_XML).getElementsByTagName('model'):
        text = ''.join(n.data for m in model.getElementsByTagName('metadata') for n in m.childNodes
                       if n.nodeType in (n.CDATA_SECTION_NODE, n.TEXT_NODE))
        sources = [{'source-name': s.getAttribute('name'), 'jndi-name': s.getAttribute('connection-jndi-name'),
                    'translator-name': s.getAttribute('translator-name')}
                   for s in model.getElementsByTagName('source')]
        out.append((model.getAttribute('name'),
                    (model.getAttribute('type') or 'PHYSICAL').upper() != 'VIRTUAL', text, sources))
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


def example_metadata() -> dict:
    tables, columns, keys, views, usage, matviews, uid = [], [], [], [], [], [], {}
    table_columns: dict[tuple[str, str], list[str]] = {}

    def new_uid(key, prefix):
        uid[key] = f'{prefix}{len(uid) + 1}'
        return uid[key]

    for model, physical, ddl, _ in _models():
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

    for model, physical, ddl, _ in _models():
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
                    if isinstance(inner, exp.Star) or (isinstance(inner, exp.Column) and isinstance(inner.this, exp.Star)):
                        expanded = [(c, [(src[0], src[1], c)]) for c in table_columns[src]]
                    else:
                        expanded = [(item.alias_or_name, [(src[0], src[1], c.name) for c in item.find_all(exp.Column)])]
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
        'virtual_databases': [{'Name': 'example', 'Version': '1',
                               'LoadingTimestamp': datetime(2026, 11, 18, 1, 0),
                               'ActiveTimestamp': datetime(2026, 11, 18, 1, 0, 1)}],
        'schemas': [{'Name': m, 'IsPhysical': f} for m, f, _, _ in _models()],
        'tables': tables, 'columns': columns, 'keys': keys, 'views': views, 'usage': usage,
        'matviews': matviews, 'procedures': [], 'triggers': [],
    }


def example_vdb_info() -> dict:
    return {
        'vdb-name': 'example', 'vdb-version': '1', 'status': 'ACTIVE', 'connection-type': 'BY_VERSION',
        'properties': [{'property-name': 'deployment-name', 'property-value': 'example-vdb.xml'}],
        'models': [{'model-name': m, 'model-type': 'PHYSICAL' if f else 'VIRTUAL', 'visible': True,
                    'metadata-status': 'LOADED', 'validity-errors': [], 'source-mappings': s}
                   for m, f, _, s in _models()],
    }


@pytest.fixture(scope='module')
def api_module(migrated, alembic_cfg, db_url):
    from fastapi.testclient import TestClient
    from conftest import TOKENS
    from ascam_knowledge.api.app import create_app
    from ascam_knowledge.db.session import make_session_factory
    from ascam_knowledge.security.auth import TokenRegistry
    from ascam_knowledge.security.crypto import SecretBox, generate_key
    factory = make_session_factory(db_url)
    app = create_app(session_factory=factory, secret_box=SecretBox([generate_key()]),
                     tokens=TokenRegistry([f'{k}:{v}' for k, v in TOKENS.items()]))
    with TestClient(app) as client:
        client.headers['Authorization'] = f"Bearer {TOKENS['ui']}"
        client.headers['X-ASCAM-User'] = 'tester'
        client.factory = factory
        client.app_ref = app
        yield client
    factory.kw['bind'].dispose()


@pytest.fixture(scope='module')
def example(api_module):
    api = api_module
    oid = api.post('/api/v1/obdf', json={'name': 'running-example'}).json()['id']
    for source in CONFIG['sources']:
        api.post(f'/api/v1/obdf/{oid}/sources', json={k: source[k] for k in ('logical_name', 'dbms', 'database_name')})
    set_clients(api, meta=fx.FakeMetadata(example_metadata()),
                admin=fx.FakeAdmin(info=example_vdb_info(), content=VDB_XML.encode()),
                agent=fx.FakeAgent(mapping=MAPPING, ontology=ONTOLOGY))
    policy = CONFIG['naming_policy']
    api.put(f'/api/v1/obdf/{oid}/naming-policy',
            json={'property_iri_template': policy['property_iri_template'],
                  'on_collision': policy['on_collision'], 'namespace': policy['namespace']})
    r = api.put(f'/api/v1/obdf/{oid}/type-mappings', json=CONFIG['type_mappings'])
    assert r.status_code == 200, r.text
    r = api.post(f'/api/v1/obdf/{oid}/sync')
    assert r.status_code == 200, r.text
    return api, oid


def actions_of(out, operation):
    return [a for a in out['actions'] if a['operation'] == operation]


def local(iri):
    return iri.rsplit('#', 1)[-1].rsplit('/', 1)[-1]


def drop(api, oid, table, column, source='reporting', schema='public'):
    return impact(api, oid, operation='drop', source=source, schema=schema, table=table, column=column)


def rename(api, oid, table, column, new, source='reporting', schema='public'):
    return impact(api, oid, operation='rename', source=source, schema=schema, table=table,
                  column=column, new_column=new)


def add(api, oid, table, column, column_type, source='reporting', schema='public'):
    return impact(api, oid, operation='add', source=source, schema=schema, table=table,
                  column=column, column_type=column_type)


def reasons_with(out, *fragments):
    return any(all(f in r for f in fragments) for r in out['reasons'])


def test_artifacts_sync_without_errors(example):
    api, oid = example
    version = api.get(f'/api/v1/obdf/{oid}/versions?limit=1').json()[0]
    detail = api.get(f"/api/v1/versions/{version['id']}").json()
    assert [i for i in detail['issues'] if i['severity'] == 'error'] == []
    assert not [i for i in detail['issues'] if i['code'] in ('logical_column_unresolved',
                                                               'logical_source_unresolved')]


# ── r01-r12: DROP and RENAME on S1 ──────────────────────────────────────────────────
def test_r01_drop_first_name_is_example_dependents(example):
    """P-002 (Example ex:dependents): W = {v_active.name}, M_c = {a2}, ex:name deprecated."""
    api, oid = example
    out = drop(api, oid, 'person', 'first_name')
    assert out['decision'] == 'auto' and out['pattern'] == 'P-002', out['reasons']
    assert [(a['model'], a['table'], a['column']) for a in actions_of(out, 'drop_column')] == [
        ('src', 'person', 'first_name')]
    [view] = actions_of(out, 'alter_view')
    assert (view['table'], view['removed_columns']) == ('v_active', ['name'])
    assert '"first_name"' not in view['body'] and 'WHERE' in view['body']
    [pom] = actions_of(out, 'remove_predicate_object_map')
    assert (local(pom['predicate_iri']), local(pom['triples_map_iri']), pom['object']) == (
        'name', 'TM_ActivePerson', 'name')
    assert actions_of(out, 'rewrite_logical_table') == []        # SELECT * names no column
    assert [local(a['predicate_iri']) for a in actions_of(out, 'deprecate_property')] == ['name']


def test_r02_rename_first_name_is_repair_a(example):
    """P-003 (Example ex:repairs, repair (a)): only NAMEINSOURCE of ĉ changes."""
    api, oid = example
    out = rename(api, oid, 'person', 'first_name', 'given_name')
    assert out['decision'] == 'auto' and out['pattern'] == 'P-003', out['reasons']
    assert out['actions'] == [{'artifact': 'vdb', 'operation': 'set_name_in_source', 'model': 'src',
                               'table': 'person', 'column': 'first_name', 'name_in_source': 'given_name'}]


def test_r03_drop_active_used_in_where_is_notified(example):
    """Table 1, drop: weakest link predicate (v_active filters on active)."""
    api, oid = example
    out = drop(api, oid, 'person', 'active')
    assert out['decision'] == 'notify' and out['actions'] == []
    assert reasons_with(out, 'WHERE', 'v.v_active')


def test_r04_drop_id_is_notified(example):
    """Table 1, drop: ĉ is in the primary key, role IRI template."""
    api, oid = example
    out = drop(api, oid, 'person', 'id')
    assert out['decision'] == 'notify' and out['actions'] == []
    assert reasons_with(out, 'primary key')
    assert reasons_with(out, 'iri_template')


def test_r05_rename_into_a_name_already_read_is_notified(example):
    """Table 1, rename: another column of τ̂ (town_legacy) already reads c' = town."""
    api, oid = example
    out = rename(api, oid, 'staff', 'city', 'town')
    assert out['decision'] == 'notify'
    assert reasons_with(out, 'already used', 'town_legacy')


def test_r06_drop_phone_keeps_the_assertion_of_phone2(example):
    """P-002: one triples map fills ex:phone from phone and phone2; only phone's assertion goes
    (condition (ii); ADR-0031) and ex:phone is not deprecated."""
    api, oid = example
    out = drop(api, oid, 'contact', 'phone')
    assert out['decision'] == 'auto', out['reasons']
    [pom] = actions_of(out, 'remove_predicate_object_map')
    assert (local(pom['triples_map_iri']), pom['object']) == ('TM_Contact', 'phone')
    assert [local(a['triples_map_iri']) for a in actions_of(out, 'rewrite_logical_table')] == ['TM_Contact']
    assert [local(a['predicate_iri']) for a in actions_of(out, 'keep_property')] == ['phone']
    assert actions_of(out, 'deprecate_property') == []


def test_r07_drop_projection_only_column_rewrites_the_query(example):
    """P-002: fax is only projected; rw_c rewrites the query, no assertion is removed, and
    ex:email (orphaned: its only assertion reads fax through the query) stays populated."""
    api, oid = example
    out = drop(api, oid, 'contact', 'fax')
    assert out['decision'] == 'auto', out['reasons']
    assert [(local(a['triples_map_iri']), a['column']) for a in actions_of(out, 'rewrite_logical_table')] == [
        ('TM_ContactEmail', 'fax')]
    assert actions_of(out, 'remove_predicate_object_map') == []
    assert actions_of(out, 'deprecate_property') == []


def test_r08_drop_nickname_keeps_property_filled_from_s2(example):
    """P-002: ex:nickname is still populated by TM_Member (MySQL)."""
    api, oid = example
    out = drop(api, oid, 'person', 'nickname')
    assert out['decision'] == 'auto', out['reasons']
    [pom] = actions_of(out, 'remove_predicate_object_map')
    assert (local(pom['triples_map_iri']), pom['object']) == ('TM_PersonNick', 'nickname')
    assert [local(a['predicate_iri']) for a in actions_of(out, 'keep_property')] == ['nickname']


def test_r09_drop_column_in_view_expression_is_notified(example):
    api, oid = example
    out = drop(api, oid, 'product', 'discount')
    assert out['decision'] == 'notify' and out['actions'] == []
    assert reasons_with(out, 'expression', 'v.v_net_price')


def test_r10_drop_that_empties_a_view_is_notified(example):
    api, oid = example
    out = drop(api, oid, 'note', 'body')
    assert out['decision'] == 'notify' and out['actions'] == []
    assert reasons_with(out, 'v.v_note_body', 'lose all')


def test_r11_drop_through_inline_view_is_notified(example):
    api, oid = example
    out = drop(api, oid, 'memo', 'body')
    assert out['decision'] == 'notify' and out['actions'] == []
    assert reasons_with(out, 'v.v_memo', 'inline')


def test_r12_drop_through_materialized_view_is_notified(example):
    api, oid = example
    out = drop(api, oid, 'doc', 'body')
    assert out['decision'] == 'notify' and out['actions'] == []
    assert reasons_with(out, 'materialized', 'v.v_doc_cache')


# ── r13-r18: ADD on S1 ──────────────────────────────────────────────────────────────
def test_r13_add_to_table_map_with_star_view(example):
    """P-001, X = {TM_City}; the plain SELECT * view v_city_all is no notification condition."""
    api, oid = example
    out = add(api, oid, 'city', 'population', 'INT')
    assert out['decision'] == 'hitl' and out['pattern'] == 'P-001', out['reasons']
    assert not reasons_with(out, 'selects *')
    [pom] = actions_of(out, 'add_predicate_object_map')
    assert (local(pom['triples_map_iri']), local(pom['predicate_iri'])) == ('TM_City', 'population')
    [prop] = actions_of(out, 'add_datatype_property')
    assert prop['domain'] == [EX + 'City']
    assert out['add_options']['range_iri'] == XSD + 'integer'


def test_r14_add_with_several_classes(example):
    """P-001, several classes: ASCAM proposes ex:Account (to be reviewed); X_C keeps the map
    without a class and leaves out TM_Customer, so no customer is inferred to be an Account."""
    api, oid = example
    out = add(api, oid, 'account', 'status', 'VARCHAR(20)')
    assert out['decision'] == 'hitl'
    assert reasons_with(out, 'more than one class')
    assert sorted(out['add_options']['classes']) == [EX + 'Account', EX + 'Customer']
    assert out['add_options']['proposed_domain'] == EX + 'Account'
    assert sorted(local(a['triples_map_iri']) for a in actions_of(out, 'add_predicate_object_map')) == [
        'TM_Account', 'TM_AccountRecord']


def test_r15_add_when_every_query_lists_its_columns(example):
    """P-001, X = ∅: a new triples map built from TM_PersonNick (same template, query + ĉ, no
    class of its own); the view v_active lists its columns and is not affected."""
    api, oid = example
    out = add(api, oid, 'person', 'birth_date', 'DATE')
    assert out['decision'] == 'hitl', out['reasons']
    assert actions_of(out, 'add_predicate_object_map') == []
    [new] = actions_of(out, 'add_triples_map')
    assert new['subject_template'] == 'http://example.org/person/{id}'
    assert 'birth_date' in new['sql_query'] and 'nickname' in new['sql_query']
    [prop] = actions_of(out, 'add_datatype_property')
    assert (local(prop['iri']), prop['domain']) == ('birthDate', [EX + 'Person'])
    assert out['add_options']['range_iri'] == XSD + 'date'


def test_r16_add_to_unmapped_table_changes_sigma_only(example):
    """P-001, X_C = ∅: only the VDB column is added."""
    api, oid = example
    out = add(api, oid, 'audit', 'actor', 'VARCHAR(20)')
    assert out['decision'] == 'hitl'
    assert [a['operation'] for a in out['actions']] == ['add_column']
    assert reasons_with(out, 'only the VDB column')


def test_r17_add_under_union_star_view_is_notified(example):
    """Table 1, add (ADR-0032): v_events gains c through * in a UNION."""
    api, oid = example
    out = add(api, oid, 'event', 'severity', 'VARCHAR(10)')
    assert out['decision'] == 'notify' and out['actions'] == []
    assert reasons_with(out, 'v.v_events', 'UNION')


def test_r18_add_reuses_deprecated_property(example):
    """P-001, reuse: ex:weight (domain ex:Tool, range xsd:decimal) is deprecated; the new column
    has the same domain and range, so the property is reused and its deprecation withdrawn."""
    api, oid = example
    out = add(api, oid, 'tool', 'weight', 'DECIMAL(6, 2)')
    assert out['decision'] == 'hitl', out['reasons']
    assert reasons_with(out, 'deprecated', 'reuses')
    [prop] = actions_of(out, 'add_datatype_property')
    assert (prop['iri'], prop['domain']) == (EX + 'weight', [EX + 'Tool'])
    assert [local(a['triples_map_iri']) for a in actions_of(out, 'add_triples_map')] == ['ascam-tool-weight']


# ── r19-r21: S2 (MySQL) ─────────────────────────────────────────────────────────────
def test_r19_rename_on_mysql(example):
    api, oid = example
    out = rename(api, oid, 'member', 'nama', 'nama_lengkap', source='registry', schema='registry')
    assert out['decision'] == 'auto' and out['pattern'] == 'P-003', out['reasons']
    assert [(a['model'], a['name_in_source']) for a in out['actions']] == [('reg', 'nama_lengkap')]


def test_r20_add_on_mysql_reuses_active_property(example):
    """P-001 on MySQL, X = ∅: a new triples map from TM_Member; ex:email exists (no domain, no
    range) and is reused."""
    api, oid = example
    out = add(api, oid, 'member', 'email', 'VARCHAR(50)', source='registry', schema='registry')
    assert out['decision'] == 'hitl', out['reasons']
    assert reasons_with(out, 'already exists')
    [new] = actions_of(out, 'add_triples_map')
    assert new['subject_template'] == 'http://example.org/member/{member_id}'
    [prop] = actions_of(out, 'add_datatype_property')
    assert (prop['iri'], prop['domain']) == (EX + 'email', [EX + 'Member'])


def test_r21_drop_on_mysql_keeps_property_filled_from_s1(example):
    api, oid = example
    out = drop(api, oid, 'member', 'panggilan', source='registry', schema='registry')
    assert out['decision'] == 'auto', out['reasons']
    [pom] = actions_of(out, 'remove_predicate_object_map')
    assert (local(pom['triples_map_iri']), pom['object']) == ('TM_Member', 'panggilan')
    assert [local(a['predicate_iri']) for a in actions_of(out, 'keep_property')] == ['nickname']
