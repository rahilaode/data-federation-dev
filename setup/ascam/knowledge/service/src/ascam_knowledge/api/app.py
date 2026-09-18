"""Aplikasi FastAPI Knowledge Service."""
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from .. import registry_service as svc
from ..db.session import make_session_factory
from ..security.auth import TokenRegistry
from ..security.crypto import SecretBox
from .routers import checks, config, events, executions, health, impact, registry, sync
from .schemas import ConfigDocument

log = logging.getLogger('ascam.knowledge')


def alembic_ini_path(explicit: str | None = None) -> str:
    """Lokasi alembic.ini: argumen, env ASCAM_KNOWLEDGE_ALEMBIC_INI, folder sumber, atau direktori kerja."""
    candidates = [explicit, os.getenv('ASCAM_KNOWLEDGE_ALEMBIC_INI'),
                  str(Path(__file__).resolve().parents[3] / 'alembic.ini'),
                  str(Path.cwd() / 'alembic.ini')]
    for path in candidates:
        if path and Path(path).is_file():
            return path
    raise RuntimeError('alembic.ini tidak ditemukan; set ASCAM_KNOWLEDGE_ALEMBIC_INI')


def bootstrap(app: FastAPI, path: str) -> None:
    """D5: konfigurasi deklaratif diterapkan saat start (idempoten)."""
    with open(path, encoding='utf-8') as fh:
        doc = ConfigDocument.model_validate(yaml.safe_load(fh))
    with app.state.session_factory() as db, db.begin():
        summary = svc.apply_config(db, doc, app.state.secret_box, actor='system:bootstrap')
    log.info('bootstrap %s: dibuat %d, diubah %d, tetap %d', path, len(summary.created),
             len(summary.updated), len(summary.unchanged))


def create_app(*, session_factory=None, secret_box=None, tokens=None,
               bootstrap_config: str | None = None, alembic_ini: str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.bootstrap_config:
            bootstrap(app, app.state.bootstrap_config)
        yield

    app = FastAPI(title='ASCAM Knowledge Service', version='0.1.0', lifespan=lifespan)
    app.state.session_factory = session_factory or make_session_factory()
    app.state.secret_box = secret_box or SecretBox.from_file(os.environ['ASCAM_KNOWLEDGE_ENCRYPTION_KEYS_FILE'])
    app.state.tokens = tokens or TokenRegistry.from_file(os.environ['ASCAM_KNOWLEDGE_API_TOKENS_FILE'])
    app.state.bootstrap_config = bootstrap_config or os.getenv('ASCAM_KNOWLEDGE_BOOTSTRAP_CONFIG')
    app.state.migration_head = health.migration_head(alembic_ini_path(alembic_ini))

    app.include_router(health.router)
    app.include_router(registry.router)
    app.include_router(config.router)
    app.include_router(checks.router)
    app.include_router(sync.router)
    app.include_router(impact.router)
    app.include_router(events.router)
    app.include_router(executions.router)

    @app.exception_handler(svc.NotFound)
    async def _not_found(_: Request, exc: svc.NotFound):
        return JSONResponse({'detail': str(exc)}, status_code=404)

    @app.exception_handler(svc.Invalid)
    async def _invalid(_: Request, exc: svc.Invalid):
        return JSONResponse({'detail': str(exc)}, status_code=422)

    @app.exception_handler(ValidationError)
    async def _validation(_: Request, exc: ValidationError):
        # validasi aturan yang dijalankan di dalam handler (mis. PATCH target yang digabung)
        errors = [{'loc': e['loc'], 'msg': e['msg']} for e in exc.errors()]
        return JSONResponse({'detail': errors}, status_code=422)

    @app.exception_handler(IntegrityError)
    async def _conflict(_: Request, exc: IntegrityError):
        constraint = getattr(getattr(exc.orig, 'diag', None), 'constraint_name', None)
        return JSONResponse({'detail': 'melanggar integritas data', 'constraint': constraint},
                            status_code=409)

    return app
