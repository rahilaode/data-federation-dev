"""
Intake of schema events and creation of adaptation plans.

The Orchestrator sends formalised events here; the decision rules stay in one place
(ADR-0015). Events are idempotent by `event_uid`, and every plan stores the specification
version it was computed on (optimistic concurrency): a plan built on an old version must not
be executed after the specification has changed.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from ..transaction import TransactionalRoute
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import impact as impact_service
from ... import registry_service as svc
from ...db import ops, registry
from ...security.auth import current_actor
from ..deps import get_db
from ..schemas import (DecisionIn, EventIn, EventOut, EventResultOut, NotificationOut,
                       PlanActionOut, PlanOut)

router = APIRouter(route_class=TransactionalRoute, prefix='/api/v1', tags=['event'], dependencies=[Depends(current_actor)])


def _plan_out(db: Session, plan: ops.AdaptationPlan) -> PlanOut:
    actions = db.execute(select(ops.PlanAction).filter_by(plan_id=plan.id)
                         .order_by(ops.PlanAction.seq)).scalars().all()
    return PlanOut.model_validate(plan).model_copy(
        update={'actions': [PlanActionOut.model_validate(a) for a in actions]})


def _object_label(event_fields: dict) -> str:
    return '.'.join(x for x in (event_fields.get('schema'), event_fields.get('table'),
                                event_fields.get('column')) if x)


def _notification_message(source: str, event_fields: dict, report) -> str:
    op = event_fields.get('operation', '').upper()
    reasons_text = '; '.join(report.reasons)[:300]
    if report.decision == 'hitl':
        return (f'{op} change on {source}:{_object_label(event_fields)} is waiting for approval '
                f'({len(report.actions)} actions planned). Reasons: {reasons_text}')
    # notify: no actions; the message names 𝒟(c, ℱ) so the administrator knows what to repair
    affected = report.affected or {}
    parts = [f"{title} {', '.join(x.rsplit('#', 1)[-1].rsplit('/', 1)[-1] for x in affected[k])}"
              for k, title in (('views', 'view'), ('triples_maps', 'TriplesMap'),
                               ('predicates', 'property')) if affected.get(k)]
    return (f'{op} change on {source}:{_object_label(event_fields)} cannot be adapted automatically '
            f'and was not executed. Affected: {"; ".join(parts) or "-"}. Reasons: {reasons_text}. '
            'Repair the OBDF specification by hand, then run a sync.')


@router.post('/obdf/{obdf_id}/events', response_model=EventResultOut)
def ingest_event(obdf_id: int, body: EventIn, db: Session = Depends(get_db),
                 actor: str = Depends(current_actor)):
    svc.get_obdf(db, obdf_id)
    event_uid = body.event_uid or uuid.uuid4()
    existing = db.execute(select(ops.SchemaEvent).filter_by(event_uid=event_uid)).scalar_one_or_none()
    if existing is not None:
        plan = db.execute(select(ops.AdaptationPlan).filter_by(event_id=existing.id)
                          .order_by(ops.AdaptationPlan.id.desc())).scalars().first()
        # the same uid with different content means the idempotency key collided; it is flagged
        # so that it does not pass silently as a duplicate (finding of F6)
        conflict = existing.structured != body.structured()
        if conflict:
            svc.audit(db, actor, 'event_uid_conflict', obdf_id, 'schema_event', str(existing.id),
                      event_uid=str(event_uid), new=body.structured())
        return EventResultOut(event=EventOut.model_validate(existing), duplicate=True,
                              conflict=conflict, plan=_plan_out(db, plan) if plan else None)

    source = db.execute(select(registry.SourceSystem).filter_by(
        obdf_id=obdf_id, logical_name=body.source)).scalar_one_or_none()
    event = ops.SchemaEvent(event_uid=event_uid, obdf_id=obdf_id,
                            source_system_id=source.id if source else None,
                            raw_message=body.raw, structured=body.structured(),
                            captured_at=body.captured_at, received_at=datetime.now(timezone.utc),
                            status='received')
    db.add(event)
    db.flush()

    report = impact_service.analyze(db, obdf_id, body.structured())
    if report.decision == 'ignored':
        event.status = 'ignored'
        event.ignore_reason = '; '.join(report.reasons)[:1000]
        svc.audit(db, actor, 'event_ignored', obdf_id, 'schema_event', str(event.id),
                  reason=event.ignore_reason)
        db.flush()
        return EventResultOut(event=EventOut.model_validate(event))

    status = {'auto': 'approved', 'hitl': 'pending_approval', 'notify': 'notified'}[report.decision]
    plan = ops.AdaptationPlan(
        event_id=event.id, base_spec_version_id=report.spec_version_id, pattern=report.pattern,
        decision=report.decision, status=status,
        impact=report.as_dict(), reasons=report.reasons, created_at=datetime.now(timezone.utc))
    db.add(plan)
    db.flush()
    for seq, action in enumerate(report.actions, start=1):
        params = {k: v for k, v in action.items() if k not in ('artifact', 'operation')}
        db.add(ops.PlanAction(plan_id=plan.id, seq=seq, artifact=action['artifact'],
                              operation=action['operation'], params=params))
    event.status = 'planned'
    if report.decision in ('hitl', 'notify'):
        db.add(ops.Notification(obdf_id=obdf_id, plan_id=plan.id, severity='warning',
                                message=_notification_message(body.source, body.structured(), report)))
    svc.audit(db, actor, 'plan_created', obdf_id, 'adaptation_plan', str(plan.id),
              decision=report.decision, pattern=report.pattern)
    db.flush()
    return EventResultOut(event=EventOut.model_validate(event), plan=_plan_out(db, plan))


@router.get('/obdf/{obdf_id}/events', response_model=list[EventOut])
def list_events(obdf_id: int, limit: int = 50, db: Session = Depends(get_db)):
    svc.get_obdf(db, obdf_id)
    return db.execute(select(ops.SchemaEvent).filter_by(obdf_id=obdf_id)
                      .order_by(ops.SchemaEvent.id.desc())
                      .limit(max(1, min(limit, 500)))).scalars().all()


@router.get('/obdf/{obdf_id}/plans', response_model=list[PlanOut])
def list_plans(obdf_id: int, status: str | None = None, limit: int = 50,
               db: Session = Depends(get_db)):
    svc.get_obdf(db, obdf_id)
    query = (select(ops.AdaptationPlan).join(ops.SchemaEvent, ops.SchemaEvent.id == ops.AdaptationPlan.event_id)
             .where(ops.SchemaEvent.obdf_id == obdf_id))
    if status:
        query = query.where(ops.AdaptationPlan.status == status)
    plans = db.execute(query.order_by(ops.AdaptationPlan.id.desc())
                       .limit(max(1, min(limit, 500)))).scalars().all()
    return [_plan_out(db, p) for p in plans]


@router.get('/plans/{plan_id}', response_model=PlanOut)
def get_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = db.get(ops.AdaptationPlan, plan_id)
    if plan is None:
        raise svc.NotFound(f'plan {plan_id} not found')
    return _plan_out(db, plan)


def _decide(db: Session, plan_id: int, actor: str, approve: bool,
            body: DecisionIn | None) -> PlanOut:
    note = body.note if body else None
    plan = db.get(ops.AdaptationPlan, plan_id)
    if plan is None:
        raise svc.NotFound(f'plan {plan_id} not found')
    if plan.status != 'pending_approval':
        raise HTTPException(409, f'plan has status {plan.status} and can no longer be decided')
    event = db.get(ops.SchemaEvent, plan.event_id)
    if approve:
        active = impact_service.active_version(db, event.obdf_id)
        if active is None or active.id != plan.base_spec_version_id:
            plan.status = 'superseded'
            plan.decided_by, plan.decided_at = actor, datetime.now(timezone.utc)
            svc.audit(db, actor, 'plan_superseded', event.obdf_id, 'adaptation_plan', str(plan.id),
                      base_spec_version_id=plan.base_spec_version_id,
                      active_spec_version_id=active.id if active else None)
            # the flag is committed separately: HTTPException makes the request transaction roll back
            db.commit()
            raise HTTPException(409, 'the specification has changed since the plan was created; '
                                     'the plan is marked superseded and must be built again')
    choice = body is not None and body.property_name is not None
    if choice and not approve:
        raise HTTPException(422, 'a property name and domain are given only when approving')
    if choice and plan.pattern != 'P-001':
        raise HTTPException(422, 'a property name and domain apply only to column additions')
    now = datetime.now(timezone.utc)
    decided = plan
    if choice:
        try:
            iri, new_actions = impact_service.revise_add(db, plan, body.property_name.strip(), body.domain)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        proposal = (plan.impact or {}).get('add_options') or {}
        if (iri, body.domain) != (proposal.get('proposed_iri'), proposal.get('proposed_domain')):
            decided = _revise(db, plan, actor, now, iri, body.domain, new_actions)
    if decided is plan:
        plan.status = 'approved' if approve else 'rejected'
        plan.decided_by, plan.decided_at = actor, now
    for notification in db.execute(select(ops.Notification).filter_by(plan_id=plan.id,
                                                                     acknowledged=False)).scalars():
        notification.acknowledged = True
        notification.acknowledged_by = actor
        notification.acknowledged_at = now
    svc.audit(db, actor, 'plan_approved' if approve else 'plan_rejected', event.obdf_id,
              'adaptation_plan', str(decided.id), note=note,
              revision_of=plan.id if decided is not plan else None)
    db.flush()
    return _plan_out(db, decided)


def _revise(db: Session, plan: ops.AdaptationPlan, actor: str, now: datetime, iri: str,
            domain: str | None, new_actions: list[dict]) -> ops.AdaptationPlan:
    """ADD plan revised by the administrator. ops rows are append-only, so the revision becomes
    a new plan (`revision_of`) and the original plan gets status `revised`."""
    impact = dict(plan.impact or {})
    impact['admin_choice'] = {'iri': iri, 'domain': domain}
    revision = ops.AdaptationPlan(
        event_id=plan.event_id, base_spec_version_id=plan.base_spec_version_id,
        pattern=plan.pattern, decision=plan.decision, status='approved', impact=impact,
        reasons=[*plan.reasons, f"revised by the administrator: property {iri}, domain {domain or '-'}"],
        created_at=now, decided_by=actor, decided_at=now, revision_of=plan.id)
    db.add(revision)
    db.flush()
    old_actions = db.execute(select(ops.PlanAction).filter_by(plan_id=plan.id, artifact='vdb')
                      .order_by(ops.PlanAction.seq)).scalars().all()
    combined = [{'artifact': a.artifact, 'operation': a.operation, **a.params} for a in old_actions] + new_actions
    for seq, action in enumerate(combined, start=1):
        params = {k: v for k, v in action.items() if k not in ('artifact', 'operation')}
        db.add(ops.PlanAction(plan_id=revision.id, seq=seq, artifact=action['artifact'],
                              operation=action['operation'], params=params))
    plan.status = 'revised'
    plan.decided_by, plan.decided_at = actor, now
    return revision


@router.post('/plans/{plan_id}/supersede', response_model=PlanOut)
def supersede_plan(plan_id: int, body: DecisionIn | None = None, db: Session = Depends(get_db),
                   actor: str = Depends(current_actor)):
    """Mark a plan as superseded so that it is neither executed nor retried.

    Used by the Executor when the base version of a plan is no longer active, and by the
    experiment procedure to neutralise plans that come from the restoration steps between runs.
    """
    plan = db.get(ops.AdaptationPlan, plan_id)
    if plan is None:
        raise svc.NotFound(f'plan {plan_id} not found')
    if plan.status not in ('approved', 'pending_approval'):
        raise HTTPException(409, f'plan has status {plan.status} and cannot be marked superseded')
    plan.status = 'superseded'
    plan.decided_by, plan.decided_at = actor, datetime.now(timezone.utc)
    event = db.get(ops.SchemaEvent, plan.event_id)
    svc.audit(db, actor, 'plan_superseded', event.obdf_id, 'adaptation_plan', str(plan.id),
              note=body.note if body else None)
    db.flush()
    return _plan_out(db, plan)


@router.post('/plans/{plan_id}/approve', response_model=PlanOut)
def approve_plan(plan_id: int, body: DecisionIn | None = None, db: Session = Depends(get_db),
                 actor: str = Depends(current_actor)):
    return _decide(db, plan_id, actor, True, body)


@router.post('/plans/{plan_id}/reject', response_model=PlanOut)
def reject_plan(plan_id: int, body: DecisionIn | None = None, db: Session = Depends(get_db),
                actor: str = Depends(current_actor)):
    return _decide(db, plan_id, actor, False, body)


@router.get('/obdf/{obdf_id}/notifications', response_model=list[NotificationOut])
def list_notifications(obdf_id: int, unread: bool = False, limit: int = 50,
                       db: Session = Depends(get_db)):
    svc.get_obdf(db, obdf_id)
    query = select(ops.Notification).filter_by(obdf_id=obdf_id)
    if unread:
        query = query.filter_by(acknowledged=False)
    return db.execute(query.order_by(ops.Notification.id.desc())
                      .limit(max(1, min(limit, 500)))).scalars().all()


@router.post('/notifications/{notification_id}/ack', response_model=NotificationOut)
def acknowledge_notification(notification_id: int, db: Session = Depends(get_db),
                             actor: str = Depends(current_actor)):
    notification = db.get(ops.Notification, notification_id)
    if notification is None:
        raise svc.NotFound(f'notification {notification_id} not found')
    if not notification.acknowledged:
        notification.acknowledged = True
        notification.acknowledged_by = actor
        notification.acknowledged_at = datetime.now(timezone.utc)
        db.flush()
    return notification
