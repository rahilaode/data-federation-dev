"""The split of the 70 NPD tables over the two sources (imported by gen_split.py).

reporting (PostgreSQL): data operators report about wells, stratigraphy, seismic surveys and
production -- the kind of data held in the Diskos national data repository.
registry (MySQL): every other table -- licences and the Petroleum Register, companies, fields,
discoveries, facilities, TUF, business arrangement areas, reserves, investments.
npd-tables.txt lists the 70 tables of the NPD dump (from pg_class of the loaded dump).
"""
import os
HERE = os.path.dirname(os.path.abspath(__file__))
TABLES = [l.strip() for l in open(os.path.join(HERE, 'npd-tables.txt')) if l.strip()]
PG = {t for t in TABLES if t.startswith(('wellbore_', 'strat_', 'seis_', 'sea', 'field_production_'))} | {'wlbPoint'}
MY = set(TABLES) - PG
assert len(TABLES) == 70 and len(PG) == 25 and len(MY) == 45
