"""
Analisis dampak dan keputusan adaptasi (matriks D11).

Masukan: event skema terformalisasi (sumber, skema, tabel, kolom, operasi). Keluaran:
kolom Teiid yang terdampak, seluruh pemakaiannya (lineage), dan keputusan beserta alasannya.
Fungsi ini TIDAK mengubah apa pun; ia hanya membaca versi spesifikasi aktif.

Keputusan (ADR-0024):
  auto    rencana aman dan ditentukan penuh oleh DDL; dieksekusi tanpa campur tangan manusia.
  hitl    hanya untuk ADD: nama dan domain property baru adalah keputusan pemodelan, sehingga
          administrator menyetujui (dan boleh merevisi) usulan ASCAM.
  notify  DROP/RENAME yang tidak dapat dibuktikan aman: ASCAM tidak menyusun tindakan, hanya
          memberi tahu administrator elemen OBDF yang terdampak, 𝒟(c, ℱ).

Aturan mengikuti bukti uji kelayakan:
  RENAME  diserap di lapisan Teiid lewat NAMEINSOURCE, sehingga view dan mapping tidak berubah
          (ADR-0002, bukti F0.7 v7 vs v8).
  DROP    otomatis hanya bila seluruh jalur pemakaiannya berupa nilai literal atau proyeksi;
          template IRI, kunci join, klausa SQL, kolom kunci primer, dan jalur yang melewati
          ekspresi view berujung notifikasi (bukti F0.7 v2–v4).
  ADD     selalu menunggu persetujuan; bentrok nama dan domain dijelaskan pada alasan.
"""
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import registry, spec
from .views import plan_view_drops

AUTO_ROLES = {'literal_value', 'projection_only'}
HITL_ROLES = {'iri_template', 'join_key', 'sql_predicate', 'dynamic_predicate'}
HITL_LINKS = {'expression', 'predicate'}
PATTERN = {'add': 'P-001', 'drop': 'P-002', 'rename': 'P-003'}


@dataclass
class TargetColumn:
    column_id: int
    model: str
    table: str
    column: str
    source_column: str | None
    in_primary_key: bool
    usages: list[dict] = field(default_factory=list)


@dataclass
class ImpactReport:
    obdf_id: int
    spec_version_id: int | None
    operation: str
    pattern: str | None
    decision: str                       # auto | hitl | notify | ignored
    reasons: list[str] = field(default_factory=list)
    targets: list[TargetColumn] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    affected: dict[str, list] = field(default_factory=dict)     # 𝒟(c, ℱ) per lapisan
    add_options: dict[str, Any] | None = None                   # pilihan untuk formulir ADD

    def as_dict(self) -> dict[str, Any]:
        out = {
            'obdf_id': self.obdf_id, 'spec_version_id': self.spec_version_id,
            'operation': self.operation, 'pattern': self.pattern, 'decision': self.decision,
            'reasons': self.reasons, 'actions': self.actions, 'affected': self.affected,
            'targets': [{'column_id': t.column_id, 'model': t.model, 'table': t.table,
                         'column': t.column, 'source_column': t.source_column,
                         'in_primary_key': t.in_primary_key, 'usages': t.usages} for t in self.targets],
        }
        if self.add_options is not None:
            out['add_options'] = self.add_options
        return out


def _setting(db: Session, obdf_id: int, key: str, default):
    row = db.get(registry.Setting, (obdf_id, key))
    return row.value if row is not None else default


def active_version(db: Session, obdf_id: int) -> spec.SpecVersion | None:
    return db.execute(select(spec.SpecVersion)
                      .filter_by(obdf_id=obdf_id, status='active')).scalar_one_or_none()


def resolve_columns(db: Session, version_id: int, source_id: int | None, schema: str | None,
                    table: str, column: str | None) -> list[TargetColumn]:
    """Mencari kolom Teiid yang mewakili kolom sumber; satu tabel sumber dapat diekspos
    lebih dari satu model."""
    query = (select(spec.TeiidColumn, spec.TeiidTable, spec.TeiidModel)
             .join(spec.TeiidTable, spec.TeiidTable.id == spec.TeiidColumn.table_id)
             .join(spec.TeiidModel, spec.TeiidModel.id == spec.TeiidTable.model_id)
             .where(spec.TeiidColumn.spec_version_id == version_id,
                    func.lower(spec.TeiidTable.source_table) == table.lower()))
    if schema:
        query = query.where(func.lower(spec.TeiidTable.source_schema) == schema.lower())
    if source_id is not None:
        query = query.join(spec.TeiidModelSource,
                           spec.TeiidModelSource.model_id == spec.TeiidModel.id) \
                     .where(spec.TeiidModelSource.source_system_id == source_id)
    out = []
    for teiid_column, teiid_table, model in db.execute(query).all():
        effective = (teiid_column.source_column or teiid_column.name)
        if column and effective.lower() != column.lower():
            continue
        out.append(TargetColumn(column_id=teiid_column.id, model=model.name, table=teiid_table.name,
                                column=teiid_column.name, source_column=teiid_column.source_column,
                                in_primary_key=teiid_column.in_primary_key))
    return out


