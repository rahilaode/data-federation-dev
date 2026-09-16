"""
ASCAM TTL Updater
=================
Memodifikasi file ontologi Turtle (.ttl) menggunakan rdflib.

Operasi:
  add_datatype_property()  → P-001 ADD COLUMN
  deprecate_property()     → P-002 DROP COLUMN (tidak hapus fisik)
  P-003 RENAME COLUMN tidak butuh update TTL (semantic preservation)

Strategi penulisan (append-only):
  RDFLib dipakai untuk MEMBACA graf (pemeriksaan idempotensi dan validasi),
  tetapi berkas TIDAK diserialisasi ulang. Serialisasi ulang oleh RDFLib
  menyusun ulang seluruh berkas dan membuang semua komentar, karena komentar
  bukan bagian dari graf RDF (terbukti pada skenario A001: 51 baris komentar
  hilang meskipun perubahan semantiknya hanya +4 triple).
  Karena P-001 dan P-002 hanya MENAMBAH triple, triple baru cukup ditambahkan
  di akhir berkas sebagai blok bertanda. Turtle mengizinkan subjek yang sama
  dideklarasikan lebih dari sekali, sehingga hasilnya tetap valid dan
  sekaligus menjadi jejak audit perubahan.
"""

import re
import logging
from pathlib import Path

from datetime import datetime, timezone

from rdflib import Graph, Namespace, Literal, URIRef, RDF, RDFS, OWL, XSD as RDFXSD
from config.settings import ONTOLOGY_PREFIX, ONTOLOGY_BASE
from executor.artifact_store import atomic_write

log = logging.getLogger('ascam.ttl')

BANSOS_NS = Namespace(ONTOLOGY_BASE)

_XSD_MAP = {
    'xsd:string'  : RDFXSD.string,
    'xsd:integer' : RDFXSD.integer,
    'xsd:decimal' : RDFXSD.decimal,
    'xsd:float'   : RDFXSD.float,
    'xsd:double'  : RDFXSD.double,
    'xsd:boolean' : RDFXSD.boolean,
    'xsd:date'    : RDFXSD.date,
    'xsd:dateTime': RDFXSD.dateTime,
}


class TTLUpdater:

    def __init__(self, path: str):
        self.path   = Path(path)
        self.g      = Graph()
        self._raw   = ''
        self._added: list[tuple] = []
        self._load()

    def _load(self):
        self._raw = self.path.read_text(encoding='utf-8')
        self.g.parse(data=self._raw, format='turtle')
        self._prefixes = _declared_prefixes(self._raw)
        self.g.bind('bansos', BANSOS_NS)
        self.g.bind('owl',    OWL)
        self.g.bind('rdfs',   RDFS)
        self.g.bind('xsd',    RDFXSD)
        log.info('[TTL] Loaded %d triples dari %s', len(self.g), self.path.name)

    def _uri(self, name: str):
        """'bansos:email' atau 'email' → URIRef"""
        local = name.replace(f'{ONTOLOGY_PREFIX}:', '')
        return BANSOS_NS[local]

    # ── P-001: ADD DatatypeProperty ───────────────────────────
    def add_datatype_property(self, property_name: str,
                               domain_class: str, xsd_range: str) -> bool:
        prop_uri  = self._uri(property_name)
        class_uri = self._uri(domain_class)

        if (prop_uri, RDF.type, OWL.DatatypeProperty) in self.g:
            log.info('[TTL] Property %s sudah ada, skip', property_name)
            return False

        range_uri = _XSD_MAP.get(xsd_range, RDFXSD.string)
        label     = _to_label(property_name.replace(f'{ONTOLOGY_PREFIX}:', ''))

        for triple in ((prop_uri, RDF.type,    OWL.DatatypeProperty),
                       (prop_uri, RDFS.label,  Literal(label, lang='id')),
                       (prop_uri, RDFS.domain, class_uri),
                       (prop_uri, RDFS.range,  range_uri)):
            self._add(triple)

        log.info('[TTL][P-001] tambah DatatypeProperty %s (domain=%s, range=%s)',
                 property_name, domain_class, xsd_range)
        return True

    # ── P-002: DEPRECATE property ─────────────────────────────
    def deprecate_property(self, property_name: str) -> bool:
        prop_uri = self._uri(property_name)

        if (prop_uri, RDF.type, OWL.DatatypeProperty) not in self.g:
            log.warning('[TTL] Property %s tidak ada di ontologi', property_name)
            return False

        if (prop_uri, OWL.deprecated, Literal(True)) in self.g:
            log.info('[TTL] Property %s sudah deprecated, skip', property_name)
            return False

        self._add((prop_uri, OWL.deprecated, Literal(True)))
        log.info('[TTL][P-002] deprecate %s', property_name)
        return True

    def _add(self, triple: tuple) -> None:
        self.g.add(triple)
        self._added.append(triple)

    def render(self, note: str = '') -> str:
        """Isi berkas asli + blok triple baru di akhir (komentar dipertahankan)."""
        if not self._added:
            return self._raw
        lines = [self._raw.rstrip('\n'), '', '',
                 '# ================================================================',
                 f'# ASCAM {datetime.now(timezone.utc).isoformat(timespec="seconds")}'
                 + (f' — {note}' if note else ''),
                 '# ================================================================']
        by_subject: dict = {}
        for s_, p_, o_ in self._added:
            by_subject.setdefault(s_, []).append((p_, o_))
        for subj, pos in by_subject.items():
            body = ' ;\n    '.join(f'{self._term(p_)} {self._term(o_)}' for p_, o_ in pos)
            lines.append(f'{self._term(subj)} {body} .')
        text = '\n'.join(lines) + '\n'
        # Validasi: hasil harus dapat di-parse dan memuat semua triple
        check = Graph().parse(data=text, format='turtle')
        missing = [t for t in self.g if t not in check]
        if missing:
            raise ValueError(f'render TTL tidak konsisten, {len(missing)} triple hilang')
        return text

    def _term(self, term) -> str:
        """Tulis term dengan prefix yang DIDEKLARASIKAN di berkas; selain itu IRI penuh."""
        if isinstance(term, URIRef):
            for prefix, ns in self._prefixes.items():
                local = str(term)[len(ns):]
                if str(term).startswith(ns) and re.fullmatch(r'[A-Za-z_][\w\-]*', local or '#'):
                    return f'{prefix}:{local}'
            return f'<{term}>'
        if isinstance(term, Literal):
            if term.datatype == RDFXSD.boolean:
                return 'true' if term.toPython() else 'false'
            return term.n3()
        return term.n3()

    def save(self):
        atomic_write(self.path, self.render())
        log.info('[TTL] Disimpan: %s', self.path.name)


def _to_label(prop_camel: str) -> str:
    spaced = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', prop_camel)
    return spaced.title()

def _declared_prefixes(text: str) -> dict[str, str]:
    """Prefix yang dideklarasikan di berkas (@prefix / PREFIX)."""
    found = re.findall(r'(?im)^\s*(?:@prefix|PREFIX)\s+([\w\-]*):\s*<([^>]*)>', text)
    return {p: ns for p, ns in found}
