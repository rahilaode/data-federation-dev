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

PREFIXES = {
    'owl': 'http://www.w3.org/2002/07/owl#',
    'rdfs': 'http://www.w3.org/2000/01/rdf-schema#',
    'xsd': 'http://www.w3.org/2001/XMLSchema#',
    'dcterms': 'http://purl.org/dc/terms/',
    'skos': 'http://www.w3.org/2004/02/skos/core#',
}
PENANDA = '# ─── blok terkelola ASCAM (jangan disunting manual) ───'


class OntologyError(Exception):
    pass


def _prefix_map(text: str) -> dict[str, str]:
    mapping = {}
    for baris in text.splitlines():
        potongan = baris.strip()
        if potongan.lower().startswith('@prefix'):
            bagian = potongan.split()
            if len(bagian) >= 3:
                mapping[bagian[2].strip('<>')] = bagian[1].rstrip(':')
    return mapping


def _singkat(iri: str, prefixes: dict[str, str]) -> str:
    for namespace, prefix in prefixes.items():
        if iri.startswith(namespace):
            sisa = iri[len(namespace):]
            if sisa and all(c.isalnum() or c in '_-' for c in sisa):
                return f'{prefix}:{sisa}'
    return f'<{iri}>'


def ensure_prefixes(text: str, dibutuhkan: dict[str, str]) -> str:
    """Menambahkan deklarasi @prefix yang belum ada, tepat setelah deklarasi terakhir."""
    ada = _prefix_map(text)
    tambahan = [f'@prefix {prefix}: <{namespace}> .'
                for prefix, namespace in dibutuhkan.items() if namespace not in ada]
    if not tambahan:
        return text
    baris = text.splitlines()
    terakhir = max((i for i, b in enumerate(baris) if b.strip().lower().startswith('@prefix')),
                   default=-1)
    baris[terakhir + 1:terakhir + 1] = tambahan
    return '\n'.join(baris) + ('\n' if text.endswith('\n') else '')


def _append(text: str, blok: str) -> str:
    dasar = text if text.endswith('\n') else text + '\n'
    if PENANDA not in dasar:
        dasar += f'\n{PENANDA}\n'
    return dasar + blok


def is_declared(text: str, iri: str) -> bool:
    prefixes = _prefix_map(text)
    pendek = _singkat(iri, prefixes)
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
    prefixes = _prefix_map(hasil)
    baris = [f'\n{_singkat(iri, prefixes)} a owl:DatatypeProperty ;']
    if domain:
        baris.append(f'    rdfs:domain {_singkat(domain, prefixes)} ;')
    baris.append(f'    rdfs:range {_singkat(range_iri, prefixes)} ;')
    baris.append(f'    rdfs:label "{label}" ;')
    baris.append(f'    rdfs:comment "{komentar}" ;')
    baris.append(f'    dcterms:created "{waktu.isoformat()}"^^xsd:dateTime ;')
    baris.append(f'    skos:changeNote "{catatan_perubahan or komentar}" .')
    return _append(hasil, '\n'.join(baris) + '\n')


def deprecate_property(text: str, iri: str, *, alasan: str,
                       waktu: datetime | None = None) -> str:
    """Menandai property sebagai deprecated (pola P-002), tanpa menghapus deklarasinya."""
    waktu = waktu or datetime.now(timezone.utc)
    hasil = ensure_prefixes(text, PREFIXES)
    prefixes = _prefix_map(hasil)
    pendek = _singkat(iri, prefixes)
    blok = (f'\n{pendek} owl:deprecated true ;\n'
            f'    dcterms:modified "{waktu.isoformat()}"^^xsd:dateTime ;\n'
            f'    skos:changeNote "{alasan}" .\n')
    return _append(hasil, blok)


def is_deprecated(text: str, iri: str) -> bool:
    prefixes = _prefix_map(text)
    pendek = _singkat(iri, prefixes)
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
