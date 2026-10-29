"""
ASCAM evaluation scenarios: the schema change applied to a source and how to undo it.
Every scenario must give the same result when it is run again, so the values of a column
that is dropped are copied first and written back after the run.
"""
import subprocess
from dataclasses import dataclass
from typing import Callable

PG = ['docker', 'exec', 'datasources-pgsql', 'psql', '-U', 'postgres', '-d', 'kemensos', '-tAc']
MY = ['docker', 'exec', 'datasources-mysql', 'mysql', '-umysql', '-pmysql', '-N', '-B',
      'dukcapil', '-e']


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


# ── A001: new column on the PostgreSQL source ─────────────────────────────────
def a001_apply() -> str:
    return pg('ALTER TABLE public.penerima_manfaat ADD COLUMN email VARCHAR(100)')


def a001_fill_data() -> str:
    return pg("UPDATE public.penerima_manfaat SET email = 'penerima' || penerima_id || '@contoh.id' "
              "WHERE penerima_id <= 3")


def a001_restore() -> str:
    exists = pg("SELECT count(*) FROM information_schema.columns "
                "WHERE table_name='penerima_manfaat' AND column_name='email'")
    if exists != '1':
        return 'clean'
    return 'restored: ' + pg('ALTER TABLE public.penerima_manfaat DROP COLUMN email')


# ── A002: column dropped on the PostgreSQL source ─────────────────────────────
def a002_prepare() -> str:
    """Copies the column values so that they can be written back after the run."""
    return pg('CREATE TABLE IF NOT EXISTS public.ascam_backup_tipe_program AS '
              'SELECT program_id, tipe_program FROM public.program_bansos')


def a002_apply() -> str:
    return pg('ALTER TABLE public.program_bansos DROP COLUMN tipe_program')


def a002_restore() -> str:
    exists = pg("SELECT count(*) FROM information_schema.columns "
                "WHERE table_name='program_bansos' AND column_name='tipe_program'")
    if exists != '0':
        # The column is there, so no DDL runs and the harness must not wait for a restore event.
        # This function used to return the row count every time, and the harness waited for an
        # event that never came (evaluation 20260924T071746 stopped at B002 run 1).
        return 'clean'
    pg('ALTER TABLE public.program_bansos ADD COLUMN tipe_program VARCHAR(100)')
    pg('UPDATE public.program_bansos p SET tipe_program = c.tipe_program '
       'FROM public.ascam_backup_tipe_program c WHERE c.program_id = p.program_id')
    return 'restored: ' + pg("SELECT count(*) FROM public.program_bansos WHERE tipe_program IS NOT NULL")


# ── A003: column renamed on the PostgreSQL source (explicit projection) ───────
def a003_apply() -> str:
    return pg('ALTER TABLE public.program_bansos RENAME COLUMN nama_program TO judul_program')


def a003_restore() -> str:
    exists = pg("SELECT count(*) FROM information_schema.columns "
                "WHERE table_name='program_bansos' AND column_name='judul_program'")
    if exists != '1':
        return 'clean'
    return 'restored: ' + pg('ALTER TABLE public.program_bansos RENAME COLUMN judul_program TO nama_program')


# ── A004: new column on the MySQL source ──────────────────────────────────────
def a004_apply() -> str:
    return my('ALTER TABLE master_wilayah ADD COLUMN kode_pos VARCHAR(10)')


def a004_fill_data() -> str:
    return my("UPDATE master_wilayah SET kode_pos = CONCAT('9022', wilayah_id) WHERE wilayah_id <= 3")


def a004_restore() -> str:
    exists = my("SELECT count(*) FROM information_schema.columns WHERE table_schema='dukcapil' "
                "AND table_name='master_wilayah' AND column_name='kode_pos'")
    if exists.strip() != '1':
        return 'clean'
    return 'restored: ' + my('ALTER TABLE master_wilayah DROP COLUMN kode_pos')


# ── A005: column dropped on the MySQL source (logical table SELECT *) ─────────
def a005_prepare() -> str:
    """Copies the column values so that they can be written back after the run."""
    exists = my("SELECT count(*) FROM information_schema.columns WHERE table_schema='dukcapil' "
                "AND table_name='master_penduduk' AND column_name='status_hidup'")
    if exists.strip() != '1':
        return 'column not restored yet; backup not recreated'
    return my('CREATE TABLE IF NOT EXISTS ascam_backup_status_hidup AS '
              'SELECT nik, status_hidup FROM master_penduduk')


def a005_apply() -> str:
    return my('ALTER TABLE master_penduduk DROP COLUMN status_hidup')


