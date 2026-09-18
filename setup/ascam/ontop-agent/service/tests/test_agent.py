"""Agen Ontop: pembacaan dan penulisan artefak, cadangan, validasi, dan muat ulang."""
import hashlib

import httpx
import pytest
from fastapi.testclient import TestClient

from ascam_ontop_agent.config import AgentConfig
from ascam_ontop_agent.ontop_cli import CommandResult, OntopRunner
from conftest import MAPPING, ONTOLOGY, PROPERTIES, TOKEN


def test_health_is_open_and_reports_artifacts(agent):
    body = TestClient(agent.app).get('/health').json()      # tanpa token
    assert body['status'] == 'ok' and body['artifacts'] == {'r2rml': True, 'ontology': True}


@pytest.mark.parametrize('path', ['/api/v1/artifacts', '/api/v1/artifacts/r2rml', '/api/v1/validate'])
def test_authentication_required(agent, path):
    anonymous = TestClient(agent.app)
    method = anonymous.post if path.endswith('validate') else anonymous.get
    assert method(path).status_code == 401
    assert method(path, headers={'Authorization': 'Bearer salah'}).status_code == 401


def test_listing_has_fingerprints(agent):
    rows = {a['kind']: a for a in agent.get('/api/v1/artifacts').json()}
    assert set(rows) == {'r2rml', 'ontology'}               # properti tidak muncul
    assert rows['r2rml']['sha256'] == hashlib.sha256(MAPPING.encode()).hexdigest()
    assert rows['r2rml']['media_type'] == 'text/turtle' and rows['r2rml']['size'] == len(MAPPING)


def test_read_artifact_content(agent):
    body = agent.get('/api/v1/artifacts/r2rml').json()
    assert body['content'] == MAPPING
    assert agent.get('/api/v1/artifacts/ontology').json()['content'] == ONTOLOGY


def test_credentials_file_is_never_exposed(agent):
    for kind in ('properties', 'government.docker.properties', '../government.docker.properties'):
        r = agent.get(f'/api/v1/artifacts/{kind}')
        assert r.status_code == 404
        assert 'SANGAT-RAHASIA' not in r.text
    assert 'SANGAT-RAHASIA' not in agent.get('/api/v1/artifacts').text


def test_missing_artifact_reported(agent):
    agent.cfg.path_of('r2rml').unlink()
    assert agent.get('/api/v1/artifacts/r2rml').status_code == 404
    listing = {a['kind']: a['exists'] for a in agent.get('/api/v1/artifacts').json()}
    assert listing == {'r2rml': False, 'ontology': True}
    assert TestClient(agent.app).get('/health').json()['artifacts']['r2rml'] is False


def test_validate_passes_db_url(agent, runner):
    ok = agent.post('/api/v1/validate', json={'db_url': 'jdbc:teiid:government@mm://teiid:31000;version=3'})
    assert ok.status_code == 200 and ok.json()['ok'] and ok.json()['duration_ms'] == 1200
    assert runner.calls == ['jdbc:teiid:government@mm://teiid:31000;version=3']
    agent.post('/api/v1/validate', json={})
    assert runner.calls[-1] is None


def test_validate_failure_is_reported(agent, runner):
    runner.result = CommandResult(False, 1, 'ERROR: There is a problem loading the mapping file', 900)
    body = agent.post('/api/v1/validate', json={}).json()
    assert body['ok'] is False and body['exit_code'] == 1 and 'problem loading' in body['output']


# ── pembungkus Docker (tanpa Docker sungguhan) ──────────────────────────────────
class FakeContainer:
    def __init__(self, code=0, logs=b'Validation completed'):
        self.attrs = {'NetworkSettings': {'Networks': {'ascam-networks': {}}}}
        self._code, self._logs, self.removed = code, logs, False

    def wait(self, timeout=None):
        return {'StatusCode': self._code}

    def logs(self):
        return self._logs

    def remove(self, force=False):
        self.removed = True


