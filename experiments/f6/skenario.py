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
    # Tindakan evaluator bila rencana menunggu persetujuan: True = disetujui (A001, A004),
    # False = ditolak karena rencana tidak lengkap dan view harus didefinisikan ulang manusia (A009)
    setujui: bool = True
    # Potongan teks yang harus muncul pada alasan HITL (memeriksa alasan, bukan hanya keputusan)
    alasan_memuat: tuple[str, ...] = ()
    view: str | None = None             # view Teiid yang dilalui kolom (A007-A009)


# ── A001: kolom baru pada sumber PostgreSQL ────────────────────────────────────
def a001_terapkan() -> str:
    return pg('ALTER TABLE public.penerima_manfaat ADD COLUMN email VARCHAR(100)')


def a001_isi_data() -> str:
    return pg("UPDATE public.penerima_manfaat SET email = 'penerima' || penerima_id || '@contoh.id' "
              "WHERE penerima_id <= 3")


def a001_pulihkan() -> str:
    ada = pg("SELECT count(*) FROM information_schema.columns "
             "WHERE table_name='penerima_manfaat' AND column_name='email'")
    if ada != '1':
        return 'bersih'
    return 'dipulihkan: ' + pg('ALTER TABLE public.penerima_manfaat DROP COLUMN email')


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
    if ada != '0':
        # Kolom sudah ada: tidak ada DDL, sehingga harness tidak boleh menunggu event pemulihan.
        # Sebelumnya fungsi ini selalu mengembalikan jumlah baris, dan harness menunggu event
        # yang tidak pernah datang (evaluasi 20260924T071746 berhenti di B002 run 1).
        return 'bersih'
    pg('ALTER TABLE public.program_bansos ADD COLUMN tipe_program VARCHAR(100)')
    pg('UPDATE public.program_bansos p SET tipe_program = c.tipe_program '
       'FROM public.ascam_cadangan_tipe_program c WHERE c.program_id = p.program_id')
    return 'dipulihkan: ' + pg("SELECT count(*) FROM public.program_bansos WHERE tipe_program IS NOT NULL")


# ── A003: kolom diganti nama pada sumber MySQL ─────────────────────────────────
def a003_terapkan() -> str:
    return my('ALTER TABLE master_penduduk RENAME COLUMN tanggal_lahir TO tgl_lahir_ktp')


def a003_pulihkan() -> str:
    ada = my("SELECT count(*) FROM information_schema.columns WHERE table_schema='dukcapil' "
             "AND table_name='master_penduduk' AND column_name='tgl_lahir_ktp'")
    if ada.strip() == '1':
        # Klien MySQL tidak mencetak apa pun saat ALTER berhasil; tanpa penanda, keluaran kosong
        # terbaca sebagai "tidak ada perubahan" dan harness tidak menunggu event pemulihan
        # (A003 run 1 dan 2, evaluasi 20260924T072348).
        return 'dipulihkan: ' + my('ALTER TABLE master_penduduk RENAME COLUMN tgl_lahir_ktp TO tanggal_lahir')
    return 'bersih'


# ── A004: kolom baru pada sumber MySQL ─────────────────────────────────────────
def a004_terapkan() -> str:
    return my('ALTER TABLE master_wilayah ADD COLUMN kode_pos VARCHAR(10)')


def a004_isi_data() -> str:
    return my("UPDATE master_wilayah SET kode_pos = CONCAT('9022', wilayah_id) WHERE wilayah_id <= 3")


def a004_pulihkan() -> str:
    ada = my("SELECT count(*) FROM information_schema.columns WHERE table_schema='dukcapil' "
             "AND table_name='master_wilayah' AND column_name='kode_pos'")
    if ada.strip() != '1':
        return 'bersih'
    return 'dipulihkan: ' + my('ALTER TABLE master_wilayah DROP COLUMN kode_pos')


# ── A005: kolom dihapus pada sumber MySQL (logical table SELECT *) ────────────
def a005_siapkan() -> str:
    """Nilai kolom disalin agar dapat dikembalikan setelah run."""
    ada = my("SELECT count(*) FROM information_schema.columns WHERE table_schema='dukcapil' "
             "AND table_name='master_penduduk' AND column_name='status_hidup'")
    if ada.strip() != '1':
        return 'kolom belum dipulihkan; cadangan tidak dibuat ulang'
    return my('CREATE TABLE IF NOT EXISTS ascam_cadangan_status_hidup AS '
              'SELECT nik, status_hidup FROM master_penduduk')


