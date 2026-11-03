"""The Teiid service views of the NPD federation and the TriplesMaps that read through them.

Scenarios A007-A009 test columns that reach the mapping THROUGH a Teiid view (ADR-0023). The
NPD mapping reads tables only, so three views are added in the virtual model `services`, and
the TriplesMaps of the affected properties are pointed at them. The 31 NPD queries give the
same answers as with the original mapping, and six of the seven predicates the same triples;
npdv:coreIntervalBottom differs in the last digits of 410 values, because the view computes the
conversion in exact NUMERIC where Ontop used double precision (ADR-0027).

  v_wellbore_mud      pass-through projection of reporting.wellbore_mud (A007, DROP through
                      a view whose projection can be narrowed with ALTER VIEW)
  v_field_investment  projection with aliases of registry.field_investment_yearly, read by the
                      mapping with SELECT * (A008)
  v_wellbore_core_ft  the ft-to-m conversion of the NPD mapping itself (TriplesMap
                      NPD:Manual10:2), moved from the logical table into the view (A009, a column
                      used inside a view expression: negative control)
"""
VIEWS = {
    'v_wellbore_mud': (
        'SELECT "wellbore_mud_id", "wlbNpdidWellbore", "wlbMD", "wlbMudWeightAtMD", '
        '"wlbMudViscosityAtMD", "wlbYieldPointAtMD", "wlbMudType" FROM "reporting"."wellbore_mud"'),
    'v_field_investment': (
        'SELECT "prfNpdidInformationCarrier" AS "fieldId", "prfYear" AS "investmentYear", '
        '"prfInvestmentsMillNOK" AS "investmentMillNOK" FROM "registry"."field_investment_yearly"'),
    'v_wellbore_core_ft': (
        'SELECT "wellbore_core_id", "wlbNpdidWellbore", "wlbCoreNumber", '
        '"wlbCoreIntervalBottom" * 0.3048 AS "wlbCoreIntervalBottomFT" '
        'FROM "reporting"."wellbore_core" WHERE "wlbCoreIntervalUom" = \'[ft  ]\''),
}

# TriplesMap IRI -> its new logical table and the text replacements in its term maps
# (column names of the table -> column names of the view). The IRI templates give the same IRIs.
REDIRECT = {
    **{f'Mapping:{n}:Literal:wellbore_mud:{c}': {
        'sql': f'SELECT "wellbore_mud_id", "wlbNpdidWellbore", "{c}" '
               'FROM "services"."v_wellbore_mud" AS "wellbore_mud"',
        'replace': {}}
       for n, c in (('00052', 'wlbMudWeightAtMD'), ('00063', 'wlbMudType'), ('00228', 'wlbMD'),
                    ('00346', 'wlbMudViscosityAtMD'), ('00734', 'wlbYieldPointAtMD'))},
    'Mapping:00242:Literal:field_investment_yearly:prfInvestmentsMillNOK': {
        'sql': 'SELECT * FROM "services"."v_field_investment"',
        'replace': {'{\\"prfNpdidInformationCarrier\\"}': '{\\"fieldId\\"}',
                    '{\\"prfYear\\"}': '{\\"investmentYear\\"}',
                    'rr:column "\\"prfInvestmentsMillNOK\\""': 'rr:column "\\"investmentMillNOK\\""'}},
    'Mapping:NPD:Manual10:2': {
        'sql': 'SELECT "wellbore_core_id", "wlbNpdidWellbore", "wlbCoreNumber", "wlbCoreIntervalBottomFT" '
               'FROM "services"."v_wellbore_core_ft" AS "wellbore_core"',
        'replace': {'rr:column "wlbcoreintervalbottomft"': 'rr:column "\\"wlbCoreIntervalBottomFT\\""'}},
}
