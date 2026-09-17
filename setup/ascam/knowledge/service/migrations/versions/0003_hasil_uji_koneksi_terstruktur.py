"""hasil uji koneksi terstruktur (JSONB) dan aktor pemicu

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-17 16:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = '0003'
down_revision: Union[str, None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Teks lama (bila ada) dipertahankan sebagai {"summary": <teks>}.
    op.alter_column('connection_check', 'detail', schema='registry',
                    type_=postgresql.JSONB(astext_type=sa.Text()),
                    postgresql_using="CASE WHEN detail IS NULL THEN '{}'::jsonb "
                                     "ELSE jsonb_build_object('summary', detail) END",
                    existing_nullable=True, nullable=False, server_default=sa.text("'{}'::jsonb"),
                    comment='summary, facts, error (tanpa rahasia)')
    op.add_column('connection_check', sa.Column('actor', sa.Text(), nullable=True), schema='registry')
    op.create_index(op.f('ix_connection_check_target_id_checked_at'), 'connection_check',
                    ['target_id', 'checked_at'], schema='registry')


def downgrade() -> None:
    op.drop_index(op.f('ix_connection_check_target_id_checked_at'), table_name='connection_check',
                  schema='registry')
    op.drop_column('connection_check', 'actor', schema='registry')
    op.alter_column('connection_check', 'detail', schema='registry', type_=sa.Text(),
                    postgresql_using="detail->>'summary'", existing_nullable=False, nullable=True,
                    server_default=None, comment=None)
