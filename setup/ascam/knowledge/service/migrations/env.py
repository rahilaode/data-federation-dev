"""Lingkungan Alembic untuk Knowledge ASCAM."""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from ascam_knowledge.config import database_url
from ascam_knowledge.db import SCHEMAS, Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def db_url():
    return database_url()


def include_name(name, type_, parent_names):
    if type_ == 'schema':
        return name in SCHEMAS
    return True


def run_migrations_offline():
    context.configure(url=db_url(), target_metadata=target_metadata, literal_binds=True,
                      include_schemas=True, include_name=include_name, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    engine = create_engine(db_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata,
                          include_schemas=True, include_name=include_name, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
