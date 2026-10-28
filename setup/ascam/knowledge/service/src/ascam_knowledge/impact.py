"""
Impact analysis and adaptation decision (decision matrix D11).

Input: a formalised schema event (source, schema, table, column, operation). Output: the
affected Teiid columns, all their usages (lineage), and the decision with its reasons.
Nothing is modified here; the function only reads the active specification version.

Decisions (ADR-0024):
  auto    the plan is safe and fully determined by the DDL; it runs without human involvement.
  hitl    ADD only: the name and domain of the new property are a modelling decision, so the
          administrator approves (and may revise) the proposal made by ASCAM.
  notify  a DROP or RENAME that cannot be shown to be safe: ASCAM builds no actions and only
          tells the administrator which OBDF elements are affected, 𝒟(c, ℱ).

The rules follow the feasibility experiments:
  RENAME  absorbed in the Teiid layer through NAMEINSOURCE, so views and mappings stay as they
          are (ADR-0002, experiment F0.7 v7 vs v8).
  DROP    automatic only when every usage path is a literal value or a projection; IRI
          templates, join keys, SQL clauses, primary-key columns, and paths through a view
          expression lead to a notification (experiment F0.7 v2-v4).
  ADD     always waits for approval; name and domain collisions are stated in the reasons.
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
    affected: dict[str, list] = field(default_factory=dict)     # 𝒟(c, ℱ) per layer
    add_options: dict[str, Any] | None = None                   # choices for the ADD form

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
    """Find the Teiid columns that represent a source column; one source table can be
    exposed by more than one model."""
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
    """Whether another column still uses the predicate (P-002 must not deprecate it then)."""
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
            reasons.append(f'model {model} uses more than one source (multi-source)')
    routines = db.execute(
        select(spec.TeiidRoutine.kind, spec.TeiidRoutine.name)
        .where(spec.TeiidRoutine.spec_version_id == version_id,
               spec.TeiidRoutine.table_name.in_(table_names))).all()
    for kind, name in routines:
        reasons.append(f'table is referenced by {kind} {name}')
    unparsed = db.execute(
        select(func.count()).select_from(spec.TriplesMap)
        .where(spec.TriplesMap.spec_version_id == version_id,
               spec.TriplesMap.sql_parse_status == 'failed')).scalar()
    if unparsed:
        reasons.append(f'{unparsed} logical tables have SQL that cannot be parsed')
    return reasons


def _explicit_projections(db: Session, version_id: int, column_id: int) -> list[tuple[str, str, str]]:
    """Logical tables given as `rr:sqlQuery` that project this column explicitly.

    `SELECT *` is not included, because it adjusts by itself when the column disappears.
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
    return [(iri, column_name, status) for iri, column_name, status in rows]


def _exposing_triples_maps(db: Session, version_id: int, table: str) -> list[dict]:
    """TriplesMaps that will expose a new column of the table without being edited.

    Only logical tables given as `rr:tableName` or as a `SELECT *` query gain the column;
    logical tables with an explicit column list do not, so they must not be chosen (a mapping
    attached to the wrong TriplesMap makes `ontop validate` reject the artifacts).
    """
    rows = db.execute(
        select(spec.TriplesMap.id, spec.TriplesMap.iri, spec.TriplesMap.logical_table_kind)
        .join(spec.LogicalSource, spec.LogicalSource.triples_map_id == spec.TriplesMap.id)
        .join(spec.TeiidTable, spec.TeiidTable.id == spec.LogicalSource.teiid_table_id)
        .where(spec.TriplesMap.spec_version_id == version_id,
               spec.TeiidTable.name == table)).all()
    found = []
    for triples_map_id, iri, kind in rows:
        star = db.execute(
            select(func.count()).select_from(spec.LogicalColumn)
            .where(spec.LogicalColumn.triples_map_id == triples_map_id,
                   spec.LogicalColumn.expression_kind == 'star')).scalar()
        if kind != 'table' and not star:
            continue
        classes = db.execute(select(spec.SubjectClass.class_iri)
                             .filter_by(triples_map_id=triples_map_id)).scalars().all()
        found.append({'id': triples_map_id, 'iri': iri, 'classes': list(classes)})
    return found


