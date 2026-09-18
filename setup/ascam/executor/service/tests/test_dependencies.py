"""Dependensi runtime: setiap modul pihak ketiga yang diimpor harus dideklarasikan.

Regresi F4a: `httpx` dipakai modul reloader tetapi hanya terdaftar sebagai dependensi
pengujian, sehingga kontainer gagal start meski seluruh uji lolos.
"""
import ast
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
ALIAS = {'kafka-python-ng': 'kafka', 'pyyaml': 'yaml', 'psycopg': 'psycopg',
         'uvicorn': 'uvicorn', 'sqlalchemy': 'sqlalchemy'}


def dependensi_dideklarasikan() -> set[str]:
    isi = (ROOT / 'pyproject.toml').read_text()
    blok = isi.split('dependencies = [', 1)[1].split(']\n', 1)[0]
    nama = set()
    for baris in blok.splitlines():
        cocok = re.search(r'"([A-Za-z0-9_.\[\]-]+)', baris)
        if cocok:
            mentah = cocok.group(1).lower()
            dasar = mentah.split('[')[0]
            nama.add(ALIAS.get(mentah, ALIAS.get(dasar, dasar.replace('-', '_'))))
    return nama


def modul_diimpor() -> set[str]:
    src = ROOT / 'src'
    paket = {p.name for p in src.iterdir()}
    diimpor = set()
    for berkas in src.rglob('*.py'):
        pohon = ast.parse(berkas.read_text())
        for simpul in ast.walk(pohon):
            if isinstance(simpul, ast.Import):
                diimpor |= {a.name.split('.')[0] for a in simpul.names}
            elif isinstance(simpul, ast.ImportFrom) and simpul.level == 0 and simpul.module:
                diimpor.add(simpul.module.split('.')[0])
    return diimpor - set(sys.stdlib_module_names) - paket


def test_semua_impor_pihak_ketiga_dideklarasikan():
    kurang = sorted(modul_diimpor() - dependensi_dideklarasikan())
    assert not kurang, f'dipakai tetapi tidak ada di pyproject dependencies: {kurang}'
