"""Migrasi: dapat dinaikkan/diturunkan dan identik dengan model."""
from alembic import command
from sqlalchemy import create_engine, inspect, text

from ascam_knowledge.db import Base

EXPECTED = {'registry': 8, 'spec': 31, 'ops': 9}


def test_roundtrip_and_no_drift(alembic_cfg, db_url, migrated):
    command.downgrade(alembic_cfg, 'base')
    eng = create_engine(db_url)
    with eng.connect() as conn:
        left = conn.execute(text("SELECT count(*) FROM information_schema.schemata "
                                 "WHERE schema_name IN ('registry','spec','ops')")).scalar()
    assert left == 0
    command.upgrade(alembic_cfg, 'head')
    command.check(alembic_cfg)          # gagal bila model dan migrasi berbeda
    eng.dispose()


def test_expected_tables(engine):
    insp = inspect(engine)
    for schema, n in EXPECTED.items():
        assert len(insp.get_table_names(schema=schema)) == n, schema
    assert len(Base.metadata.tables) == sum(EXPECTED.values())


def test_every_spec_table_is_versioned(engine):
    insp = inspect(engine)
    for table in insp.get_table_names(schema='spec'):
        if table == 'spec_version':
            continue
        cols = {c['name'] for c in insp.get_columns(table, schema='spec')}
        assert 'spec_version_id' in cols, table


def test_integrity_triggers_installed(engine):
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT n.nspname, count(*) FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE NOT t.tgisinternal GROUP BY 1""")).all()
    counts = dict(rows)
    assert counts['spec'] == EXPECTED['spec']     # 30 tabel anak + spec_version
    assert counts['ops'] == EXPECTED['ops']
