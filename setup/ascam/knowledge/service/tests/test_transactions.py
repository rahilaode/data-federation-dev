"""
Regression F6: the transaction must be committed BEFORE the response is sent to the client.

The commit used to happen in the finaliser of the get_db dependency, which in modern FastAPI
runs after the client has received the response. The Executor, which calls /sync and right
after /finish with the candidate_spec_version_id of the new version, sometimes overtook that
commit, so its foreign key was violated (409) and the execution stayed `running` (5 of 60 runs).
"""
import asyncio

import httpx
from sqlalchemy.orm import Session

from test_impact import obdf                      # noqa: F401 — synced OBDF fixture


def _order(api, monkeypatch, method, path, **kwargs) -> tuple[list[str], int]:
    order: list[str] = []
    original = Session.commit

    def commit(self):
        order.append('commit')
        return original(self)

    monkeypatch.setattr(Session, 'commit', commit)
    app = api.app_ref

    async def wrapped(scope, receive, send):
        async def send_wrapped(message):
            if message['type'] == 'http.response.start':
                order.append('response')
            await send(message)
        await app(scope, receive, send_wrapped)

    async def run():
        transport = httpx.ASGITransport(app=wrapped)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as client:
            return await client.request(method, path, headers=dict(api.headers), **kwargs)

    response = asyncio.run(run())
    return order, response.status_code


def test_write_is_committed_before_the_response_is_sent(api, obdf, monkeypatch):
    order, status = _order(api, monkeypatch, 'PUT',
                             f'/api/v1/obdf/{obdf}/settings/adaptation.add_column',
                             json={'value': {'mode': 'hitl'}})
    assert status == 200
    assert 'commit' in order and order.index('commit') < order.index('response'), order


def test_failed_request_is_rolled_back_and_not_committed(api, obdf, monkeypatch):
    order, status = _order(api, monkeypatch, 'POST', '/api/v1/executions/999999/finish',
                             json={'status': 'succeeded'})
    assert status == 404
    assert 'commit' not in order


def test_integrity_error_raised_at_commit_maps_to_conflict(api, obdf, monkeypatch):
    """When an integrity violation is detected only at commit (after the handler finished), the
    client still receives 409 and the change is undone, instead of a 200 followed by a silent failure."""
    from sqlalchemy.exc import IntegrityError

    def failing_commit(self):
        raise IntegrityError('INSERT', {}, Exception('fake foreign key violation'))

    monkeypatch.setattr(Session, 'commit', failing_commit)
    r = api.put(f'/api/v1/obdf/{obdf}/settings/adaptation.add_column', json={'value': {'mode': 'auto'}})
    assert r.status_code == 409


def test_every_route_commits_before_responding(api):
    """A new router that forgets TransactionalRoute would commit after the response again."""
    from fastapi.routing import APIRoute
    from ascam_knowledge.api.transaction import TransactionalRoute

    def walk(routes):
        # older FastAPI copies routes into the app; newer FastAPI keeps them in _IncludedRouter
        for r in routes:
            sub = getattr(r, 'original_router', None) or getattr(r, 'router', None)
            if isinstance(r, APIRoute):
                yield r
            elif sub is not None and hasattr(sub, 'routes'):
                yield from walk(sub.routes)

    routes_found = list(walk(api.app_ref.routes))
    detached = [r.path for r in routes_found if not isinstance(r, TransactionalRoute)]
    assert routes_found and not detached, f'routes without TransactionalRoute: {detached}'
