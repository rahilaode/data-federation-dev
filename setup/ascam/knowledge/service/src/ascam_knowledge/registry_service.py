"""
Logika registry yang dipakai bersama oleh API dan penerapan konfigurasi deklaratif.
Setiap perubahan dicatat di ops.audit_log tanpa memuat rahasia.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import owl
from .api import schemas as sc
from .config import read_secret_file
from .db import ops, registry
from .security.crypto import SecretBox


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


def audit(db: Session, actor: str, action: str, obdf_id: int | None,
          object_kind: str | None = None, object_ref: str | None = None, **detail) -> None:
    db.add(ops.AuditLog(actor=actor, action=action, obdf_id=obdf_id, object_kind=object_kind,
                        object_ref=object_ref, detail=detail))


def get_obdf(db: Session, obdf_id: int) -> registry.ObdfInstance:
    obj = db.get(registry.ObdfInstance, obdf_id)
    if obj is None:
        raise NotFound(f'OBDF {obdf_id} tidak ditemukan')
    return obj


def owned(db: Session, model, obj_id: int, obdf_id: int | None = None):
    obj = db.get(model, obj_id)
    if obj is None or (obdf_id is not None and obj.obdf_id != obdf_id):
        raise NotFound(f'{model.__tablename__} {obj_id} tidak ditemukan pada OBDF ini')
    return obj


def resolve_type_mapping(item: sc.TypeMappingIn) -> dict:
    data = item.model_dump()
    computed = owl.owl2ql_compatible(item.xsd_datatype)
    if data['owl2ql_compatible'] is None:
        data['owl2ql_compatible'] = computed
    elif data['owl2ql_compatible'] != computed:
        raise Invalid(f'owl2ql_compatible untuk {item.xsd_datatype} seharusnya {computed}')
    data['native_type'] = data['native_type'].lower()
    return data


def _upsert(db, model, keys: dict, values: dict, label: str, summary: sc.ApplySummary,
            actor: str, obdf_id: int, object_kind: str):
    obj = db.execute(select(model).filter_by(**keys)).scalar_one_or_none()
    if obj is None:
        obj = model(**keys, **values)
        db.add(obj)
        db.flush()
        summary.created.append(label)
        audit(db, actor, 'create', obdf_id, object_kind, label, source='config')
        return obj
    changed = [k for k, v in values.items() if getattr(obj, k) != v]
    if changed:
        for k in changed:
            setattr(obj, k, values[k])
        summary.updated.append(label)
        audit(db, actor, 'update', obdf_id, object_kind, label, fields=changed, source='config')
    else:
        summary.unchanged.append(label)
    return obj


def apply_config(db: Session, doc: sc.ConfigDocument, box: SecretBox, actor: str) -> sc.ApplySummary:
    """Upsert idempoten; tidak menghapus objek yang tidak disebut di dokumen."""
    obdf = db.execute(select(registry.ObdfInstance).filter_by(name=doc.obdf.name)).scalar_one_or_none()
    if obdf is None:
        obdf = registry.ObdfInstance(name=doc.obdf.name, description=doc.obdf.description)
        db.add(obdf)
        db.flush()
        summary = sc.ApplySummary(obdf_id=obdf.id, created=[f'obdf:{obdf.name}'])
        audit(db, actor, 'create', obdf.id, 'obdf', obdf.name, source='config')
    else:
        summary = sc.ApplySummary(obdf_id=obdf.id)
        if obdf.description != doc.obdf.description:
            obdf.description = doc.obdf.description
            summary.updated.append(f'obdf:{obdf.name}')
            audit(db, actor, 'update', obdf.id, 'obdf', obdf.name, fields=['description'], source='config')
        else:
            summary.unchanged.append(f'obdf:{obdf.name}')

    for src in doc.sources:
        _upsert(db, registry.SourceSystem, {'obdf_id': obdf.id, 'logical_name': src.logical_name},
                {'dbms': src.dbms, 'database_name': src.database_name, 'kafka_topic': src.kafka_topic,
                 'identifier_case': src.resolved_case()},
                f'source:{src.logical_name}', summary, actor, obdf.id, 'source')

    credentials: dict[str, registry.Credential] = {}
    for cred in doc.credentials:
        secret = cred.secret.get_secret_value() if cred.secret else read_secret_file(cred.secret_file)
        label = f'credential:{cred.name}'
        obj = db.execute(select(registry.Credential)
                         .filter_by(obdf_id=obdf.id, name=cred.name)).scalar_one_or_none()
        if obj is None:
            ciphertext, version = box.encrypt(secret)
            obj = registry.Credential(obdf_id=obdf.id, name=cred.name, username=cred.username,
                                      secret_ciphertext=ciphertext, key_version=version)
            db.add(obj)
            db.flush()
            summary.created.append(label)
            audit(db, actor, 'create', obdf.id, 'credential', cred.name, source='config')
        else:
            changed = []
            if obj.username != cred.username:
                obj.username = cred.username
                changed.append('username')
            if box.decrypt(obj.secret_ciphertext) != secret:
                obj.secret_ciphertext, obj.key_version = box.encrypt(secret)
                obj.rotated_at = func.now()
                changed.append('secret')
            if changed:
                summary.updated.append(label)
                audit(db, actor, 'update', obdf.id, 'credential', cred.name, fields=changed, source='config')
            else:
                summary.unchanged.append(label)
        credentials[cred.name] = obj

    for tgt in doc.targets:
        cred_id = None
        if tgt.credential:
            cred = credentials.get(tgt.credential) or db.execute(
                select(registry.Credential).filter_by(obdf_id=obdf.id, name=tgt.credential)).scalar_one_or_none()
            if cred is None:
                raise Invalid(f'target {tgt.name}: kredensial {tgt.credential} tidak dikenal')
            cred_id = cred.id
        sc.TargetIn(kind=tgt.kind, name=tgt.name, endpoint=tgt.endpoint,
                    credential_id=cred_id, enabled=tgt.enabled)          # validasi aturan per jenis
        _upsert(db, registry.Target, {'obdf_id': obdf.id, 'name': tgt.name},
                {'kind': tgt.kind, 'endpoint': tgt.endpoint.model_dump(), 'credential_id': cred_id,
                 'enabled': tgt.enabled},
                f'target:{tgt.name}', summary, actor, obdf.id, 'target')

    for key, value in doc.settings.items():
        _upsert(db, registry.Setting, {'obdf_id': obdf.id, 'key': key}, {'value': value},
                f'setting:{key}', summary, actor, obdf.id, 'setting')

    if doc.naming_policy:
        np_ = doc.naming_policy
        if np_.namespace:
            _upsert(db, registry.Setting, {'obdf_id': obdf.id, 'key': 'ontology.namespace'},
                    {'value': np_.namespace}, 'setting:ontology.namespace', summary, actor, obdf.id, 'setting')
        _upsert(db, registry.NamingPolicy, {'obdf_id': obdf.id},
                {'property_iri_template': np_.property_iri_template, 'on_collision': np_.on_collision,
                 'label_language': np_.label_language},
                'naming_policy', summary, actor, obdf.id, 'naming_policy')

    for tm in doc.type_mappings:
        data = resolve_type_mapping(tm)
        _upsert(db, registry.TypeMapping,
                {'obdf_id': obdf.id, 'dbms': data['dbms'], 'native_type': data['native_type']},
                {k: data[k] for k in ('teiid_type', 'xsd_datatype', 'owl2ql_compatible')},
                f"type_mapping:{data['dbms']}:{data['native_type']}", summary, actor, obdf.id, 'type_mapping')

    audit(db, actor, 'apply_config', obdf.id, 'obdf', obdf.name,
          created=len(summary.created), updated=len(summary.updated), unchanged=len(summary.unchanged))
    return summary
