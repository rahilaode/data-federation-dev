"""execution step start_ontop for the blue-green OBDA layer (ADR-0022)

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

OLD = ('deploy_vdb', 'validate', 'switch', 'reload_ontop', 'verify', 'rollback', 'sync')
NEW = ('deploy_vdb', 'validate', 'start_ontop', 'switch', 'reload_ontop', 'verify', 'rollback',
        'sync')


def _replace(names: tuple[str, ...], not_valid: bool = False) -> None:
    op.drop_constraint(op.f('ck_execution_step_name'), 'execution_step', schema='ops', type_='check')
    listed = ', '.join(f"'{n}'" for n in names)
    op.execute(f'ALTER TABLE ops.execution_step ADD CONSTRAINT ck_execution_step_name '
               f'CHECK (name IN ({listed})){" NOT VALID" if not_valid else ""}')


def upgrade() -> None:
    _replace(NEW)


def downgrade() -> None:
    # ops rows are append-only (a trigger rejects DELETE), so start_ontop steps already
    # recorded are kept: the old constraint applies only to new rows (NOT VALID).
    _replace(OLD, not_valid=True)
