"""
ASCAM evaluation scenarios: the schema change applied to a source and how to undo it.
Every scenario must give the same result when it is run again, so the values of a column
that is dropped are copied first and written back after the run.
"""
import subprocess
from dataclasses import dataclass
from typing import Callable

# NPD case study (ADR-0027): reporting = PostgreSQL (public schema), registry = MySQL
PG = ['docker', 'exec', 'datasources-pgsql', 'psql', '-U', 'postgres', '-d', 'reporting', '-tAc']
MY = ['docker', 'exec', 'datasources-mysql', 'mysql', '-umysql', '-pmysql', '-N', '-B',
      'registry', '-e']


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
    decision: str                       # expected D11 decision
    source: str
    table: str
    column: str
    apply: Callable[[], str]
    restore: Callable[[], str]
    prepare: Callable[[], str] | None = None
    predicate: str | None = None        # predicate expected to change
    note: str = ''
    # Fills the new column so that "the new query returns rows" can be tested (A001)
    fill_data: Callable[[], str] | None = None
    # Text fragments that must appear in the reasons of a non-automatic decision (this checks
    # the reason, not only the decision)
    reasons_contain: tuple[str, ...] = ()
    view: str | None = None             # Teiid view the column passes through (A007-A009)


# NPD names are mixed case: PostgreSQL needs them double-quoted (ADR-0029), MySQL is run in its
# default mode here, so backticks are used there.
def _pg_exists(table: str, column: str) -> bool:
    return pg("SELECT count(*) FROM information_schema.columns WHERE table_schema='public' "
              f"AND table_name='{table}' AND column_name='{column}'") == '1'


def _my_exists(table: str, column: str) -> bool:
    return my("SELECT count(*) FROM information_schema.columns WHERE table_schema='registry' "
              f"AND table_name='{table}' AND column_name='{column}'").strip() == '1'


def _pg_drop(table: str, column: str, key: list[str], sql_type: str):
    """apply/restore/prepare of a DROP on PostgreSQL that can be run again: the values are copied
    to ascam_backup_<column> before the first run and written back by restore."""
    backup = f'ascam_backup_{column.lower()}'
    keys = ', '.join(f'"{k}"' for k in key)
    join = ' AND '.join(f'c."{k}" = t."{k}"' for k in key)

    def prepare() -> str:
        if not _pg_exists(table, column):
            return 'column not restored yet; backup not recreated'
        return pg(f'CREATE TABLE IF NOT EXISTS public.{backup} AS SELECT {keys}, "{column}" FROM public."{table}"')

    def apply() -> str:
        return pg(f'ALTER TABLE public."{table}" DROP COLUMN "{column}"')

    def restore() -> str:
        if _pg_exists(table, column):
            # no DDL runs, so the harness must not wait for a restore event (finding of
            # evaluation 20260924T071746 in the first case study)
            return 'clean'
        pg(f'ALTER TABLE public."{table}" ADD COLUMN "{column}" {sql_type}')
        pg(f'UPDATE public."{table}" t SET "{column}" = c."{column}" FROM public.{backup} c WHERE {join}')
        return 'restored: ' + pg(f'SELECT count(*) FROM public."{table}" WHERE "{column}" IS NOT NULL')
    return prepare, apply, restore


def _my_drop(table: str, column: str, key: list[str], sql_type: str):
    backup = f'ascam_backup_{column.lower()}'
    keys = ', '.join(f'`{k}`' for k in key)
    join = ' AND '.join(f'c.`{k}` = t.`{k}`' for k in key)

    def prepare() -> str:
        if not _my_exists(table, column):
            return 'column not restored yet; backup not recreated'
        return my(f'CREATE TABLE IF NOT EXISTS {backup} AS SELECT {keys}, `{column}` FROM `{table}`')

    def apply() -> str:
        return my(f'ALTER TABLE `{table}` DROP COLUMN `{column}`')

    def restore() -> str:
        if _my_exists(table, column):
            return 'clean'
        my(f'ALTER TABLE `{table}` ADD COLUMN `{column}` {sql_type}')
        my(f'UPDATE `{table}` t JOIN {backup} c ON {join} SET t.`{column}` = c.`{column}`')
        return 'restored: ' + my(f'SELECT count(*) FROM `{table}` WHERE `{column}` IS NOT NULL')
    return prepare, apply, restore


