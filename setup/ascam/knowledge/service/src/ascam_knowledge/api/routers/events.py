"""
Penerimaan event skema dan pembentukan rencana adaptasi.

Orchestrator mengirim event terformalisasi ke sini; aturan keputusan tetap berada di satu
tempat (ADR-0015). Event bersifat idempoten berdasarkan `event_uid`, dan setiap rencana
menyimpan versi spesifikasi yang menjadi dasarnya (optimistic concurrency): rencana yang dibuat
di atas versi lama tidak boleh dieksekusi setelah spesifikasi berubah.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import impact as impact_service
from ... import registry_service as svc
from ...db import ops, registry
from ...security.auth import current_actor
from ..deps import get_db
from ..schemas import DecisionIn, EventIn, EventOut, EventResultOut, PlanActionOut, PlanOut

router = APIRouter(prefix='/api/v1', tags=['event'], dependencies=[Depends(current_actor)])


def _plan_out(db: Session, plan: ops.AdaptationPlan) -> PlanOut:
    actions = db.execute(select(ops.PlanAction).filter_by(plan_id=plan.id)
                         .order_by(ops.PlanAction.seq)).scalars().all()
    return PlanOut.model_validate(plan).model_copy(
        update={'actions': [PlanActionOut.model_validate(a) for a in actions]})


@router.post('/obdf/{obdf_id}/events', response_model=EventResultOut)
def ingest_event(obdf_id: int, body: EventIn, db: Session = Depends(get_db),
                 actor: str = Depends(current_actor)):
    svc.get_obdf(db, obdf_id)
    event_uid = body.event_uid or uuid.uuid4()
    existing = db.execute(select(ops.SchemaEvent).filter_by(event_uid=event_uid)).scalar_one_or_none()
    if existing is not None:
        plan = db.execute(select(ops.AdaptationPlan).filter_by(event_id=existing.id)
                          .order_by(ops.AdaptationPlan.id.desc())).scalars().first()
        return EventResultOut(event=EventOut.model_validate(existing), duplicate=True,
                              plan=_plan_out(db, plan) if plan else None)

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

    plan = ops.AdaptationPlan(
        event_id=event.id, base_spec_version_id=report.spec_version_id, pattern=report.pattern,
        decision=report.decision, status='approved' if report.decision == 'auto' else 'pending_approval',
        impact=report.as_dict(), reasons=report.reasons, created_at=datetime.now(timezone.utc))
    db.add(plan)
    db.flush()
    for seq, action in enumerate(report.actions, start=1):
        params = {k: v for k, v in action.items() if k not in ('artifact', 'operation')}
        db.add(ops.PlanAction(plan_id=plan.id, seq=seq, artifact=action['artifact'],
                              operation=action['operation'], params=params))
    event.status = 'planned'
    if report.decision == 'hitl':
        db.add(ops.Notification(obdf_id=obdf_id, plan_id=plan.id, severity='warning',
                                message=f'Rencana {report.pattern} menunggu persetujuan: '
                                        f"{'; '.join(report.reasons)[:400]}"))
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
        raise svc.NotFound(f'rencana {plan_id} tidak ditemukan')
    return _plan_out(db, plan)


def _decide(db: Session, plan_id: int, actor: str, approve: bool, note: str | None) -> PlanOut:
    plan = db.get(ops.AdaptationPlan, plan_id)
    if plan is None:
        raise svc.NotFound(f'rencana {plan_id} tidak ditemukan')
    if plan.status != 'pending_approval':
        raise HTTPException(409, f'rencana berstatus {plan.status}, tidak dapat diputuskan lagi')
    event = db.get(ops.SchemaEvent, plan.event_id)
    if approve:
        active = impact_service.active_version(db, event.obdf_id)
        if active is None or active.id != plan.base_spec_version_id:
            plan.status = 'superseded'
            plan.decided_by, plan.decided_at = actor, datetime.now(timezone.utc)
            svc.audit(db, actor, 'plan_superseded', event.obdf_id, 'adaptation_plan', str(plan.id),
                      base_spec_version_id=plan.base_spec_version_id,
                      active_spec_version_id=active.id if active else None)
            # penandaan di-commit terpisah: HTTPException membuat transaksi permintaan di-rollback
            db.commit()
            raise HTTPException(409, 'spesifikasi sudah berubah sejak rencana dibuat; '
                                     'rencana ditandai superseded dan perlu disusun ulang')
    plan.status = 'approved' if approve else 'rejected'
    plan.decided_by, plan.decided_at = actor, datetime.now(timezone.utc)
    for notification in db.execute(select(ops.Notification).filter_by(plan_id=plan.id,
                                                                     acknowledged=False)).scalars():
        notification.acknowledged = True
        notification.acknowledged_by = actor
        notification.acknowledged_at = datetime.now(timezone.utc)
    svc.audit(db, actor, 'plan_approved' if approve else 'plan_rejected', event.obdf_id,
              'adaptation_plan', str(plan.id), note=note)
    db.flush()
    return _plan_out(db, plan)


@router.post('/plans/{plan_id}/supersede', response_model=PlanOut)
def supersede_plan(plan_id: int, body: DecisionIn | None = None, db: Session = Depends(get_db),
                   actor: str = Depends(current_actor)):
    """Menandai rencana usang agar tidak dieksekusi atau dicoba ulang.

    Dipakai Executor ketika versi dasar rencana tidak lagi aktif, dan oleh prosedur eksperimen
    untuk menetralkan rencana yang timbul dari langkah pemulihan antar-run.
    """
    plan = db.get(ops.AdaptationPlan, plan_id)
    if plan is None:
        raise svc.NotFound(f'rencana {plan_id} tidak ditemukan')
    if plan.status not in ('approved', 'pending_approval'):
        raise HTTPException(409, f'rencana berstatus {plan.status}, tidak dapat ditandai usang')
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
    return _decide(db, plan_id, actor, True, body.note if body else None)


@router.post('/plans/{plan_id}/reject', response_model=PlanOut)
def reject_plan(plan_id: int, body: DecisionIn | None = None, db: Session = Depends(get_db),
                actor: str = Depends(current_actor)):
    return _decide(db, plan_id, actor, False, body.note if body else None)
