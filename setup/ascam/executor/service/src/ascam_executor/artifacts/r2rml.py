"""
Penyuntingan mapping R2RML (ℳ).

Berbeda dari 𝒯, penghapusan predicate-object map tidak dapat dilakukan secara append-only,
sehingga berkas ditulis ulang dari graf RDF (rdflib). Konsekuensinya komentar di dalam berkas
hilang; header berkas (komentar pembuka dan urutan prefix) dipertahankan, dan isi asli setiap
versi tetap tersimpan di Knowledge (ADR-0012). Trade-off ini dicatat pada ADR-0019.
"""
from rdflib import RDF, BNode, Graph, Literal, Namespace, URIRef

RR = Namespace('http://www.w3.org/ns/r2rml#')
BASE = 'urn:ascam:mapping'


class MappingError(Exception):
    pass


def _graph(text: str) -> Graph:
    graph = Graph()
    try:
        graph.parse(data=text, format='turtle', publicID=BASE)
    except Exception as exc:                        # noqa: BLE001
        raise MappingError(f'mapping tidak dapat diurai: {exc}') from exc
    return graph


def _header(text: str) -> str:
    """Komentar pembuka berkas (sebelum triple pertama) dipertahankan apa adanya."""
    baris = []
    for item in text.splitlines():
        potongan = item.strip()
        if potongan.startswith('#') or not potongan:
            baris.append(item)
            continue
        break
    return '\n'.join(baris).rstrip() + '\n\n' if baris else ''


def _serialize(graph: Graph, text_asal: str) -> str:
    isi = graph.serialize(format='turtle', base=BASE)
    return _header(text_asal) + isi


def triples_map_for_table(graph: Graph, table: str) -> URIRef | None:
    """TriplesMap yang membaca tabel tertentu (lewat rr:tableName atau rr:sqlQuery)."""
    target = table.lower()
    for tm in set(graph.subjects(RR.logicalTable, None)):
        logical = graph.value(tm, RR.logicalTable)
        nama = graph.value(logical, RR.tableName)
        kueri = graph.value(logical, RR.sqlQuery)
        if nama is not None and str(nama).replace('"', '').lower().endswith(target):
            return tm
        if kueri is not None and target in str(kueri).lower():
            return tm
    return None


def add_predicate_object_map(text: str, table: str, column: str, predicate_iri: str,
                             datatype_iri: str | None = None) -> str:
    """Menambahkan pemetaan kolom -> predikat pada TriplesMap tabel tersebut (pola P-001)."""
    graph = _graph(text)
    tm = triples_map_for_table(graph, table)
    if tm is None:
        raise MappingError(f'tidak ada TriplesMap yang membaca tabel {table}')
    for pom in graph.objects(tm, RR.predicateObjectMap):
        if (pom, RR.predicate, URIRef(predicate_iri)) in graph:
            return text                              # sudah ada: idempoten
    pom = BNode()
    object_map = BNode()
    graph.add((tm, RR.predicateObjectMap, pom))
    graph.add((pom, RDF.type, RR.PredicateObjectMap))
    graph.add((pom, RR.predicate, URIRef(predicate_iri)))
    graph.add((pom, RR.objectMap, object_map))
    graph.add((object_map, RR.column, Literal(column)))
    if datatype_iri:
        graph.add((object_map, RR.datatype, URIRef(datatype_iri)))
    return _serialize(graph, text)


def remove_predicate_object_map(text: str, predicate_iri: str,
                                table: str | None = None) -> tuple[str, int]:
    """Menghapus predicate-object map untuk predikat tertentu (pola P-002)."""
    graph = _graph(text)
    sasaran = URIRef(predicate_iri)
    terhapus = 0
    for tm in set(graph.subjects(RR.predicateObjectMap, None)):
        if table is not None and triples_map_for_table(graph, table) != tm:
            continue
        for pom in list(graph.objects(tm, RR.predicateObjectMap)):
            if (pom, RR.predicate, sasaran) not in graph:
                continue
            for objek in list(graph.objects(pom, RR.objectMap)):
                graph.remove((objek, None, None))
            graph.remove((pom, None, None))
            graph.remove((tm, RR.predicateObjectMap, pom))
            terhapus += 1
    if terhapus == 0:
        return text, 0
    return _serialize(graph, text), terhapus


def predicates_of(text: str) -> set[str]:
    graph = _graph(text)
    return {str(o) for o in graph.objects(None, RR.predicate)}


def apply_actions(text: str, actions: list[dict]) -> str:
    hasil = text
    for action in actions:
        operation = action['operation']
        if operation == 'add_predicate_object_map':
            hasil = add_predicate_object_map(hasil, action['table'], action['column'],
                                             action['predicate_iri'], action.get('datatype_iri'))
        elif operation == 'remove_predicate_object_map':
            hasil, jumlah = remove_predicate_object_map(hasil, action['predicate_iri'],
                                                        action.get('table'))
            if jumlah == 0:
                raise MappingError(f"predikat {action['predicate_iri']} tidak ditemukan di mapping")
        else:
            raise MappingError(f'tindakan mapping {operation} tidak dikenal')
    return hasil
