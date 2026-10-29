"""The declarative configuration of the case study maps SQL types as R2RML does.

R2RML (W3C Recommendation, 2012), §10.2 "Natural Mapping of SQL Values", Table: SMALLINT,
INTEGER, BIGINT -> xsd:integer; NUMERIC, DECIMAL -> xsd:decimal; FLOAT, REAL, DOUBLE PRECISION ->
xsd:double; BOOLEAN -> xsd:boolean; DATE -> xsd:date; TIME -> xsd:time; TIMESTAMP ->
xsd:dateTime. The paper states that xsd(theta) is this datatype, so the configuration must agree.
"""
from pathlib import Path

import yaml

CONFIG = Path(__file__).resolve().parents[2] / 'config' / 'obdf-bansos.yaml'
FAMILY = {
    'smallint': 'xsd:integer', 'integer': 'xsd:integer', 'int': 'xsd:integer', 'bigint': 'xsd:integer',
    'tinyint': 'xsd:integer', 'numeric': 'xsd:decimal', 'decimal': 'xsd:decimal',
    'real': 'xsd:double', 'float': 'xsd:double', 'double precision': 'xsd:double', 'double': 'xsd:double',
    'boolean': 'xsd:boolean', 'date': 'xsd:date', 'time': 'xsd:time',
    'time without time zone': 'xsd:time', 'timestamp without time zone': 'xsd:dateTime',
    'timestamp with time zone': 'xsd:dateTime', 'datetime': 'xsd:dateTime', 'timestamp': 'xsd:dateTime',
}


def test_type_mappings_follow_the_r2rml_natural_mapping():
    rows = yaml.safe_load(CONFIG.read_text())['type_mappings']
    checked = [(r['dbms'], r['native_type'], r['xsd_datatype'], FAMILY[r['native_type']])
               for r in rows if r['native_type'] in FAMILY]
    assert len(checked) >= 15
    wrong = [c for c in checked if c[2] != c[3]]
    assert wrong == []
