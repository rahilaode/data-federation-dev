"""Endpoint registry: OBDF, sumber, kredensial, target, kebijakan, audit."""
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from ... import registry_service as svc
from ...db import ops, registry, spec
from ..deps import get_db
from ..schemas import (AuditOut, CredentialIn, CredentialOut, NamingPolicyIn, NamingPolicyOut,
                       ObdfIn, ObdfOut, SecretIn, SettingIn, SettingOut, SourceIn, SourceOut,
                       TargetIn, TargetOut, TargetPatch, TypeMappingIn, TypeMappingOut)
from ...security.auth import current_actor

router = APIRouter(prefix='/api/v1', tags=['registry'], dependencies=[Depends(current_actor)])


def _obdf_out(db: Session, obj) -> ObdfOut:
    active = db.execute(select(spec.SpecVersion.version_no).filter_by(
        obdf_id=obj.id, status='active')).scalar_one_or_none()
    return ObdfOut.model_validate(obj).model_copy(update={'active_version_no': active})


# ── OBDF ─────────────────────────────────────────────────────────────────────────
@router.post('/obdf', response_model=ObdfOut, status_code=status.HTTP_201_CREATED)
def create_obdf(body: ObdfIn, db: Session = Depends(get_db), actor: str = Depends(current_actor)):
    obj = registry.ObdfInstance(**body.model_dump())
    db.add(obj)
    db.flush()
    svc.audit(db, actor, 'create', obj.id, 'obdf', obj.name)
    return _obdf_out(db, obj)


@router.get('/obdf', response_model=list[ObdfOut])
def list_obdf(db: Session = Depends(get_db)):
    return [_obdf_out(db, o) for o in db.execute(select(registry.ObdfInstance)
                                                 .order_by(registry.ObdfInstance.name)).scalars()]


@router.get('/obdf/{obdf_id}', response_model=ObdfOut)
def get_obdf(obdf_id: int, db: Session = Depends(get_db)):
    return _obdf_out(db, svc.get_obdf(db, obdf_id))


# ── sumber data ──────────────────────────────────────────────────────────────────
@router.post('/obdf/{obdf_id}/sources', response_model=SourceOut, status_code=201)
def create_source(obdf_id: int, body: SourceIn, db: Session = Depends(get_db),
                  actor: str = Depends(current_actor)):
    svc.get_obdf(db, obdf_id)
    data = body.model_dump()
    data['identifier_case'] = body.resolved_case()
    data['default_schema'] = body.resolved_schema()
    obj = registry.SourceSystem(obdf_id=obdf_id, **data)
    db.add(obj)
    db.flush()
    svc.audit(db, actor, 'create', obdf_id, 'source', obj.logical_name)
    return obj


@router.get('/obdf/{obdf_id}/sources', response_model=list[SourceOut])
def list_sources(obdf_id: int, db: Session = Depends(get_db)):
    svc.get_obdf(db, obdf_id)
    return db.execute(select(registry.SourceSystem).filter_by(obdf_id=obdf_id)
                      .order_by(registry.SourceSystem.logical_name)).scalars().all()


# ── kredensial ───────────────────────────────────────────────────────────────────
@router.post('/obdf/{obdf_id}/credentials', response_model=CredentialOut, status_code=201)
def create_credential(obdf_id: int, body: CredentialIn, request: Request,
                      db: Session = Depends(get_db), actor: str = Depends(current_actor)):
    svc.get_obdf(db, obdf_id)
    ciphertext, version = request.app.state.secret_box.encrypt(body.secret.get_secret_value())
    obj = registry.Credential(obdf_id=obdf_id, name=body.name, username=body.username,
                              secret_ciphertext=ciphertext, key_version=version)
    db.add(obj)
    db.flush()
    svc.audit(db, actor, 'create', obdf_id, 'credential', obj.name)
    return obj


@router.get('/obdf/{obdf_id}/credentials', response_model=list[CredentialOut])
def list_credentials(obdf_id: int, db: Session = Depends(get_db)):
    svc.get_obdf(db, obdf_id)
    return db.execute(select(registry.Credential).filter_by(obdf_id=obdf_id)
                      .order_by(registry.Credential.name)).scalars().all()


@router.put('/credentials/{credential_id}/secret', response_model=CredentialOut)
def rotate_secret(credential_id: int, body: SecretIn, request: Request,
                  db: Session = Depends(get_db), actor: str = Depends(current_actor)):
    obj = svc.owned(db, registry.Credential, credential_id)
    obj.secret_ciphertext, obj.key_version = request.app.state.secret_box.encrypt(
        body.secret.get_secret_value())
    obj.rotated_at = func.now()
    db.flush()
    db.refresh(obj)
    svc.audit(db, actor, 'rotate_secret', obj.obdf_id, 'credential', obj.name)
    return obj


# ── target ───────────────────────────────────────────────────────────────────────
def _check_credential(db: Session, obdf_id: int, credential_id: int | None):
    if credential_id is not None:
        svc.owned(db, registry.Credential, credential_id, obdf_id)


@router.post('/obdf/{obdf_id}/targets', response_model=TargetOut, status_code=201)
def create_target(obdf_id: int, body: TargetIn, db: Session = Depends(get_db),
                  actor: str = Depends(current_actor)):
    svc.get_obdf(db, obdf_id)
    _check_credential(db, obdf_id, body.credential_id)
    obj = registry.Target(obdf_id=obdf_id, kind=body.kind, name=body.name,
                          endpoint=body.endpoint.model_dump(), credential_id=body.credential_id,
                          enabled=body.enabled)
    db.add(obj)
    db.flush()
    db.refresh(obj)
    svc.audit(db, actor, 'create', obdf_id, 'target', obj.name, kind=obj.kind)
    return obj