def load_usages(db: Session, version_id: int, column_id: int) -> list[dict]:
    rows = db.execute(
        select(spec.ColumnUsage, spec.TriplesMap.iri)
        .join(spec.TriplesMap, spec.TriplesMap.id == spec.ColumnUsage.triples_map_id)
        .where(spec.ColumnUsage.spec_version_id == version_id,
               spec.ColumnUsage.foreign_column_id == column_id)).all()
    return [{'triples_map': iri, 'role': u.role, 'predicate_iri': u.predicate_iri,
             'predicate_entity_id': u.predicate_entity_id, 'weakest_link': u.weakest_link,
             'path': u.path, 'term_map_id': u.term_map_id} for u, iri in rows]


def _predicate_shared(db: Session, version_id: int, predicate_iri: str,
                      column_ids: set[int]) -> bool:
    """Apakah predikat masih dipakai kolom lain (P-002 tidak boleh men-deprecate bila ya)."""
    others = db.execute(
        select(func.count()).select_from(spec.ColumnUsage)
        .where(spec.ColumnUsage.spec_version_id == version_id,
               spec.ColumnUsage.predicate_iri == predicate_iri,
               spec.ColumnUsage.foreign_column_id.not_in(column_ids))).scalar()
    return bool(others)


def _table_flags(db: Session, version_id: int, targets: list[TargetColumn]) -> list[str]:
    reasons = []
    table_names = {t.table for t in targets}
    models = {t.model for t in targets}
    multi_source = db.execute(
        select(spec.TeiidModel.name, func.count())
        .join(spec.TeiidModelSource, spec.TeiidModelSource.model_id == spec.TeiidModel.id)
        .where(spec.TeiidModel.spec_version_id == version_id, spec.TeiidModel.name.in_(models))
        .group_by(spec.TeiidModel.name)).all()
    for model, count in multi_source:
        if count > 1:
            reasons.append(f'model {model} memakai lebih dari satu sumber (multi-source)')
    routines = db.execute(
        select(spec.TeiidRoutine.kind, spec.TeiidRoutine.name)
        .where(spec.TeiidRoutine.spec_version_id == version_id,
               spec.TeiidRoutine.table_name.in_(table_names))).all()
    for kind, name in routines:
        reasons.append(f'tabel dirujuk {kind} {name}')
    unparsed = db.execute(
        select(func.count()).select_from(spec.TriplesMap)
        .where(spec.TriplesMap.spec_version_id == version_id,
               spec.TriplesMap.sql_parse_status == 'failed')).scalar()
    if unparsed:
        reasons.append(f'{unparsed} logical table memiliki SQL yang tidak dapat diurai')
    return reasons


def _explicit_projections(db: Session, version_id: int, column_id: int) -> list[tuple[str, str, str]]:
    """Logical table `rr:sqlQuery` yang memproyeksikan kolom ini secara eksplisit.

    `SELECT *` tidak termasuk, karena ikut menyesuaikan sendiri saat kolom hilang.
    """
    rows = db.execute(
        select(spec.TriplesMap.iri, spec.LogicalColumn.name, spec.TriplesMap.sql_parse_status)
        .join(spec.LogicalColumn, spec.LogicalColumn.triples_map_id == spec.TriplesMap.id)
        .join(spec.LogicalColumnSource,
              spec.LogicalColumnSource.logical_column_id == spec.LogicalColumn.id)
        .where(spec.TriplesMap.spec_version_id == version_id,
               spec.LogicalColumnSource.teiid_column_id == column_id,
               spec.TriplesMap.logical_table_kind == 'sql_query',
               spec.LogicalColumn.expression_kind == 'passthrough')).all()
    return [(iri, nama, status) for iri, nama, status in rows]


