"""
ASCAM OBDA Updater
==================
Membaca, memodifikasi, dan menulis kembali file mapping.obda Ontop.

Catatan penting tentang RENAME COLUMN dan SELECT *:
────────────────────────────────────────────────────
Ontop melakukan dua langkah saat validasi mapping:
  1. Tanya Teiid (via JDBC): kolom apa yang ada di tabel ini?
     → Teiid mengembalikan skema AKTUAL dari data source
  2. Cocokkan setiap variabel binding {x} di target dengan kolom
     yang dikembalikan Teiid

Masalah dengan "SELECT *, new_col AS old_col":
  - SELECT * di-expand Teiid menjadi daftar kolom aktual (sudah pakai nama baru)
  - Kolom hasil rename (new_col) muncul DUA KALI: sekali dari *, sekali dari alias
  - Ontop bingung dan throw InvalidMappingSourceQueriesException

Solusi: untuk RENAME COLUMN, SELECT * harus di-expand menjadi daftar kolom
eksplisit, di mana kolom yang di-rename ditulis sebagai "new_col AS old_col"
dan kolom lain tetap dengan nama aslinya.

Untuk itu, alias_column() menerima parameter `all_columns` yang berisi
daftar kolom SEBELUM rename (diambil dari VDB file oleh caller di main.py).
"""

import re
import shutil
import logging
from pathlib import Path

from config.settings import ONTOLOGY_PREFIX

log = logging.getLogger('ascam.obda')

_BLOCK_RE = re.compile(
    r'(mappingId\s+(\S+)\s*\n'
    r'target\s+(.*?)\s*\n'
    r'source\s+(.*?))'
    r'(?=\s*\n\s*mappingId|\s*\n\s*\]\])',
    re.DOTALL,
)


