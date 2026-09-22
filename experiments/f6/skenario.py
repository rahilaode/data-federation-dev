"""
Skenario evaluasi ASCAM: perubahan skema yang diterapkan pada sumber, beserta cara
mengembalikannya. Setiap skenario harus dapat dijalankan berulang kali dengan hasil yang sama,
sehingga data pada kolom yang dihapus disalin lebih dahulu dan dikembalikan setelah run.
"""
import subprocess
from dataclasses import dataclass
from typing import Callable

PG = ['docker', 'exec', 'datasources-pgsql', 'psql', '-U', 'postgres', '-d', 'kemensos', '-tAc']
MY = ['docker', 'exec', 'datasources-mysql', 'mysql', '-umysql', '-pmysql', '-N', '-B',
      'dukcapil', '-e']


def jalankan(perintah: list[str], sql: str) -> str:
    hasil = subprocess.run([*perintah, sql], capture_output=True, text=True)
    keluaran = (hasil.stdout or '').strip()
    galat = (hasil.stderr or '').strip()
    # peringatan kata sandi pada klien MySQL bukan galat
    galat = '\n'.join(b for b in galat.splitlines() if 'password on the command line' not in b)
    if galat:
        return f'{keluaran} [{galat[:200]}]'.strip()
    return keluaran


def pg(sql: str) -> str:
    return jalankan(PG, sql)


def my(sql: str) -> str:
    return jalankan(MY, sql)


@dataclass
class Skenario:
    kode: str
    judul: str
    pola: str
    keputusan: str                      # keputusan D11 yang diharapkan
    sumber: str
    tabel: str
    kolom: str
    terapkan: Callable[[], str]
    pulihkan: Callable[[], str]
    siapkan: Callable[[], str] | None = None
    predikat: str | None = None         # predikat yang diharapkan berubah
    catatan: str = ''
    # Mengisi data pada kolom baru agar "kueri baru mengembalikan hasil" dapat diuji (A001)
    isi_data: Callable[[], str] | None = None


# ── A001: kolom baru pada sumber PostgreSQL ────────────────────────────────────
def a001_terapkan() -> str:
    return pg('ALTER TABLE public.penerima_manfaat ADD COLUMN email VARCHAR(100)')


def a001_isi_data() -> str:
    return pg("UPDATE public.penerima_manfaat SET email = 'penerima' || penerima_id || '@contoh.id' "
              "WHERE penerima_id <= 3")


def a001_pulihkan() -> str:
    ada = pg("SELECT count(*) FROM information_schema.columns "
             "WHERE table_name='penerima_manfaat' AND column_name='email'")
    return pg('ALTER TABLE public.penerima_manfaat DROP COLUMN email') if ada == '1' else 'bersih'


# ── A002: kolom dihapus pada sumber PostgreSQL ─────────────────────────────────
def a002_siapkan() -> str:
    """Nilai kolom disalin agar dapat dikembalikan setelah run."""
    return pg('CREATE TABLE IF NOT EXISTS public.ascam_cadangan_tipe_program AS '
              'SELECT program_id, tipe_program FROM public.program_bansos')


def a002_terapkan() -> str:
    return pg('ALTER TABLE public.program_bansos DROP COLUMN tipe_program')


def a002_pulihkan() -> str:
    ada = pg("SELECT count(*) FROM information_schema.columns "
             "WHERE table_name='program_bansos' AND column_name='tipe_program'")
    if ada == '0':
        pg('ALTER TABLE public.program_bansos ADD COLUMN tipe_program VARCHAR(100)')
        pg('UPDATE public.program_bansos p SET tipe_program = c.tipe_program '
           'FROM public.ascam_cadangan_tipe_program c WHERE c.program_id = p.program_id')
    return pg("SELECT count(*) FROM public.program_bansos WHERE tipe_program IS NOT NULL")


# ── A003: kolom diganti nama pada sumber MySQL ─────────────────────────────────
def a003_terapkan() -> str:
    return my('ALTER TABLE master_penduduk RENAME COLUMN tanggal_lahir TO tgl_lahir_ktp')


def a003_pulihkan() -> str:
    ada = my("SELECT count(*) FROM information_schema.columns WHERE table_schema='dukcapil' "
             "AND table_name='master_penduduk' AND column_name='tgl_lahir_ktp'")
    if ada.strip() == '1':
        return my('ALTER TABLE master_penduduk RENAME COLUMN tgl_lahir_ktp TO tanggal_lahir')
    return 'bersih'



SKENARIO = {
    'a001': Skenario(kode='a001', judul='ADD COLUMN email pada penerima_manfaat', pola='P-001',
                     # ADR-0021: ADD memerlukan persetujuan administrator
                     keputusan='hitl', sumber='kemensos', tabel='penerima_manfaat', kolom='email',
                     terapkan=a001_terapkan, pulihkan=a001_pulihkan,
                     predikat='http://bansos.go.id/ontology/email',
                     catatan='kolom baru diisi 3 baris setelah perubahan',
                     isi_data=a001_isi_data),
    'a002': Skenario(kode='a002', judul='DROP COLUMN tipe_program pada program_bansos',
                     pola='P-002', keputusan='auto', sumber='kemensos', tabel='program_bansos',
                     kolom='tipe_program', siapkan=a002_siapkan, terapkan=a002_terapkan,
                     pulihkan=a002_pulihkan,
                     predikat='http://bansos.go.id/ontology/tipeProgram',
                     catatan='predikat tipeProgram harus hilang dari graf'),
    'a003': Skenario(kode='a003', judul='RENAME COLUMN tanggal_lahir pada master_penduduk',
                     pola='P-003', keputusan='auto', sumber='dukcapil', tabel='master_penduduk',
                     kolom='tanggal_lahir', terapkan=a003_terapkan, pulihkan=a003_pulihkan,
                     predikat='http://bansos.go.id/ontology/tanggalLahir',
                     catatan='jawaban harus identik dengan sebelum adaptasi'),
}
