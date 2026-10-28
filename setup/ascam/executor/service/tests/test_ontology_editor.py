"""Ontology editing: append-only, comments intact, deprecation, idempotency."""
from datetime import datetime, timezone

import pytest
from rdflib import Graph

from ascam_executor.artifacts import ontology

ONT = """# bansos ontology
# An important comment written by a person.
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix bansos: <http://bansos.go.id/ontology/> .

<http://bansos.go.id/ontology> a owl:Ontology .

# Classes
bansos:PenerimaBansos a owl:Class .
bansos:tipeProgram a owl:DatatypeProperty ; rdfs:range xsd:string .
"""
WHEN = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
EMAIL = 'http://bansos.go.id/ontology/email'
TIPE_PROGRAM = 'http://bansos.go.id/ontology/tipeProgram'


def test_add_property_is_append_only_and_keeps_comments():
    result = ontology.add_datatype_property(
        ONT, EMAIL, domain='http://bansos.go.id/ontology/PenerimaBansos',
        range_iri='http://www.w3.org/2001/XMLSchema#string', label='email',
        comment='Added automatically.', when=WHEN)
    assert result.startswith(ONT.split('@prefix')[0])           # leading comments intact
    assert '# Classes' in result and 'An important comment' in result
    assert result.index('bansos:tipeProgram') < result.index('bansos:email')   # appended at the end
    graph = Graph()
    graph.parse(data=result, format='turtle')                    # still valid Turtle
    assert (None, None, None) in graph
    assert 'dcterms:created "2026-09-18T12:00:00+00:00"' in result
    assert '@prefix dcterms:' in result and '@prefix skos:' in result


def test_add_property_is_idempotent():
    once = ontology.add_datatype_property(ONT, EMAIL, domain=None, range_iri='xsd:string',
                                            label='email', comment='x', when=WHEN)
    twice = ontology.add_datatype_property(once, EMAIL, domain=None, range_iri='xsd:string',
                                              label='email', comment='x', when=WHEN)
    assert once == twice


def test_deprecate_marks_without_removing():
    result = ontology.deprecate_property(ONT, TIPE_PROGRAM, reason='Column dropped.', when=WHEN)
    assert 'bansos:tipeProgram a owl:DatatypeProperty' in result      # the declaration remains
    assert ontology.is_deprecated(result, TIPE_PROGRAM)
    assert not ontology.is_deprecated(ONT, TIPE_PROGRAM)
    graph = Graph()
    graph.parse(data=result, format='turtle')


def test_apply_actions_handles_plan_actions():
    result = ontology.apply_actions(ONT, [
        {'operation': 'add_datatype_property', 'iri': EMAIL, 'column': 'email',
         'domain': ['http://bansos.go.id/ontology/PenerimaBansos']},
        {'operation': 'keep_property', 'predicate_iri': 'http://bansos.go.id/ontology/nik'},
        {'operation': 'deprecate_property', 'predicate_iri': TIPE_PROGRAM}], when=WHEN)
    assert 'bansos:email' in result and ontology.is_deprecated(result, TIPE_PROGRAM)
    assert 'Added automatically by ASCAM for source column email.' in result
    with pytest.raises(ontology.OntologyError, match='unknown'):
        ontology.apply_actions(ONT, [{'operation': 'drop_class'}])


def test_prefixes_are_added_only_when_missing():
    result = ontology.ensure_prefixes(ONT, ontology.PREFIXES)
    assert result.count('@prefix owl:') == 1 and result.count('@prefix dcterms:') == 1
    assert ontology.ensure_prefixes(result, ontology.PREFIXES) == result