class OBDAUpdater:

    def __init__(self, path: str):
        self.path   = Path(path)
        self._raw   = ''
        self._blocks: dict[str, dict] = {}
        self._load()

    def _load(self):
        self._raw = self.path.read_text(encoding='utf-8')
        self._blocks = {}
        for m in _BLOCK_RE.finditer(self._raw):
            mid = m.group(2).strip()
            self._blocks[mid] = {
                'target': m.group(3).strip(),
                'source': m.group(4).strip(),
            }
        log.info('[OBDA] Loaded %d mappings dari %s', len(self._blocks), self.path.name)

    # ── P-003: RENAME COLUMN ─────────────────────────────────
    def alias_column(self, mapping_id: str, old_col: str, new_col: str,
                     all_columns: list[str] | None = None) -> bool:
        """
        Update source query untuk RENAME COLUMN.

        Strategi bergantung pada bentuk source query:

        CASE A — SELECT * (paling umum):
            Harus di-expand menjadi kolom eksplisit karena Ontop tidak bisa
            resolve alias di atas SELECT *.

            Membutuhkan `all_columns` (daftar kolom SEBELUM rename, dari VDB).
            Hasil: SELECT nik, no_kk, ..., new_col AS old_col, ... FROM tabel

            Jika all_columns tidak tersedia, fallback ke CASE B.

        CASE B — SELECT dengan kolom eksplisit:
            Ganti nama kolom lama dengan alias langsung.
            Hasil: SELECT nik, no_kk, new_col AS old_col, ... FROM tabel

        Target binding {old_col} TIDAK berubah di kedua case —
        semantic preservation (Xiao et al., 2019).
        """
        blk = self._blocks.get(mapping_id)
        if blk is None:
            log.warning('[OBDA] mapping_id %s tidak ditemukan', mapping_id)
            return False

        src = blk['source']
        uses_star = bool(re.search(r'SELECT\s+\*', src, re.IGNORECASE))

        if uses_star and all_columns:
            # ── CASE A: expand SELECT * menjadi kolom eksplisit ──
            # Bangun daftar kolom: kolom yang di-rename pakai alias,
            # kolom lain tetap dengan nama aslinya
            col_list = []
            for col in all_columns:
                if col.lower() == old_col.lower():
                    col_list.append(f'{new_col} AS {old_col}')
                else:
                    col_list.append(col)

            select_clause = ', '.join(col_list)

            # Ekstrak klausa FROM dan seterusnya dari source query
            from_match = re.search(r'(FROM\s+.*)', src, re.IGNORECASE | re.DOTALL)
            if not from_match:
                log.warning('[OBDA] Tidak bisa ekstrak FROM clause dari: %s', src[:100])
                return False

            blk['source'] = f'SELECT {select_clause} {from_match.group(1)}'
            log.info(
                '[OBDA][P-003] expand SELECT * → eksplisit, '
                'alias %s AS %s di %s', new_col, old_col, mapping_id
            )

        elif uses_star and not all_columns:
            # ── Fallback: tidak ada info kolom, pakai subquery ───
            # Bungkus SELECT * dalam subquery sehingga alias terdefinisi
            # dengan jelas tanpa konflik dengan kolom yang di-expand
            from_match = re.search(r'FROM\s+(\S+)', src, re.IGNORECASE)
            if not from_match:
                log.warning('[OBDA] Tidak bisa ekstrak nama tabel dari: %s', src[:100])
                return False

            table_ref  = from_match.group(1)
            blk['source'] = (
                f'SELECT sub.*, sub.{new_col} AS {old_col} '
                f'FROM (SELECT * FROM {table_ref}) AS sub'
            )
            log.warning(
                '[OBDA][P-003] all_columns tidak tersedia, pakai subquery fallback '
                'untuk %s di %s. Disarankan berikan all_columns untuk hasil optimal.',
                mapping_id, old_col
            )

        else:
            # ── CASE B: SELECT dengan kolom eksplisit ────────────
            pattern = rf'\b{re.escape(old_col)}\b(?!\s+AS\b)'
            new_src = re.sub(
                pattern, f'{new_col} AS {old_col}', src, flags=re.IGNORECASE
            )
            if new_src == src:
                log.warning(
                    '[OBDA] Kolom %s tidak ditemukan di source %s',
                    old_col, mapping_id
                )
                return False
            blk['source'] = new_src
            log.info(
                '[OBDA][P-003] replace %s → %s AS %s di %s (eksplisit select)',
                old_col, new_col, old_col, mapping_id
            )

        return True

    # ── P-002: DROP COLUMN → hapus property dari target ──────
    def remove_property(self, mapping_id: str, property_name: str,
                        col_name: str) -> bool:
        """Hapus satu property dari bagian target mapping."""
        blk = self._blocks.get(mapping_id)
        if blk is None:
            log.warning('[OBDA] mapping_id %s tidak ditemukan', mapping_id)
            return False

        prop_clean = property_name.replace(f'{ONTOLOGY_PREFIX}:', '')

        pattern = re.compile(
            rf';\s*{re.escape(ONTOLOGY_PREFIX)}:{re.escape(prop_clean)}'
            rf'\s+\{{[^}}]+\}}(?:\^\^[^\s;.]+)?\s*',
            re.DOTALL,
        )
        new_target = pattern.sub('', blk['target'])

        # Fallback: match by binding variable
        if new_target == blk['target']:
            fallback = re.compile(
                rf';\s*{re.escape(ONTOLOGY_PREFIX)}:\w+\s+'
                rf'\{{{re.escape(col_name)}\}}(?:\^\^[^\s;.]+)?\s*',
                re.DOTALL,
            )
            new_target = fallback.sub('', blk['target'])

        if new_target == blk['target']:
            log.warning(
                '[OBDA] Property %s tidak ditemukan di %s',
                property_name, mapping_id
            )
            return False

        blk['target'] = _fix_target(new_target)
        log.info('[OBDA][P-002] hapus property %s dari %s', property_name, mapping_id)
        return True

    # ── P-001: ADD COLUMN → tambah property ke target ────────
    def add_property(self, mapping_id: str, col_name: str,
                     property_name: str, xsd_type: str = 'xsd:string') -> bool:
        """Tambahkan property baru ke akhir target mapping."""
        blk = self._blocks.get(mapping_id)
        if blk is None:
            log.warning('[OBDA] mapping_id %s tidak ditemukan', mapping_id)
            return False

        prop_clean = property_name.replace(f'{ONTOLOGY_PREFIX}:', '')
        if re.search(
            rf'{re.escape(ONTOLOGY_PREFIX)}:{re.escape(prop_clean)}',
            blk['target']
        ):
            log.info('[OBDA] Property %s sudah ada, skip', property_name)
            return False

        stripped      = blk['target'].rstrip().rstrip('.')
        blk['target'] = f'{stripped} ; {property_name} {{{col_name}}}^^{xsd_type} .'
        log.info('[OBDA][P-001] tambah property %s ke %s', property_name, mapping_id)
        return True

    # ── Save ─────────────────────────────────────────────────
    def save(self):
        backup = self.path.with_suffix('.obda.bak')
        shutil.copy2(self.path, backup)

        header_match = re.match(
            r'(.*?\[MappingDeclaration\]\s*@collection\s*\[\[)',
            self._raw, re.DOTALL,
        )
        if not header_match:
            raise ValueError('Format .obda tidak valid')

        header = header_match.group(1)
        blocks = [
            f'mappingId    {mid}\n'
            f'target       {blk["target"]}\n'
            f'source       {blk["source"]}'
            for mid, blk in self._blocks.items()
        ]
        content = header + '\n\n' + '\n\n'.join(blocks) + '\n\n]]'
        self.path.write_text(content, encoding='utf-8')
        log.info('[OBDA] Disimpan: %s (backup: %s)', self.path.name, backup.name)


# ── helper ────────────────────────────────────────────────────
def _fix_target(target: str) -> str:
    target = re.sub(r';\s*\.', '.', target)
    target = re.sub(r';\s*;', ';', target)
    target = target.rstrip()
    if not target.endswith('.'):
        target = target.rstrip(';').rstrip() + ' .'
    return target