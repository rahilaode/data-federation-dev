"""langkah eksekusi start_ontop untuk blue-green lapisan OBDA (ADR-0022)

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-08 23:40:00
"""
from typing import Sequence, Union

from alembic import op

revision: str = '0006'
down_revision: Union[str, None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LAMA = ('deploy_vdb', 'validate', 'switch', 'reload_ontop', 'verify', 'rollback', 'sync')
BARU = ('deploy_vdb', 'validate', 'start_ontop', 'switch', 'reload_ontop', 'verify', 'rollback',
        'sync')


def _ganti(nama: tuple[str, ...], not_valid: bool = False) -> None:
    op.drop_constraint(op.f('ck_execution_step_name'), 'execution_step', schema='ops', type_='check')
    daftar = ', '.join(f"'{n}'" for n in nama)
    op.execute(f'ALTER TABLE ops.execution_step ADD CONSTRAINT ck_execution_step_name '
               f'CHECK (name IN ({daftar})){" NOT VALID" if not_valid else ""}')


def upgrade() -> None:
    _ganti(BARU)


def downgrade() -> None:
    # Baris ops bersifat append-only (pemicu menolak DELETE), sehingga langkah start_ontop yang
    # sudah tercatat dipertahankan: batasan lama hanya berlaku untuk baris baru (NOT VALID).
    _ganti(LAMA, not_valid=True)
