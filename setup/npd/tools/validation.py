"""The validation layer of branch feature/npd_v2: elements ADDED to the NPD federation so that
every rule of the Plan in the paper can be triggered on real NPD tables and data.

Nothing of the NPD benchmark is changed or removed (docs/npd-v2-modifications.md explains every
element and the rule it serves). The layer adds

  Σ   one foreign column (company.cmpGroupName) and six views in the model `services`;
  ℳ   ten TriplesMaps, all reading NPD tables or the new views;
  𝒯   four classes and eight data properties in the namespace npdx (see NPDX), and two
      deprecated data properties in the NPD namespace, written as ASCAM writes a deprecation.

The new classes and properties live in their own namespace, and no new axiom relates them to
the NPD vocabulary, so the 31 NPD queries keep their answers. The two deprecated npdv
properties are needed because the naming policy of the case study puts new properties in the
NPD namespace ({namespace}{column_camel}, obdf-npd.yaml), and a reuse is only proposed for a
property whose IRI the policy produces.

Run from setup/npd:  python3 tools/validation.py
The script is idempotent: it removes an earlier layer before it writes the current one.
"""
import pathlib
import re
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[3]
VDB = ROOT / 'setup/data-federation/deployments/npd-vdb.xml'
MAPPING = ROOT / 'setup/vkg-system/config/mapping.ttl'
ONTOLOGY = ROOT / 'setup/vkg-system/config/ontology_file.ttl'
sys.path.insert(0, str(ROOT / 'setup/ascam/executor/service/src'))
from ascam_executor.artifacts import ontology as ontology_editor    # noqa: E402

NPDX = 'https://github.com/rahilaode/data-federation-dev/npd-validation#'
NPDV = 'http://sws.ifi.uio.no/vocab/npd-v2#'
DATA = 'http://sws.ifi.uio.no/data/npd-v2/'
RECORD = 'https://github.com/rahilaode/data-federation-dev/npd-validation/record/'
BEGIN = '# >>> ASCAM validation layer (setup/npd/tools/validation.py); do not edit by hand'
END = '# <<< ASCAM validation layer'
WHEN = datetime(2026, 11, 18, tzinfo=timezone.utc)

# ── Σ: one foreign column ──────────────────────────────────────────────────────────────────
# company.cmpGroupName reads a source column that does not exist (yet). It stands for a column
# that Σ kept after an earlier change was only notified. RENAME cmpGroup TO cmpGroupName then
# finds c' already read by another column (Table 1, rename).
LEGACY_TABLE = 'company'
LEGACY_COLUMN = '"cmpGroupName" string(100) OPTIONS(NAMEINSOURCE \'`cmpGroupName`\')'

# ── Σ_V: six views in the model `services` ────────────────────────────────────────────────
VIEWS = [
    # Example 1 of the paper on NPD: a filter in WHERE and a renamed column. cmpOrgNumberBrReg is
    # read by nothing else in the NPD mapping, so D(c, F) holds exactly the elements of Example 2
    'CREATE VIEW "v_current_operator" AS SELECT "cmpNpdidCompany", "cmpOrgNumberBrReg" AS "orgNumber" '
    'FROM "registry"."company" WHERE "cmpLicenceOperCurrent" = \'Y\';',
    # a plain SELECT * view: it gains a new column, which is no notification condition (P-001)
    'CREATE VIEW "v_wellbore_overview" AS SELECT * FROM "reporting"."wellbore_npdid_overview";',
    # a view with one column: dropping that column leaves the view with none (Table 1, drop)
    'CREATE VIEW "v_point_field" AS SELECT "wlbField" FROM "reporting"."wlbPoint";',
    # a view that declares its columns inline, under the names of the table (Table 1, drop)
    'CREATE VIEW "v_pipeline_operator" ("pipNpdidPipe" integer, "pipNameCurrentOperator" string) AS '
    'SELECT "pipNpdidPipe", "pipNameCurrentOperator" FROM "registry"."pipLine";',
    # a materialized view (internal materialization of Teiid) (Table 1, drop)
    'CREATE VIEW "v_field_text_cache" OPTIONS (MATERIALIZED \'TRUE\') AS '
    'SELECT "fldNpdidField", "fldDescriptionText" FROM "registry"."field_description";',
    # SELECT * in one branch of a UNION and a column list in the other (Table 1, add): a new
    # column would reach only the first branch
    'CREATE VIEW "v_field_status" AS SELECT * FROM "registry"."field_activity_status_hst" '
    'WHERE "fldStatus" = \'PRODUCING\' UNION ALL SELECT "fldName", "fldStatusFromDate", '
    '"fldStatusToDate", "fldStatus", "fldNpdidField", "fldStatusDateUpdated", "dateSyncNPD" '
    'FROM "registry"."field_activity_status_hst" WHERE "fldStatus" <> \'PRODUCING\';',
]


