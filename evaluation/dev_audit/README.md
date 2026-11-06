# Development-split audit

This is an offline analysis of the **historical development predictions** (187 cases) for DeepSeek-R1 8B, Qwen3 14B, and Laya. It is separate from, and does not modify, the frozen holdout archive in the parent directory.

- `analyze_development.py`: a standard-library-only scorer. It never calls a model or the network, and never scores the holdout.
- `test_analyze_development.py`: metric correctness, edge-case, validation, and leak-guard tests.
- `dev_inputs.sha256.json`: SHA-256 pins for the three input prediction files.
- `results/dev_metrics.json`, `results/dev_tables.md`: generated outputs. Do not edit them by hand.
- `DEV_AUDIT_REPORT.md`: findings, representative errors, root-cause hypotheses, and experiments.
- `LABEL_REVIEW.md`: the development-label quality review (30 flagged cases, decisions D1–D9).
- `CLASSIFICATION_POLICY_v3.md`: the versioned policy draft (`3.0.0-draft`).
- `policy_v3.py`: hard policy checks plus D1–D9 lint.
- `build_v3_candidate.py`: builds a v3 candidate from APPROVED proposals only.
- `test_policy_v3.py`: tests for the policy checks and the migration gates.
- `v3_proposal/`: proposed v3 labels, the changelog, lint exceptions, and `MIGRATION_PROPOSAL.md`. Everything there is PENDING approval.

## Run

From the repository root:

```powershell
python evaluation/dev_audit/analyze_development.py --check                       # verify committed outputs
python evaluation/dev_audit/analyze_development.py --out evaluation/dev_audit/results   # regenerate
python -m unittest discover -s evaluation -v                                     # all evaluation tests
```

## Inputs

The input prediction files live under `backend/evaluation_results_*`, which is **gitignored**. On a clean clone, the data-dependent tests skip, and `run()` / `--check` fail with `FileNotFoundError`. The pure metric and validation tests still run.

## Safety rules enforced in code

- The benchmark must match `../artifacts.sha256.json`.
- Holdout rows are discarded immediately after their IDs are recorded.
- A prediction file containing any holdout case ID is rejected (`HoldoutLeakError`).
- Paths under `evaluation/frozen/`, and any path whose name contains "holdout", are refused.
- Outputs cannot be written to `evaluation/`, `evaluation/frozen/`, the repository root, or `backend/`.
