"""Ontop agent: reading and writing artifacts, backups, validation, and reload."""
import hashlib

import httpx
import pytest
from fastapi.testclient import TestClient

from ascam_ontop_agent.config import AgentConfig
from ascam_ontop_agent.ontop_cli import CommandResult, OntopRunner
from conftest import MAPPING, ONTOLOGY, PROPERTIES, TOKEN


def test_health_is_open_and_reports_artifacts(agent):
    body = TestClient(agent.app).get('/health').json()      # without a token
    assert body['status'] == 'ok' and body['artifacts'] == {'r2rml': True, 'ontology': True}


@pytest.mark.parametrize('path', ['/api/v1/artifacts', '/api/v1/artifacts/r2rml', '/api/v1/validate'])
def test_authentication_required(agent, path):
    anonymous = TestClient(agent.app)
    method = anonymous.post if path.endswith('validate') else anonymous.get
    assert method(path).status_code == 401
    assert method(path, headers={'Authorization': 'Bearer wrong'}).status_code == 401


def test_listing_has_fingerprints(agent):
    rows = {a['kind']: a for a in agent.get('/api/v1/artifacts').json()}
    assert set(rows) == {'r2rml', 'ontology'}               # the properties file is not listed
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
        assert 'VERY-SECRET' not in r.text
    assert 'VERY-SECRET' not in agent.get('/api/v1/artifacts').text


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


# ── Docker wrapper (without real Docker) ────────────────────────────────────────
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
    assert result.ok and docker.container.removed        # the one-off container is cleaned up


def test_runner_reports_nonzero_exit(tmp_path):
    result = OntopRunner(AgentConfig(artifacts_dir=tmp_path), docker_client=FakeDocker(code=1)).validate()
    assert not result.ok and result.exit_code == 1


# ── artifact writes, backups, and restore ───────────────────────────────────────
NEW_MAPPING = MAPPING + '<#MapB> a rr:TriplesMap .\n'


def test_write_creates_backup_and_replaces_atomically(agent, artifacts_dir):
    old_sha = agent.get('/api/v1/artifacts/r2rml').json()['sha256']
    out = agent.put('/api/v1/artifacts/r2rml', json={'content': NEW_MAPPING}).json()
    assert out['sha256'] == hashlib.sha256(NEW_MAPPING.encode()).hexdigest()
    assert (artifacts_dir / 'mapping.ttl').read_text() == NEW_MAPPING
    assert not list(artifacts_dir.glob('.*ascam-tmp'))          # temporary files are cleaned up

    backups = agent.get('/api/v1/backups').json()
    assert len(backups) == 1 and backups[0]['kind'] == 'r2rml' and backups[0]['sha256'] == old_sha
    assert backups[0]['backup_id'] == out['backup_id']


def test_write_rejects_stale_expected_digest(agent):
    current = agent.get('/api/v1/artifacts/r2rml').json()['sha256']
    assert agent.put('/api/v1/artifacts/r2rml',
                     json={'content': NEW_MAPPING, 'expected_sha256': current}).status_code == 200
    conflict = agent.put('/api/v1/artifacts/r2rml',
                        json={'content': MAPPING, 'expected_sha256': current})
    assert conflict.status_code == 409 and 'has changed' in conflict.json()['detail']
    assert agent.get('/api/v1/artifacts/r2rml').json()['content'] == NEW_MAPPING   # content not overwritten


def test_restore_returns_previous_content(agent):
    original = agent.get('/api/v1/artifacts/r2rml').json()['content']
    backup_id = agent.put('/api/v1/artifacts/r2rml', json={'content': NEW_MAPPING}).json()['backup_id']
    restored = agent.post('/api/v1/artifacts/restore', json={'backup_id': backup_id}).json()
    assert restored['kind'] == 'r2rml'
    assert agent.get('/api/v1/artifacts/r2rml').json()['content'] == original
    assert len(agent.get('/api/v1/backups').json()) == 2          # the restore is backed up as well


@pytest.mark.parametrize('backup_id', ['does-not-exist', '../mapping.ttl',
                                       'properties-20260918T000000000000-abcdefabcdef.properties'])
def test_restore_rejects_unknown_or_unsafe_backup(agent, backup_id):
    assert agent.post('/api/v1/artifacts/restore', json={'backup_id': backup_id}).status_code == 404


