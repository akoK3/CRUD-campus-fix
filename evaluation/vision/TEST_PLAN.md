# CampusFix Layer 6: Claude Vision evaluation test plan

| | |
|---|---|
| **Version** | 1.0 (draft) |
| **Date** | 2026-11-05 |
| **Status** | Harness built and tested with mocks. **No live evaluation has been run, and no accuracy result exists.** |
| **Model under test** | `claude-opus-5-5`, configurable with `--model` |
| **Scope** | Offline evaluation of image understanding only. Production routing, the backend, and Layer 5 artifacts are untouched. |

## 1. Objectives

Measure whether Claude Vision can safely turn report photos into **suggestions** for staff. Staff remain the decision makers.

| # | Capability | Primary question |
|---|---|---|
| C1 | Campus issue recognition | When a photo clearly shows an issue, is the suggested issue type acceptable? |
| C2 | Ambiguity and abstention | When several issue types fit, does it abstain instead of guessing? |
| C3 | Multiple images | Does it combine related photos, and notice photos that conflict? |
| C4 | Degraded images | Does it recognize blurry, dark, obstructed, or tiny photos as unusable and ask for a new photo? |
| C5 | Structured output | Is every response valid against the schema and the semantic rules? |
| C6 | Hallucination and safe failure | Does it avoid inventing issues, hazards, or text? Does every failure end with a human? |

Non-goals: priority prediction, department routing, people identification, cause diagnosis, and production latency.

## 2. System under test

- **Request:**
  - The frozen system prompt `prompts.py` (`PROMPT_VERSION = vision-1.0`).
  - Labeled images ("Image 1:", "Image 2:", …) placed before the task text.
  - The optional reporter description, wrapped in `<reporter_description>` and marked as data.
- **Structured outputs:** the response format is constrained with `output_config.format` using `schema/vision_result.schema.json`, and validated again locally (`vision_schema.validate`).
- **Semantic rules S1–S11** (`vision_schema.semantic_errors`): outcome-field coherence, one assessment per image, evidence only from usable and existing images, safety-flag consistency, and conflicting images requiring abstention.
- **Routing decision** (`claude_client.routing_decision`):
  - Anything other than a valid, non-low-confidence identification goes to `MANUAL_REVIEW` or `REQUEST_NEW_PHOTO`.
  - Visible hazards always set `safety_escalation`.
  - The harness only *reports* this decision; production routing is unchanged.
- **Thinking:** adaptive (always on for Opus 5.5).
- **Effort:** set explicitly (default `high`, recorded in metrics).
- **Prompt caching:** a breakpoint on the frozen system prompt. Per-case images are never cached.

## 3. Input constraints (verified against the Claude vision docs, 2026-11-05)

| Constraint | Claude API | CampusFix frontend today | Harness behavior |
|---|---|---|---|
| Formats | JPEG, PNG, GIF (first frame only), WebP | Also accepts **HEIC** | HEIC is rejected locally (`unsupported_media_type`) and routed to manual review |
| Size | **10 MB base64-encoded** per image | 10 MB **raw** file | Checks the base64 size. A raw file of roughly 7.5–10 MB passes the frontend but fails the API |
| Dimensions | ≤ 8000 px; ≤ 3000 px when more than 20 images | — | Rejected locally |
| Small images | Accuracy degrades below 200 px | — | Warning recorded |
| Metadata | EXIF is not read, so orientation is not corrected from metadata | — | Plan includes rotated-photo cases |
| Cost | `⌈w/28⌉×⌈h/28⌉` visual tokens after resize. Opus 5.5 is high-resolution tier (2,576 px long edge, 4,784 tokens) | — | `preflight.resized_size` copies the docs' reference implementation |

Format is detected from magic bytes, never from the file extension.

**These are integration gaps for production, which Layer 6 does not modify:** HEIC conversion, and resizing to ≤ 2,576 px before upload. Resizing also caps cost.

## 4. Test layers

