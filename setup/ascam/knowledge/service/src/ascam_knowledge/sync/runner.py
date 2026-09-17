"""
Sync: mengambil spesifikasi OBDF yang sedang berjalan dan menyimpannya sebagai satu versi
spesifikasi baru di Knowledge (P2 pada docs/knowledge/README.md).

Alur:
  1. catat ops.sync_run;
  2. ambil Σ_S (ODBC + get-vdb), isi berkas VDB (read-content, diverifikasi SHA-1), serta
     artefak ℳ dan 𝒯 dari agen Ontop (diverifikasi SHA-256);
  3. hitung sidik jari isi; bila sama dengan versi aktif, TIDAK ada versi baru;
  4. bila berbeda: buat versi `candidate`, isi Σ_S, artefak, triple RDF, dan masalah
     konsistensi, lalu aktifkan (versi lama menjadi `superseded`).
"""
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

from rdflib import BNode, Graph, Literal, URIRef
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..db import ops, registry, spec
from ..security.crypto import SecretBox
from . import sigma
from .clients import OntopAgentClient, TeiidAdminClient, TeiidMetadataClient

ARTIFACT_KINDS = {'r2rml': 'r2rml', 'ontology': 'ontology'}


@dataclass
class SyncResult:
    sync_run_id: int
    changed: bool
    spec_version_id: int | None
    version_no: int | None
    drift_detected: bool
    counts: dict[str, int] = field(default_factory=dict)
    issues: list[dict] = field(default_factory=list)


class SyncError(Exception):
    pass


def default_clients(db: Session, obdf_id: int, box: SecretBox) -> dict:
    """Membangun klien dari target terdaftar (kredensial didekripsi di dalam layanan)."""
    targets = {t.kind: t for t in db.execute(
        select(registry.Target).filter_by(obdf_id=obdf_id, enabled=True)).scalars()}
    missing = {'teiid_mgmt', 'teiid_odbc', 'ontop_agent'} - set(targets)
    if missing:
        raise SyncError(f'target belum terdaftar atau nonaktif: {sorted(missing)}')

    def secret(target):
        cred = db.get(registry.Credential, target.credential_id)
        return cred.username, box.decrypt(cred.secret_ciphertext)

    mgmt_user, mgmt_secret = secret(targets['teiid_mgmt'])
    odbc_user, odbc_secret = secret(targets['teiid_odbc'])
    _, agent_token = secret(targets['ontop_agent'])
    return {
        'admin': TeiidAdminClient(targets['teiid_mgmt'].endpoint, mgmt_user, mgmt_secret),
        'metadata': TeiidMetadataClient(targets['teiid_odbc'].endpoint, odbc_user, odbc_secret),
        'agent': OntopAgentClient(targets['ontop_agent'].endpoint, agent_token),
    }


def _identifier_cases(db: Session, obdf_id: int, models: list[dict]) -> tuple[dict, dict, list[dict]]:
    """Memetakan model Teiid ke sumber terdaftar berdasarkan nama sumber pada VDB."""
    sources = {s.logical_name: s for s in db.execute(
        select(registry.SourceSystem).filter_by(obdf_id=obdf_id)).scalars()}
    cases, source_ids, issues = {}, {}, []
    for model in models:
        matched = None
        for src in model['sources']:
            matched = sources.get(src['source_name']) or sources.get(model['name'])
            if matched:
                break
        if matched:
            cases[model['name']] = matched.identifier_case
            source_ids[model['name']] = matched.id
        elif model['model_type'] == 'physical':
            issues.append({'code': 'source_system_unresolved', 'severity': 'warning',
                           'subject_kind': 'teiid_model', 'subject_ref': model['name'],
                           'message': f"model fisik {model['name']} tidak terpetakan ke sumber terdaftar"})
    return cases, source_ids, issues


def _triples(content: str, artifact_id: int, version_id: int) -> list[spec.RdfTriple]:
    graph = Graph()
    graph.parse(data=content, format='turtle')
    rows = []
    for s, p, o in graph:
        if isinstance(o, Literal):
            kind, datatype, lang = 'literal', (str(o.datatype) if o.datatype else None), o.language
        elif isinstance(o, BNode):
            kind, datatype, lang = 'bnode', None, None
        else:
            kind, datatype, lang = 'iri', None, None
        rows.append(spec.RdfTriple(spec_version_id=version_id, artifact_id=artifact_id,
                                   subject=str(s), predicate=str(p), object=str(o),
                                   object_kind=kind, datatype=datatype, lang=lang))
    return rows


def sync_obdf(db: Session, obdf_id: int, box: SecretBox, actor: str, *,
              trigger: str = 'manual', clients: dict | None = None) -> SyncResult:
    run = ops.SyncRun(obdf_id=obdf_id, trigger=trigger, status='running',
                      started_at=datetime.now(timezone.utc))
    db.add(run)
    db.flush()
    try:
        result = _sync(db, obdf_id, box, actor, run, clients)
    except Exception as exc:                            # noqa: BLE001 — kegagalan dicatat
        run.status = 'failed'
        run.error = {'type': type(exc).__name__, 'message': str(exc)[:1000]}
        run.finished_at = datetime.now(timezone.utc)
        raise
    run.status = 'succeeded'
    run.finished_at = datetime.now(timezone.utc)
    run.produced_spec_version_id = result.spec_version_id if result.changed else None
    run.drift_detected = result.drift_detected
    run.drift_summary = {'changed': result.changed, 'counts': result.counts}
    db.flush()
    result.sync_run_id = run.id
    return result


