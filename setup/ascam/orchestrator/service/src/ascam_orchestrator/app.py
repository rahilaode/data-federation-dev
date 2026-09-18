"""API kecil Orchestrator: kesehatan dan statistik pemrosesan."""
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import Settings
from .worker import Orchestrator

log = logging.getLogger('ascam.orchestrator')


def create_app(settings: Settings | None = None, orchestrator: Orchestrator | None = None,
               run_worker: bool = True) -> FastAPI:
    settings = settings or Settings()
    worker = orchestrator or Orchestrator(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        thread = None
        if run_worker:
            def loop():
                try:
                    worker.run()
                except Exception as exc:               # noqa: BLE001 — kesehatan tetap terlihat
                    worker.stats.state = 'failed'
                    worker.stats.last_error = f'{type(exc).__name__}: {exc}'[:300]
                    log.exception('pekerja berhenti')
            thread = threading.Thread(target=loop, name='ascam-orchestrator', daemon=True)
            thread.start()
        yield
        worker.stop()
        if thread:
            thread.join(timeout=10)

    app = FastAPI(title='ASCAM Orchestrator', version='0.1.0', lifespan=lifespan)
    app.state.worker = worker

    @app.get('/health', tags=['kesehatan'])
    def health():
        stats = worker.stats.snapshot()
        return {'status': 'ok' if stats['state'] in ('running', 'starting') else 'degraded', **stats}

    return app
