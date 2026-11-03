"""Converts the NPD R2RML mapping so that it reads the federation through Teiid.

Usage (from setup/npd):
    python3 tools/gen_mapping.py npd-benchmark/mappings/postgres/npd-v2-ql.r2rml \
        > ../vkg-system/config/mapping.ttl

Ontop 4.1.1 reaches Teiid through TeiidDBMetadataProvider, which knows a table only by its full
name <model>.<table> (getAllIDs returns that one ID), so every table in the mapping SQL must be
qualified with the Teiid model that holds it. Nothing else in the mapping changes.

Each table reference after FROM, JOIN or a comma of a FROM list is rewritten as
    "<model>"."<table>" AS <table as it was written>
when it had no alias, or as "<model>"."<table>" <alias> when it had one. The alias keeps every
column qualification of the original query ("baaArea"."baaNpdidBsnsArrArea",
wellbore_exploration_all."wlbNpdidWellbore") valid without touching it.
Starts from the PostgreSQL variant of the mapping because it double-quotes the mixed-case names.

Seven TriplesMaps are then pointed at the service views of tools/services.py (scenarios
A007-A009); see services.py for how their answers compare with the original.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from services import REDIRECT  # noqa: E402
from split import PG, MY  # noqa: E402

MODEL = {**{t: 'reporting' for t in PG}, **{t: 'registry' for t in MY}}
MODEL_CI = {t.lower(): (t, m) for t, m in MODEL.items()}

TOKEN = re.compile(r'"(?:[^"]|"")*"|\'(?:[^\']|\'\')*\'|\w+|\s+|.', re.S)
# words that can follow a table reference and therefore are not an alias
NOT_ALIAS = {'INNER', 'LEFT', 'RIGHT', 'FULL', 'CROSS', 'NATURAL', 'JOIN', 'ON', 'WHERE', 'GROUP',
             'ORDER', 'HAVING', 'UNION', 'EXCEPT', 'INTERSECT', 'LIMIT', 'OFFSET', 'OUTER', 'USING'}
ENDS_FROM_LIST = {'WHERE', 'ON', 'GROUP', 'ORDER', 'HAVING', 'UNION', 'EXCEPT', 'INTERSECT', 'LIMIT'}


def is_ident(tok):
    return tok.startswith('"') or re.fullmatch(r'[A-Za-z_]\w*', tok) is not None


def unquote(tok):
    return tok[1:-1].replace('""', '"') if tok.startswith('"') else tok


def qualify(sql):
    toks = TOKEN.findall(sql)
    out, i, expect_table, in_from_list, depth = [], 0, False, False, 0
    found = []
    while i < len(toks):
        tok, up = toks[i], toks[i].upper()
        if tok == '(':
            depth += 1
        elif tok == ')':
            depth -= 1
        if expect_table and not tok.isspace():
            if not is_ident(tok):
                raise ValueError(f'unexpected token {tok!r} where a table was expected: {sql}')
            j = i + 1
            if j < len(toks) and toks[j] == '.':
                raise ValueError(f'already qualified: {sql}')
            name = unquote(tok)
            real, model = MODEL.get(name) and (name, MODEL[name]) or MODEL_CI.get(name.lower(), (None, None))
            if real is None or (tok.startswith('"') and real != name):
                raise ValueError(f'unknown table {tok}: {sql}')
            # does an alias follow?
            k = j
            while k < len(toks) and toks[k].isspace():
                k += 1
            nxt = toks[k] if k < len(toks) else ''
            has_alias = nxt.upper() == 'AS' or (is_ident(nxt) and nxt.upper() not in NOT_ALIAS)
            out.append(f'"{model}"."{real}"' + ('' if has_alias else f' AS {tok}'))
            found.append((real, model))
            expect_table = False
            i += 1
            continue
        if up == 'FROM':
            expect_table, in_from_list = True, True
        elif up == 'JOIN':
            expect_table = True
        elif up in ENDS_FROM_LIST:
            in_from_list = False
        elif tok == ',' and in_from_list and depth == 0:
            expect_table = True
        out.append(tok)
        i += 1
    if not found:
        raise ValueError(f'no table found: {sql}')
    return ''.join(out), found


LITERAL = re.compile(r'(rr:sqlQuery\s+)"((?:[^"\\]|\\.)*)"')


def turtle_unescape(s):
    return re.sub(r'\\(.)', lambda m: {'n': '\n', 't': '\t', 'r': '\r'}.get(m[1], m[1]), s)


def turtle_escape(s):
    return s.replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r')


def convert(text):
    count = 0

    def repl(m):
        nonlocal count
        sql, _ = qualify(turtle_unescape(m[2]))
        count += 1
        return m[1] + '"' + turtle_escape(sql) + '"'

    result = LITERAL.sub(repl, text)
    return result, count


def redirect(text: str) -> str:
    """Points the TriplesMaps of services.REDIRECT at the service views (text-level edit of their
    Turtle blocks, so the rest of the file stays as generated)."""
    blocks = text.split('\n\n')
    done = set()
    for index, block in enumerate(blocks):
        head = block.lstrip().split(' ', 1)[0]
        iri = head[1:-1] if head.startswith('<') and head.endswith('>') else None
        if iri not in REDIRECT:
            continue
        spec = REDIRECT[iri]
        new, count = LITERAL.subn(lambda m: m[1] + '"' + turtle_escape(spec['sql']) + '"', block)
        if count != 1:
            raise SystemExit(f'{iri}: expected one rr:sqlQuery, found {count}')
        for old, replacement in spec['replace'].items():
            if old not in new:
                raise SystemExit(f'{iri}: {old!r} not found')
            new = new.replace(old, replacement)
        blocks[index] = new
        done.add(iri)
    missing = set(REDIRECT) - done
    if missing:
        raise SystemExit(f'TriplesMaps to redirect not found: {sorted(missing)}')
    return '\n\n'.join(blocks)


if __name__ == '__main__':
    src = open(sys.argv[1], encoding='utf-8').read()
    result, count = convert(src)
    if count != src.count('rr:sqlQuery'):
        raise SystemExit(f'converted {count} of {src.count("rr:sqlQuery")} rr:sqlQuery literals')
    result = redirect(result)
    header = ('# NPD Benchmark mapping (ontop/npd-benchmark @ 5b1eeb3, mappings/postgres/npd-v2-ql.r2rml)\n'
              '# with every table qualified by its Teiid model of the VDB "npd" (reporting = PostgreSQL,\n'
              f'# registry = MySQL) and {len(REDIRECT)} TriplesMaps reading the views of model "services".\n'
              f'# Generated by setup/npd/tools/gen_mapping.py ({count} rr:sqlQuery); do not edit by hand.\n')
    sys.stdout.write(header + result)
