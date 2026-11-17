"""
Scenarios of the running example (docs/running-example.md): one schema change per Plan branch
of the paper, with how to undo it. Every scenario must give the same result when it is run
again, so the values of a column that is dropped are copied first and written back after the run.

  r01-r12  DROP and RENAME (P-002, P-003, and the notification conditions of Table 1)
  r13-r18  ADD (P-001) on S1 = reporting (PostgreSQL)
  r19-r21  the three operators on S2 = registry (MySQL)
"""
import subprocess
from dataclasses import dataclass, field
from typing import Callable

PG = ['docker', 'exec', 'datasources-pgsql', 'psql', '-U', 'postgres', '-d', 'reporting', '-tAc']
MY = ['docker', 'exec', 'datasources-mysql', 'mysql', '-umysql', '-pmysql', '-N', '-B',
      'registry', '-e']
EX = 'http://example.org/'


def run(command: list[str], sql: str) -> str:
    result = subprocess.run([*command, sql], capture_output=True, text=True)
    output = (result.stdout or '').strip()
    error = (result.stderr or '').strip()
    # the MySQL client warns about a password on the command line; that is not an error
    error = '\n'.join(b for b in error.splitlines() if 'password on the command line' not in b)
    if error:
        return f'{output} [{error[:200]}]'.strip()
    return output


def pg(sql: str) -> str:
    return run(PG, sql)


def my(sql: str) -> str:
    return run(MY, sql)


@dataclass
class Scenario:
    code: str
    title: str
    pattern: str
    decision: str                       # expected decision: auto, hitl, or notify
    source: str
    table: str
    column: str
    apply: Callable[[], str]
    restore: Callable[[], str]
    prepare: Callable[[], str] | None = None
    predicate: str | None = None        # predicate expected to change
    note: str = ''
    fill_data: Callable[[], str] | None = None
    # Text fragments that must appear together in one reason of the plan, so that the plan is
    # checked for the reason it was designed for, not only for its decision
    reasons_contain: tuple[str, ...] = ()
    view: str | None = None             # Teiid view that the notification must name
    branch: str = ''                    # the Plan branch of the paper that this scenario exercises
    new_column: str | None = None       # the new name of a RENAME
    expect: dict = field(default_factory=dict)   # expectations after the treatment (queries.py)


def _pg_exists(table: str, column: str) -> bool:
    return pg("SELECT count(*) FROM information_schema.columns WHERE table_schema='public' "
              f"AND table_name='{table}' AND column_name='{column}'") == '1'


def _my_exists(table: str, column: str) -> bool:
    return my("SELECT count(*) FROM information_schema.columns WHERE table_schema='registry' "
              f"AND table_name='{table}' AND column_name='{column}'").strip() == '1'


def _pg_drop(table: str, column: str, sql_type: str, key: str = 'id', after: str | None = None):
    """prepare/apply/restore of a DROP on PostgreSQL that can be run again: the values are
    copied to ascam_backup_<table>_<column> before the first run and written back by restore.
    `key` identifies a row (another column when the key itself is dropped); `after` runs at the
    end of the restore, e.g. to recreate a primary key (not a column operation, so the schema
    monitor does not log it)."""
    backup = f'ascam_backup_{table}_{column}'

    def prepare() -> str:
        if not _pg_exists(table, column):
            return 'column not restored yet; backup not recreated'
        return pg(f'CREATE TABLE IF NOT EXISTS public.{backup} AS SELECT {key}, {column} FROM public.{table}')

    def apply() -> str:
        return pg(f'ALTER TABLE public.{table} DROP COLUMN {column}')

    def restore() -> str:
        if _pg_exists(table, column):
            return 'clean'                         # no DDL runs: the harness waits for no event
        pg(f'ALTER TABLE public.{table} ADD COLUMN {column} {sql_type}')
        pg(f'UPDATE public.{table} t SET {column} = c.{column} FROM public.{backup} c WHERE c.{key} = t.{key}')
        if after:
            pg(after)
        return 'restored: ' + pg(f'SELECT count(*) FROM public.{table} WHERE {column} IS NOT NULL')
    return prepare, apply, restore


