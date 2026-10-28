"""
Recording of plan executions.

The Executor reports every step (ADR-0004) here, so the trail is complete even when the
Executor stops halfway: an execution that never finishes stays visible with status `running`
in the UI. The plan status is updated from the final result of the execution.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from ..transaction import TransactionalRoute
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import registry_service as svc
from ...db import ops
from ...security.auth import current_actor
from ..deps import get_db
from ..schemas import (ExecutionOut, ExecutionStartIn, FinishIn, StepIn, StepOut, ValidationIn)

router = APIRouter(route_class=TransactionalRoute, prefix='/api/v1', tags=['executions'], dependencies=[Depends(current_actor)])
PLAN_STATUS = {'succeeded': 'executed', 'failed': 'failed', 'rolled_back': 'failed'}


def _execution_out(db: Session, execution: ops.Execution) -> ExecutionOut:
    steps = db.execute(select(ops.ExecutionStep).filter_by(execution_id=execution.id)
                       .order_by(ops.ExecutionStep.seq)).scalars().all()
    return ExecutionOut.model_validate(execution).model_copy(
        update={'steps': [StepOut.model_validate(s) for s in steps]})


def _execution(db: Session, execution_id: int) -> ops.Execution:
    execution = db.get(ops.Execution, execution_id)
    if execution is None:
        raise svc.NotFound(f'execution {execution_id} not found')
    return execution


@router.post('/plans/{plan_id}/executions', response_model=ExecutionOut, status_code=201)
def start_execution(plan_id: int, body: ExecutionStartIn | None = None,
                    db: Session = Depends(get_db), actor: str = Depends(current_actor)):
    plan = db.get(ops.AdaptationPlan, plan_id)
    if plan is None:
        raise svc.NotFound(f'plan {plan_id} not found')
    if plan.status != 'approved':
        raise HTTPException(409, f'plan has status {plan.status}; only approved plans are executed')
    running = db.execute(select(ops.Execution).filter_by(plan_id=plan_id, status='running')
                          ).scalars().first()
    if running is not None:
        raise HTTPException(409, f'execution {running.id} of this plan is still running')
    execution = ops.Execution(plan_id=plan_id, status='running',
                              candidate_spec_version_id=body.candidate_spec_version_id if body else None,
                              started_at=datetime.now(timezone.utc))
    db.add(execution)
    db.flush()
    event = db.get(ops.SchemaEvent, plan.event_id)
    svc.audit(db, actor, 'execution_started', event.obdf_id, 'execution', str(execution.id),
              plan_id=plan_id)
    return _execution_out(db, execution)


@router.post('/executions/{execution_id}/steps', response_model=ExecutionOut)
def add_step(execution_id: int, body: StepIn, db: Session = Depends(get_db)):
    execution = _execution(db, execution_id)
    db.add(ops.ExecutionStep(execution_id=execution.id, seq=body.seq, name=body.name,
                             status=body.status, detail=body.detail,
                             started_at=body.started_at or datetime.now(timezone.utc),
                             finished_at=body.finished_at))
    db.flush()
    return _execution_out(db, execution)


@router.post('/executions/{execution_id}/validations', response_model=ExecutionOut)
def add_validation(execution_id: int, body: ValidationIn, db: Session = Depends(get_db)):
    execution = _execution(db, execution_id)
    plan = db.get(ops.AdaptationPlan, execution.plan_id)
    db.add(ops.Validation(execution_id=execution.id,
                          spec_version_id=body.spec_version_id or plan.base_spec_version_id,
                          validator=body.validator, passed=body.passed, details=body.details))
    db.flush()
    return _execution_out(db, execution)


@router.post('/executions/{execution_id}/finish', response_model=ExecutionOut)
def finish_execution(execution_id: int, body: FinishIn, db: Session = Depends(get_db),
                     actor: str = Depends(current_actor)):
    execution = _execution(db, execution_id)
    if execution.status != 'running':
        raise HTTPException(409, f'execution has status {execution.status}')
    execution.status = body.status
    execution.timings = body.timings
    execution.failure = body.failure
    execution.finished_at = datetime.now(timezone.utc)
    if body.candidate_spec_version_id is not None:
        execution.candidate_spec_version_id = body.candidate_spec_version_id
    plan = db.get(ops.AdaptationPlan, execution.plan_id)
    plan.status = PLAN_STATUS[body.status]
    event = db.get(ops.SchemaEvent, plan.event_id)
    if body.status != 'succeeded':
        db.add(ops.Notification(obdf_id=event.obdf_id, plan_id=plan.id, severity='error',
                                message=f'Execution of plan {plan.id} {body.status}: '
                                        f"{str((body.failure or {}).get('message', ''))[:300]}"))
    svc.audit(db, actor, f'execution_{body.status}', event.obdf_id, 'execution', str(execution.id),
              plan_id=plan.id, timings=body.timings)
    db.flush()
    return _execution_out(db, execution)


@router.get('/obdf/{obdf_id}/executions', response_model=list[ExecutionOut])
def list_executions(obdf_id: int, limit: int = 20, db: Session = Depends(get_db)):
    svc.get_obdf(db, obdf_id)
    rows = db.execute(select(ops.Execution)
                      .join(ops.AdaptationPlan, ops.AdaptationPlan.id == ops.Execution.plan_id)
                      .join(ops.SchemaEvent, ops.SchemaEvent.id == ops.AdaptationPlan.event_id)
                      .where(ops.SchemaEvent.obdf_id == obdf_id)
                      .order_by(ops.Execution.id.desc())
                      .limit(max(1, min(limit, 200)))).scalars().all()
    return [_execution_out(db, e) for e in rows]