def test_write_rejects_unmanaged_artifact(agent):
    r = agent.put('/api/v1/artifacts/properties', json={'content': 'jdbc.password=leaked'})
    assert r.status_code == 404
    assert 'leaked' not in agent.get('/api/v1/artifacts').text


def test_prune_keeps_latest_backups(agent, artifacts_dir):
    from ascam_ontop_agent import writer
    from ascam_ontop_agent.config import AgentConfig
    cfg = AgentConfig(artifacts_dir=artifacts_dir)
    for i in range(5):
        writer.write(cfg, 'r2rml', MAPPING + f'# version {i}\n')
    assert len(writer.list_backups(cfg, 'r2rml')) == 5
    assert writer.prune(cfg, keep=2) == 3
    remaining = writer.list_backups(cfg, 'r2rml')
    assert len(remaining) == 2 and remaining[0].created_at > remaining[1].created_at


# ── Ontop reload ────────────────────────────────────────────────────────────────
class FakeOntopContainer:
    def __init__(self, fails=False):
        self.restarts = 0
        self.fails = fails

    def restart(self, timeout=None):
        self.restarts += 1
        if self.fails:
            raise RuntimeError('the container cannot be restarted')


class FakeDockerForReload:
    def __init__(self, container):
        outer = self

        class Containers:
            def get(self, name):
                return outer.container
        self.container = container
        self.containers = Containers()


def sparql_after(failed_attempts: int):
    left = {'n': failed_attempts}

    def handler(request):
        if left['n'] > 0:
            left['n'] -= 1
            raise httpx.ConnectError('not ready yet')
        return httpx.Response(200, json={'boolean': True})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_reload_waits_until_endpoint_answers(agent, artifacts_dir):
    from ascam_ontop_agent.config import AgentConfig
    from ascam_ontop_agent.reloader import reload_ontop
    container = FakeOntopContainer()
    result = reload_ontop(AgentConfig(artifacts_dir=artifacts_dir),
                         docker_client=FakeDockerForReload(container),
                         http=sparql_after(3), sleep=lambda s: None)
    assert result.ok and container.restarts == 1 and result.total_ms >= 0


def test_reload_reports_failure(agent, artifacts_dir):
    from ascam_ontop_agent.config import AgentConfig
    from ascam_ontop_agent.reloader import reload_ontop
    cfg = AgentConfig(artifacts_dir=artifacts_dir)
    failed_restart = reload_ontop(cfg, docker_client=FakeDockerForReload(FakeOntopContainer(fails=True)),
                                 http=sparql_after(0), sleep=lambda s: None)
    assert not failed_restart.ok and 'cannot be restarted' in failed_restart.error

    not_ready = reload_ontop(cfg, docker_client=FakeDockerForReload(FakeOntopContainer()),
                              http=sparql_after(10_000), ready_timeout=0.05, sleep=lambda s: None)
    assert not not_ready.ok and 'not ready' in not_ready.error


def test_reload_endpoint_uses_injected_docker(agent, artifacts_dir, monkeypatch):
    container = FakeOntopContainer()
    agent.app.state.docker = FakeDockerForReload(container)
    # fake SPARQL endpoint: the reload will fail waiting, but the restart still happens
    monkeypatch.setattr('ascam_ontop_agent.app.reload_ontop',
                        lambda cfg, docker_client=None, container_name=None: __import__('ascam_ontop_agent.reloader',
                                                                   fromlist=['ReloadResult'])
                        .ReloadResult(True, 800, 5200, 6000))
    body = agent.post('/api/v1/reload').json()
    assert body == {'ok': True, 'stop_ms': 800, 'ready_ms': 5200, 'total_ms': 6000, 'error': None}


def test_prune_endpoint_keeps_latest(agent, artifacts_dir):
    for i in range(4):
        agent.put('/api/v1/artifacts/r2rml', json={'content': MAPPING + f'# version {i}\n'})
    assert len(agent.get('/api/v1/backups').json()) == 4
    result = agent.post('/api/v1/backups/prune', params={'keep': 2}).json()
    assert result == {'removed': 2, 'keep': 2}
    assert len(agent.get('/api/v1/backups').json()) == 2
