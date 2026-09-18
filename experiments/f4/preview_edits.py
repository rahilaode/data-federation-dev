"""
F4b — Pratinjau suntingan artefak terhadap berkas OBDF yang sebenarnya.

Skrip ini TIDAK menulis apa pun: ia membaca artefak dari working copy, menerapkan dua rencana
contoh di memori, lalu menampilkan diff-nya. Tujuannya melihat bentuk suntingan sebelum
Executor menerapkannya.

  P-001  ADD kolom `email` pada kemensos.penerima_manfaat
  P-002  DROP kolom `tipe_program` pada kemensos.program_bansos

Dijalankan dari host: `python3 experiments/f4/preview_edits.py`
(memerlukan paket executor: pip install -e setup/ascam/executor/service, atau jalankan
lewat kontainer seperti dijelaskan README executor).
"""
import difflib
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'setup/ascam/executor/service/src'))

from ascam_executor.artifacts import ontology, r2rml, vdb  # noqa: E402

VDB_PATH = ROOT / 'setup/data-federation/deployments/government-vdb.xml'
MAPPING_PATH = ROOT / 'setup/vkg-system/config/mapping.ttl'
ONTOLOGY_PATH = ROOT / 'setup/vkg-system/config/ontology_file.ttl'
WAKTU = datetime(2026, 9, 18, 12, 0, tzinfo=timezone.utc)
EMAIL = 'http://bansos.go.id/ontology/email'
TIPE = 'http://bansos.go.id/ontology/tipeProgram'

RENCANA_ADD = {
    'vdb': [{'operation': 'add_column', 'model': 'kemensos', 'table': 'penerima_manfaat',
             'column': 'email', 'column_type': 'string(100)'}],
    'ontology': [{'operation': 'add_datatype_property', 'iri': EMAIL, 'column': 'email',
                  'domain': ['http://bansos.go.id/ontology/PenerimaBansos']}],
    'r2rml': [{'operation': 'add_predicate_object_map', 'table': 'penerima_manfaat',
               'column': 'email', 'predicate_iri': EMAIL}],
}
RENCANA_DROP = {
    'vdb': [{'operation': 'drop_column', 'model': 'kemensos', 'table': 'program_bansos',
             'column': 'tipe_program'}],
    'ontology': [{'operation': 'deprecate_property', 'predicate_iri': TIPE}],
    'r2rml': [{'operation': 'remove_predicate_object_map', 'predicate_iri': TIPE}],
}


def diff(judul: str, lama: str, baru: str, batas: int = 24) -> None:
    baris = list(difflib.unified_diff(lama.splitlines(), baru.splitlines(),
                                      fromfile=f'{judul} (sebelum)', tofile=f'{judul} (sesudah)',
                                      lineterm='', n=1))
    print(f'\n--- {judul}: {len(baris)} baris diff')
    for item in baris[:batas]:
        print('   ' + item)
    if len(baris) > batas:
        print(f'   ... ({len(baris) - batas} baris lagi)')


def jalankan(nama: str, rencana: dict) -> None:
    print(f'\n================ {nama} ================')
    xml = VDB_PATH.read_text()
    mapping = MAPPING_PATH.read_text()
    ont = ONTOLOGY_PATH.read_text()

    xml_baru, statements = vdb.apply_actions(xml, rencana['vdb'], new_version=vdb.next_version(xml))
    print(f'  VDB versi {vdb.version_of(xml)} -> {vdb.version_of(xml_baru)}')
    for statement in statements:
        print(f'    {statement}')
    diff('VDB', xml, xml_baru, batas=12)

    ont_baru = ontology.apply_actions(ont, rencana['ontology'], waktu=WAKTU)
    diff('ontologi', ont, ont_baru, batas=18)

    mapping_baru = r2rml.apply_actions(mapping, rencana['r2rml'])
    diff('mapping (R2RML)', mapping, mapping_baru, batas=16)
    print(f"  predikat: {len(r2rml.predicates_of(mapping))} -> "
          f'{len(r2rml.predicates_of(mapping_baru))}')


if __name__ == '__main__':
    jalankan('P-001 ADD kolom email', RENCANA_ADD)
    jalankan('P-002 DROP kolom tipe_program', RENCANA_DROP)
    print('\nTidak ada berkas yang ditulis: pratinjau saja.')
