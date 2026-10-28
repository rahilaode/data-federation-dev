"""
Append-only editing of the ontology (𝒯).

Rationale (ADR-0010, ownership invariant P6): ASCAM only adds triples, and only removes those
it added itself. By writing marked blocks at the end of the file, the comments and layout of
the parts written by people stay intact, a property already shown in the first iteration.

New properties are written with common vocabulary: `rdfs:label`, `rdfs:comment`, `rdfs:domain`,
`rdfs:range`, `dcterms:created`, and `skos:changeNote`. Deprecation uses `owl:deprecated`
(OWL 2) instead of removal, so that existing queries do not break at once.
"""
from datetime import datetime, timezone

from .turtle_text import BLOCK_MARKER, append_block, ensure_prefixes, prefix_map, shorten

PREFIXES = {
    'owl': 'http://www.w3.org/2002/07/owl#',
    'rdfs': 'http://www.w3.org/2000/01/rdf-schema#',
    'xsd': 'http://www.w3.org/2001/XMLSchema#',
    'dcterms': 'http://purl.org/dc/terms/',
    'skos': 'http://www.w3.org/2004/02/skos/core#',
}
MARKER = BLOCK_MARKER


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


def add_datatype_property(text: str, iri: str, *, domain: str | None, range_iri: str,
                          label: str, comment: str, when: datetime | None = None,
                          change_note: str | None = None) -> str:
    """Adds a new DatatypeProperty (pattern P-001); idempotent if it is already declared."""
    if is_declared(text, iri):
        return text
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


def deprecate_property(text: str, iri: str, *, reason: str,
                       when: datetime | None = None) -> str:
    """Marks a property as deprecated (pattern P-002) without removing its declaration."""
    when = when or datetime.now(timezone.utc)
    result = ensure_prefixes(text, PREFIXES)
    prefixes = prefix_map(result)
    short = shorten(iri, prefixes)
    block = (f'\n{short} owl:deprecated true ;\n'
            f'    dcterms:modified "{when.isoformat()}"^^xsd:dateTime ;\n'
            f'    skos:changeNote "{reason}" .\n')
    return append_block(result, f'deprecate {iri}', block)


def is_deprecated(text: str, iri: str) -> bool:
    prefixes = prefix_map(text)
    short = shorten(iri, prefixes)
    for lines in text.splitlines():
        stripped = lines.strip()
        if stripped.startswith(short) and 'owl:deprecated' in stripped and 'true' in stripped:
            return True
    return False


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
                                       reason=action.get('reason')
                                       or 'The source column was dropped; property deprecated by ASCAM.',
                                       when=when)
        elif operation == 'keep_property':
            continue                                    # no change to 𝒯
        else:
            raise OntologyError(f'unknown ontology action {operation}')
    return result
