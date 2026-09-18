"""
Analisis kueri SQL pada logical table R2RML (`rr:sqlQuery`) dengan sqlglot.

Menghasilkan: tabel yang dibaca beserta aliasnya, kolom yang diproyeksikan (pass-through atau
ekspresi) beserta kolom sumbernya, pemakaian `SELECT *`, dan kolom yang dirujuk di klausa lain
(WHERE, JOIN, GROUP BY, HAVING, ORDER BY, subkueri). SQL yang gagal diurai ditandai `failed`
sehingga perubahan pada tabel yang dirujuk diperlakukan konservatif (matriks D11).
"""
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

CLAUSES = {'where': 'where', 'group': 'group_by', 'having': 'having', 'order': 'order_by'}


@dataclass
class Projection:
    name: str
    kind: str                                   # passthrough | expression | star
    sources: list[tuple[str | None, str]] = field(default_factory=list)   # (alias tabel, kolom)
    expression_sql: str | None = None


@dataclass
class SqlAnalysis:
    parse_status: str
    sources: list[dict] = field(default_factory=list)        # {'table': ..., 'alias': ...}
    projections: list[Projection] = field(default_factory=list)
    uses_star: bool = False
    references: list[dict] = field(default_factory=list)      # {'clause','alias','column'}


def _columns(node) -> list[tuple[str | None, str]]:
    return [(c.table or None, c.name) for c in node.find_all(exp.Column)]


def analyze(sql: str) -> SqlAnalysis:
    try:
        tree = sqlglot.parse_one(sql)
        select = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
        if select is None:
            raise ValueError('bukan SELECT')
    except Exception:                                        # noqa: BLE001
        return SqlAnalysis(parse_status='failed')

    result = SqlAnalysis(parse_status='ok')
    for table in tree.find_all(exp.Table):
        name = '.'.join(part for part in (table.catalog, table.db, table.name) if part)
        result.sources.append({'table': name, 'alias': table.alias or None})

    for item in select.expressions:
        inner = item.this if isinstance(item, exp.Alias) else item
        if isinstance(item, exp.Star) or isinstance(inner, exp.Star):
            result.uses_star = True
            result.projections.append(Projection(name='*', kind='star'))
            continue
        kind = 'passthrough' if isinstance(inner, exp.Column) else 'expression'
        result.projections.append(Projection(name=item.alias_or_name, kind=kind,
                                             sources=_columns(inner),
                                             expression_sql=None if kind == 'passthrough' else inner.sql()))

    for arg, clause in CLAUSES.items():
        node = select.args.get(arg)
        if node is not None:
            for alias, column in _columns(node):
                result.references.append({'clause': clause, 'alias': alias, 'column': column})
    for join in select.args.get('joins') or []:
        for alias, column in _columns(join):
            result.references.append({'clause': 'join', 'alias': alias, 'column': column})
    for sub in select.find_all(exp.Subquery):
        for alias, column in _columns(sub):
            result.references.append({'clause': 'subquery', 'alias': alias, 'column': column})
    return result
