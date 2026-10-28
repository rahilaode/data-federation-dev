"""R2RML mapping editing: adding and removing predicate-object maps."""
import pytest
from rdflib import Graph, Namespace

from ascam_executor.artifacts import r2rml

RR = Namespace('http://www.w3.org/ns/r2rml#')
MAPPING = """# bansos mapping (header kept)
@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix bansos: <http://bansos.go.id/ontology/> .

<#MapPenerima> a rr:TriplesMap ;
    rr:logicalTable [ rr:sqlQuery "SELECT * FROM kemensos.penerima_manfaat" ] ;
    rr:subjectMap [ rr:template "http://bansos.go.id/resource/penerima/{penerima_id}" ;
                    rr:class bansos:PenerimaBansos ] ;
    rr:predicateObjectMap [ rr:predicate bansos:nik ; rr:objectMap [ rr:column "nik" ] ] ;
    rr:predicateObjectMap [ rr:predicate bansos:statusEkonomi ;
                            rr:objectMap [ rr:column "status_ekonomi" ] ] .

<#MapProgram> a rr:TriplesMap ;
    rr:logicalTable [ rr:tableName "kemensos.program_bansos" ] ;
    rr:subjectMap [ rr:template "http://bansos.go.id/resource/program/{program_id}" ] ;
    rr:predicateObjectMap [ rr:predicate bansos:nik ; rr:objectMap [ rr:column "nik_petugas" ] ] .
"""
EMAIL = 'http://bansos.go.id/ontology/email'
STATUS = 'http://bansos.go.id/ontology/statusEkonomi'
NIK = 'http://bansos.go.id/ontology/nik'


def test_add_predicate_object_map():
    result = r2rml.add_predicate_object_map(MAPPING, 'penerima_manfaat', 'email', EMAIL,
                                           datatype_iri='http://www.w3.org/2001/XMLSchema#string')
    assert EMAIL in r2rml.predicates_of(result)
    graph = Graph()
    graph.parse(data=result, format='turtle', publicID=r2rml.BASE)
    columns = {str(o) for o in graph.objects(None, RR.column)}
    assert 'email' in columns and 'nik' in columns                 # existing mappings remain
    assert result.startswith('# bansos mapping')                # header kept


def test_add_is_idempotent():
    once = r2rml.add_predicate_object_map(MAPPING, 'penerima_manfaat', 'email', EMAIL)
    assert r2rml.add_predicate_object_map(once, 'penerima_manfaat', 'email', EMAIL) == once


def test_remove_predicate_object_map():
    result, count = r2rml.remove_predicate_object_map(MAPPING, STATUS)
    assert count == 1 and STATUS not in r2rml.predicates_of(result)
    assert NIK in r2rml.predicates_of(result)
    graph = Graph()
    graph.parse(data=result, format='turtle', publicID=r2rml.BASE)
    assert 'status_ekonomi' not in {str(o) for o in graph.objects(None, RR.column)}


def test_remove_can_be_scoped_to_one_table():
    """The same predicate is used by two TriplesMaps; only the relevant one is removed."""
    result, count = r2rml.remove_predicate_object_map(MAPPING, NIK, table='penerima_manfaat')
    assert count == 1
    graph = Graph()
    graph.parse(data=result, format='turtle', publicID=r2rml.BASE)
    columns = {str(o) for o in graph.objects(None, RR.column)}
    assert 'nik_petugas' in columns and 'nik' not in columns
    whole, count_all = r2rml.remove_predicate_object_map(MAPPING, NIK)
    assert count_all == 2


def test_missing_targets_are_reported():
    assert r2rml.remove_predicate_object_map(MAPPING, 'http://example.org/does-not-exist')[1] == 0
    with pytest.raises(r2rml.MappingError, match='not found'):
        r2rml.apply_actions(MAPPING, [{'operation': 'remove_predicate_object_map',
                                       'predicate_iri': 'http://example.org/does-not-exist'}])
    with pytest.raises(r2rml.MappingError, match='TriplesMap'):
        r2rml.add_predicate_object_map(MAPPING, 'other_table', 'x', EMAIL)
    with pytest.raises(r2rml.MappingError, match='cannot be parsed'):
        r2rml.predicates_of('not turtle @@@')