@router.get('/obdf/{obdf_id}/targets', response_model=list[TargetOut])
def list_targets(obdf_id: int, db: Session = Depends(get_db)):
    svc.get_obdf(db, obdf_id)
    return db.execute(select(registry.Target).filter_by(obdf_id=obdf_id)
                      .order_by(registry.Target.name)).scalars().all()


@router.patch('/targets/{target_id}', response_model=TargetOut)
def patch_target(target_id: int, body: TargetPatch, db: Session = Depends(get_db),
                 actor: str = Depends(current_actor)):
    obj = svc.owned(db, registry.Target, target_id)
    changes = body.model_dump(exclude_unset=True)
    if 'credential_id' in changes:
        _check_credential(db, obj.obdf_id, changes['credential_id'])
    merged = TargetIn(kind=obj.kind, name=obj.name,
                      endpoint=changes.get('endpoint', obj.endpoint),
                      credential_id=changes.get('credential_id', obj.credential_id),
                      enabled=changes.get('enabled', obj.enabled))      # validasi ulang aturan per jenis
    obj.endpoint = merged.endpoint.model_dump()
    obj.credential_id = merged.credential_id
    obj.enabled = merged.enabled
    db.flush()
    db.refresh(obj)
    svc.audit(db, actor, 'update', obj.obdf_id, 'target', obj.name, fields=sorted(changes))
    return obj


# ── pengaturan dan kebijakan ─────────────────────────────────────────────────────
@router.get('/obdf/{obdf_id}/settings', response_model=list[SettingOut])
def list_settings(obdf_id: int, db: Session = Depends(get_db)):
    svc.get_obdf(db, obdf_id)
    return db.execute(select(registry.Setting).filter_by(obdf_id=obdf_id)
                      .order_by(registry.Setting.key)).scalars().all()


@router.put('/obdf/{obdf_id}/settings/{key}', response_model=SettingOut)
def put_setting(obdf_id: int, key: str, body: SettingIn, db: Session = Depends(get_db),
                actor: str = Depends(current_actor)):
    svc.get_obdf(db, obdf_id)
    obj = db.get(registry.Setting, (obdf_id, key))
    if obj is None:
        obj = registry.Setting(obdf_id=obdf_id, key=key, value=body.value)
        db.add(obj)
    else:
        obj.value = body.value
    db.flush()
    db.refresh(obj)
    svc.audit(db, actor, 'put', obdf_id, 'setting', key)
    return obj


@router.get('/obdf/{obdf_id}/naming-policy', response_model=NamingPolicyOut)
def get_naming_policy(obdf_id: int, db: Session = Depends(get_db)):
    svc.get_obdf(db, obdf_id)
    obj = db.get(registry.NamingPolicy, obdf_id)
    if obj is None:
        raise svc.NotFound('kebijakan penamaan belum diatur')
    return obj


@router.put('/obdf/{obdf_id}/naming-policy', response_model=NamingPolicyOut)
def put_naming_policy(obdf_id: int, body: NamingPolicyIn, db: Session = Depends(get_db),
                      actor: str = Depends(current_actor)):
    svc.get_obdf(db, obdf_id)
    data = body.model_dump(exclude={'namespace'})
    obj = db.get(registry.NamingPolicy, obdf_id)
    if obj is None:
        obj = registry.NamingPolicy(obdf_id=obdf_id, **data)
        db.add(obj)
    else:
        for k, v in data.items():
            setattr(obj, k, v)
    if body.namespace:
        setting = db.get(registry.Setting, (obdf_id, 'ontology.namespace'))
        if setting is None:
            db.add(registry.Setting(obdf_id=obdf_id, key='ontology.namespace', value=body.namespace))
        else:
            setting.value = body.namespace
    db.flush()
    db.refresh(obj)
    svc.audit(db, actor, 'put', obdf_id, 'naming_policy', None)
    return obj


@router.get('/obdf/{obdf_id}/type-mappings', response_model=list[TypeMappingOut])
def list_type_mappings(obdf_id: int, db: Session = Depends(get_db)):
    svc.get_obdf(db, obdf_id)
    return db.execute(select(registry.TypeMapping).filter_by(obdf_id=obdf_id)
                      .order_by(registry.TypeMapping.dbms, registry.TypeMapping.native_type)).scalars().all()


@router.put('/obdf/{obdf_id}/type-mappings', response_model=list[TypeMappingOut])
def replace_type_mappings(obdf_id: int, body: list[TypeMappingIn], db: Session = Depends(get_db),
                          actor: str = Depends(current_actor)):
    """Mengganti seluruh pemetaan tipe OBDF (operasi atomik)."""
    svc.get_obdf(db, obdf_id)
    rows = [svc.resolve_type_mapping(item) for item in body]
    db.execute(delete(registry.TypeMapping).where(registry.TypeMapping.obdf_id == obdf_id))
    db.add_all(registry.TypeMapping(obdf_id=obdf_id, **r) for r in rows)
    db.flush()
    svc.audit(db, actor, 'replace', obdf_id, 'type_mapping', None, count=len(rows))
    return list_type_mappings(obdf_id, db)


# ── audit ────────────────────────────────────────────────────────────────────────
@router.get('/obdf/{obdf_id}/audit', response_model=list[AuditOut])
def list_audit(obdf_id: int, limit: int = 50, db: Session = Depends(get_db)):
    svc.get_obdf(db, obdf_id)
    limit = max(1, min(limit, 500))
    return db.execute(select(ops.AuditLog).filter_by(obdf_id=obdf_id)
                      .order_by(ops.AuditLog.id.desc()).limit(limit)).scalars().all()
