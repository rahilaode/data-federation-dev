"""
Penyuntingan berkas VDB Teiid.

Prinsip (ADR-0002): DDL yang sudah ada TIDAK diurai maupun ditulis ulang. Setiap perubahan
hanya MENAMBAHKAN pernyataan `ALTER FOREIGN TABLE` di akhir blok metadata model yang
bersangkutan. Struktur XML dimanipulasi dengan parser XML (minidom) sehingga blok CDATA tetap
utuh. Seluruh identifier diberi tanda kutip ganda karena nama kolom sumber dapat berupa kata
kunci Teiid (ADR-0003).

Versi VDB dinaikkan pada atribut `version`, sesuai penerapan blue-green (ADR-0004).
"""
from xml.dom import minidom


class VdbError(Exception):
    pass


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _document(xml: str) -> minidom.Document:
    try:
        return minidom.parseString(xml)
    except Exception as exc:                        # noqa: BLE001
        raise VdbError(f'berkas VDB tidak dapat diurai: {exc}') from exc


def version_of(xml: str) -> str:
    return _document(xml).documentElement.getAttribute('version')


def name_of(xml: str) -> str:
    return _document(xml).documentElement.getAttribute('name')


def _model(document: minidom.Document, model: str) -> minidom.Element:
    for element in document.getElementsByTagName('model'):
        if element.getAttribute('name') == model:
            return element
    raise VdbError(f'model {model} tidak ada pada VDB')


def _metadata_text_node(model_element: minidom.Element):
    for metadata in model_element.getElementsByTagName('metadata'):
        if (metadata.getAttribute('type') or 'DDL').upper() != 'DDL':
            continue
        for child in metadata.childNodes:
            if child.nodeType in (child.CDATA_SECTION_NODE, child.TEXT_NODE):
                return child
    raise VdbError(f"model {model_element.getAttribute('name')} tidak memiliki metadata DDL")


def statement_add_column(table: str, column: str, teiid_type: str) -> str:
    return f'ALTER FOREIGN TABLE {quote(table)} ADD COLUMN {quote(column)} {teiid_type};'


def statement_drop_column(table: str, column: str) -> str:
    return f'ALTER FOREIGN TABLE {quote(table)} DROP COLUMN {quote(column)};'


def statement_set_name_in_source(table: str, column: str, name_in_source: str) -> str:
    aman = name_in_source.replace("'", "''")
    return (f'ALTER FOREIGN TABLE {quote(table)} ALTER COLUMN {quote(column)} '
            f"OPTIONS (SET NAMEINSOURCE '{aman}');")


def append_statements(xml: str, model: str, statements: list[str],
                      new_version: str | None = None) -> str:
    """Menambahkan pernyataan ALTER ke metadata model, opsional menaikkan versi VDB."""
    if not statements:
        raise VdbError('tidak ada pernyataan untuk ditambahkan')
    document = _document(xml)
    node = _metadata_text_node(_model(document, model))
    blok = '\n' + '\n'.join(statements) + '\n'
    node.data = node.data.rstrip() + '\n' + blok
    if new_version is not None:
        document.documentElement.setAttribute('version', str(new_version))
    return document.toxml()


def next_version(xml: str) -> str:
    """Versi berikutnya: bilangan bulat berikutnya bila versi saat ini numerik."""
    current = version_of(xml)
    try:
        return str(int(current) + 1)
    except ValueError:
        raise VdbError(f'versi VDB {current!r} bukan bilangan bulat; tentukan versi secara eksplisit')


def apply_actions(xml: str, actions: list[dict], type_lookup=None,
                  new_version: str | None = None) -> tuple[str, list[str]]:
    """Menerapkan tindakan rencana beraksi artefak `vdb`.

    `type_lookup(column_type)` memetakan tipe asli sumber ke tipe Teiid (dari Knowledge);
    bila tidak diberikan, `column_type` dipakai apa adanya.
    """
    per_model: dict[str, list[str]] = {}
    for action in actions:
        operation = action['operation']
        model, table = action['model'], action['table']
        if operation == 'add_column':
            tipe = (type_lookup or (lambda t: t))(action.get('column_type'))
            if not tipe:
                raise VdbError(f"tipe kolom untuk {table}.{action['column']} tidak diketahui")
            statement = statement_add_column(table, action['column'], tipe)
        elif operation == 'drop_column':
            statement = statement_drop_column(table, action['column'])
        elif operation == 'set_name_in_source':
            statement = statement_set_name_in_source(table, action['column'],
                                                     action['name_in_source'])
        else:
            raise VdbError(f'tindakan VDB {operation} tidak dikenal')
        per_model.setdefault(model, []).append(statement)

    hasil = xml
    semua: list[str] = []
    for index, (model, statements) in enumerate(per_model.items()):
        hasil = append_statements(hasil, model, statements,
                                  new_version if index == 0 else None)
        semua += statements
    return hasil, semua
