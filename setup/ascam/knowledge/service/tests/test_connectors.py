"""Uji koneksi target: perilaku pemeriksa, penyaringan rahasia, endpoint API."""
import datetime
import json
import uuid

import httpx
import pytest

from ascam_knowledge import connectors
from ascam_knowledge.check_service import Hooks

PASSWORD = 'Sandi-Rahasia-987'


# ── server tiruan ───────────────────────────────────────────────────────────────
def wildfly(valid_user='admin', state='running'):
    """Management API tiruan dengan tantangan Digest seperti WildFly."""
    def handler(request: httpx.Request):
        auth = request.headers.get('authorization', '')
        if not auth.startswith('Digest '):
            return httpx.Response(401, headers={
                'WWW-Authenticate': 'Digest realm="ManagementRealm", nonce="abc", qop="auth", algorithm=MD5'})
        if f'username="{valid_user}"' not in auth:
            return httpx.Response(401, headers={
                'WWW-Authenticate': 'Digest realm="ManagementRealm", nonce="abd", qop="auth", algorithm=MD5'})
        op = json.loads(request.content)
        if op.get('operation') == 'list-vdbs':
            return httpx.Response(200, json={'outcome': 'success', 'result': [
                {'vdb-name': 'government', 'vdb-version': '1', 'status': 'ACTIVE', 'connection-type': 'BY_VERSION'}]})
        result = state if op.get('name') == 'server-state' else '19.1.0.Final'
        return httpx.Response(200, json={'outcome': 'success', 'result': result})
    return httpx.Client(transport=httpx.MockTransport(handler))


def sparql(status=200):
    def handler(request: httpx.Request):
        assert request.url.params['query'].startswith('ASK')
        if status != 200:
            return httpx.Response(status, text='galat')
        return httpx.Response(200, json={'head': {}, 'boolean': True})
    return httpx.Client(transport=httpx.MockTransport(handler))


class FakeConn:
    def __init__(self, row):
        self.row = row
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def cursor(self):
        return self
    def execute(self, sql):
        assert 'SYS.VirtualDatabases' in sql
    def fetchone(self):
        return self.row


class FakeAdmin:
    def __init__(self, topics):
        self.topics = topics
    def list_topics(self):
        return list(self.topics)
    def close(self):
        pass


TEIID_EP = {'host': 'teiid', 'port': 9990, 'path': '/management'}
ODBC_EP = {'host': 'teiid', 'port': 35432, 'options': {'vdb': 'government'}}


# ── pemeriksa ───────────────────────────────────────────────────────────────────
def test_teiid_mgmt_success_and_facts():
    r = connectors.check_teiid_mgmt(TEIID_EP, 'admin', PASSWORD, http=wildfly())
    assert r.ok and r.facts['product_version'] == '19.1.0.Final'
    assert r.facts['vdbs'][0] == {'name': 'government', 'version': '1', 'status': 'ACTIVE',
                                  'connection_type': 'BY_VERSION'}


def test_teiid_mgmt_rejected_credentials_and_bad_state():
    denied = connectors.check_teiid_mgmt(TEIID_EP, 'bukan-admin', PASSWORD, http=wildfly())
    assert not denied.ok and denied.summary == 'autentikasi ditolak'
    stopped = connectors.check_teiid_mgmt(TEIID_EP, 'admin', PASSWORD, http=wildfly(state='restart-required'))
    assert not stopped.ok


def test_teiid_odbc_success():
    active = datetime.datetime(2026, 9, 17, 8, 0, 0)
    r = connectors.check_teiid_odbc(ODBC_EP, 'user1', PASSWORD,
                                    connect=lambda **kw: FakeConn(('government', 1, active)))
    assert r.ok and r.facts == {'vdb': 'government', 'version': '1', 'active_since': active.isoformat()}


def test_secret_is_redacted_from_errors():
    def leaky_connect(**kw):
        raise RuntimeError(f"gagal login user={kw['user']} password={kw['password']}")
    r = connectors.check_teiid_odbc(ODBC_EP, 'user1', PASSWORD, connect=leaky_connect)
    assert not r.ok and PASSWORD not in json.dumps(r.detail()) and '***' in r.error


def test_teiid_odbc_unreachable_real_socket():
    r = connectors.check_teiid_odbc({'host': '127.0.0.1', 'port': 1, 'options': {'vdb': 'x'}},
                                    'user1', PASSWORD, timeout=2)
    assert not r.ok and r.error and PASSWORD not in r.error


def test_ontop_sparql():
    assert connectors.check_ontop_sparql({'host': 'ontop', 'port': 8080, 'path': '/sparql'}, http=sparql()).ok
    down = connectors.check_ontop_sparql({'host': 'ontop', 'port': 8080}, http=sparql(503))
    assert not down.ok and down.summary == 'HTTP 503'


