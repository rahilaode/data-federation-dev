from fastapi import APIRouter, Depends, Request
from ..transaction import TransactionalRoute
from sqlalchemy.orm import Session

from ... import registry_service as svc
from ...security.auth import current_actor
from ..deps import get_db
from ..schemas import ApplySummary, ConfigDocument

router = APIRouter(route_class=TransactionalRoute, prefix='/api/v1', tags=['konfigurasi'])


@router.post('/config/apply', response_model=ApplySummary)
def apply_config(doc: ConfigDocument, request: Request, db: Session = Depends(get_db),
                 actor: str = Depends(current_actor)):
    """Menerapkan konfigurasi deklaratif secara idempoten (tidak menghapus objek lain)."""
    return svc.apply_config(db, doc, request.app.state.secret_box, actor)
