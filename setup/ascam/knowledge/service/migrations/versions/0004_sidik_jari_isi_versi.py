"""sidik jari isi versi spesifikasi (Σ_S + artefak)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-18 06:00:00
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '0004'
down_revision: Union[str, None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# guard_version ditulis ulang agar content_digest ikut tidak dapat diubah setelah versi disegel.
GUARD = r"""
CREATE OR REPLACE FUNCTION spec.guard_version() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'candidate' THEN
      RAISE EXCEPTION 'versi baru harus berstatus candidate (diberikan %)', NEW.status USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
  END IF;
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'baris versi spesifikasi % tidak dihapus (tombstone)', OLD.id
      USING ERRCODE = '55000', HINT = 'gunakan spec.purge_version() untuk menghapus isi versi';
  END IF;
  IF OLD.status <> 'candidate' AND
     (NEW.id, NEW.obdf_id, NEW.version_no, NEW.parent_id, NEW.origin, NEW.teiid_vdb_name,
      NEW.teiid_vdb_version, NEW.plan_id, NEW.created_at, NEW.sealed_at, NEW.content_digest)
     IS DISTINCT FROM
     (OLD.id, OLD.obdf_id, OLD.version_no, OLD.parent_id, OLD.origin, OLD.teiid_vdb_name,
      OLD.teiid_vdb_version, OLD.plan_id, OLD.created_at, OLD.sealed_at, OLD.content_digest) THEN
    RAISE EXCEPTION 'atribut versi spesifikasi % tidak dapat diubah setelah disegel', OLD.id
      USING ERRCODE = '55000';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    IF (OLD.status, NEW.status) NOT IN (('candidate', 'active'), ('candidate', 'rejected'),
                                        ('active', 'superseded'), ('active', 'rolled_back'),
                                        ('superseded', 'active'),
                                        ('superseded', 'purged'), ('rejected', 'purged'),
                                        ('rolled_back', 'purged')) THEN
      RAISE EXCEPTION 'transisi status versi % -> % tidak diizinkan', OLD.status, NEW.status
        USING ERRCODE = '55000';
    END IF;
    IF NEW.status = 'purged' AND NOT ops.purge_allowed('spec') THEN
      RAISE EXCEPTION 'purge versi % memerlukan pemilik skema dan ascam.allow_purge = on', OLD.id
        USING ERRCODE = '55000';
    END IF;
    IF OLD.status = 'candidate' THEN
      NEW.sealed_at := now();
    END IF;
  END IF;
  RETURN NEW;
END
$$;
"""

GUARD_TANPA_DIGEST = GUARD.replace(", NEW.content_digest)", ")").replace(", OLD.content_digest)", ")")


def upgrade() -> None:
    op.add_column('spec_version', sa.Column('content_digest', sa.Text(), nullable=True,
                                            comment='sidik jari isi versi (Σ_S + artefak)'),
                  schema='spec')
    op.execute(GUARD)


def downgrade() -> None:
    op.execute(GUARD_TANPA_DIGEST)
    op.drop_column('spec_version', 'content_digest', schema='spec')