def _exposing_triples_maps(db: Session, version_id: int, table: str) -> list[dict]:
    """TriplesMap yang akan otomatis memuat kolom baru pada tabel tersebut.

    Hanya logical table berupa `rr:tableName` atau kueri `SELECT *` yang ikut bertambah kolom;
    logical table dengan daftar kolom eksplisit tidak, sehingga tidak boleh dipilih (bukti:
    pemetaan yang ditempelkan ke TriplesMap salah membuat `ontop validate` menolak artefak).
    """
    rows = db.execute(
        select(spec.TriplesMap.id, spec.TriplesMap.iri, spec.TriplesMap.logical_table_kind)
        .join(spec.LogicalSource, spec.LogicalSource.triples_map_id == spec.TriplesMap.id)
        .join(spec.TeiidTable, spec.TeiidTable.id == spec.LogicalSource.teiid_table_id)
        .where(spec.TriplesMap.spec_version_id == version_id,
               spec.TeiidTable.name == table)).all()
    keluaran = []
    for triples_map_id, iri, kind in rows:
        star = db.execute(
            select(func.count()).select_from(spec.LogicalColumn)
            .where(spec.LogicalColumn.triples_map_id == triples_map_id,
                   spec.LogicalColumn.expression_kind == 'star')).scalar()
        if kind != 'table' and not star:
            continue
        classes = db.execute(select(spec.SubjectClass.class_iri)
                             .filter_by(triples_map_id=triples_map_id)).scalars().all()
        keluaran.append({'id': triples_map_id, 'iri': iri, 'classes': list(classes)})
    return keluaran


def analyze(db: Session, obdf_id: int, event: dict) -> ImpactReport:
    operation = (event.get('operation') or '').lower()
    version = active_version(db, obdf_id)
    report = ImpactReport(obdf_id=obdf_id, spec_version_id=version.id if version else None,
                          operation=operation, pattern=PATTERN.get(operation), decision='notify')
    if version is None:
        report.decision, report.reasons = 'ignored', ['belum ada versi spesifikasi aktif']
        return report
    if operation not in PATTERN:
        report.reasons = [f'operasi {operation!r} tidak didukung']
        return report

    source = db.execute(select(registry.SourceSystem).filter_by(
        obdf_id=obdf_id, logical_name=event.get('source'))).scalar_one_or_none()
    if source is None:
        report.decision = 'ignored'
        report.reasons = [f"sumber {event.get('source')!r} tidak terdaftar"]
        return report

    schema = event.get('schema') or source.default_schema
    lookup_column = None if operation == 'add' else event.get('column')
    targets = resolve_columns(db, version.id, source.id, schema, event.get('table') or '',
                              lookup_column)
    if operation == 'add':
        # ADD: tabelnya harus difederasikan, kolomnya memang belum ada
        table_columns = resolve_columns(db, version.id, source.id, schema, event.get('table') or '', None)
        if not table_columns:
            report.decision = 'ignored'
            report.reasons = [f"tabel {schema}.{event.get('table')} tidak difederasikan"]
            return report
        existing = {c.column.lower() for c in table_columns} | {
            (c.source_column or '').lower() for c in table_columns}
        report.targets = []
        return _decide_add(db, obdf_id, version, event, table_columns, existing, report)

    if not targets:
        report.decision = 'ignored'
        report.reasons = [f"kolom {schema}.{event.get('table')}.{event.get('column')} "
                          f'tidak ditemukan pada spesifikasi aktif']
        return report

    for target in targets:
        target.usages = load_usages(db, version.id, target.column_id)
    report.targets = targets
    report.reasons = _table_flags(db, version.id, targets)
    report.affected = affected_elements(db, version.id, targets)

    if operation == 'rename':
        report = _decide_rename(db, version, event, targets, report)
    else:
        report = _decide_drop(db, version, event, targets, report)
    if report.decision == 'notify':
        # Rencana yang tidak terbukti aman tidak disusun sebagian: administrator hanya diberi tahu
        # elemen yang terdampak dan memperbaikinya sendiri, lalu menjalankan sync (ADR-0024).
        report.actions = []
    return report


