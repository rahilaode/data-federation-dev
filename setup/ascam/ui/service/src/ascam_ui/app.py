"""
Konsol administrator ASCAM: backend-for-frontend.

Peramban tidak pernah memegang token layanan. Administrator masuk dengan kata sandi; sesi
disimpan pada cookie HttpOnly bertanda tangan (SameSite=Strict). Permintaan ke Knowledge
diteruskan lewat DAFTAR IZIN: hanya pembacaan, keputusan rencana, notifikasi, pemeriksaan
koneksi, sync, analisis dampak, dan kebijakan adaptasi. Setiap permintaan yang mengubah
keadaan wajib membawa header `X-ASCAM-UI`, yang tidak dapat dikirim lintas situs tanpa CORS.
Nama administrator diteruskan sebagai `X-ASCAM-User` sehingga jejak audit mencatat manusianya.
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
IZIN = {
    'GET': [
        r'obdf', r'obdf/\d+',
        r'obdf/\d+/(sources|targets|credentials|settings|naming-policy|type-mappings|audit|'
        r'versions|events|plans|executions|notifications|sync-runs)',
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


def diizinkan(metode: str, path: str) -> bool:
    return any(re.fullmatch(pola, path) for pola in IZIN.get(metode, []))


def create_app(settings: Settings | None = None,
               transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or Settings()
    if not settings.session_key:
        raise RuntimeError('kunci sesi UI belum diset (ASCAM_UI_SESSION_KEY_FILE)')
    app = FastAPI(title='ASCAM Konsol', version='0.1.0', docs_url=None, redoc_url=None)
    app.add_middleware(SessionMiddleware, secret_key=settings.session_key,
                       session_cookie='ascam_sesi', max_age=settings.session_max_age,
                       same_site='strict', https_only=settings.secure_cookie)
    app.state.settings = settings
    app.state.http = httpx.AsyncClient(transport=transport, timeout=60.0)

    @app.on_event('shutdown')
    async def _tutup():
        await app.state.http.aclose()

    # ── autentikasi ────────────────────────────────────────────────────────────
    def pengguna(request: Request) -> str:
        nama = request.session.get('user')
        if not nama:
            raise HTTPException(401, 'Silakan masuk terlebih dahulu')
        return nama

    def pengguna_mengubah(request: Request, nama: str = Depends(pengguna)) -> str:
        if request.headers.get('X-ASCAM-UI') != '1':
            raise HTTPException(403, 'Permintaan tanpa header X-ASCAM-UI ditolak')
        return nama

    @app.post('/api/ui/login')
    async def login(body: LoginIn, request: Request):
        benar_nama = hmac.compare_digest(body.username, settings.admin_user)
        benar_sandi = bool(settings.admin_password) and hmac.compare_digest(
            body.password, settings.admin_password or '')
        if not (benar_nama and benar_sandi):
            await asyncio.sleep(0.5)                  # memperlambat tebakan kata sandi
            raise HTTPException(401, 'Nama pengguna atau kata sandi salah')
        request.session.clear()
        request.session['user'] = body.username
        return {'user': body.username}

    @app.post('/api/ui/logout')
    async def logout(request: Request):
        request.session.clear()
        return {'ok': True}

    @app.get('/api/ui/me')
    async def me(nama: str = Depends(pengguna)):
        return {'user': nama, 'obdf': settings.obdf_name}

    # ── penerus ke Knowledge ───────────────────────────────────────────────────
    async def knowledge(metode: str, path: str, nama: str, tipe: str | None = None,
                        **kwargs) -> httpx.Response:
        headers = {'Authorization': f'Bearer {settings.knowledge_token}', 'X-ASCAM-User': nama}
        if tipe:
            headers['Content-Type'] = tipe
        return await app.state.http.request(metode, f'{settings.knowledge_url}/api/v1/{path}',
                                            headers=headers, **kwargs)

    async def teruskan(request: Request, path: str, nama: str) -> Response:
        if not diizinkan(request.method, path):
            raise HTTPException(403, f'{request.method} /{path} tidak diizinkan dari konsol')
        body = await request.body()
        try:
            hasil = await knowledge(request.method, path, nama, params=request.query_params,
                                    content=body or None,
                                    tipe=request.headers.get('content-type'))
        except httpx.HTTPError as exc:
            raise HTTPException(502, f'Knowledge tidak dapat dihubungi: {type(exc).__name__}')
        return Response(hasil.content, status_code=hasil.status_code,
                        media_type=hasil.headers.get('content-type', 'application/json'))

    @app.get('/api/k/{path:path}')
    async def baca_knowledge(path: str, request: Request, nama: str = Depends(pengguna)):
        return await teruskan(request, path, nama)

    @app.api_route('/api/k/{path:path}', methods=['POST', 'PUT'])
    async def ubah_knowledge(path: str, request: Request, nama: str = Depends(pengguna_mengubah)):
        return await teruskan(request, path, nama)

    # ── ringkasan dan kesehatan ────────────────────────────────────────────────
    async def json_atau_galat(url: str, headers: dict | None = None) -> dict:
        try:
            hasil = await app.state.http.get(url, headers=headers, timeout=5.0)
            if hasil.status_code >= 400:
                return {'_galat': f'HTTP {hasil.status_code}'}
            return hasil.json()
        except (httpx.HTTPError, ValueError) as exc:
            return {'_galat': type(exc).__name__}

    @app.get('/api/ui/health')
    async def kesehatan(nama: str = Depends(pengguna)):
        knowledge_siap, orch, exe, agen = await asyncio.gather(
            json_atau_galat(f'{settings.knowledge_url}/ready'),
            json_atau_galat(f'{settings.orchestrator_url}/health'),
            json_atau_galat(f'{settings.executor_url}/health'),
            json_atau_galat(f'{settings.agent_url}/health'))
        return {'knowledge': knowledge_siap, 'orchestrator': orch, 'executor': exe, 'agent': agen}

    @app.get('/api/ui/overview')
    async def ringkasan(nama: str = Depends(pengguna)):
        daftar = (await knowledge('GET', 'obdf', nama)).json()
        obdf = next((o for o in daftar if o['name'] == settings.obdf_name), None)
        if obdf is None:
            raise HTTPException(404, f'OBDF {settings.obdf_name} belum terdaftar di Knowledge')
        oid = obdf['id']
        versi, event, menunggu, eksekusi, notif = await asyncio.gather(
            knowledge('GET', f'obdf/{oid}/versions', nama, params={'limit': 1}),
            knowledge('GET', f'obdf/{oid}/events', nama, params={'limit': 8}),
            knowledge('GET', f'obdf/{oid}/plans', nama, params={'status': 'pending_approval'}),
            knowledge('GET', f'obdf/{oid}/executions', nama, params={'limit': 6}),
            knowledge('GET', f'obdf/{oid}/notifications', nama, params={'unread': True}))
        return {'obdf': obdf, 'active_version': (versi.json() or [None])[0],
                'events': event.json(), 'pending': menunggu.json(),
                'executions': eksekusi.json(), 'notifications': notif.json()}

    # ── perbandingan artefak antarversi ─────────────────────────────────────────
    @app.get('/api/ui/diff')
    async def beda(dari: int, ke: int, kind: str, nama: str = Depends(pengguna)):
        if not re.fullmatch(r'[a-z0-9_]+', kind):          # r2rml memuat angka
            raise HTTPException(400, 'jenis artefak tidak valid')
        a, b = await asyncio.gather(knowledge('GET', f'versions/{dari}/artifacts/{kind}', nama),
                                    knowledge('GET', f'versions/{ke}/artifacts/{kind}', nama))
        if a.status_code == 404 or b.status_code == 404:
            raise HTTPException(404, f'artefak {kind} tidak ada pada salah satu versi')
        isi_a, isi_b = a.json()['content'], b.json()['content']
        baris = list(difflib.unified_diff(isi_a.splitlines(), isi_b.splitlines(),
                                          fromfile=f'versi {dari}', tofile=f'versi {ke}',
                                          lineterm='', n=3))
        return {'kind': kind, 'dari': dari, 'ke': ke, 'identik': not baris,
                'tambah': sum(1 for x in baris if x.startswith('+') and not x.startswith('+++')),
                'hapus': sum(1 for x in baris if x.startswith('-') and not x.startswith('---')),
                'diff': baris}

    # ── kendali Executor ────────────────────────────────────────────────────────
    @app.post('/api/ui/executor/{aksi}')
    async def kendali(aksi: str, nama: str = Depends(pengguna_mengubah)):
        if aksi not in ('pause', 'resume'):
            raise HTTPException(404, 'aksi tidak dikenal')
        try:
            hasil = await app.state.http.post(f'{settings.executor_url}/control/{aksi}')
        except httpx.HTTPError as exc:
            raise HTTPException(502, f'Executor tidak dapat dihubungi: {type(exc).__name__}')
        return JSONResponse(hasil.json(), status_code=hasil.status_code)

    # ── berkas statis ──────────────────────────────────────────────────────────
    app.mount('/static', StaticFiles(directory=STATIC), name='static')

    @app.get('/')
    async def indeks():
        return FileResponse(STATIC / 'index.html')

    @app.get('/healthz')
    async def healthz():
        return {'status': 'ok'}

    return app