class FakeDocker:
    def __init__(self, code=0):
        self.container = FakeContainer(code)
        self.run_kwargs = None
        outer = self

        class Containers:
            def get(self, name):
                return outer.container

            def run(self, image, **kwargs):
                outer.run_kwargs = {'image': image, **kwargs}
                return outer.container
        self.containers = Containers()


def test_runner_uses_volumes_from_and_ontop_network(tmp_path):
    cfg = AgentConfig(artifacts_dir=tmp_path)
    docker = FakeDocker()
    result = OntopRunner(cfg, docker_client=docker).validate('jdbc:teiid:x@mm://t:31000;version=2')
    kwargs = docker.run_kwargs
    assert kwargs['image'] == cfg.ontop_image and kwargs['entrypoint'] == 'java'
    assert kwargs['volumes_from'] == [cfg.ontop_container] and kwargs['network'] == 'ascam-networks'
    expected_tail = ['validate',
                     '-m', '/opt/ontop/input/mapping.ttl',
                     '-t', '/opt/ontop/input/ontology_file.ttl',
                     '-p', '/opt/ontop/input/government.docker.properties',
                     '--db-url', 'jdbc:teiid:x@mm://t:31000;version=2']
    cmd = kwargs['command']
    assert cmd[-len(expected_tail):] == expected_tail
    assert cmd[0] == '-cp' and 'it.unibz.inf.ontop.cli.Ontop' in cmd
    assert result.ok and docker.container.removed        # kontainer sekali jalan dibersihkan


def test_runner_reports_nonzero_exit(tmp_path):
    result = OntopRunner(AgentConfig(artifacts_dir=tmp_path), docker_client=FakeDocker(code=1)).validate()
    assert not result.ok and result.exit_code == 1


# ── penulisan artefak, cadangan, dan pemulihan ──────────────────────────────────
BARU = MAPPING + '<#MapB> a rr:TriplesMap .\n'


def test_write_creates_backup_and_replaces_atomically(agent, artifacts_dir):
    lama = agent.get('/api/v1/artifacts/r2rml').json()['sha256']
    out = agent.put('/api/v1/artifacts/r2rml', json={'content': BARU}).json()
    assert out['sha256'] == hashlib.sha256(BARU.encode()).hexdigest()
    assert (artifacts_dir / 'mapping.ttl').read_text() == BARU
    assert not list(artifacts_dir.glob('.*ascam-tmp'))          # berkas sementara dibersihkan

    cadangan = agent.get('/api/v1/backups').json()
    assert len(cadangan) == 1 and cadangan[0]['kind'] == 'r2rml' and cadangan[0]['sha256'] == lama
    assert cadangan[0]['backup_id'] == out['backup_id']


def test_write_rejects_stale_expected_digest(agent):
    sekarang = agent.get('/api/v1/artifacts/r2rml').json()['sha256']
    assert agent.put('/api/v1/artifacts/r2rml',
                     json={'content': BARU, 'expected_sha256': sekarang}).status_code == 200
    konflik = agent.put('/api/v1/artifacts/r2rml',
                        json={'content': MAPPING, 'expected_sha256': sekarang})
    assert konflik.status_code == 409 and 'sudah berubah' in konflik.json()['detail']
    assert agent.get('/api/v1/artifacts/r2rml').json()['content'] == BARU   # isi tidak tertimpa


def test_restore_returns_previous_content(agent):
    asli = agent.get('/api/v1/artifacts/r2rml').json()['content']
    backup_id = agent.put('/api/v1/artifacts/r2rml', json={'content': BARU}).json()['backup_id']
    dipulihkan = agent.post('/api/v1/artifacts/restore', json={'backup_id': backup_id}).json()
    assert dipulihkan['kind'] == 'r2rml'
    assert agent.get('/api/v1/artifacts/r2rml').json()['content'] == asli
    assert len(agent.get('/api/v1/backups').json()) == 2          # pemulihan juga dicadangkan


@pytest.mark.parametrize('backup_id', ['tidak-ada', '../mapping.ttl',
                                       'properties-20260918T000000000000-abcdefabcdef.properties'])
