"""
ASCAM VDB Updater
==================
Memodifikasi file government-vdb.xml (Teiid Virtual Database definition)
sesuai perubahan skema yang terdeteksi.

Mengapa VDB perlu diupdate?
───────────────────────────
Teiid menggunakan definisi `CREATE FOREIGN TABLE` di dalam VDB untuk
mengetahui skema tabel di setiap data source. Ini adalah metadata layer
Teiid — berbeda dari mapping Ontop (.obda) yang ada di atasnya.

Jika kolom berubah di data source tapi VDB tidak diupdate:
  - ADD COLUMN   → Teiid tidak tahu kolom baru, query federation yang
                   menyentuh kolom itu akan error "column not found"
  - DROP COLUMN  → Teiid masih mencoba query kolom yang sudah tidak ada,
                   menyebabkan SQL error dari data source
  - RENAME COLUMN→ Teiid memakai nama lama, query gagal di data source

Struktur government-vdb.xml yang ditangani:
───────────────────────────────────────────
    <vdb name="government" version="1">
      <model name="dukcapil">
        <source ... />
        <metadata type="DDL"><![CDATA[
          CREATE FOREIGN TABLE master_penduduk (
            nik  varchar(16) not null primary key,
            nama varchar(100) not null,
            ...
          ) OPTIONS(UPDATABLE 'FALSE');
        ]]></metadata>
      </model>

      <model name="kemensos">
        ...
      </model>
    </vdb>

Strategi parsing:
─────────────────
VDB XML menggunakan CDATA section untuk DDL. Library xml.etree.ElementTree
Python tidak preserve CDATA secara native — jika kita parse dan re-serialize
dengan ET, CDATA akan menjadi escaped text biasa dan format file berubah.

Strategi yang dipakai: manipulasi teks regex langsung pada konten CDATA,
bukan via DOM. Ini lebih aman untuk format file ini karena:
  1. Format CDATA di VDB konsisten dan predictable
  2. Menghindari perubahan formatting yang bisa merusak deployment Teiid
  3. Operasi yang dilakukan (add/drop/rename satu kolom) cukup sederhana
     untuk regex yang tepat
"""

import re
import shutil
import logging
from pathlib import Path

from pattern_library.patterns import sql_type_to_teiid

log = logging.getLogger('ascam.vdb')


