"""
Returns the source columns of ALL evaluation scenarios to their base state, without running an
evaluation.

Use it when an earlier run stopped half-way (for example, it was cut off before the `finally`
block of evaluate.py) and left a source in its post-DDL state: a column added by ADD is still
there, or a column removed by DROP/RENAME has not come back. The preflight checks then refuse
the evaluation because the base view queries (A007-A009) fail.

The restore issues DDL that the monitors capture too. So run ./restart-ascam.sh AFTER this
script: Knowledge is reset and the Orchestrator starts from the latest message, and the restore
DDL is not treated as a new change.

Usage (from the repository root):
  python3 experiments/evaluation/restore_sources.py
  ./restart-ascam.sh
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenarios import SCENARIOS  # noqa: E402


def main() -> int:
    for code, s in SCENARIOS.items():
        result = s.restore()
        print(f'{code}: {result}')
        if s.prepare:
            # creates the backup table if it is missing (only once the column is back)
            print(f'{code}: backup {s.prepare() or "exists"}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
