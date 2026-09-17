"""Skema `ops`: jejak MAPE-K dan operasi (append-only; baris tidak dihapus)."""
import uuid
from datetime import datetime

from sqlalchemy import (BigInteger, Boolean, DateTime, ForeignKey, Integer, Text,
                        UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, created_at, one_of, pk_id

S = {'schema': 'ops'}


class SyncRun(Base):
    __tablename__ = 'sync_run'
    __table_args__ = (one_of('trigger', ('startup', 'manual', 'scheduled', 'post_adaptation')),
                      one_of('status', ('running', 'succeeded', 'failed')), S)
    id: Mapped[int] = pk_id()
    obdf_id: Mapped[int] = mapped_column(ForeignKey('registry.obdf_instance.id', ondelete='RESTRICT'), index=True)
    trigger: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default='running')
    produced_spec_version_id: Mapped[int | None] = mapped_column(
        ForeignKey('spec.spec_version.id', ondelete='RESTRICT'))
    drift_detected: Mapped[bool | None] = mapped_column(Boolean)
    drift_summary: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SchemaEvent(Base):
    __tablename__ = 'schema_event'
    __table_args__ = (one_of('status', ('received', 'planned', 'ignored', 'duplicate', 'failed')), S)
    id: Mapped[int] = pk_id()
    event_uid: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), unique=True)
    obdf_id: Mapped[int] = mapped_column(ForeignKey('registry.obdf_instance.id', ondelete='RESTRICT'), index=True)
    source_system_id: Mapped[int | None] = mapped_column(
        ForeignKey('registry.source_system.id', ondelete='RESTRICT'), index=True)
    raw_message: Mapped[dict] = mapped_column(JSONB)
    structured: Mapped[dict | None] = mapped_column(JSONB, comment='event terformalisasi')
    captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = created_at()
    status: Mapped[str] = mapped_column(Text, server_default='received')
    ignore_reason: Mapped[str | None] = mapped_column(Text)


PLAN_STATUS = ('pending_approval', 'approved', 'rejected', 'superseded', 'executed', 'failed')


class AdaptationPlan(Base):
    __tablename__ = 'adaptation_plan'
    __table_args__ = (one_of('decision', ('auto', 'hitl')), one_of('status', PLAN_STATUS), S)
    id: Mapped[int] = pk_id()
    event_id: Mapped[int] = mapped_column(ForeignKey('ops.schema_event.id', ondelete='RESTRICT'), index=True)
    base_spec_version_id: Mapped[int] = mapped_column(
        ForeignKey('spec.spec_version.id', ondelete='RESTRICT'), comment='optimistic concurrency')
    pattern: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)
    impact: Mapped[dict] = mapped_column(JSONB, server_default='{}')
    reasons: Mapped[list] = mapped_column(JSONB, server_default='[]')
    created_at: Mapped[datetime] = created_at()
    decided_by: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlanAction(Base):
    __tablename__ = 'plan_action'
    __table_args__ = (UniqueConstraint('plan_id', 'seq'),
                      one_of('artifact', ('vdb', 'r2rml', 'ontology')), S)
    id: Mapped[int] = pk_id()
    plan_id: Mapped[int] = mapped_column(ForeignKey('ops.adaptation_plan.id', ondelete='RESTRICT'), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    artifact: Mapped[str] = mapped_column(Text)
    operation: Mapped[str] = mapped_column(Text)
    params: Mapped[dict] = mapped_column(JSONB, server_default='{}')


class Execution(Base):
    __tablename__ = 'execution'
    __table_args__ = (one_of('status', ('running', 'succeeded', 'failed', 'rolled_back')), S)
    id: Mapped[int] = pk_id()
    plan_id: Mapped[int] = mapped_column(ForeignKey('ops.adaptation_plan.id', ondelete='RESTRICT'), index=True)
    candidate_spec_version_id: Mapped[int | None] = mapped_column(
        ForeignKey('spec.spec_version.id', ondelete='RESTRICT'))
    status: Mapped[str] = mapped_column(Text, server_default='running')
    timings: Mapped[dict] = mapped_column(JSONB, server_default='{}', comment='dekomposisi Δt_adapt')
    failure: Mapped[dict | None] = mapped_column(JSONB)
    started_at: Mapped[datetime] = created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


STEP_NAMES = ('deploy_vdb', 'validate', 'switch', 'reload_ontop', 'verify', 'rollback', 'sync')


class ExecutionStep(Base):
    __tablename__ = 'execution_step'
    __table_args__ = (UniqueConstraint('execution_id', 'seq'), one_of('name', STEP_NAMES),
                      one_of('status', ('running', 'succeeded', 'failed', 'skipped')), S)
    id: Mapped[int] = pk_id()
    execution_id: Mapped[int] = mapped_column(ForeignKey('ops.execution.id', ondelete='RESTRICT'), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default='running')
    started_at: Mapped[datetime] = created_at()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detail: Mapped[dict] = mapped_column(JSONB, server_default='{}')


VALIDATORS = ('teiid_status', 'ontop_validate', 'ascam_vocabulary', 'sparql_regression')


class Validation(Base):
    __tablename__ = 'validation'
    __table_args__ = (one_of('validator', VALIDATORS), S)
    id: Mapped[int] = pk_id()
    execution_id: Mapped[int | None] = mapped_column(ForeignKey('ops.execution.id', ondelete='RESTRICT'), index=True)
    spec_version_id: Mapped[int] = mapped_column(ForeignKey('spec.spec_version.id', ondelete='RESTRICT'), index=True)
    validator: Mapped[str] = mapped_column(Text)
    passed: Mapped[bool] = mapped_column(Boolean)
    details: Mapped[dict] = mapped_column(JSONB, server_default='{}', comment='termasuk validity-errors Teiid')
    created_at: Mapped[datetime] = created_at()


class Notification(Base):
    __tablename__ = 'notification'
    __table_args__ = (one_of('severity', ('info', 'warning', 'error')), S)
    id: Mapped[int] = pk_id()
    obdf_id: Mapped[int] = mapped_column(ForeignKey('registry.obdf_instance.id', ondelete='RESTRICT'), index=True)
    plan_id: Mapped[int | None] = mapped_column(ForeignKey('ops.adaptation_plan.id', ondelete='RESTRICT'))
    severity: Mapped[str] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text)
    acknowledged: Mapped[bool] = mapped_column(Boolean, server_default='false')
    acknowledged_by: Mapped[str | None] = mapped_column(Text)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at()


class AuditLog(Base):
    __tablename__ = 'audit_log'
    __table_args__ = S
    id: Mapped[int] = pk_id()
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    obdf_id: Mapped[int | None] = mapped_column(
        ForeignKey('registry.obdf_instance.id', ondelete='RESTRICT'), index=True)
    actor: Mapped[str] = mapped_column(Text)
    action: Mapped[str] = mapped_column(Text)
    object_kind: Mapped[str | None] = mapped_column(Text)
    object_ref: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[dict] = mapped_column(JSONB, server_default='{}')