def a005_restore() -> str:
    exists = my("SELECT count(*) FROM information_schema.columns WHERE table_schema='dukcapil' "
                "AND table_name='master_penduduk' AND column_name='status_hidup'")
    if exists.strip() != '0':
        return 'clean'
    my('ALTER TABLE master_penduduk ADD COLUMN status_hidup VARCHAR(20)')
    my('UPDATE master_penduduk p JOIN ascam_backup_status_hidup c ON c.nik = p.nik '
       'SET p.status_hidup = c.status_hidup')
    return 'restored: ' + my('SELECT count(*) FROM master_penduduk WHERE status_hidup IS NOT NULL')


# ── A006: column renamed on the MySQL source ──────────────────────────────────
def a006_apply() -> str:
    return my('ALTER TABLE master_penduduk RENAME COLUMN tanggal_lahir TO tgl_lahir_ktp')


def a006_restore() -> str:
    exists = my("SELECT count(*) FROM information_schema.columns WHERE table_schema='dukcapil' "
                "AND table_name='master_penduduk' AND column_name='tgl_lahir_ktp'")
    if exists.strip() == '1':
        # The MySQL client prints nothing when ALTER succeeds; without a marker the empty output
        # reads as "nothing changed" and the harness does not wait for the restore event
        # (runs 1 and 2 of this scenario, then numbered A003, evaluation 20260924T072348).
        return 'restored: ' + my('ALTER TABLE master_penduduk RENAME COLUMN tgl_lahir_ktp TO tanggal_lahir')
    return 'clean'


# ── A007-A009: columns the mapping reads THROUGH a Teiid view (service model, ADR-0023) ──
def _pg_exists(table: str, column: str) -> bool:
    return pg("SELECT count(*) FROM information_schema.columns WHERE table_schema='public' "
              f"AND table_name='{table}' AND column_name='{column}'") == '1'


def _my_exists(table: str, column: str) -> bool:
    return my("SELECT count(*) FROM information_schema.columns WHERE table_schema='dukcapil' "
              f"AND table_name='{table}' AND column_name='{column}'").strip() == '1'


# A007: pass-through view on PostgreSQL; no other TriplesMap reads the column
def a007_prepare() -> str:
    if not _pg_exists('penerima_manfaat', 'no_kartu_keluarga'):
        return 'column not restored yet; backup not recreated'
    return pg('CREATE TABLE IF NOT EXISTS public.ascam_backup_no_kk AS '
              'SELECT penerima_id, no_kartu_keluarga FROM public.penerima_manfaat')


def a007_apply() -> str:
    return pg('ALTER TABLE public.penerima_manfaat DROP COLUMN no_kartu_keluarga')


def a007_restore() -> str:
    if _pg_exists('penerima_manfaat', 'no_kartu_keluarga'):
        return 'clean'
    pg('ALTER TABLE public.penerima_manfaat ADD COLUMN no_kartu_keluarga CHAR(16)')
    pg('UPDATE public.penerima_manfaat p SET no_kartu_keluarga = c.no_kartu_keluarga '
       'FROM public.ascam_backup_no_kk c WHERE c.penerima_id = p.penerima_id')
    return 'restored: ' + pg('SELECT count(*) FROM public.penerima_manfaat '
                             'WHERE no_kartu_keluarga IS NOT NULL')


# A008: view with an alias (diubah_oleh AS petugas) on MySQL; the mapping runs SELECT * over the view
def a008_prepare() -> str:
    if not _my_exists('riwayat_perubahan_data', 'diubah_oleh'):
        return 'column not restored yet; backup not recreated'
    return my('CREATE TABLE IF NOT EXISTS ascam_backup_diubah_oleh AS '
              'SELECT riwayat_id, diubah_oleh FROM riwayat_perubahan_data')


def a008_apply() -> str:
    return my('ALTER TABLE riwayat_perubahan_data DROP COLUMN diubah_oleh')


def a008_restore() -> str:
    if _my_exists('riwayat_perubahan_data', 'diubah_oleh'):
        return 'clean'
    my('ALTER TABLE riwayat_perubahan_data ADD COLUMN diubah_oleh VARCHAR(100)')
    my('UPDATE riwayat_perubahan_data r JOIN ascam_backup_diubah_oleh c '
       'ON c.riwayat_id = r.riwayat_id SET r.diubah_oleh = c.diubah_oleh')
    return 'restored: ' + my('SELECT count(*) FROM riwayat_perubahan_data WHERE diubah_oleh IS NOT NULL')


# A009: the column is used inside a view expression (YEAR(periode_selesai)); negative control
def a009_prepare() -> str:
    if not _pg_exists('program_bansos', 'periode_selesai'):
        return 'column not restored yet; backup not recreated'
    return pg('CREATE TABLE IF NOT EXISTS public.ascam_backup_periode_selesai AS '
              'SELECT program_id, periode_selesai FROM public.program_bansos')


def a009_apply() -> str:
    return pg('ALTER TABLE public.program_bansos DROP COLUMN periode_selesai')


