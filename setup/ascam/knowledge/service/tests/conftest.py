"""
Fixture pengujian Knowledge.

Uji ini MENGHAPUS dan MEMBUAT ULANG skema (downgrade base -> upgrade head), sehingga
hanya boleh dijalankan pada basis data khusus uji: nama basis data wajib berakhiran
`_test`. URL diberikan lewat ASCAM_KNOWLEDGE_DB_URL.
"""
import os
import pathlib

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture(scope='session')
def db_url():
    url = os.getenv('ASCAM_KNOWLEDGE_DB_URL')
    if not url:
        pytest.skip('ASCAM_KNOWLEDGE_DB_URL belum diset')
    if not (make_url(url).database or '').endswith('_test'):
        pytest.exit('Basis data uji harus bernama *_test (uji menghapus skema).', returncode=2)
    return url


@pytest.fixture(scope='session')
def alembic_cfg(db_url):
    # dipakai juga oleh create_app() saat paket dipasang (bukan dijalankan dari folder sumber)
    os.environ.setdefault('ASCAM_KNOWLEDGE_ALEMBIC_INI', str(ROOT / 'alembic.ini'))
    return Config(str(ROOT / 'alembic.ini'))


@pytest.fixture(scope='session')
def migrated(alembic_cfg):
    command.downgrade(alembic_cfg, 'base')
    command.upgrade(alembic_cfg, 'head')
    yield


@pytest.fixture
def engine(db_url, migrated):
    eng = create_engine(db_url)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    """Setiap uji berjalan dalam satu transaksi yang di-rollback di akhir."""
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn, join_transaction_mode='create_savepoint')
    yield s
    s.close()
    trans.rollback()
    conn.close()


def expect_db_error(session, sqlstate, action):
    """Jalankan `action` di savepoint; pastikan gagal dengan SQLSTATE tertentu."""
    sp = session.begin_nested()
    with pytest.raises(DBAPIError) as info:
        action()
        session.flush()
    sp.rollback()
    got = getattr(info.value.orig, 'sqlstate', None)
    assert got == sqlstate, f'SQLSTATE {got} != {sqlstate}: {info.value.orig}'
    return info.value


# ── fixture API ──────────────────────────────────────────────────────────────────
TOKENS = {'ui': 'token-ui-uji', 'orchestrator': 'token-orch-uji'}


@pytest.fixture
def keys():
    from ascam_knowledge.security.crypto import generate_key
    return [generate_key()]


@pytest.fixture
def api(migrated, alembic_cfg, db_url, keys):
    from fastapi.testclient import TestClient
    from ascam_knowledge.api.app import create_app
    from ascam_knowledge.db.session import make_session_factory
    from ascam_knowledge.security.auth import TokenRegistry
    from ascam_knowledge.security.crypto import SecretBox
    factory = make_session_factory(db_url)
    app = create_app(session_factory=factory, secret_box=SecretBox(keys),
                     tokens=TokenRegistry([f'{k}:{v}' for k, v in TOKENS.items()]))
    with TestClient(app) as client:
        client.headers['Authorization'] = f"Bearer {TOKENS['ui']}"
        client.headers['X-ASCAM-User'] = 'penguji'
        client.factory = factory
        client.app_ref = app
        yield client
    factory.kw['bind'].dispose()


def pytest_configure(config):
    # peringatan pustaka pihak ketiga pada TestClient; tidak terkait kode ASCAM
    config.addinivalue_line('filterwarnings', 'ignore::DeprecationWarning:starlette.*')
    config.addinivalue_line('filterwarnings', 'ignore::DeprecationWarning:fastapi.*')
    config.addinivalue_line('filterwarnings', 'ignore:Using `httpx` with `starlette.testclient`')