def affected_elements(db: Session, version_id: int, targets: list[TargetColumn]) -> dict[str, list]:
    """𝒟(c, ℱ) per lapisan: view Teiid (penutupan transitif atas dependensi), TriplesMap, dan
    property ontologi yang bergantung pada kolom foreign yang berubah."""
    nama_tabel = {tid: (model, nama) for tid, model, nama in db.execute(
        select(spec.TeiidTable.id, spec.TeiidModel.name, spec.TeiidTable.name)
        .join(spec.TeiidModel, spec.TeiidModel.id == spec.TeiidTable.model_id)
        .where(spec.TeiidTable.spec_version_id == version_id)).all()}
    dependensi: dict[int, list[tuple[int, int | None]]] = {}
    for used, dep_table, dep_col in db.execute(
            select(spec.TeiidDependency.used_column_id, spec.TeiidDependency.dependent_table_id,
                   spec.TeiidDependency.dependent_column_id)
            .where(spec.TeiidDependency.spec_version_id == version_id,
                   spec.TeiidDependency.used_column_id.is_not(None))).all():
        dependensi.setdefault(used, []).append((dep_table, dep_col))
    views: set[int] = set()
    antrean, dilihat = [t.column_id for t in targets], set()
    while antrean:
        kolom = antrean.pop()
        if kolom in dilihat:
            continue
        dilihat.add(kolom)
        for dep_table, dep_col in dependensi.get(kolom, []):
            views.add(dep_table)
            if dep_col is not None:
                antrean.append(dep_col)
    triples_maps = sorted({u['triples_map'] for t in targets for u in t.usages})
    predicates = sorted({u['predicate_iri'] for t in targets for u in t.usages if u['predicate_iri']})
    return {
        'columns': [f'{t.model}.{t.table}.{t.column}' for t in targets],
        'views': sorted('.'.join(nama_tabel[v]) for v in views if v in nama_tabel),
        'triples_maps': triples_maps,
        'predicates': predicates,
    }


def _decide_rename(db, version, event, targets, report) -> ImpactReport:
    new_name = event.get('new_column')
    if not new_name:
        report.reasons.append('nama kolom baru tidak disertakan pada event')
        return report
    for target in targets:
        siblings = db.execute(
            select(spec.TeiidColumn.name, spec.TeiidColumn.source_column)
            .join(spec.TeiidTable, spec.TeiidTable.id == spec.TeiidColumn.table_id)
            .where(spec.TeiidColumn.spec_version_id == version.id,
                   spec.TeiidTable.name == target.table,
                   spec.TeiidColumn.id != target.column_id)).all()
        for name, source_column in siblings:
            if (source_column or name).lower() == new_name.lower():
                report.reasons.append(
                    f'nama sumber {new_name} sudah dipakai kolom Teiid {name} pada tabel {target.table}')
        report.actions.append({'artifact': 'vdb', 'operation': 'set_name_in_source',
                               'model': target.model, 'table': target.table,
                               'column': target.column, 'name_in_source': new_name})
    report.decision = 'notify' if report.reasons else 'auto'
    if report.decision == 'auto':
        report.reasons.append('RENAME diserap NAMEINSOURCE; ℳ dan 𝒯 tidak berubah')
    return report


