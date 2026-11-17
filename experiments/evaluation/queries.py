"""
The query set Q_k of every scenario of the running example and how it is scored.

Each scenario has three kinds of query:
  affected   reads the predicate that the change touches;
  neighbour  same table or view, another column;
  control    another table, including a query that reads both sources (ex:nickname).

Every query execution is recorded in full: HTTP status, Ontop error message, row count, and the
sorted answer set, so the baseline and the treatment are scored the same way.

  ResultPreserved(q)     = 1 if both executions succeed and the answer sets are identical
  ExecutionPreserved(q)  = 1 if the Ontop response after the change is not an error
  PreservationRatio(A_k) = mean of P_k(q) over Q_k × 100 %
"""
import json
import time
import urllib.error
import urllib.parse
import urllib.request

SPARQL = 'http://localhost:8080/sparql'
P = 'PREFIX ex: <http://example.org/>\n'


def q(body: str) -> str:
    return P + 'SELECT * WHERE { ' + body + ' }'


# No query has ORDER BY: run() sorts the answer set before it is compared.
ACTIVE_NAME = q('?p a ex:ActivePerson ; ex:name ?n')
ACTIVE = q('?p a ex:ActivePerson')
NICKNAME = q('?s ex:nickname ?n')                     # filled from both sources
PERSON = q('?p a ex:Person')
PHONE = q('?s ex:phone ?x')
EMAIL = q('?s ex:email ?e')
CONTACT = q('?c a ex:Contact')
NET_PRICE = q('?p ex:netPrice ?x')
PRODUCT = q('?p a ex:Product')
CITY_NAME = q('?c a ex:City ; ex:cityName ?n')
MEMBER_NAME = q('?m a ex:Member ; ex:fullName ?n')

CONTROL = {'city_name': ('control', CITY_NAME), 'member_name': ('control', MEMBER_NAME)}

Q = {
    'r01': {'name': ('affected', ACTIVE_NAME), 'active': ('neighbour', ACTIVE),
            'nickname': ('control', NICKNAME), **CONTROL},
    'r02': {'name': ('affected', ACTIVE_NAME), 'active': ('neighbour', ACTIVE),
            'nickname': ('control', NICKNAME), **CONTROL},
    'r03': {'name': ('affected', ACTIVE_NAME), 'person': ('neighbour', PERSON), **CONTROL},
    'r04': {'nickname': ('affected', q('?p a ex:Person ; ex:nickname ?n')),
            'name': ('neighbour', ACTIVE_NAME), **CONTROL},
    'r05': {'staff_city': ('affected', q('?s ex:city ?c')), 'staff': ('neighbour', q('?s a ex:Staff')),
            **CONTROL},
    'r06': {'phone': ('affected', PHONE), 'contact': ('neighbour', CONTACT),
            'email': ('neighbour', EMAIL), **CONTROL},
    'r07': {'email': ('affected', EMAIL), 'phone': ('neighbour', PHONE), **CONTROL},
    'r08': {'nickname': ('affected', NICKNAME), 'person': ('neighbour', PERSON),
            'name': ('neighbour', ACTIVE_NAME), **CONTROL},
    'r09': {'net_price': ('affected', NET_PRICE), 'product': ('neighbour', PRODUCT), **CONTROL},
    'r10': {'note_body': ('affected', q('?n ex:noteBody ?b')), 'note': ('neighbour', q('?n a ex:Note')),
            **CONTROL},
    'r11': {'memo_body': ('affected', q('?m ex:memoBody ?b')), 'memo': ('neighbour', q('?m a ex:Memo')),
            **CONTROL},
    'r12': {'doc_body': ('affected', q('?d ex:docBody ?b')), 'doc': ('neighbour', q('?d a ex:Doc')),
            **CONTROL},
    'r13': {'population': ('affected', q('?c ex:population ?x')), 'city_name': ('neighbour', CITY_NAME),
            'member_name': ('control', MEMBER_NAME)},
    'r14': {'status': ('affected', q('?a ex:status ?s')), 'owner': ('neighbour', q('?a ex:owner ?o')),
            'customer': ('neighbour', q('?c a ex:Customer')), **CONTROL},
    'r15': {'birth_date': ('affected', q('?p ex:birthDate ?d')), 'nickname': ('neighbour', NICKNAME),
            'name': ('neighbour', ACTIVE_NAME), **CONTROL},
    # audit is read by no triples map, so the neighbour is the graph of another S1 table
    'r16': {'actor': ('affected', q('?a ex:actor ?x')), 'person': ('neighbour', PERSON), **CONTROL},
    'r17': {'severity': ('affected', q('?e ex:severity ?s')), 'kind': ('neighbour', q('?e ex:kind ?k')),
            **CONTROL},
    'r18': {'weight': ('affected', q('?t ex:weight ?w')), 'label': ('neighbour', q('?t ex:label ?l')),
            **CONTROL},
    'r19': {'member_name': ('affected', MEMBER_NAME), 'nickname': ('neighbour', NICKNAME),
            'city_name': ('control', CITY_NAME)},
    'r20': {'member_email': ('affected', q('?m a ex:Member ; ex:email ?e')),
            'contact_email': ('neighbour', EMAIL), **CONTROL},
    'r21': {'nickname': ('affected', NICKNAME), 'member_name': ('neighbour', MEMBER_NAME),
            'city_name': ('control', CITY_NAME)},
}