class VDBUpdater:

    def __init__(self, path: str):
        self.path     = Path(path)
        self._content = ''
        self._load()

    def _load(self):
        if not self.path.exists():
            raise FileNotFoundError(f'VDB file tidak ditemukan: {self.path}')
        self._content = self.path.read_text(encoding='utf-8')
        log.info('[VDB] Loaded: %s', self.path.name)

    # ─────────────────────────────────────────────────────────
    # P-001: ADD COLUMN → tambah kolom di FOREIGN TABLE
    # ─────────────────────────────────────────────────────────
    def add_column(self, model_name: str, table_name: str,
                   col_name: str, sql_type: str) -> bool:
        """
        Tambahkan definisi kolom baru ke CREATE FOREIGN TABLE.

        Strategi: temukan penutup ')' dari tabel yang dimaksud di
        dalam model yang tepat, lalu sisipkan sebelum tanda ')'.

        Kolom baru ditambahkan tanpa NOT NULL dan tanpa constraint
        karena kita tidak tahu apakah kolom nullable — operator bisa
        sesuaikan manual jika perlu.

        Contoh hasil:
            ...
            status_hidup varchar(20) not null,
            email string           ← baris baru
          ) OPTIONS(UPDATABLE 'FALSE');
        """
        teiid_type = sql_type_to_teiid(sql_type)

        # Cari blok CDATA milik model yang tepat
        cdata, start, end = self._get_cdata(model_name)
        if cdata is None:
            log.warning('[VDB] Model %s tidak ditemukan', model_name)
            return False

        # Idempotent: cek apakah kolom sudah ada
        col_pattern = re.compile(
            rf'\b{re.escape(col_name)}\b',
            re.IGNORECASE,
        )
        # Cari dalam definisi tabel yang spesifik
        table_block = _get_table_block(cdata, table_name)
        if table_block and col_pattern.search(table_block):
            log.info('[VDB] Kolom %s sudah ada di %s.%s, skip', col_name, model_name, table_name)
            return False

        # Temukan titik insert: sebelum ')' penutup CREATE FOREIGN TABLE
        # yang merupakan milik tabel ini
        close_pattern = re.compile(
            rf'(CREATE\s+FOREIGN\s+TABLE\s+{re.escape(table_name)}\s*\(.*?)'
            rf'(\s*\)\s*OPTIONS)',
            re.DOTALL | re.IGNORECASE,
        )
        m = close_pattern.search(cdata)
        if not m:
            log.warning('[VDB] Tabel %s tidak ditemukan di model %s', table_name, model_name)
            return False

        # Sisipkan kolom baru sebelum penutup
        new_col_line = f'\n            {col_name} {teiid_type}'
        new_cdata = (
            cdata[:m.start(2)]
            + ','
            + new_col_line
            + cdata[m.start(2):]
        )

        self._content = self._content[:start] + new_cdata + self._content[end:]
        log.info('[VDB][P-001] tambah kolom %s %s ke %s.%s',
                 col_name, teiid_type, model_name, table_name)
        return True

    # ─────────────────────────────────────────────────────────
    # P-002: DROP COLUMN → hapus kolom dari FOREIGN TABLE
    # ─────────────────────────────────────────────────────────
    def drop_column(self, model_name: str, table_name: str, col_name: str) -> bool:
        """
        Hapus definisi kolom dari CREATE FOREIGN TABLE.

        Menangani dua posisi kolom:
          - Kolom di tengah: hapus baris + koma trailing
          - Kolom di akhir : hapus baris + koma di baris sebelumnya
        """
        cdata, start, end = self._get_cdata(model_name)
        if cdata is None:
            log.warning('[VDB] Model %s tidak ditemukan', model_name)
            return False

        # Pola: satu baris definisi kolom di dalam tabel ini
        # Contoh:   "  email string,\n"  atau  "  email string\n"
        col_line_pattern = re.compile(
            rf'[ \t]+{re.escape(col_name)}\s+\w+(?:\([^)]*\))?'
            rf'(?:\s+not\s+null)?(?:\s+primary\s+key)?,?\s*\n',
            re.IGNORECASE,
        )

        table_block = _get_table_block(cdata, table_name)
        if table_block is None:
            log.warning('[VDB] Tabel %s tidak ditemukan di model %s', table_name, model_name)
            return False

        if not col_line_pattern.search(table_block):
            log.warning('[VDB] Kolom %s tidak ditemukan di %s.%s', col_name, model_name, table_name)
            return False

        # Hapus baris kolom
        new_table_block = col_line_pattern.sub('', table_block)

        # Bersihkan koma gantung di baris terakhir sebelum ')'
        new_table_block = re.sub(r',(\s*\))', r'\1', new_table_block)

        # Ganti blok tabel lama dengan yang baru di dalam CDATA
        new_cdata = cdata.replace(table_block, new_table_block, 1)
        self._content = self._content[:start] + new_cdata + self._content[end:]

        log.info('[VDB][P-002] hapus kolom %s dari %s.%s', col_name, model_name, table_name)
        return True

    # ─────────────────────────────────────────────────────────
    # P-003: RENAME COLUMN → ubah nama di FOREIGN TABLE
    # ─────────────────────────────────────────────────────────
    def rename_column(self, model_name: str, table_name: str,
                      old_col: str, new_col: str) -> bool:
        """
        Rename definisi kolom di CREATE FOREIGN TABLE.

        Ganti nama kolom lama dengan nama baru, tipe data dipertahankan.

        Catatan penting: di Teiid, nama kolom di VDB DDL harus cocok
        dengan nama kolom yang dikembalikan oleh data source. Karena
        skema monitor MySQL menggunakan:
            ALTER TABLE ... RENAME COLUMN old TO new
        maka nama fisik di MySQL sudah berubah jadi 'new'. VDB harus
        memakai nama baru juga supaya Teiid bisa query ke MySQL.

        Di sisi Ontop (.obda), kita pakai SQL alias (new AS old) supaya
        variabel binding tetap menggunakan nama lama.
        """
        cdata, start, end = self._get_cdata(model_name)
        if cdata is None:
            log.warning('[VDB] Model %s tidak ditemukan', model_name)
            return False

        # Pola: nama kolom lama di awal baris definisi kolom
        old_pattern = re.compile(
            rf'(\b){re.escape(old_col)}(\s+\w)',
            re.IGNORECASE,
        )

        table_block = _get_table_block(cdata, table_name)
        if table_block is None:
            log.warning('[VDB] Tabel %s tidak ditemukan di model %s', table_name, model_name)
            return False

        if not old_pattern.search(table_block):
            log.warning('[VDB] Kolom %s tidak ditemukan di %s.%s', old_col, model_name, table_name)
            return False

        new_table_block = old_pattern.sub(rf'\g<1>{new_col}\2', table_block)
        new_cdata = cdata.replace(table_block, new_table_block, 1)
        self._content = self._content[:start] + new_cdata + self._content[end:]

        log.info('[VDB][P-003] rename kolom %s → %s di %s.%s',
                 old_col, new_col, model_name, table_name)
        return True


    # ── Public helper: ambil daftar kolom dari tabel ─────────
    def get_columns(self, model_name: str, table_name: str) -> list[str]:
        """
        Kembalikan daftar nama kolom dari CREATE FOREIGN TABLE di VDB.
        Panggil SEBELUM rename_column() agar daftar masih pakai nama lama.
        """
        cdata, _, _ = self._get_cdata(model_name)
        if cdata is None:
            log.warning('[VDB] Model %s tidak ditemukan untuk get_columns', model_name)
            return []
        table_block = _get_table_block(cdata, table_name)
        if table_block is None:
            log.warning('[VDB] Tabel %s tidak ditemukan untuk get_columns', table_name)
            return []
        import re as _re
        body_match = _re.search(
            r'CREATE\s+FOREIGN\s+TABLE\s+\S+\s*\((.*?)\)\s*OPTIONS',
            table_block, _re.DOTALL | _re.IGNORECASE,
        )
        if not body_match:
            return []
        columns = []
        for line in body_match.group(1).splitlines():
            line = line.strip().rstrip(',')
            if not line:
                continue
            col_name = line.split()[0].strip()
            if col_name:
                columns.append(col_name)
        log.info('[VDB] Kolom %s.%s: %s', model_name, table_name, columns)
        return columns

    # ─────────────────────────────────────────────────────────
    # Save
    # ─────────────────────────────────────────────────────────
    def save(self):
        backup = self.path.with_suffix('.xml.bak')
        shutil.copy2(self.path, backup)
        self.path.write_text(self._content, encoding='utf-8')
        log.info('[VDB] Disimpan: %s (backup: %s)', self.path.name, backup.name)

    # ─────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────
    def _get_cdata(self, model_name: str) -> tuple[str | None, int, int]:
        """
        Ekstrak konten CDATA dari model tertentu.

        Returns: (cdata_content, start_index, end_index) dalam self._content
        Indices merujuk pada posisi konten CDATA (bukan tag CDATA-nya).
        """
        # Cari blok <model name="..."> yang tepat
        model_pattern = re.compile(
            rf'<model[^>]+name="{re.escape(model_name)}"[^>]*>'
            rf'(.*?)'
            rf'</model>',
            re.DOTALL,
        )
        m = model_pattern.search(self._content)
        if not m:
            return None, -1, -1

        model_block     = m.group(1)
        model_start_pos = m.start(1)

        # Cari CDATA di dalam model block
        cdata_pattern = re.compile(r'<!\[CDATA\[(.*?)\]\]>', re.DOTALL)
        cm = cdata_pattern.search(model_block)
        if not cm:
            return None, -1, -1

        # Hitung posisi absolut dalam self._content
        abs_start = model_start_pos + cm.start(1)
        abs_end   = model_start_pos + cm.end(1)

        return cm.group(1), abs_start, abs_end


def _get_table_block(cdata: str, table_name: str) -> str | None:
    """
    Ekstrak blok CREATE FOREIGN TABLE untuk tabel tertentu dari CDATA.

    Returns string blok DDL tabel, atau None jika tidak ditemukan.
    """
    pattern = re.compile(
        rf'(CREATE\s+FOREIGN\s+TABLE\s+{re.escape(table_name)}\s*\(.*?\)\s*OPTIONS\s*\([^)]+\)\s*;)',
        re.DOTALL | re.IGNORECASE,
    )
    m = pattern.search(cdata)
    return m.group(1) if m else None