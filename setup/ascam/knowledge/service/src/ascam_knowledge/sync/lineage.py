"""
Membangun lineage kolom (`spec.column_usage`): jalur dari kolom foreign table sampai ke
predikat pada ℳ, beserta perannya.

Jalur dapat melewati view (bertingkat). Setiap tepi diberi jenis, dan jalur diberi
`weakest_link`, yaitu tepi terlemah yang dilalui; satu tepi `expression` atau `predicate`
membuat seluruh jalur tidak dapat disesuaikan otomatis saat DROP (matriks D11, bukti F0.7).

Peran (`role`):
  literal_value     nilai literal sebuah DatatypeProperty
  iri_template      pembentuk IRI subjek, objek, atau graph
  join_key          kolom pada rr:joinCondition
  sql_predicate     kolom di WHERE/JOIN/klausa lain, termasuk milik view yang dibaca mapping
  dynamic_predicate predikat yang berasal dari kolom (tidak diketahui secara statis)
  projection_only   kolom logical table yang tidak dipakai term map mana pun
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import spec

WEAKNESS = {'direct': 0, 'passthrough': 1, 'star': 2, 'expression': 3, 'predicate': 4}


def _weakest(*kinds: str) -> str:
    return max(kinds, key=lambda k: WEAKNESS.get(k, 0))


class _Resolver:
    """Menelusuri kolom (mungkin kolom view) sampai ke kolom foreign table."""

    def __init__(self, db: Session, version_id: int):
        self.table_kind: dict[int, str] = {}
        self.column_table: dict[int, int] = {}
        self.column_name: dict[int, str] = {}
        for column_id, table_id, name, kind in db.execute(
                select(spec.TeiidColumn.id, spec.TeiidColumn.table_id, spec.TeiidColumn.name,
                       spec.TeiidTable.kind)
                .join(spec.TeiidTable, spec.TeiidTable.id == spec.TeiidColumn.table_id)
                .where(spec.TeiidColumn.spec_version_id == version_id)).all():
            self.column_table[column_id] = table_id
            self.column_name[column_id] = name
            self.table_kind[table_id] = kind

        self.edges: dict[int, list[tuple[int, str]]] = {}          # kolom view -> (kolom sumber, jenis)
        self.view_predicates: dict[int, list[int]] = {}            # tabel view -> kolom yang dipakai WHERE/JOIN
        expression_kind = dict(db.execute(
            select(spec.TeiidViewColumn.column_id, spec.TeiidViewColumn.expression_kind)
            .where(spec.TeiidViewColumn.spec_version_id == version_id)).all())
        for dep_col, used_col, dep_table, role in db.execute(
                select(spec.TeiidDependency.dependent_column_id, spec.TeiidDependency.used_column_id,
                       spec.TeiidDependency.dependent_table_id, spec.TeiidDependency.derived_role)
                .where(spec.TeiidDependency.spec_version_id == version_id)).all():
            if dep_col and used_col:
                self.edges.setdefault(dep_col, []).append(
                    (used_col, expression_kind.get(dep_col, 'passthrough')))
            elif used_col and role == 'predicate':
                self.view_predicates.setdefault(dep_table, []).append(used_col)

    def to_foreign(self, column_id: int, seen: frozenset[int] = frozenset()  # noqa: B006
                   ) -> list[tuple[int, list[dict], str]]:
        """-> [(id kolom foreign, jalur, tepi terlemah)]"""
        table_id = self.column_table.get(column_id)
        if table_id is None or column_id in seen:
            return []
        if self.table_kind.get(table_id) == 'foreign':
            return [(column_id, [], 'direct')]
        out = []
        for used_column, kind in self.edges.get(column_id, []):
            hop = {'column_id': column_id, 'name': self.column_name.get(column_id), 'kind': kind}
            for foreign, path, weakest in self.to_foreign(used_column, seen | {column_id}):
                out.append((foreign, [hop, *path], _weakest(kind, weakest)))
        return out


def build(db: Session, version_id: int, issues: list[dict]) -> dict[str, int]:
    resolver = _Resolver(db, version_id)

    entities = {}
    for entity_id, iri, kind, deprecated in db.execute(
            select(spec.OntEntity.id, spec.OntEntity.iri, spec.OntEntity.kind, spec.OntEntity.deprecated)
            .where(spec.OntEntity.spec_version_id == version_id)).all():
        entities.setdefault(iri, []).append({'id': entity_id, 'kind': kind, 'deprecated': deprecated})

    logical_kind = dict(db.execute(
        select(spec.LogicalColumn.id, spec.LogicalColumn.expression_kind)
        .where(spec.LogicalColumn.spec_version_id == version_id)).all())

    logical_sources = {}                                    # logical column -> [kolom Teiid]
    for logical_id, column_id in db.execute(
            select(spec.LogicalColumnSource.logical_column_id, spec.LogicalColumnSource.teiid_column_id)
            .where(spec.LogicalColumnSource.spec_version_id == version_id)).all():
        logical_sources.setdefault(logical_id, []).append(column_id)

    predicate_of_pom: dict[int, tuple[str | None, bool]] = {}   # pom -> (IRI predikat, dinamis?)
    for pom_id, value_kind, value in db.execute(
            select(spec.TermMap.pom_id, spec.TermMap.value_kind, spec.TermMap.value)
            .where(spec.TermMap.spec_version_id == version_id,
                   spec.TermMap.position == 'predicate')).all():
        predicate_of_pom[pom_id] = (value if value_kind == 'constant' else None,
                                    value_kind != 'constant')

    used_logical: set[int] = set()
    rows = 0

    def add(foreign_column_id, triples_map_id, term_map_id, role, predicate_iri, path, weakest):
        nonlocal rows
        entity = None
        if predicate_iri and predicate_iri in entities:
            candidates = entities[predicate_iri]
            entity = next((e for e in candidates if e['kind'] == 'datatype_property'), candidates[0])
        db.add(spec.ColumnUsage(spec_version_id=version_id, foreign_column_id=foreign_column_id,
                                triples_map_id=triples_map_id, term_map_id=term_map_id, role=role,
                                predicate_iri=predicate_iri,
                                predicate_entity_id=entity['id'] if entity else None,
                                path=path, weakest_link=weakest))
        rows += 1

    # 1. kolom yang dipakai term map
    for term_map, logical_id in db.execute(
            select(spec.TermMap, spec.TermMapColumn.logical_column_id)
            .join(spec.TermMapColumn, spec.TermMapColumn.term_map_id == spec.TermMap.id)
            .where(spec.TermMap.spec_version_id == version_id)).all():
        used_logical.add(logical_id)
        predicate_iri, dynamic = predicate_of_pom.get(term_map.pom_id, (None, False))
        if term_map.position == 'predicate':
            role = 'dynamic_predicate'
        elif term_map.position == 'object':
            literal = (term_map.term_type == 'literal' or term_map.datatype or term_map.language
                       or (term_map.value_kind == 'column' and term_map.term_type is None))
            role = 'literal_value' if literal else 'iri_template'
            if dynamic:
                role = 'dynamic_predicate'
        else:                                               # subject / graph
            role = 'iri_template'
        for column_id in logical_sources.get(logical_id, []):
            for foreign, path, weakest in resolver.to_foreign(column_id):
                # sifat kolom logical table (ekspresi, SELECT *) ikut melemahkan jalur
                add(foreign, term_map.triples_map_id, term_map.id, role,
                    predicate_iri if term_map.position == 'object' else None, path,
                    _weakest(weakest, logical_kind.get(logical_id, 'passthrough')))

    # 2. kolom pada join condition
    for join, triples_map_id in db.execute(
            select(spec.JoinCondition, spec.TermMap.triples_map_id)
            .join(spec.TermMap, spec.TermMap.id == spec.JoinCondition.term_map_id)
            .where(spec.JoinCondition.spec_version_id == version_id)).all():
        for logical_id, in db.execute(
                select(spec.LogicalColumn.id)
                .where(spec.LogicalColumn.spec_version_id == version_id,
                       spec.LogicalColumn.triples_map_id == triples_map_id,
                       spec.LogicalColumn.name == join.child_column)).all():
            used_logical.add(logical_id)
            for column_id in logical_sources.get(logical_id, []):
                for foreign, path, weakest in resolver.to_foreign(column_id):
                    add(foreign, triples_map_id, None, 'join_key', None, path,
                        _weakest(weakest, logical_kind.get(logical_id, 'passthrough')))

    # 3. kolom pada klausa SQL logical table
    for triples_map_id, column_id in db.execute(
            select(spec.SqlReference.triples_map_id, spec.SqlReference.teiid_column_id)
            .where(spec.SqlReference.spec_version_id == version_id)).all():
        for foreign, path, weakest in resolver.to_foreign(column_id):
            add(foreign, triples_map_id, None, 'sql_predicate', None, path,
                _weakest(weakest, 'predicate'))

    # 4. kolom yang dipakai view (WHERE/JOIN) yang dibaca mapping
    for triples_map_id, table_id in db.execute(
            select(spec.LogicalSource.triples_map_id, spec.LogicalSource.teiid_table_id)
            .where(spec.LogicalSource.spec_version_id == version_id,
                   spec.LogicalSource.teiid_table_id.is_not(None))).all():
        for column_id in resolver.view_predicates.get(table_id, []):
            for foreign, path, weakest in resolver.to_foreign(column_id):
                add(foreign, triples_map_id, None, 'sql_predicate', None, path,
                    _weakest(weakest, 'predicate'))

    # 5. kolom logical table yang hanya diproyeksikan
    for logical_id, triples_map_id in db.execute(
            select(spec.LogicalColumn.id, spec.LogicalColumn.triples_map_id)
            .where(spec.LogicalColumn.spec_version_id == version_id)).all():
        if logical_id in used_logical:
            continue
        for column_id in logical_sources.get(logical_id, []):
            for foreign, path, weakest in resolver.to_foreign(column_id):
                add(foreign, triples_map_id, None, 'projection_only', None, path,
                    _weakest(weakest, logical_kind.get(logical_id, 'passthrough')))

    db.flush()
    _check_vocabulary(db, version_id, entities, issues)
    return {'column_usages': rows}


def _check_vocabulary(db: Session, version_id: int, entities: dict, issues: list[dict]) -> None:
    """Pemeriksaan yang tidak dilakukan `ontop validate` (temuan F0.5)."""
    used_predicates = set()
    for predicate_iri, role in db.execute(
            select(spec.ColumnUsage.predicate_iri, spec.ColumnUsage.role)
            .where(spec.ColumnUsage.spec_version_id == version_id,
                   spec.ColumnUsage.predicate_iri.is_not(None))).all():
        used_predicates.add(predicate_iri)
        candidates = entities.get(predicate_iri)
        if not candidates:
            issues.append({'code': 'predicate_undeclared', 'severity': 'error',
                           'subject_kind': 'predicate', 'subject_ref': predicate_iri,
                           'message': 'predikat pada mapping tidak dideklarasikan di ontologi'})
            continue
        kinds = {c['kind'] for c in candidates}
        if role == 'literal_value' and 'datatype_property' not in kinds:
            issues.append({'code': 'predicate_kind_mismatch', 'severity': 'error',
                           'subject_kind': 'predicate', 'subject_ref': predicate_iri,
                           'message': f'objek literal memakai predikat berjenis {sorted(kinds)}'})
        if all(c['deprecated'] for c in candidates):
            issues.append({'code': 'predicate_deprecated_in_use', 'severity': 'warning',
                           'subject_kind': 'predicate', 'subject_ref': predicate_iri,
                           'message': 'predikat berstatus deprecated tetapi masih dipakai mapping'})

    for iri, candidates in entities.items():
        if iri in used_predicates:
            continue
        if any(c['kind'] == 'datatype_property' and not c['deprecated'] for c in candidates):
            issues.append({'code': 'unused_property', 'severity': 'info',
                           'subject_kind': 'predicate', 'subject_ref': iri,
                           'message': 'DatatypeProperty tidak dipakai mapping mana pun'})
