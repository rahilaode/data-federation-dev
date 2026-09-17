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