def a005_terapkan() -> str:
    return my('ALTER TABLE master_penduduk DROP COLUMN status_hidup')


def a005_pulihkan() -> str:
    ada = my("SELECT count(*) FROM information_schema.columns WHERE table_schema='dukcapil' "
             "AND table_name='master_penduduk' AND column_name='status_hidup'")
    if ada.strip() != '0':
        return 'bersih'
    my('ALTER TABLE master_penduduk ADD COLUMN status_hidup VARCHAR(20)')
    my('UPDATE master_penduduk p JOIN ascam_cadangan_status_hidup c ON c.nik = p.nik '
       'SET p.status_hidup = c.status_hidup')
    return 'dipulihkan: ' + my('SELECT count(*) FROM master_penduduk WHERE status_hidup IS NOT NULL')


# ── A006: kolom diganti nama pada sumber PostgreSQL (proyeksi eksplisit) ──────
def a006_terapkan() -> str:
    return pg('ALTER TABLE public.program_bansos RENAME COLUMN nama_program TO judul_program')


def a006_pulihkan() -> str:
    ada = pg("SELECT count(*) FROM information_schema.columns "
             "WHERE table_name='program_bansos' AND column_name='judul_program'")
    if ada != '1':
        return 'bersih'
    return 'dipulihkan: ' + pg('ALTER TABLE public.program_bansos RENAME COLUMN judul_program TO nama_program')


# ── A007-A009: kolom yang dibaca mapping LEWAT VIEW Teiid (model layanan, ADR-0023) ──
def _pg_ada(tabel: str, kolom: str) -> bool:
    return pg("SELECT count(*) FROM information_schema.columns WHERE table_schema='public' "
              f"AND table_name='{tabel}' AND column_name='{kolom}'") == '1'


def _my_ada(tabel: str, kolom: str) -> bool:
    return my("SELECT count(*) FROM information_schema.columns WHERE table_schema='dukcapil' "
              f"AND table_name='{tabel}' AND column_name='{kolom}'").strip() == '1'


# A007: pass-through view pada PostgreSQL; kolom tidak dibaca TriplesMap lain
def a007_siapkan() -> str:
    if not _pg_ada('penerima_manfaat', 'no_kartu_keluarga'):
        return 'kolom belum dipulihkan; cadangan tidak dibuat ulang'
    return pg('CREATE TABLE IF NOT EXISTS public.ascam_cadangan_no_kk AS '
              'SELECT penerima_id, no_kartu_keluarga FROM public.penerima_manfaat')


def a007_terapkan() -> str:
    return pg('ALTER TABLE public.penerima_manfaat DROP COLUMN no_kartu_keluarga')


def a007_pulihkan() -> str:
    if _pg_ada('penerima_manfaat', 'no_kartu_keluarga'):
        return 'bersih'
    pg('ALTER TABLE public.penerima_manfaat ADD COLUMN no_kartu_keluarga CHAR(16)')
    pg('UPDATE public.penerima_manfaat p SET no_kartu_keluarga = c.no_kartu_keluarga '
       'FROM public.ascam_cadangan_no_kk c WHERE c.penerima_id = p.penerima_id')
    return 'dipulihkan: ' + pg('SELECT count(*) FROM public.penerima_manfaat '
                               'WHERE no_kartu_keluarga IS NOT NULL')


# A008: view dengan alias (diubah_oleh AS petugas) pada MySQL; mapping SELECT * atas view
def a008_siapkan() -> str:
    if not _my_ada('riwayat_perubahan_data', 'diubah_oleh'):
        return 'kolom belum dipulihkan; cadangan tidak dibuat ulang'
    return my('CREATE TABLE IF NOT EXISTS ascam_cadangan_diubah_oleh AS '
              'SELECT riwayat_id, diubah_oleh FROM riwayat_perubahan_data')


def a008_terapkan() -> str:
    return my('ALTER TABLE riwayat_perubahan_data DROP COLUMN diubah_oleh')


def a008_pulihkan() -> str:
    if _my_ada('riwayat_perubahan_data', 'diubah_oleh'):
        return 'bersih'
    my('ALTER TABLE riwayat_perubahan_data ADD COLUMN diubah_oleh VARCHAR(100)')
    my('UPDATE riwayat_perubahan_data r JOIN ascam_cadangan_diubah_oleh c '
       'ON c.riwayat_id = r.riwayat_id SET r.diubah_oleh = c.diubah_oleh')
    return 'dipulihkan: ' + my('SELECT count(*) FROM riwayat_perubahan_data WHERE diubah_oleh IS NOT NULL')