# ── A001: new column on reporting (PostgreSQL) ─────────────────────────────────
def a001_apply() -> str:
    return pg('ALTER TABLE public.wellbore_mud ADD COLUMN "wlbMudPh" NUMERIC(6,2)')


def a001_fill_data() -> str:
    return pg('UPDATE public.wellbore_mud SET "wlbMudPh" = 8.5 + wellbore_mud_id / 10.0 '
              'WHERE wellbore_mud_id <= 3')


def a001_restore() -> str:
    if not _pg_exists('wellbore_mud', 'wlbMudPh'):
        return 'clean'
    return 'restored: ' + pg('ALTER TABLE public.wellbore_mud DROP COLUMN "wlbMudPh"')


# ── A002: column dropped on reporting ─────────────────────────────────────────
a002_prepare, a002_apply, a002_restore = _pg_drop(
    'wellbore_casing_and_lot', 'wlbLotMudDencity', ['wellbore_casing_and_lot_id'], 'NUMERIC(13,6)')


# ── A003: column renamed on reporting (joins wells to companies of registry) ───
def a003_apply() -> str:
    return pg('ALTER TABLE public.wellbore_exploration_all RENAME COLUMN "wlbDrillingOperator" '
              'TO "wlbDrillingCompany"')


def a003_restore() -> str:
    if not _pg_exists('wellbore_exploration_all', 'wlbDrillingCompany'):
        return 'clean'
    return 'restored: ' + pg('ALTER TABLE public.wellbore_exploration_all RENAME COLUMN '
                             '"wlbDrillingCompany" TO "wlbDrillingOperator"')


# ── A004: new column on registry (MySQL) ──────────────────────────────────────
def a004_apply() -> str:
    return my('ALTER TABLE company ADD COLUMN cmpCountryCode VARCHAR(2)')


def a004_fill_data() -> str:
    return my("UPDATE company SET cmpCountryCode = 'NO' ORDER BY cmpNpdidCompany LIMIT 3")


def a004_restore() -> str:
    if not _my_exists('company', 'cmpCountryCode'):
        return 'clean'
    return 'restored: ' + my('ALTER TABLE company DROP COLUMN cmpCountryCode')


# ── A005: column dropped on registry ──────────────────────────────────────────
a005_prepare, a005_apply, a005_restore = _my_drop(
    'discovery', 'dscDiscoveryYear', ['dscNpdidDiscovery'], 'INT')


# ── A006: column renamed on registry (target of the wells -> company join) ────
def a006_apply() -> str:
    return my('ALTER TABLE company RENAME COLUMN cmpLongName TO cmpFullName')


def a006_restore() -> str:
    if _my_exists('company', 'cmpFullName'):
        # the MySQL client prints nothing when ALTER succeeds; without a marker the empty output
        # reads as "nothing changed" and the harness does not wait for the restore event
        return 'restored: ' + my('ALTER TABLE company RENAME COLUMN cmpFullName TO cmpLongName')
    return 'clean'


# ── A007-A009: columns the mapping reads THROUGH a Teiid view (model services, ADR-0023) ──
# A007: pass-through view on reporting; no other TriplesMap reads the column
a007_prepare, a007_apply, a007_restore = _pg_drop(
    'wellbore_mud', 'wlbMudViscosityAtMD', ['wellbore_mud_id'], 'NUMERIC(13,6)')
# A008: view with aliases on registry; the mapping runs SELECT * over the view
a008_prepare, a008_apply, a008_restore = _my_drop(
    'field_investment_yearly', 'prfInvestmentsMillNOK', ['prfNpdidInformationCarrier', 'prfYear'],
    'DECIMAL(13,6)')
# A009: the column is used inside a view expression ("wlbCoreIntervalBottom" * 0.3048); negative control
a009_prepare, a009_apply, a009_restore = _pg_drop(
    'wellbore_core', 'wlbCoreIntervalBottom', ['wellbore_core_id'], 'NUMERIC(13,6)')


