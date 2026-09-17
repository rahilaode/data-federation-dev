"""nama kredensial sebagai rujukan konfigurasi dan UI; hak baca versi migrasi untuk ascam_app

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-17 15:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0002'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ditambahkan nullable, diisi untuk baris lama, lalu dijadikan NOT NULL,
    # sehingga migrasi aman untuk basis data yang sudah berisi.
    op.add_column('credential', sa.Column('name', sa.Text(), nullable=True,
                                          comment='nama rujukan pada konfigurasi dan UI'),
                  schema='registry')
    op.execute("UPDATE registry.credential SET name = 'credential-' || id WHERE name IS NULL")
    op.alter_column('credential', 'name', nullable=False, schema='registry')
    op.create_unique_constraint(op.f('uq_credential_obdf_id_name'), 'credential', ['obdf_id', 'name'],
                                schema='registry')
    # /ready pada Knowledge Service (berjalan sebagai ascam_app) membaca revisi skema.
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ascam_app') THEN
            GRANT SELECT ON TABLE public.alembic_version TO ascam_app;
          END IF;
        END
        $$;
    """)


def downgrade() -> None:
    op.execute("""
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ascam_app') THEN
            REVOKE SELECT ON TABLE public.alembic_version FROM ascam_app;
          END IF;
        END
        $$;
    """)
    op.drop_constraint(op.f('uq_credential_obdf_id_name'), 'credential', schema='registry', type_='unique')
    op.drop_column('credential', 'name', schema='registry')