def _q(sql: str) -> str:
    return sql.replace('\\', '\\\\').replace('"', '\\"')


def _tm(name: str, sql: str, template: str, classes=(), poms=()) -> str:
    """A TriplesMap in the style of the generated NPD mapping."""
    lines = [f'<Validation:{name}> a rr:TriplesMap;',
             '  rr:logicalTable [ a rr:R2RMLView;',
             f'      rr:sqlQuery "{_q(sql)}"', '    ];']
    for predicate, column in poms:
        lines += ['  rr:predicateObjectMap [ a rr:PredicateObjectMap;',
                  '      rr:objectMap [ a rr:ObjectMap, rr:TermMap;',
                  f'          rr:column "{_q(chr(34) + column + chr(34))}";',
                  '          rr:termType rr:Literal', '        ];',
                  f'      rr:predicate {predicate}', '    ];']
    lines += ['  rr:subjectMap [ a rr:SubjectMap, rr:TermMap;']
    lines += [f'      rr:class {c};' for c in classes]
    lines += [f'      rr:template "{_q(template)}";', '      rr:termType rr:IRI', '    ] .', '']
    return '\n'.join(lines)


COMPANY = DATA + 'company/{"cmpNpdidCompany"}'
WELLBORE = DATA + 'wellbore/{"wlbNpdidWellbore"}'
FACILITY = DATA + 'facility/{"fclNpdidFacility"}'

TRIPLES_MAPS = [
    # Example 1: m with a1 (class) and a2 (property) over the view
    _tm('CurrentOperator', 'SELECT * FROM "services"."v_current_operator"', COMPANY,
        ['npdx:CurrentOperator'], [('npdx:orgNumber', 'orgNumber')]),
    # P-001, X ≠ ∅ with a safe domain: a class map over the whole table, and a map without class
    # with the same template (ADR-0033, R2)
    _tm('OverviewWellbore', 'SELECT * FROM "reporting"."wellbore_npdid_overview"', WELLBORE,
        ['npdx:OverviewWellbore']),
    _tm('OverviewWellboreType', 'SELECT * FROM "reporting"."wellbore_npdid_overview"', WELLBORE,
        (), [('npdx:overviewWellType', 'wlbWellType')]),
    # P-002, a column that a logical table names but no triple uses (rw_c only)
    _tm('PointFacility', 'SELECT "wlbNpdidWellbore", "wlbDrillingFacility", "wlbProductionFacility" '
        'FROM "reporting"."wlbPoint"', WELLBORE, (), [('npdx:drillingFacility', 'wlbDrillingFacility')]),
    # npdx:drillingFacility is also filled from the shallow wellbores, so dropping the column of
    # wlbPoint leaves the property populated (P-002 keeps it)
    _tm('ShallowDrillingFacility', 'SELECT "wlbNpdidWellbore", "wlbDrillingFacility" '
        'FROM "reporting"."wellbore_shallow_all"', WELLBORE, (),
        [('npdx:drillingFacility', 'wlbDrillingFacility')]),
    # the inline view and the materialized view are read by the mapping
    _tm('PipelineOperator', 'SELECT * FROM "services"."v_pipeline_operator"',
        DATA + 'pipeline/{"pipNpdidPipe"}', (), [('npdx:currentOperatorName', 'pipNameCurrentOperator')]),
    _tm('FieldDescriptionText', 'SELECT * FROM "services"."v_field_text_cache"',
        DATA + 'field/{"fldNpdidField"}', (), [('npdx:descriptionText', 'fldDescriptionText')]),
    # P-001 with several classes and a map without class that builds other IRIs: the domain is
    # not safe (ADR-0033)
    _tm('MoveableFacility', 'SELECT * FROM "registry"."facility_moveable"', FACILITY,
        ['npdx:MoveableFacility'], [('npdx:facilityKind', 'fclKind')]),
    _tm('ForeignFlaggedFacility', 'SELECT * FROM "registry"."facility_moveable" '
        'WHERE "fclNationName" <> \'NORWAY\'', FACILITY,
        ['npdx:ForeignFlaggedFacility'], [('npdx:flagNation', 'fclNationName')]),
    _tm('FacilityRecord', 'SELECT * FROM "registry"."facility_moveable"',
        RECORD + 'facility/{"fclNpdidFacility"}', (), [('npdx:responsibleCompany', 'fclCurrentRespCompanyName')]),
]

