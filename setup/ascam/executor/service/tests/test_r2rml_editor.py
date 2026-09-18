"""Penyuntingan mapping R2RML: penambahan dan penghapusan predicate-object map."""
import pytest
from rdflib import Graph, Namespace

from ascam_executor.artifacts import r2rml

RR = Namespace('http://www.w3.org/ns/r2rml#')
MAPPING = """# Mapping bansos (header dipertahankan)
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
    hasil = r2rml.add_predicate_object_map(MAPPING, 'penerima_manfaat', 'email', EMAIL,
                                           datatype_iri='http://www.w3.org/2001/XMLSchema#string')
    assert EMAIL in r2rml.predicates_of(hasil)
    graf = Graph()
    graf.parse(data=hasil, format='turtle', publicID=r2rml.BASE)
    kolom = {str(o) for o in graf.objects(None, RR.column)}
    assert 'email' in kolom and 'nik' in kolom                 # pemetaan lama tetap ada
    assert hasil.startswith('# Mapping bansos')                # header dipertahankan


def test_add_is_idempotent():
    sekali = r2rml.add_predicate_object_map(MAPPING, 'penerima_manfaat', 'email', EMAIL)
    assert r2rml.add_predicate_object_map(sekali, 'penerima_manfaat', 'email', EMAIL) == sekali


def test_remove_predicate_object_map():
    hasil, jumlah = r2rml.remove_predicate_object_map(MAPPING, STATUS)
    assert jumlah == 1 and STATUS not in r2rml.predicates_of(hasil)
    assert NIK in r2rml.predicates_of(hasil)
    graf = Graph()
    graf.parse(data=hasil, format='turtle', publicID=r2rml.BASE)
    assert 'status_ekonomi' not in {str(o) for o in graf.objects(None, RR.column)}


def test_remove_can_be_scoped_to_one_table():
    """Predikat yang sama dipakai dua TriplesMap; hanya yang relevan dihapus."""
    hasil, jumlah = r2rml.remove_predicate_object_map(MAPPING, NIK, table='penerima_manfaat')
    assert jumlah == 1
    graf = Graph()
    graf.parse(data=hasil, format='turtle', publicID=r2rml.BASE)
    kolom = {str(o) for o in graf.objects(None, RR.column)}
    assert 'nik_petugas' in kolom and 'nik' not in kolom
    semua, jumlah_semua = r2rml.remove_predicate_object_map(MAPPING, NIK)
    assert jumlah_semua == 2


def test_missing_targets_are_reported():
    assert r2rml.remove_predicate_object_map(MAPPING, 'http://contoh/tidak-ada')[1] == 0
    with pytest.raises(r2rml.MappingError, match='tidak ditemukan'):
        r2rml.apply_actions(MAPPING, [{'operation': 'remove_predicate_object_map',
                                       'predicate_iri': 'http://contoh/tidak-ada'}])
    with pytest.raises(r2rml.MappingError, match='TriplesMap'):
        r2rml.add_predicate_object_map(MAPPING, 'tabel_asing', 'x', EMAIL)
    with pytest.raises(r2rml.MappingError, match='tidak dapat diurai'):
        r2rml.predicates_of('bukan turtle @@@')


def test_apply_actions_runs_plan():
    hasil = r2rml.apply_actions(MAPPING, [
        {'operation': 'add_predicate_object_map', 'table': 'penerima_manfaat',
         'column': 'email', 'predicate_iri': EMAIL},
        {'operation': 'remove_predicate_object_map', 'predicate_iri': STATUS}])
    predikat = r2rml.predicates_of(hasil)
    assert EMAIL in predikat and STATUS not in predikat


# ── penyuntingan append-only untuk pemetaan milik ASCAM ─────────────────────────
def test_add_is_append_only_and_preserves_file():
    hasil = r2rml.add_predicate_object_map(MAPPING, 'penerima_manfaat', 'email', EMAIL)
    assert hasil.startswith(MAPPING.rstrip('\n'))            # berkas asli tidak ditulis ulang
    assert '# Mapping bansos (header dipertahankan)' in hasil
    assert '<#MapPenerima> rr:predicateObjectMap [' in hasil
    assert '# ascam:mulai pom %s pada penerima_manfaat' % EMAIL in hasil
    graf = Graph()
    graf.parse(data=hasil, format='turtle', publicID=r2rml.BASE)   # tetap Turtle yang sah
    assert EMAIL in r2rml.predicates_of(hasil)
    # triple tambahan menyatu dengan TriplesMap yang sama, bukan membuat TriplesMap baru
    assert len(set(graf.subjects(RR.logicalTable, None))) == 2


def test_ascam_owned_mapping_is_removed_as_text():
    ditambah = r2rml.add_predicate_object_map(MAPPING, 'penerima_manfaat', 'email', EMAIL)
    dihapus, jumlah = r2rml.remove_predicate_object_map(ditambah, EMAIL, table='penerima_manfaat')
    assert jumlah == 1
    assert dihapus.rstrip('\n') == MAPPING.rstrip('\n')      # kembali persis seperti semula
    tanpa_tabel, jumlah2 = r2rml.remove_predicate_object_map(ditambah, EMAIL)
    assert jumlah2 == 1 and EMAIL not in r2rml.predicates_of(tanpa_tabel)


def test_human_written_mapping_removal_falls_back_to_rewrite():
    hasil, jumlah = r2rml.remove_predicate_object_map(MAPPING, STATUS)
    assert jumlah == 1 and STATUS not in r2rml.predicates_of(hasil)
    assert '@prefix bansos:' in hasil                        # prefix asal dipertahankan
    assert hasil.startswith('# Mapping bansos')


def test_datatype_is_written_when_given():
    hasil = r2rml.add_predicate_object_map(MAPPING, 'penerima_manfaat', 'usia', EMAIL,
                                           datatype_iri='http://www.w3.org/2001/XMLSchema#integer')
    assert 'rr:datatype xsd:integer' in hasil
    graf = Graph()
    graf.parse(data=hasil, format='turtle', publicID=r2rml.BASE)
    assert (None, RR.datatype, None) in graf
