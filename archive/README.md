# Archive

Files that the final system no longer uses, kept because the thesis and the design decisions
refer to them as evidence. They are stored exactly as they were run, so their comments and output
are in Indonesian and their paths may point to the old layout (for example `experiments/f6/` or
`run.sh`). None of them is needed to run ASCAM or its evaluation.

| Path | What it is |
|---|---|
| `setup/adaptive-engine/` | Iteration-1 adaptation engine (edited `mapping.obda`, deployed through the shared folder); retired by ADR-0010 |
| `setup/query.md`, `setup/simulation.md` | Notes on manual queries and simulations from iteration 1 |
| `experiments/f0/` | Feasibility tests F0.2–F0.8 behind ADR-0002 to ADR-0006 and ADR-0023 (Teiid `ALTER`, system tables, management API and VDB versions, R2RML validation, view dependencies, `ALTER VIEW`) |
| `experiments/f1/` | Probes of the artifact sources and of the Kafka messages (ADR-0009, ADR-0016) |
| `experiments/f3/` | End-to-end check of the Event Normalizer |
| `experiments/f4/` | Execution and artifact-edit previews (ADR-0019, ADR-0020) |
| `experiments/f5/` | Checks of Teiid restarts and VDB persistence (ADR-0020) |
| `experiments/queries/`, `*.py`, `*.sh` | Iteration-1 experiment runner, aggregation, and SPARQL helpers |
| `example/` | Stand-alone example of a Teiid multi-source model, separate from the case-study OBDF |
| `scripts/` | Iteration-1 start, reset, and stop scripts (replaced by `start.sh`, `restart-ascam.sh`, and `stop.sh`) |

The architecture decision records themselves were condensed into `docs/design-decisions.md`; the
original files are in the git history (`docs/adr/`, commit `a1e9b0a`).
