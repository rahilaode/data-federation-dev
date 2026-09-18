"""Penyuntingan ontologi: append-only, komentar utuh, deprecation, idempotensi."""
from datetime import datetime, timezone

import pytest
from rdflib import Graph

from ascam_executor.artifacts import ontology

ONT = """# Ontologi bansos
# Komentar penting yang ditulis manusia.
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix bansos: <http://bansos.go.id/ontology/> .

<http://bansos.go.id/ontology> a owl:Ontology .

# Kelas
bansos:PenerimaBansos a owl:Class .
bansos:tipeProgram a owl:DatatypeProperty ; rdfs:range xsd:string .
"""
WAKTU = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
EMAIL = 'http://bansos.go.id/ontology/email'
TIPE = 'http://bansos.go.id/ontology/tipeProgram'


def test_add_property_is_append_only_and_keeps_comments():
    hasil = ontology.add_datatype_property(
        ONT, EMAIL, domain='http://bansos.go.id/ontology/PenerimaBansos',
        range_iri='http://www.w3.org/2001/XMLSchema#string', label='email',
        komentar='Ditambahkan otomatis.', waktu=WAKTU)
    assert hasil.startswith(ONT.split('@prefix')[0])           # komentar pembuka utuh
    assert '# Kelas' in hasil and 'Komentar penting' in hasil
    assert hasil.index('bansos:tipeProgram') < hasil.index('bansos:email')   # ditambahkan di akhir
    graf = Graph()
    graf.parse(data=hasil, format='turtle')                    # tetap Turtle yang sah
    assert (None, None, None) in graf
    assert 'dcterms:created "2026-09-18T12:00:00+00:00"' in hasil
    assert '@prefix dcterms:' in hasil and '@prefix skos:' in hasil


def test_add_property_is_idempotent():
    sekali = ontology.add_datatype_property(ONT, EMAIL, domain=None, range_iri='xsd:string',
                                            label='email', komentar='x', waktu=WAKTU)
    dua_kali = ontology.add_datatype_property(sekali, EMAIL, domain=None, range_iri='xsd:string',
                                              label='email', komentar='x', waktu=WAKTU)
    assert sekali == dua_kali


def test_deprecate_marks_without_removing():
    hasil = ontology.deprecate_property(ONT, TIPE, alasan='Kolom dihapus.', waktu=WAKTU)
    assert 'bansos:tipeProgram a owl:DatatypeProperty' in hasil      # deklarasi tetap ada
    assert ontology.is_deprecated(hasil, TIPE)
    assert not ontology.is_deprecated(ONT, TIPE)
    graf = Graph()
    graf.parse(data=hasil, format='turtle')


def test_apply_actions_handles_plan_actions():
    hasil = ontology.apply_actions(ONT, [
        {'operation': 'add_datatype_property', 'iri': EMAIL, 'column': 'email',
         'domain': ['http://bansos.go.id/ontology/PenerimaBansos']},
        {'operation': 'keep_property', 'predicate_iri': 'http://bansos.go.id/ontology/nik'},
        {'operation': 'deprecate_property', 'predicate_iri': TIPE}], waktu=WAKTU)
    assert 'bansos:email' in hasil and ontology.is_deprecated(hasil, TIPE)
    assert 'Ditambahkan otomatis ASCAM untuk kolom sumber email.' in hasil
    with pytest.raises(ontology.OntologyError, match='tidak dikenal'):
        ontology.apply_actions(ONT, [{'operation': 'hapus_kelas'}])


def test_prefixes_are_added_only_when_missing():
    hasil = ontology.ensure_prefixes(ONT, ontology.PREFIXES)
    assert hasil.count('@prefix owl:') == 1 and hasil.count('@prefix dcterms:') == 1
    assert ontology.ensure_prefixes(hasil, ontology.PREFIXES) == hasil
