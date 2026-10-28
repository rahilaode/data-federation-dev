"""
Rewriting of Teiid view definition text (ADR-0023), with no database dependency.

Kept apart from views.py so that the Teiid feasibility experiment
(archive/experiments/f0/f0_8_teiid_alter_view.py) uses exactly the same functions as Knowledge.
"""
import re

import sqlglot
from sqlglot import exp


class RewriteError(Exception):
    pass


# ── rewriting of the definition text ───────────────────────────────────────────
def _split_top_level(text: str, start: int) -> tuple[list[tuple[int, int]], int]:
    """Split the projection list at top-level commas, stopping at the top-level FROM.

    Returns the span of every item and the start position of the word FROM."""
    depth, quote, items, begin, i = 0, None, [], start, start
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == quote:
                if i + 1 < len(text) and text[i + 1] == quote:       # doubled quote
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
    raise RewriteError('no top-level FROM found')


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
    """The view definition without the projection items named `names` (case-insensitive)."""
    try:
        tree = sqlglot.parse_one(body)
    except Exception as exc:                            # noqa: BLE001
        raise RewriteError(f'the view definition cannot be parsed: {exc}') from exc
    if not isinstance(tree, exp.Select):
        raise RewriteError('the view definition is not a single SELECT (UNION/WITH are not rewritten)')
    match = re.match(r'\s*SELECT\s+(?:DISTINCT\s+)?', body, re.IGNORECASE)
    if not match:
        raise RewriteError('the view definition does not start with SELECT')
    items, from_pos = _split_top_level(body, match.end())
    wanted = {n.lower() for n in names}
    kept, removed = [], set()
    for begin, end in items:
        text = body[begin:end].strip()
        name = _item_name(text)
        if name is None:
            raise RewriteError(f'projection item cannot be parsed: {text!r}')
        if name.lower() in wanted:
            removed.add(name.lower())
        else:
            kept.append(text)
    if removed != wanted:
        raise RewriteError(f'projection items not found: {sorted(wanted - removed)}')
    if not kept:
        raise RewriteError('the view would lose all of its columns')
    return f'{body[:match.end()]}{", ".join(kept)} {body[from_pos:]}'


def inline_columns(vdb_xml: str | None, model: str, view: str) -> bool:
    """Whether the view is declared with an inline column list: CREATE VIEW v (a, b) AS ..."""
    if not vdb_xml:
        return False
    name = r'(?:"?{m}"?\s*\.\s*)?"?{v}"?'.format(m=re.escape(model), v=re.escape(view))
    pattern = re.compile(r'CREATE\s+(?:VIRTUAL\s+)?VIEW\s+' + name + r'\s*\(', re.IGNORECASE)
    return bool(pattern.search(vdb_xml))

