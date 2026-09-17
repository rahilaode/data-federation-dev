"""skema awal knowledge

Revision ID: 0001
Revises:
Create Date: 2026-09-17 07:44:51.383959
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

SCHEMAS = ('registry', 'spec', 'ops')

# ── Integritas di tingkat basis data ────────────────────────────────────────────
# P3 (docs/knowledge/README.md): isi versi spesifikasi hanya dapat diubah selama status
# `candidate`; transisi status dibatasi; baris ops tidak dihapus; audit_log tidak diubah.
# Penghapusan (retensi) hanya oleh pemilik skema dengan `SET ascam.allow_purge = 'on'`.
# Catatan: ini pengaman dari kesalahan aplikasi, bukan pengganti pengelolaan hak akses.
INTEGRITY_SQL = r"""
CREATE FUNCTION ops.purge_allowed(p_schema text) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT coalesce(current_setting('ascam.allow_purge', true), '') = 'on'
     AND pg_has_role(current_user,
                     (SELECT nspowner FROM pg_namespace WHERE nspname = p_schema), 'MEMBER')
$$;

CREATE FUNCTION spec.guard_sealed_rows() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v_old text;
  v_new text;
BEGIN
  IF TG_OP IN ('UPDATE', 'DELETE') THEN
    SELECT status INTO v_old FROM spec.spec_version WHERE id = OLD.spec_version_id;
    IF v_old IS NOT NULL AND v_old <> 'candidate'
       AND NOT (TG_OP = 'DELETE' AND ops.purge_allowed(TG_TABLE_SCHEMA)) THEN
      RAISE EXCEPTION 'versi spesifikasi % sudah disegel (status %): % pada %.% ditolak',
        OLD.spec_version_id, v_old, TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
        USING ERRCODE = '55000', HINT = 'buat versi kandidat baru';
    END IF;
  END IF;
  IF TG_OP IN ('INSERT', 'UPDATE') THEN
    SELECT status INTO v_new FROM spec.spec_version WHERE id = NEW.spec_version_id;
    IF v_new IS DISTINCT FROM 'candidate' THEN
      RAISE EXCEPTION 'versi spesifikasi % berstatus %: % pada %.% ditolak',
        NEW.spec_version_id, v_new, TG_OP, TG_TABLE_SCHEMA, TG_TABLE_NAME
        USING ERRCODE = '55000', HINT = 'isi versi hanya dapat diubah selama status candidate';
    END IF;
  END IF;
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  RETURN NEW;
END
$$;

DO $$
DECLARE t text;
BEGIN
  FOR t IN
    SELECT c.table_name FROM information_schema.columns c
    JOIN information_schema.tables tb
      ON tb.table_schema = c.table_schema AND tb.table_name = c.table_name AND tb.table_type = 'BASE TABLE'
    WHERE c.table_schema = 'spec' AND c.column_name = 'spec_version_id' AND c.table_name <> 'spec_version'
  LOOP
    EXECUTE format('CREATE TRIGGER trg_guard_sealed BEFORE INSERT OR UPDATE OR DELETE ON spec.%I '
                   'FOR EACH ROW EXECUTE FUNCTION spec.guard_sealed_rows()', t);
  END LOOP;
END
$$;

CREATE FUNCTION spec.guard_version() RETURNS trigger
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
      NEW.teiid_vdb_version, NEW.plan_id, NEW.created_at, NEW.sealed_at)
     IS DISTINCT FROM
     (OLD.id, OLD.obdf_id, OLD.version_no, OLD.parent_id, OLD.origin, OLD.teiid_vdb_name,
      OLD.teiid_vdb_version, OLD.plan_id, OLD.created_at, OLD.sealed_at) THEN
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

CREATE TRIGGER trg_guard_version BEFORE INSERT OR UPDATE OR DELETE ON spec.spec_version
  FOR EACH ROW EXECUTE FUNCTION spec.guard_version();

CREATE FUNCTION ops.guard_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF NOT ops.purge_allowed(TG_TABLE_SCHEMA) THEN
      RAISE EXCEPTION 'baris %.% tidak boleh dihapus (append-only)', TG_TABLE_SCHEMA, TG_TABLE_NAME
        USING ERRCODE = '55000';
    END IF;
    RETURN OLD;
  END IF;
  IF TG_TABLE_NAME = 'audit_log' THEN
    RAISE EXCEPTION 'ops.audit_log tidak boleh diubah' USING ERRCODE = '55000';
  END IF;
  RETURN NEW;
END
$$;

DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT table_name FROM information_schema.tables
           WHERE table_schema = 'ops' AND table_type = 'BASE TABLE'
  LOOP
    EXECUTE format('CREATE TRIGGER trg_append_only BEFORE UPDATE OR DELETE ON ops.%I '
                   'FOR EACH ROW EXECUTE FUNCTION ops.guard_append_only()', t);
  END LOOP;
END
$$;

-- Retensi: hapus ISI versi (bukan barisnya) lalu tandai `purged`.
CREATE FUNCTION spec.purge_version(p_id bigint) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
  v_status text;
  t text;
  n integer;
  total integer := 0;
BEGIN
  SELECT status INTO v_status FROM spec.spec_version WHERE id = p_id FOR UPDATE;
  IF v_status IS NULL THEN
    RAISE EXCEPTION 'versi spesifikasi % tidak ditemukan', p_id USING ERRCODE = 'P0002';
  END IF;
  IF v_status NOT IN ('superseded', 'rejected', 'rolled_back') THEN
    RAISE EXCEPTION 'versi % berstatus % tidak dapat di-purge', p_id, v_status USING ERRCODE = '55000';
  END IF;
  IF NOT ops.purge_allowed('spec') THEN
    RAISE EXCEPTION 'purge memerlukan pemilik skema dan ascam.allow_purge = on' USING ERRCODE = '55000';
  END IF;
  -- Hitung isi versi lebih dahulu: penghapusan memicu CASCADE sehingga ROW_COUNT
  -- per pernyataan tidak mencerminkan jumlah baris yang benar-benar terhapus.
  FOR t IN
    SELECT c.table_name FROM information_schema.columns c
    JOIN information_schema.tables tb
      ON tb.table_schema = c.table_schema AND tb.table_name = c.table_name AND tb.table_type = 'BASE TABLE'
    WHERE c.table_schema = 'spec' AND c.column_name = 'spec_version_id' AND c.table_name <> 'spec_version'
  LOOP
    EXECUTE format('SELECT count(*) FROM spec.%I WHERE spec_version_id = $1', t) INTO n USING p_id;
    total := total + n;
  END LOOP;
  FOR t IN
    SELECT c.table_name FROM information_schema.columns c
    JOIN information_schema.tables tb
      ON tb.table_schema = c.table_schema AND tb.table_name = c.table_name AND tb.table_type = 'BASE TABLE'
    WHERE c.table_schema = 'spec' AND c.column_name = 'spec_version_id' AND c.table_name <> 'spec_version'
  LOOP
    EXECUTE format('DELETE FROM spec.%I WHERE spec_version_id = $1', t) USING p_id;
  END LOOP;
  UPDATE spec.spec_version SET status = 'purged' WHERE id = p_id;
  RETURN total;
END
$$;

CREATE VIEW spec.active_version AS
SELECT sv.obdf_id, o.name AS obdf_name, sv.id AS spec_version_id, sv.version_no, sv.origin,
       sv.teiid_vdb_name, sv.teiid_vdb_version, sv.teiid_connection_type, sv.created_at, sv.sealed_at
FROM spec.spec_version sv
JOIN registry.obdf_instance o ON o.id = sv.obdf_id
WHERE sv.status = 'active';

-- Hak akses role aplikasi (dibuat oleh skrip inisialisasi basis data, bila ada)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'ascam_app') THEN
    GRANT USAGE ON SCHEMA registry, spec, ops TO ascam_app;
    GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA registry, spec, ops TO ascam_app;
    GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA registry, spec, ops TO ascam_app;
    GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA spec, ops TO ascam_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA registry, spec, ops
      GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO ascam_app;
    ALTER DEFAULT PRIVILEGES IN SCHEMA registry, spec, ops
      GRANT USAGE, SELECT ON SEQUENCES TO ascam_app;
  END IF;
