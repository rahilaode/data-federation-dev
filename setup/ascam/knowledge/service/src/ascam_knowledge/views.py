"""
Adjustment of Teiid views when a source column is dropped (ADR-0023).

Dropping a column from a view is treated as an operator drop(v, c) on the federated schema,
and the rule is propagated along the view dependencies (nested views): when a foreign-table
column c is dropped, every view column that passes c on unchanged is removed from the
projection of that view, and that view column is then treated the same way for the views
above it.

The adjustment is automatic only when it narrows the projection, that is, when it changes
neither the rows nor the values of the other view columns. It goes to the administrator when
the column is
  * used in WHERE, JOIN, GROUP BY, HAVING, or ORDER BY of the view (changes the set of rows);
  * used inside an expression of a view column (changes the meaning of a value);
  * passed on by a view that declares its columns inline, because Teiid does not allow
    ALTER VIEW to change the column list (teiid-documents, Schema object DDL, p. 357);
  * passed on by a materialized view, by a view whose definition cannot be parsed, or by a
    view that would lose all of its columns.

Views with `SELECT *` are not rewritten; Teiid derives their columns again when the VDB is
loaded, and the propagation continues to the views above them. The new definition is formed
by removing projection items from the original definition text, so the other parts (FROM,
WHERE, Teiid functions) are not rewritten by the parser.
"""
import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import spec
from .viewsql import RewriteError, inline_columns, remove_projection

__all__ = ['RewriteError', 'ViewPlan', 'check_view_adds', 'inline_columns', 'plan_view_drops',
           'remove_projection']


@dataclass
class ViewPlan:
    actions: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    view_column_ids: set[int] = field(default_factory=set)   # view columns that disappear too


# ── propagation ────────────────────────────────────────────────────────────────
def plan_view_drops(db: Session, version_id: int, column_ids: set[int],
                    vdb_xml: str | None = None) -> ViewPlan:
    """ALTER VIEW actions for dropping the columns `column_ids`, or the reasons to notify."""
    plan = ViewPlan()
    tables = {t.id: t for t in db.execute(
        select(spec.TeiidTable).where(spec.TeiidTable.spec_version_id == version_id)).scalars()}
    models = {m.id: m.name for m in db.execute(
        select(spec.TeiidModel).where(spec.TeiidModel.spec_version_id == version_id)).scalars()}
    columns = {c.id: c for c in db.execute(
        select(spec.TeiidColumn).where(spec.TeiidColumn.spec_version_id == version_id)).scalars()}
    views = {v.table_id: v for v in db.execute(
        select(spec.TeiidView).where(spec.TeiidView.spec_version_id == version_id)).scalars()}
    kinds = {k.column_id: k.expression_kind for k in db.execute(
        select(spec.TeiidViewColumn).where(spec.TeiidViewColumn.spec_version_id == version_id)
    ).scalars()}
    dependencies = db.execute(select(spec.TeiidDependency)
                              .where(spec.TeiidDependency.spec_version_id == version_id)).scalars().all()

    def label(column_id: int) -> str:
        col = columns[column_id]
        table = tables[col.table_id]
        return f'{models[table.model_id]}.{table.name}.{col.name}'

    removals: dict[int, set[str]] = {}                  # view id -> names of removed columns
    queue, seen = list(column_ids), set(column_ids)
    while queue:
        used = queue.pop(0)
        for dep in dependencies:
            if dep.used_column_id != used:
                continue
            view_table = tables.get(dep.dependent_table_id)
            if view_table is None or view_table.kind not in ('view', 'materialized_view'):
                continue
            view_name = f'{models[view_table.model_id]}.{view_table.name}'
            if dep.derived_role == 'predicate':
                plan.reasons.append(f'column {label(used)} is used in WHERE/JOIN of view {view_name}')
                continue
            if dep.derived_role != 'projection' or dep.dependent_column_id is None:
                continue
            kind = kinds.get(dep.dependent_column_id, 'unknown')
            dependent = dep.dependent_column_id
            if view_table.kind == 'materialized_view':
                plan.reasons.append(f'column {label(used)} is passed on by materialized view {view_name}')
            elif kind == 'star':
                pass                                    # derived again by Teiid; still propagated
            elif kind == 'passthrough':
                removals.setdefault(view_table.id, set()).add(columns[dependent].name)
            else:
                plan.reasons.append(f'column {label(used)} is used in expression '
                                    f'{label(dependent)} of view {view_name}')
                continue
            plan.view_column_ids.add(dependent)
            if dependent not in seen:
                seen.add(dependent)
                queue.append(dependent)

    for table_id, names in sorted(removals.items()):
        table = tables[table_id]
        model = models[table.model_id]
        view = views.get(table_id)
        view_name = f'{model}.{table.name}'
        if view is None or view.parse_status != 'ok':
            plan.reasons.append(f'the definition of view {view_name} cannot be parsed')
            continue
        if inline_columns(vdb_xml, model, table.name):
            plan.reasons.append(f'view {view_name} declares its columns inline; Teiid does not '
                                f'allow ALTER VIEW to change the column list')

            continue
        try:
            body = remove_projection(view.body, names)
        except RewriteError as exc:
            plan.reasons.append(f'view {view_name}: {exc}')
            continue
        plan.actions.append({'artifact': 'vdb', 'operation': 'alter_view', 'model': model,
                             'table': table.name, 'body': body,
                             'removed_columns': sorted(names)})
    return plan


