"""
Penyuntingan ontologi (𝒯) secara append-only.

Alasan (ADR-0010, invarian kepemilikan P6): ASCAM hanya menambahkan triple, dan hanya
menghapus yang ia tambahkan sendiri. Dengan menulis blok bertanda di akhir berkas, komentar
dan tata letak bagian yang ditulis manusia tetap utuh — sifat yang sudah terbukti pada
iterasi pertama.

Property baru ditulis dengan kosakata umum: `rdfs:label`, `rdfs:comment`, `rdfs:domain`,
`rdfs:range`, `dcterms:created`, dan `skos:changeNote`. Deprecation memakai `owl:deprecated`
(OWL 2), bukan penghapusan, sehingga kueri lama tidak langsung rusak.
"""
from datetime import datetime, timezone

from .turtle_text import PENANDA_BLOK, append_block, ensure_prefixes, prefix_map, shorten

PREFIXES = {
    'owl': 'http://www.w3.org/2002/07/owl#',
    'rdfs': 'http://www.w3.org/2000/01/rdf-schema#',
    'xsd': 'http://www.w3.org/2001/XMLSchema#',
    'dcterms': 'http://purl.org/dc/terms/',
    'skos': 'http://www.w3.org/2004/02/skos/core#',
}
PENANDA = PENANDA_BLOK


class OntologyError(Exception):
    pass






def is_declared(text: str, iri: str) -> bool:
    prefixes = prefix_map(text)
    pendek = shorten(iri, prefixes)
    for baris in text.splitlines():
        potongan = baris.strip()
        if potongan.startswith((pendek + ' ', pendek + '\t')) and 'owl:DatatypeProperty' in potongan:
            return True
    return False


def add_datatype_property(text: str, iri: str, *, domain: str | None, range_iri: str,
                          label: str, komentar: str, waktu: datetime | None = None,
                          catatan_perubahan: str | None = None) -> str:
    """Menambahkan DatatypeProperty baru (pola P-001); idempoten bila sudah dideklarasikan."""
    if is_declared(text, iri):
        return text
    waktu = waktu or datetime.now(timezone.utc)
    hasil = ensure_prefixes(text, PREFIXES)
    prefixes = prefix_map(hasil)
    baris = [f'\n{shorten(iri, prefixes)} a owl:DatatypeProperty ;']
    if domain:
        baris.append(f'    rdfs:domain {shorten(domain, prefixes)} ;')
    baris.append(f'    rdfs:range {shorten(range_iri, prefixes)} ;')
    baris.append(f'    rdfs:label "{label}" ;')
    baris.append(f'    rdfs:comment "{komentar}" ;')
    baris.append(f'    dcterms:created "{waktu.isoformat()}"^^xsd:dateTime ;')
    baris.append(f'    skos:changeNote "{catatan_perubahan or komentar}" .')
    return append_block(hasil, f'property {iri}', '\n'.join(baris))


def deprecate_property(text: str, iri: str, *, alasan: str,
                       waktu: datetime | None = None) -> str:
    """Menandai property sebagai deprecated (pola P-002), tanpa menghapus deklarasinya."""
    waktu = waktu or datetime.now(timezone.utc)
    hasil = ensure_prefixes(text, PREFIXES)
    prefixes = prefix_map(hasil)
    pendek = shorten(iri, prefixes)
    blok = (f'\n{pendek} owl:deprecated true ;\n'
            f'    dcterms:modified "{waktu.isoformat()}"^^xsd:dateTime ;\n'
            f'    skos:changeNote "{alasan}" .\n')
    return append_block(hasil, f'deprecate {iri}', blok)


def is_deprecated(text: str, iri: str) -> bool:
    prefixes = prefix_map(text)
    pendek = shorten(iri, prefixes)
    for baris in text.splitlines():
        potongan = baris.strip()
        if potongan.startswith(pendek) and 'owl:deprecated' in potongan and 'true' in potongan:
            return True
    return False


def apply_actions(text: str, actions: list[dict], waktu: datetime | None = None) -> str:
    hasil = text
    for action in actions:
        operation = action['operation']
        if operation == 'add_datatype_property':
            domain = (action.get('domain') or [None])[0]
            kolom = action.get('column')
            hasil = add_datatype_property(
                hasil, action['iri'], domain=domain,
                range_iri=action.get('range_iri') or f"{PREFIXES['xsd']}string",
                label=action.get('label') or kolom or action['iri'].rsplit('/', 1)[-1],
                komentar=action.get('comment')
                or f'Ditambahkan otomatis ASCAM untuk kolom sumber {kolom}.',
                waktu=waktu)
        elif operation == 'deprecate_property':
            hasil = deprecate_property(hasil, action['predicate_iri'],
                                       alasan=action.get('alasan')
                                       or 'Kolom sumber dihapus; property ditandai usang oleh ASCAM.',
                                       waktu=waktu)
        elif operation == 'keep_property':
            continue                                    # tidak ada perubahan pada 𝒯
        else:
            raise OntologyError(f'tindakan ontologi {operation} tidak dikenal')
    return hasil
