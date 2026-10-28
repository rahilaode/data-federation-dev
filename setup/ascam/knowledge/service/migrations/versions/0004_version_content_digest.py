"""fingerprint of the specification version content (Σ_S + artifacts)

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

# guard_version is rewritten so that content_digest cannot be changed after the version is sealed either.
GUARD = r"""
CREATE OR REPLACE FUNCTION spec.guard_version() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'candidate' THEN
      RAISE EXCEPTION 'a new version must have status candidate (given %)', NEW.status USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
  END IF;
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'specification version row % is not deleted (tombstone)', OLD.id
      USING ERRCODE = '55000', HINT = 'use spec.purge_version() to delete the content of a version';
  END IF;
  IF OLD.status <> 'candidate' AND
     (NEW.id, NEW.obdf_id, NEW.version_no, NEW.parent_id, NEW.origin, NEW.teiid_vdb_name,
      NEW.teiid_vdb_version, NEW.plan_id, NEW.created_at, NEW.sealed_at, NEW.content_digest)
     IS DISTINCT FROM
     (OLD.id, OLD.obdf_id, OLD.version_no, OLD.parent_id, OLD.origin, OLD.teiid_vdb_name,
      OLD.teiid_vdb_version, OLD.plan_id, OLD.created_at, OLD.sealed_at, OLD.content_digest) THEN
    RAISE EXCEPTION 'attributes of specification version % cannot be changed after sealing', OLD.id
      USING ERRCODE = '55000';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    IF (OLD.status, NEW.status) NOT IN (('candidate', 'active'), ('candidate', 'rejected'),
                                        ('active', 'superseded'), ('active', 'rolled_back'),
                                        ('superseded', 'active'),
                                        ('superseded', 'purged'), ('rejected', 'purged'),
                                        ('rolled_back', 'purged')) THEN
      RAISE EXCEPTION 'status transition of version % -> % is not allowed', OLD.status, NEW.status
        USING ERRCODE = '55000';
    END IF;
    IF NEW.status = 'purged' AND NOT ops.purge_allowed('spec') THEN
      RAISE EXCEPTION 'purge of version % requires the schema owner and ascam.allow_purge = on', OLD.id
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

GUARD_WITHOUT_DIGEST = GUARD.replace(", NEW.content_digest)", ")").replace(", OLD.content_digest)", ")")


def upgrade() -> None:
    op.add_column('spec_version', sa.Column('content_digest', sa.Text(), nullable=True,
                                            comment='fingerprint of the version content (Σ_S + artifacts)'),
                  schema='spec')
    op.execute(GUARD)


def downgrade() -> None:
    op.execute(GUARD_WITHOUT_DIGEST)
    op.drop_column('spec_version', 'content_digest', schema='spec')
