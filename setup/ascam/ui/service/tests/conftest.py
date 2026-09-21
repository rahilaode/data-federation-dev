"""Tiruan Knowledge, Orchestrator, Executor, dan agen untuk menguji konsol tanpa layanan nyata."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from ascam_ui.app import create_app
from ascam_ui.config import Settings

K = 'http://knowledge'
ARTEFAK = {
    (7, 'r2rml'): '@prefix rr: <http://www.w3.org/ns/r2rml#> .\n<#MapA> a rr:TriplesMap .\n',
    (8, 'r2rml'): '@prefix rr: <http://www.w3.org/ns/r2rml#> .\n<#MapA> a rr:TriplesMap .\n'
                  '<#MapA> rr:predicateObjectMap [ rr:predicate <http://x/email> ] .\n',
}


class KnowledgePalsu:
    def __init__(self):
        self.permintaan: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.permintaan.append(request)
        path = request.url.path
        host = request.url.host
        if host == 'orchestrator':
            return httpx.Response(200, json={'state': 'running', 'messages': 3})
        if host == 'executor':
            if path.startswith('/control/'):
                return httpx.Response(200, json={'paused': path.endswith('pause')})
            return httpx.Response(200, json={'state': 'running', 'paused': False})
        if host == 'agent':
            raise httpx.ConnectError('agen mati')
        if path == '/ready':
            return httpx.Response(200, json={'status': 'ok'})
        if path == '/api/v1/obdf':
            return httpx.Response(200, json=[{'id': 1, 'name': 'bansos'}])
        if path.startswith('/api/v1/versions/') and '/artifacts/' in path:
            bagian = path.split('/')
            isi = ARTEFAK.get((int(bagian[4]), bagian[6]))
            if isi is None:
                return httpx.Response(404, json={'detail': 'tidak ada'})
            return httpx.Response(200, json={'content': isi})
        if path.endswith('/approve'):
            return httpx.Response(200, json={'id': 5, 'status': 'approved',
                                              'decided_by': f"ui:{request.headers.get('X-ASCAM-User')}",
                                              'terima': json.loads(request.content or b'{}')})
        if path.startswith('/api/v1/obdf/1/'):
            return httpx.Response(200, json=[])
        return httpx.Response(200, json={'path': path})


@pytest.fixture
def palsu():
    return KnowledgePalsu()


@pytest.fixture
def konsol(palsu):
    settings = Settings(knowledge_url=K, orchestrator_url='http://orchestrator',
                        executor_url='http://executor', agent_url='http://agent',
                        admin_user='admin', admin_password='rahasia-yang-panjang',
                        session_key='kunci-sesi-uji-' + 'x' * 32, knowledge_token='token-ui')
    app = create_app(settings, transport=httpx.MockTransport(palsu))
    with TestClient(app) as client:
        yield client


@pytest.fixture
def masuk(konsol):
    r = konsol.post('/api/ui/login', json={'username': 'admin', 'password': 'rahasia-yang-panjang'})
    assert r.status_code == 200
    return konsol
