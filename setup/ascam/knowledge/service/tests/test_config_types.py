"""The declarative configuration of the case study maps SQL types as R2RML does.

R2RML (W3C Recommendation, 2012), §10.2 "Natural Mapping of SQL Values", Table: SMALLINT,
INTEGER, BIGINT -> xsd:integer; NUMERIC, DECIMAL -> xsd:decimal; FLOAT, REAL, DOUBLE PRECISION ->
xsd:double; BOOLEAN -> xsd:boolean; DATE -> xsd:date; TIME -> xsd:time; TIMESTAMP ->
xsd:dateTime. The paper states that xsd(theta) is this datatype, so the configuration must agree.
"""
from pathlib import Path

import yaml

import pytest

CONFIGS = [Path(__file__).resolve().parents[2] / 'config' / 'obdf-npd.yaml',
           Path(__file__).resolve().parents[5] / 'archive/bansos/knowledge/config/obdf-bansos.yaml']
FAMILY = {
    'smallint': 'xsd:integer', 'integer': 'xsd:integer', 'int': 'xsd:integer', 'bigint': 'xsd:integer',
    'tinyint': 'xsd:integer', 'numeric': 'xsd:decimal', 'decimal': 'xsd:decimal',
    'real': 'xsd:double', 'float': 'xsd:double', 'double precision': 'xsd:double', 'double': 'xsd:double',
    'boolean': 'xsd:boolean', 'date': 'xsd:date', 'time': 'xsd:time',
    'time without time zone': 'xsd:time', 'timestamp without time zone': 'xsd:dateTime',
    'timestamp with time zone': 'xsd:dateTime', 'datetime': 'xsd:dateTime', 'timestamp': 'xsd:dateTime',
}


@pytest.mark.parametrize('config', CONFIGS, ids=lambda p: p.name)
def test_type_mappings_follow_the_r2rml_natural_mapping(config):
    rows = yaml.safe_load(config.read_text())['type_mappings']
    checked = [(r['dbms'], r['native_type'], r['xsd_datatype'], FAMILY[r['native_type']])
               for r in rows if r['native_type'] in FAMILY]
    assert len(checked) >= 15
    wrong = [c for c in checked if c[2] != c[3]]
    assert wrong == []


def test_npd_types_of_the_dump_and_of_sqlglot_are_mapped():
    """Every native type of the NPD sources (catalogs in setup/npd/tools/catalog) and the names
    sqlglot gives them in a DDL event have a Teiid type (ADR-0027)."""
    rows = yaml.safe_load(CONFIGS[0].read_text())['type_mappings']
    known = {(r['dbms'], r['native_type']) for r in rows}
    catalogs = Path(__file__).resolve().parents[5] / 'setup/npd/tools/catalog'
    for dbms, name in (('postgresql', 'reporting.tsv'), ('mysql', 'registry.tsv')):
        native = {line.split('\t')[3] for line in (catalogs / name).read_text().splitlines() if line}
        assert {(dbms, t) for t in native} <= known, sorted({t for t in native} - {t for d, t in known if d == dbms})
    assert {('postgresql', t) for t in ('decimal', 'int', 'varchar', 'char', 'text', 'date', 'bigint')} <= known
