"""Runtime dependencies: every third-party module that is imported must be declared.

Regression F4a: `httpx` was used by the reloader module but was listed only as a test
dependency, so the container failed to start although every test passed.
"""
import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
ALIAS = {'kafka-python-ng': 'kafka', 'pyyaml': 'yaml', 'psycopg': 'psycopg',
         'uvicorn': 'uvicorn', 'sqlalchemy': 'sqlalchemy'}


def declared_dependencies() -> set[str]:
    content = (ROOT / 'pyproject.toml').read_text()
    block = content.split('dependencies = [', 1)[1].split(']\n', 1)[0]
    names = set()
    for line in block.splitlines():
        found = re.search(r'"([A-Za-z0-9_.\[\]-]+)', line)
        if found:
            raw = found.group(1).lower()
            base = raw.split('[')[0]
            names.add(ALIAS.get(raw, ALIAS.get(base, base.replace('-', '_'))))
    return names


def imported_modules() -> set[str]:
    src = ROOT / 'src'
    packages = {p.name for p in src.iterdir()}
    imported = set()
    for path in src.rglob('*.py'):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split('.')[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported.add(node.module.split('.')[0])
    return imported - set(sys.stdlib_module_names) - packages


def test_every_third_party_import_is_declared():
    missing = sorted(imported_modules() - declared_dependencies())
    assert not missing, f'used but not listed in pyproject dependencies: {missing}'
