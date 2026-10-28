"""VDB file editing: only ALTER statements are appended, CDATA stays intact, identifiers are quoted."""
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
    result = vdb.append_statements(VDB, 'kemensos', [vdb.statement_add_column(
        'penerima_manfaat', 'email', 'string(100)')])
    assert result.count('<![CDATA[') == 2
    assert 'CREATE FOREIGN TABLE penerima_manfaat' in result          # the old DDL is not rewritten
    assert 'ALTER FOREIGN TABLE "penerima_manfaat" ADD COLUMN "email" string(100);' in result
    minidom.parseString(result)                                       # still valid XML


def test_version_is_bumped_once_for_multiple_models():
    result, statements = vdb.apply_actions(VDB, [
        {'operation': 'add_column', 'model': 'kemensos', 'table': 'penerima_manfaat',
         'column': 'email', 'column_type': 'string(100)'},
        {'operation': 'drop_column', 'model': 'dukcapil', 'table': 'master_penduduk',
         'column': 'nik'}], new_version='2')
    assert vdb.version_of(result) == '2' and len(statements) == 2
    assert result.count('version="2"') == 1


def test_identifiers_are_quoted_even_for_keywords():
    statement = vdb.statement_add_column('order', 'precision', 'integer')
    assert statement == 'ALTER FOREIGN TABLE "order" ADD COLUMN "precision" integer;'
    assert vdb.statement_set_name_in_source('t', 'c', "odd'name") == (
        'ALTER FOREIGN TABLE "t" ALTER COLUMN "c" OPTIONS (SET NAMEINSOURCE \'odd\'\'name\');')


def test_type_lookup_is_used_for_add():
    result, statements = vdb.apply_actions(
        VDB, [{'operation': 'add_column', 'model': 'kemensos', 'table': 'penerima_manfaat',
               'column': 'email', 'column_type': 'character varying'}],
        type_lookup=lambda t: {'character varying': 'string'}.get(t))
    assert 'ADD COLUMN "email" string;' in result


def test_errors_are_explicit():
    with pytest.raises(vdb.VdbError, match='model'):
        vdb.append_statements(VDB, 'does-not-exist', ['ALTER FOREIGN TABLE "t" DROP COLUMN "c";'])
    with pytest.raises(vdb.VdbError, match='cannot be parsed'):
        vdb.version_of('not xml')
    with pytest.raises(vdb.VdbError, match='unknown column type'):
        vdb.apply_actions(VDB, [{'operation': 'add_column', 'model': 'kemensos',
                                 'table': 'penerima_manfaat', 'column': 'x', 'column_type': None}])
    with pytest.raises(vdb.VdbError, match='unknown VDB action'):
        vdb.apply_actions(VDB, [{'operation': 'change_type', 'model': 'kemensos',
                                 'table': 't', 'column': 'c'}])
    with pytest.raises(vdb.VdbError, match='is not an integer'):
        vdb.next_version(VDB.replace('version="1"', 'version="1.0.1"'))


def test_repeated_adaptation_accumulates_statements():
    once, _ = vdb.apply_actions(VDB, [{'operation': 'add_column', 'model': 'kemensos',
                                       'table': 'penerima_manfaat', 'column': 'email',
                                       'column_type': 'string(100)'}], new_version='2')
    twice, _ = vdb.apply_actions(once, [{'operation': 'drop_column', 'model': 'kemensos',
                                       'table': 'penerima_manfaat', 'column': 'nik'}],
                               new_version='3')
    assert twice.count('ALTER FOREIGN TABLE') == 2 and vdb.version_of(twice) == '3'


def test_xml_declaration_and_indentation_are_preserved():
    result = vdb.append_statements(VDB, 'kemensos', [vdb.statement_drop_column('t', 'c')])
    assert result.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<vdb ')
    alter_line = [b for b in result.splitlines() if 'ALTER FOREIGN TABLE' in b][0]
    create_line = [b for b in result.splitlines() if 'CREATE FOREIGN TABLE' in b][0]
    indent = lambda b: len(b) - len(b.lstrip())            # noqa: E731
    assert indent(alter_line) == indent(create_line)      # aligned with the existing DDL


VDB_VIEW = VDB.replace('</vdb>', """  <model visible="true" name="v" type="VIRTUAL">
    <metadata type="DDL"><![CDATA[
      CREATE VIEW profil AS SELECT p.nik, p.pekerjaan FROM dukcapil.master_penduduk AS p;
    ]]></metadata>
  </model>
</vdb>""")


def test_alter_view_is_appended_to_the_view_model():
    """ADR-0023: dropping a foreign-table column and a view column in one new VDB version."""
    result, statements = vdb.apply_actions(VDB_VIEW, [
        {'operation': 'drop_column', 'model': 'dukcapil', 'table': 'master_penduduk',
         'column': 'pekerjaan'},
        {'operation': 'alter_view', 'model': 'v', 'table': 'profil',
         'body': 'SELECT p.nik FROM dukcapil.master_penduduk AS p'},
    ], new_version='2')
    assert statements[-1] == 'ALTER VIEW "profil" AS SELECT p.nik FROM dukcapil.master_penduduk AS p;'
    model_v = [m for m in minidom.parseString(result).getElementsByTagName('model')
               if m.getAttribute('name') == 'v'][0]
    ddl = model_v.getElementsByTagName('metadata')[0].firstChild.data
    assert 'CREATE VIEW profil AS SELECT p.nik, p.pekerjaan' in ddl     # the old definition is intact
    assert ddl.rstrip().endswith('ALTER VIEW "profil" AS SELECT p.nik FROM dukcapil.master_penduduk AS p;')
    assert vdb.version_of(result) == '2'


def test_alter_view_rejects_empty_body():
    with pytest.raises(vdb.VdbError):
        vdb.statement_alter_view('profil', ' ; ')


def test_recreate_view_mode_drops_then_creates():
    """F0.8 fallback: DROP VIEW then CREATE VIEW, still only appending statements."""
    result, statements = vdb.apply_actions(VDB_VIEW, [
        {'operation': 'drop_column', 'model': 'dukcapil', 'table': 'master_penduduk',
         'column': 'pekerjaan'},
        {'operation': 'alter_view', 'model': 'v', 'table': 'profil',
         'body': 'SELECT p.nik FROM dukcapil.master_penduduk AS p;'},
    ], new_version='2', view_statement='recreate')
    assert statements[-2:] == ['DROP VIEW "profil";',
                               'CREATE VIEW "profil" AS SELECT p.nik FROM dukcapil.master_penduduk AS p;']
    model_v = [m for m in minidom.parseString(result).getElementsByTagName('model')
               if m.getAttribute('name') == 'v'][0]
    ddl = model_v.getElementsByTagName('metadata')[0].firstChild.data
    assert 'CREATE VIEW profil AS SELECT p.nik, p.pekerjaan' in ddl
    assert ddl.index('DROP VIEW "profil";') < ddl.index('CREATE VIEW "profil" AS SELECT p.nik FROM')


def test_unknown_view_statement_mode_is_rejected():
    with pytest.raises(vdb.VdbError):
        vdb.apply_actions(VDB_VIEW, [], view_statement='replace')
