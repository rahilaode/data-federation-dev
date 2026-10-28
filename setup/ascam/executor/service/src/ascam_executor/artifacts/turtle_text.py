"""
Text-level editing helpers for Turtle files.

They let ASCAM add and remove **its own** parts without rewriting the whole file, so the
comments and layout written by people stay intact (ownership invariant P6). Every block written
by ASCAM is enclosed in markers that hold its key, so removing it again is deterministic.
"""
BLOCK_MARKER = '# ─── ASCAM-managed blocks (do not edit by hand) ───'
BEGIN = '# ascam:begin'
END = '# ascam:end'


def prefix_map(text: str) -> dict[str, str]:
    """{namespace: prefix} from the @prefix declarations of the file."""
    mapping = {}
    for lines in text.splitlines():
        stripped = lines.strip()
        if stripped.lower().startswith('@prefix'):
            parts = stripped.split()
            if len(parts) >= 3:
                mapping[parts[2].strip('<>')] = parts[1].rstrip(':')
    return mapping


def shorten(iri: str, prefixes: dict[str, str]) -> str:
    for namespace, prefix in sorted(prefixes.items(), key=lambda kv: -len(kv[0])):
        if iri.startswith(namespace):
            rest = iri[len(namespace):]
            if rest and all(c.isalnum() or c in '_-' for c in rest):
                return f'{prefix}:{rest}'
    return f'<{iri}>'


def ensure_prefixes(text: str, required: dict[str, str]) -> str:
    """Adds the missing @prefix declarations after the last existing one."""
    present = prefix_map(text)
    additions = [f'@prefix {prefix}: <{namespace}> .'
                for prefix, namespace in required.items() if namespace not in present]
    if not additions:
        return text
    lines = text.splitlines()
    last = max((i for i, b in enumerate(lines) if b.strip().lower().startswith('@prefix')),
                   default=-1)
    lines[last + 1:last + 1] = additions
    return '\n'.join(lines) + ('\n' if text.endswith('\n') else '')


def append_block(text: str, key: str, content: str) -> str:
    """Appends a managed block with its markers at the end of the file."""
    base = text if text.endswith('\n') else text + '\n'
    if BLOCK_MARKER not in base:
        base += f'\n{BLOCK_MARKER}\n'
    return f'{base}\n{BEGIN} {key}\n{content.strip()}\n{END} {key}\n'


def remove_block(text: str, key: str) -> tuple[str, int]:
    """Removes the managed blocks with the given key; returns (text, number of blocks).

    When no managed block is left, the block marker is removed too, so the file is exactly
    as it was before ASCAM edited it.
    """
    lines = text.splitlines(keepends=True)
    kept, count, skipping = [], 0, False
    for item in lines:
        stripped = item.strip()
        if stripped == f'{BEGIN} {key}':
            skipping, count = True, count + 1
            if kept and kept[-1].strip() == '':
                kept.pop()
            continue
        if skipping:
            if stripped == f'{END} {key}':
                skipping = False
            continue
        kept.append(item)
    out = ''.join(kept)
    if count and BEGIN not in out:
        out = out.replace(BLOCK_MARKER + '\n', '').rstrip() + '\n'
    return out, count


def has_block(text: str, key: str) -> bool:
    return f'{BEGIN} {key}' in text
