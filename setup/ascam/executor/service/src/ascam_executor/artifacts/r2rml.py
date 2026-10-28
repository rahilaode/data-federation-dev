"""
Editing of the R2RML mapping (ℳ).

Unlike 𝒯, removing a predicate-object map cannot be done append-only, so the file is rewritten
from the RDF graph (rdflib). As a consequence, comments inside the file are lost; the file header
(leading comments and prefix order) is kept, and the original content of every version stays in
Knowledge (ADR-0012). This trade-off is recorded in ADR-0019.
"""
import sqlglot
from rdflib import RDF, BNode, Graph, Literal, Namespace, URIRef
from sqlglot import exp

from .turtle_text import append_block, ensure_prefixes, has_block, prefix_map, remove_block, shorten

RR = Namespace('http://www.w3.org/ns/r2rml#')
BASE = 'urn:ascam:mapping'
PREFIXES = {'rr': str(RR), 'xsd': 'http://www.w3.org/2001/XMLSchema#'}


class MappingError(Exception):
    pass


def _graph(text: str) -> Graph:
    graph = Graph()
    try:
        graph.parse(data=text, format='turtle', publicID=BASE)
    except Exception as exc:                        # noqa: BLE001
        raise MappingError(f'the mapping cannot be parsed: {exc}') from exc
    return graph


def _header(text: str) -> str:
    """The leading comments of the file (before the first triple) are kept as they are."""
    lines = []
    for item in text.splitlines():
        stripped = item.strip()
        if stripped.startswith('#') or not stripped:
            lines.append(item)
            continue
        break
    return '\n'.join(lines).rstrip() + '\n\n' if lines else ''


def _serialize(graph: Graph, original_text: str) -> str:
    """Full rewrite: used only when an edit touches a part written by people."""
    for namespace, prefix in prefix_map(original_text).items():          # keep the original prefix names
        graph.namespace_manager.bind(prefix, namespace, replace=True, override=True)
    body = graph.serialize(format='turtle', base=BASE)
    return _header(original_text) + body


def _pom_key(predicate_iri: str, target: str) -> str:
    return f'pom {predicate_iri} on {target}'


def _reference(iri: str, prefixes: dict[str, str]) -> str:
    """Reference to a TriplesMap in the form used by the original file (e.g. <#MapPenerima>)."""
    if iri.startswith(BASE + '#'):
        return f'<#{iri[len(BASE) + 1:]}>'
    return shorten(iri, prefixes)


def triples_map_for_table(graph: Graph, table: str) -> URIRef | None:
    """TriplesMap that reads a given table (through rr:tableName or rr:sqlQuery)."""
    target = table.lower()
    for tm in set(graph.subjects(RR.logicalTable, None)):
        logical = graph.value(tm, RR.logicalTable)
        table_name = graph.value(logical, RR.tableName)
        query = graph.value(logical, RR.sqlQuery)
        if table_name is not None and str(table_name).replace('"', '').lower().endswith(target):
            return tm
        if query is not None and target in str(query).lower():
            return tm
    return None


def _triples_map(graph: Graph, table: str | None, triples_map_iri: str | None):
    """Target TriplesMap: the IRI from the plan if present (chosen by Knowledge from the
    lineage), falling back to matching the table name only."""
    if triples_map_iri:
        candidate = URIRef(triples_map_iri)
        if (candidate, RR.logicalTable, None) in graph:
            return candidate
        raise MappingError(f'TriplesMap {triples_map_iri} does not exist in the mapping')
    if table is None:
        raise MappingError('a table or triples_map_iri must be given')
    return triples_map_for_table(graph, table)


def add_predicate_object_map(text: str, table: str | None, column: str, predicate_iri: str,
                             datatype_iri: str | None = None,
                             triples_map_iri: str | None = None) -> str:
    """Adds a column -> predicate mapping (pattern P-001).

    Written as a managed block at the end of the file: in Turtle, extra triples for the same
    subject merge with its earlier declaration, so the original file need not be rewritten and
    its comments stay intact.
    """
    graph = _graph(text)
    tm = _triples_map(graph, table, triples_map_iri)
    if tm is None:
        raise MappingError(f'no TriplesMap reads table {table}')
    for pom in graph.objects(tm, RR.predicateObjectMap):
        if (pom, RR.predicate, URIRef(predicate_iri)) in graph:
            return text                              # already present: idempotent

    # only the prefixes that the new block actually uses are added
    required = {'rr': str(RR)}
    if datatype_iri:
        required['xsd'] = 'http://www.w3.org/2001/XMLSchema#'
    result = ensure_prefixes(text, required)
    prefixes = prefix_map(result)
    obj = 'rr:column "%s"' % column
    if datatype_iri:
        obj += ' ; rr:datatype %s' % shorten(datatype_iri, prefixes)
    block = '\n'.join([
        '%s rr:predicateObjectMap [' % _reference(str(tm), prefixes),
        '    rr:predicate %s ;' % shorten(predicate_iri, prefixes),
        '    rr:objectMap [ %s ]' % obj,
        '] .',
    ])
    return append_block(result, _pom_key(predicate_iri, str(tm)), block)