# ── ADD: views that select * from the table ───────────────────────────────────
_STAR_TEXT = re.compile(r'(?:SELECT\s+(?:DISTINCT\s+)?|,)\s*(?:[\w"]+\s*\.\s*)?\*', re.IGNORECASE)
_SET_OPERATIONS = tuple(getattr(exp, name) for name in ('Union', 'Except', 'Intersect')
                        if hasattr(exp, name))


def _star_tables(tree: exp.Expression, resolve) -> set[int]:
    """Teiid tables whose columns a `*` or `alias.*` in any SELECT of `tree` passes on."""
    covered: set[int] = set()
    for select in tree.find_all(exp.Select):
        sources = []
        source_from = select.args.get('from_') or select.args.get('from')   # key differs by version
        if source_from is not None:
            sources.append(source_from.this)
        sources += [join.this for join in select.args.get('joins') or []]
        by_alias = {}
        for source in sources:
            if isinstance(source, exp.Table):
                table_id = resolve(source.db, source.name)
                if table_id is not None:
                    by_alias[(source.alias_or_name or '').lower()] = table_id
                    by_alias.setdefault(source.name.lower(), table_id)
        for item in select.expressions:
            inner = item.this if isinstance(item, exp.Alias) else item
            if isinstance(inner, exp.Star):
                covered |= set(by_alias.values())
            elif isinstance(inner, exp.Column) and isinstance(inner.this, exp.Star):
                table_id = by_alias.get((inner.table or '').lower())
                if table_id is not None:
                    covered.add(table_id)
    return covered


def check_view_adds(db: Session, version_id: int, table_id: int, column: str,
                    vdb_xml: str | None = None) -> list[str]:
    """Reasons why a new column of `table_id` cannot be added safely (pattern P-001, ADR-0032).

    A view that selects `*` from the table, directly or through other such views, gets the new
    column when Teiid loads the next VDB version. That fails, or gives a wrong view, when the
    view is materialized (its cache table lacks the column), uses UNION, EXCEPT, or INTERSECT
    (the branches would differ in their number of columns), declares its columns inline (Teiid
    does not add columns to such a view), or already has a column of that name. A view whose
    definition cannot be parsed but seems to select `*` from the table is reported as well.
    Views that list their columns are not affected.
    """
    tables = {t.id: t for t in db.execute(
        select(spec.TeiidTable).where(spec.TeiidTable.spec_version_id == version_id)).scalars()}
    models = {m.id: m.name for m in db.execute(
        select(spec.TeiidModel).where(spec.TeiidModel.spec_version_id == version_id)).scalars()}
    views = {v.table_id: v for v in db.execute(
        select(spec.TeiidView).where(spec.TeiidView.spec_version_id == version_id)).scalars()}
    uses: dict[int, set[int]] = {}
    for dep in db.execute(select(spec.TeiidDependency)
                          .where(spec.TeiidDependency.spec_version_id == version_id)).scalars():
        uses.setdefault(dep.dependent_table_id, set()).add(dep.used_table_id)
    column_names: dict[int, set[str]] = {}
    for col in db.execute(select(spec.TeiidColumn)
                          .where(spec.TeiidColumn.spec_version_id == version_id)).scalars():
        column_names.setdefault(col.table_id, set()).add(col.name.lower())

    def resolve(schema: str, name: str) -> int | None:
        matches = [t.id for t in tables.values() if t.name.lower() == (name or '').lower()
                   and (not schema or models[t.model_id].lower() == schema.lower())]
        return matches[0] if len(matches) == 1 else None

    def label(tid: int) -> str:
        return f'{models[tables[tid].model_id]}.{tables[tid].name}'

    reasons: list[str] = []
    covered, done = {table_id}, set()
    changed = True
    while changed:                                     # nested views: until nothing new is covered
        changed = False
        for view_id, used in uses.items():
            if view_id in done or not used & covered or view_id not in tables:
                continue
            table = tables[view_id]
            if table.kind not in ('view', 'materialized_view'):
                continue
            view = views.get(view_id)
            name, read = label(view_id), ', '.join(sorted(label(t) for t in used & covered))
            tree = None
            if view is not None and view.parse_status == 'ok':
                try:
                    tree = sqlglot.parse_one(view.body)
                except Exception:                      # noqa: BLE001
                    tree = None
            if tree is None:
                body = view.body if view is not None else ''
                if _STAR_TEXT.search(body or ''):
                    reasons.append(f'the definition of view {name}, which reads {read}, cannot be '
                                   'parsed, so it is unknown whether it gets the new column')
                    done.add(view_id)
                    covered.add(view_id)
                    changed = True
                continue
            if not _star_tables(tree, resolve) & covered:
                done.add(view_id)
                continue
            done.add(view_id)
            covered.add(view_id)
            changed = True
            if table.kind == 'materialized_view':
                reasons.append(f'view {name} selects * from {read} and is materialized; its '
                               'cache table would lack the new column')
            if _SET_OPERATIONS and (isinstance(tree, _SET_OPERATIONS) or tree.find(*_SET_OPERATIONS)) \
                    and any(not _star_tables(branch, resolve) & covered
                            for branch in tree.find_all(exp.Select)):
                reasons.append(f'view {name} selects * from {read} in a UNION, EXCEPT, or '
                               'INTERSECT; its branches would differ in their number of columns')
            if inline_columns(vdb_xml, models[table.model_id], table.name):
                reasons.append(f'view {name} selects * from {read} but declares its columns '
                               'inline; Teiid does not add the new column to it')
            if column.lower() in column_names.get(view_id, set()):
                reasons.append(f'view {name} selects * from {read} and already has a column '
                               f'named {column}')
    return reasons