def analyze(db: Session, obdf_id: int, event: dict) -> ImpactReport:
    operation = (event.get('operation') or '').lower()
    version = active_version(db, obdf_id)
    report = ImpactReport(obdf_id=obdf_id, spec_version_id=version.id if version else None,
                          operation=operation, pattern=PATTERN.get(operation), decision='notify')
    if version is None:
        report.decision, report.reasons = 'ignored', ['no active specification version yet']
        return report
    if operation not in PATTERN:
        report.reasons = [f'operation {operation!r} is not supported']
        return report

    source = db.execute(select(registry.SourceSystem).filter_by(
        obdf_id=obdf_id, logical_name=event.get('source'))).scalar_one_or_none()
    if source is None:
        report.decision = 'ignored'
        report.reasons = [f"source {event.get('source')!r} is not registered"]
        return report

    schema = event.get('schema') or source.default_schema
    lookup_column = None if operation == 'add' else event.get('column')
    targets = resolve_columns(db, version.id, source.id, schema, event.get('table') or '',
                              lookup_column)
    if operation == 'add':
        # ADD: the table must be federated and the column must not exist yet
        table_columns = resolve_columns(db, version.id, source.id, schema, event.get('table') or '', None)
        if not table_columns:
            report.decision = 'ignored'
            report.reasons = [f"table {schema}.{event.get('table')} is not federated"]
            return report
        existing = {c.column.lower() for c in table_columns} | {
            (c.source_column or '').lower() for c in table_columns}
        report.targets = []
        return _decide_add(db, obdf_id, version, event, table_columns, existing, report)

    if not targets:
        report.decision = 'ignored'
        report.reasons = [f"column {schema}.{event.get('table')}.{event.get('column')} "
                          f'not found in the active specification']
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
        # A plan that is not shown to be safe is not built in part: the administrator is only
        # told which elements are affected, repairs them, and then runs a sync (ADR-0024).
        report.actions = []
    return report


def affected_elements(db: Session, version_id: int, targets: list[TargetColumn]) -> dict[str, list]:
    """𝒟(c, ℱ) per layer: Teiid views (transitive closure over the dependencies), TriplesMaps,
    and ontology properties that depend on the changed foreign column."""
    table_labels = {tid: (model, name) for tid, model, name in db.execute(
        select(spec.TeiidTable.id, spec.TeiidModel.name, spec.TeiidTable.name)
        .join(spec.TeiidModel, spec.TeiidModel.id == spec.TeiidTable.model_id)
        .where(spec.TeiidTable.spec_version_id == version_id)).all()}
    dependents: dict[int, list[tuple[int, int | None]]] = {}
    for used, dep_table, dep_col in db.execute(
            select(spec.TeiidDependency.used_column_id, spec.TeiidDependency.dependent_table_id,
                   spec.TeiidDependency.dependent_column_id)
            .where(spec.TeiidDependency.spec_version_id == version_id,
                   spec.TeiidDependency.used_column_id.is_not(None))).all():
        dependents.setdefault(used, []).append((dep_table, dep_col))
    views: set[int] = set()
    queue, seen = [t.column_id for t in targets], set()
    while queue:
        current = queue.pop()
        if current in seen:
            continue
        seen.add(current)
        for dep_table, dep_col in dependents.get(current, []):
            views.add(dep_table)
            if dep_col is not None:
                queue.append(dep_col)
    triples_maps = sorted({u['triples_map'] for t in targets for u in t.usages})
    predicates = sorted({u['predicate_iri'] for t in targets for u in t.usages if u['predicate_iri']})
    return {
        'columns': [f'{t.model}.{t.table}.{t.column}' for t in targets],
        'views': sorted('.'.join(table_labels[v]) for v in views if v in table_labels),
        'triples_maps': triples_maps,
        'predicates': predicates,
    }


def _decide_rename(db, version, event, targets, report) -> ImpactReport:
    new_name = event.get('new_column')
    if not new_name:
        report.reasons.append('the event does not include the new column name')
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
                    f'source name {new_name} is already used by Teiid column {name} of table {target.table}')
        report.actions.append({'artifact': 'vdb', 'operation': 'set_name_in_source',
                               'model': target.model, 'table': target.table,
                               'column': target.column, 'name_in_source': new_name})
    report.decision = 'notify' if report.reasons else 'auto'
    if report.decision == 'auto':
        report.reasons.append('RENAME is absorbed by NAMEINSOURCE; ℳ and 𝒯 do not change')
    return report