def _decide_drop(db, version, event, targets, report) -> ImpactReport:
    column_ids = {t.column_id for t in targets}
    for target in targets:
        if target.in_primary_key:
            report.reasons.append(f'kolom {target.table}.{target.column} bagian dari kunci primer')
        for usage in target.usages:
            if usage['role'] in HITL_ROLES:
                report.reasons.append(
                    f"dipakai {usage['triples_map'].rsplit('#', 1)[-1]} sebagai {usage['role']}")
            elif usage['weakest_link'] in HITL_LINKS:
                report.reasons.append(
                    f"jalur ke {usage['triples_map'].rsplit('#', 1)[-1]} melewati "
                    f"{usage['weakest_link']}")
        report.actions.append({'artifact': 'vdb', 'operation': 'drop_column',
                               'model': target.model, 'table': target.table, 'column': target.column})

    # View Teiid yang meneruskan kolom ikut disesuaikan (ADR-0023): proyeksinya dikecilkan
    # dengan ALTER VIEW, dirambatkan ke view bertingkat; selain itu diserahkan ke administrator.
    vdb_xml = db.execute(select(spec.Artifact.content).where(
        spec.Artifact.spec_version_id == version.id, spec.Artifact.kind == 'vdb_xml')).scalar()
    view_plan = plan_view_drops(db, version.id, column_ids, vdb_xml)
    report.reasons.extend(view_plan.reasons)
    report.actions.extend(view_plan.actions)

    # Logical table yang menyebut kolom secara eksplisit harus ikut ditulis ulang; tanpa itu
    # kueri masih merujuk kolom yang sudah hilang dan `ontop validate` menolak (temuan A002).
    # Termasuk kolom view yang ikut hilang karena penyesuaian view.
    for column_id in sorted(column_ids | view_plan.view_column_ids):
        for triples_map_iri, nama_logis, status in _explicit_projections(db, version.id,
                                                                        column_id):
            if status == 'failed':
                report.reasons.append(
                    f'logical table {triples_map_iri} memakai SQL yang tidak dapat diurai')
                continue
            report.actions.append({'artifact': 'r2rml', 'operation': 'rewrite_logical_table',
                                   'triples_map_iri': triples_map_iri, 'column': nama_logis})

    predicates: dict[str, set[str]] = {}
    for target in targets:
        for usage in target.usages:
            if usage['role'] == 'literal_value' and usage['predicate_iri']:
                predicates.setdefault(usage['predicate_iri'], set()).add(usage['triples_map'])
    for predicate in sorted(predicates):
        for triples_map in sorted(predicates[predicate]):
            # TriplesMap ditentukan dari lineage, bukan ditebak Executor dari nama tabel
            report.actions.append({'artifact': 'r2rml', 'operation': 'remove_predicate_object_map',
                                   'predicate_iri': predicate, 'triples_map_iri': triples_map})
        if _predicate_shared(db, version.id, predicate, column_ids):
            report.actions.append({'artifact': 'ontology', 'operation': 'keep_property',
                                   'predicate_iri': predicate,
                                   'alasan': 'masih dipakai mapping lain'})
        else:
            report.actions.append({'artifact': 'ontology', 'operation': 'deprecate_property',
                                   'predicate_iri': predicate})
    report.decision = 'notify' if report.reasons else 'auto'
    return report


NAMA_LOKAL = re.compile(r'[A-Za-z_][A-Za-z0-9_-]{0,99}')


def _iri_property(template: str, namespace: str, local: str, column: str | None = None) -> str:
    return (template.replace('{namespace}', namespace)
            .replace('{column_camel}', local)
            .replace('{column}', column if column is not None else local))


def property_problems(db: Session, version_id: int, iri: str, domain: str | None) -> list[str]:
    """Bentrok IRI property baru dengan entitas pada versi aktif.

    OWL 2 QL dan Ontop menolak nama yang dipakai sebagai object property sekaligus data property
    ("name sets are not disjoint"); property data yang sudah ada hanya boleh dipakai ulang bila
    domainnya sama."""
    entity = db.execute(select(spec.OntEntity).where(spec.OntEntity.spec_version_id == version_id,
                                                    spec.OntEntity.iri == iri)).scalars().first()
    if entity is None:
        return []
    if entity.kind != 'datatype_property':
        return [f'IRI {iri} sudah dipakai sebagai {entity.kind}']
    domains = set(db.execute(select(spec.OntDomain.class_expression)
                             .filter_by(entity_id=entity.id)).scalars().all())
    if domains - ({domain} if domain else set()):
        return [f'IRI {iri} sudah ada dengan domain berbeda: {sorted(domains)}']
    return []


def property_actions(options: dict, iri: str, domain: str | None) -> list[dict]:
    """Tindakan 𝒯 dan ℳ untuk property baru. Pemetaan tidak ditempelkan ke TriplesMap yang
    subjeknya berkelas lain selain `domain`, karena reasoner akan menyimpulkan subjek tersebut
    juga anggota `domain`; TriplesMap tanpa rr:class tetap disertakan."""
    column = options['column']
    tms = [tm for tm in options['triples_maps']
           if domain is None or not tm['classes'] or domain in tm['classes']]
    actions = [{'artifact': 'ontology', 'operation': 'add_datatype_property',
                'iri': iri, 'domain': [domain] if domain else [], 'column': column}]
    for tm in tms:
        actions.append({'artifact': 'r2rml', 'operation': 'add_predicate_object_map',
                        'predicate_iri': iri, 'column': column, 'table': options['table'],
                        'triples_map_iri': tm['iri']})
    return actions