def _sync(db, obdf_id, box, actor, run, clients) -> SyncResult:
    clients = clients or default_clients(db, obdf_id, box)
    admin, metadata_client, agent = clients['admin'], clients['metadata'], clients['agent']
    issues: list[dict] = []

    metadata = metadata_client.fetch_all()
    vdb_row = (metadata.get('virtual_databases') or [{}])[0]
    vdb_name, vdb_version = vdb_row.get('Name'), str(vdb_row.get('Version') or '1')
    if not vdb_name:
        raise SyncError('VDB tidak teridentifikasi dari SYS.VirtualDatabases')
    vdb_info = admin.get_vdb(vdb_name, vdb_version)

    cases, source_ids, source_issues = _identifier_cases(db, obdf_id, [
        {'name': m.get('model-name'),
         'model_type': (m.get('model-type') or 'PHYSICAL').lower(),
         'sources': [{'source_name': s.get('source-name')} for s in (m.get('source-mappings') or [])]}
        for m in vdb_info.get('models', [])])
    issues += source_issues

    snapshot = sigma.build(metadata, vdb_info, cases)
    for model in snapshot.models:
        for err in model['validity_errors']:
            issues.append({'code': 'vdb_validity_error', 'severity': 'error',
                           'subject_kind': 'teiid_model', 'subject_ref': model['name'],
                           'message': str(err.get('message'))[:1000]})
    for key, view in snapshot.views.items():
        if view.parse_status != 'ok':
            issues.append({'code': 'view_body_unparsed', 'severity': 'warning',
                           'subject_kind': 'teiid_view', 'subject_ref': key,
                           'message': 'definisi view tidak dapat diurai; perubahan kolomnya diperlakukan konservatif'})

    deployment = next((p.get('property-value') for p in (vdb_info.get('properties') or [])
                       if p.get('property-name') == 'deployment-name'), None)
    artifacts: list[dict] = []
    if deployment:
        content = admin.read_deployment_content(deployment)
        reported = admin.deployment_hash(deployment)
        actual = hashlib.sha1(content).hexdigest()       # noqa: S324 — cocokkan format WildFly
        if reported and reported != actual:
            issues.append({'code': 'artifact_hash_mismatch', 'severity': 'error',
                           'subject_kind': 'artifact', 'subject_ref': deployment,
                           'message': f'SHA-1 unduhan {actual} != laporan server {reported}'})
        artifacts.append({'kind': 'vdb_xml', 'name': deployment, 'media_type': 'application/xml',
                          'content': content.decode('utf-8'),
                          'sha256': hashlib.sha256(content).hexdigest()})
    else:
        issues.append({'code': 'artifact_unavailable', 'severity': 'warning',
                       'subject_kind': 'artifact', 'subject_ref': vdb_name,
                       'message': 'nama deployment tidak diketahui; isi berkas VDB tidak diambil'})

    for art in agent.artifacts():
        artifacts.append({'kind': ARTIFACT_KINDS.get(art.kind, art.kind), 'name': art.name,
                          'media_type': art.media_type, 'content': art.content, 'sha256': art.sha256})

    digest = hashlib.sha256(('|'.join([snapshot.digest()] +
                                      [f"{a['kind']}:{a['sha256']}" for a in sorted(artifacts,
                                                                                    key=lambda a: a['kind'])]
                                      )).encode()).hexdigest()

    active = db.execute(select(spec.SpecVersion).filter_by(obdf_id=obdf_id, status='active')
                        ).scalar_one_or_none()
    if active is not None and active.content_digest == digest:
        return SyncResult(sync_run_id=run.id, changed=False, spec_version_id=active.id,
                          version_no=active.version_no, drift_detected=False,
                          counts={'unchanged': 1}, issues=issues)

    next_no = (db.execute(select(func.max(spec.SpecVersion.version_no))
                          .filter_by(obdf_id=obdf_id)).scalar() or 0) + 1
    version = spec.SpecVersion(obdf_id=obdf_id, version_no=next_no,
                               parent_id=active.id if active else None, origin='sync',
                               teiid_vdb_name=snapshot.vdb['name'],
                               teiid_vdb_version=snapshot.vdb['version'],
                               teiid_connection_type=snapshot.vdb.get('connection_type'),
                               content_digest=digest)
    db.add(version)
    db.flush()

    counts = _persist(db, version, snapshot, artifacts, source_ids, issues)
    for issue in issues:
        db.add(spec.ConsistencyIssue(spec_version_id=version.id, detected_by='sync', **issue))
    db.flush()

    if active is not None:
        active.status = 'superseded'
        db.flush()
    version.status = 'active'
    db.flush()

    db.add(ops.AuditLog(actor=actor, action='sync', obdf_id=obdf_id, object_kind='spec_version',
                        object_ref=str(version.version_no),
                        detail={'counts': counts, 'issues': len(issues), 'digest': digest[:12]}))
    return SyncResult(sync_run_id=run.id, changed=True, spec_version_id=version.id,
                      version_no=version.version_no, drift_detected=active is not None,
                      counts=counts, issues=issues)


