"""Konsol administrator: autentikasi, daftar izin, CSRF, identitas pelaku, dan fitur BFF."""
import pytest

UBAH = {'X-ASCAM-UI': '1'}


def test_page_and_static_files_are_served(konsol):
    assert 'Konsol ASCAM' in konsol.get('/').text
    assert konsol.get('/static/app.js').status_code == 200
    assert konsol.get('/static/style.css').status_code == 200


def test_api_requires_login(konsol):
    assert konsol.get('/api/ui/me').status_code == 401
    assert konsol.get('/api/k/obdf').status_code == 401
    assert konsol.get('/api/ui/overview').status_code == 401


def test_wrong_password_is_rejected(konsol):
    r = konsol.post('/api/ui/login', json={'username': 'admin', 'password': 'salah'})
    assert r.status_code == 401 and 'salah' in r.json()['detail']
    assert konsol.get('/api/ui/me').status_code == 401


def test_session_cookie_is_httponly_and_strict(konsol):
    r = konsol.post('/api/ui/login', json={'username': 'admin', 'password': 'rahasia-yang-panjang'})
    cookie = r.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'samesite=strict' in cookie


def test_login_logout_cycle(masuk):
    assert masuk.get('/api/ui/me').json()['user'] == 'admin'
    masuk.post('/api/ui/logout')
    assert masuk.get('/api/ui/me').status_code == 401


def test_browser_never_receives_service_token(masuk, palsu):
    masuk.get('/api/k/obdf')
    assert palsu.permintaan[-1].headers['Authorization'] == 'Bearer token-ui'
    assert 'token-ui' not in masuk.get('/').text
    assert 'token-ui' not in masuk.get('/static/app.js').text


@pytest.mark.parametrize('metode, path', [
    ('POST', 'obdf/1/sources'),                 # mengubah registri
    ('PUT', 'credentials/3/secret'),            # mengganti kredensial
    ('POST', 'plans/5/supersede'),              # hanya untuk Executor dan eksperimen
    ('POST', 'obdf/1/events'),                  # hanya untuk Orchestrator
    ('PUT', 'obdf/1/settings/ontology.namespace'),
    ('GET', 'obdf/1/../../admin'),
])
def test_proxy_blocks_paths_outside_allowlist(masuk, metode, path):
    r = masuk.request(metode, f'/api/k/{path}', headers=UBAH, json={})
    assert r.status_code in (403, 404)


def test_mutations_require_csrf_header(masuk):
    tanpa = masuk.post('/api/k/plans/5/approve', json={})
    assert tanpa.status_code == 403 and 'X-ASCAM-UI' in tanpa.json()['detail']
    assert masuk.post('/api/ui/executor/pause').status_code == 403


def test_approval_carries_admin_identity_and_note(masuk, palsu):
    r = masuk.post('/api/k/plans/5/approve', headers=UBAH, json={'note': 'nama property tepat'})
    assert r.status_code == 200
    body = r.json()
    assert body['decided_by'] == 'ui:admin'
    assert body['terima'] == {'note': 'nama property tepat'}
    assert palsu.permintaan[-1].headers['Content-Type'] == 'application/json'


def test_policy_change_is_allowed(masuk, palsu):
    r = masuk.put('/api/k/obdf/1/settings/adaptation.add_column', headers=UBAH,
                  json={'value': {'mode': 'hitl'}})
    assert r.status_code == 200
    assert palsu.permintaan[-1].url.path == '/api/v1/obdf/1/settings/adaptation.add_column'


def test_health_aggregates_services_and_tolerates_failures(masuk):
    sehat = masuk.get('/api/ui/health').json()
    assert sehat['orchestrator']['state'] == 'running'
    assert sehat['executor']['paused'] is False
    assert sehat['agent'] == {'_galat': 'ConnectError'}        # layanan mati tidak menjatuhkan konsol


def test_overview_combines_knowledge_views(masuk):
    r = masuk.get('/api/ui/overview').json()
    assert r['obdf']['name'] == 'bansos'
    assert set(r) >= {'active_version', 'events', 'pending', 'executions', 'notifications'}


def test_diff_between_versions(masuk):
    d = masuk.get('/api/ui/diff', params={'dari': 7, 'ke': 8, 'kind': 'r2rml'}).json()
    assert d['identik'] is False and d['tambah'] == 1 and d['hapus'] == 0
    assert any('rr:predicateObjectMap' in baris for baris in d['diff'])
    assert masuk.get('/api/ui/diff', params={'dari': 7, 'ke': 7, 'kind': 'r2rml'}).json()['identik']
    assert masuk.get('/api/ui/diff', params={'dari': 7, 'ke': 9, 'kind': 'r2rml'}).status_code == 404
    assert masuk.get('/api/ui/diff', params={'dari': 7, 'ke': 8, 'kind': '../x'}).status_code == 400


def test_executor_control(masuk):
    assert masuk.post('/api/ui/executor/pause', headers=UBAH).json() == {'paused': True}
    assert masuk.post('/api/ui/executor/resume', headers=UBAH).json() == {'paused': False}
    assert masuk.post('/api/ui/executor/hapus', headers=UBAH).status_code == 404


def test_app_refuses_to_start_without_session_key():
    from ascam_ui.app import create_app
    from ascam_ui.config import Settings
    with pytest.raises(RuntimeError, match='kunci sesi'):
        create_app(Settings(session_key=None))


def test_secret_reader(tmp_path):
    from ascam_ui.config import baca
    token = tmp_path / 'tokens'
    token.write_text('# komentar\nui:token-ui\nexecutor:token-exe\n')
    assert baca(str(token), 'ui') == 'token-ui'
    assert baca(str(token), 'tidak-ada') is None
    sandi = tmp_path / 'sandi'
    sandi.write_text('kata:sandi:bertitik-dua\n')
    assert baca(str(sandi)) == 'kata:sandi:bertitik-dua'      # kata sandi tidak dipotong
    assert baca(str(tmp_path / 'tidak-ada')) is None


def test_mapping_artifacts_are_reachable(masuk):
    """Regresi: pola [a-z_]+ menolak 'r2rml' karena memuat angka."""
    assert masuk.get('/api/k/versions/7/artifacts/r2rml').status_code == 200
    assert masuk.get('/api/ui/diff', params={'dari': 7, 'ke': 8, 'kind': 'r2rml'}).status_code == 200
