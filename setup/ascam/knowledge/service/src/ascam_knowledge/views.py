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
import re
from dataclasses import dataclass, field

import sqlglot
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlglot import exp

from .db import spec


class RewriteError(Exception):
    pass


@dataclass
class ViewPlan:
    actions: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    view_column_ids: set[int] = field(default_factory=set)   # kolom view yang ikut hilang


# ── penulisan ulang teks definisi ──────────────────────────────────────────────
def _split_top_level(text: str, start: int) -> tuple[list[tuple[int, int]], int]:
    """Membagi daftar proyeksi pada koma tingkat atas, berhenti di FROM tingkat atas.

    Mengembalikan rentang setiap butir dan posisi awal kata FROM."""
    depth, quote, items, begin, i = 0, None, [], start, start
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                if i + 1 < len(text) and text[i + 1] == quote:       # tanda kutip ganda
                    i += 2
                    continue
                quote = None
        elif ch in ('"', "'", '`'):
            quote = ch
        elif ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        elif depth == 0 and ch == ',':
            items.append((begin, i))
            begin = i + 1
        elif depth == 0 and re.match(r'(?i)FROM\b', text[i:i + 5]) and (i == 0 or not (
                text[i - 1].isalnum() or text[i - 1] == '_')):
            items.append((begin, i))
            return items, i
        i += 1
    raise RewriteError('FROM tingkat atas tidak ditemukan')


def _item_name(item_sql: str) -> str | None:
    try:
        parsed = sqlglot.parse_one(f'SELECT {item_sql}')
        node = parsed.expressions[0]
    except Exception:                                   # noqa: BLE001
        return None
    if isinstance(node, exp.Star) or isinstance(getattr(node, 'this', None), exp.Star):
        return '*'
    return node.alias_or_name


def remove_projection(body: str, names: set[str]) -> str:
    """Definisi view tanpa butir proyeksi bernama `names` (perbandingan tanpa peka huruf)."""
    try:
        tree = sqlglot.parse_one(body)
    except Exception as exc:                            # noqa: BLE001
        raise RewriteError(f'definisi view tidak dapat diurai: {exc}') from exc
    if not isinstance(tree, exp.Select):
        raise RewriteError('definisi view bukan SELECT tunggal (UNION/WITH tidak ditulis ulang)')
    match = re.match(r'\s*SELECT\s+(?:DISTINCT\s+)?', body, re.IGNORECASE)
    if not match:
        raise RewriteError('definisi view tidak diawali SELECT')
    items, from_pos = _split_top_level(body, match.end())
    wanted = {n.lower() for n in names}
    kept, removed = [], set()
    for begin, end in items:
        text = body[begin:end].strip()
        name = _item_name(text)
        if name is None:
            raise RewriteError(f'butir proyeksi tidak dapat diurai: {text!r}')
        if name.lower() in wanted:
            removed.add(name.lower())
        else:
            kept.append(text)
    if removed != wanted:
        raise RewriteError(f'butir proyeksi tidak ditemukan: {sorted(wanted - removed)}')
    if not kept:
        raise RewriteError('view akan kehilangan seluruh kolomnya')
    return f'{body[:match.end()]}{", ".join(kept)} {body[from_pos:]}'


def inline_columns(vdb_xml: str | None, model: str, view: str) -> bool:
    """Apakah view dideklarasikan dengan daftar kolom inline: CREATE VIEW v (a, b) AS ..."""
    if not vdb_xml:
        return False
    nama = r'(?:"?{m}"?\s*\.\s*)?"?{v}"?'.format(m=re.escape(model), v=re.escape(view))
    pola = re.compile(r'CREATE\s+(?:VIRTUAL\s+)?VIEW\s+' + nama + r'\s*\(', re.IGNORECASE)
    return bool(pola.search(vdb_xml))


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
