#!/usr/bin/env bash
# =============================================================================
# run-evaluation.sh — clean down, start again, then the evaluation and its analysis, in one go.
#
#   1. every container and volume is taken down, then the OBDF and ASCAM are started from
#      scratch (start.sh: source data rebuilt from init.sql, mapping and ontology from git)
#   2. baseline + treatment evaluation (evaluate.py)
#   3. analysis of the results (analyze.py), also saved to a file
#
# Usage (from the repository root):
#   ./run-evaluation.sh            20 treatment runs, 5 baseline runs (the paper's numbers)
#   ./run-evaluation.sh 2 1        few runs, for a trial
# All output is also written to log-evaluation-<time>.txt.
# =============================================================================
set -Eeuo pipefail
cd "$(dirname "$0")"

RUNS="${1:-20}"
BASELINE="${2:-5}"
LOG="log-evaluation-$(date +%Y%m%dT%H%M%S).txt"
exec > >(tee "$LOG") 2>&1

echo "==== [1/3] clean down, then start again (start.sh) ===="
./start.sh

echo; echo "==== [2/3] evaluation: $RUNS treatment runs, $BASELINE baseline runs ===="
BEFORE="$(ls -1 results/evaluation 2>/dev/null | tail -n 1 || true)"
python3 experiments/evaluation/evaluate.py --mode both --runs "$RUNS" --baseline-runs "$BASELINE"
RESULT="$(ls -1 results/evaluation | tail -n 1)"
if [ "$RESULT" = "$BEFORE" ]; then
  echo "the evaluation produced no new folder"; exit 1
fi

echo; echo "==== [3/3] analysis of results/evaluation/$RESULT ===="
python3 experiments/evaluation/analyze.py "results/evaluation/$RESULT" | tee "results/evaluation/$RESULT/analysis.md"

echo
echo "Done. Send this file: results/evaluation/$RESULT/analysis.md  (full log: $LOG)"
