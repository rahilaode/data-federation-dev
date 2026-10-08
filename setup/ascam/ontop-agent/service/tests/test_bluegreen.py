"""Blue-green lapisan OBDA (ADR-0022), tanpa Docker sungguhan."""
import json

import httpx
import pytest

from ascam_ontop_agent.bluegreen import BlueGreen, BlueGreenError, other, pin_version, upstream_conf
from ascam_ontop_agent.config import AgentConfig
from ascam_ontop_agent.writer import Conflict, _digest
from conftest import MAPPING, ONTOLOGY, PROPERTIES, TOKEN, FakeRunner


class FakeContainer:
    def __init__(self, name, status='exited'):
        self.name, self.status, self.log = name, status, []

    def reload(self):
        pass

    def start(self):
        self.log.append('start'); self.status = 'running'

    def restart(self, timeout=None):
        self.log.append('restart'); self.status = 'running'

    def stop(self, timeout=None):
        self.log.append('stop'); self.status = 'exited'

    def exec_run(self, cmd):
        self.log.append(' '.join(cmd))
        return (self.exec_code, b'nginx: reload gagal') if hasattr(self, 'exec_code') else (0, b'')


class FakeDocker:
    def __init__(self):
        self.items = {n: FakeContainer(n) for n in
                      ('vkg-system-ontop-blue', 'vkg-system-ontop-green', 'vkg-system-ontop-teiid')}
        self.items['vkg-system-ontop-blue'].status = 'running'
        self.items['vkg-system-ontop-teiid'].status = 'running'
        self.containers = self

    def get(self, name):
        return self.items[name]


def ready_http(down=()):
    """Endpoint menjawab 200 kecuali host yang disebut `down`."""
    def handler(request):
        return httpx.Response(503 if request.url.host in down else 200, json={'boolean': True})
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def lab(tmp_path):
    config = tmp_path / 'config'; config.mkdir()
    (config / 'mapping.ttl').write_text(MAPPING)
    (config / 'ontology_file.ttl').write_text(ONTOLOGY)
    (config / 'government.docker.properties').write_text(PROPERTIES)
    slots = tmp_path / 'slots'
    for color in ('blue', 'green'):
        (slots / color).mkdir(parents=True)
    (slots / 'state.json').write_text(json.dumps({'active': 'blue', 'vdb_version': '1'}))
    runtime = tmp_path / 'proxy'; runtime.mkdir()
    (runtime / 'upstream.conf').write_text(upstream_conf('vkg-system-ontop-blue'))
    cfg = AgentConfig(artifacts_dir=config, slots_dir=slots, proxy_runtime_dir=runtime)
    docker = FakeDocker()
    bg = BlueGreen(cfg, runner=FakeRunner(), docker_client=docker, http=ready_http(),
                   sleep=lambda s: None)
    return bg, docker, cfg


def test_pin_version_replaces_existing_version():
    pinned = pin_version(PROPERTIES, 3)
    assert 'jdbc.url=jdbc:teiid:government@mm://teiid:31000;version=3' in pinned
    assert pin_version(pinned, 4).count(';version=') == 1 and ';version=4' in pin_version(pinned, 4)
    assert 'SANGAT-RAHASIA' in pinned                      # baris lain tidak diubah
    with pytest.raises(BlueGreenError):
        pin_version('jdbc.user=x\n', 2)


def test_other_color():
    assert other('blue') == 'green' and other('green') == 'blue'


def test_prepare_writes_standby_slot_only(lab):
    bg, _, cfg = lab
    result = bg.prepare('M2', 'T2', '2', {'r2rml': _digest(MAPPING.encode())})
    assert result.ok and result.color == 'green'
    green = cfg.slots_dir / 'green'
    assert (green / 'mapping.ttl').read_text() == 'M2' and (green / 'ontology_file.ttl').read_text() == 'T2'
    assert ';version=2' in (green / 'government.docker.properties').read_text()
    assert cfg.path_of('r2rml').read_text() == MAPPING      # instance aktif tidak tersentuh
    assert bg.state()['standby_vdb_version'] == '2'


def test_prepare_rejects_changed_active_artifact(lab):
    bg, _, _ = lab
    with pytest.raises(Conflict):
        bg.prepare('M2', 'T2', '2', {'r2rml': 'f' * 64})


def test_validate_uses_standby_volumes(lab):
    bg, _, _ = lab
    color, result = bg.validate('jdbc:teiid:government@mm://teiid:31000;version=2')
    assert color == 'green' and result.ok
    assert bg.runner.containers == ['vkg-system-ontop-green']


def test_start_starts_standby_and_waits(lab):
    bg, docker, _ = lab
    result = bg.start()
    assert result.ok and docker.items['vkg-system-ontop-green'].log == ['start']
    assert docker.items['vkg-system-ontop-blue'].log == []   # instance aktif tidak disentuh


def test_start_reports_unready_endpoint(lab):
    bg, _, _ = lab
    bg.http = ready_http(down={'vkg-system-ontop-green'})
    result = bg.start(ready_timeout=0.01)
    assert not result.ok and 'tidak siap' in result.error


def test_switch_redirects_proxy_promotes_and_retires(lab):
    bg, docker, cfg = lab
    bg.prepare('M2', 'T2', '2'); bg.start()
    result = bg.switch()
    assert result.ok and result.color == 'green' and result.detail['retired'] == 'blue'
    assert 'vkg-system-ontop-green' in (cfg.proxy_runtime_dir / 'upstream.conf').read_text()
    assert docker.items['vkg-system-ontop-teiid'].log == ['nginx -s reload']
    assert docker.items['vkg-system-ontop-blue'].log == ['stop']
    assert cfg.path_of('r2rml').read_text() == 'M2'          # artefak kanonik = instance aktif
    assert set(result.detail['backups']) == {'r2rml', 'ontology'}
    state = bg.state()
    assert state['active'] == 'green' and state['vdb_version'] == '2' and 'standby_vdb_version' not in state


