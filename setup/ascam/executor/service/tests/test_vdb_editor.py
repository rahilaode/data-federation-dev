"""Penyuntingan berkas VDB: hanya menambahkan ALTER, CDATA utuh, identifier dikutip."""
import pytest
from xml.dom import minidom

from ascam_executor.artifacts import vdb

VDB = """<?xml version="1.0" encoding="UTF-8"?>
<vdb name="government" version="1">
  <model visible="true" name="kemensos">
    <source name="kemensos" translator-name="postgresql" connection-jndi-name="java:/pgsql"/>
    <metadata type="DDL"><![CDATA[
      CREATE FOREIGN TABLE penerima_manfaat (
        penerima_id integer not null primary key,
        nik varchar(16) not null
      )OPTIONS(UPDATABLE 'FALSE');
    ]]></metadata>
  </model>
  <model visible="true" name="dukcapil">
    <source name="dukcapil" translator-name="mysql5" connection-jndi-name="java:/mysql"/>
    <metadata type="DDL"><![CDATA[
      CREATE FOREIGN TABLE master_penduduk (nik varchar(16) not null primary key);
    ]]></metadata>
  </model>
</vdb>
"""


def test_append_keeps_cdata_and_existing_ddl():
    hasil = vdb.append_statements(VDB, 'kemensos', [vdb.statement_add_column(
        'penerima_manfaat', 'email', 'string(100)')])
    assert hasil.count('<![CDATA[') == 2
    assert 'CREATE FOREIGN TABLE penerima_manfaat' in hasil          # DDL lama tidak ditulis ulang
    assert 'ALTER FOREIGN TABLE "penerima_manfaat" ADD COLUMN "email" string(100);' in hasil
    minidom.parseString(hasil)                                       # tetap XML yang sah


def test_version_is_bumped_once_for_multiple_models():
    hasil, statements = vdb.apply_actions(VDB, [
        {'operation': 'add_column', 'model': 'kemensos', 'table': 'penerima_manfaat',
         'column': 'email', 'column_type': 'string(100)'},
        {'operation': 'drop_column', 'model': 'dukcapil', 'table': 'master_penduduk',
         'column': 'nik'}], new_version='2')
    assert vdb.version_of(hasil) == '2' and len(statements) == 2
    assert hasil.count('version="2"') == 1


def test_identifiers_are_quoted_even_for_keywords():
    statement = vdb.statement_add_column('order', 'precision', 'integer')
    assert statement == 'ALTER FOREIGN TABLE "order" ADD COLUMN "precision" integer;'
    assert vdb.statement_set_name_in_source('t', 'c', "nama'aneh") == (
        'ALTER FOREIGN TABLE "t" ALTER COLUMN "c" OPTIONS (SET NAMEINSOURCE \'nama\'\'aneh\');')


def test_type_lookup_is_used_for_add():
    hasil, statements = vdb.apply_actions(
        VDB, [{'operation': 'add_column', 'model': 'kemensos', 'table': 'penerima_manfaat',
               'column': 'email', 'column_type': 'character varying'}],
        type_lookup=lambda t: {'character varying': 'string'}.get(t))
    assert 'ADD COLUMN "email" string;' in hasil


def test_errors_are_explicit():
    with pytest.raises(vdb.VdbError, match='model'):
        vdb.append_statements(VDB, 'tidak-ada', ['ALTER FOREIGN TABLE "t" DROP COLUMN "c";'])
    with pytest.raises(vdb.VdbError, match='tidak dapat diurai'):
        vdb.version_of('bukan xml')
    with pytest.raises(vdb.VdbError, match='tipe kolom'):
        vdb.apply_actions(VDB, [{'operation': 'add_column', 'model': 'kemensos',
                                 'table': 'penerima_manfaat', 'column': 'x', 'column_type': None}])
    with pytest.raises(vdb.VdbError, match='tidak dikenal'):
        vdb.apply_actions(VDB, [{'operation': 'ubah_tipe', 'model': 'kemensos',
                                 'table': 't', 'column': 'c'}])
    with pytest.raises(vdb.VdbError, match='bukan bilangan bulat'):
        vdb.next_version(VDB.replace('version="1"', 'version="1.0.1"'))


def test_repeated_adaptation_accumulates_statements():
    satu, _ = vdb.apply_actions(VDB, [{'operation': 'add_column', 'model': 'kemensos',
                                       'table': 'penerima_manfaat', 'column': 'email',
                                       'column_type': 'string(100)'}], new_version='2')
    dua, _ = vdb.apply_actions(satu, [{'operation': 'drop_column', 'model': 'kemensos',
                                       'table': 'penerima_manfaat', 'column': 'nik'}],
                               new_version='3')
    assert dua.count('ALTER FOREIGN TABLE') == 2 and vdb.version_of(dua) == '3'


def test_xml_declaration_and_indentation_are_preserved():
    hasil = vdb.append_statements(VDB, 'kemensos', [vdb.statement_drop_column('t', 'c')])
    assert hasil.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<vdb ')
    baris_alter = [b for b in hasil.splitlines() if 'ALTER FOREIGN TABLE' in b][0]
    baris_create = [b for b in hasil.splitlines() if 'CREATE FOREIGN TABLE' in b][0]
    spasi = lambda b: len(b) - len(b.lstrip())            # noqa: E731
    assert spasi(baris_alter) == spasi(baris_create)      # sejajar dengan DDL yang ada