def _persist(db, version, snapshot, artifacts, source_ids, issues) -> dict[str, int]:
    vid = version.id
    model_ids, table_ids, column_ids = {}, {}, {}

    for model in snapshot.models:
        row = spec.TeiidModel(spec_version_id=vid, name=model['name'],
                              model_type=model['model_type'], visible=model['visible'])
        db.add(row)
        db.flush()
        model_ids[model['name']] = row.id
        for src in model['sources']:
            db.add(spec.TeiidModelSource(spec_version_id=vid, model_id=row.id,
                                         source_name=src['source_name'], translator=src['translator'],
                                         jndi_name=src['jndi_name'],
                                         source_system_id=source_ids.get(model['name'])))

    for table in snapshot.tables:
        row = spec.TeiidTable(spec_version_id=vid, model_id=model_ids[table['model']],
                              uid=table['uid'], name=table['name'], kind=table['kind'],
                              name_in_source=table['name_in_source'],
                              source_schema=table['source_schema'], source_table=table['source_table'])
        db.add(row)
        db.flush()
        table_ids[f"{table['model']}.{table['name']}"] = row.id

    for column in snapshot.columns:
        key = f"{column['model']}.{column['table']}"
        row = spec.TeiidColumn(spec_version_id=vid, table_id=table_ids[key], uid=column['uid'],
                               name=column['name'], name_in_source=column['name_in_source'],
                               source_column=column['source_column'], position=column['position'],
                               data_type=column['data_type'], nullable=column['nullable'],
                               length=column['length'], precision=column['precision'],
                               scale=column['scale'], in_primary_key=column['in_primary_key'])
        db.add(row)
        db.flush()
        column_ids[f"{key}.{column['name']}"] = row.id

    for key, view in snapshot.views.items():
        table_id = table_ids.get(key)
        if table_id is None:
            continue
        db.add(spec.TeiidView(spec_version_id=vid, table_id=table_id, body=view.body,
                              parse_status=view.parse_status, uses_star=view.uses_star))
        for column_name, kind in view.column_kinds.items():
            column_id = column_ids.get(f'{key}.{column_name}')
            if column_id:
                db.add(spec.TeiidViewColumn(spec_version_id=vid, column_id=column_id,
                                            expression_kind=kind))
        if view.uses_star:
            for column_key, column_id in column_ids.items():
                if column_key.rsplit('.', 1)[0] == key and column_key.rsplit('.', 1)[1] not in view.column_kinds:
                    db.add(spec.TeiidViewColumn(spec_version_id=vid, column_id=column_id,
                                                expression_kind='star'))

    for dep in snapshot.dependencies:
        dependent_table = table_ids.get(dep['dependent'])
        used_table = table_ids.get(dep['used'])
        if dependent_table is None or used_table is None:
            continue
        db.add(spec.TeiidDependency(
            spec_version_id=vid, dependent_table_id=dependent_table,
            dependent_column_id=column_ids.get(f"{dep['dependent']}.{dep['dependent_column']}")
            if dep['dependent_column'] else None,
            used_table_id=used_table,
            used_column_id=column_ids.get(f"{dep['used']}.{dep['used_column']}")
            if dep['used_column'] else None,
            derived_role=dep['derived_role']))

    for routine in snapshot.routines:
        db.add(spec.TeiidRoutine(spec_version_id=vid, model_id=model_ids[routine['model']],
                                 kind=routine['kind'], name=routine['name'],
                                 table_name=routine['table_name'], body=routine['body']))

    triple_count = 0
    for art in artifacts:
        row = spec.Artifact(spec_version_id=vid, kind=art['kind'], name=art['name'],
                            media_type=art['media_type'], content=art['content'], sha256=art['sha256'])
        db.add(row)
        db.flush()
        if art['kind'] in ('r2rml', 'ontology'):
            try:
                triples = _triples(art['content'], row.id, vid)
            except Exception as exc:                    # noqa: BLE001 — artefak tak terurai
                issues.append({'code': 'artifact_unparsed', 'severity': 'error',
                               'subject_kind': 'artifact', 'subject_ref': art['kind'],
                               'message': f'{type(exc).__name__}: {str(exc)[:300]}'})
                continue
            db.add_all(triples)
            triple_count += len(triples)
    db.flush()

    return {'models': len(snapshot.models), 'tables': len(snapshot.tables),
            'columns': len(snapshot.columns), 'views': len(snapshot.views),
            'dependencies': len(snapshot.dependencies), 'routines': len(snapshot.routines),
            'artifacts': len(artifacts), 'triples': triple_count, 'issues': len(issues)}
