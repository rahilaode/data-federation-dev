"""Column lineage of ℱ for the console: source column to ontology property (ADR-0024). Read-only."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ... import impact as impact_service
from ... import lineage_view
from ... import registry_service as svc
from ...db import spec
from ...security.auth import current_actor
from ..deps import get_db
from ..transaction import TransactionalRoute

router = APIRouter(route_class=TransactionalRoute, prefix='/api/v1', tags=['lineage'],
                   dependencies=[Depends(current_actor)])


@router.get('/obdf/{obdf_id}/lineage')
def column_lineage(obdf_id: int, version_id: int | None = None, source: str | None = None,
                   table: str | None = None, column: str | None = None,
                   db: Session = Depends(get_db)):
    """Lineage graph of the active version (or `version_id`), optionally filtered by source, table,
    and column IN THE SOURCE (the same names as in schema events)."""
    svc.get_obdf(db, obdf_id)
    if version_id is None:
        version = impact_service.active_version(db, obdf_id)
        if version is None:
            raise svc.NotFound('no active specification version yet')
    else:
        version = db.get(spec.SpecVersion, version_id)
        if version is None or version.obdf_id != obdf_id:
            raise svc.NotFound(f'specification version {version_id} not found')
    return lineage_view.build(db, obdf_id, version, source, table, column)
