"""Normalisasi pesan monitor skema menjadi event terformalisasi."""
import uuid

import pytest

from ascam_orchestrator.normalizer import normalize


def pesan(after: dict, op: str = 'c') -> dict:
    return {'before': None, 'after': after, 'op': op, 'ts_ms': 1789610589382,
            'source': {'connector': 'postgresql', 'name': 'kemensos'}}


PG_ROW = {'id': 7, 'username': 'postgres', 'schema_name': 'public', 'table_name': 'penerima_manfaat',
          'object_tag': 'public.penerima_manfaat', 'command_tag': 'ALTER TABLE',
          'alter_type': 'ADD COLUMN', 'column_name': 'email',
          'ddl_command': 'ALTER TABLE public.penerima_manfaat ADD COLUMN email VARCHAR(100);',
          'is_regulated': True, 'captured_at': '2026-09-18T04:00:00Z'}


def test_add_column(pg=PG_ROW):
    events = normalize('kemensos.schema_monitor.ddl_event_log', 0, 12, pesan(pg), 'kemensos')
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
    (event,) = normalize('t', 0, 1, pesan(row), 'kemensos')
    assert (event.operation, event.table, event.column) == ('drop', 'program_bansos', 'tipe_program')


def test_rename_takes_new_name_from_ddl():
    """Monitor hanya menyimpan nama lama; nama baru diambil dari teks DDL."""
    row = {**PG_ROW, 'alter_type': 'RENAME COLUMN', 'column_name': 'tanggal_lahir',
           'table_name': 'master_penduduk',
           'ddl_command': 'ALTER TABLE public.master_penduduk RENAME COLUMN tanggal_lahir '
                          'TO tgl_lahir_ktp;'}
    (event,) = normalize('t', 0, 1, pesan(row), 'dukcapil')
    assert (event.operation, event.column, event.new_column) == ('rename', 'tanggal_lahir',
                                                                 'tgl_lahir_ktp')


def test_mysql_row_uses_db_name_and_dialect():
    row = {'db_name': 'dukcapil', 'table_name': 'master_penduduk', 'alter_type': 'RENAME COLUMN',
           'column_name': 'tanggal_lahir', 'captured_at': '2026-09-18T04:00:00Z',
           'ddl_command': 'ALTER TABLE master_penduduk RENAME COLUMN tanggal_lahir TO tgl_lahir_ktp'}
    (event,) = normalize('t', 0, 1, pesan(row), 'dukcapil', dbms='mysql')
    assert (event.schema, event.column, event.new_column) == ('dukcapil', 'tanggal_lahir',
                                                              'tgl_lahir_ktp')


def test_multi_statement_ddl_becomes_several_events():
    """Satu baris log dapat memuat beberapa pernyataan; masing-masing menjadi event sendiri."""
    row = {**PG_ROW, 'table_name': 'f0_uji',
           'ddl_command': "ALTER TABLE public.f0_uji ADD COLUMN email VARCHAR(100);\n"
                          "ALTER TABLE public.f0_uji DROP COLUMN kode;\n"
                          "UPDATE public.f0_uji SET email = 'x' WHERE id = 1;"}
    events = normalize('t', 0, 5, pesan(row), 'kemensos')
    assert [(e.operation, e.column) for e in events] == [('add', 'email'), ('drop', 'kode')]
    assert len({e.event_uid for e in events}) == 2               # uid berbeda per pernyataan


def test_unsupported_statement_becomes_other():
    row = {**PG_ROW, 'alter_type': 'ADD COLUMN',
           'ddl_command': 'ALTER TABLE public.penerima_manfaat ALTER COLUMN nik TYPE text;'}
    (event,) = normalize('t', 0, 1, pesan(row), 'kemensos')
    assert event.operation == 'other' and 'unsupported_statement' in event.raw


def test_unparsable_ddl_falls_back_to_log_row():
    row = {**PG_ROW, 'ddl_command': 'BUKAN SQL ###'}
    (event,) = normalize('t', 0, 1, pesan(row), 'kemensos')
    assert (event.operation, event.column) == ('add', 'email')
    assert 'fallback' in event.raw or 'parse_error' in event.raw


def test_uid_is_deterministic_per_offset():
    a = normalize('topik', 0, 42, pesan(PG_ROW), 'kemensos')[0]
    b = normalize('topik', 0, 42, pesan(PG_ROW), 'kemensos')[0]
    c = normalize('topik', 0, 43, pesan(PG_ROW), 'kemensos')[0]
    assert a.event_uid == b.event_uid != c.event_uid
    assert uuid.UUID(a.event_uid).version == 5


@pytest.mark.parametrize('value', [None, {}, {'op': 'u', 'after': PG_ROW}, {'op': 'c', 'after': None}])
def test_non_insert_or_malformed_messages_are_skipped(value):
    assert normalize('t', 0, 1, value, 'kemensos') == []


def test_debezium_envelope_with_schema_wrapper():
    envelope = {'schema': {'type': 'struct'}, 'payload': pesan(PG_ROW)}
    assert len(normalize('t', 0, 1, envelope, 'kemensos')) == 1
