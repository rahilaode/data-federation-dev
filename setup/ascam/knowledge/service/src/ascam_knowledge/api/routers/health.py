from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

router = APIRouter(tags=['kesehatan'])


@router.get('/health')
def health():
    """Liveness: proses berjalan."""
    return {'status': 'ok'}


@router.get('/ready')
def ready(request: Request):
    """Readiness: basis data dapat dijangkau dan skema berada pada revisi migrasi terbaru."""
    expected = request.app.state.migration_head
    try:
        with request.app.state.session_factory() as db:
            current = db.execute(text('SELECT version_num FROM alembic_version')).scalar()
    except Exception as exc:                       # noqa: BLE001
        return JSONResponse({'status': 'unavailable', 'detail': str(exc)[:200]}, status_code=503)
    body = {'status': 'ok' if current == expected else 'migration_mismatch',
            'schema_revision': current, 'expected_revision': expected}
    return JSONResponse(body, status_code=200 if current == expected else 503)


def migration_head(alembic_ini: str) -> str:
    return ScriptDirectory.from_config(Config(alembic_ini)).get_current_head()