def test_kafka_reports_missing_topics():
    ok = connectors.check_kafka({'host': 'kafka', 'port': 9092}, ['a', 'b'],
                                admin_factory=lambda: FakeAdmin({'a', 'b', 'c'}))
    assert ok.ok and ok.facts['missing_topics'] == []
    missing = connectors.check_kafka({'host': 'kafka', 'port': 9092}, ['a', 'z'],
                                     admin_factory=lambda: FakeAdmin({'a'}))
    assert not missing.ok and missing.facts['missing_topics'] == ['z']


def test_latency_is_measured():
    r = connectors.check_ontop_sparql({'host': 'ontop', 'port': 8080}, http=sparql())
    assert r.latency_ms >= 0


# ── endpoint API ────────────────────────────────────────────────────────────────
@pytest.fixture
def lab(api):
    """OBDF dengan lima target dan hook tiruan."""
    class LabHooks(Hooks):
        http = None
        connect = staticmethod(lambda **kw: FakeConn(('government', 1, datetime.datetime(2026, 9, 17))))
        kafka_admin_factory = staticmethod(lambda: FakeAdmin({'kemensos.topik'}))

    def transport(request: httpx.Request):
        if request.url.port == 9990:
            return wildfly().send(request)
        return sparql().send(request)
    LabHooks.http = httpx.Client(transport=httpx.MockTransport(transport))
    api.app_ref.state.check_hooks = LabHooks

    name = f'lab-{uuid.uuid4().hex[:8]}'
    oid = api.post('/api/v1/obdf', json={'name': name}).json()['id']
    api.post(f'/api/v1/obdf/{oid}/sources', json={'logical_name': 'kemensos', 'dbms': 'postgresql',
                                                  'database_name': 'kemensos', 'kafka_topic': 'kemensos.topik'})
    api.post(f'/api/v1/obdf/{oid}/sources', json={'logical_name': 'dukcapil', 'dbms': 'mysql',
                                                  'database_name': 'dukcapil', 'kafka_topic': 'dukcapil.topik'})
    admin = api.post(f'/api/v1/obdf/{oid}/credentials',
                     json={'name': 'teiid-admin', 'username': 'admin', 'secret': PASSWORD}).json()['id']
    user = api.post(f'/api/v1/obdf/{oid}/credentials',
                    json={'name': 'teiid-user', 'username': 'user1', 'secret': PASSWORD}).json()['id']
    base = f'/api/v1/obdf/{oid}/targets'
    ids = {
        'mgmt': api.post(base, json={'kind': 'teiid_mgmt', 'name': 'mgmt', 'credential_id': admin,
                                     'endpoint': TEIID_EP}).json()['id'],
        'odbc': api.post(base, json={'kind': 'teiid_odbc', 'name': 'odbc', 'credential_id': user,
                                     'endpoint': ODBC_EP}).json()['id'],
        'ontop': api.post(base, json={'kind': 'ontop_sparql', 'name': 'ontop',
                                      'endpoint': {'host': 'ontop', 'port': 8080, 'path': '/sparql'}}).json()['id'],
        'kafka': api.post(base, json={'kind': 'kafka', 'name': 'kafka',
                                      'endpoint': {'host': 'kafka', 'port': 9092}}).json()['id'],
        'agent': api.post(base, json={'kind': 'ontop_agent', 'name': 'agent', 'enabled': False,
                                      'endpoint': {'host': 'agent', 'port': 8000}}).json()['id'],
    }
    yield oid, ids
    LabHooks.http.close()
    del api.app_ref.state.check_hooks


def test_check_single_target_records_result(api, lab):
    oid, ids = lab
    r = api.post(f"/api/v1/targets/{ids['mgmt']}/check")
    assert r.status_code == 200 and r.json()['ok'] and r.json()['actor'] == 'ui:penguji'
    assert PASSWORD not in r.text
    history = api.get(f"/api/v1/targets/{ids['mgmt']}/checks").json()
    assert history[0]['id'] == r.json()['id']


def test_check_all_skips_disabled_and_reports_kafka_gap(api, lab):
    oid, ids = lab
    rows = api.post(f'/api/v1/obdf/{oid}/checks').json()
    by_target = {row['target_id']: row for row in rows}
    assert set(by_target) == {ids['mgmt'], ids['odbc'], ids['ontop'], ids['kafka']}
    assert by_target[ids['mgmt']]['ok'] and by_target[ids['odbc']]['ok'] and by_target[ids['ontop']]['ok']
    kafka = by_target[ids['kafka']]
    assert not kafka['ok'] and kafka['detail']['facts']['missing_topics'] == ['dukcapil.topik']

    disabled = api.post(f"/api/v1/targets/{ids['agent']}/check").json()
    assert not disabled['ok'] and 'nonaktif' in disabled['detail']['summary']

    status = {s['target']['name']: s['last_check'] for s in api.get(f'/api/v1/obdf/{oid}/status').json()}
    assert status['agent']['ok'] is False and status['mgmt']['ok'] is True
    audit = [a['action'] for a in api.get(f'/api/v1/obdf/{oid}/audit').json()]
    assert 'check_all' in audit and 'check' in audit
