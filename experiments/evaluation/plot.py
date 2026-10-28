"""
Draws the breakdown of the adaptation time Δt_adapt per operator from summary.json.

Each stacked bar holds the median of every component, from the DDL execution until the
adaptation is complete: detection, the gap before the execution (derived: the median Δt_adapt
minus the medians of the other components), deploying the VDB, validation, starting or
restarting Ontop, verification, and the switch. Grey fills and hatching keep the figure legible
in black-and-white print.

Requirements: run extract.py first, and install matplotlib
  python3 -m pip install --user matplotlib

Usage (from the repository root):
  python3 experiments/evaluation/plot.py                                   # latest result directory
  python3 experiments/evaluation/plot.py results/evaluation/20260924T141111
Output: <directory>/summary/fig_adaptation_time.{png,svg,pdf}
"""
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import rcParams  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
# bottom to top; the PostgreSQL/MySQL pair of every operator sits together, ADD at the top
ORDER = ['a008', 'a007', 'a003', 'a006', 'a005', 'a002', 'a004', 'a001']
# Paper numbering: A001-A003 PostgreSQL, A004-A006 MySQL, so harness a006 = A003 and a003 = A006.
PAPER = {'a001': 'A001', 'a002': 'A002', 'a006': 'A003', 'a004': 'A004', 'a005': 'A005', 'a003': 'A006'}
LABEL = {'a001': 'ADD, PostgreSQL (A001)', 'a004': 'ADD, MySQL (A004)',
         'a002': 'DROP, PostgreSQL (A002)', 'a005': 'DROP, MySQL (A005)',
         'a006': 'RENAME, PostgreSQL (A003)', 'a003': 'RENAME, MySQL (A006)',
         'a007': 'DROP via view, PostgreSQL (A007)', 'a008': 'DROP via view, MySQL (A008)'}
# A009 is not drawn: its change is not adapted, so it has no Δt_adapt.

# (legend label, key in summary.json, fill colour, hatch); a label starting with "_" has no legend entry
# Restart strategy (ADR-0001): the single Ontop is reloaded after the VDB switch.
RESTART_COMPONENTS = [
    ('Detection', 'detection', '#ffffff', '////'),
    ('Ingestion and planning', 'derived_gap_median', '#e6e6e6', ''),
    ('Deploy / switch VDB', 'deploy_vdb', '#000000', ''),
    ('Validate', 'validate', '#a6a6a6', ''),
    ('_switch', 'switch', '#000000', ''),             # < 5 ms, merged into the deploy/switch entry
    ('Restart Ontop', 'reload_ontop', '#595959', ''),
    ('Verify', 'verify', '#ffffff', '....'),
]
# Blue-green strategy (ADR-0022): the standby instance is started and verified before the switch.
BLUEGREEN_COMPONENTS = [
    ('Detection', 'detection', '#ffffff', '////'),
    ('Ingestion and planning', 'derived_gap_median', '#e6e6e6', ''),
    ('Deploy VDB', 'deploy_vdb', '#000000', ''),
    ('Validate', 'validate', '#a6a6a6', ''),
    ('Start standby Ontop', 'start_ontop', '#595959', ''),
    ('Verify', 'verify', '#ffffff', '....'),
    ('Switch', 'switch', '#d9d9d9', 'xxxx'),
]


def components_for(summary: dict, codes: list[str]) -> list[tuple]:
    bluegreen = any((summary[k]['times_ms'].get('start_ontop') or {}) for k in codes)
    return BLUEGREEN_COMPONENTS if bluegreen else RESTART_COMPONENTS


def pick_directory() -> Path:
    if len(sys.argv) > 1:
        d = Path(sys.argv[1])
        return d if d.is_absolute() else ROOT / d
    candidates = sorted(p for p in (ROOT / 'results/evaluation').glob('*') if p.is_dir())
    if not candidates:
        sys.exit('no result directory in results/evaluation')
    return candidates[-1]


def median_seconds(times: dict, key: str) -> float:
    value = times.get(key)
    if isinstance(value, dict):
        return value['median'] / 1000
    return (value or 0) / 1000                         # derived_gap_median is a plain number


def main() -> int:
    directory = pick_directory()
    file = directory / 'summary' / 'summary.json'
    if not file.exists():
        sys.exit(f'{file.relative_to(ROOT)} does not exist yet; run extract.py first')
    summary = json.loads(file.read_text())['scenarios']
    codes = [k for k in ORDER if k in summary and summary[k].get('succeeded')
             and (summary[k].get('times_ms') or {}).get('dt_adapt')]
    if not codes:
        sys.exit('no successful treatment scenario to draw')

    rcParams.update({'font.family': 'serif',
                     'font.serif': ['Times New Roman', 'Liberation Serif', 'DejaVu Serif'],
                     'font.size': 8, 'axes.linewidth': 0.6, 'hatch.linewidth': 0.5})
    fig, ax = plt.subplots(figsize=(6.1, 0.35 + 0.53 * len(codes)), dpi=300)

    y_labels = [LABEL[k] for k in codes]
    left = [0.0] * len(codes)
    for label, key, colour, hatch in components_for(summary, codes):
        values = [max(median_seconds(summary[k]['times_ms'], key), 0.0) for k in codes]
        ax.barh(y_labels, values, left=left, color=colour, edgecolor='black', linewidth=0.5,
                hatch=hatch, height=0.55, label=label)
        left = [a + b for a, b in zip(left, values)]

    # No interquartile lines and no numbers on the bars: the values go into the figure caption.
    right_edge = max(summary[k]['times_ms']['dt_adapt']['median'] / 1000 for k in codes)
    ax.set_xlim(0, (int(right_edge / 5) + 1) * 5)
    ax.set_xlabel('Time from DDL execution (s)')
    ax.xaxis.grid(True, linewidth=0.3, color='#bbbbbb')
    ax.set_axisbelow(True)
    for side in ('top', 'right'):
        ax.spines[side].set_visible(False)
    ax.legend(ncol=4, fontsize=6.6, frameon=False, loc='upper center',
              bbox_to_anchor=(0.46, -0.62 / (0.53 * len(codes))),   # fixed gap below the axis
              handlelength=1.5, columnspacing=0.9, handletextpad=0.4)
    fig.tight_layout()

    out = directory / 'summary'
    for extension in ('png', 'svg', 'pdf'):
        fig.savefig(out / f'fig_adaptation_time.{extension}', dpi=300, bbox_inches='tight')
    print('Values for the figure caption (median, seconds):')
    for k in reversed(codes):
        w = summary[k]['times_ms']
        d = lambda n: median_seconds(w, n)
        print(f"  {PAPER.get(k, k)}: total {d('dt_adapt'):.1f}; detection {d('detection'):.1f}; "
              f"ingestion and planning {d('derived_gap_median'):.1f}; validate {d('validate'):.1f}; "
              f"restart Ontop {d('reload_ontop'):.1f}; start standby Ontop {d('start_ontop'):.1f}; "
              f"verify {d('verify'):.1f}; "
              f"deploy and switch {d('deploy_vdb') + d('switch'):.2f}")
    print(f'Figure written to {out.relative_to(ROOT)}/fig_adaptation_time.{{png,svg,pdf}}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