def _pg_rename(table: str, column: str, new: str):
    def apply() -> str:
        return pg(f'ALTER TABLE public.{table} RENAME COLUMN {column} TO {new}')

    def restore() -> str:
        if not _pg_exists(table, new):
            return 'clean'
        return 'restored: ' + pg(f'ALTER TABLE public.{table} RENAME COLUMN {new} TO {column}')
    return apply, restore


def _pg_add(table: str, column: str, sql_type: str, fill: str | None = None):
    def apply() -> str:
        return pg(f'ALTER TABLE public.{table} ADD COLUMN {column} {sql_type}')

    def restore() -> str:
        if not _pg_exists(table, column):
            return 'clean'
        return 'restored: ' + pg(f'ALTER TABLE public.{table} DROP COLUMN {column}')

    def fill_data() -> str:
        return pg(f'UPDATE public.{table} SET {column} = {fill}')
    return apply, restore, (fill_data if fill else None)


def _my_drop(table: str, column: str, sql_type: str, key: str):
    backup = f'ascam_backup_{table}_{column}'

    def prepare() -> str:
        if not _my_exists(table, column):
            return 'column not restored yet; backup not recreated'
        return my(f'CREATE TABLE IF NOT EXISTS {backup} AS SELECT {key}, {column} FROM {table}')

    def apply() -> str:
        return my(f'ALTER TABLE {table} DROP COLUMN {column}')

    def restore() -> str:
        if _my_exists(table, column):
            return 'clean'
        my(f'ALTER TABLE {table} ADD COLUMN {column} {sql_type}')
        my(f'UPDATE {table} t JOIN {backup} c ON c.{key} = t.{key} SET t.{column} = c.{column}')
        return 'restored: ' + my(f'SELECT count(*) FROM {table} WHERE {column} IS NOT NULL')
    return prepare, apply, restore


def _my_rename(table: str, column: str, new: str):
    def apply() -> str:
        return my(f'ALTER TABLE {table} RENAME COLUMN {column} TO {new}')

    def restore() -> str:
        if not _my_exists(table, new):
            return 'clean'
        # the MySQL client prints nothing when ALTER succeeds: the marker tells the harness that
        # a DDL ran and that it must wait for the restore event
        return 'restored: ' + my(f'ALTER TABLE {table} RENAME COLUMN {new} TO {column}')
    return apply, restore


def _my_add(table: str, column: str, sql_type: str, fill: str | None = None):
    def apply() -> str:
        return my(f'ALTER TABLE {table} ADD COLUMN {column} {sql_type}')

    def restore() -> str:
        if not _my_exists(table, column):
            return 'clean'
        return 'restored: ' + my(f'ALTER TABLE {table} DROP COLUMN {column}')

    def fill_data() -> str:
        return my(f'UPDATE {table} SET {column} = {fill}')
    return apply, restore, (fill_data if fill else None)


def _scenario(code, title, pattern, decision, source, table, column, ops, **kw) -> Scenario:
    """ops: (prepare, apply, restore) for a DROP, (apply, restore) for a RENAME, and
    (apply, restore, fill_data) for an ADD."""
    if pattern == 'P-002':
        prepare, apply, restore = ops
        kw.setdefault('prepare', prepare)
    elif pattern == 'P-001':
        apply, restore, fill = ops
        kw.setdefault('fill_data', fill)
    else:
        apply, restore = ops
    return Scenario(code=code, title=title, pattern=pattern, decision=decision, source=source,
                    table=table, column=column, apply=apply, restore=restore, **kw)


