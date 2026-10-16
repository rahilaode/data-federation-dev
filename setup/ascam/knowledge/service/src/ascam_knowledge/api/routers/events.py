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


def _objek(struktur: dict) -> str:
    return '.'.join(x for x in (struktur.get('schema'), struktur.get('table'),
                                struktur.get('column')) if x)


def _pesan_notifikasi(source: str, struktur: dict, report) -> str:
    operasi = struktur.get('operation', '').upper()
    alasan = '; '.join(report.reasons)[:300]
    if report.decision == 'hitl':
        return (f'Perubahan {operasi} pada {source}:{_objek(struktur)} menunggu persetujuan '
                f'({len(report.actions)} tindakan direncanakan). Alasan: {alasan}')
    # notify: tidak ada tindakan; pesan menyebut 𝒟(c, ℱ) agar administrator tahu yang harus diperbaiki
    dampak = report.affected or {}
    bagian = [f"{judul} {', '.join(x.rsplit('#', 1)[-1].rsplit('/', 1)[-1] for x in dampak[k])}"
              for k, judul in (('views', 'view'), ('triples_maps', 'TriplesMap'),
                               ('predicates', 'property')) if dampak.get(k)]
    return (f'Perubahan {operasi} pada {source}:{_objek(struktur)} tidak dapat diadaptasi otomatis '
            f'dan tidak dieksekusi. Terdampak: {"; ".join(bagian) or "-"}. Alasan: {alasan}. '
            'Perbaiki spesifikasi OBDF secara manual, lalu jalankan sinkronisasi.')


@router.post('/obdf/{obdf_id}/events', response_model=EventResultOut)
def ingest_event(obdf_id: int, body: EventIn, db: Session = Depends(get_db),
                 actor: str = Depends(current_actor)):
    svc.get_obdf(db, obdf_id)
    event_uid = body.event_uid or uuid.uuid4()
    existing = db.execute(select(ops.SchemaEvent).filter_by(event_uid=event_uid)).scalar_one_or_none()
    if existing is not None:
        plan = db.execute(select(ops.AdaptationPlan).filter_by(event_id=existing.id)
                          .order_by(ops.AdaptationPlan.id.desc())).scalars().first()
        # uid yang sama dengan isi berbeda menandakan kunci idempotensi bertabrakan; ditandai
        # agar tidak lolos diam-diam sebagai duplikat (temuan F6)
        conflict = existing.structured != body.structured()
        if conflict:
            svc.audit(db, actor, 'event_uid_conflict', obdf_id, 'schema_event', str(existing.id),
                      event_uid=str(event_uid), baru=body.structured())
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
                                message=_pesan_notifikasi(body.source, body.structured(), report)))
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


def _decide(db: Session, plan_id: int, actor: str, approve: bool,
            body: DecisionIn | None) -> PlanOut:
    note = body.note if body else None
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
    pilihan = body is not None and body.property_name is not None
    if pilihan and not approve:
        raise HTTPException(422, 'nama dan domain property hanya diberikan saat menyetujui')
    if pilihan and plan.pattern != 'P-001':
        raise HTTPException(422, 'nama dan domain property hanya berlaku untuk penambahan kolom')
    now = datetime.now(timezone.utc)
    decided = plan
    if pilihan:
        try:
            iri, tindakan = impact_service.revise_add(db, plan, body.property_name.strip(), body.domain)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        usulan = (plan.impact or {}).get('add_options') or {}
        if (iri, body.domain) != (usulan.get('proposed_iri'), usulan.get('proposed_domain')):
            decided = _revisi(db, plan, actor, now, iri, body.domain, tindakan)
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


def _revisi(db: Session, plan: ops.AdaptationPlan, actor: str, now: datetime, iri: str,
            domain: str | None, tindakan: list[dict]) -> ops.AdaptationPlan:
    """Rencana ADD yang direvisi administrator. Baris ops bersifat append-only, sehingga revisi
    menjadi rencana baru (`revision_of`) dan rencana asal berstatus `revised`."""
    impact = dict(plan.impact or {})
    impact['admin_choice'] = {'iri': iri, 'domain': domain}
    revisi = ops.AdaptationPlan(
        event_id=plan.event_id, base_spec_version_id=plan.base_spec_version_id,
        pattern=plan.pattern, decision=plan.decision, status='approved', impact=impact,
        reasons=[*plan.reasons, f"direvisi administrator: property {iri}, domain {domain or '-'}"],
        created_at=now, decided_by=actor, decided_at=now, revision_of=plan.id)
    db.add(revisi)
    db.flush()
    lama = db.execute(select(ops.PlanAction).filter_by(plan_id=plan.id, artifact='vdb')
                      .order_by(ops.PlanAction.seq)).scalars().all()
    baru = [{'artifact': a.artifact, 'operation': a.operation, **a.params} for a in lama] + tindakan
    for seq, action in enumerate(baru, start=1):
        params = {k: v for k, v in action.items() if k not in ('artifact', 'operation')}
        db.add(ops.PlanAction(plan_id=revisi.id, seq=seq, artifact=action['artifact'],
                              operation=action['operation'], params=params))
    plan.status = 'revised'
    plan.decided_by, plan.decided_at = actor, now
    return revisi


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
        raise svc.NotFound(f'notifikasi {notification_id} tidak ditemukan')
    if not notification.acknowledged:
        notification.acknowledged = True
        notification.acknowledged_by = actor
        notification.acknowledged_at = datetime.now(timezone.utc)
        db.flush()
    return notification
