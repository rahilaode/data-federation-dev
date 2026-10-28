"""Administrator console: authentication, allow list, CSRF, actor identity, and BFF features."""
import pytest

CHANGE = {'X-ASCAM-UI': '1'}


def test_page_and_static_files_are_served(console):
    assert 'ASCAM Console' in console.get('/').text
    assert console.get('/static/app.js').status_code == 200
    assert console.get('/static/style.css').status_code == 200


def test_api_requires_login(console):
    assert console.get('/api/ui/me').status_code == 401
    assert console.get('/api/k/obdf').status_code == 401
    assert console.get('/api/ui/overview').status_code == 401


def test_wrong_password_is_rejected(console):
    r = console.post('/api/ui/login', json={'username': 'admin', 'password': 'wrong'})
    assert r.status_code == 401 and 'Wrong' in r.json()['detail']
    assert console.get('/api/ui/me').status_code == 401


def test_session_cookie_is_httponly_and_strict(console):
    r = console.post('/api/ui/login', json={'username': 'admin', 'password': 'a-long-secret'})
    cookie = r.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=strict' in cookie


def test_login_logout_cycle(logged_in):
    assert logged_in.get('/api/ui/me').json()['user'] == 'admin'
    logged_in.post('/api/ui/logout')
    assert logged_in.get('/api/ui/me').status_code == 401


def test_browser_never_receives_service_token(logged_in, fake):
    logged_in.get('/api/k/obdf')
    assert fake.requests[-1].headers['Authorization'] == 'Bearer token-ui'
    assert 'token-ui' not in logged_in.get('/').text
    assert 'token-ui' not in logged_in.get('/static/app.js').text


@pytest.mark.parametrize('method, path', [
    ('POST', 'obdf/1/sources'),                 # changes the registry
    ('PUT', 'credentials/3/secret'),            # replaces a credential
    ('POST', 'plans/5/supersede'),              # for the Executor and the experiments only
    ('POST', 'obdf/1/events'),                  # for the Orchestrator only
    ('PUT', 'obdf/1/settings/ontology.namespace'),
    ('GET', 'obdf/1/../../admin'),
])
def test_proxy_blocks_paths_outside_allowlist(logged_in, method, path):
    r = logged_in.request(method, f'/api/k/{path}', headers=CHANGE, json={})
    assert r.status_code in (403, 404)


def test_mutations_require_csrf_header(logged_in):
    without = logged_in.post('/api/k/plans/5/approve', json={})
    assert without.status_code == 403 and 'X-ASCAM-UI' in without.json()['detail']
    assert logged_in.post('/api/ui/executor/pause').status_code == 403


def test_approval_carries_admin_identity_and_note(logged_in, fake):
    r = logged_in.post('/api/k/plans/5/approve', headers=CHANGE, json={'note': 'the property name is right'})
    assert r.status_code == 200
    body = r.json()
    assert body['decided_by'] == 'ui:admin'
    assert body['received'] == {'note': 'the property name is right'}
    assert fake.requests[-1].headers['Content-Type'] == 'application/json'


def test_approval_forwards_property_choice(logged_in, fake):
    choice = {'note': None, 'property_name': 'alamatDomisili',
               'domain': 'http://bansos.go.id/ontology/PenerimaBansos'}
    r = logged_in.post('/api/k/plans/5/approve', headers=CHANGE, json=choice)
    assert r.status_code == 200 and r.json()['received'] == choice


def test_lineage_is_readable(logged_in, fake):
    logged_in.get('/api/k/obdf/1/lineage', params={'source': 'kemensos', 'table': 'program_bansos'})
    last = fake.requests[-1]
    assert last.url.path == '/api/v1/obdf/1/lineage'
    assert last.url.params['table'] == 'program_bansos'


def test_policy_change_is_allowed(logged_in, fake):
    r = logged_in.put('/api/k/obdf/1/settings/adaptation.add_column', headers=CHANGE,
                  json={'value': {'mode': 'hitl'}})
    assert r.status_code == 200
    assert fake.requests[-1].url.path == '/api/v1/obdf/1/settings/adaptation.add_column'


def test_health_aggregates_services_and_tolerates_failures(logged_in):
    health = logged_in.get('/api/ui/health').json()
    assert health['orchestrator']['state'] == 'running'
    assert health['executor']['paused'] is False
    assert health['agent'] == {'_error': 'ConnectError'}        # a service that is down does not bring the console down


def test_overview_combines_knowledge_views(logged_in):
    r = logged_in.get('/api/ui/overview').json()
    assert r['obdf']['name'] == 'bansos'
    assert set(r) >= {'active_version', 'events', 'pending', 'executions', 'notifications'}


def test_diff_between_versions(logged_in):
    d = logged_in.get('/api/ui/diff', params={'from_version': 7, 'to_version': 8, 'kind': 'r2rml'}).json()
    assert d['identical'] is False and d['added'] == 1 and d['removed'] == 0
    assert any('rr:predicateObjectMap' in line for line in d['diff'])
    assert logged_in.get('/api/ui/diff', params={'from_version': 7, 'to_version': 7, 'kind': 'r2rml'}).json()['identical']
    assert logged_in.get('/api/ui/diff', params={'from_version': 7, 'to_version': 9, 'kind': 'r2rml'}).status_code == 404
    assert logged_in.get('/api/ui/diff', params={'from_version': 7, 'to_version': 8, 'kind': '../x'}).status_code == 400


def test_executor_control(logged_in):
    assert logged_in.post('/api/ui/executor/pause', headers=CHANGE).json() == {'paused': True}
    assert logged_in.post('/api/ui/executor/resume', headers=CHANGE).json() == {'paused': False}
    assert logged_in.post('/api/ui/executor/delete', headers=CHANGE).status_code == 404


def test_app_refuses_to_start_without_session_key():
    from ascam_ui.app import create_app
    from ascam_ui.config import Settings
    with pytest.raises(RuntimeError, match='session key'):
        create_app(Settings(session_key=None))


def test_secret_reader(tmp_path):
    from ascam_ui.config import read_secret
    token = tmp_path / 'tokens'
    token.write_text('# comment\nui:token-ui\nexecutor:token-exe\n')
    assert read_secret(str(token), 'ui') == 'token-ui'
    assert read_secret(str(token), 'does-not-exist') is None
    password_file = tmp_path / 'password'
    password_file.write_text('pass:word:with-colons\n')
    assert read_secret(str(password_file)) == 'pass:word:with-colons'      # the password is not cut
    assert read_secret(str(tmp_path / 'does-not-exist')) is None


def test_mapping_artifacts_are_reachable(logged_in):
    """Regression: the pattern [a-z_]+ rejected 'r2rml' because it contains a digit."""
    assert logged_in.get('/api/k/versions/7/artifacts/r2rml').status_code == 200
    assert logged_in.get('/api/ui/diff', params={'from_version': 7, 'to_version': 8, 'kind': 'r2rml'}).status_code == 200