def test_restore_rejects_unknown_or_unsafe_backup(agent, backup_id):
    assert agent.post('/api/v1/artifacts/restore', json={'backup_id': backup_id}).status_code == 404


def test_write_rejects_unmanaged_artifact(agent):
    r = agent.put('/api/v1/artifacts/properties', json={'content': 'jdbc.password=bocor'})
    assert r.status_code == 404
    assert 'bocor' not in agent.get('/api/v1/artifacts').text


def test_prune_keeps_latest_backups(agent, artifacts_dir):
    from ascam_ontop_agent import writer
    from ascam_ontop_agent.config import AgentConfig
    cfg = AgentConfig(artifacts_dir=artifacts_dir)
    for i in range(5):
        writer.write(cfg, 'r2rml', MAPPING + f'# versi {i}\n')
    assert len(writer.list_backups(cfg, 'r2rml')) == 5
    assert writer.prune(cfg, keep=2) == 3
    tersisa = writer.list_backups(cfg, 'r2rml')
    assert len(tersisa) == 2 and tersisa[0].created_at > tersisa[1].created_at


# ── muat ulang Ontop ────────────────────────────────────────────────────────────
class FakeOntopContainer:
    def __init__(self, gagal=False):
        self.restarts = 0
        self.gagal = gagal

    def restart(self, timeout=None):
        self.restarts += 1
        if self.gagal:
            raise RuntimeError('kontainer tidak dapat di-restart')


class FakeDockerForReload:
    def __init__(self, container):
        outer = self

        class Containers:
            def get(self, name):
                return outer.container
        self.container = container
        self.containers = Containers()


def sparql_setelah(percobaan_gagal: int):
    sisa = {'n': percobaan_gagal}

    def handler(request):
        if sisa['n'] > 0:
            sisa['n'] -= 1
            raise httpx.ConnectError('belum siap')
        return httpx.Response(200, json={'boolean': True})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_reload_waits_until_endpoint_answers(agent, artifacts_dir):
    from ascam_ontop_agent.config import AgentConfig
    from ascam_ontop_agent.reloader import reload_ontop
    container = FakeOntopContainer()
    hasil = reload_ontop(AgentConfig(artifacts_dir=artifacts_dir),
                         docker_client=FakeDockerForReload(container),
                         http=sparql_setelah(3), sleep=lambda s: None)
    assert hasil.ok and container.restarts == 1 and hasil.total_ms >= 0


def test_reload_reports_failure(agent, artifacts_dir):
    from ascam_ontop_agent.config import AgentConfig
    from ascam_ontop_agent.reloader import reload_ontop
    cfg = AgentConfig(artifacts_dir=artifacts_dir)
    gagal_restart = reload_ontop(cfg, docker_client=FakeDockerForReload(FakeOntopContainer(gagal=True)),
                                 http=sparql_setelah(0), sleep=lambda s: None)
    assert not gagal_restart.ok and 'tidak dapat di-restart' in gagal_restart.error

    tidak_siap = reload_ontop(cfg, docker_client=FakeDockerForReload(FakeOntopContainer()),
                              http=sparql_setelah(10_000), ready_timeout=0.05, sleep=lambda s: None)
    assert not tidak_siap.ok and 'tidak siap' in tidak_siap.error


def test_reload_endpoint_uses_injected_docker(agent, artifacts_dir, monkeypatch):
    container = FakeOntopContainer()
    agent.app.state.docker = FakeDockerForReload(container)
    # endpoint SPARQL tiruan: reload akan gagal menunggu, tetapi restart tetap terjadi
    monkeypatch.setattr('ascam_ontop_agent.app.reload_ontop',
                        lambda cfg, docker_client=None: __import__('ascam_ontop_agent.reloader',
                                                                   fromlist=['ReloadResult'])
                        .ReloadResult(True, 800, 5200, 6000))
    body = agent.post('/api/v1/reload').json()
    assert body == {'ok': True, 'stop_ms': 800, 'ready_ms': 5200, 'total_ms': 6000, 'error': None}