# Light query used to watch endpoint availability during an adaptation: no scenario touches
# the city name, so a failure means the endpoint itself is not available.
AVAILABILITY = P + 'SELECT ?c WHERE { ?c a ex:City } LIMIT 1'

# Queries through the views of model v that must answer in the base state (preflight.py)
BASE_CHECKS = ('r01', 'r09')

# What happens to the affected query of a P-002 scenario: 'empty' when its property is
# deprecated, 'fewer' when another assertion still populates it, 'same' when the dropped
# column was only projected.
P002_AFFECTED = {'r01': 'empty', 'r06': 'fewer', 'r07': 'same', 'r08': 'fewer', 'r21': 'fewer'}


def pattern(code: str) -> str:
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
    """For the BASE snapshot: retried while the endpoint does not answer."""
    result = {}
    for _ in range(attempts):
        result = run_set(code)
        if all(h['status'] is not None for h in result.values()):
            return result
        time.sleep(delay)
    return result


def score(code: str, before: dict, after: dict) -> dict:
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
    """What the paper predicts for the scenario after the treatment (docs/running-example.md)."""
    from scenarios import SCENARIOS
    sc = SCENARIOS[code]
    name = affected(code)
    a = after.get(name) or {}
    per = assessment['per_query']
    others_run = all(v['execution_preserved'] for n, v in per.items() if n != name)
    if sc.decision == 'notify':
        if sc.pattern == 'P-001':
            # ADD is not adapted: Σ does not change, so the new column is simply not visible
            return {'old_queries_valid': all(v['execution_preserved'] for v in per.values())}
        # not adapted: the query through the broken element keeps failing until a person repairs it
        return {'affected_query_fails': not a.get('ok')}
    if sc.pattern == 'P-001':
        out = {'old_queries_valid': others_run}
        if sc.expect.get('property', True):
            out['new_query_returns_rows'] = bool(a.get('ok') and (a.get('n') or 0) > 0)
        return out
    if sc.pattern == 'P-002':
        before_n, after_n = per[name]['rows_before'], per[name]['rows_after']
        rule = P002_AFFECTED[code]
        out = {'old_query_still_runs': bool(a.get('ok')), 'other_queries_run': others_run}
        if rule == 'empty':
            out['answer_set_empty'] = bool(a.get('ok') and a.get('n') == 0)
        elif rule == 'fewer':
            out['fewer_answers_but_not_empty'] = bool(a.get('ok') and 0 < (after_n or 0) < (before_n or 0))
        else:
            out['answers_identical'] = per[name]['result_preserved'] == 1
        return out
    return {'answer_set_identical': assessment['preservation_ratio'] == 100.0}
