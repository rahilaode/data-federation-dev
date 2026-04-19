"""
ASCAM DDL Parser
================
Mengubah teks DDL dari Kafka menjadi dict terstruktur.

Lebih robust dari split() — menangani backtick, schema prefix,
dan variasi spasi yang dihasilkan schema monitor MySQL/PostgreSQL.
"""

import re
import logging

log = logging.getLogger('ascam.parser')


def parse_ddl(ddl_command: str) -> dict | None:
    """
    Parse teks DDL menjadi dict terstruktur.

    Menangani format yang dihasilkan schema monitor Anda:
        MySQL (dari sp_detect_ddl_changes):
            ALTER TABLE `dukcapil`.`master_penduduk` ADD COLUMN `email` VARCHAR(100)
            ALTER TABLE `dukcapil`.`master_penduduk` DROP COLUMN `penghasilan`
            ALTER TABLE `dukcapil`.`master_penduduk` RENAME COLUMN `tanggal_lahir` TO `ttl`

        PostgreSQL (dari fn_capture_alter_column):
            ALTER TABLE public.penerima_manfaat ADD COLUMN email varchar(100)
            ALTER TABLE public.penerima_manfaat DROP COLUMN aktif
            ALTER TABLE public.penerima_manfaat RENAME COLUMN tgl_lahir TO tanggal_lahir

    Returns:
        dict dengan keys:
            alter_type  : 'ADD COLUMN' | 'DROP COLUMN' | 'RENAME COLUMN'
            table_name  : nama tabel bersih (tanpa schema prefix, tanpa backtick)
            column_name : nama kolom (ADD & DROP)
            old_column  : nama lama (RENAME)
            new_column  : nama baru (RENAME)
            column_type : tipe SQL (ADD, e.g. 'VARCHAR(100)')
        None jika DDL tidak dikenali.
    """
    if not ddl_command:
        return None

    # Normalisasi whitespace
    ddl = ' '.join(ddl_command.split())

    def clean(token: str) -> str:
        """Hapus backtick dan ambil bagian setelah titik (strip schema prefix)."""
        return token.replace('`', '').replace('"', '').split('.')[-1].strip()

    # ── ADD COLUMN ───────────────────────────────────────────
    m = re.match(
        r'ALTER\s+TABLE\s+(\S+)\s+ADD\s+COLUMN\s+(\S+)\s+(\S+.*)',
        ddl, re.IGNORECASE
    )
    if m:
        return {
            'alter_type' : 'ADD COLUMN',
            'table_name' : clean(m.group(1)),
            'column_name': clean(m.group(2)),
            'column_type': m.group(3).strip(),
            'old_column' : None,
            'new_column' : None,
        }

    # ── DROP COLUMN ──────────────────────────────────────────
    m = re.match(
        r'ALTER\s+TABLE\s+(\S+)\s+DROP\s+COLUMN\s+(\S+)',
        ddl, re.IGNORECASE
    )
    if m:
        return {
            'alter_type' : 'DROP COLUMN',
            'table_name' : clean(m.group(1)),
            'column_name': clean(m.group(2)),
            'column_type': None,
            'old_column' : None,
            'new_column' : None,
        }

    # ── RENAME COLUMN ─────────────────────────────────────────
    m = re.match(
        r'ALTER\s+TABLE\s+(\S+)\s+RENAME\s+COLUMN\s+(\S+)\s+TO\s+(\S+)',
        ddl, re.IGNORECASE
    )
    if m:
        return {
            'alter_type' : 'RENAME COLUMN',
            'table_name' : clean(m.group(1)),
            'column_name': None,
            'column_type': None,
            'old_column' : clean(m.group(2)),
            'new_column' : clean(m.group(3)),
        }

    log.warning('[Parser] DDL tidak dikenali: %s', ddl[:120])
    return None


def extract_event(msg_dict: dict) -> dict | None:
    """
    Ekstrak field dari pesan Kafka format Debezium.

    Format Debezium:
        {
          "payload": {
            "before": null,
            "after": {
              "ddl_command": "ALTER TABLE ...",
              "is_regulated": 1,
              "table_name": "...",
              ...
            },
            "op": "c"
          }
        }

    Returns dict 'after', atau None untuk DELETE / tombstone event.
    """
    try:
        payload = msg_dict.get('payload', msg_dict)
        if isinstance(payload, dict):
            op    = payload.get('op', 'c')
            after = payload.get('after', payload)
            if op == 'd' or after is None:
                return None
            return after
        return msg_dict
    except Exception:
        return None