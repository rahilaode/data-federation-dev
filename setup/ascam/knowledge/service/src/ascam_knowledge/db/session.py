from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..config import database_url


def make_session_factory(url=None):
    engine = create_engine(url or database_url(), pool_pre_ping=True, pool_size=5, max_overflow=5)
    return sessionmaker(bind=engine, expire_on_commit=False)
