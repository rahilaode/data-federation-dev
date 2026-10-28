import pytest
from fastapi.testclient import TestClient

from ascam_ontop_agent.app import create_app
from ascam_ontop_agent.config import AgentConfig
from ascam_ontop_agent.ontop_cli import CommandResult
from ascam_ontop_agent.security import TokenRegistry

TOKEN = 'token-test'
MAPPING = '@prefix rr: <http://www.w3.org/ns/r2rml#> .\n<#MapA> a rr:TriplesMap .\n'
ONTOLOGY = '@prefix owl: <http://www.w3.org/2002/07/owl#> .\n<http://example.org/ont> a owl:Ontology .\n'
PROPERTIES = 'jdbc.url=jdbc:teiid:government@mm://teiid:31000\njdbc.password=VERY-SECRET\n'


class FakeRunner:
    """Stand-in for OntopRunner: records the arguments without running Docker."""
    def __init__(self, result=None):
        self.calls = []
        self.result = result or CommandResult(True, 0, 'Validation completed', 1200)

    def validate(self, db_url=None, container_name=None):
        self.calls.append(db_url)
        self.containers = getattr(self, 'containers', []) + [container_name]
        return self.result


@pytest.fixture
def artifacts_dir(tmp_path):
    (tmp_path / 'mapping.ttl').write_text(MAPPING)
    (tmp_path / 'ontology_file.ttl').write_text(ONTOLOGY)
    (tmp_path / 'government.docker.properties').write_text(PROPERTIES)
    return tmp_path


@pytest.fixture
def runner():
    return FakeRunner()


@pytest.fixture
def agent(artifacts_dir, runner):
    cfg = AgentConfig(artifacts_dir=artifacts_dir)
    app = create_app(cfg=cfg, tokens=TokenRegistry([f'knowledge:{TOKEN}']), runner=runner)
    with TestClient(app) as client:
        client.headers['Authorization'] = f'Bearer {TOKEN}'
        client.cfg = cfg
        yield client