END
$$;
"""

DROP_INTEGRITY_SQL = r"""
DROP VIEW IF EXISTS spec.active_version;
DROP FUNCTION IF EXISTS ops.guard_append_only() CASCADE;
DROP FUNCTION IF EXISTS spec.purge_version(bigint);
DROP FUNCTION IF EXISTS spec.guard_version() CASCADE;
DROP FUNCTION IF EXISTS spec.guard_sealed_rows() CASCADE;
DROP FUNCTION IF EXISTS ops.purge_allowed(text);
"""

revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for schema in SCHEMAS:
        op.execute(sa.schema.CreateSchema(schema, if_not_exists=True))
    op.create_table('obdf_instance',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_obdf_instance')),
    sa.UniqueConstraint('name', name=op.f('uq_obdf_instance_name')),
    schema='registry'
    )
    op.create_table('audit_log',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('obdf_id', sa.BigInteger(), nullable=True),
    sa.Column('actor', sa.Text(), nullable=False),
    sa.Column('action', sa.Text(), nullable=False),
    sa.Column('object_kind', sa.Text(), nullable=True),
    sa.Column('object_ref', sa.Text(), nullable=True),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.ForeignKeyConstraint(['obdf_id'], ['registry.obdf_instance.id'], name=op.f('fk_audit_log_obdf_id_obdf_instance'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_log')),
    schema='ops'
    )
    op.create_index(op.f('ix_audit_log_obdf_id'), 'audit_log', ['obdf_id'], unique=False, schema='ops')
    op.create_table('credential',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('obdf_id', sa.BigInteger(), nullable=False),
    sa.Column('username', sa.Text(), nullable=False),
    sa.Column('secret_ciphertext', sa.LargeBinary(), nullable=False),
    sa.Column('key_version', sa.Integer(), server_default='1', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('rotated_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['obdf_id'], ['registry.obdf_instance.id'], name=op.f('fk_credential_obdf_id_obdf_instance'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_credential')),
    schema='registry'
    )
    op.create_index(op.f('ix_credential_obdf_id'), 'credential', ['obdf_id'], unique=False, schema='registry')
    op.create_table('naming_policy',
    sa.Column('obdf_id', sa.BigInteger(), nullable=False),
    sa.Column('property_iri_template', sa.Text(), nullable=False),
    sa.Column('on_collision', sa.Text(), server_default='hitl', nullable=False),
    sa.Column('label_language', sa.Text(), server_default='id', nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("on_collision IN ('qualify_with_class', 'hitl')", name=op.f('ck_naming_policy_on_collision')),
    sa.ForeignKeyConstraint(['obdf_id'], ['registry.obdf_instance.id'], name=op.f('fk_naming_policy_obdf_id_obdf_instance'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('obdf_id', name=op.f('pk_naming_policy')),
    schema='registry'
    )
    op.create_table('setting',
    sa.Column('obdf_id', sa.BigInteger(), nullable=False),
    sa.Column('key', sa.Text(), nullable=False),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['obdf_id'], ['registry.obdf_instance.id'], name=op.f('fk_setting_obdf_id_obdf_instance'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('obdf_id', 'key', name=op.f('pk_setting')),
    schema='registry'
    )
    op.create_table('source_system',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('obdf_id', sa.BigInteger(), nullable=False),
    sa.Column('logical_name', sa.Text(), nullable=False, comment='nilai field source pada event'),
    sa.Column('dbms', sa.Text(), nullable=False),
    sa.Column('database_name', sa.Text(), nullable=False),
    sa.Column('kafka_topic', sa.Text(), nullable=True),
    sa.Column('identifier_case', sa.Text(), nullable=False, comment='aturan normalisasi nama identifier'),
    sa.CheckConstraint("dbms IN ('postgresql', 'mysql')", name=op.f('ck_source_system_dbms')),
    sa.CheckConstraint("identifier_case IN ('lower', 'preserve', 'insensitive')", name=op.f('ck_source_system_identifier_case')),
    sa.ForeignKeyConstraint(['obdf_id'], ['registry.obdf_instance.id'], name=op.f('fk_source_system_obdf_id_obdf_instance'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_source_system')),
    sa.UniqueConstraint('obdf_id', 'logical_name', name=op.f('uq_source_system_obdf_id_logical_name')),
    schema='registry'
    )
    op.create_index(op.f('ix_source_system_obdf_id'), 'source_system', ['obdf_id'], unique=False, schema='registry')
    op.create_table('type_mapping',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('obdf_id', sa.BigInteger(), nullable=False),
    sa.Column('dbms', sa.Text(), nullable=False),
    sa.Column('native_type', sa.Text(), nullable=False),
    sa.Column('teiid_type', sa.Text(), nullable=False),
    sa.Column('xsd_datatype', sa.Text(), nullable=False),
    sa.Column('owl2ql_compatible', sa.Boolean(), nullable=False),
    sa.CheckConstraint("dbms IN ('postgresql', 'mysql')", name=op.f('ck_type_mapping_dbms')),
    sa.ForeignKeyConstraint(['obdf_id'], ['registry.obdf_instance.id'], name=op.f('fk_type_mapping_obdf_id_obdf_instance'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_type_mapping')),
    sa.UniqueConstraint('obdf_id', 'dbms', 'native_type', name=op.f('uq_type_mapping_obdf_id_dbms_native_type')),
    schema='registry'
    )
    op.create_index(op.f('ix_type_mapping_obdf_id'), 'type_mapping', ['obdf_id'], unique=False, schema='registry')
    op.create_table('spec_version',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('obdf_id', sa.BigInteger(), nullable=False),
    sa.Column('version_no', sa.Integer(), nullable=False),
    sa.Column('parent_id', sa.BigInteger(), nullable=True),
    sa.Column('origin', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default='candidate', nullable=False),
    sa.Column('teiid_vdb_name', sa.Text(), nullable=True),
    sa.Column('teiid_vdb_version', sa.Text(), nullable=True),
    sa.Column('teiid_connection_type', sa.Text(), nullable=True),
    sa.Column('plan_id', sa.BigInteger(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('sealed_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("origin IN ('sync', 'adaptation', 'rollback', 'manual')", name=op.f('ck_spec_version_origin')),
    sa.CheckConstraint("status IN ('candidate', 'active', 'superseded', 'rejected', 'rolled_back', 'purged')", name=op.f('ck_spec_version_status')),
    sa.CheckConstraint("teiid_connection_type IN ('BY_VERSION', 'ANY', 'NONE')", name=op.f('ck_spec_version_teiid_connection_type')),
    sa.ForeignKeyConstraint(['obdf_id'], ['registry.obdf_instance.id'], name=op.f('fk_spec_version_obdf_id_obdf_instance'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['parent_id'], ['spec.spec_version.id'], name=op.f('fk_spec_version_parent_id_spec_version'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_spec_version')),
    sa.UniqueConstraint('obdf_id', 'version_no', name=op.f('uq_spec_version_obdf_id_version_no')),
    schema='spec'
    )
    op.create_index(op.f('ix_spec_version_obdf_id'), 'spec_version', ['obdf_id'], unique=False, schema='spec')
    op.create_index('uq_spec_version_one_active', 'spec_version', ['obdf_id'], unique=True, schema='spec', postgresql_where=sa.text("status = 'active'"))
    op.create_table('schema_event',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('event_uid', sa.UUID(), nullable=False),
    sa.Column('obdf_id', sa.BigInteger(), nullable=False),
    sa.Column('source_system_id', sa.BigInteger(), nullable=True),
    sa.Column('raw_message', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('structured', postgresql.JSONB(astext_type=sa.Text()), nullable=True, comment='event terformalisasi'),
    sa.Column('captured_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('received_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('status', sa.Text(), server_default='received', nullable=False),
    sa.Column('ignore_reason', sa.Text(), nullable=True),
    sa.CheckConstraint("status IN ('received', 'planned', 'ignored', 'duplicate', 'failed')", name=op.f('ck_schema_event_status')),
    sa.ForeignKeyConstraint(['obdf_id'], ['registry.obdf_instance.id'], name=op.f('fk_schema_event_obdf_id_obdf_instance'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['source_system_id'], ['registry.source_system.id'], name=op.f('fk_schema_event_source_system_id_source_system'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_schema_event')),
    sa.UniqueConstraint('event_uid', name=op.f('uq_schema_event_event_uid')),
    schema='ops'
    )
    op.create_index(op.f('ix_schema_event_obdf_id'), 'schema_event', ['obdf_id'], unique=False, schema='ops')
    op.create_index(op.f('ix_schema_event_source_system_id'), 'schema_event', ['source_system_id'], unique=False, schema='ops')
    op.create_table('sync_run',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('obdf_id', sa.BigInteger(), nullable=False),
    sa.Column('trigger', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default='running', nullable=False),
    sa.Column('produced_spec_version_id', sa.BigInteger(), nullable=True),
    sa.Column('drift_detected', sa.Boolean(), nullable=True),
    sa.Column('drift_summary', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('running', 'succeeded', 'failed')", name=op.f('ck_sync_run_status')),
    sa.CheckConstraint("trigger IN ('startup', 'manual', 'scheduled', 'post_adaptation')", name=op.f('ck_sync_run_trigger')),
    sa.ForeignKeyConstraint(['obdf_id'], ['registry.obdf_instance.id'], name=op.f('fk_sync_run_obdf_id_obdf_instance'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['produced_spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_sync_run_produced_spec_version_id_spec_version'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sync_run')),
    schema='ops'
    )
    op.create_index(op.f('ix_sync_run_obdf_id'), 'sync_run', ['obdf_id'], unique=False, schema='ops')
    op.create_table('target',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('obdf_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('endpoint', postgresql.JSONB(astext_type=sa.Text()), nullable=False, comment='host, port, path, tls'),
    sa.Column('credential_id', sa.BigInteger(), nullable=True),
    sa.Column('enabled', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('teiid_mgmt', 'teiid_odbc', 'ontop_sparql', 'ontop_agent', 'kafka')", name=op.f('ck_target_kind')),
    sa.ForeignKeyConstraint(['credential_id'], ['registry.credential.id'], name=op.f('fk_target_credential_id_credential'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['obdf_id'], ['registry.obdf_instance.id'], name=op.f('fk_target_obdf_id_obdf_instance'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_target')),
    sa.UniqueConstraint('obdf_id', 'name', name=op.f('uq_target_obdf_id_name')),
    schema='registry'
    )
    op.create_index(op.f('ix_target_credential_id'), 'target', ['credential_id'], unique=False, schema='registry')
    op.create_index(op.f('ix_target_obdf_id'), 'target', ['obdf_id'], unique=False, schema='registry')
    op.create_table('artifact',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False, comment='nama berkas'),
    sa.Column('media_type', sa.Text(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('sha256', sa.CHAR(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("kind IN ('vdb_xml', 'r2rml', 'ontology')", name=op.f('ck_artifact_kind')),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_artifact_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_artifact')),
    sa.UniqueConstraint('spec_version_id', 'id', name=op.f('uq_artifact_spec_version_id_id')),
    sa.UniqueConstraint('spec_version_id', 'kind', 'name', name=op.f('uq_artifact_spec_version_id_kind_name')),
    schema='spec'
    )
    op.create_index(op.f('ix_artifact_spec_version_id'), 'artifact', ['spec_version_id'], unique=False, schema='spec')
    op.create_table('consistency_issue',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('code', sa.Text(), nullable=False, comment='kode masalah (docs/knowledge/README.md)'),
    sa.Column('severity', sa.Text(), nullable=False),
    sa.Column('subject_kind', sa.Text(), nullable=True),
    sa.Column('subject_ref', sa.Text(), nullable=True),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('detected_by', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("detected_by IN ('sync', 'ascam_vocabulary', 'ontop_validate', 'teiid_status')", name=op.f('ck_consistency_issue_detected_by')),
    sa.CheckConstraint("severity IN ('error', 'warning', 'info')", name=op.f('ck_consistency_issue_severity')),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_consistency_issue_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_consistency_issue')),
    schema='spec'
    )
    op.create_index(op.f('ix_consistency_issue_spec_version_id'), 'consistency_issue', ['spec_version_id'], unique=False, schema='spec')
    op.create_table('teiid_model',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('model_type', sa.Text(), nullable=False),
    sa.Column('visible', sa.Boolean(), server_default='true', nullable=False),
    sa.CheckConstraint("model_type IN ('physical', 'virtual')", name=op.f('ck_teiid_model_model_type')),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_teiid_model_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_teiid_model')),
    sa.UniqueConstraint('spec_version_id', 'id', name=op.f('uq_teiid_model_spec_version_id_id')),
    sa.UniqueConstraint('spec_version_id', 'name', name=op.f('uq_teiid_model_spec_version_id_name')),
    schema='spec'
    )
    op.create_index(op.f('ix_teiid_model_spec_version_id'), 'teiid_model', ['spec_version_id'], unique=False, schema='spec')
    op.create_table('adaptation_plan',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('event_id', sa.BigInteger(), nullable=False),
    sa.Column('base_spec_version_id', sa.BigInteger(), nullable=False, comment='optimistic concurrency'),
    sa.Column('pattern', sa.Text(), nullable=True),
    sa.Column('decision', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), nullable=False),
    sa.Column('impact', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('reasons', postgresql.JSONB(astext_type=sa.Text()), server_default='[]', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('decided_by', sa.Text(), nullable=True),
    sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("decision IN ('auto', 'hitl')", name=op.f('ck_adaptation_plan_decision')),
    sa.CheckConstraint("status IN ('pending_approval', 'approved', 'rejected', 'superseded', 'executed', 'failed')", name=op.f('ck_adaptation_plan_status')),
    sa.ForeignKeyConstraint(['base_spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_adaptation_plan_base_spec_version_id_spec_version'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['event_id'], ['ops.schema_event.id'], name=op.f('fk_adaptation_plan_event_id_schema_event'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_adaptation_plan')),
    schema='ops'
    )
    op.create_index(op.f('ix_adaptation_plan_event_id'), 'adaptation_plan', ['event_id'], unique=False, schema='ops')
    op.create_table('connection_check',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('target_id', sa.BigInteger(), nullable=False),
    sa.Column('checked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('ok', sa.Boolean(), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['target_id'], ['registry.target.id'], name=op.f('fk_connection_check_target_id_target'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_connection_check')),
    schema='registry'
    )
    op.create_index(op.f('ix_connection_check_target_id'), 'connection_check', ['target_id'], unique=False, schema='registry')
    op.create_table('ontology',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('artifact_id', sa.BigInteger(), nullable=True),
    sa.Column('iri', sa.Text(), nullable=False),
    sa.Column('version_iri', sa.Text(), nullable=True),
    sa.Column('version_info', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['spec_version_id', 'artifact_id'], ['spec.artifact.spec_version_id', 'spec.artifact.id'], name=op.f('fk_ontology_spec_version_id_artifact_id_artifact'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_ontology_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ontology')),
    sa.UniqueConstraint('spec_version_id', 'id', name=op.f('uq_ontology_spec_version_id_id')),
    sa.UniqueConstraint('spec_version_id', 'iri', name=op.f('uq_ontology_spec_version_id_iri')),
    schema='spec'
    )
    op.create_index(op.f('ix_ontology_spec_version_id'), 'ontology', ['spec_version_id'], unique=False, schema='spec')
    op.create_table('rdf_triple',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('artifact_id', sa.BigInteger(), nullable=False),
    sa.Column('subject', sa.Text(), nullable=False),
    sa.Column('predicate', sa.Text(), nullable=False),
    sa.Column('object', sa.Text(), nullable=False),
    sa.Column('object_kind', sa.Text(), nullable=False),
    sa.Column('datatype', sa.Text(), nullable=True),
    sa.Column('lang', sa.Text(), nullable=True),
    sa.CheckConstraint("object_kind IN ('iri', 'literal', 'bnode')", name=op.f('ck_rdf_triple_object_kind')),
    sa.ForeignKeyConstraint(['spec_version_id', 'artifact_id'], ['spec.artifact.spec_version_id', 'spec.artifact.id'], name=op.f('fk_rdf_triple_spec_version_id_artifact_id_artifact'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_rdf_triple_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_rdf_triple')),
    schema='spec'
    )
    op.create_index(op.f('ix_rdf_triple_predicate'), 'rdf_triple', ['predicate'], unique=False, schema='spec')
    op.create_index(op.f('ix_rdf_triple_spec_version_id'), 'rdf_triple', ['spec_version_id'], unique=False, schema='spec')
    op.create_index(op.f('ix_rdf_triple_spec_version_id_artifact_id'), 'rdf_triple', ['spec_version_id', 'artifact_id'], unique=False, schema='spec')
    op.create_table('teiid_model_source',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('model_id', sa.BigInteger(), nullable=False),
    sa.Column('source_name', sa.Text(), nullable=False),
    sa.Column('translator', sa.Text(), nullable=False),
    sa.Column('jndi_name', sa.Text(), nullable=True),
    sa.Column('source_system_id', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['source_system_id'], ['registry.source_system.id'], name=op.f('fk_teiid_model_source_source_system_id_source_system'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['spec_version_id', 'model_id'], ['spec.teiid_model.spec_version_id', 'spec.teiid_model.id'], name=op.f('fk_teiid_model_source_spec_version_id_model_id_teiid_model'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_teiid_model_source_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_teiid_model_source')),
    sa.UniqueConstraint('spec_version_id', 'model_id', 'source_name', name=op.f('uq_teiid_model_source_spec_version_id_model_id_source_name')),
    schema='spec'
    )
    op.create_index(op.f('ix_teiid_model_source_source_system_id'), 'teiid_model_source', ['source_system_id'], unique=False, schema='spec')
    op.create_index(op.f('ix_teiid_model_source_spec_version_id'), 'teiid_model_source', ['spec_version_id'], unique=False, schema='spec')
    op.create_table('teiid_routine',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('model_id', sa.BigInteger(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('table_name', sa.Text(), nullable=True, comment='tabel pemicu (trigger)'),
    sa.Column('body', sa.Text(), nullable=True),
    sa.CheckConstraint("kind IN ('stored_procedure', 'trigger')", name=op.f('ck_teiid_routine_kind')),
    sa.ForeignKeyConstraint(['spec_version_id', 'model_id'], ['spec.teiid_model.spec_version_id', 'spec.teiid_model.id'], name=op.f('fk_teiid_routine_spec_version_id_model_id_teiid_model'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_teiid_routine_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_teiid_routine')),
    schema='spec'
    )
    op.create_index(op.f('ix_teiid_routine_spec_version_id'), 'teiid_routine', ['spec_version_id'], unique=False, schema='spec')
    op.create_table('teiid_table',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('model_id', sa.BigInteger(), nullable=False),
    sa.Column('uid', sa.Text(), nullable=True, comment='UID Teiid'),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('name_in_source', sa.Text(), nullable=True),
    sa.Column('source_schema', sa.Text(), nullable=True, comment='hasil normalisasi'),
    sa.Column('source_table', sa.Text(), nullable=True, comment='hasil normalisasi'),
    sa.Column('managed_by', sa.Text(), server_default='human', nullable=False),
    sa.Column('introduced_in_version', sa.BigInteger(), nullable=True),
    sa.CheckConstraint("kind IN ('foreign', 'view', 'materialized_view')", name=op.f('ck_teiid_table_kind')),
    sa.CheckConstraint("managed_by IN ('human', 'ascam')", name=op.f('ck_teiid_table_managed_by')),
    sa.ForeignKeyConstraint(['introduced_in_version'], ['spec.spec_version.id'], name=op.f('fk_teiid_table_introduced_in_version_spec_version'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['spec_version_id', 'model_id'], ['spec.teiid_model.spec_version_id', 'spec.teiid_model.id'], name=op.f('fk_teiid_table_spec_version_id_model_id_teiid_model'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_teiid_table_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_teiid_table')),
    sa.UniqueConstraint('spec_version_id', 'id', name=op.f('uq_teiid_table_spec_version_id_id')),
    sa.UniqueConstraint('spec_version_id', 'model_id', 'name', name=op.f('uq_teiid_table_spec_version_id_model_id_name')),
    schema='spec'
    )
    op.create_index(op.f('ix_teiid_table_spec_version_id'), 'teiid_table', ['spec_version_id'], unique=False, schema='spec')
    op.create_index(op.f('ix_teiid_table_spec_version_id_source_schema_source_table'), 'teiid_table', ['spec_version_id', 'source_schema', 'source_table'], unique=False, schema='spec')
    op.create_table('triples_map',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('artifact_id', sa.BigInteger(), nullable=True),
    sa.Column('iri', sa.Text(), nullable=False),
    sa.Column('logical_table_kind', sa.Text(), nullable=False),
    sa.Column('table_name', sa.Text(), nullable=True),
    sa.Column('sql_query', sa.Text(), nullable=True),
    sa.Column('sql_parse_status', sa.Text(), nullable=False),
    sa.Column('managed_by', sa.Text(), server_default='human', nullable=False),
    sa.Column('introduced_in_version', sa.BigInteger(), nullable=True),
    sa.CheckConstraint("logical_table_kind IN ('table', 'sql_query')", name=op.f('ck_triples_map_logical_table_kind')),
    sa.CheckConstraint("managed_by IN ('human', 'ascam')", name=op.f('ck_triples_map_managed_by')),
    sa.CheckConstraint("sql_parse_status IN ('ok', 'failed', 'not_applicable')", name=op.f('ck_triples_map_sql_parse_status')),
    sa.ForeignKeyConstraint(['introduced_in_version'], ['spec.spec_version.id'], name=op.f('fk_triples_map_introduced_in_version_spec_version'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['spec_version_id', 'artifact_id'], ['spec.artifact.spec_version_id', 'spec.artifact.id'], name=op.f('fk_triples_map_spec_version_id_artifact_id_artifact'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_triples_map_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_triples_map')),
    sa.UniqueConstraint('spec_version_id', 'id', name=op.f('uq_triples_map_spec_version_id_id')),
    sa.UniqueConstraint('spec_version_id', 'iri', name=op.f('uq_triples_map_spec_version_id_iri')),
    schema='spec'
    )
    op.create_index(op.f('ix_triples_map_spec_version_id'), 'triples_map', ['spec_version_id'], unique=False, schema='spec')
    op.create_table('execution',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('plan_id', sa.BigInteger(), nullable=False),
    sa.Column('candidate_spec_version_id', sa.BigInteger(), nullable=True),
    sa.Column('status', sa.Text(), server_default='running', nullable=False),
    sa.Column('timings', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False, comment='dekomposisi Δt_adapt'),
    sa.Column('failure', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('running', 'succeeded', 'failed', 'rolled_back')", name=op.f('ck_execution_status')),
    sa.ForeignKeyConstraint(['candidate_spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_execution_candidate_spec_version_id_spec_version'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['plan_id'], ['ops.adaptation_plan.id'], name=op.f('fk_execution_plan_id_adaptation_plan'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_execution')),
    schema='ops'
    )
    op.create_index(op.f('ix_execution_plan_id'), 'execution', ['plan_id'], unique=False, schema='ops')
    op.create_table('notification',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('obdf_id', sa.BigInteger(), nullable=False),
    sa.Column('plan_id', sa.BigInteger(), nullable=True),
    sa.Column('severity', sa.Text(), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('acknowledged', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('acknowledged_by', sa.Text(), nullable=True),
    sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("severity IN ('info', 'warning', 'error')", name=op.f('ck_notification_severity')),
    sa.ForeignKeyConstraint(['obdf_id'], ['registry.obdf_instance.id'], name=op.f('fk_notification_obdf_id_obdf_instance'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['plan_id'], ['ops.adaptation_plan.id'], name=op.f('fk_notification_plan_id_adaptation_plan'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notification')),
    schema='ops'
    )
    op.create_index(op.f('ix_notification_obdf_id'), 'notification', ['obdf_id'], unique=False, schema='ops')
    op.create_table('plan_action',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('plan_id', sa.BigInteger(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('artifact', sa.Text(), nullable=False),
    sa.Column('operation', sa.Text(), nullable=False),
    sa.Column('params', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.CheckConstraint("artifact IN ('vdb', 'r2rml', 'ontology')", name=op.f('ck_plan_action_artifact')),
    sa.ForeignKeyConstraint(['plan_id'], ['ops.adaptation_plan.id'], name=op.f('fk_plan_action_plan_id_adaptation_plan'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_plan_action')),
    sa.UniqueConstraint('plan_id', 'seq', name=op.f('uq_plan_action_plan_id_seq')),
    schema='ops'
    )
    op.create_index(op.f('ix_plan_action_plan_id'), 'plan_action', ['plan_id'], unique=False, schema='ops')
    op.create_table('logical_column',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('triples_map_id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False, comment='nama yang dilihat term map'),
    sa.Column('expression_kind', sa.Text(), nullable=False),
    sa.Column('expression_sql', sa.Text(), nullable=True),
    sa.CheckConstraint("expression_kind IN ('passthrough', 'expression', 'star', 'unknown')", name=op.f('ck_logical_column_expression_kind')),
    sa.ForeignKeyConstraint(['spec_version_id', 'triples_map_id'], ['spec.triples_map.spec_version_id', 'spec.triples_map.id'], name=op.f('fk_logical_column_spec_version_id_triples_map_id_triples_map'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_logical_column_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_logical_column')),
    sa.UniqueConstraint('spec_version_id', 'id', name=op.f('uq_logical_column_spec_version_id_id')),
    sa.UniqueConstraint('spec_version_id', 'triples_map_id', 'name', name=op.f('uq_logical_column_spec_version_id_triples_map_id_name')),
    schema='spec'
    )
    op.create_index(op.f('ix_logical_column_spec_version_id'), 'logical_column', ['spec_version_id'], unique=False, schema='spec')
    op.create_table('logical_source',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('triples_map_id', sa.BigInteger(), nullable=False),
    sa.Column('teiid_table_id', sa.BigInteger(), nullable=True, comment='kosong bila tidak terselesaikan'),
    sa.Column('reference_name', sa.Text(), nullable=False, comment='nama tabel/view sebagaimana tertulis'),
    sa.Column('alias', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['spec_version_id', 'teiid_table_id'], ['spec.teiid_table.spec_version_id', 'spec.teiid_table.id'], name=op.f('fk_logical_source_spec_version_id_teiid_table_id_teiid_table'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id', 'triples_map_id'], ['spec.triples_map.spec_version_id', 'spec.triples_map.id'], name=op.f('fk_logical_source_spec_version_id_triples_map_id_triples_map'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_logical_source_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_logical_source')),
    schema='spec'
    )
    op.create_index(op.f('ix_logical_source_spec_version_id'), 'logical_source', ['spec_version_id'], unique=False, schema='spec')
    op.create_table('ont_entity',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('ontology_id', sa.BigInteger(), nullable=False),
    sa.Column('iri', sa.Text(), nullable=False),
    sa.Column('kind', sa.Text(), nullable=False),
    sa.Column('declared_in', sa.Text(), server_default='local', nullable=False),
    sa.Column('deprecated', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('functional', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('managed_by', sa.Text(), server_default='human', nullable=False),
    sa.Column('introduced_in_version', sa.BigInteger(), nullable=True),
    sa.CheckConstraint("declared_in IN ('local', 'imported')", name=op.f('ck_ont_entity_declared_in')),
    sa.CheckConstraint("kind IN ('class', 'datatype_property', 'object_property', 'annotation_property')", name=op.f('ck_ont_entity_kind')),
    sa.CheckConstraint("managed_by IN ('human', 'ascam')", name=op.f('ck_ont_entity_managed_by')),
    sa.ForeignKeyConstraint(['introduced_in_version'], ['spec.spec_version.id'], name=op.f('fk_ont_entity_introduced_in_version_spec_version'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['spec_version_id', 'ontology_id'], ['spec.ontology.spec_version_id', 'spec.ontology.id'], name=op.f('fk_ont_entity_spec_version_id_ontology_id_ontology'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_ont_entity_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ont_entity')),
    sa.UniqueConstraint('spec_version_id', 'id', name=op.f('uq_ont_entity_spec_version_id_id')),
    sa.UniqueConstraint('spec_version_id', 'ontology_id', 'iri', 'kind', name=op.f('uq_ont_entity_spec_version_id_ontology_id_iri_kind')),
    schema='spec'
    )
    op.create_index(op.f('ix_ont_entity_spec_version_id'), 'ont_entity', ['spec_version_id'], unique=False, schema='spec')
    op.create_index(op.f('ix_ont_entity_spec_version_id_iri'), 'ont_entity', ['spec_version_id', 'iri'], unique=False, schema='spec')
    op.create_table('ont_import',
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('ontology_id', sa.BigInteger(), nullable=False),
    sa.Column('imported_iri', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['spec_version_id', 'ontology_id'], ['spec.ontology.spec_version_id', 'spec.ontology.id'], name=op.f('fk_ont_import_spec_version_id_ontology_id_ontology'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_ont_import_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('spec_version_id', 'ontology_id', 'imported_iri', name=op.f('pk_ont_import')),
    schema='spec'
    )
    op.create_table('predicate_object_map',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('triples_map_id', sa.BigInteger(), nullable=False),
    sa.Column('signature', sa.Text(), nullable=False, comment='tanda tangan struktural, pengganti label blank node'),
    sa.Column('managed_by', sa.Text(), server_default='human', nullable=False),
    sa.Column('introduced_in_version', sa.BigInteger(), nullable=True),
    sa.CheckConstraint("managed_by IN ('human', 'ascam')", name=op.f('ck_predicate_object_map_managed_by')),
    sa.ForeignKeyConstraint(['introduced_in_version'], ['spec.spec_version.id'], name=op.f('fk_predicate_object_map_introduced_in_version_spec_version'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['spec_version_id', 'triples_map_id'], ['spec.triples_map.spec_version_id', 'spec.triples_map.id'], name=op.f('fk_predicate_object_map_spec_version_id_triples_map_id_triples_map'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_predicate_object_map_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_predicate_object_map')),
    sa.UniqueConstraint('spec_version_id', 'id', name=op.f('uq_predicate_object_map_spec_version_id_id')),
    sa.UniqueConstraint('spec_version_id', 'triples_map_id', 'signature', name=op.f('uq_predicate_object_map_spec_version_id_triples_map_id_signature')),
    schema='spec'
    )
    op.create_index(op.f('ix_predicate_object_map_spec_version_id'), 'predicate_object_map', ['spec_version_id'], unique=False, schema='spec')
    op.create_table('subject_class',
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('triples_map_id', sa.BigInteger(), nullable=False),
    sa.Column('class_iri', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['spec_version_id', 'triples_map_id'], ['spec.triples_map.spec_version_id', 'spec.triples_map.id'], name=op.f('fk_subject_class_spec_version_id_triples_map_id_triples_map'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_subject_class_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('spec_version_id', 'triples_map_id', 'class_iri', name=op.f('pk_subject_class')),
    schema='spec'
    )
    op.create_table('teiid_alter',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('model_id', sa.BigInteger(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('statement', sa.Text(), nullable=False, comment='ALTER yang ditambahkan ASCAM (ADR-0002)'),
    sa.Column('plan_id', sa.BigInteger(), nullable=True),
    sa.ForeignKeyConstraint(['plan_id'], ['ops.adaptation_plan.id'], name=op.f('fk_teiid_alter_plan_id_adaptation_plan'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['spec_version_id', 'model_id'], ['spec.teiid_model.spec_version_id', 'spec.teiid_model.id'], name=op.f('fk_teiid_alter_spec_version_id_model_id_teiid_model'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_teiid_alter_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_teiid_alter')),
    sa.UniqueConstraint('spec_version_id', 'model_id', 'seq', name=op.f('uq_teiid_alter_spec_version_id_model_id_seq')),
    schema='spec'
    )
    op.create_index(op.f('ix_teiid_alter_spec_version_id'), 'teiid_alter', ['spec_version_id'], unique=False, schema='spec')
    op.create_table('teiid_column',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('table_id', sa.BigInteger(), nullable=False),
    sa.Column('uid', sa.Text(), nullable=True),
    sa.Column('name', sa.Text(), nullable=False, comment='nama di Teiid'),
    sa.Column('name_in_source', sa.Text(), nullable=True),
    sa.Column('source_column', sa.Text(), nullable=True, comment='nama efektif di sumber, dinormalisasi'),
    sa.Column('position', sa.Integer(), nullable=False, comment='tidak dijamin berurutan'),
    sa.Column('data_type', sa.Text(), nullable=False),
    sa.Column('native_type', sa.Text(), nullable=True),
    sa.Column('nullable', sa.Boolean(), nullable=True),
    sa.Column('length', sa.Integer(), nullable=True),
    sa.Column('precision', sa.Integer(), nullable=True),
    sa.Column('scale', sa.Integer(), nullable=True),
    sa.Column('in_primary_key', sa.Boolean(), server_default='false', nullable=False),
    sa.Column('managed_by', sa.Text(), server_default='human', nullable=False),
    sa.Column('introduced_in_version', sa.BigInteger(), nullable=True),
    sa.CheckConstraint("managed_by IN ('human', 'ascam')", name=op.f('ck_teiid_column_managed_by')),
    sa.ForeignKeyConstraint(['introduced_in_version'], ['spec.spec_version.id'], name=op.f('fk_teiid_column_introduced_in_version_spec_version'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['spec_version_id', 'table_id'], ['spec.teiid_table.spec_version_id', 'spec.teiid_table.id'], name=op.f('fk_teiid_column_spec_version_id_table_id_teiid_table'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_teiid_column_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_teiid_column')),
    sa.UniqueConstraint('spec_version_id', 'id', name=op.f('uq_teiid_column_spec_version_id_id')),
    sa.UniqueConstraint('spec_version_id', 'table_id', 'name', name=op.f('uq_teiid_column_spec_version_id_table_id_name')),
    schema='spec'
    )
    op.create_index(op.f('ix_teiid_column_spec_version_id'), 'teiid_column', ['spec_version_id'], unique=False, schema='spec')
    op.create_index(op.f('ix_teiid_column_spec_version_id_table_id_source_column'), 'teiid_column', ['spec_version_id', 'table_id', 'source_column'], unique=False, schema='spec')
    op.create_table('teiid_view',
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('table_id', sa.BigInteger(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False, comment='SYSADMIN.Views.Body'),
    sa.Column('parse_status', sa.Text(), nullable=False),
    sa.Column('uses_star', sa.Boolean(), server_default='false', nullable=False),
    sa.CheckConstraint("parse_status IN ('ok', 'failed')", name=op.f('ck_teiid_view_parse_status')),
    sa.ForeignKeyConstraint(['spec_version_id', 'table_id'], ['spec.teiid_table.spec_version_id', 'spec.teiid_table.id'], name=op.f('fk_teiid_view_spec_version_id_table_id_teiid_table'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_teiid_view_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('spec_version_id', 'table_id', name=op.f('pk_teiid_view')),
    schema='spec'
    )
    op.create_table('execution_step',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('execution_id', sa.BigInteger(), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('status', sa.Text(), server_default='running', nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('detail', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.CheckConstraint("name IN ('deploy_vdb', 'validate', 'switch', 'reload_ontop', 'verify', 'rollback', 'sync')", name=op.f('ck_execution_step_name')),
    sa.CheckConstraint("status IN ('running', 'succeeded', 'failed', 'skipped')", name=op.f('ck_execution_step_status')),
    sa.ForeignKeyConstraint(['execution_id'], ['ops.execution.id'], name=op.f('fk_execution_step_execution_id_execution'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_execution_step')),
    sa.UniqueConstraint('execution_id', 'seq', name=op.f('uq_execution_step_execution_id_seq')),
    schema='ops'
    )
    op.create_index(op.f('ix_execution_step_execution_id'), 'execution_step', ['execution_id'], unique=False, schema='ops')
    op.create_table('validation',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('execution_id', sa.BigInteger(), nullable=True),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('validator', sa.Text(), nullable=False),
    sa.Column('passed', sa.Boolean(), nullable=False),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False, comment='termasuk validity-errors Teiid'),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("validator IN ('teiid_status', 'ontop_validate', 'ascam_vocabulary', 'sparql_regression')", name=op.f('ck_validation_validator')),
    sa.ForeignKeyConstraint(['execution_id'], ['ops.execution.id'], name=op.f('fk_validation_execution_id_execution'), ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_validation_spec_version_id_spec_version'), ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_validation')),
    schema='ops'
    )
    op.create_index(op.f('ix_validation_execution_id'), 'validation', ['execution_id'], unique=False, schema='ops')
    op.create_index(op.f('ix_validation_spec_version_id'), 'validation', ['spec_version_id'], unique=False, schema='ops')
    op.create_table('logical_column_source',
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('logical_column_id', sa.BigInteger(), nullable=False),
    sa.Column('teiid_column_id', sa.BigInteger(), nullable=False),
    sa.ForeignKeyConstraint(['spec_version_id', 'logical_column_id'], ['spec.logical_column.spec_version_id', 'spec.logical_column.id'], name=op.f('fk_logical_column_source_spec_version_id_logical_column_id_logical_column'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id', 'teiid_column_id'], ['spec.teiid_column.spec_version_id', 'spec.teiid_column.id'], name=op.f('fk_logical_column_source_spec_version_id_teiid_column_id_teiid_column'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_logical_column_source_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('spec_version_id', 'logical_column_id', 'teiid_column_id', name=op.f('pk_logical_column_source')),
    schema='spec'
    )
    op.create_table('ont_annotation',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('entity_id', sa.BigInteger(), nullable=False),
    sa.Column('property_iri', sa.Text(), nullable=False),
    sa.Column('value', sa.Text(), nullable=False),
    sa.Column('lang', sa.Text(), nullable=True),
    sa.Column('managed_by', sa.Text(), server_default='human', nullable=False),
    sa.CheckConstraint("managed_by IN ('human', 'ascam')", name=op.f('ck_ont_annotation_managed_by')),
    sa.ForeignKeyConstraint(['spec_version_id', 'entity_id'], ['spec.ont_entity.spec_version_id', 'spec.ont_entity.id'], name=op.f('fk_ont_annotation_spec_version_id_entity_id_ont_entity'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_ont_annotation_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ont_annotation')),
    schema='spec'
    )
    op.create_index(op.f('ix_ont_annotation_spec_version_id'), 'ont_annotation', ['spec_version_id'], unique=False, schema='spec')
    op.create_table('ont_domain',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('entity_id', sa.BigInteger(), nullable=False),
    sa.Column('class_expression', sa.Text(), nullable=False, comment='IRI atau bentuk kompleks (mis. union)'),
    sa.Column('is_simple', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['spec_version_id', 'entity_id'], ['spec.ont_entity.spec_version_id', 'spec.ont_entity.id'], name=op.f('fk_ont_domain_spec_version_id_entity_id_ont_entity'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_ont_domain_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ont_domain')),
    schema='spec'
    )
    op.create_index(op.f('ix_ont_domain_spec_version_id'), 'ont_domain', ['spec_version_id'], unique=False, schema='spec')
    op.create_table('ont_property_relation',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('entity_id', sa.BigInteger(), nullable=False),
    sa.Column('relation', sa.Text(), nullable=False),
    sa.Column('other_iri', sa.Text(), nullable=False),
    sa.CheckConstraint("relation IN ('sub_property_of', 'equivalent_property', 'inverse_of', 'disjoint_with')", name=op.f('ck_ont_property_relation_relation')),
    sa.ForeignKeyConstraint(['spec_version_id', 'entity_id'], ['spec.ont_entity.spec_version_id', 'spec.ont_entity.id'], name=op.f('fk_ont_property_relation_spec_version_id_entity_id_ont_entity'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_ont_property_relation_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ont_property_relation')),
    schema='spec'
    )
    op.create_index(op.f('ix_ont_property_relation_spec_version_id'), 'ont_property_relation', ['spec_version_id'], unique=False, schema='spec')
    op.create_table('ont_range',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('entity_id', sa.BigInteger(), nullable=False),
    sa.Column('range_iri', sa.Text(), nullable=False),
    sa.Column('owl2ql_compatible', sa.Boolean(), nullable=True),
    sa.ForeignKeyConstraint(['spec_version_id', 'entity_id'], ['spec.ont_entity.spec_version_id', 'spec.ont_entity.id'], name=op.f('fk_ont_range_spec_version_id_entity_id_ont_entity'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_ont_range_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ont_range')),
    schema='spec'
    )
    op.create_index(op.f('ix_ont_range_spec_version_id'), 'ont_range', ['spec_version_id'], unique=False, schema='spec')
    op.create_table('sql_reference',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('triples_map_id', sa.BigInteger(), nullable=False),
    sa.Column('teiid_column_id', sa.BigInteger(), nullable=False),
    sa.Column('clause', sa.Text(), nullable=False),
    sa.CheckConstraint("clause IN ('where', 'join', 'group_by', 'having', 'order_by', 'subquery', 'other')", name=op.f('ck_sql_reference_clause')),
    sa.ForeignKeyConstraint(['spec_version_id', 'teiid_column_id'], ['spec.teiid_column.spec_version_id', 'spec.teiid_column.id'], name=op.f('fk_sql_reference_spec_version_id_teiid_column_id_teiid_column'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id', 'triples_map_id'], ['spec.triples_map.spec_version_id', 'spec.triples_map.id'], name=op.f('fk_sql_reference_spec_version_id_triples_map_id_triples_map'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_sql_reference_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_sql_reference')),
    schema='spec'
    )
    op.create_index(op.f('ix_sql_reference_spec_version_id'), 'sql_reference', ['spec_version_id'], unique=False, schema='spec')
    op.create_table('teiid_dependency',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('dependent_table_id', sa.BigInteger(), nullable=False),
    sa.Column('dependent_column_id', sa.BigInteger(), nullable=True, comment='kosong = tingkat view'),
    sa.Column('used_table_id', sa.BigInteger(), nullable=False),
    sa.Column('used_column_id', sa.BigInteger(), nullable=True, comment='kosong = tingkat tabel'),
    sa.Column('derived_role', sa.Text(), nullable=False),
    sa.CheckConstraint("derived_role IN ('projection', 'predicate', 'table')", name=op.f('ck_teiid_dependency_derived_role')),
    sa.ForeignKeyConstraint(['spec_version_id', 'dependent_column_id'], ['spec.teiid_column.spec_version_id', 'spec.teiid_column.id'], name=op.f('fk_teiid_dependency_spec_version_id_dependent_column_id_teiid_column'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id', 'dependent_table_id'], ['spec.teiid_table.spec_version_id', 'spec.teiid_table.id'], name=op.f('fk_teiid_dependency_spec_version_id_dependent_table_id_teiid_table'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id', 'used_column_id'], ['spec.teiid_column.spec_version_id', 'spec.teiid_column.id'], name=op.f('fk_teiid_dependency_spec_version_id_used_column_id_teiid_column'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id', 'used_table_id'], ['spec.teiid_table.spec_version_id', 'spec.teiid_table.id'], name=op.f('fk_teiid_dependency_spec_version_id_used_table_id_teiid_table'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_teiid_dependency_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_teiid_dependency')),
    schema='spec'
    )
    op.create_index(op.f('ix_teiid_dependency_spec_version_id'), 'teiid_dependency', ['spec_version_id'], unique=False, schema='spec')
    op.create_index(op.f('ix_teiid_dependency_spec_version_id_used_column_id'), 'teiid_dependency', ['spec_version_id', 'used_column_id'], unique=False, schema='spec')
    op.create_index(op.f('ix_teiid_dependency_spec_version_id_used_table_id'), 'teiid_dependency', ['spec_version_id', 'used_table_id'], unique=False, schema='spec')
    op.create_table('teiid_view_column',
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('column_id', sa.BigInteger(), nullable=False),
    sa.Column('expression_kind', sa.Text(), nullable=False),
    sa.Column('expression_sql', sa.Text(), nullable=True),
    sa.CheckConstraint("expression_kind IN ('passthrough', 'expression', 'star', 'unknown')", name=op.f('ck_teiid_view_column_expression_kind')),
    sa.ForeignKeyConstraint(['spec_version_id', 'column_id'], ['spec.teiid_column.spec_version_id', 'spec.teiid_column.id'], name=op.f('fk_teiid_view_column_spec_version_id_column_id_teiid_column'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_teiid_view_column_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('spec_version_id', 'column_id', name=op.f('pk_teiid_view_column')),
    schema='spec'
    )
    op.create_table('term_map',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('triples_map_id', sa.BigInteger(), nullable=False),
    sa.Column('pom_id', sa.BigInteger(), nullable=True, comment='kosong untuk subject map'),
    sa.Column('position', sa.Text(), nullable=False),
    sa.Column('value_kind', sa.Text(), nullable=False),
    sa.Column('value', sa.Text(), nullable=False),
    sa.Column('term_type', sa.Text(), nullable=True),
    sa.Column('datatype', sa.Text(), nullable=True),
    sa.Column('language', sa.Text(), nullable=True),
    sa.CheckConstraint("position IN ('subject', 'predicate', 'object', 'graph')", name=op.f('ck_term_map_position')),
    sa.CheckConstraint("term_type IN ('iri', 'literal', 'blank')", name=op.f('ck_term_map_term_type')),
    sa.CheckConstraint("value_kind IN ('constant', 'column', 'template', 'parent_triples_map')", name=op.f('ck_term_map_value_kind')),
    sa.ForeignKeyConstraint(['spec_version_id', 'pom_id'], ['spec.predicate_object_map.spec_version_id', 'spec.predicate_object_map.id'], name=op.f('fk_term_map_spec_version_id_pom_id_predicate_object_map'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id', 'triples_map_id'], ['spec.triples_map.spec_version_id', 'spec.triples_map.id'], name=op.f('fk_term_map_spec_version_id_triples_map_id_triples_map'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_term_map_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_term_map')),
    sa.UniqueConstraint('spec_version_id', 'id', name=op.f('uq_term_map_spec_version_id_id')),
    schema='spec'
    )
    op.create_index(op.f('ix_term_map_spec_version_id'), 'term_map', ['spec_version_id'], unique=False, schema='spec')
    op.create_table('column_usage',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('foreign_column_id', sa.BigInteger(), nullable=False, comment='kolom foreign table (ujung sumber)'),
    sa.Column('triples_map_id', sa.BigInteger(), nullable=False),
    sa.Column('term_map_id', sa.BigInteger(), nullable=True, comment='kosong bila hanya di SQL'),
    sa.Column('role', sa.Text(), nullable=False),
    sa.Column('predicate_iri', sa.Text(), nullable=True),
    sa.Column('predicate_entity_id', sa.BigInteger(), nullable=True),
    sa.Column('path', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False, comment='urutan kolom view yang dilalui'),
    sa.Column('weakest_link', sa.Text(), nullable=False),
    sa.CheckConstraint("role IN ('literal_value', 'iri_template', 'join_key', 'sql_predicate', 'dynamic_predicate', 'projection_only')", name=op.f('ck_column_usage_role')),
    sa.CheckConstraint("weakest_link IN ('direct', 'passthrough', 'star', 'expression', 'predicate')", name=op.f('ck_column_usage_weakest_link')),
    sa.ForeignKeyConstraint(['spec_version_id', 'foreign_column_id'], ['spec.teiid_column.spec_version_id', 'spec.teiid_column.id'], name=op.f('fk_column_usage_spec_version_id_foreign_column_id_teiid_column'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id', 'predicate_entity_id'], ['spec.ont_entity.spec_version_id', 'spec.ont_entity.id'], name=op.f('fk_column_usage_spec_version_id_predicate_entity_id_ont_entity'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id', 'term_map_id'], ['spec.term_map.spec_version_id', 'spec.term_map.id'], name=op.f('fk_column_usage_spec_version_id_term_map_id_term_map'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id', 'triples_map_id'], ['spec.triples_map.spec_version_id', 'spec.triples_map.id'], name=op.f('fk_column_usage_spec_version_id_triples_map_id_triples_map'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_column_usage_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_column_usage')),
    schema='spec'
    )
    op.create_index(op.f('ix_column_usage_spec_version_id'), 'column_usage', ['spec_version_id'], unique=False, schema='spec')
    op.create_index(op.f('ix_column_usage_spec_version_id_foreign_column_id'), 'column_usage', ['spec_version_id', 'foreign_column_id'], unique=False, schema='spec')
    op.create_index(op.f('ix_column_usage_spec_version_id_predicate_iri'), 'column_usage', ['spec_version_id', 'predicate_iri'], unique=False, schema='spec')
    op.create_table('join_condition',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=False), nullable=False),
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('term_map_id', sa.BigInteger(), nullable=False),
    sa.Column('child_column', sa.Text(), nullable=False),
    sa.Column('parent_column', sa.Text(), nullable=False),
    sa.Column('parent_triples_map_iri', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['spec_version_id', 'term_map_id'], ['spec.term_map.spec_version_id', 'spec.term_map.id'], name=op.f('fk_join_condition_spec_version_id_term_map_id_term_map'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_join_condition_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_join_condition')),
    schema='spec'
    )
    op.create_index(op.f('ix_join_condition_spec_version_id'), 'join_condition', ['spec_version_id'], unique=False, schema='spec')
    op.create_table('term_map_column',
    sa.Column('spec_version_id', sa.BigInteger(), nullable=False),
    sa.Column('term_map_id', sa.BigInteger(), nullable=False),
    sa.Column('logical_column_id', sa.BigInteger(), nullable=False),
    sa.Column('template_position', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['spec_version_id', 'logical_column_id'], ['spec.logical_column.spec_version_id', 'spec.logical_column.id'], name=op.f('fk_term_map_column_spec_version_id_logical_column_id_logical_column'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id', 'term_map_id'], ['spec.term_map.spec_version_id', 'spec.term_map.id'], name=op.f('fk_term_map_column_spec_version_id_term_map_id_term_map'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['spec_version_id'], ['spec.spec_version.id'], name=op.f('fk_term_map_column_spec_version_id_spec_version'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('spec_version_id', 'term_map_id', 'logical_column_id', name=op.f('pk_term_map_column')),
    schema='spec'
    )
    # FK melingkar spec_version.plan_id -> ops.adaptation_plan dibuat setelah kedua tabel ada
    op.create_foreign_key('fk_spec_version_plan_id_adaptation_plan', 'spec_version', 'adaptation_plan',
                          ['plan_id'], ['id'], source_schema='spec', referent_schema='ops',
                          ondelete='RESTRICT')
    op.execute(INTEGRITY_SQL)


def downgrade() -> None:
    op.execute(DROP_INTEGRITY_SQL)
    op.drop_constraint('fk_spec_version_plan_id_adaptation_plan', 'spec_version',
                       schema='spec', type_='foreignkey')
    op.drop_table('term_map_column', schema='spec')
    op.drop_index(op.f('ix_join_condition_spec_version_id'), table_name='join_condition', schema='spec')
    op.drop_table('join_condition', schema='spec')
    op.drop_index(op.f('ix_column_usage_spec_version_id_predicate_iri'), table_name='column_usage', schema='spec')
    op.drop_index(op.f('ix_column_usage_spec_version_id_foreign_column_id'), table_name='column_usage', schema='spec')
    op.drop_index(op.f('ix_column_usage_spec_version_id'), table_name='column_usage', schema='spec')
    op.drop_table('column_usage', schema='spec')
    op.drop_index(op.f('ix_term_map_spec_version_id'), table_name='term_map', schema='spec')
    op.drop_table('term_map', schema='spec')
    op.drop_table('teiid_view_column', schema='spec')
    op.drop_index(op.f('ix_teiid_dependency_spec_version_id_used_table_id'), table_name='teiid_dependency', schema='spec')
    op.drop_index(op.f('ix_teiid_dependency_spec_version_id_used_column_id'), table_name='teiid_dependency', schema='spec')
    op.drop_index(op.f('ix_teiid_dependency_spec_version_id'), table_name='teiid_dependency', schema='spec')
    op.drop_table('teiid_dependency', schema='spec')
    op.drop_index(op.f('ix_sql_reference_spec_version_id'), table_name='sql_reference', schema='spec')
    op.drop_table('sql_reference', schema='spec')
    op.drop_index(op.f('ix_ont_range_spec_version_id'), table_name='ont_range', schema='spec')
    op.drop_table('ont_range', schema='spec')
    op.drop_index(op.f('ix_ont_property_relation_spec_version_id'), table_name='ont_property_relation', schema='spec')
    op.drop_table('ont_property_relation', schema='spec')
    op.drop_index(op.f('ix_ont_domain_spec_version_id'), table_name='ont_domain', schema='spec')
    op.drop_table('ont_domain', schema='spec')
    op.drop_index(op.f('ix_ont_annotation_spec_version_id'), table_name='ont_annotation', schema='spec')
    op.drop_table('ont_annotation', schema='spec')
    op.drop_table('logical_column_source', schema='spec')
    op.drop_index(op.f('ix_validation_spec_version_id'), table_name='validation', schema='ops')
    op.drop_index(op.f('ix_validation_execution_id'), table_name='validation', schema='ops')
    op.drop_table('validation', schema='ops')
    op.drop_index(op.f('ix_execution_step_execution_id'), table_name='execution_step', schema='ops')
    op.drop_table('execution_step', schema='ops')
    op.drop_table('teiid_view', schema='spec')
    op.drop_index(op.f('ix_teiid_column_spec_version_id_table_id_source_column'), table_name='teiid_column', schema='spec')
    op.drop_index(op.f('ix_teiid_column_spec_version_id'), table_name='teiid_column', schema='spec')
    op.drop_table('teiid_column', schema='spec')
    op.drop_index(op.f('ix_teiid_alter_spec_version_id'), table_name='teiid_alter', schema='spec')
    op.drop_table('teiid_alter', schema='spec')
    op.drop_table('subject_class', schema='spec')
    op.drop_index(op.f('ix_predicate_object_map_spec_version_id'), table_name='predicate_object_map', schema='spec')
    op.drop_table('predicate_object_map', schema='spec')
    op.drop_table('ont_import', schema='spec')
    op.drop_index(op.f('ix_ont_entity_spec_version_id_iri'), table_name='ont_entity', schema='spec')
    op.drop_index(op.f('ix_ont_entity_spec_version_id'), table_name='ont_entity', schema='spec')
    op.drop_table('ont_entity', schema='spec')
    op.drop_index(op.f('ix_logical_source_spec_version_id'), table_name='logical_source', schema='spec')
    op.drop_table('logical_source', schema='spec')
    op.drop_index(op.f('ix_logical_column_spec_version_id'), table_name='logical_column', schema='spec')
    op.drop_table('logical_column', schema='spec')
    op.drop_index(op.f('ix_plan_action_plan_id'), table_name='plan_action', schema='ops')
    op.drop_table('plan_action', schema='ops')
    op.drop_index(op.f('ix_notification_obdf_id'), table_name='notification', schema='ops')
    op.drop_table('notification', schema='ops')
    op.drop_index(op.f('ix_execution_plan_id'), table_name='execution', schema='ops')
    op.drop_table('execution', schema='ops')
    op.drop_index(op.f('ix_triples_map_spec_version_id'), table_name='triples_map', schema='spec')
    op.drop_table('triples_map', schema='spec')
    op.drop_index(op.f('ix_teiid_table_spec_version_id_source_schema_source_table'), table_name='teiid_table', schema='spec')
    op.drop_index(op.f('ix_teiid_table_spec_version_id'), table_name='teiid_table', schema='spec')
    op.drop_table('teiid_table', schema='spec')
    op.drop_index(op.f('ix_teiid_routine_spec_version_id'), table_name='teiid_routine', schema='spec')
    op.drop_table('teiid_routine', schema='spec')
    op.drop_index(op.f('ix_teiid_model_source_spec_version_id'), table_name='teiid_model_source', schema='spec')
    op.drop_index(op.f('ix_teiid_model_source_source_system_id'), table_name='teiid_model_source', schema='spec')
    op.drop_table('teiid_model_source', schema='spec')
    op.drop_index(op.f('ix_rdf_triple_spec_version_id_artifact_id'), table_name='rdf_triple', schema='spec')
    op.drop_index(op.f('ix_rdf_triple_spec_version_id'), table_name='rdf_triple', schema='spec')
    op.drop_index(op.f('ix_rdf_triple_predicate'), table_name='rdf_triple', schema='spec')
    op.drop_table('rdf_triple', schema='spec')
    op.drop_index(op.f('ix_ontology_spec_version_id'), table_name='ontology', schema='spec')
    op.drop_table('ontology', schema='spec')
    op.drop_index(op.f('ix_connection_check_target_id'), table_name='connection_check', schema='registry')
    op.drop_table('connection_check', schema='registry')
    op.drop_index(op.f('ix_adaptation_plan_event_id'), table_name='adaptation_plan', schema='ops')
    op.drop_table('adaptation_plan', schema='ops')
    op.drop_index(op.f('ix_teiid_model_spec_version_id'), table_name='teiid_model', schema='spec')
    op.drop_table('teiid_model', schema='spec')
    op.drop_index(op.f('ix_consistency_issue_spec_version_id'), table_name='consistency_issue', schema='spec')
    op.drop_table('consistency_issue', schema='spec')
    op.drop_index(op.f('ix_artifact_spec_version_id'), table_name='artifact', schema='spec')
    op.drop_table('artifact', schema='spec')
    op.drop_index(op.f('ix_target_obdf_id'), table_name='target', schema='registry')
    op.drop_index(op.f('ix_target_credential_id'), table_name='target', schema='registry')
    op.drop_table('target', schema='registry')
    op.drop_index(op.f('ix_sync_run_obdf_id'), table_name='sync_run', schema='ops')
    op.drop_table('sync_run', schema='ops')
    op.drop_index(op.f('ix_schema_event_source_system_id'), table_name='schema_event', schema='ops')
    op.drop_index(op.f('ix_schema_event_obdf_id'), table_name='schema_event', schema='ops')
    op.drop_table('schema_event', schema='ops')
    op.drop_index('uq_spec_version_one_active', table_name='spec_version', schema='spec', postgresql_where=sa.text("status = 'active'"))
    op.drop_index(op.f('ix_spec_version_obdf_id'), table_name='spec_version', schema='spec')
    op.drop_table('spec_version', schema='spec')
    op.drop_index(op.f('ix_type_mapping_obdf_id'), table_name='type_mapping', schema='registry')
    op.drop_table('type_mapping', schema='registry')
    op.drop_index(op.f('ix_source_system_obdf_id'), table_name='source_system', schema='registry')
    op.drop_table('source_system', schema='registry')
    op.drop_table('setting', schema='registry')
    op.drop_table('naming_policy', schema='registry')
    op.drop_index(op.f('ix_credential_obdf_id'), table_name='credential', schema='registry')
    op.drop_table('credential', schema='registry')
    op.drop_index(op.f('ix_audit_log_obdf_id'), table_name='audit_log', schema='ops')
    op.drop_table('audit_log', schema='ops')
    op.drop_table('obdf_instance', schema='registry')
    for schema in reversed(SCHEMAS):
        op.execute(sa.schema.DropSchema(schema, if_exists=True))
