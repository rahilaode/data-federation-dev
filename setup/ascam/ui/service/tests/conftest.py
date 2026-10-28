"""Fakes of Knowledge, Orchestrator, Executor, and the agent, for testing the console without real services."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from ascam_ui.app import create_app
from ascam_ui.config import Settings

K = 'http://knowledge'
ARTIFACTS = {
    (7, 'r2rml'): '@prefix rr: <http://www.w3.org/ns/r2rml#> .\n<#MapA> a rr:TriplesMap .\n',
    (8, 'r2rml'): '@prefix rr: <http://www.w3.org/ns/r2rml#> .\n<#MapA> a rr:TriplesMap .\n'
                  '<#MapA> rr:predicateObjectMap [ rr:predicate <http://x/email> ] .\n',
}


class FakeKnowledge:
    def __init__(self):
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        host = request.url.host
        if host == 'orchestrator':
            return httpx.Response(200, json={'state': 'running', 'messages': 3})
        if host == 'executor':
            if path.startswith('/control/'):
                return httpx.Response(200, json={'paused': path.endswith('pause')})
            return httpx.Response(200, json={'state': 'running', 'paused': False})
        if host == 'agent':
            raise httpx.ConnectError('agent down')
        if path == '/ready':
            return httpx.Response(200, json={'status': 'ok'})
        if path == '/api/v1/obdf':
            return httpx.Response(200, json=[{'id': 1, 'name': 'bansos'}])
        if path.startswith('/api/v1/versions/') and '/artifacts/' in path:
            parts = path.split('/')
            content = ARTIFACTS.get((int(parts[4]), parts[6]))
            if content is None:
                return httpx.Response(404, json={'detail': 'not found'})
            return httpx.Response(200, json={'content': content})
        if path.endswith('/approve'):
            return httpx.Response(200, json={'id': 5, 'status': 'approved',
                                              'decided_by': f"ui:{request.headers.get('X-ASCAM-User')}",
                                              'received': json.loads(request.content or b'{}')})
        if path.startswith('/api/v1/obdf/1/'):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={'path': path})


@pytest.fixture
def fake():
    return FakeKnowledge()


@pytest.fixture
def console(fake):
    settings = Settings(knowledge_url=K, orchestrator_url='http://orchestrator',
                        executor_url='http://executor', agent_url='http://agent',
                        admin_user='admin', admin_password='a-long-secret',
                        session_key='test-session-key-' + 'x' * 32, knowledge_token='token-ui')
    app = create_app(settings, transport=httpx.MockTransport(fake))
    with TestClient(app) as client:
        yield client


@pytest.fixture
def logged_in(console):
    r = console.post('/api/ui/login', json={'username': 'admin', 'password': 'a-long-secret'})
    assert r.status_code == 200
    return console