def test_apply_actions_runs_plan():
    result = r2rml.apply_actions(MAPPING, [
        {'operation': 'add_predicate_object_map', 'table': 'penerima_manfaat',
         'column': 'email', 'predicate_iri': EMAIL},
        {'operation': 'remove_predicate_object_map', 'predicate_iri': STATUS}])
    predicates = r2rml.predicates_of(result)
    assert EMAIL in predicates and STATUS not in predicates


# ── append-only editing of mappings owned by ASCAM ─────────────────────────────
def test_add_is_append_only_and_preserves_file():
    result = r2rml.add_predicate_object_map(MAPPING, 'penerima_manfaat', 'email', EMAIL)
    assert result.startswith(MAPPING.rstrip('\n'))            # the original file is not rewritten
    assert '# bansos mapping (header kept)' in result
    assert '<#MapPenerima> rr:predicateObjectMap [' in result
    assert '# ascam:begin pom %s on %s#MapPenerima' % (EMAIL, r2rml.BASE) in result
    graph = Graph()
    graph.parse(data=result, format='turtle', publicID=r2rml.BASE)   # still valid Turtle
    assert EMAIL in r2rml.predicates_of(result)
    # the extra triples merge with the same TriplesMap instead of creating a new one
    assert len(set(graph.subjects(RR.logicalTable, None))) == 2


def test_ascam_owned_mapping_is_removed_as_text():
    added = r2rml.add_predicate_object_map(MAPPING, 'penerima_manfaat', 'email', EMAIL)
    removed, count = r2rml.remove_predicate_object_map(added, EMAIL, table='penerima_manfaat')
    assert count == 1
    assert removed.rstrip('\n') == MAPPING.rstrip('\n')      # back exactly as before
    without_table, count2 = r2rml.remove_predicate_object_map(added, EMAIL)
    assert count2 == 1 and EMAIL not in r2rml.predicates_of(without_table)


def test_human_written_mapping_removal_falls_back_to_rewrite():
    result, count = r2rml.remove_predicate_object_map(MAPPING, STATUS)
    assert count == 1 and STATUS not in r2rml.predicates_of(result)
    assert '@prefix bansos:' in result                        # the original prefixes are kept
    assert result.startswith('# bansos mapping')


def test_datatype_is_written_when_given():
    result = r2rml.add_predicate_object_map(MAPPING, 'penerima_manfaat', 'usia', EMAIL,
                                           datatype_iri='http://www.w3.org/2001/XMLSchema#integer')
    assert 'rr:datatype xsd:integer' in result
    graph = Graph()
    graph.parse(data=result, format='turtle', publicID=r2rml.BASE)
    assert (None, RR.datatype, None) in graph


# ── TriplesMap targeting from the plan (regression F4c) ─────────────────────────
LINK = 'urn:ascam:mapping#MapLink'
MAPPING_TWO = MAPPING + """
<#MapLink> a rr:TriplesMap ;
    rr:logicalTable [ rr:sqlQuery "SELECT penerima_id, nik FROM kemensos.penerima_manfaat" ] ;
    rr:subjectMap [ rr:template "http://bansos.go.id/resource/penerima/{penerima_id}" ] ;
    rr:predicateObjectMap [ rr:predicate bansos:memilik ;
        rr:objectMap [ rr:template "http://bansos.go.id/resource/penduduk/{nik}" ;
                       rr:termType rr:IRI ] ] .
"""


def test_triples_map_iri_from_plan_is_used():
    """Two TriplesMaps read the same table; the plan decides which one is right."""
    result = r2rml.add_predicate_object_map(MAPPING_TWO, None, 'email', EMAIL,
                                           triples_map_iri='urn:ascam:mapping#MapPenerima')
    assert '<#MapPenerima> rr:predicateObjectMap [' in result
    assert '<#MapLink> rr:predicateObjectMap [' not in result.split('# ascam:begin')[1]
    graph = Graph()
    graph.parse(data=result, format='turtle', publicID=r2rml.BASE)
    owners = {str(s) for s in graph.subjects(RR.predicateObjectMap, None)
               for p in graph.objects(s, RR.predicateObjectMap)
               if (p, RR.predicate, __import__('rdflib').URIRef(EMAIL)) in graph}
    assert owners == {'urn:ascam:mapping#MapPenerima'}


