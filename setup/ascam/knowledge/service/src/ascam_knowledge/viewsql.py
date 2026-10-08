"""
Penulisan ulang teks definisi view Teiid (ADR-0023), tanpa dependensi basis data.

Dipisahkan dari views.py agar uji kelayakan Teiid (experiments/f0/f0_8_teiid_alter_view.py)
memakai fungsi yang persis sama dengan Knowledge.
"""
import re

import sqlglot
from sqlglot import exp


class RewriteError(Exception):
    pass


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
