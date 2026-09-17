"""Agen Ontop: pembacaan artefak, sidik jari, proteksi kredensial, validasi."""
import hashlib

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
