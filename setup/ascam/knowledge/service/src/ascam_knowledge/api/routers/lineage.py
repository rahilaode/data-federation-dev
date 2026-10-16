"""Lineage kolom ℱ untuk konsol: kolom sumber sampai property ontologi (ADR-0024). Baca saja."""
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
    """Graf lineage versi aktif (atau `version_id`), opsional disaring per sumber, tabel, dan
    kolom DI SUMBER (nama yang sama dengan isi event skema)."""
    svc.get_obdf(db, obdf_id)
    if version_id is None:
        version = impact_service.active_version(db, obdf_id)
        if version is None:
            raise svc.NotFound('belum ada versi spesifikasi aktif')
    else:
        version = db.get(spec.SpecVersion, version_id)
        if version is None or version.obdf_id != obdf_id:
            raise svc.NotFound(f'versi spesifikasi {version_id} tidak ditemukan')
    return lineage_view.build(db, obdf_id, version, source, table, column)
