"""Datatype OWL 2 QL (W3C, OWL 2 Web Ontology Language Profiles, 2nd ed., 2012, §3.2.3)."""
XSD = 'http://www.w3.org/2001/XMLSchema#'
RDF = 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
RDFS = 'http://www.w3.org/2000/01/rdf-schema#'
OWL = 'http://www.w3.org/2002/07/owl#'

OWL2QL_DATATYPES = frozenset({
    RDF + 'PlainLiteral', RDF + 'XMLLiteral', RDFS + 'Literal', OWL + 'real', OWL + 'rational',
    *(XSD + n for n in ('decimal', 'integer', 'nonNegativeInteger', 'string', 'normalizedString',
                        'token', 'Name', 'NCName', 'NMTOKEN', 'hexBinary', 'base64Binary',
                        'anyURI', 'dateTime', 'dateTimeStamp')),
})
PREFIXES = {'xsd:': XSD, 'rdf:': RDF, 'rdfs:': RDFS, 'owl:': OWL}


def expand(curie_or_iri: str) -> str:
    for prefix, ns in PREFIXES.items():
        if curie_or_iri.startswith(prefix):
            return ns + curie_or_iri[len(prefix):]
    return curie_or_iri


def owl2ql_compatible(datatype: str) -> bool:
    return expand(datatype) in OWL2QL_DATATYPES
