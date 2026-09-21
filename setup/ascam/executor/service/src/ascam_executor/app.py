"""API kecil Executor: kesehatan dan statistik eksekusi."""
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import Settings
from .worker import ExecutorWorker

log = logging.getLogger('ascam.executor')


def create_app(settings: Settings | None = None, worker: ExecutorWorker | None = None,
               run_worker: bool | None = None) -> FastAPI:
    settings = settings or Settings()
    worker = worker or ExecutorWorker(settings)
    jalankan = settings.enabled if run_worker is None else run_worker

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        thread = None
        if jalankan:
            def loop():
                try:
                    worker.run()
                except Exception as exc:            # noqa: BLE001
                    worker.stats.state = 'failed'
                    worker.stats.last_error = f'{type(exc).__name__}: {exc}'[:300]
                    log.exception('pekerja berhenti')
            thread = threading.Thread(target=loop, name='ascam-executor', daemon=True)
            thread.start()
        else:
            worker.stats.state = 'disabled'
        yield
        worker.stop()
        if thread:
            thread.join(timeout=30)

    app = FastAPI(title='ASCAM Executor', version='0.1.0', lifespan=lifespan)
    app.state.worker = worker

    @app.post('/control/pause', tags=['kendali'])
    def pause():
        """Menjeda pengambilan rencana baru; eksekusi yang sedang berjalan tetap diselesaikan.

        Dipakai prosedur eksperimen agar perubahan skema pada langkah pemulihan antar-run tidak
        ikut dieksekusi. Port layanan hanya terikat ke 127.0.0.1.
        """
        worker.pause()
        return {'paused': True}

    @app.post('/control/resume', tags=['kendali'])
    def resume():
        worker.resume()
        return {'paused': False}

    @app.get('/health', tags=['kesehatan'])
    def health():
        stats = worker.stats.snapshot()
        sehat = stats['state'] in ('running', 'starting', 'disabled', 'retrying')
        return {'status': 'ok' if sehat else 'degraded', **stats}

    return app
