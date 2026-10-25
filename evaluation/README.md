# Frozen CampusFix AI comparison

This directory is the intentional evaluation archive for the pre-RAG baseline.
It contains **synthetic tickets, not real campus production reports**.

## Split and freeze policy

The dataset has **249 unique cases: 187 development and 62 holdout**. Development
used only the development split. The untouched holdout was used once for the
final DeepSeek-R1 8B versus Qwen3 8B model-selection comparison. It is now a
historical test set, not an available development or tuning set.

Do not tune prompts, policy rules, model choice, RAG, or thresholds against these
holdout texts or labels. Do not rerun inference against them to select future
changes. Use development/new data for iteration and reserve a new independently
held-out set for a future final evaluation. CI only rescores the existing files.

## Artifacts

- `campusfix_benchmark_v2.csv`: original 249 cases, IDs, labels, and split.
- `evaluate_holdout_models.py`: exact historical evaluator used for both models;
  retained unchanged for provenance, not a production dependency.
- `frozen/deepseek-r1_8b_holdout_predictions.csv`: all 62 original DeepSeek rows.
- `frozen/qwen3_8b_holdout_predictions.csv`: all 62 original Qwen rows.
- `artifacts.sha256.json`: byte-level hashes of those four original artifacts.
- `.gitattributes`: prevents newline conversion of the hashed originals.
- `metrics.json`: full-precision scores computed from the frozen predictions.
- `reproduce_metrics.py`: offline, standard-library-only integrity/metrics replay.
- `requirements.txt`: optional, isolated dependencies for the historical evaluator.

The four source artifacts were copied byte-for-byte from the completed project.
No predictions, dataset rows, labels, prompts, or split assignments were edited.
Old experiments, Laya dependencies, intermediate outputs, and backups are excluded.

## Recorded results on 62 holdout cases

| Model | Issue accuracy | Department accuracy | Priority accuracy | Issue macro-F1 | Department macro-F1 | Priority macro-F1 | Mean latency |
|---|---:|---:|---:|---:|---:|---:|---:|
| **DeepSeek-R1 8B (selected)** | **82.3% (51/62)** | **82.3% (51/62)** | **64.5% (40/62)** | 0.8071 | 0.7246 | 0.5906 | 19.3657 s |
| Qwen3 8B (comparison only) | 80.6% (50/62) | 85.5% (53/62) | 54.8% (34/62) | 0.8015 | 0.7653 | 0.4422 | 2.7073 s |

Median recorded latency: DeepSeek 17.7635 s; Qwen 2.5257 s. Accuracy is exact
matches divided by 62. Macro-F1 is the unweighted mean over the union of expected
and predicted labels for each task, with zero division scored as zero, matching
`sklearn.metrics.f1_score(..., average="macro", zero_division=0)`.

Latency is the saved per-case wall time in the historical evaluator, including
its request/parsing/error handling. It is not Netlify/browser latency, enrichment
latency, or a promised response time. No new timing run was performed. The
historical evaluator uses its fixed issue-to-department mapping; these scores do
not measure every API emergency/access exception, authentication, or enrichment.

## Reproduce metrics safely (no inference, network, packages, or credentials)

From the repository root with Python 3.11+:

```powershell
py -3.11 evaluation/reproduce_metrics.py --check
py -3.11 -m unittest discover -s evaluation -v
```

Any Python 3.11+ executable can replace `py -3.11`. The scorer checks all four
hashes, unique case IDs, exact split sizes, complete prediction coverage, label
alignment, finite latencies, and equality with `metrics.json`. It only reads
files and prints JSON. It does not load `.env`, contact Supabase/Ollama, or write
predictions. These commands reproduce the recorded metrics, not a new holdout run.

## Historical inference provenance and limits

The evaluator used the same prompt, taxonomy/mapping, zero temperature,
`think=false`, non-streaming Ollama `/api/chat`, and 180-second request timeout
for each model. `OLLAMA_MODEL` selected `deepseek-r1:8b` or `qwen3:8b`. Source
copies of the evaluator with and without the CampusFix prefix are byte-identical.

Do **not** execute `evaluate_holdout_models.py` against this frozen dataset.
It is archived unchanged, so its main routine still performs holdout inference.
Its output directory is separate from `frozen/` and ignored by Git. Future
inference experiments need a separately prepared dataset and separately reviewed
evaluator/output path; do not edit this historical evaluator or overwrite results.
If studying the evaluator in isolation, its optional packages are installed with
`python -m pip install -r evaluation/requirements.txt` in a separate environment.
They are not needed by production or offline metrics replay.

The original run did not record a hardware specification, Ollama version,
immutable weight digest, or full dependency lock. Model tags can change and local
inference timing/output can vary. Saved predictions and metrics are exactly
reproducible; bit-identical new model inference is not claimed. The optional
pinned requirements describe a replay environment, not recovered historical
package versions. A small synthetic holdout is not evidence of production accuracy.
