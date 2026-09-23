"""
Batas transaksi per permintaan yang ter-commit SEBELUM respons dikirim.

Penutup dependensi `yield` (tempat commit semula dilakukan) dijalankan FastAPI modern setelah
respons diterima klien. Klien yang langsung memakai hasil sebuah penulisan, misalnya Executor
yang memanggil /sync lalu /finish dengan versi yang baru dibuat, dapat mendahului commit itu:
kunci asing dilanggar dan permintaan kedua ditolak (temuan evaluasi F6, 5 dari 60 run). Kelas
rute ini melakukan commit setelah handler selesai tetapi sebelum respons dikembalikan, dan
melakukan rollback bila handler atau commit gagal, sehingga galat integritas tetap sampai ke
klien sebagai 409 melalui penangan galat aplikasi.
"""
from collections.abc import Callable

from fastapi import Request, Response
from fastapi.routing import APIRoute
from fastapi.concurrency import run_in_threadpool

KUNCI_SESI = 'ascam_db'
KUNCI_SELESAI = 'ascam_tx_selesai'


class TransactionalRoute(APIRoute):
    def get_route_handler(self) -> Callable:
        handler = super().get_route_handler()

        async def route_handler(request: Request) -> Response:
            try:
                response = await handler(request)
            except Exception:
                await _akhiri(request, commit=False)
                raise
            await _akhiri(request, commit=True)       # galat commit diteruskan ke penangan galat
            return response

        return route_handler


async def _akhiri(request: Request, commit: bool) -> None:
    db = getattr(request.state, KUNCI_SESI, None)
    if db is None or getattr(request.state, KUNCI_SELESAI, False):
        return
    setattr(request.state, KUNCI_SELESAI, True)
    if not commit:
        await run_in_threadpool(db.rollback)
        return
    try:
        await run_in_threadpool(db.commit)
    except Exception:
        await run_in_threadpool(db.rollback)
        raise