def _decide_drop(db, version, event, targets, report) -> ImpactReport:
    column_ids = {t.column_id for t in targets}
    for target in targets:
        if target.in_primary_key:
            report.reasons.append(f'column {target.table}.{target.column} is part of the primary key')
        for usage in target.usages:
            if usage['role'] in HITL_ROLES:
                report.reasons.append(
                    f"used by {usage['triples_map'].rsplit('#', 1)[-1]} as {usage['role']}")
            elif usage['weakest_link'] in HITL_LINKS:
                report.reasons.append(
                    f"the path to {usage['triples_map'].rsplit('#', 1)[-1]} passes through "
                    f"{usage['weakest_link']}")
        report.actions.append({'artifact': 'vdb', 'operation': 'drop_column',
                               'model': target.model, 'table': target.table, 'column': target.column})

    # Teiid views that pass the column on are adjusted too (ADR-0023): their projection is
    # narrowed with ALTER VIEW and propagated to nested views; anything else goes to the
    # administrator.
    vdb_xml = db.execute(select(spec.Artifact.content).where(
        spec.Artifact.spec_version_id == version.id, spec.Artifact.kind == 'vdb_xml')).scalar()
    view_plan = plan_view_drops(db, version.id, column_ids, vdb_xml)
    report.reasons.extend(view_plan.reasons)
    report.actions.extend(view_plan.actions)

    # Logical tables that name the column explicitly must be rewritten as well; otherwise the
    # query still refers to the missing column and `ontop validate` rejects it (finding A002).
    # This includes view columns that disappear because of the view adjustment.
    for column_id in sorted(column_ids | view_plan.view_column_ids):
        for triples_map_iri, logical_name, status in _explicit_projections(db, version.id,
                                                                          column_id):
            if status == 'failed':
                report.reasons.append(
                    f'logical table {triples_map_iri} uses SQL that cannot be parsed')
                continue
            report.actions.append({'artifact': 'r2rml', 'operation': 'rewrite_logical_table',
                                   'triples_map_iri': triples_map_iri, 'column': logical_name})

    predicates: dict[str, set[str]] = {}
    for target in targets:
        for usage in target.usages:
            if usage['role'] == 'literal_value' and usage['predicate_iri']:
                predicates.setdefault(usage['predicate_iri'], set()).add(usage['triples_map'])
    for predicate in sorted(predicates):
        for triples_map in sorted(predicates[predicate]):
            # the TriplesMap comes from the lineage; the Executor does not guess it from the table name
            report.actions.append({'artifact': 'r2rml', 'operation': 'remove_predicate_object_map',
                                   'predicate_iri': predicate, 'triples_map_iri': triples_map})
        if _predicate_shared(db, version.id, predicate, column_ids):
            report.actions.append({'artifact': 'ontology', 'operation': 'keep_property',
                                   'predicate_iri': predicate,
                                   'reason': 'still used by another mapping'})
        else:
            report.actions.append({'artifact': 'ontology', 'operation': 'deprecate_property',
                                   'predicate_iri': predicate})
    report.decision = 'notify' if report.reasons else 'auto'
    return report


LOCAL_NAME = re.compile(r'[A-Za-z_][A-Za-z0-9_-]{0,99}')


def _iri_property(template: str, namespace: str, local: str, column: str | None = None) -> str:
    return (template.replace('{namespace}', namespace)
            .replace('{column_camel}', local)
            .replace('{column}', column if column is not None else local))