def test_unknown_triples_map_is_reported():
    with pytest.raises(r2rml.MappingError, match='does not exist in the mapping'):
        r2rml.add_predicate_object_map(MAPPING_TWO, None, 'email', EMAIL,
                                       triples_map_iri='urn:ascam:mapping#DoesNotExist')


def test_removal_is_scoped_to_the_named_triples_map():
    two = r2rml.add_predicate_object_map(MAPPING_TWO, None, 'email', EMAIL,
                                         triples_map_iri='urn:ascam:mapping#MapPenerima')
    two = r2rml.add_predicate_object_map(two, None, 'email', EMAIL, triples_map_iri=LINK)
    assert two.count('# ascam:begin pom') == 2
    partial, count = r2rml.remove_predicate_object_map(two, EMAIL, triples_map_iri=LINK)
    assert count == 1 and partial.count('# ascam:begin pom') == 1
    assert 'MapPenerima' in partial.split('# ascam:begin')[1]


# ── rewriting the logical table when a column is dropped (regression A002) ──────
MAPPING_EXPLICIT = """@prefix rr: <http://www.w3.org/ns/r2rml#> .
@prefix bansos: <http://bansos.go.id/ontology/> .

<#MapProgram> a rr:TriplesMap ;
    rr:logicalTable [ rr:sqlQuery "SELECT program_id, nama_program, tipe_program, nominal FROM kemensos.program_bansos" ] ;
    rr:subjectMap [ rr:template "http://bansos.go.id/resource/program/{program_id}" ] ;
    rr:predicateObjectMap [ rr:predicate bansos:tipeProgram ;
                            rr:objectMap [ rr:column "tipe_program" ] ] .
"""
TIPE_PROGRAM = 'http://bansos.go.id/ontology/tipeProgram'
MAP_PROGRAM = 'urn:ascam:mapping#MapProgram'


def logical_query(text: str) -> str:
    graph = Graph()
    graph.parse(data=text, format='turtle', publicID=r2rml.BASE)
    logical = graph.value(__import__('rdflib').URIRef(MAP_PROGRAM), RR.logicalTable)
    return str(graph.value(logical, RR.sqlQuery))


def test_dropped_column_is_removed_from_explicit_select():
    result, changed = r2rml.rewrite_logical_table(MAPPING_EXPLICIT, MAP_PROGRAM, 'tipe_program')
    assert changed
    query = logical_query(result).lower()
    assert 'tipe_program' not in query
    assert all(k in query for k in ('program_id', 'nama_program', 'nominal'))
    assert 'kemensos.program_bansos' in query


def test_rewrite_is_noop_when_column_not_projected():
    _, changed = r2rml.rewrite_logical_table(MAPPING_EXPLICIT, MAP_PROGRAM, 'other_column')
    assert changed is False
    star = MAPPING_EXPLICIT.replace(
        'SELECT program_id, nama_program, tipe_program, nominal FROM kemensos.program_bansos',
        'SELECT * FROM kemensos.program_bansos')
    _, changed = r2rml.rewrite_logical_table(star, MAP_PROGRAM, 'tipe_program')
    assert changed is False                      # SELECT * adjusts by itself


def test_rewrite_refuses_when_nothing_would_remain():
    single = MAPPING_EXPLICIT.replace(
        'SELECT program_id, nama_program, tipe_program, nominal FROM kemensos.program_bansos',
        'SELECT tipe_program FROM kemensos.program_bansos')
    with pytest.raises(r2rml.MappingError, match='cannot be edited automatically'):
        r2rml.rewrite_logical_table(single, MAP_PROGRAM, 'tipe_program')


def test_drop_plan_rewrites_query_and_removes_mapping():
    result = r2rml.apply_actions(MAPPING_EXPLICIT, [
        {'operation': 'remove_predicate_object_map', 'predicate_iri': TIPE_PROGRAM,
         'triples_map_iri': MAP_PROGRAM},
        {'operation': 'rewrite_logical_table', 'triples_map_iri': MAP_PROGRAM,
         'column': 'tipe_program'}])
    assert TIPE_PROGRAM not in r2rml.predicates_of(result)
    assert 'tipe_program' not in logical_query(result).lower()
