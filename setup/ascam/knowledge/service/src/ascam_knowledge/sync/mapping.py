"""
Penguraian ℳ (R2RML, Turtle) menjadi struktur yang dapat dianalisis.

Mengacu pada W3C R2RML: logical table (`rr:tableName` / `rr:sqlQuery`), subject map,
predicate-object map, referencing object map beserta join condition, bentuk pintasan
(`rr:subject`, `rr:predicate`, `rr:object`, `rr:class`, `rr:graph`), template, dan datatype.
Label blank node tidak stabil antar-parse, sehingga setiap predicate-object map diberi
tanda tangan struktural (P6 pada rancangan Knowledge).
"""
import hashlib
import json
from dataclasses import dataclass, field

from rdflib import RDF, BNode, Graph, Literal, Namespace, URIRef

RR = Namespace('http://www.w3.org/ns/r2rml#')
# IRI relatif (mis. <#MapPenduduk>) diselesaikan terhadap base tetap agar identitas TriplesMap
# stabil antarversi dan tidak bergantung pada lokasi berkas.
BASE = 'urn:ascam:mapping'


@dataclass
class TermMapSpec:
    position: str                       # subject | predicate | object | graph
    value_kind: str                     # constant | column | template | parent_triples_map
    value: str
    term_type: str | None = None
    datatype: str | None = None
    language: str | None = None
    columns: list[str] = field(default_factory=list)
    joins: list[dict] = field(default_factory=list)          # untuk parent_triples_map


@dataclass
class PomSpec:
    signature: str
    predicates: list[TermMapSpec] = field(default_factory=list)
    objects: list[TermMapSpec] = field(default_factory=list)
    graphs: list[TermMapSpec] = field(default_factory=list)


@dataclass
class TriplesMapSpec:
    iri: str
    logical_table_kind: str             # table | sql_query
    table_name: str | None
    sql_query: str | None
    subject: TermMapSpec | None
    classes: list[str] = field(default_factory=list)
    graphs: list[TermMapSpec] = field(default_factory=list)
    poms: list[PomSpec] = field(default_factory=list)


def template_columns(template: str) -> list[str]:
    """Nama kolom di dalam template R2RML; `\\{` dan `\\}` adalah karakter biasa."""
    columns, current, inside, escaped = [], '', False, False
    for ch in template:
        if escaped:
            if inside:
                current += ch
            escaped = False
        elif ch == '\\':
            escaped = True
        elif ch == '{':
            inside, current = True, ''
        elif ch == '}' and inside:
            columns.append(current)
            inside = False
        elif inside:
            current += ch
    return columns


def _term_map(graph: Graph, node, position: str) -> TermMapSpec | None:
    if isinstance(node, (URIRef, Literal)) and not isinstance(node, BNode):
        if (node, None, None) not in graph or position == 'predicate':
            value = str(node)
            return TermMapSpec(position=position, value_kind='constant', value=value,
                               term_type='literal' if isinstance(node, Literal) else 'iri')
    constant = graph.value(node, RR.constant)
    column = graph.value(node, RR.column)
    template = graph.value(node, RR.template)
    parent = graph.value(node, RR.parentTriplesMap)
    term_type = graph.value(node, RR.termType)
    datatype = graph.value(node, RR.datatype)
    language = graph.value(node, RR.language)

    if parent is not None:
        joins = []
        for jc in graph.objects(node, RR.joinCondition):
            joins.append({'child': str(graph.value(jc, RR.child)),
                          'parent': str(graph.value(jc, RR.parent))})
        return TermMapSpec(position=position, value_kind='parent_triples_map', value=str(parent),
                           term_type='iri', joins=joins)
    if column is not None:
        return TermMapSpec(position=position, value_kind='column', value=str(column),
                           term_type=str(term_type).rsplit('#', 1)[-1].lower() if term_type else None,
                           datatype=str(datatype) if datatype else None,
                           language=str(language) if language else None, columns=[str(column)])
    if template is not None:
        return TermMapSpec(position=position, value_kind='template', value=str(template),
                           term_type=str(term_type).rsplit('#', 1)[-1].lower() if term_type else 'iri',
                           datatype=str(datatype) if datatype else None,
                           language=str(language) if language else None,
                           columns=template_columns(str(template)))
    if constant is not None:
        return TermMapSpec(position=position, value_kind='constant', value=str(constant),
                           term_type='literal' if isinstance(constant, Literal) else 'iri',
                           datatype=str(constant.datatype) if isinstance(constant, Literal)
                           and constant.datatype else None)
    return None


def _maps(graph: Graph, subject, map_predicate, shortcut, position: str) -> list[TermMapSpec]:
    out = []
    for node in graph.objects(subject, map_predicate):
        spec = _term_map(graph, node, position)
        if spec:
            out.append(spec)
    for node in graph.objects(subject, shortcut):
        out.append(TermMapSpec(position=position, value_kind='constant', value=str(node),
                               term_type='literal' if isinstance(node, Literal) else 'iri',
                               datatype=str(node.datatype) if isinstance(node, Literal)
                               and node.datatype else None,
                               language=node.language if isinstance(node, Literal) else None))
    return out


def _signature(predicates: list[TermMapSpec], objects: list[TermMapSpec]) -> str:
    payload = [[(t.position, t.value_kind, t.value, t.term_type, t.datatype, t.language)
                for t in group] for group in (predicates, objects)]
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:32]


def parse(content: str) -> list[TriplesMapSpec]:
    graph = Graph()
    graph.parse(data=content, format='turtle', publicID=BASE)
    triples_maps = set(graph.subjects(RDF.type, RR.TriplesMap)) | set(graph.subjects(RR.logicalTable, None))

    specs = []
    for tm in sorted(triples_maps, key=str):
        logical = graph.value(tm, RR.logicalTable)
        table_name = graph.value(logical, RR.tableName) if logical is not None else None
        sql_query = graph.value(logical, RR.sqlQuery) if logical is not None else None
        subject_node = graph.value(tm, RR.subjectMap)
        subject = _term_map(graph, subject_node, 'subject') if subject_node is not None else None
        if subject is None:
            shortcut = graph.value(tm, RR.subject)
            if shortcut is not None:
                subject = TermMapSpec('subject', 'constant', str(shortcut), term_type='iri')

        classes = [str(c) for node in ([subject_node] if subject_node is not None else []) + [tm]
                   for c in graph.objects(node, RR['class'])]
        graphs = (_maps(graph, subject_node, RR.graphMap, RR.graph, 'graph')
                  if subject_node is not None else [])

        poms = []
        for pom in graph.objects(tm, RR.predicateObjectMap):
            predicates = _maps(graph, pom, RR.predicateMap, RR.predicate, 'predicate')
            objects = _maps(graph, pom, RR.objectMap, RR.object, 'object')
            poms.append(PomSpec(signature=_signature(predicates, objects), predicates=predicates,
                                objects=objects,
                                graphs=_maps(graph, pom, RR.graphMap, RR.graph, 'graph')))

        specs.append(TriplesMapSpec(
            iri=str(tm),
            logical_table_kind='sql_query' if sql_query is not None else 'table',
            table_name=str(table_name) if table_name is not None else None,
            sql_query=str(sql_query) if sql_query is not None else None,
            subject=subject, classes=sorted(set(classes)), graphs=graphs, poms=poms))
    return specs