# A009: kolom dipakai di dalam ekspresi view (YEAR(periode_selesai)); kontrol negatif
def a009_siapkan() -> str:
    if not _pg_ada('program_bansos', 'periode_selesai'):
        return 'kolom belum dipulihkan; cadangan tidak dibuat ulang'
    return pg('CREATE TABLE IF NOT EXISTS public.ascam_cadangan_periode_selesai AS '
              'SELECT program_id, periode_selesai FROM public.program_bansos')


def a009_terapkan() -> str:
    return pg('ALTER TABLE public.program_bansos DROP COLUMN periode_selesai')


def a009_pulihkan() -> str:
    if _pg_ada('program_bansos', 'periode_selesai'):
        return 'bersih'
    pg('ALTER TABLE public.program_bansos ADD COLUMN periode_selesai DATE')
    pg('UPDATE public.program_bansos p SET periode_selesai = c.periode_selesai '
       'FROM public.ascam_cadangan_periode_selesai c WHERE c.program_id = p.program_id')
    return 'dipulihkan: ' + pg('SELECT count(*) FROM public.program_bansos '
                               'WHERE periode_selesai IS NOT NULL')


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
    # Rancangan 3 x 2: setiap operator juga diuji pada DBMS lainnya.
    'a004': Skenario(kode='a004', judul='ADD COLUMN kode_pos pada master_wilayah', pola='P-001',
                     keputusan='hitl', sumber='dukcapil', tabel='master_wilayah', kolom='kode_pos',
                     terapkan=a004_terapkan, pulihkan=a004_pulihkan,
                     predikat='http://bansos.go.id/ontology/kodePos',
                     catatan='kolom baru diisi 3 baris setelah perubahan', isi_data=a004_isi_data),
    'a005': Skenario(kode='a005', judul='DROP COLUMN status_hidup pada master_penduduk',
                     pola='P-002', keputusan='auto', sumber='dukcapil', tabel='master_penduduk',
                     kolom='status_hidup', siapkan=a005_siapkan, terapkan=a005_terapkan,
                     pulihkan=a005_pulihkan,
                     predikat='http://bansos.go.id/ontology/statusHidup',
                     catatan='logical table SELECT *: tanpa penulisan ulang proyeksi'),
    'a006': Skenario(kode='a006', judul='RENAME COLUMN nama_program pada program_bansos',
                     pola='P-003', keputusan='auto', sumber='kemensos', tabel='program_bansos',
                     kolom='nama_program', terapkan=a006_terapkan, pulihkan=a006_pulihkan,
                     predikat='http://bansos.go.id/ontology/namaProgram',
                     catatan='proyeksi eksplisit menyebut kolom; jawaban harus identik'),
    'a007': Skenario(kode='a007', judul='DROP COLUMN no_kartu_keluarga lewat view v_penerima_aktif',
                     pola='P-002', keputusan='auto', sumber='kemensos', tabel='penerima_manfaat',
                     kolom='no_kartu_keluarga', siapkan=a007_siapkan, terapkan=a007_terapkan,
                     pulihkan=a007_pulihkan, view='layanan.v_penerima_aktif',
                     predikat='http://bansos.go.id/ontology/noKartuKeluarga',
                     catatan='view pass-through: proyeksi view dikecilkan (ALTER VIEW)'),
    'a008': Skenario(kode='a008', judul='DROP COLUMN diubah_oleh lewat view v_riwayat_perubahan',
                     pola='P-002', keputusan='auto', sumber='dukcapil', tabel='riwayat_perubahan_data',
                     kolom='diubah_oleh', siapkan=a008_siapkan, terapkan=a008_terapkan,
                     pulihkan=a008_pulihkan, view='layanan.v_riwayat_perubahan',
                     predikat='http://bansos.go.id/ontology/diubahOleh',
                     catatan='view pass-through beralias; mapping SELECT * atas view'),
    'a009': Skenario(kode='a009', judul='DROP COLUMN periode_selesai yang dipakai ekspresi view',
                     pola='P-002', keputusan='hitl', sumber='kemensos', tabel='program_bansos',
                     kolom='periode_selesai', siapkan=a009_siapkan, terapkan=a009_terapkan,
                     pulihkan=a009_pulihkan, view='layanan.v_program_berakhir',
                     predikat='http://bansos.go.id/ontology/tahunBerakhir', setujui=False,
                     alasan_memuat=('ekspresi', 'layanan.v_program_berakhir'),
                     catatan='kontrol negatif: rencana tidak lengkap, evaluator menolak'),
}
