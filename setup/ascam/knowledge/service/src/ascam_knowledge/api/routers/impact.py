"""Impact analysis: the endpoint used by the Orchestrator and the impact page of the UI."""
from fastapi import APIRouter, Depends
from ..transaction import TransactionalRoute
from sqlalchemy.orm import Session

from ... import impact as impact_service
from ... import registry_service as svc
from ...security.auth import current_actor
from ..deps import get_db
from ..schemas import ImpactIn, ImpactOut

router = APIRouter(route_class=TransactionalRoute, prefix='/api/v1', tags=['impact'], dependencies=[Depends(current_actor)])


@router.post('/obdf/{obdf_id}/impact', response_model=ImpactOut)
def analyze_impact(obdf_id: int, body: ImpactIn, db: Session = Depends(get_db)):
    """Analyse the impact of one schema change without changing anything (read-only)."""
    svc.get_obdf(db, obdf_id)
    return impact_service.analyze(db, obdf_id, body.model_dump()).as_dict()