def remove_predicate_object_map(text: str, predicate_iri: str, table: str | None = None,
                                triples_map_iri: str | None = None) -> tuple[str, int]:
    """Removes the predicate-object maps of a given predicate (pattern P-002).

    Blocks that ASCAM wrote earlier are removed as text (the rest of the file is untouched).
    Mappings written by people can only be removed by rewriting the file from the RDF graph.
    """
    if triples_map_iri and has_block(text, _pom_key(predicate_iri, triples_map_iri)):
        return remove_block(text, _pom_key(predicate_iri, triples_map_iri))
    managed, count = text, 0
    for target in _managed_targets(text, predicate_iri):
        if triples_map_iri and target != triples_map_iri:
            continue
        managed, removed = remove_block(managed, _pom_key(predicate_iri, target))
        count += removed
    if count:
        return managed, count

    graph = _graph(text)
    target = URIRef(predicate_iri)
    deleted = 0
    for tm in set(graph.subjects(RR.predicateObjectMap, None)):
        if triples_map_iri is not None and str(tm) != triples_map_iri:
            continue
        if triples_map_iri is None and table is not None and triples_map_for_table(graph, table) != tm:
            continue
        for pom in list(graph.objects(tm, RR.predicateObjectMap)):
            if (pom, RR.predicate, target) not in graph:
                continue
            for obj in list(graph.objects(pom, RR.objectMap)):
                graph.remove((obj, None, None))
            graph.remove((pom, None, None))
            graph.remove((tm, RR.predicateObjectMap, pom))
            deleted += 1
    if deleted == 0:
        return text, 0
    return _serialize(graph, text), deleted


def _managed_targets(text: str, predicate_iri: str) -> list[str]:
    """Targets (TriplesMap or table) whose POM block for this predicate was written by ASCAM."""
    prefix = f'# ascam:begin pom {predicate_iri} on '
    return [lines.strip()[len(prefix):] for lines in text.splitlines()
            if lines.strip().startswith(prefix)]


def rewrite_logical_table(text: str, triples_map_iri: str, drop_column: str) -> tuple[str, bool]:
    """Removes one column from the projection list of `rr:sqlQuery` (pattern P-002).

    Needed when the logical table names the column explicitly: removing the predicate-object
    map alone is not enough, because the query still refers to the dropped column and
    `ontop validate` rejects it (finding of evaluation scenario A002).
    """
    graph = _graph(text)
    tm = URIRef(triples_map_iri)
    logical = graph.value(tm, RR.logicalTable)
    if logical is None:
        raise MappingError(f'TriplesMap {triples_map_iri} does not exist in the mapping')
    query = graph.value(logical, RR.sqlQuery)
    if query is None:
        return text, False                      # rr:tableName adjusts by itself
    try:
        tree = sqlglot.parse_one(str(query))
        select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
        if select is None:
            raise ValueError('not a SELECT')
    except Exception as exc:                    # noqa: BLE001
        raise MappingError(f'the logical-table query cannot be parsed: {exc}') from exc

    remaining = [item for item in select.expressions
            if (item.alias_or_name or '').lower() != drop_column.lower()]
    if len(remaining) == len(select.expressions):
        return text, False                      # the column is not projected at all
    if not remaining:
        raise MappingError(f'the logical-table query of {triples_map_iri} projects only '
                           f'{drop_column}; it cannot be edited automatically')
    select.set('expressions', remaining)
    graph.set((logical, RR.sqlQuery, Literal(select.sql())))
    return _serialize(graph, text), True


def predicates_of(text: str) -> set[str]:
    graph = _graph(text)
    return {str(o) for o in graph.objects(None, RR.predicate)}


def apply_actions(text: str, actions: list[dict]) -> str:
    result = text
    for action in actions:
        operation = action['operation']
        if operation == 'add_predicate_object_map':
            result = add_predicate_object_map(result, action.get('table'), action['column'],
                                             action['predicate_iri'], action.get('datatype_iri'),
                                             action.get('triples_map_iri'))
        elif operation == 'rewrite_logical_table':
            result, changed = rewrite_logical_table(result, action['triples_map_iri'],
                                                  action['column'])
        elif operation == 'remove_predicate_object_map':
            result, count = remove_predicate_object_map(result, action['predicate_iri'],
                                                        action.get('table'),
                                                        action.get('triples_map_iri'))
            if count == 0:
                raise MappingError(f"predicate {action['predicate_iri']} not found in the mapping")
        else:
            raise MappingError(f'unknown mapping action {operation}')
    return result
