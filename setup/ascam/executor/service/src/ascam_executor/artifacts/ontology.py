"""
Append-only editing of the ontology (𝒯).

Rationale (ADR-0010, ownership invariant P6): ASCAM only adds triples, and only removes those
it added itself. By writing marked blocks at the end of the file, the comments and layout of
the parts written by people stay intact, a property already shown in the first iteration.

New properties are written with common vocabulary: `rdfs:label`, `rdfs:comment`, `rdfs:domain`,
`rdfs:range`, `dcterms:created`, and `skos:changeNote`. Deprecation uses `owl:deprecated`
(OWL 2) instead of removal, so that existing queries do not break at once.

A property keeps at most one ASCAM status block, so repeated changes to the same column do not
make the file grow: `deprecate <iri>` after a DROP, or `reactivate <iri>` after the column was
added again. Adding the column again withdraws ASCAM's own deprecation; a deprecation written by
a person is never removed (P6), and the action fails instead.
"""
from datetime import datetime, timezone

from rdflib import Graph, Literal, URIRef
from rdflib.namespace import OWL, RDFS

from .turtle_text import BLOCK_MARKER, append_block, ensure_prefixes, prefix_map, remove_block, shorten

PREFIXES = {
    'owl': 'http://www.w3.org/2002/07/owl#',
    'rdfs': 'http://www.w3.org/2000/01/rdf-schema#',
    'xsd': 'http://www.w3.org/2001/XMLSchema#',
    'dcterms': 'http://purl.org/dc/terms/',
    'skos': 'http://www.w3.org/2004/02/skos/core#',
}
MARKER = BLOCK_MARKER
DEPRECATED = 'The source column was dropped; property deprecated by ASCAM.'
REACTIVATED = 'The source column was added again; deprecation withdrawn by ASCAM.'


class OntologyError(Exception):
    pass


def is_declared(text: str, iri: str) -> bool:
    prefixes = prefix_map(text)
    short = shorten(iri, prefixes)
    for lines in text.splitlines():
        stripped = lines.strip()
        if stripped.startswith((short + ' ', short + '\t')) and 'owl:DatatypeProperty' in stripped:
            return True
    return False


def _graph(text: str) -> Graph:
    graph = Graph()
    try:
        graph.parse(data=text, format='turtle')
    except Exception as exc:                            # noqa: BLE001
        raise OntologyError(f'the ontology is not valid Turtle: {exc}') from exc
    return graph


def _full(iri: str) -> str:
    return PREFIXES['xsd'] + iri[4:] if iri.startswith('xsd:') else iri


def declared_ranges(text: str, iri: str) -> set[str]:
    return {_full(str(r)) for r in _graph(text).objects(URIRef(iri), RDFS.range)}


def add_datatype_property(text: str, iri: str, *, domain: str | None, range_iri: str,
                          label: str, comment: str, when: datetime | None = None,
                          change_note: str | None = None) -> str:
    """Adds a new DatatypeProperty (pattern P-001).

    If the property is already declared (its column was dropped and is added again), nothing is
    declared twice: ASCAM's deprecation is withdrawn instead. A declared range that differs from
    the range of the new column is an error, because the old declaration would stay in force."""
    if is_declared(text, iri):
        ranges = declared_ranges(text, iri)
        if ranges and _full(range_iri) not in ranges:
            raise OntologyError(f'{iri} is already declared with range {sorted(ranges)}, '
                                f'but the new column maps to {range_iri}; choose another property name')
        return reactivate_property(text, iri, when=when)
    when = when or datetime.now(timezone.utc)
    result = ensure_prefixes(text, PREFIXES)
    prefixes = prefix_map(result)
    lines = [f'\n{shorten(iri, prefixes)} a owl:DatatypeProperty ;']
    if domain:
        lines.append(f'    rdfs:domain {shorten(domain, prefixes)} ;')
    lines.append(f'    rdfs:range {shorten(range_iri, prefixes)} ;')
    lines.append(f'    rdfs:label "{label}" ;')
    lines.append(f'    rdfs:comment "{comment}" ;')
    lines.append(f'    dcterms:created "{when.isoformat()}"^^xsd:dateTime ;')
    lines.append(f'    skos:changeNote "{change_note or comment}" .')
    return append_block(result, f'property {iri}', '\n'.join(lines))


def _status_block(text: str, iri: str, kind: str, statement: str | None, reason: str,
                  when: datetime | None) -> str:
    """Replaces the ASCAM status blocks of `iri` with one block of the given kind."""
    when = when or datetime.now(timezone.utc)
    result = ensure_prefixes(text, PREFIXES)
    short = shorten(iri, prefix_map(result))
    lines = [f'\n{short} {statement} ;'] if statement else [f'\n{short}']
    lines.append(f'    dcterms:modified "{when.isoformat()}"^^xsd:dateTime ;')
    lines.append(f'    skos:changeNote "{reason}" .')
    return append_block(result, f'{kind} {iri}', '\n'.join(lines))


def _without_status(text: str, iri: str) -> tuple[str, int]:
    result, deprecations = remove_block(text, f'deprecate {iri}')
    result, _ = remove_block(result, f'reactivate {iri}')
    return result, deprecations


def deprecate_property(text: str, iri: str, *, reason: str,
                       when: datetime | None = None) -> str:
    """Marks a property as deprecated (pattern P-002) without removing its declaration.

    An earlier ASCAM status block of the property is replaced, not repeated. If a person has
    already deprecated the property outside the managed blocks, nothing is added."""
    base, _ = _without_status(text, iri)
    if is_deprecated(base, iri):
        return text
    return _status_block(base, iri, 'deprecate', 'owl:deprecated true', reason, when)


def reactivate_property(text: str, iri: str, *, reason: str = REACTIVATED,
                        when: datetime | None = None) -> str:
    """Withdraws ASCAM's deprecation of `iri` (its column was added again); idempotent."""
    base, deprecations = _without_status(text, iri)
    if is_deprecated(base, iri):
        raise OntologyError(f'{iri} is deprecated outside the ASCAM-managed blocks; ASCAM does not '
                            'remove statements it did not write (P6), so choose another property name '
                            'or withdraw the deprecation by hand')
    if not deprecations:
        return text                                     # not deprecated: nothing to withdraw
    return _status_block(base, iri, 'reactivate', None, reason, when)


def is_deprecated(text: str, iri: str) -> bool:
    """Whether the ontology states `iri owl:deprecated true`, wherever it is written."""
    graph = _graph(text)
    return any(value.toPython() is True or str(value).lower() == 'true'
               for value in graph.objects(URIRef(iri), OWL.deprecated)
               if isinstance(value, Literal))


def apply_actions(text: str, actions: list[dict], when: datetime | None = None) -> str:
    result = text
    for action in actions:
        operation = action['operation']
        if operation == 'add_datatype_property':
            domain = (action.get('domain') or [None])[0]
            column = action.get('column')
            result = add_datatype_property(
                result, action['iri'], domain=domain,
                range_iri=action.get('range_iri') or f"{PREFIXES['xsd']}string",
                label=action.get('label') or column or action['iri'].rsplit('/', 1)[-1],
                comment=action.get('comment')
                or f'Added automatically by ASCAM for source column {column}.',
                when=when)
        elif operation == 'deprecate_property':
            result = deprecate_property(result, action['predicate_iri'],
                                       reason=action.get('reason') or DEPRECATED, when=when)
        elif operation == 'keep_property':
            continue                                    # no change to 𝒯
        else:
            raise OntologyError(f'unknown ontology action {operation}')
    return result
