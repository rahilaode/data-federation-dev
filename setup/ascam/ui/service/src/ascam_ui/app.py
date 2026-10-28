"""
ASCAM administrator console: backend-for-frontend.

The browser never holds a service token. The administrator logs in with a password; the session
is kept in a signed HttpOnly cookie (SameSite=Strict). Requests to Knowledge are forwarded through
an ALLOW LIST: reads only (including column lineage), plan decisions, notifications, connection
checks, sync, impact analysis, and adaptation policies. Every request that changes state must carry
the header `X-ASCAM-UI`, which cannot be sent cross-site without CORS. The administrator's name
is forwarded as `X-ASCAM-User`, so the audit trail records the person.
"""
import asyncio
import difflib
import hmac
import re
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from .config import Settings

STATIC = Path(__file__).parent / 'static'
ALLOWED = {
    'GET': [
        r'obdf', r'obdf/\d+',
        r'obdf/\d+/(sources|targets|credentials|settings|naming-policy|type-mappings|audit|'
        r'versions|events|plans|executions|notifications|sync-runs|lineage)',
        r'versions/\d+', r'versions/\d+/artifacts/[a-z0-9_]+', r'plans/\d+',
    ],
    'POST': [
        r'plans/\d+/(approve|reject)', r'notifications/\d+/ack',
        r'obdf/\d+/(checks|sync|impact)', r'targets/\d+/check',
    ],
    'PUT': [r'obdf/\d+/settings/adaptation\.[a-z_]+', r'obdf/\d+/naming-policy'],
}


class LoginIn(BaseModel):
    username: str
    password: str


def allowed(method: str, path: str) -> bool:
    return any(re.fullmatch(pattern, path) for pattern in ALLOWED.get(method, []))