def test_failed_proxy_reload_keeps_old_upstream(lab):
    bg, docker, cfg = lab
    bg.prepare('M2', 'T2', '2'); bg.start()
    docker.items['vkg-system-ontop-teiid'].exec_code = 1
    result = bg.switch()
    assert not result.ok and 'nginx' in result.error
    assert 'vkg-system-ontop-blue' in (cfg.proxy_runtime_dir / 'upstream.conf').read_text()
    assert bg.state()['active'] == 'blue' and cfg.path_of('r2rml').read_text() == MAPPING
    assert docker.items['vkg-system-ontop-blue'].log == []


def test_discard_stops_standby_without_switch(lab):
    bg, docker, cfg = lab
    bg.prepare('M2', 'T2', '2'); bg.start()
    result = bg.discard()
    assert result.ok and docker.items['vkg-system-ontop-green'].log == ['start', 'stop']
    assert bg.state()['active'] == 'blue' and 'standby_vdb_version' not in bg.state()
    assert cfg.path_of('r2rml').read_text() == MAPPING


def test_two_adaptations_alternate_colors(lab):
    bg, docker, cfg = lab
    bg.prepare('M2', 'T2', '2'); bg.start(); bg.switch()
    bg.prepare('M3', 'T3', '3'); assert bg.start().color == 'blue'
    assert bg.switch().ok and bg.state() == {'active': 'blue', 'vdb_version': '3'}
    assert cfg.path_of('r2rml').read_text() == 'M3'
    assert 'vkg-system-ontop-blue' in (cfg.proxy_runtime_dir / 'upstream.conf').read_text()


def test_reset_rebuilds_active_from_canonical(lab):
    bg, docker, cfg = lab
    bg.prepare('M2', 'T2', '2'); bg.start(); bg.switch()          # aktif: green, versi 2
    cfg.path_of('r2rml').write_text(MAPPING)                       # git checkout oleh harness
    cfg.path_of('ontology').write_text(ONTOLOGY)
    result = bg.reset('1')
    assert result.ok and result.color == 'green'
    green = cfg.slots_dir / 'green'
    assert (green / 'mapping.ttl').read_text() == MAPPING
    assert ';version=1' in (green / 'government.docker.properties').read_text()
    assert bg.state() == {'active': 'green', 'vdb_version': '1'}


def test_missing_state_is_reported(tmp_path):
    cfg = AgentConfig(artifacts_dir=tmp_path, slots_dir=tmp_path / 's', proxy_runtime_dir=tmp_path / 'p')
    with pytest.raises(BlueGreenError):
        BlueGreen(cfg, docker_client=FakeDocker()).state()
    with pytest.raises(BlueGreenError):
        BlueGreen(AgentConfig(artifacts_dir=tmp_path))


def test_api_endpoints(lab):
    from fastapi.testclient import TestClient
    from ascam_ontop_agent.app import create_app
    from ascam_ontop_agent.security import TokenRegistry
    bg, _, cfg = lab
    app = create_app(cfg=cfg, tokens=TokenRegistry([f'executor:{TOKEN}']), runner=bg.runner,
                     bluegreen=bg)
    with TestClient(app) as client:
        client.headers['Authorization'] = f'Bearer {TOKEN}'
        assert client.get('/api/v1/bluegreen').json()['standby_sparql_url'] == \
            'http://vkg-system-ontop-green:8080/sparql'
        assert client.post('/api/v1/bluegreen/prepare', json={
            'r2rml': 'M2', 'ontology': 'T2', 'vdb_version': '2',
            'expected_r2rml_sha256': 'f' * 64}).status_code == 409
        assert client.post('/api/v1/bluegreen/prepare', json={
            'r2rml': 'M2', 'ontology': 'T2', 'vdb_version': '2'}).json()['color'] == 'green'
        assert client.post('/api/v1/bluegreen/validate', json={}).json()['facts']['color'] == 'green'
        assert client.post('/api/v1/bluegreen/start').json()['ok']
        assert client.post('/api/v1/bluegreen/switch').json()['detail']['retired'] == 'blue'
        assert TestClient(app).post('/api/v1/bluegreen/switch').status_code == 401


def test_legacy_reload_and_validate_target_active_instance(lab, monkeypatch):
    from fastapi.testclient import TestClient
    from ascam_ontop_agent.app import create_app
    from ascam_ontop_agent.reloader import ReloadResult
    from ascam_ontop_agent.security import TokenRegistry
    bg, _, cfg = lab
    bg.prepare('M2', 'T2', '2'); bg.start(); bg.switch()          # aktif: green
    dipanggil = []
    monkeypatch.setattr('ascam_ontop_agent.app.reload_ontop',
                        lambda cfg, docker_client=None, container_name=None:
                        dipanggil.append(container_name) or ReloadResult(True, 1, 1, 2))
    app = create_app(cfg=cfg, tokens=TokenRegistry([f'executor:{TOKEN}']), runner=bg.runner,
                     bluegreen=bg)
    with TestClient(app) as client:
        client.headers['Authorization'] = f'Bearer {TOKEN}'
        assert client.post('/api/v1/reload').json()['ok']
        client.post('/api/v1/validate', json={})
    assert dipanggil == ['vkg-system-ontop-green']
    assert bg.runner.containers[-1] == 'vkg-system-ontop-green'
