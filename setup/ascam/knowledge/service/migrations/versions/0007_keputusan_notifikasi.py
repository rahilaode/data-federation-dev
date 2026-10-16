"""keputusan notify untuk DROP/RENAME tak aman dan revisi rencana ADD oleh administrator (ADR-0024)

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-16 22:00:00
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = '0007'
down_revision: Union[str, None] = '0006'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

KEPUTUSAN_LAMA = ('auto', 'hitl')
KEPUTUSAN_BARU = ('auto', 'hitl', 'notify')
STATUS_LAMA = ('pending_approval', 'approved', 'rejected', 'superseded', 'executed', 'failed')
STATUS_BARU = STATUS_LAMA + ('notified', 'revised')


def _ganti(kolom: str, nilai: tuple[str, ...], not_valid: bool = False) -> None:
    nama = f'ck_adaptation_plan_{kolom}'
    op.drop_constraint(op.f(nama), 'adaptation_plan', schema='ops', type_='check')
    daftar = ', '.join(f"'{n}'" for n in nilai)
    op.execute(f'ALTER TABLE ops.adaptation_plan ADD CONSTRAINT {nama} '
               f'CHECK ({kolom} IN ({daftar})){" NOT VALID" if not_valid else ""}')


def upgrade() -> None:
    _ganti('decision', KEPUTUSAN_BARU)
    _ganti('status', STATUS_BARU)
    op.add_column('adaptation_plan',
                  sa.Column('revision_of', sa.BigInteger(), nullable=True,
                            comment='rencana ADD yang direvisi administrator saat persetujuan'),
                  schema='ops')
    op.create_foreign_key(op.f('fk_adaptation_plan_revision_of_adaptation_plan'),
                          'adaptation_plan', 'adaptation_plan', ['revision_of'], ['id'],
                          source_schema='ops', referent_schema='ops', ondelete='RESTRICT')


def downgrade() -> None:
    op.drop_constraint(op.f('fk_adaptation_plan_revision_of_adaptation_plan'), 'adaptation_plan',
                       schema='ops', type_='foreignkey')
    op.drop_column('adaptation_plan', 'revision_of', schema='ops')
    # baris ops append-only: nilai baru yang sudah tercatat dipertahankan (NOT VALID)
    _ganti('status', STATUS_LAMA, not_valid=True)
    _ganti('decision', KEPUTUSAN_LAMA, not_valid=True)
