"""
Analisis dampak dan keputusan adaptasi (matriks D11).

Masukan: event skema terformalisasi (sumber, skema, tabel, kolom, operasi). Keluaran:
kolom Teiid yang terdampak, seluruh pemakaiannya (lineage), dan keputusan `auto` atau `hitl`
beserta alasannya. Fungsi ini TIDAK mengubah apa pun; ia hanya membaca versi spesifikasi aktif.

Aturan mengikuti bukti uji kelayakan:
  RENAME  diserap di lapisan Teiid lewat NAMEINSOURCE, sehingga view dan mapping tidak berubah
          (ADR-0002, bukti F0.7 v7 vs v8).
  DROP    otomatis hanya bila seluruh jalur pemakaiannya berupa nilai literal atau proyeksi;
          template IRI, kunci join, klausa SQL, kolom kunci primer, dan jalur yang melewati
          ekspresi view memerlukan HITL (bukti F0.7 v2–v4).
  ADD     otomatis sesuai kebijakan; bentrok nama kolom Teiid dan bentrok domain property
          dieskalasi.
"""
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import registry, spec

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
    decision: str                       # auto | hitl | ignored
    reasons: list[str] = field(default_factory=list)
    targets: list[TargetColumn] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            'obdf_id': self.obdf_id, 'spec_version_id': self.spec_version_id,
            'operation': self.operation, 'pattern': self.pattern, 'decision': self.decision,
            'reasons': self.reasons, 'actions': self.actions,
            'targets': [{'column_id': t.column_id, 'model': t.model, 'table': t.table,
                         'column': t.column, 'source_column': t.source_column,
                         'in_primary_key': t.in_primary_key, 'usages': t.usages} for t in self.targets],
        }


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


def analyze(db: Session, obdf_id: int, event: dict) -> ImpactReport:
    operation = (event.get('operation') or '').lower()
    version = active_version(db, obdf_id)
    report = ImpactReport(obdf_id=obdf_id, spec_version_id=version.id if version else None,
                          operation=operation, pattern=PATTERN.get(operation), decision='hitl')
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

    if operation == 'rename':
        return _decide_rename(db, version, event, targets, report)
    return _decide_drop(db, version, event, targets, report)


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
    report.decision = 'hitl' if report.reasons else 'auto'
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

    predicates = {u['predicate_iri'] for t in targets for u in t.usages
                  if u['role'] == 'literal_value' and u['predicate_iri']}
    for predicate in sorted(predicates):
        report.actions.append({'artifact': 'r2rml', 'operation': 'remove_predicate_object_map',
                               'predicate_iri': predicate})
        if _predicate_shared(db, version.id, predicate, column_ids):
            report.actions.append({'artifact': 'ontology', 'operation': 'keep_property',
                                   'predicate_iri': predicate,
                                   'alasan': 'masih dipakai mapping lain'})
        else:
            report.actions.append({'artifact': 'ontology', 'operation': 'deprecate_property',
                                   'predicate_iri': predicate})
    report.decision = 'hitl' if report.reasons else 'auto'
    return report


def _decide_add(db, obdf_id, version, event, table_columns, existing, report) -> ImpactReport:
    column = (event.get('column') or '').lower()
    policy = _setting(db, obdf_id, 'adaptation.add_column', {'mode': 'auto'}) or {}
    target_table = table_columns[0]
    report.actions.append({'artifact': 'vdb', 'operation': 'add_column',
                           'model': target_table.model, 'table': target_table.table,
                           'column': event.get('column'), 'column_type': event.get('column_type')})
    if column in existing:
        report.reasons.append(f'nama kolom {column} sudah ada pada tabel Teiid {target_table.table}')
    if policy.get('mode') != 'auto':
        report.reasons.append('kebijakan adaptation.add_column bukan auto')
        report.decision = 'hitl'
        return report

    naming = db.get(registry.NamingPolicy, obdf_id)
    namespace = _setting(db, obdf_id, 'ontology.namespace', None)
    if naming is None or not namespace:
        report.reasons.append('kebijakan penamaan atau namespace ontologi belum diatur')
        report.decision = 'hitl'
        return report

    local = ''.join(part.capitalize() if i else part
                    for i, part in enumerate((event.get('column') or '').split('_')))
    iri = (naming.property_iri_template
           .replace('{namespace}', namespace)
           .replace('{column_camel}', local)
           .replace('{column}', event.get('column') or ''))
    existing_entity = db.execute(
        select(spec.OntEntity).where(spec.OntEntity.spec_version_id == version.id,
                                     spec.OntEntity.iri == iri)).scalars().first()
    classes = db.execute(
        select(spec.SubjectClass.class_iri).join(
            spec.TriplesMap, spec.TriplesMap.id == spec.SubjectClass.triples_map_id)
        .join(spec.LogicalSource, spec.LogicalSource.triples_map_id == spec.TriplesMap.id)
        .join(spec.TeiidTable, spec.TeiidTable.id == spec.LogicalSource.teiid_table_id)
        .where(spec.TriplesMap.spec_version_id == version.id,
               spec.TeiidTable.name == target_table.table)).scalars().all()
    if existing_entity is not None:
        domains = db.execute(select(spec.OntDomain.class_expression)
                             .filter_by(entity_id=existing_entity.id)).scalars().all()
        if set(domains) - set(classes):
            report.reasons.append(f'IRI {iri} sudah ada dengan domain berbeda: {sorted(set(domains))}')
            if naming.on_collision == 'qualify_with_class' and classes:
                iri = f"{iri}_{classes[0].rsplit('/', 1)[-1]}"
                report.reasons.append(f'kebijakan bentrok nama memakai IRI {iri}')
            else:
                report.decision = 'hitl'
                return report
    report.actions.append({'artifact': 'ontology', 'operation': 'add_datatype_property',
                           'iri': iri, 'domain': classes[:1], 'column': event.get('column')})
    report.actions.append({'artifact': 'r2rml', 'operation': 'add_predicate_object_map',
                           'predicate_iri': iri, 'column': event.get('column')})
    report.decision = 'hitl' if any('sudah ada pada tabel Teiid' in r for r in report.reasons) else 'auto'
    return report