R = 'reporting'
SCENARIOS = {s.code: s for s in [
    # ── DROP and RENAME on S1 (PostgreSQL) ──────────────────────────────────────────
    _scenario('r01', 'DROP COLUMN first_name on person (Example ex:dependents)', 'P-002', 'auto',
              R, 'person', 'first_name', _pg_drop('person', 'first_name', 'VARCHAR(50)'),
              predicate=EX + 'name', view='v.v_active',
              branch='P-002: W = {v_active.name}, M_c = {a2}; ex:name is orphaned and deprecated',
              note='v_active is narrowed with ALTER VIEW; ex:name disappears from the graph'),
    _scenario('r02', 'RENAME COLUMN first_name TO given_name on person (Example ex:repairs)', 'P-003',
              'auto', R, 'person', 'first_name', _pg_rename('person', 'first_name', 'given_name'), new_column='given_name',
              predicate=EX + 'name',
              branch='P-003: Σ\' = Σ[src(ĉ) := given_name]; M and T unchanged (repair (a))',
              note='answers must be identical'),
    _scenario('r03', 'DROP COLUMN active on person (used in WHERE of v_active)', 'P-002', 'notify',
              R, 'person', 'active', _pg_drop('person', 'active', 'BOOLEAN'),
              predicate=EX + 'name', view='v.v_active', reasons_contain=('WHERE', 'v.v_active'),
              branch='notify: weakest link predicate (rows would change)'),
    _scenario('r04', 'DROP COLUMN id on person (primary key, IRI template)', 'P-002', 'notify',
              R, 'person', 'id', _pg_drop('person', 'id', 'INTEGER', key='first_name',
                                          after='ALTER TABLE public.person ADD PRIMARY KEY (id)'),
              predicate=EX + 'nickname', reasons_contain=('primary key',),
              branch='notify: ĉ is in the primary key; role IRI template'),
    _scenario('r05', 'RENAME COLUMN city TO town on staff (town is already read)', 'P-003', 'notify',
              R, 'staff', 'city', _pg_rename('staff', 'city', 'town'), new_column='town',
              predicate=EX + 'city', reasons_contain=('already used', 'town_legacy'),
              branch='notify: another column of τ̂ already reads c\''),
    _scenario('r06', 'DROP COLUMN phone on contact (ex:phone also filled from phone2)', 'P-002', 'auto',
              R, 'contact', 'phone', _pg_drop('contact', 'phone', 'VARCHAR(20)'),
              predicate=EX + 'phone',
              branch='P-002: only the assertion of phone is removed; ex:phone is kept (ADR-0031)'),
    _scenario('r07', 'DROP COLUMN fax on contact (projection only)', 'P-002', 'auto',
              R, 'contact', 'fax', _pg_drop('contact', 'fax', 'VARCHAR(20)'),
              predicate=EX + 'email',
              branch='P-002: rw_c removes fax from the query; ex:email is orphaned but still populated'),
    _scenario('r08', 'DROP COLUMN nickname on person (ex:nickname also filled from S2)', 'P-002', 'auto',
              R, 'person', 'nickname', _pg_drop('person', 'nickname', 'VARCHAR(50)'),
              predicate=EX + 'nickname',
              branch='P-002: ex:nickname is kept because TM_Member (MySQL) still populates it'),
    _scenario('r09', 'DROP COLUMN discount on product (used in a view expression)', 'P-002', 'notify',
              R, 'product', 'discount', _pg_drop('product', 'discount', 'NUMERIC(4,2)'),
              predicate=EX + 'netPrice', view='v.v_net_price',
              reasons_contain=('expression', 'v.v_net_price'),
              branch='notify: weakest link expression (values would change)'),
    _scenario('r10', 'DROP COLUMN body on note (view v_note_body would lose all its columns)', 'P-002',
              'notify', R, 'note', 'body', _pg_drop('note', 'body', 'VARCHAR(100)'),
              predicate=EX + 'noteBody', view='v.v_note_body',
              reasons_contain=('v.v_note_body', 'lose all'),
              branch='notify: a view passing ĉ on would lose all its columns'),
    _scenario('r11', 'DROP COLUMN body on memo (view v_memo declares its columns inline)', 'P-002',
              'notify', R, 'memo', 'body', _pg_drop('memo', 'body', 'VARCHAR(100)'),
              predicate=EX + 'memoBody', view='v.v_memo', reasons_contain=('v.v_memo', 'inline'),
              branch='notify: a view passing ĉ on declares its columns inline'),
    _scenario('r12', 'DROP COLUMN body on doc (view v_doc_cache is materialized)', 'P-002', 'notify',
              R, 'doc', 'body', _pg_drop('doc', 'body', 'VARCHAR(100)'),
              predicate=EX + 'docBody', view='v.v_doc_cache',
              reasons_contain=('materialized', 'v.v_doc_cache'),
              branch='notify: a view passing ĉ on is materialized'),

    # ── ADD on S1 (PostgreSQL) ──────────────────────────────────────────────────────
    _scenario('r13', 'ADD COLUMN population on city (rr:tableName map; v_city_all selects *)', 'P-001',
              'hitl', R, 'city', 'population', _pg_add('city', 'population', 'INTEGER', 'id * 1000'),
              predicate=EX + 'population',
              branch='P-001, X = {TM_City}: one predicate-object map is added; the plain SELECT * '
                     'view is no notification condition'),
    _scenario('r14', 'ADD COLUMN status on account (two classes and a map without a class)', 'P-001',
              'hitl', R, 'account', 'status', _pg_add('account', 'status', 'VARCHAR(20)', "'open'"),
              predicate=EX + 'status', reasons_contain=('more than one class',),
              branch='P-001, several classes: ASCAM proposes ex:Account; X_C = {TM_Account, '
                     'TM_AccountRecord}, TM_Customer is left out'),
    _scenario('r15', 'ADD COLUMN birth_date on person (every query lists its columns)', 'P-001',
              'hitl', R, 'person', 'birth_date', _pg_add('person', 'birth_date', 'DATE', "DATE '2000-01-01'"),
              predicate=EX + 'birthDate', reasons_contain=('NEW TriplesMap',),
              branch='P-001, X = ∅: a new triples map from TM_PersonNick (same template, no class)'),
    _scenario('r16', 'ADD COLUMN actor on audit (no triples map reads audit)', 'P-001', 'hitl',
              R, 'audit', 'actor', _pg_add('audit', 'actor', 'VARCHAR(20)', "'system'"),
              reasons_contain=('only the VDB column',),
              branch='P-001, X_C = ∅: only Σ changes; M and T stay as they are',
              expect={'property': False}),
    _scenario('r17', 'ADD COLUMN severity on event (v_events selects * in a UNION)', 'P-001', 'notify',
              R, 'event', 'severity', _pg_add('event', 'severity', 'VARCHAR(10)'),
              view='v.v_events', reasons_contain=('v.v_events', 'UNION'),
              branch='notify (ADR-0032): a view that gains c through * combines branches with UNION',
              expect={'property': False}),
    _scenario('r18', 'ADD COLUMN weight on tool (reuses the deprecated ex:weight)', 'P-001', 'hitl',
              R, 'tool', 'weight', _pg_add('tool', 'weight', 'NUMERIC(6,2)', '1.5'),
              predicate=EX + 'weight', reasons_contain=('deprecated',),
              branch='P-001, reuse: same domain and range; deprecated(ex:weight) is withdrawn'),

    # ── the three operators on S2 (MySQL) ──────────────────────────────────────────
    _scenario('r19', 'RENAME COLUMN nama TO nama_lengkap on member', 'P-003', 'auto', 'registry',
              'member', 'nama', _my_rename('member', 'nama', 'nama_lengkap'), new_column='nama_lengkap',
              predicate=EX + 'fullName', branch='P-003 on MySQL'),
    _scenario('r20', 'ADD COLUMN email on member (reuses the active ex:email)', 'P-001', 'hitl',
              'registry', 'member', 'email', _my_add('member', 'email', 'VARCHAR(50)', "'member@example.org'"),
              predicate=EX + 'email', reasons_contain=('already exists',),
              branch='P-001 on MySQL, X = ∅ and an existing property with no domain or range'),
    _scenario('r21', 'DROP COLUMN panggilan on member (ex:nickname also filled from S1)', 'P-002',
              'auto', 'registry', 'member', 'panggilan',
              _my_drop('member', 'panggilan', 'VARCHAR(50)', 'member_id'),
              predicate=EX + 'nickname', branch='P-002 on MySQL; ex:nickname is kept'),
]}
