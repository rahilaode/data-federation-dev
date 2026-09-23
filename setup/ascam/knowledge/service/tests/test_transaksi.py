"""
Regresi F6: transaksi harus ter-commit SEBELUM respons dikirim ke klien.

Sebelumnya commit dilakukan di penutup dependensi get_db, yang pada FastAPI modern berjalan
setelah respons diterima klien. Executor yang memanggil /sync lalu segera /finish dengan
candidate_spec_version_id dari versi baru kadang mendahului commit tersebut, sehingga kunci
asingnya dilanggar (409) dan eksekusi tertinggal berstatus running (5 dari 60 run evaluasi).
"""
import asyncio

import httpx
from sqlalchemy.orm import Session

from test_impact import obdf                      # noqa: F401 — fixture OBDF tersinkron


def _urutan(api, monkeypatch, metode, path, **kwargs) -> tuple[list[str], int]:
    urutan: list[str] = []
    asli = Session.commit

    def commit(self):
        urutan.append('commit')
        return asli(self)

    monkeypatch.setattr(Session, 'commit', commit)
    app = api.app_ref

    async def terbungkus(scope, receive, send):
        async def kirim(pesan):
            if pesan['type'] == 'http.response.start':
                urutan.append('respons')
            await send(pesan)
        await app(scope, receive, kirim)

    async def jalankan():
        transport = httpx.ASGITransport(app=terbungkus)
        async with httpx.AsyncClient(transport=transport, base_url='http://uji') as klien:
            return await klien.request(metode, path, headers=dict(api.headers), **kwargs)

    respons = asyncio.run(jalankan())
    return urutan, respons.status_code


def test_write_is_committed_before_the_response_is_sent(api, obdf, monkeypatch):
    urutan, status = _urutan(api, monkeypatch, 'PUT',
                             f'/api/v1/obdf/{obdf}/settings/adaptation.add_column',
                             json={'value': {'mode': 'hitl'}})
    assert status == 200
    assert 'commit' in urutan and urutan.index('commit') < urutan.index('respons'), urutan


def test_failed_request_is_rolled_back_and_not_committed(api, obdf, monkeypatch):
    urutan, status = _urutan(api, monkeypatch, 'POST', '/api/v1/executions/999999/finish',
                             json={'status': 'succeeded'})
    assert status == 404
    assert 'commit' not in urutan


def test_integrity_error_raised_at_commit_maps_to_conflict(api, obdf, monkeypatch):
    """Bila pelanggaran integritas baru terdeteksi saat commit (setelah handler selesai), klien
    tetap menerima 409 dan perubahan dibatalkan, bukan 200 yang diikuti kegagalan diam-diam."""
    from sqlalchemy.exc import IntegrityError

    def commit_gagal(self):
        raise IntegrityError('INSERT', {}, Exception('pelanggaran kunci asing tiruan'))

    monkeypatch.setattr(Session, 'commit', commit_gagal)
    r = api.put(f'/api/v1/obdf/{obdf}/settings/adaptation.add_column', json={'value': {'mode': 'auto'}})
    assert r.status_code == 409


def test_every_route_commits_before_responding(api):
    """Router baru yang lupa memakai TransactionalRoute akan kembali meng-commit setelah respons."""
    from fastapi.routing import APIRoute
    from ascam_knowledge.api.transaction import TransactionalRoute

    def telusuri(routes):
        # FastAPI lama menyalin rute ke aplikasi; FastAPI baru menyimpannya di _IncludedRouter
        for r in routes:
            sub = getattr(r, 'original_router', None) or getattr(r, 'router', None)
            if isinstance(r, APIRoute):
                yield r
            elif sub is not None and hasattr(sub, 'routes'):
                yield from telusuri(sub.routes)

    rute = list(telusuri(api.app_ref.routes))
    lepas = [r.path for r in rute if not isinstance(r, TransactionalRoute)]
    assert rute and not lepas, f'rute tanpa TransactionalRoute: {lepas}'
