"""
Editing of the Teiid VDB file.

Principle (ADR-0002): existing DDL is NEITHER parsed NOR rewritten. Every change only APPENDS
`ALTER FOREIGN TABLE` statements (and `ALTER VIEW`, ADR-0023) at the end of the metadata block of
the model concerned. The XML structure is handled with an XML parser (minidom), so the CDATA
blocks stay intact. Every identifier is double-quoted, because source column names can be Teiid
keywords (ADR-0003).

The VDB version is raised in the attribute `version`, as the blue-green deployment requires (ADR-0004).
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
        raise VdbError(f'the VDB file cannot be parsed: {exc}') from exc


def version_of(xml: str) -> str:
    return _document(xml).documentElement.getAttribute('version')


def name_of(xml: str) -> str:
    return _document(xml).documentElement.getAttribute('name')


def _model(document: minidom.Document, model: str) -> minidom.Element:
    for element in document.getElementsByTagName('model'):
        if element.getAttribute('name') == model:
            return element
    raise VdbError(f'model {model} does not exist in the VDB')


def _metadata_text_node(model_element: minidom.Element):
    for metadata in model_element.getElementsByTagName('metadata'):
        if (metadata.getAttribute('type') or 'DDL').upper() != 'DDL':
            continue
        for child in metadata.childNodes:
            if child.nodeType in (child.CDATA_SECTION_NODE, child.TEXT_NODE):
                return child
    raise VdbError(f"model {model_element.getAttribute('name')} has no DDL metadata")


def statement_add_column(table: str, column: str, teiid_type: str) -> str:
    return f'ALTER FOREIGN TABLE {quote(table)} ADD COLUMN {quote(column)} {teiid_type};'


def statement_drop_column(table: str, column: str) -> str:
    return f'ALTER FOREIGN TABLE {quote(table)} DROP COLUMN {quote(column)};'


def statement_set_name_in_source(table: str, column: str, name_in_source: str) -> str:
    escaped = name_in_source.replace("'", "''")
    return (f'ALTER FOREIGN TABLE {quote(table)} ALTER COLUMN {quote(column)} '
            f"OPTIONS (SET NAMEINSOURCE '{escaped}');")


def statement_alter_view(view: str, body: str) -> str:
    """The view definition is replaced; Teiid derives its column list from the new query (ADR-0023)."""
    body_text = body.strip().rstrip(';').strip()
    if not body_text:
        raise VdbError(f'the new definition of view {view} is empty')
    return f'ALTER VIEW {quote(view)} AS {body_text};'


def statements_recreate_view(view: str, body: str) -> list[str]:
    """Fallback when Teiid rejects an ALTER VIEW that changes the column list: the view is dropped
    and defined again (`DROP VIEW`, BNF *drop table*, teiid-documents p. 819). Still only appends
    statements (ADR-0002); the mode is chosen with ASCAM_EXEC_VIEW_STATEMENT."""
    body_text = statement_alter_view(view, body)[len(f'ALTER VIEW {quote(view)} AS '):]
    return [f'DROP VIEW {quote(view)};', f'CREATE VIEW {quote(view)} AS {body_text}']


VIEW_STATEMENTS = ('alter', 'recreate')


def _indentation(text: str) -> str:
    """Indentation of the existing DDL lines, so that new statements line up with them."""
    for line in text.splitlines():
        if line.strip():
            return line[:len(line) - len(line.lstrip())]
    return ''


def append_statements(xml: str, model: str, statements: list[str],
                      new_version: str | None = None) -> str:
    """Appends ALTER statements to the metadata of a model, optionally raising the VDB version."""
    if not statements:
        raise VdbError('no statements to append')
    document = _document(xml)
    node = _metadata_text_node(_model(document, model))
    indent = _indentation(node.data)
    block = '\n' + '\n'.join(indent + item for item in statements) + '\n' + indent
    node.data = node.data.rstrip() + '\n' + block
    if new_version is not None:
        document.documentElement.setAttribute('version', str(new_version))
    # toxml(encoding=...) keeps the complete XML declaration, including the encoding
    return document.toxml(encoding='UTF-8').decode('utf-8').replace('?><vdb', '?>\n<vdb', 1)


def next_version(xml: str) -> str:
    """The next version: the next integer if the current version is numeric."""
    current = version_of(xml)
    try:
        return str(int(current) + 1)
    except ValueError:
        raise VdbError(f'VDB version {current!r} is not an integer; give the version explicitly')


def apply_actions(xml: str, actions: list[dict], type_lookup=None,
                  new_version: str | None = None,
                  view_statement: str = 'alter') -> tuple[str, list[str]]:
    """Applies the plan actions on artifact `vdb`.

    `type_lookup(column_type)` maps the original source type to a Teiid type (from Knowledge);
    if it is not given, `column_type` is used as it is. `view_statement` sets the form of the
    view adjustment: 'alter' (ALTER VIEW) or 'recreate' (DROP VIEW then CREATE VIEW).
    """
    if view_statement not in VIEW_STATEMENTS:
        raise VdbError(f'unknown view statement form: {view_statement!r}')
    per_model: dict[str, list[str]] = {}
    for action in actions:
        operation = action['operation']
        model, table = action['model'], action['table']
        if operation == 'add_column':
            teiid_type = (type_lookup or (lambda t: t))(action.get('column_type'))
            if not teiid_type:
                raise VdbError(f"unknown column type for {table}.{action['column']}")
            statement = statement_add_column(table, action['column'], teiid_type)
        elif operation == 'drop_column':
            statement = statement_drop_column(table, action['column'])
        elif operation == 'alter_view' and view_statement == 'recreate':
            per_model.setdefault(model, []).extend(statements_recreate_view(table, action['body']))
            continue
        elif operation == 'alter_view':
            statement = statement_alter_view(table, action['body'])
        elif operation == 'set_name_in_source':
            statement = statement_set_name_in_source(table, action['column'],
                                                     action['name_in_source'])
        else:
            raise VdbError(f'unknown VDB action {operation}')
        per_model.setdefault(model, []).append(statement)

    result = xml
    all_statements: list[str] = []
    for index, (model, statements) in enumerate(per_model.items()):
        result = append_statements(result, model, statements,
                                  new_version if index == 0 else None)
        all_statements += statements
    return result, all_statements