def create_app(settings: Settings | None = None,
               transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or Settings()
    if not settings.session_key:
        raise RuntimeError('the UI session key is not set (ASCAM_UI_SESSION_KEY_FILE)')
    app = FastAPI(title='ASCAM Console', version='0.1.0', docs_url=None, redoc_url=None)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_key,
                       session_cookie='ascam_session', max_age=settings.session_max_age,
                       same_site='strict', https_only=settings.secure_cookie)
    app.state.settings = settings
    app.state.http = httpx.AsyncClient(transport=transport, timeout=60.0)

    @app.on_event('shutdown')
    async def _close():
        await app.state.http.aclose()

    # ── authentication ─────────────────────────────────────────────────────────
    def current_user(request: Request) -> str:
        user = request.session.get('user')
        if not user:
            raise HTTPException(401, 'Please log in first')
        return user

    def changing_user(request: Request, user: str = Depends(current_user)) -> str:
        if request.headers.get('X-ASCAM-UI') != '1':
            raise HTTPException(403, 'Request without the X-ASCAM-UI header rejected')
        return user

    @app.post('/api/ui/login')
    async def login(body: LoginIn, request: Request):
        name_ok = hmac.compare_digest(body.username, settings.admin_user)
        password_ok = bool(settings.admin_password) and hmac.compare_digest(
            body.password, settings.admin_password or '')
        if not (name_ok and password_ok):
            await asyncio.sleep(0.5)                  # slows down password guessing
            raise HTTPException(401, 'Wrong user name or password')
        request.session.clear()
        request.session['user'] = body.username
        return {'user': body.username}

    @app.post('/api/ui/logout')
    async def logout(request: Request):
        request.session.clear()
        return {'ok': True}

    @app.get('/api/ui/me')
    async def me(user: str = Depends(current_user)):
        return {'user': user, 'obdf': settings.obdf_name}

    # ── forwarding to Knowledge ────────────────────────────────────────────────
    async def knowledge(method: str, path: str, user: str, content_type: str | None = None,
                        **kwargs) -> httpx.Response:
        headers = {'Authorization': f'Bearer {settings.knowledge_token}', 'X-ASCAM-User': user}
        if content_type:
            headers['Content-Type'] = content_type
        return await app.state.http.request(method, f'{settings.knowledge_url}/api/v1/{path}',
                                            headers=headers, **kwargs)

    async def forward(request: Request, path: str, user: str) -> Response:
        if not allowed(request.method, path):
            raise HTTPException(403, f'{request.method} /{path} is not allowed from the console')
        body = await request.body()
        try:
            response = await knowledge(request.method, path, user, params=request.query_params,
                                    content=body or None,
                                    content_type=request.headers.get('content-type'))
        except httpx.HTTPError as exc:
            raise HTTPException(502, f'Knowledge cannot be reached: {type(exc).__name__}')
        if response.status_code == 401:
            # Knowledge refused the console's service token. Passing the 401 on would make the
            # browser treat it as an expired session and return to the login form for ever.
            raise HTTPException(502, 'Knowledge rejected the service token of the console: the ui line '
                                     'of secrets/knowledge_api_tokens differs from the one Knowledge '
                                     'loaded; restart both after changing the secrets')
        return Response(response.content, status_code=response.status_code,
                        media_type=response.headers.get('content-type', 'application/json'))

    @app.get('/api/k/{path:path}')
    async def read_knowledge(path: str, request: Request, user: str = Depends(current_user)):
        return await forward(request, path, user)

    @app.api_route('/api/k/{path:path}', methods=['POST', 'PUT'])
    async def change_knowledge(path: str, request: Request, user: str = Depends(changing_user)):
        return await forward(request, path, user)

    # ── overview and health ────────────────────────────────────────────────────
    async def json_or_error(url: str, headers: dict | None = None) -> dict:
        try:
            response = await app.state.http.get(url, headers=headers, timeout=5.0)
            if response.status_code >= 400:
                return {'_error': f'HTTP {response.status_code}'}
            return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            return {'_error': type(exc).__name__}

    @app.get('/api/ui/health')
    async def health(user: str = Depends(current_user)):
        knowledge_ready, orch, exe, agent = await asyncio.gather(
            json_or_error(f'{settings.knowledge_url}/ready'),
            json_or_error(f'{settings.orchestrator_url}/health'),
            json_or_error(f'{settings.executor_url}/health'),
            json_or_error(f'{settings.agent_url}/health'))
        return {'knowledge': knowledge_ready, 'orchestrator': orch, 'executor': exe, 'agent': agent}

    @app.get('/api/ui/overview')
    async def overview(user: str = Depends(current_user)):
        listing = (await knowledge('GET', 'obdf', user)).json()
        obdf = next((o for o in listing if o['name'] == settings.obdf_name), None)
        if obdf is None:
            raise HTTPException(404, f'OBDF {settings.obdf_name} is not registered in Knowledge')
        oid = obdf['id']
        version, event, pending, executions, notif = await asyncio.gather(
            knowledge('GET', f'obdf/{oid}/versions', user, params={'limit': 1}),
            knowledge('GET', f'obdf/{oid}/events', user, params={'limit': 8}),
            knowledge('GET', f'obdf/{oid}/plans', user, params={'status': 'pending_approval'}),
            knowledge('GET', f'obdf/{oid}/executions', user, params={'limit': 6}),
            knowledge('GET', f'obdf/{oid}/notifications', user, params={'unread': True}))
        return {'obdf': obdf, 'active_version': (version.json() or [None])[0],
                'events': event.json(), 'pending': pending.json(),
                'executions': executions.json(), 'notifications': notif.json()}

    # ── comparison of artifacts between versions ────────────────────────────────
    @app.get('/api/ui/diff')
    async def diff(from_version: int, to_version: int, kind: str, user: str = Depends(current_user)):
        if not re.fullmatch(r'[a-z0-9_]+', kind):          # r2rml contains digits
            raise HTTPException(400, 'invalid artifact kind')
        a, b = await asyncio.gather(knowledge('GET', f'versions/{from_version}/artifacts/{kind}', user),
                                    knowledge('GET', f'versions/{to_version}/artifacts/{kind}', user))
        if a.status_code == 404 or b.status_code == 404:
            raise HTTPException(404, f'artifact {kind} does not exist in one of the versions')
        content_a, content_b = a.json()['content'], b.json()['content']
        lines = list(difflib.unified_diff(content_a.splitlines(), content_b.splitlines(),
                                          fromfile=f'version {from_version}', tofile=f'version {to_version}',
                                          lineterm='', n=3))
        return {'kind': kind, 'from': from_version, 'to': to_version, 'identical': not lines,
                'added': sum(1 for x in lines if x.startswith('+') and not x.startswith('+++')),
                'removed': sum(1 for x in lines if x.startswith('-') and not x.startswith('---')),
                'diff': lines}

    # ── Executor control ────────────────────────────────────────────────────────
    @app.post('/api/ui/executor/{action}')
    async def control(action: str, user: str = Depends(changing_user)):
        if action not in ('pause', 'resume'):
            raise HTTPException(404, 'unknown action')
        try:
            response = await app.state.http.post(f'{settings.executor_url}/control/{action}')
        except httpx.HTTPError as exc:
            raise HTTPException(502, f'the Executor cannot be reached: {type(exc).__name__}')
        return JSONResponse(response.json(), status_code=response.status_code)

    # ── static files ───────────────────────────────────────────────────────────
    # The page and its assets must be revalidated on every load: a browser that keeps an older
    # index.html next to a newer app.js (or the reverse) after an upgrade shows a login form
    # whose fields the script cannot find, so logging in does nothing.
    @app.middleware('http')
    async def revalidate_static(request: Request, call_next):
        response = await call_next(request)
        if request.url.path == '/' or request.url.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-cache'
        return response

    app.mount('/static', StaticFiles(directory=STATIC), name='static')

    @app.get('/')
    async def index():
        return FileResponse(STATIC / 'index.html')

    @app.get('/healthz')
    async def healthz():
        return {'status': 'ok'}

    return app