| Layer | What it runs | Cost | When |
|---|---|---|---|
| **L1 Mocked unit tests** (`test_vision_*.py`) | Schema, validator (cross-checked against `jsonschema`), semantic rules, preflight (including the docs' token table), request shape, response interpretation, routing, retries, grading, hallucination checks, runner, and live-run gates | Free, no network | Every change; CI (`python -m unittest discover -s evaluation`) |
| **L2 Mock end-to-end** (`--mock oracle/abstain/overconfident`) | The whole pipeline on synthetic fixtures | Free | Before any live run. The oracle must score 100%, and the null baselines must fail in the documented ways |
| **L3 Estimate** (`--estimate`) | Preflight and token/cost estimate on the real case set | Free, no API | Before requesting live approval |
| **L4 Live dev** (`--live --split dev`) | Real Claude calls on the dev split | Paid; needs approval | After the case set and estimate are approved |
| **L5 Live holdout** (`--live --split holdout --allow-holdout`) | One final run on unseen cases | Paid | Once, after all prompt and schema changes are frozen |

Mocked results are labeled "measure the harness, not Claude", and `is_model_evaluation` is false in their metrics. They may never be written to a `live` directory.

## 5. Case set

### 5.1 Required composition (targets)

| Category | Dev | Holdout | Must include |
|---|---|---|---|
| recognition | 24 | 10 | The top 10 issue types by ticket volume. At least 6 with a visible hazard (water, wiring, glass, smoke). At least 2 rotated photos |
| ambiguous | 8 | 4 | Ceiling stains, generic damage, equipment of unclear type |
| multi_image | 8 | 4 | Wide shot plus close-up (consistent); two unrelated problems (conflicting); partial overlap |
| degraded | 12 | 4 | 3 each of blurry, dark, obstructed, and tiny; where possible, degraded copies of a recognition photo with the same ground truth |
| hallucination | 8 | 4 | Clean scenes with misleading ticket text; scenes with no legible text; a photographed sign containing an instruction (prompt injection) |
| safe_failure | 4 | 2 | A real HEIC photo, a non-image file, an image over the size limit |
| **Total** | **64** | **28** | |

Per-category targets are small: at n=8, a 95% interval spans about ±30 points. Report intervals and do not over-read differences.

### 5.2 Case fields (`cases/cases_template.jsonl`)

| Field | Meaning |
|---|---|
| `case_id`, `category`, `split` | Identity. The split is fixed at creation and never changed |
| `ground_truth_source` | `human_annotated` or `human_verified` for live cases. `synthetic_fixture` only for mocks |
| `images[].path`, `images[].ground_truth_quality` | `usable`, `blurry`, `dark`, `obstructed`, `too_small`, or `irrelevant` |
| `ticket_text` | Optional reporter description (may be misleading on purpose) |
| `expected_outcome` | `issue_identified`, `no_issue_visible`, or `abstain` |
| `acceptable_issue_types` | Every issue type a domain expert would accept. Required for `issue_identified` |
| `acceptable_abstain_reasons` | Reasons that count as correct when abstaining |
| `required_hazards` / `allowed_hazards` | Must be reported / may be reported. Anything else is a false alarm |
| `visible_text` | `null` = not graded; `[]` = no legible text (any transcription is fabricated); otherwise the legible strings |
| `expected_consistency` | Required when there are several images |
| `expected_input_rejection` | For safe_failure cases: the preflight rejection code |

### 5.3 Ground truth protocol

1. **Collection:** photos come from campus staff or the project team with consent.
   - No identifiable faces, licence plates, or personal documents; blur or exclude them.
   - Photos are not committed to Git unless the project decides so explicitly.
2. **Annotation before the model:** two annotators label each case independently, using `CLASSIFICATION_POLICY_v3.md` for issue-type definitions. Disagreements are adjudicated and recorded, and agreement is reported. Labels are frozen before any model output is viewed.
3. **No model-derived labels:** Claude's outputs are never used as ground truth.
4. **Holdout discipline (lesson from Layer 5):** holdout cases are written at the same time as dev cases, stored with `split: holdout`, and run once.

## 6. Metrics and grading

Every check is atomic and recorded per (case, rep) in `grades.jsonl`. Rates carry Wilson 95% intervals.

| Metric | Definition | Capability |
|---|---|---|
| `issue_correct` | Expected issue_identified, and the predicted issue type is in `acceptable_issue_types` | C1 |
| `coverage_on_answerable` / `selective_issue_accuracy` | How often it answers when it should, and how accurate those answers are | C1/C2 |
| abstention precision / recall | Positive class = abstain. Both directions are covered: always-abstain scores 0% `issue_correct` | C2/C4 |
| `abstain_reason_acceptable` | The reason matches the annotation (secondary) | C4 |
| `consistency_correct` | Multi-image consistency label | C3 |
| `image_quality_accuracy` | Per-image usable/unusable versus ground truth | C4 |
| `valid_output` | status = ok (schema- and semantics-valid). Refusals and truncations are excluded | C5 |
| `confident_misidentification` | Identified an issue with high or medium confidence where none should be identified | C6 |
| `confident_wrong_issue` | Identified the wrong issue type with high confidence | C6 |
| `hazard_false_alarm` / `safety_miss` | A hazard outside required ∪ allowed / a required hazard without safety escalation | C6 |
| `fabricated_text` | A transcription that is not among the legible strings | C6 |
| `any_hallucination` | Any of the four hallucination checks above | C6 |
| `safe_input_rejection_correct` | A preflight rejection matches the expected code, and no API call is made | C6 |
| `routed_to_human_on_failure` | Every non-ok status ends in MANUAL_REVIEW | C6 |
| `refusal`, `truncated` | Their own rates, never counted as wrong answers | C5/C6 |
| infra errors | Recorded in `errors.jsonl` by kind and attempts, never scored | — |
| `rep_outcome_agreement` | Same outcome-correctness across reps | stability |

**Harness integrity rules:**
- The served model must equal the requested model. A fallback-served or rerouted answer becomes `model_mismatch` and is not scored.
- Retries use jittered backoff and the attempt count is recorded. Authentication and permission errors abort the run.
- Trajectories store image hashes, never image data.
- Cost comes from `usage`, never from estimates.

### 6.1 Acceptance criteria (to approve before the first live run, not after)

These are proposed thresholds, not results:
- `safety_miss` = 0 on recognition cases with hazards.
- `confident_misidentification` ≤ 5% on hallucination and degraded cases.
- `valid_output` ≥ 99%.
- `routed_to_human_on_failure` = 100%.
- `safe_input_rejection_correct` = 100%.
- `issue_correct` reported with its interval. No accuracy threshold is set until a baseline exists.

## 7. Procedure

1. `python -m unittest discover -s evaluation`: all L1 tests pass.
2. Build the fixtures with `python evaluation/vision/synthetic_fixtures.py <dir>`. Then run `--mock oracle`, `--mock abstain`, and `--mock overconfident`, and confirm the oracle scores 100% while the baselines fail.
3. Collect and annotate photos (§5.3), then write `cases/live_cases.jsonl` from the template.
4. Run `--estimate` on the dev split and share the cost estimate for approval.
5. With approval: set `CAMPUSFIX_VISION_LIVE_APPROVED=yes` and run `--live --split dev --budget-usd <approved> --reps 3 --out evaluation/vision/results/live/<date>`.
6. Spot-check at least 10 graded failures by hand. If more than 1 in 10 is a grader or label error, fix the case or grader and re-run (no prompt changes in the same step).
7. Iterate on the prompt or schema using dev only. Bump `PROMPT_VERSION` on every change.
8. Run the holdout once at the end.

## 8. Risks and limitations

- The vision documentation lists limits on people identification, spatial precision, counting, AI-generated image detection, and low-quality, rotated, or small images. The plan tests the last group directly.
- A small, single-campus photo set does not establish production accuracy.
- The cost estimate assumes about 1,800 prompt tokens and 2,500 output tokens per case. Confirm with `count_tokens` and the first live rows.
- The docs' example table and their reference implementation differ by 1 px for 2000×1500 on the standard tier, with identical token cost. The harness follows the reference implementation.
- Server-side refusal fallbacks are **off** by default in the eval, so refusals are measured as refusals. With `--fallbacks`, fallback-served rows are excluded as `model_mismatch`. For a production integration, fallbacks would be enabled (`fallbacks: "default"`).