def _decide_add(db, obdf_id, version, event, table_columns, existing, report) -> ImpactReport:
    """ADD selalu menunggu persetujuan: ASCAM mengusulkan nama dan domain property dari kebijakan
    penamaan, administrator menyetujui atau merevisinya di konsol (ADR-0021, ADR-0024)."""
    column = (event.get('column') or '').lower()
    target_table = table_columns[0]
    report.decision = 'hitl'
    report.actions.append({'artifact': 'vdb', 'operation': 'add_column',
                           'model': target_table.model, 'table': target_table.table,
                           'column': event.get('column'), 'column_type': event.get('column_type')})
    report.reasons.append('Penambahan kolom menambah property baru ke ontologi; nama dan domainnya '
                          'adalah keputusan pemodelan yang ditetapkan administrator')
    if column in existing:
        report.reasons.append(f'nama kolom {column} sudah ada pada tabel Teiid {target_table.table}')

    exposing = _exposing_triples_maps(db, version.id, target_table.table)
    naming = db.get(registry.NamingPolicy, obdf_id)
    namespace = _setting(db, obdf_id, 'ontology.namespace', None)
    classes = sorted({c for tm in exposing for c in tm['classes']})
    options = {'column': event.get('column'), 'table': target_table.table,
               'namespace': namespace,
               'iri_template': naming.property_iri_template if naming else None,
               'triples_maps': [{'iri': tm['iri'], 'classes': tm['classes']} for tm in exposing],
               'classes': classes, 'proposed_iri': None, 'proposed_domain': None}
    report.add_options = options
    if not exposing:
        report.reasons.append(
            f'tidak ada TriplesMap yang otomatis mengekspos kolom baru pada {target_table.table} '
            '(logical table memakai daftar kolom eksplisit); hanya kolom VDB yang dapat ditambahkan')
        return report
    if naming is None or not namespace:
        report.reasons.append('kebijakan penamaan atau namespace ontologi belum diatur')
        return report

    local = ''.join(part.capitalize() if i else part
                    for i, part in enumerate((event.get('column') or '').split('_')))
    iri = _iri_property(naming.property_iri_template, namespace, local, event.get('column') or '')
    domain = classes[0] if classes else None
    masalah = property_problems(db, version.id, iri, domain)
    if masalah:
        report.reasons.extend(masalah)
        if naming.on_collision == 'qualify_with_class' and domain:
            iri = f"{iri}_{domain.rsplit('/', 1)[-1]}"
            report.reasons.append(f'kebijakan bentrok nama memakai IRI {iri}')
            masalah = property_problems(db, version.id, iri, domain)
        if masalah:
            report.reasons.append('tentukan nama property lain pada formulir persetujuan')
            return report
    if len(classes) > 1:
        report.reasons.append(
            f'TriplesMap yang mengekspos {target_table.table} memberi lebih dari satu kelas '
            f'{classes}; usulan memakai {domain} sebagai domain dan perlu ditinjau')
    options['proposed_iri'], options['proposed_domain'] = iri, domain
    report.actions.extend(property_actions(options, iri, domain))
    return report


def revise_add(db: Session, plan, local_name: str, domain: str | None) -> tuple[str, list[dict]]:
    """Memeriksa pilihan administrator untuk rencana ADD dan menyusun tindakan 𝒯 dan ℳ-nya.

    -> (IRI property, tindakan). ValueError berisi alasan penolakan pilihan."""
    options = (plan.impact or {}).get('add_options')
    if not options or not options.get('triples_maps'):
        raise ValueError('rencana ini tidak dapat diberi property: tidak ada TriplesMap yang '
                         'mengekspos kolom baru, sehingga hanya kolom VDB yang ditambahkan')
    if not options.get('namespace') or not options.get('iri_template'):
        raise ValueError('kebijakan penamaan atau namespace ontologi belum diatur')
    if not NAMA_LOKAL.fullmatch(local_name or ''):
        raise ValueError('nama property harus diawali huruf atau garis bawah dan hanya memuat '
                         'huruf, angka, garis bawah, atau tanda hubung (maksimal 100 karakter)')
    if options['classes'] and domain not in options['classes']:
        raise ValueError(f"domain harus salah satu kelas TriplesMap pengekspos: {options['classes']}")
    if not options['classes'] and domain:
        raise ValueError('TriplesMap pengekspos tidak memberi kelas, sehingga domain tidak dapat dipilih')
    iri = _iri_property(options['iri_template'], options['namespace'], local_name)
    masalah = property_problems(db, plan.base_spec_version_id, iri, domain)
    if masalah:
        raise ValueError('; '.join(masalah))
    return iri, property_actions(options, iri, domain)
