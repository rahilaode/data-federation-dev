"""Endpoint sync dan penjelajahan versi spesifikasi."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from ..transaction import TransactionalRoute
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ... import registry_service as svc
from ...db import ops, spec
from ...security.auth import current_actor
from ...sync import sync_obdf
from ..deps import get_db
from ..schemas import ArtifactOut, SpecVersionOut, SyncResultOut, SyncRunOut, VersionDetailOut

router = APIRouter(route_class=TransactionalRoute, prefix='/api/v1', tags=['sync'], dependencies=[Depends(current_actor)])

CONTENT_TABLES = {
    'teiid_model': spec.TeiidModel, 'teiid_table': spec.TeiidTable, 'teiid_column': spec.TeiidColumn,
    'teiid_view': spec.TeiidView, 'teiid_dependency': spec.TeiidDependency,
    'teiid_routine': spec.TeiidRoutine, 'artifact': spec.Artifact, 'rdf_triple': spec.RdfTriple,
    'consistency_issue': spec.ConsistencyIssue,
}


@router.post('/obdf/{obdf_id}/sync', response_model=SyncResultOut)
def run_sync(obdf_id: int, request: Request, db: Session = Depends(get_db),
             actor: str = Depends(current_actor)):
    """Mengambil spesifikasi OBDF yang berjalan dan menyimpannya sebagai versi baru bila berubah."""
    svc.get_obdf(db, obdf_id)
    clients = getattr(request.app.state, 'sync_clients', None)
    try:
        result = sync_obdf(db, obdf_id, request.app.state.secret_box, actor,
                           trigger='manual', clients=clients(db, obdf_id) if clients else None)
    except Exception as exc:                            # noqa: BLE001
        # transaksi permintaan di-rollback; catatan kegagalan ditulis dan di-commit terpisah
        # agar jejak sync yang gagal tidak ikut hilang.
        db.rollback()
        db.add(ops.SyncRun(obdf_id=obdf_id, trigger='manual', status='failed',
                           started_at=datetime.now(timezone.utc),
                           finished_at=datetime.now(timezone.utc),
                           error={'type': type(exc).__name__, 'message': str(exc)[:1000]}))
        db.add(ops.AuditLog(actor=actor, action='sync_failed', obdf_id=obdf_id,
                            object_kind='obdf', object_ref=str(obdf_id),
                            detail={'error': type(exc).__name__}))
        db.commit()
        raise HTTPException(502, f'sync gagal: {type(exc).__name__}: {str(exc)[:300]}')
    return SyncResultOut(**result.__dict__)


@router.get('/obdf/{obdf_id}/versions', response_model=list[SpecVersionOut])
def list_versions(obdf_id: int, limit: int = 20, db: Session = Depends(get_db)):
    svc.get_obdf(db, obdf_id)
    return db.execute(select(spec.SpecVersion).filter_by(obdf_id=obdf_id)
                      .order_by(spec.SpecVersion.version_no.desc())
                      .limit(max(1, min(limit, 200)))).scalars().all()


@router.get('/versions/{version_id}', response_model=VersionDetailOut)
def version_detail(version_id: int, db: Session = Depends(get_db)):
    version = db.get(spec.SpecVersion, version_id)
    if version is None:
        raise svc.NotFound(f'versi spesifikasi {version_id} tidak ditemukan')
    counts = {name: db.execute(select(func.count()).select_from(model)
                               .filter_by(spec_version_id=version_id)).scalar()
              for name, model in CONTENT_TABLES.items()}
    issues = db.execute(select(spec.ConsistencyIssue).filter_by(spec_version_id=version_id)
                        .order_by(spec.ConsistencyIssue.severity)).scalars().all()
    artifacts = db.execute(select(spec.Artifact).filter_by(spec_version_id=version_id)
                           .order_by(spec.Artifact.kind)).scalars().all()
    return VersionDetailOut(version=SpecVersionOut.model_validate(version), counts=counts,
                            artifacts=[ArtifactOut.model_validate(a) for a in artifacts],
                            issues=[{'code': i.code, 'severity': i.severity, 'subject_kind': i.subject_kind,
                                     'subject_ref': i.subject_ref, 'message': i.message} for i in issues])


@router.get('/versions/{version_id}/artifacts/{kind}')
def version_artifact(version_id: int, kind: str, db: Session = Depends(get_db)):
    row = db.execute(select(spec.Artifact).filter_by(spec_version_id=version_id, kind=kind)
                     ).scalar_one_or_none()
    if row is None:
        raise svc.NotFound(f'artefak {kind} tidak ada pada versi {version_id}')
    return {'kind': row.kind, 'name': row.name, 'media_type': row.media_type,
            'sha256': row.sha256, 'content': row.content}


@router.get('/obdf/{obdf_id}/sync-runs', response_model=list[SyncRunOut])
def sync_runs(obdf_id: int, limit: int = 20, db: Session = Depends(get_db)):
    svc.get_obdf(db, obdf_id)
    return db.execute(select(ops.SyncRun).filter_by(obdf_id=obdf_id)
                      .order_by(ops.SyncRun.id.desc()).limit(max(1, min(limit, 200)))).scalars().all()
