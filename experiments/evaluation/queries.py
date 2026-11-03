"""
The query set Q_k of every scenario and how it is scored (thesis proposal §3.10–3.11.3).

Each scenario has three kinds of query:
  affected   touches the column that changes;
  neighbour  same table, another column;
  control    another table, including the cross-source query.

Every query execution is recorded in full: HTTP status, Ontop error message, row count, and the
sorted answer set, so the baseline (§3.10.1) and the treatment (§3.10.2) are scored the same way.

  ResultPreserved(q)     = 1 if both executions succeed and the answer sets are identical (eq. 3.19)
  ExecutionPreserved(q)  = 1 if the Ontop response after the change is not an error (eq. 3.20)
  PreservationRatio(A_k) = mean of P_k(q) over Q_k × 100 % (eq. 3.21)
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

SPARQL = 'http://localhost:8080/sparql'
N = 'PREFIX npdv: <http://sws.ifi.uio.no/vocab/npd-v2#>\n'

# NPD case study (ADR-0027). No query has ORDER BY: run() sorts the answer set before it is
# compared, and for a view without a key Ontop 4.1.1 adds SELECT DISTINCT, with which Teiid rejects
# ORDER BY over an IRI expression that is not projected (TEIID30088, found with A007 of the first
# case study).
CONTROL = {
    # cross-source: exploration wells (reporting) joined to their licences (registry) on
    # prlNpdidProductionLicence = prlNpdidLicence, a pair of columns no scenario touches
    'well_licence': ('control', N + 'SELECT ?w ?l WHERE { ?w npdv:explorationWellboreForLicence ?l }'),
    'well_name': ('control', N + 'SELECT ?w ?n WHERE { ?w a npdv:ExplorationWellbore ; npdv:name ?n }'),
}
COMPANY_NAME = N + 'SELECT ?c ?n WHERE { ?c a npdv:Company ; npdv:name ?n }'
MUD_WEIGHT = N + 'SELECT ?m ?w WHERE { ?m a npdv:WellboreDrillingMudSample ; npdv:mudWeight ?w }'
CASING_TYPE = N + 'SELECT ?c ?t WHERE { ?c a npdv:WellboreCasingAndLeakoffTest ; npdv:casingType ?t }'
DISCOVERY_NAME = N + 'SELECT ?d ?n WHERE { ?d a npdv:Discovery ; npdv:name ?n }'
DRILLING_OPERATOR = N + 'SELECT ?w ?c WHERE { ?w npdv:drillingOperatorCompany ?c }'

Q = {
    # reporting (PostgreSQL)
    'a001': {
        'mud_ph': ('affected', N + 'SELECT ?m ?ph WHERE { ?m npdv:wlbMudPh ?ph }'),
        'mud_weight': ('neighbour', MUD_WEIGHT),
        'company_name': ('control', COMPANY_NAME),
        **CONTROL,
    },
    'a002': {
        'lot_mud_density': ('affected', N + 'SELECT ?c ?d WHERE { ?c npdv:LOTMudDensity ?d }'),
        'casing_type': ('neighbour', CASING_TYPE),
        'company_name': ('control', COMPANY_NAME),
        **CONTROL,
    },
    'a003': {
        # the renamed column joins wells (reporting) to companies (registry)
        'drilling_operator': ('affected', DRILLING_OPERATOR),
        'well_total_depth': ('neighbour', N + 'SELECT ?w ?d WHERE { ?w a npdv:ExplorationWellbore ; '
                                              'npdv:wellboreTotalDepth ?d }'),
        'discovery_name': ('control', DISCOVERY_NAME),
        **CONTROL,
    },
    # registry (MySQL)
    'a004': {
        'country_code': ('affected', N + 'SELECT ?c ?cc WHERE { ?c npdv:cmpCountryCode ?cc }'),
        'company_short_name': ('neighbour', N + 'SELECT ?c ?s WHERE { ?c a npdv:Company ; npdv:shortName ?s }'),
        'mud_weight': ('control', MUD_WEIGHT),
        **CONTROL,
    },
    'a005': {
        'discovery_year': ('affected', N + 'SELECT ?d ?y WHERE { ?d a npdv:Discovery ; npdv:discoveryYear ?y }'),
        'discovery_name': ('neighbour', DISCOVERY_NAME),
        'casing_type': ('control', CASING_TYPE),
        **CONTROL,
    },
    'a006': {
        # the renamed column gives company names and is the target of the wells -> company join
        'company_name': ('affected', COMPANY_NAME),
        'company_short_name': ('neighbour', N + 'SELECT ?c ?s WHERE { ?c a npdv:Company ; npdv:shortName ?s }'),
        'drilling_operator': ('control', DRILLING_OPERATOR),
        **CONTROL,
    },
    # through the service views (A007-A009); the neighbour reads another column of the same view
    # (A007) or of the same base table (A008, A009)
    'a007': {
        'mud_viscosity': ('affected', N + 'SELECT ?m ?v WHERE { ?m npdv:mudViscosity ?v }'),
        'mud_yield_point': ('neighbour', N + 'SELECT ?m ?y WHERE { ?m npdv:mudYieldPoint ?y }'),
        'company_name': ('control', COMPANY_NAME),
        **CONTROL,
    },
    'a008': {
        'investment': ('affected', N + 'SELECT ?i ?x WHERE { ?i npdv:investmentNOK ?x }'),
        'investment_year': ('neighbour', N + 'SELECT ?i ?y WHERE { ?i a npdv:FieldInvestment ; '
                                             'npdv:productionYear ?y }'),
        'mud_weight': ('control', MUD_WEIGHT),
        **CONTROL,
    },
    'a009': {
        'core_bottom': ('affected', N + 'SELECT ?c ?b WHERE { ?c npdv:coreIntervalBottom ?b }'),
        'core_top': ('neighbour', N + 'SELECT ?c ?t WHERE { ?c a npdv:WellboreCore ; npdv:coreIntervalTop ?t }'),
        'company_name': ('control', COMPANY_NAME),
        **CONTROL,
    },
}

# Light query used to watch endpoint availability during an adaptation: no scenario touches the
# licence table (registry), so a failure means the endpoint itself is not available.
AVAILABILITY = N + 'SELECT ?l WHERE { ?l a npdv:ProductionLicence } LIMIT 1'


def pattern(code: str) -> str:
    """ASCAM pattern of a scenario; scoring follows the pattern, not the code, so that it is the
    same on both DBMSs."""
    from scenarios import SCENARIOS
    return SCENARIOS[code].pattern


def affected(code: str) -> str:
    return next(n for n, (kind, _) in Q[code].items() if kind == 'affected')


def run(query: str, timeout: float = 90.0) -> dict:
    """One query execution, recorded as it is (errors included)."""
    url = SPARQL + '?' + urllib.parse.urlencode({'query': query})
    request = urllib.request.Request(url, headers={'Accept': 'application/sparql-results+json'})
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read())
        rows = sorted(json.dumps(b, sort_keys=True) for b in body['results']['bindings'])
        return {'status': response.status, 'ok': True, 'error': None, 'n': len(rows),
                'rows': rows, 'duration_ms': int((time.perf_counter() - start) * 1000)}
    except urllib.error.HTTPError as exc:
        message = exc.read()[:600].decode('utf-8', 'replace')
        return {'status': exc.code, 'ok': False, 'error': message, 'n': None, 'rows': None,
                'duration_ms': int((time.perf_counter() - start) * 1000)}
    except Exception as exc:                                        # noqa: BLE001
        return {'status': None, 'ok': False, 'error': f'{type(exc).__name__}: {str(exc)[:300]}',
                'n': None, 'rows': None, 'duration_ms': int((time.perf_counter() - start) * 1000)}


def run_set(code: str) -> dict:
    return {name: run(query) for name, (_, query) in Q[code].items()}


def run_stable(code: str, attempts: int = 6, delay: float = 2.0) -> dict:
    """For the BASE snapshot: retried while the endpoint does not answer (not used after the
    change, because an error after the change is exactly what is measured)."""
    result = {}
    for _ in range(attempts):
        result = run_set(code)
        if all(h['status'] is not None for h in result.values()):
            return result
        time.sleep(delay)
    return result


def score(code: str, before: dict, after: dict) -> dict:
    """ResultPreserved and ExecutionPreserved per query, and the PreservationRatio of the scenario."""
    per_query = {}
    for name, (kind, _) in Q[code].items():
        a, b = before.get(name) or {}, after.get(name) or {}
        per_query[name] = {
            'kind': kind,
            'result_preserved': int(bool(a.get('ok') and b.get('ok') and a['rows'] == b['rows'])),
            'execution_preserved': int(bool(b.get('ok'))),
            'rows_before': a.get('n'), 'rows_after': b.get('n'),
            'status_after': b.get('status'),
        }
    key = 'result_preserved' if pattern(code) == 'P-003' else 'execution_preserved'
    ratio = 100.0 * sum(v[key] for v in per_query.values()) / len(per_query)
    return {'metric': key, 'preservation_ratio': ratio, 'per_query': per_query}


def expectations(code: str, after: dict, assessment: dict) -> dict:
    """Theoretical expectation of a treatment scenario by its pattern (thesis proposal §3.10.2,
    p. 104)."""
    from scenarios import SCENARIOS
    q = after.get(affected(code)) or {}
    if SCENARIOS[code].decision == 'notify':
        # Not adapted (ADR-0024): the OBDF does not change, so the query through the broken view
        # keeps failing until the administrator redefines the view (same behaviour as the
        # baseline). The other queries are recorded in `assessment` without an expectation: that
        # depends on whether Teiid prunes the view columns that are not requested before push-down.
        return {'affected_query_fails': not q.get('ok')}
    if pattern(code) == 'P-001':
        return {'old_queries_valid': all(v['execution_preserved'] for n, v in assessment['per_query'].items()
                                         if n != affected(code)),
                'new_query_returns_rows': bool(q.get('ok') and (q.get('n') or 0) > 0)}
    if pattern(code) == 'P-002':
        return {'old_query_still_runs': bool(q.get('ok')),
                'answer_set_empty': bool(q.get('ok') and q.get('n') == 0)}
    return {'answer_set_identical': assessment['preservation_ratio'] == 100.0}
