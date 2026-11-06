# CampusFix Layer 6: Claude Vision evaluation harness

This is an isolated, offline-first harness for evaluating Claude Vision on campus issue photos. It is **not** a production dependency: nothing in `backend/` or `frontend/` imports it, and `anthropic` is imported only by the live client.

**Status:** mocked tests pass. **No live Claude evaluation has been run; there are no accuracy numbers.** See [TEST_PLAN.md](TEST_PLAN.md).

| File | Purpose |
|---|---|
| `schema/vision_result.schema.json` | Output contract (structured-outputs-compatible JSON Schema) |
| `vision_schema.py` | Schema loader, stdlib validator, semantic rules S1–S11 |
| `preflight.py` | Format sniffing, API limits, the docs' resize and visual-token reference implementation |
| `prompts.py` | Frozen system prompt (`PROMPT_VERSION`) and request content builder |
| `claude_client.py` | Live Claude client (lazy SDK import), response interpretation, safe routing, retries, cost |
| `mock_clients.py` | Oracle, always-abstain, overconfident, and scripted offline clients |
| `grader.py` | Case validation, atomic checks, hallucination detection, aggregation with Wilson intervals |
| `run_vision_eval.py` | CLI runner: `--mock`, `--estimate`, `--live` (gated) |
| `synthetic_fixtures.py` | Generates placeholder images and a mock case set (harness tests only) |
| `cases/cases_template.jsonl` | Template for the real, human-annotated case set |
| `test_vision_schema_preflight.py`, `test_vision_harness.py` | Mocked tests: no network, no SDK needed |
| `requirements-live.txt` | Only needed for `--live` |

## Run (repository root)

```powershell
python -m unittest discover -s evaluation -v                                  # all mocked tests
python evaluation/vision/synthetic_fixtures.py $env:TEMP\cf_vision             # placeholder fixtures
python evaluation/vision/run_vision_eval.py --cases $env:TEMP\cf_vision\cases.jsonl --mock oracle --out $env:TEMP\cf_vision_out
python evaluation/vision/run_vision_eval.py --cases evaluation/vision/cases/live_cases.jsonl --estimate
```

## Live runs

A live run requires all three of the following. The runner refuses otherwise, and stops when recorded spend reaches the budget:

1. a `--live` flag;
2. the environment variable `CAMPUSFIX_VISION_LIVE_APPROVED=yes`, set after the `--estimate` output is approved;
3. `--budget-usd N`, which must be at least the estimate.

```powershell
python -m pip install -r evaluation/vision/requirements-live.txt   # separate environment, not the backend venv
$env:CAMPUSFIX_VISION_LIVE_APPROVED = "yes"
python evaluation/vision/run_vision_eval.py --cases evaluation/vision/cases/live_cases.jsonl --live --budget-usd 5 --reps 3 --out evaluation/vision/results/live/2026-11-XX
```

Credentials are resolved by the SDK (`ANTHROPIC_API_KEY` or an `ant auth login` profile). Never put keys in this directory.

Outputs: `results.jsonl` (scored rows), `errors.jsonl` (infrastructure failures, never scored), `trajectories.jsonl` (requests with image hashes instead of data, plus the raw model text), `grades.jsonl`, `metrics.json`, and `report.md`.
