"""
Normalisasi pesan monitor skema menjadi event terformalisasi (D7).

Bentuk pesan (terbukti pada F3-probe): envelope Debezium dengan `after` berisi baris
`ddl_event_log`: `schema_name` (PostgreSQL) atau `db_name` (MySQL), `table_name`,
`alter_type`, `column_name`, `ddl_command`, `is_regulated`, `captured_at`.

Dua hal yang tidak dapat diambil langsung dari baris log:
  * nama kolom BARU pada RENAME — monitor hanya menyimpan nama lama;
  * DDL yang memuat beberapa pernyataan sekaligus — baris log hanya mencatat satu jenis.
Keduanya diselesaikan dengan menguraikan `ddl_command` memakai sqlglot. Bila penguraian gagal,
event tetap dibentuk dari baris log (RENAME tanpa nama baru akan dieskalasi Knowledge), dan
pernyataan yang tidak didukung dikirim sebagai operasi `other` agar tetap terlihat administrator.
"""
import uuid
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlglot import exp

NAMESPACE = uuid.NAMESPACE_URL
DIALECT = {'postgresql': 'postgres', 'mysql': 'mysql'}
ALTER_TYPE = {'ADD COLUMN': 'add', 'DROP COLUMN': 'drop', 'RENAME COLUMN': 'rename'}
INSERT_OPS = {'c', 'r'}                      # create dan snapshot read


@dataclass
class Normalized:
    event_uid: str
    operation: str
    source: str
    table: str
    schema: str | None = None
    column: str | None = None
    new_column: str | None = None
    column_type: str | None = None
    captured_at: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def payload(self) -> dict[str, Any]:
        return {'event_uid': self.event_uid, 'operation': self.operation, 'source': self.source,
                'schema': self.schema, 'table': self.table, 'column': self.column,
                'new_column': self.new_column, 'column_type': self.column_type,
                'captured_at': self.captured_at, 'raw': self.raw}


def _unwrap(value: Any) -> dict | None:
    if not isinstance(value, dict):
        return None
    return value.get('payload') if isinstance(value.get('payload'), dict) else value


def _table_name(node) -> tuple[str | None, str]:
    if isinstance(node, exp.Table):
        return (node.db or None), node.name
    return None, str(node)


def _actions(ddl: str, dialect: str) -> list[dict]:
    """Daftar operasi kolom pada satu teks DDL (dapat memuat beberapa pernyataan)."""
    out: list[dict] = []
    for statement in sqlglot.parse(ddl, dialect=dialect):
        if not isinstance(statement, exp.Alter):
            continue
        schema, table = _table_name(statement.this)
        for action in statement.args.get('actions') or []:
            if isinstance(action, exp.ColumnDef):
                out.append({'operation': 'add', 'schema': schema, 'table': table,
                            'column': action.name,
                            'column_type': action.args['kind'].sql(dialect=dialect)
                            if action.args.get('kind') else None})
            elif isinstance(action, exp.Drop) and (action.args.get('kind') or '').upper() == 'COLUMN':
                for column in action.args.get('tables') or []:
                    out.append({'operation': 'drop', 'schema': schema, 'table': table,
                                'column': column.name})
            elif type(action).__name__ == 'RenameColumn':
                out.append({'operation': 'rename', 'schema': schema, 'table': table,
                            'column': action.args['this'].name if hasattr(action.args['this'], 'name')
                            else str(action.args['this']),
                            'new_column': action.args['to'].name if hasattr(action.args['to'], 'name')
                            else str(action.args['to'])})
            else:
                out.append({'operation': 'other', 'schema': schema, 'table': table,
                            'column': None, 'unsupported': action.sql(dialect=dialect)})
    return out


def skip_reason(value: Any) -> str:
    """Mengapa pesan tidak menghasilkan event; dipakai agar pesan tidak dibuang tanpa jejak."""
    if value is None:
        return 'pesan kosong (tombstone)'
    if isinstance(value, (str, bytes)):
        return f'isi pesan berupa {type(value).__name__}, bukan objek JSON (kemungkinan converter berubah)'
    if not isinstance(value, dict):
        return f'isi pesan bertipe {type(value).__name__}'
    payload = _unwrap(value)
    if payload is None:
        return 'envelope tanpa payload'
    op = payload.get('op')
    if op not in INSERT_OPS:
        return f"operasi Debezium {op!r} bukan INSERT/snapshot (kunci: {sorted(payload)[:8]})"
    if not isinstance(payload.get('after'), dict):
        return 'tidak ada baris after'
    return 'tidak ada pernyataan kolom'


def normalize(topic: str, partition: int, offset: int, value: Any, source: str,
              dbms: str = 'postgresql') -> list[Normalized]:
    payload = _unwrap(value)
    if payload is None or payload.get('op') not in INSERT_OPS:
        return []
    row = payload.get('after')
    if not isinstance(row, dict):
        return []

    schema = row.get('schema_name') or row.get('db_name')
    table = row.get('table_name')
    ddl = row.get('ddl_command') or ''
    captured_at = row.get('captured_at')
    base_raw = {'topic': topic, 'partition': partition, 'offset': offset, 'after': row,
                'source_metadata': payload.get('source')}

    try:
        actions = _actions(ddl, DIALECT.get(dbms, 'postgres'))
    except Exception as exc:                    # noqa: BLE001 — DDL tidak dapat diurai
        actions = []
        base_raw['parse_error'] = f'{type(exc).__name__}: {exc}'[:300]

    if not actions:                             # cadangan: pakai baris log apa adanya
        operation = ALTER_TYPE.get((row.get('alter_type') or '').upper(), 'other')
        actions = [{'operation': operation, 'schema': schema, 'table': table,
                    'column': row.get('column_name')}]
        base_raw['fallback'] = 'dari baris log; ddl_command tidak diurai'

    # Kunci idempotensi diturunkan dari DDL itu sendiri (sumber, id baris log, dan waktu
    # tangkap), BUKAN dari offset Kafka. Offset kembali ke 0 bila topik dibuat ulang, sehingga
    # DDL baru mendapat uid yang sama dengan event lama dan dianggap duplikat (temuan F6).
    identitas = (f"{source}:{row.get('id')}:{captured_at}"
                 if row.get('id') is not None and captured_at else f'{topic}:{partition}:{offset}')
    events = []
    for index, action in enumerate(actions):
        raw = dict(base_raw)
        if 'unsupported' in action:
            raw['unsupported_statement'] = action['unsupported']
        events.append(Normalized(
            event_uid=str(uuid.uuid5(NAMESPACE, f'ascam:{identitas}:{index}')),
            operation=action['operation'], source=source,
            schema=action.get('schema') or schema, table=action.get('table') or table,
            column=action.get('column'), new_column=action.get('new_column'),
            column_type=action.get('column_type'), captured_at=captured_at, raw=raw))
    return events
