"""
ASCAM TTL Updater
=================
Memodifikasi file ontologi Turtle (.ttl) menggunakan rdflib.

Operasi:
  add_datatype_property()  → P-001 ADD COLUMN
  deprecate_property()     → P-002 DROP COLUMN (tidak hapus fisik)
  P-003 RENAME COLUMN tidak butuh update TTL (semantic preservation)
"""

import re
import shutil
import logging
from pathlib import Path

from rdflib import Graph, Namespace, Literal, RDF, RDFS, OWL, XSD as RDFXSD
from config.settings import ONTOLOGY_PREFIX, ONTOLOGY_BASE

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
        self.path = Path(path)
        self.g    = Graph()
        self._load()

    def _load(self):
        self.g.parse(str(self.path), format='turtle')
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

        self.g.add((prop_uri, RDF.type,    OWL.DatatypeProperty))
        self.g.add((prop_uri, RDFS.label,  Literal(label, lang='id')))
        self.g.add((prop_uri, RDFS.domain, class_uri))
        self.g.add((prop_uri, RDFS.range,  range_uri))

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

        self.g.add((prop_uri, OWL.deprecated, Literal(True)))
        log.info('[TTL][P-002] deprecate %s', property_name)
        return True

    def save(self):
        backup = self.path.with_suffix('.ttl.bak')
        shutil.copy2(self.path, backup)
        self.g.serialize(destination=str(self.path), format='turtle')
        log.info('[TTL] Disimpan: %s (backup: %s)', self.path.name, backup.name)


def _to_label(prop_camel: str) -> str:
    spaced = re.sub(r'(?<=[a-z])(?=[A-Z])', ' ', prop_camel)
    return spaced.title()