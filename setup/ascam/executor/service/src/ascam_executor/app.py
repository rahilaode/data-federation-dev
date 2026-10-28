"""Small Executor API: health and execution statistics."""
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
    run_loop = settings.enabled if run_worker is None else run_worker

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        thread = None
        if run_loop:
            def loop():
                try:
                    worker.run()
                except Exception as exc:            # noqa: BLE001
                    worker.stats.state = 'failed'
                    worker.stats.last_error = f'{type(exc).__name__}: {exc}'[:300]
                    log.exception('worker stopped')
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

    @app.post('/control/pause', tags=['control'])
    def pause():
        """Pause taking new plans; an execution that is running is still completed.

        Used by the experiment procedure so that schema changes of the restoration steps between
        runs are not executed. The service port is bound to 127.0.0.1 only.
        """
        worker.pause()
        return {'paused': True}

    @app.post('/control/resume', tags=['control'])
    def resume():
        worker.resume()
        return {'paused': False}

    @app.get('/health', tags=['health'])
    def health():
        stats = worker.stats.snapshot()
        healthy = stats['state'] in ('running', 'starting', 'disabled', 'retrying')
        return {'status': 'ok' if healthy else 'degraded', **stats}

    return app
