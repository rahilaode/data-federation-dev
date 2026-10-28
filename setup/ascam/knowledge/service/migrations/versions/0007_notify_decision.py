"""decision notify for unsafe DROP/RENAME and revision of ADD plans by the administrator (ADR-0024)

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

OLD_DECISIONS = ('auto', 'hitl')
NEW_DECISIONS = ('auto', 'hitl', 'notify')
OLD_STATUSES = ('pending_approval', 'approved', 'rejected', 'superseded', 'executed', 'failed')
NEW_STATUSES = OLD_STATUSES + ('notified', 'revised')


def _replace(column: str, values: tuple[str, ...], not_valid: bool = False) -> None:
    name = f'ck_adaptation_plan_{column}'
    op.drop_constraint(op.f(name), 'adaptation_plan', schema='ops', type_='check')
    listed = ', '.join(f"'{n}'" for n in values)
    op.execute(f'ALTER TABLE ops.adaptation_plan ADD CONSTRAINT {name} '
               f'CHECK ({column} IN ({listed})){" NOT VALID" if not_valid else ""}')


def upgrade() -> None:
    _replace('decision', NEW_DECISIONS)
    _replace('status', NEW_STATUSES)
    op.add_column('adaptation_plan',
                  sa.Column('revision_of', sa.BigInteger(), nullable=True,
                            comment='ADD plan revised by the administrator on approval'),
                  schema='ops')
    op.create_foreign_key(op.f('fk_adaptation_plan_revision_of_adaptation_plan'),
                          'adaptation_plan', 'adaptation_plan', ['revision_of'], ['id'],
                          source_schema='ops', referent_schema='ops', ondelete='RESTRICT')


def downgrade() -> None:
    op.drop_constraint(op.f('fk_adaptation_plan_revision_of_adaptation_plan'), 'adaptation_plan',
                       schema='ops', type_='foreignkey')
    op.drop_column('adaptation_plan', 'revision_of', schema='ops')
    # ops rows are append-only: new values already recorded are kept (NOT VALID)
    _replace('status', OLD_STATUSES, not_valid=True)
    _replace('decision', OLD_DECISIONS, not_valid=True)