CLASSES = {
    'CurrentOperator': 'Company that is currently operator of a production licence (view v_current_operator).',
    'OverviewWellbore': 'Wellbore listed in the NPDID overview (wellbore_npdid_overview).',
    'MoveableFacility': 'Moveable facility (facility_moveable).',
    'ForeignFlaggedFacility': 'Moveable facility whose flag nation is not Norway.',
}
PROPERTIES = {
    'orgNumber': ('xsd:string', 'Organisation number (Brønnøysund register) of a current licence operator.'),
    'overviewWellType': ('xsd:string', 'Well type as given in the NPDID overview.'),
    'drillingFacility': ('xsd:string', 'Name of the drilling facility of a wellbore.'),
    'currentOperatorName': ('xsd:string', 'Name of the current operator of a pipeline.'),
    'descriptionText': ('xsd:string', 'Text of a field description.'),
    'facilityKind': ('xsd:string', 'Kind of a moveable facility.'),
    'flagNation': ('xsd:string', 'Flag nation of a moveable facility.'),
    'responsibleCompany': ('xsd:string', 'Name of the company responsible for a moveable facility.'),
}
# Properties whose column had been dropped earlier: ASCAM deprecated them (P-002), and an ADD of
# that column proposes them again (P-001, reuse). Domain and range are those P-001 would give.
DEPRECATED = {
    'wlbEntryYear': ('npdx:OverviewWellbore', 'xsd:integer',
                     'reporting.wellbore_npdid_overview.wlbEntryYear'),
    'fclStatus': ('npdx:MoveableFacility', 'xsd:string', 'registry.facility_moveable.fclStatus'),
}


def _strip(text: str) -> str:
    return re.sub(rf'\n?{re.escape(BEGIN)}.*?{re.escape(END)}\n', '\n', text, flags=re.S)


def apply_vdb(text: str) -> str:
    text = re.sub(r'\n\s*"cmp\w+" string\(100\) OPTIONS\(NAMEINSOURCE \'`cmp\w+`\'\),', '', text)
    anchor = re.search(r'CREATE FOREIGN TABLE "company" \(.*?\n(\s*)PRIMARY KEY', text, re.S)
    indent = anchor.group(1)
    text = text[:anchor.start(1)] + f'{indent}{LEGACY_COLUMN},\n' + text[anchor.start(1):]
    # the markers are SQL comments, because the views are inside the CDATA of the DDL
    block = ('\n            /* >>> validation views (setup/npd/tools/validation.py) */\n'
             + ''.join(f'            {v}\n\n' for v in VIEWS).rstrip('\n')
             + '\n            /* <<< validation views */\n        ')
    text = re.sub(r'\n\s*/\* >>> validation views.*?/\* <<< validation views \*/', '', text, flags=re.S)
    services = text.rindex(']]></metadata>')
    return text[:services].rstrip() + '\n' + block + text[services:]


def apply_mapping(text: str) -> str:
    text = _strip(text).rstrip('\n') + '\n'
    return (text + f'\n{BEGIN}\n@prefix npdx: <{NPDX}> .\n\n' + '\n'.join(TRIPLES_MAPS) + f'{END}\n')


def apply_ontology(text: str) -> str:
    text = _strip(text).rstrip('\n') + '\n'
    for name in DEPRECATED:                     # earlier ASCAM blocks of the layer
        text = re.sub(rf'\n?# ascam:begin \w+ {re.escape(NPDV + name)}\n.*?# ascam:end \w+ '
                      rf'{re.escape(NPDV + name)}\n', '\n', text, flags=re.S)
    if '# ascam:begin' not in text:             # the executor writes this header once
        text = re.sub(r'\n*# ─── ASCAM-managed blocks \(do not edit by hand\) ───\n*', '\n', text)
    lines = [BEGIN, f'@prefix npdx: <{NPDX}> .', '']
    for name, comment in CLASSES.items():
        lines += [f'npdx:{name} a owl:Class ;', f'    rdfs:label "{name}"@en ;',
                  f'    rdfs:comment "{comment}"@en .', '']
    for name, (range_, comment) in PROPERTIES.items():
        lines += [f'npdx:{name} a owl:DatatypeProperty ;', f'    rdfs:range {range_} ;',
                  f'    rdfs:label "{name}"@en ;', f'    rdfs:comment "{comment}"@en .', '']
    for name, (domain, range_, column) in DEPRECATED.items():
        lines += [f'npdv:{name} a owl:DatatypeProperty ;', f'    rdfs:domain {domain} ;',
                  f'    rdfs:range {range_} ;', f'    rdfs:label "{name}"@en ;',
                  f'    rdfs:comment "Mapped from {column} until that column was dropped."@en .', '']
    text = text + '\n' + '\n'.join(lines) + END + '\n'
    for name, (_, _, column) in DEPRECATED.items():
        text = ontology_editor.deprecate_property(
            text, NPDV + name, reason=f'column {column} was dropped', when=WHEN)
    return text


def main() -> None:
    VDB.write_text(apply_vdb(VDB.read_text()))
    MAPPING.write_text(apply_mapping(MAPPING.read_text()))
    ONTOLOGY.write_text(apply_ontology(ONTOLOGY.read_text()))
    print('validation layer written to', VDB.name, MAPPING.name, ONTOLOGY.name)


if __name__ == '__main__':
    main()
