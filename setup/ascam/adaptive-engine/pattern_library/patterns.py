"""
ASCAM Pattern Library
======================
Katalog aturan transformasi berdasarkan taksonomi SMO
(Curino et al., 2013 — referensi [14] di proposal).

Setiap pola mendefinisikan:
  - obda_action  : aksi pada file .obda
  - ttl_action   : aksi pada file .ttl (None = tidak perlu berubah)
  - vdb_action   : aksi pada file government-vdb.xml
  - auto_mode    : AUTOMATIC atau HITL
"""

from config.settings import SQL_TO_XSD, SQL_TO_TEIID


# ─────────────────────────────────────────────────────────────
# PATTERN DEFINITIONS
# ─────────────────────────────────────────────────────────────

PATTERNS = {

    # P-001: ADD COLUMN
    # Kolom baru → tambah ke OBDA target, tambah DatatypeProperty di TTL,
    # tambah kolom di CREATE FOREIGN TABLE di VDB
    # Ref: Lembo et al. (2017) — setiap atribut yang diekspos perlu
    #      representasi ontologis; Teiid perlu tahu kolom baru untuk query
    'ADD COLUMN': {
        'pattern_id'     : 'P-001',
        'obda_action'    : 'add_property',
        'ttl_action'     : 'add_datatype_property',
        'vdb_action'     : 'add_column',
        'auto_mode'      : 'AUTOMATIC',
        'semantic_impact': 'HIGH',
    },

    # P-002: DROP COLUMN
    # Kolom hilang → hapus dari OBDA target (mencegah broken mapping),
    # deprecate di TTL (jaga backward compatibility SPARQL query),
    # hapus dari CREATE FOREIGN TABLE di VDB
    # Ref: Velegrakis et al. (2004) — deprecate lebih aman dari hard delete
    'DROP COLUMN': {
        'pattern_id'     : 'P-002',
        'obda_action'    : 'remove_property',
        'ttl_action'     : 'deprecate_property',
        'vdb_action'     : 'drop_column',
        'auto_mode'      : 'AUTOMATIC',
        'semantic_impact': 'HIGH',
    },

    # P-003: RENAME COLUMN
    # Nama fisik berubah → konsep TIDAK berubah (semantic preservation)
    # Cukup tambah SQL alias di OBDA source, TTL tidak perlu berubah,
    # rename kolom di CREATE FOREIGN TABLE di VDB
    # Ref: Xiao et al. (2019) — keunggulan VKG: perubahan fisik terisolasi
    #      di mapping layer, query SPARQL tetap valid
    'RENAME COLUMN': {
        'pattern_id'     : 'P-003',
        'obda_action'    : 'alias_column',
        'ttl_action'     : None,
        'vdb_action'     : 'rename_column',
        'auto_mode'      : 'AUTOMATIC',
        'semantic_impact': 'LOW',
    },
}


def get_pattern(alter_type: str) -> dict | None:
    """Kembalikan pola berdasarkan jenis DDL. None jika tidak dikenal."""
    return PATTERNS.get(alter_type.upper().strip())


# ─────────────────────────────────────────────────────────────
# KONVERSI TIPE
# ─────────────────────────────────────────────────────────────

def sql_type_to_xsd(sql_type: str) -> str:
    """'VARCHAR(100)' → 'xsd:string'"""
    base = sql_type.lower().split('(')[0].strip()
    return SQL_TO_XSD.get(base, 'xsd:string')


def sql_type_to_teiid(sql_type: str) -> str:
    """'VARCHAR(100)' → 'string'  (tipe untuk VDB DDL Teiid)"""
    base = sql_type.lower().split('(')[0].strip()
    return SQL_TO_TEIID.get(base, 'string')


def column_to_property(col_name: str) -> str:
    """
    snake_case → camelCase untuk nama property ontologi.
    'tanggal_lahir' → 'tanggalLahir'
    """
    parts = col_name.lower().split('_')
    return parts[0] + ''.join(w.capitalize() for w in parts[1:])