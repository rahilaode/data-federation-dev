"""
Knowledge test fixtures.

These tests DROP AND RECREATE the schemas (downgrade base -> upgrade head), so they may run
only against a database dedicated to tests: its name must end with `_test`. The URL is given
in ASCAM_KNOWLEDGE_DB_URL.
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
        pytest.skip('ASCAM_KNOWLEDGE_DB_URL is not set')
    if not (make_url(url).database or '').endswith('_test'):
        pytest.exit('The test database must be named *_test (the tests drop the schemas).', returncode=2)
    return url


@pytest.fixture(scope='session')
def alembic_cfg(db_url):
    # also used by create_app() when the package is installed (not run from the source folder)
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
    """Every test runs in one transaction that is rolled back at the end."""
    conn = engine.connect()
    trans = conn.begin()
    s = Session(bind=conn, join_transaction_mode='create_savepoint')
    yield s
    s.close()
    trans.rollback()
    conn.close()


def expect_db_error(session, sqlstate, action):
    """Run `action` in a savepoint and expect it to fail with the given SQLSTATE."""
    sp = session.begin_nested()
    with pytest.raises(DBAPIError) as info:
        action()
        session.flush()
    sp.rollback()
    got = getattr(info.value.orig, 'sqlstate', None)
    assert got == sqlstate, f'SQLSTATE {got} != {sqlstate}: {info.value.orig}'
    return info.value


# ── fixture API ──────────────────────────────────────────────────────────────────
TOKENS = {'ui': 'token-ui-test', 'orchestrator': 'token-orch-test'}


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
        client.headers['X-ASCAM-User'] = 'tester'
        client.factory = factory
        client.app_ref = app
        yield client
    factory.kw['bind'].dispose()


def pytest_configure(config):
    # third-party library warnings in TestClient; unrelated to ASCAM code
    config.addinivalue_line('filterwarnings', 'ignore::DeprecationWarning:starlette.*')
    config.addinivalue_line('filterwarnings', 'ignore::DeprecationWarning:fastapi.*')
    config.addinivalue_line('filterwarnings', 'ignore:Using `httpx` with `starlette.testclient`')
