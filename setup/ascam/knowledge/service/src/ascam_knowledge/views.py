"""
Penyesuaian view Teiid saat kolom sumber dihapus (ADR-0023).

Penghapusan kolom dari view diperlakukan sebagai operator drop(v, c) pada skema federasi, dan
aturannya dirambatkan sepanjang dependensi view (bertingkat): bila kolom foreign table c
dihapus, setiap kolom view yang meneruskan c apa adanya ikut dihapus dari proyeksi view itu,
lalu kolom view tersebut diperlakukan dengan cara yang sama untuk view di atasnya.

Penyesuaian hanya otomatis bila mengecilkan proyeksi, yaitu tidak mengubah baris maupun nilai
kolom view yang lain. Diserahkan ke administrator bila kolom:
  * dipakai di WHERE, JOIN, GROUP BY, HAVING, atau ORDER BY view (mengubah himpunan baris);
  * dipakai di dalam ekspresi kolom view (mengubah arti nilai);
  * diteruskan oleh view yang kolomnya dideklarasikan inline, karena Teiid tidak mengizinkan
    ALTER VIEW mengubah daftar kolom (teiid-documents, Schema object DDL, hlm. 357);
  * diteruskan oleh view materialisasi, view yang definisinya tidak dapat diurai, atau view
    yang akan kehilangan seluruh kolomnya.

View dengan `SELECT *` tidak ditulis ulang; kolomnya diturunkan ulang oleh Teiid saat VDB
dimuat, dan perambatan tetap berlanjut ke view di atasnya. Definisi baru dibentuk dengan
membuang butir proyeksi dari teks definisi asli, sehingga bagian lain (FROM, WHERE, fungsi
Teiid) tidak ditulis ulang oleh pengurai.
"""
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import spec
from .viewsql import RewriteError, inline_columns, remove_projection

__all__ = ['RewriteError', 'ViewPlan', 'inline_columns', 'plan_view_drops', 'remove_projection']


@dataclass
class ViewPlan:
    actions: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    view_column_ids: set[int] = field(default_factory=set)   # kolom view yang ikut hilang


# ── perambatan ─────────────────────────────────────────────────────────────────
def plan_view_drops(db: Session, version_id: int, column_ids: set[int],
                    vdb_xml: str | None = None) -> ViewPlan:
    """Tindakan ALTER VIEW untuk penghapusan kolom `column_ids`, atau alasan persetujuan."""
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

    removals: dict[int, set[str]] = {}                  # id view -> nama kolom yang dibuang
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
                plan.reasons.append(f'kolom {label(used)} dipakai pada WHERE/JOIN view {view_name}')
                continue
            if dep.derived_role != 'projection' or dep.dependent_column_id is None:
                continue
            kind = kinds.get(dep.dependent_column_id, 'unknown')
            dependent = dep.dependent_column_id
            if view_table.kind == 'materialized_view':
                plan.reasons.append(f'kolom {label(used)} diteruskan view materialisasi {view_name}')
            elif kind == 'star':
                pass                                    # diturunkan ulang Teiid; tetap dirambatkan
            elif kind == 'passthrough':
                removals.setdefault(view_table.id, set()).add(columns[dependent].name)
            else:
                plan.reasons.append(f'kolom {label(used)} dipakai dalam ekspresi '
                                    f'{label(dependent)} pada view {view_name}')
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
            plan.reasons.append(f'definisi view {view_name} tidak dapat diurai')
            continue
        if inline_columns(vdb_xml, model, table.name):
            plan.reasons.append(f'view {view_name} mendeklarasikan kolom inline; Teiid tidak '
                                f'mengizinkan ALTER VIEW mengubah daftar kolom')
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