N = 'http://sws.ifi.uio.no/vocab/npd-v2#'
SCENARIOS = {
    'a001': Scenario(code='a001', title='ADD COLUMN wlbMudPh on wellbore_mud', pattern='P-001',
                     # ADR-0021: ADD needs the administrator's approval; ADR-0027: the logical
                     # tables list their columns, so ASCAM proposes a new TriplesMap
                     decision='hitl', source='reporting', table='wellbore_mud', column='wlbMudPh',
                     apply=a001_apply, restore=a001_restore, predicate=N + 'wlbMudPh',
                     note='3 rows of the new column are filled after the change',
                     fill_data=a001_fill_data),
    'a002': Scenario(code='a002', title='DROP COLUMN wlbLotMudDencity on wellbore_casing_and_lot',
                     pattern='P-002', decision='auto', source='reporting',
                     table='wellbore_casing_and_lot', column='wlbLotMudDencity',
                     prepare=a002_prepare, apply=a002_apply, restore=a002_restore,
                     predicate=N + 'LOTMudDensity',
                     note='the LOTMudDensity predicate must disappear from the graph'),
    'a003': Scenario(code='a003', title='RENAME COLUMN wlbDrillingOperator on wellbore_exploration_all',
                     pattern='P-003', decision='auto', source='reporting',
                     table='wellbore_exploration_all', column='wlbDrillingOperator',
                     apply=a003_apply, restore=a003_restore, predicate=N + 'drillingOperatorCompany',
                     note='join column of a cross-source TriplesMap; answers must be identical'),
    # 3 x 2 design: A001-A003 test the three operators on PostgreSQL, A004-A006 on MySQL.
    'a004': Scenario(code='a004', title='ADD COLUMN cmpCountryCode on company', pattern='P-001',
                     decision='hitl', source='registry', table='company', column='cmpCountryCode',
                     apply=a004_apply, restore=a004_restore, predicate=N + 'cmpCountryCode',
                     note='3 rows of the new column are filled after the change',
                     fill_data=a004_fill_data),
    'a005': Scenario(code='a005', title='DROP COLUMN dscDiscoveryYear on discovery',
                     pattern='P-002', decision='auto', source='registry', table='discovery',
                     column='dscDiscoveryYear', prepare=a005_prepare, apply=a005_apply,
                     restore=a005_restore, predicate=N + 'discoveryYear',
                     note='the discoveryYear predicate must disappear from the graph'),
    'a006': Scenario(code='a006', title='RENAME COLUMN cmpLongName on company',
                     pattern='P-003', decision='auto', source='registry', table='company',
                     column='cmpLongName', apply=a006_apply, restore=a006_restore,
                     predicate=N + 'name',
                     note='company names and the target of the wells -> company join'),
    'a007': Scenario(code='a007', title='DROP COLUMN wlbMudViscosityAtMD through view v_wellbore_mud',
                     pattern='P-002', decision='auto', source='reporting', table='wellbore_mud',
                     column='wlbMudViscosityAtMD', prepare=a007_prepare, apply=a007_apply,
                     restore=a007_restore, view='services.v_wellbore_mud',
                     predicate=N + 'mudViscosity',
                     note='pass-through view: the view projection shrinks (ALTER VIEW)'),
    'a008': Scenario(code='a008', title='DROP COLUMN prfInvestmentsMillNOK through view v_field_investment',
                     pattern='P-002', decision='auto', source='registry',
                     table='field_investment_yearly', column='prfInvestmentsMillNOK',
                     prepare=a008_prepare, apply=a008_apply, restore=a008_restore,
                     view='services.v_field_investment', predicate=N + 'investmentNOK',
                     note='aliased pass-through view; the mapping runs SELECT * over the view'),
    'a009': Scenario(code='a009', title='DROP COLUMN wlbCoreIntervalBottom used in a view expression',
                     # ADR-0024: a DROP that is not proven safe is only notified, with no action
                     pattern='P-002', decision='notify', source='reporting', table='wellbore_core',
                     column='wlbCoreIntervalBottom', prepare=a009_prepare, apply=a009_apply,
                     restore=a009_restore, view='services.v_wellbore_core_ft',
                     predicate=N + 'coreIntervalBottom',
                     reasons_contain=('expression', 'services.v_wellbore_core_ft'),
                     note='negative control: not adapted, the administrator is notified'),
}