def a009_restore() -> str:
    if _pg_exists('program_bansos', 'periode_selesai'):
        return 'clean'
    pg('ALTER TABLE public.program_bansos ADD COLUMN periode_selesai DATE')
    pg('UPDATE public.program_bansos p SET periode_selesai = c.periode_selesai '
       'FROM public.ascam_backup_periode_selesai c WHERE c.program_id = p.program_id')
    return 'restored: ' + pg('SELECT count(*) FROM public.program_bansos '
                             'WHERE periode_selesai IS NOT NULL')


SCENARIOS = {
    'a001': Scenario(code='a001', title='ADD COLUMN email on penerima_manfaat', pattern='P-001',
                     # ADR-0021: ADD needs the administrator's approval
                     decision='hitl', source='kemensos', table='penerima_manfaat', column='email',
                     apply=a001_apply, restore=a001_restore,
                     predicate='http://bansos.go.id/ontology/email',
                     note='3 rows of the new column are filled after the change',
                     fill_data=a001_fill_data),
    'a002': Scenario(code='a002', title='DROP COLUMN tipe_program on program_bansos',
                     pattern='P-002', decision='auto', source='kemensos', table='program_bansos',
                     column='tipe_program', prepare=a002_prepare, apply=a002_apply,
                     restore=a002_restore,
                     predicate='http://bansos.go.id/ontology/tipeProgram',
                     note='the tipeProgram predicate must disappear from the graph'),
    'a003': Scenario(code='a003', title='RENAME COLUMN nama_program on program_bansos',
                     pattern='P-003', decision='auto', source='kemensos', table='program_bansos',
                     column='nama_program', apply=a003_apply, restore=a003_restore,
                     predicate='http://bansos.go.id/ontology/namaProgram',
                     note='the explicit projection names the column; answers must be identical'),
    # 3 x 2 design: A001-A003 test the three operators on PostgreSQL, A004-A006 on MySQL.
    'a004': Scenario(code='a004', title='ADD COLUMN kode_pos on master_wilayah', pattern='P-001',
                     decision='hitl', source='dukcapil', table='master_wilayah', column='kode_pos',
                     apply=a004_apply, restore=a004_restore,
                     predicate='http://bansos.go.id/ontology/kodePos',
                     note='3 rows of the new column are filled after the change', fill_data=a004_fill_data),
    'a005': Scenario(code='a005', title='DROP COLUMN status_hidup on master_penduduk',
                     pattern='P-002', decision='auto', source='dukcapil', table='master_penduduk',
                     column='status_hidup', prepare=a005_prepare, apply=a005_apply,
                     restore=a005_restore,
                     predicate='http://bansos.go.id/ontology/statusHidup',
                     note='logical table SELECT *: no projection is rewritten'),
    'a006': Scenario(code='a006', title='RENAME COLUMN tanggal_lahir on master_penduduk',
                     pattern='P-003', decision='auto', source='dukcapil', table='master_penduduk',
                     column='tanggal_lahir', apply=a006_apply, restore=a006_restore,
                     predicate='http://bansos.go.id/ontology/tanggalLahir',
                     note='answers must be identical to those before the adaptation'),
    'a007': Scenario(code='a007', title='DROP COLUMN no_kartu_keluarga through view v_penerima_aktif',
                     pattern='P-002', decision='auto', source='kemensos', table='penerima_manfaat',
                     column='no_kartu_keluarga', prepare=a007_prepare, apply=a007_apply,
                     restore=a007_restore, view='layanan.v_penerima_aktif',
                     predicate='http://bansos.go.id/ontology/noKartuKeluarga',
                     note='pass-through view: the view projection shrinks (ALTER VIEW)'),
    'a008': Scenario(code='a008', title='DROP COLUMN diubah_oleh through view v_riwayat_perubahan',
                     pattern='P-002', decision='auto', source='dukcapil', table='riwayat_perubahan_data',
                     column='diubah_oleh', prepare=a008_prepare, apply=a008_apply,
                     restore=a008_restore, view='layanan.v_riwayat_perubahan',
                     predicate='http://bansos.go.id/ontology/diubahOleh',
                     note='aliased pass-through view; the mapping runs SELECT * over the view'),
    'a009': Scenario(code='a009', title='DROP COLUMN periode_selesai used in a view expression',
                     # ADR-0024: a DROP that is not proven safe is only notified, with no action
                     pattern='P-002', decision='notify', source='kemensos', table='program_bansos',
                     column='periode_selesai', prepare=a009_prepare, apply=a009_apply,
                     restore=a009_restore, view='layanan.v_program_berakhir',
                     predicate='http://bansos.go.id/ontology/tahunBerakhir',
                     reasons_contain=('expression', 'layanan.v_program_berakhir'),
                     note='negative control: not adapted, the administrator is notified'),
}
