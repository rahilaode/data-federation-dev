"""skema bawaan koneksi sumber (untuk tabel Teiid tanpa NAMEINSOURCE)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-18 07:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0005'
down_revision: Union[str, None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('source_system',
                  sa.Column('default_schema', sa.Text(), nullable=True,
                            comment='skema bawaan koneksi sumber; dipakai bila tabel Teiid tanpa NAMEINSOURCE'),
                  schema='registry')
    # Nilai untuk baris yang sudah ada: PostgreSQL memakai 'public', MySQL memakai nama basis data.
    op.execute("""
        UPDATE registry.source_system
        SET default_schema = CASE WHEN dbms = 'postgresql' THEN 'public' ELSE database_name END
        WHERE default_schema IS NULL
    """)


def downgrade() -> None:
    op.drop_column('source_system', 'default_schema', schema='registry')
