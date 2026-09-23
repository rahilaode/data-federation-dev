"""Uji koneksi target dan status terakhir (dasbor UI)."""
from fastapi import APIRouter, Depends, Request
from ..transaction import TransactionalRoute
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import registry_service as svc
from ...check_service import Hooks, run_check
from ...db import registry
from ...security.auth import current_actor
from ..deps import get_db
from ..schemas import CheckOut, TargetOut, TargetStatusOut

router = APIRouter(route_class=TransactionalRoute, prefix='/api/v1', tags=['uji koneksi'], dependencies=[Depends(current_actor)])


def _hooks(request: Request) -> type[Hooks]:
    return getattr(request.app.state, 'check_hooks', Hooks)


@router.post('/targets/{target_id}/check', response_model=CheckOut)
def check_target(target_id: int, request: Request, db: Session = Depends(get_db),
                 actor: str = Depends(current_actor)):
    target = svc.owned(db, registry.Target, target_id)
    row = run_check(db, target, request.app.state.secret_box, actor, _hooks(request))
    svc.audit(db, actor, 'check', target.obdf_id, 'target', target.name, ok=row.ok)
    return row


@router.post('/obdf/{obdf_id}/checks', response_model=list[CheckOut])
def check_all(obdf_id: int, request: Request, db: Session = Depends(get_db),
              actor: str = Depends(current_actor)):
    """Menguji semua target yang aktif pada OBDF."""
    svc.get_obdf(db, obdf_id)
    targets = db.execute(select(registry.Target).filter_by(obdf_id=obdf_id, enabled=True)
                         .order_by(registry.Target.name)).scalars().all()
    rows = [run_check(db, t, request.app.state.secret_box, actor, _hooks(request)) for t in targets]
    svc.audit(db, actor, 'check_all', obdf_id, 'obdf', None,
              ok=sum(r.ok for r in rows), failed=sum(not r.ok for r in rows))
    return rows


@router.get('/targets/{target_id}/checks', response_model=list[CheckOut])
def check_history(target_id: int, limit: int = 20, db: Session = Depends(get_db)):
    svc.owned(db, registry.Target, target_id)
    return db.execute(select(registry.ConnectionCheck).filter_by(target_id=target_id)
                      .order_by(registry.ConnectionCheck.checked_at.desc(), registry.ConnectionCheck.id.desc())
                      .limit(max(1, min(limit, 200)))).scalars().all()


@router.get('/obdf/{obdf_id}/status', response_model=list[TargetStatusOut])
def status(obdf_id: int, db: Session = Depends(get_db)):
    """Status terkini setiap target (hasil uji terakhir)."""
    svc.get_obdf(db, obdf_id)
    out = []
    for t in db.execute(select(registry.Target).filter_by(obdf_id=obdf_id)
                        .order_by(registry.Target.name)).scalars():
        last = db.execute(select(registry.ConnectionCheck).filter_by(target_id=t.id)
                          .order_by(registry.ConnectionCheck.checked_at.desc(), registry.ConnectionCheck.id.desc())
                          .limit(1)).scalar_one_or_none()
        out.append(TargetStatusOut(target=TargetOut.model_validate(t),
                                   last_check=CheckOut.model_validate(last) if last else None))
    return out