def property_problems(db: Session, version_id: int, iri: str, domain: str | None) -> list[str]:
    """Collisions between the IRI of a new property and entities of the active version.

    OWL 2 QL and Ontop reject a name used both as an object property and as a data property
    ("name sets are not disjoint"); an existing data property may be reused only when its
    domain is the same."""
    entity = db.execute(select(spec.OntEntity).where(spec.OntEntity.spec_version_id == version_id,
                                                    spec.OntEntity.iri == iri)).scalars().first()
    if entity is None:
        return []
    if entity.kind != 'datatype_property':
        return [f'IRI {iri} is already used as {entity.kind}']
    domains = set(db.execute(select(spec.OntDomain.class_expression)
                             .filter_by(entity_id=entity.id)).scalars().all())
    if domains - ({domain} if domain else set()):
        return [f'IRI {iri} already exists with a different domain: {sorted(domains)}']
    return []


def property_actions(options: dict, iri: str, domain: str | None) -> list[dict]:
    """𝒯 and ℳ actions for a new property. The mapping is not attached to a TriplesMap whose
    subjects have a class other than `domain`, because a reasoner would then infer that those
    subjects belong to `domain` too; TriplesMaps without rr:class are included."""
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
    """ADD always waits for approval: ASCAM proposes the property name and domain from the
    naming policy, and the administrator approves or revises them in the console (ADR-0021,
    ADR-0024)."""
    column = (event.get('column') or '').lower()
    target_table = table_columns[0]
    report.decision = 'hitl'
    report.actions.append({'artifact': 'vdb', 'operation': 'add_column',
                           'model': target_table.model, 'table': target_table.table,
                           'column': event.get('column'), 'column_type': event.get('column_type')})
    report.reasons.append('Adding a column adds a new property to the ontology; its name and domain '
                          'are a modelling decision made by the administrator')
    if column in existing:
        report.reasons.append(f'column name {column} already exists in Teiid table {target_table.table}')

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
            f'no TriplesMap exposes a new column of {target_table.table} by itself '
            '(the logical tables use explicit column lists); only the VDB column can be added')
        return report
    if naming is None or not namespace:
        report.reasons.append('the naming policy or the ontology namespace is not set')
        return report

    local = ''.join(part.capitalize() if i else part
                    for i, part in enumerate((event.get('column') or '').split('_')))
    iri = _iri_property(naming.property_iri_template, namespace, local, event.get('column') or '')
    domain = classes[0] if classes else None
    problems = property_problems(db, version.id, iri, domain)
    if problems:
        report.reasons.extend(problems)
        if naming.on_collision == 'qualify_with_class' and domain:
            iri = f"{iri}_{domain.rsplit('/', 1)[-1]}"
            report.reasons.append(f'the name collision policy uses IRI {iri}')
            problems = property_problems(db, version.id, iri, domain)
        if problems:
            report.reasons.append('choose another property name in the approval form')
            return report
    if len(classes) > 1:
        report.reasons.append(
            f'the TriplesMaps that expose {target_table.table} give more than one class '
            f'{classes}; the proposal uses {domain} as domain and should be reviewed')
    options['proposed_iri'], options['proposed_domain'] = iri, domain
    report.actions.extend(property_actions(options, iri, domain))
    return report


def revise_add(db: Session, plan, local_name: str, domain: str | None) -> tuple[str, list[dict]]:
    """Check the administrator's choice for an ADD plan and build its 𝒯 and ℳ actions.

    Returns (property IRI, actions). Raises ValueError with the reason a choice is rejected."""
    options = (plan.impact or {}).get('add_options')
    if not options or not options.get('triples_maps'):
        raise ValueError('this plan cannot be given a property: no TriplesMap exposes the new '
                         'column, so only the VDB column is added')
    if not options.get('namespace') or not options.get('iri_template'):
        raise ValueError('the naming policy or the ontology namespace is not set')
    if not LOCAL_NAME.fullmatch(local_name or ''):
        raise ValueError('a property name must start with a letter or underscore and contain only '
                         'letters, digits, underscores, or hyphens (at most 100 characters)')
    if options['classes'] and domain not in options['classes']:
        raise ValueError(f"the domain must be one of the classes of the exposing TriplesMaps: {options['classes']}")
    if not options['classes'] and domain:
        raise ValueError('the exposing TriplesMaps give no class, so no domain can be chosen')
    iri = _iri_property(options['iri_template'], options['namespace'], local_name)
    problems = property_problems(db, plan.base_spec_version_id, iri, domain)
    if problems:
        raise ValueError('; '.join(problems))
    return iri, property_actions(options, iri, domain)
