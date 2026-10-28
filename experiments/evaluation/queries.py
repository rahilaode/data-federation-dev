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
B = 'PREFIX bansos: <http://bansos.go.id/ontology/>\n'

CONTROL = {
    'count_per_class': ('control', B + 'SELECT ?kelas (COUNT(?s) AS ?n) WHERE { ?s a ?kelas } '
                                   'GROUP BY ?kelas ORDER BY ?kelas'),
    'transaction_status': ('control', B + 'SELECT ?t ?status WHERE { ?t a bansos:TransaksiBansos ; '
                                      'bansos:statusTransaksi ?status } ORDER BY ?t'),
}

Q = {
    'a001': {
        'recipient': ('neighbour', B + 'SELECT ?p ?nama ?status WHERE { ?p a bansos:PenerimaBansos ; '
                                   'bansos:namaLengkap ?nama ; bansos:statusEkonomi ?status } ORDER BY ?p'),
        'email': ('affected', B + 'SELECT ?p ?email WHERE { ?p bansos:email ?email } ORDER BY ?p'),
        'resident_link': ('control', B + 'SELECT ?p ?d WHERE { ?p bansos:memilikDataKependudukan ?d } '
                                     'ORDER BY ?p'),
        **CONTROL,
    },
    'a002': {
        'program_type': ('affected', B + 'SELECT ?p ?tipe WHERE { ?p a bansos:ProgramBansos ; '
                                     'bansos:tipeProgram ?tipe } ORDER BY ?p'),
        'program_name': ('neighbour', B + 'SELECT ?p ?nama ?nominal WHERE { ?p a bansos:ProgramBansos ; '
                                      'bansos:namaProgram ?nama ; bansos:nominal ?nominal } ORDER BY ?p'),
        'transaction_program': ('control', B + 'SELECT ?t ?prog WHERE { ?t bansos:terdaftarPadaProgram ?prog } '
                                           'ORDER BY ?t'),
        **CONTROL,
    },
    'a003': {
        'birth_date': ('affected', B + 'SELECT ?s ?tgl WHERE { ?s a bansos:Penduduk ; '
                                   'bansos:tanggalLahir ?tgl } ORDER BY ?s'),
        'name_occupation': ('neighbour', B + 'SELECT ?s ?nama ?kerja WHERE { ?s a bansos:Penduduk ; '
                                         'bansos:namaPenduduk ?nama ; bansos:pekerjaan ?kerja } ORDER BY ?s'),
        'recipient_to_resident': ('control', B + 'SELECT ?p ?nama WHERE { ?p bansos:memilikDataKependudukan ?d . '
                                             '?d bansos:namaPenduduk ?nama } ORDER BY ?p'),
        **CONTROL,
    },
    'a004': {
        'postal_code': ('affected', B + 'SELECT ?w ?kode WHERE { ?w bansos:kodePos ?kode } ORDER BY ?w'),
        'region': ('neighbour', B + 'SELECT ?w ?prov ?kab WHERE { ?w a bansos:Wilayah ; '
                                'bansos:provinsi ?prov ; bansos:kabupaten ?kab } ORDER BY ?w'),
        'recipient_to_resident': ('control', B + 'SELECT ?p ?nama WHERE { ?p bansos:memilikDataKependudukan ?d . '
                                             '?d bansos:namaPenduduk ?nama } ORDER BY ?p'),
        **CONTROL,
    },
    'a005': {
        'living_status': ('affected', B + 'SELECT ?s ?status WHERE { ?s a bansos:Penduduk ; '
                                      'bansos:statusHidup ?status } ORDER BY ?s'),
        'name_occupation': ('neighbour', B + 'SELECT ?s ?nama ?kerja WHERE { ?s a bansos:Penduduk ; '
                                         'bansos:namaPenduduk ?nama ; bansos:pekerjaan ?kerja } ORDER BY ?s'),
        # The only cross-source query touches master_penduduk, so the A005 control uses another
        # MySQL table (a limitation of the mapping, reported as a threat to validity).
        'family_region': ('control', B + 'SELECT ?k ?w WHERE { ?k bansos:berdomisiliDi ?w } ORDER BY ?k'),
        **CONTROL,
    },
    'a006': {
        'program_name': ('affected', B + 'SELECT ?p ?nama WHERE { ?p a bansos:ProgramBansos ; '
                                     'bansos:namaProgram ?nama } ORDER BY ?p'),
        'type_amount': ('neighbour', B + 'SELECT ?p ?tipe ?nominal WHERE { ?p a bansos:ProgramBansos ; '
                                     'bansos:tipeProgram ?tipe ; bansos:nominal ?nominal } ORDER BY ?p'),
        'recipient_to_resident': ('control', B + 'SELECT ?p ?nama WHERE { ?p bansos:memilikDataKependudukan ?d . '
                                             '?d bansos:namaPenduduk ?nama } ORDER BY ?p'),
        **CONTROL,
    },
    # A007-A009: the affected query reads a predicate built from a VIEW; the neighbour query reads
    # another column of the same view (A007, A008) or of the same base table (A009).
    # No ORDER BY: for a view without a key Ontop 4.1.1 adds SELECT DISTINCT, and Teiid rejects
    # ORDER BY over an IRI expression that is not projected (TEIID30088). Order is not needed,
    # because run() sorts the answer set before it is compared.
    'a007': {
        'active_recipient_kk': ('affected', B + 'SELECT ?p ?kk WHERE { ?p a bansos:PenerimaAktif ; '
                                            'bansos:noKartuKeluarga ?kk }'),
        'active_recipient': ('neighbour', B + 'SELECT ?p WHERE { ?p a bansos:PenerimaAktif }'),
        'resident_link': ('control', B + 'SELECT ?p ?d WHERE { ?p bansos:memilikDataKependudukan ?d } '
                                     'ORDER BY ?p'),
        **CONTROL,
    },
    'a008': {
        'history_officer': ('affected', B + 'SELECT ?r ?petugas WHERE { ?r bansos:diubahOleh ?petugas }'),
        'history_column': ('neighbour', B + 'SELECT ?r ?kolom WHERE { ?r a bansos:RiwayatPerubahan ; '
                                        'bansos:kolomDiubah ?kolom }'),
        'family_region': ('control', B + 'SELECT ?k ?w WHERE { ?k bansos:berdomisiliDi ?w } ORDER BY ?k'),
        **CONTROL,
    },
    'a009': {
        'end_year': ('affected', B + 'SELECT ?p ?th WHERE { ?p bansos:tahunBerakhir ?th }'),
        'program_name': ('neighbour', B + 'SELECT ?p ?nama ?nominal WHERE { ?p a bansos:ProgramBansos ; '
                                      'bansos:namaProgram ?nama ; bansos:nominal ?nominal } ORDER BY ?p'),
        'transaction_program': ('control', B + 'SELECT ?t ?prog WHERE { ?t bansos:terdaftarPadaProgram ?prog } '
                                           'ORDER BY ?t'),
        **CONTROL,
    },
}

# Light query used to watch endpoint availability during an adaptation: no scenario touches the
# transaksi_bansos table, so a failure means the endpoint itself is not available.
AVAILABILITY = B + 'SELECT ?t WHERE { ?t a bansos:TransaksiBansos } LIMIT 1'


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
