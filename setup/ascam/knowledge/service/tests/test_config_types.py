"""The declarative configuration of the case studies maps SQL types as R2RML does.

R2RML (W3C Recommendation, 2012), §10.2 "Natural Mapping of SQL Values", Table: SMALLINT,
INTEGER, BIGINT -> xsd:integer; NUMERIC, DECIMAL -> xsd:decimal; FLOAT, REAL, DOUBLE PRECISION ->
xsd:double; BOOLEAN -> xsd:boolean; DATE -> xsd:date; TIME -> xsd:time; TIMESTAMP ->
xsd:dateTime. The paper states that xsd(theta) is this datatype, so the configuration must agree.
"""
import re
from pathlib import Path

import yaml

import pytest

CONFIGS = [Path(__file__).resolve().parents[2] / 'config' / 'obdf-example.yaml',
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


def test_example_types_of_the_sources_and_of_the_scenarios_are_mapped():
    """Every native type of the running-example sources (setup/data-source/*/01-example.sql) and of
    the columns the scenarios add, as sqlglot names them in a DDL event, has a Teiid type."""
    rows = yaml.safe_load(CONFIGS[0].read_text())['type_mappings']
    known = {(r['dbms'], r['native_type']) for r in rows}
    sources = Path(__file__).resolve().parents[5] / 'setup/data-source'
    table = re.compile(r'CREATE TABLE[^(]*\((.*?)\n\)', re.S | re.I)
    word = re.compile(r'^\s+\w+\s+(\w+)', re.M)
    for dbms, path in (('postgresql', 'pgsql/01-example.sql'), ('mysql', 'mysql/01-example.sql')):
        bodies = table.findall((sources / path).read_text())
        native = {t.lower() for body in bodies for t in word.findall(body)
                  if t.upper() not in ('PRIMARY', 'KEY', 'CONSTRAINT')}
        assert native
        assert {(dbms, t) for t in native} <= known, sorted(native - {t for d, t in known if d == dbms})
    # r13 INTEGER, r14 VARCHAR, r15 DATE, r18 NUMERIC on S1; r20 VARCHAR on S2
    assert {('postgresql', t) for t in ('integer', 'varchar', 'date', 'decimal')} <= known
    assert ('mysql', 'varchar') in known
