"""Normalisation of schema monitor messages into formalised events."""
import uuid

import pytest

from ascam_orchestrator.normalizer import normalize


def message(after: dict, op: str = 'c') -> dict:
    return {'before': None, 'after': after, 'op': op, 'ts_ms': 1789610589382,
            'source': {'connector': 'postgresql', 'name': 'kemensos'}}


PG_ROW = {'id': 7, 'username': 'postgres', 'schema_name': 'public', 'table_name': 'penerima_manfaat',
          'object_tag': 'public.penerima_manfaat', 'command_tag': 'ALTER TABLE',
          'alter_type': 'ADD COLUMN', 'column_name': 'email',
          'ddl_command': 'ALTER TABLE public.penerima_manfaat ADD COLUMN email VARCHAR(100);',
          'is_regulated': True, 'captured_at': '2026-09-18T04:00:00Z'}


def test_add_column(pg=PG_ROW):
    events = normalize('kemensos.schema_monitor.ddl_event_log', 0, 12, message(pg), 'kemensos')
    assert len(events) == 1
    e = events[0]
    assert (e.operation, e.schema, e.table, e.column, e.column_type) == (
        'add', 'public', 'penerima_manfaat', 'email', 'VARCHAR(100)')
    assert e.captured_at == '2026-09-18T04:00:00Z'
    assert e.raw['offset'] == 12 and e.raw['after']['id'] == 7


def test_drop_column():
    row = {**PG_ROW, 'alter_type': 'DROP COLUMN', 'column_name': 'tipe_program',
           'table_name': 'program_bansos',
           'ddl_command': 'ALTER TABLE public.program_bansos DROP COLUMN tipe_program;'}
    (event,) = normalize('t', 0, 1, message(row), 'kemensos')
    assert (event.operation, event.table, event.column) == ('drop', 'program_bansos', 'tipe_program')


def test_rename_takes_new_name_from_ddl():
    """The monitor stores only the old name; the new name comes from the DDL text."""
    row = {**PG_ROW, 'alter_type': 'RENAME COLUMN', 'column_name': 'tanggal_lahir',
           'table_name': 'master_penduduk',
           'ddl_command': 'ALTER TABLE public.master_penduduk RENAME COLUMN tanggal_lahir '
                          'TO tgl_lahir_ktp;'}
    (event,) = normalize('t', 0, 1, message(row), 'dukcapil')
    assert (event.operation, event.column, event.new_column) == ('rename', 'tanggal_lahir',
                                                                 'tgl_lahir_ktp')


def test_mysql_row_uses_db_name_and_dialect():
    row = {'db_name': 'dukcapil', 'table_name': 'master_penduduk', 'alter_type': 'RENAME COLUMN',
           'column_name': 'tanggal_lahir', 'captured_at': '2026-09-18T04:00:00Z',
           'ddl_command': 'ALTER TABLE master_penduduk RENAME COLUMN tanggal_lahir TO tgl_lahir_ktp'}
    (event,) = normalize('t', 0, 1, message(row), 'dukcapil', dbms='mysql')
    assert (event.schema, event.column, event.new_column) == ('dukcapil', 'tanggal_lahir',
                                                              'tgl_lahir_ktp')


def test_multi_statement_ddl_becomes_several_events():
    """One log row can hold several statements; each becomes its own event."""
    row = {**PG_ROW, 'table_name': 'f0_test',
           'ddl_command': "ALTER TABLE public.f0_test ADD COLUMN email VARCHAR(100);\n"
                          "ALTER TABLE public.f0_test DROP COLUMN code;\n"
                          "UPDATE public.f0_test SET email = 'x' WHERE id = 1;"}
    events = normalize('t', 0, 5, message(row), 'kemensos')
    assert [(e.operation, e.column) for e in events] == [('add', 'email'), ('drop', 'code')]
    assert len({e.event_uid for e in events}) == 2               # a different uid per statement


def test_unsupported_statement_becomes_other():
    row = {**PG_ROW, 'alter_type': 'ADD COLUMN',
           'ddl_command': 'ALTER TABLE public.penerima_manfaat ALTER COLUMN nik TYPE text;'}
    (event,) = normalize('t', 0, 1, message(row), 'kemensos')
    assert event.operation == 'other' and 'unsupported_statement' in event.raw


def test_unparsable_ddl_falls_back_to_log_row():
    row = {**PG_ROW, 'ddl_command': 'NOT SQL ###'}
    (event,) = normalize('t', 0, 1, message(row), 'kemensos')
    assert (event.operation, event.column) == ('add', 'email')
    assert 'fallback' in event.raw or 'parse_error' in event.raw


def test_uid_is_deterministic_for_the_same_ddl():
    a = normalize('topic', 0, 42, message(PG_ROW), 'kemensos')[0]
    b = normalize('topic', 0, 42, message(PG_ROW), 'kemensos')[0]
    assert a.event_uid == b.event_uid
    assert uuid.UUID(a.event_uid).version == 5


def test_uid_survives_topic_recreation():
    """Regression F6: the topic is recreated and the offset starts again, but the DDL differs."""
    old = normalize('topic', 0, 2, message(PG_ROW), 'kemensos')[0]
    new_row = {**PG_ROW, 'id': 3, 'captured_at': '2026-09-21T21:19:12.849084Z'}
    new = normalize('topic', 0, 2, message(new_row), 'kemensos')[0]      # same offset
    assert old.event_uid != new.event_uid
    # the same message delivered again from another offset is still recognised as a duplicate
    again = normalize('topic', 0, 99, message(PG_ROW), 'kemensos')[0]
    assert again.event_uid == old.event_uid


def test_uid_falls_back_to_offset_without_row_identity():
    without_id = {k: v for k, v in PG_ROW.items() if k not in ('id', 'captured_at')}
    a = normalize('topic', 0, 5, message(without_id), 'kemensos')[0]
    b = normalize('topic', 0, 6, message(without_id), 'kemensos')[0]
    assert a.event_uid != b.event_uid


@pytest.mark.parametrize('value', [None, {}, {'op': 'u', 'after': PG_ROW}, {'op': 'c', 'after': None}])
def test_non_insert_or_malformed_messages_are_skipped(value):
    assert normalize('t', 0, 1, value, 'kemensos') == []


def test_debezium_envelope_with_schema_wrapper():
    envelope = {'schema': {'type': 'struct'}, 'payload': message(PG_ROW)}
    assert len(normalize('t', 0, 1, envelope, 'kemensos')) == 1


@pytest.mark.parametrize('value, fragment', [
    (None, 'tombstone'),
    ('{"payload": {}}', 'not a JSON object'),
    ({'op': 'u', 'after': PG_ROW}, "Debezium operation 'u'"),
    ({'op': 'c', 'after': None}, 'no after row'),
])
def test_skip_reason_explains_dropped_messages(value, fragment):
    from ascam_orchestrator.normalizer import skip_reason
    assert normalize('t', 0, 1, value, 'kemensos') == []
    assert fragment in skip_reason(value)
