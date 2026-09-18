"""
Penguraian 𝒯 (ontologi OWL dalam Turtle) menjadi entitas dan aksioma yang dibutuhkan ASCAM.

Yang dicatat: IRI dan versi ontologi, `owl:imports`, entitas (kelas, datatype property, object
property, annotation property; punning diperbolehkan), domain (termasuk bentuk kompleks seperti
`owl:unionOf`), range beserta penanda kompatibilitas OWL 2 QL, relasi antarproperty, dan
anotasi (label, komentar, deprecation, catatan perubahan).
"""
from dataclasses import dataclass, field

from rdflib import OWL, RDF, RDFS, BNode, Graph, Literal, URIRef

from .. import owl as owl_helper

BASE = 'urn:ascam:ontology'
ENTITY_TYPES = {
    OWL.Class: 'class', RDFS.Class: 'class',
    OWL.DatatypeProperty: 'datatype_property',
    OWL.ObjectProperty: 'object_property',
    OWL.AnnotationProperty: 'annotation_property',
}
RELATIONS = {
    RDFS.subPropertyOf: 'sub_property_of',
    OWL.equivalentProperty: 'equivalent_property',
    OWL.inverseOf: 'inverse_of',
    OWL.propertyDisjointWith: 'disjoint_with',
}
ANNOTATION_PROPERTIES = (RDFS.label, RDFS.comment, RDFS.isDefinedBy, OWL.versionInfo,
                         URIRef('http://purl.org/dc/terms/created'),
                         URIRef('http://purl.org/dc/terms/modified'),
                         URIRef('http://www.w3.org/2004/02/skos/core#changeNote'),
                         URIRef('http://www.w3.org/2004/02/skos/core#historyNote'))


@dataclass
class EntitySpec:
    iri: str
    kind: str
    deprecated: bool = False
    functional: bool = False
    domains: list[tuple[str, bool]] = field(default_factory=list)      # (ekspresi kelas, sederhana)
    ranges: list[tuple[str, bool | None]] = field(default_factory=list)  # (IRI, kompatibel OWL 2 QL)
    relations: list[tuple[str, str]] = field(default_factory=list)      # (relasi, IRI lain)
    annotations: list[dict] = field(default_factory=list)


@dataclass
class OntologySpec:
    iri: str | None
    version_iri: str | None
    version_info: str | None
    imports: list[str] = field(default_factory=list)
    entities: list[EntitySpec] = field(default_factory=list)


def _class_expression(graph: Graph, node) -> tuple[str, bool]:
    if isinstance(node, URIRef):
        return str(node), True
    # bentuk kompleks (mis. owl:unionOf) disimpan apa adanya dalam notasi N3
    return node.n3(graph.namespace_manager), False


def parse(content: str) -> OntologySpec:
    graph = Graph()
    graph.parse(data=content, format='turtle', publicID=BASE)

    ontology_iri = next(iter(graph.subjects(RDF.type, OWL.Ontology)), None)
    spec = OntologySpec(
        iri=str(ontology_iri) if ontology_iri else None,
        version_iri=str(graph.value(ontology_iri, OWL.versionIRI)) if ontology_iri else None,
        version_info=str(graph.value(ontology_iri, OWL.versionInfo)) if ontology_iri else None,
        imports=[str(o) for o in graph.objects(ontology_iri, OWL.imports)] if ontology_iri else [])

    by_key: dict[tuple[str, str], EntitySpec] = {}
    for rdf_type, kind in ENTITY_TYPES.items():
        for iri in graph.subjects(RDF.type, rdf_type):
            if isinstance(iri, BNode) or iri == ontology_iri:
                continue
            by_key.setdefault((str(iri), kind), EntitySpec(iri=str(iri), kind=kind))

    for key, entity in by_key.items():
        node = URIRef(entity.iri)
        entity.deprecated = bool(graph.value(node, OWL.deprecated))
        entity.functional = (node, RDF.type, OWL.FunctionalProperty) in graph
        for domain in graph.objects(node, RDFS.domain):
            entity.domains.append(_class_expression(graph, domain))
        for range_ in graph.objects(node, RDFS.range):
            iri = str(range_)
            # Kompatibilitas OWL 2 QL hanya bermakna untuk range datatype; range object property
            # berupa kelas, sehingga ditandai tidak berlaku (None).
            compatible = (owl_helper.owl2ql_compatible(iri)
                          if entity.kind == 'datatype_property' and isinstance(range_, URIRef) else None)
            entity.ranges.append((iri, compatible))
        for predicate, relation in RELATIONS.items():
            for other in graph.objects(node, predicate):
                entity.relations.append((relation, str(other)))
        for predicate in ANNOTATION_PROPERTIES:
            for value in graph.objects(node, predicate):
                entity.annotations.append({'property_iri': str(predicate), 'value': str(value),
                                           'lang': value.language if isinstance(value, Literal) else None})
    spec.entities = sorted(by_key.values(), key=lambda e: (e.iri, e.kind))
    return spec
