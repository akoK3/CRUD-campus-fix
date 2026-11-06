# Benchmark v2 → v3 migration proposal

**Status: PROPOSED. Nothing is approved and v3 is not finalized.** All 38 proposal rows and 3 lint exceptions have `approval=PENDING`. `campusfix_benchmark_v2.csv`, the frozen holdout, and all predictions are unchanged. No `campusfix_benchmark_v3.csv` exists.

| Artifact | Purpose |
|---|---|
| [`../CLASSIFICATION_POLICY_v3.md`](../CLASSIFICATION_POLICY_v3.md) | Versioned policy draft `3.0.0-draft` (D1–D9 plus rule IDs) |
| [`proposed_v3_labels.csv`](proposed_v3_labels.csv) | **Source of truth.** One row per affected case, with an `approval` column |
| [`changelog_v2_to_v3.csv`](changelog_v2_to_v3.csv) | Field-level changelog, generated from the proposals (59 entries) |
| [`lint_exceptions.csv`](lint_exceptions.csv) | Justified policy-lint warnings that would remain after migration |
| [`../policy_v3.py`](../policy_v3.py) | Hard policy checks and D1–D9 lint |
| [`../build_v3_candidate.py`](../build_v3_candidate.py) | Builds a candidate from APPROVED rows only; refuses otherwise |

## 1. Scope of the migration

- **Development split only (187 cases).** The frozen 62-case holdout remains a historical v2 artifact and is not relabeled. A new holdout should be written under policy v3 (confirm in U-16).
- **Schema:** v2 columns plus `secondary_issue_type`, `safety_escalation`, `accessibility_escalation`, `review_reason`, `policy_version`, `policy_refs`, and `v2_changed_fields`.
- **Text is never edited.** Where text is ambiguous (CFB-0248, -0190, -0100), the recommendation is a *new* case in a future release, not a rewrite.

## 2. Summary of proposed changes

| | Count |
|---|---|
| Proposal rows | 38 (30 flagged + 8 metadata-only) |
| Cases with **label** changes | **14** (22 field changes: 8 priority, 6 issue type, 6 department, 2 action) |
| Cases reviewed: keep labels, annotate only | 12 (D7/D8/D9 flags, review reasons, noise-type fixes) |
| Cases reviewed: keep, no change | 7 |
| Cases left **unresolved**, keeping v2 labels | 5 (CFB-0033, -0111, -0136, -0192, -0248) |
| Metadata (`noise_type`) fixes | 3 |

The **30 flagged cases** from `LABEL_REVIEW.md` break down as 14 CHANGE, 4 ANNOTATE, 7 KEEP, and 5 UNRESOLVED.

**Expected-label distribution, development split, if every CHANGE is approved:**

| | v2 | v3 proposal |
|---|---|---|
| NEEDS_REVIEW cases | 11 | 9 (0205 and 0207 move to URGENT under D2) |
| URGENT | 36 | 38 (+0021, +0205, +0207, −0100) |
| HIGH | 39 | 38 (+0158, +0100, −0021, −0017, −0119) |
| MEDIUM | 55 | 55 (+0017, +0119, −0158, −0029) |
| LOW | 46 | 47 (+0029) |

## 3. Proposed label changes (every label change from v2)

| Case | Change | New fields | Policy | Confidence | Open item |
|---|---|---|---|---|---|
| CFB-0021 | priority: HIGH → **URGENT** | safety_escalation=true | D8, R-PRI-URG | Medium | U-04 |
| CFB-0158 | priority: MEDIUM → **HIGH** | — | R-PRI-SEC | High | — |
| CFB-0100 | priority: URGENT → **HIGH** | — | D1 | Low | U-01 |
| CFB-0017 | priority: HIGH → **MEDIUM** | — | D5, R-PRI-BASE | Medium | — |
| CFB-0029 | priority: MEDIUM → **LOW** | — | R-PRI-BASE | Medium | — |
| CFB-0119 | priority: HIGH → **MEDIUM** | — | D5 | Low | U-07 |
| CFB-0205 | issue: Other → **Security Concern**<br>dept: Other / Review → **Campus Safety**<br>priority: NEEDS_REVIEW → **URGENT**<br>action: NEEDS_REVIEW → **AUTO_ROUTE** | secondary=Laundry, safety=true | D2, D4 | Medium | U-13 |
| CFB-0207 | issue: Other → **Plumbing**<br>dept: Other / Review → **Facilities**<br>priority: NEEDS_REVIEW → **URGENT**<br>action: NEEDS_REVIEW → **AUTO_ROUTE** | secondary=Wi-Fi / Network, safety=true | D2 | Medium | U-13 |
| CFB-0112 | issue: Housing / Roommate → **Security Concern**<br>dept: Residence Life → **Campus Safety** | safety=true | D4 | High | — |
| CFB-0154 | issue: Lost and Found → **Security Concern** | safety=true | D4, R-ISS-SEC | Medium | U-09 |
| CFB-0142 | issue: ID Card → **Security Concern** | safety=true | D4, R-ISS-SEC | Low | U-09 |
| CFB-0236 | issue: Furniture → **Residence Maintenance**<br>dept: Facilities → **Residence Life** | — | D3 | Medium | — |
| CFB-0178 | dept: IT → **Facilities** | safety=true | D6 | Medium | U-11 |
| CFB-0118 | dept: Residence Life → **Facilities** | safety=true | D6 | Medium | U-11 |

The per-case justifications are in `proposed_v3_labels.csv`, column `justification`.

## 4. Reviewed: labels kept

| Case | Decision | Note |
|---|---|---|
| CFB-0233 | ANNOTATE `safety_escalation=true` | D8 anchor (no lights on emergency stairs), URGENT kept |
| CFB-0063, CFB-0065 | ANNOTATE `accessibility_escalation=true` | D9; blocked from class, nobody stranded ⇒ HIGH kept |
| CFB-0203 | ANNOTATE `review_reason=insufficient_detail` | D7; Elevator / Facilities preserved |
| CFB-0130, -0172, -0184, -0190, -0235, -0246 | KEEP URGENT | Meet D1 (wide scope + imminent deadline or essential service) |
| CFB-0167 | KEEP MEDIUM | D5: repetition of a service-quality complaint is not material |
| CFB-0193–0198 (5), CFB-0204, -0206, -0208 | ANNOTATE `review_reason` | Not flagged; required by R-REV-2. CFB-0195, -0196, -0198 also get noise_type → `none` |

## 5. Unresolved decisions requiring approval

Every item below needs an explicit decision before v3 is finalized. Each lists the proposal currently encoded and the alternative.

| ID | Question | Cases | Encoded proposal | Alternative |
|---|---|---|---|---|
| **U-01** | Does "time-sensitive" without a stated window count as an imminent deadline under D1? | 0100 | No ⇒ HIGH | Yes ⇒ keep URGENT |
| **U-02** | Does "shortly before a hard deadline closes" count as imminent? | 0190 | Yes ⇒ keep URGENT | No ⇒ HIGH |
| **U-03** | "All printers are down…" has no stated scope. Keep HIGH, or read it as campus-wide? | 0248 | Keep HIGH; add an explicit new case later | URGENT |
| **U-04** | Is every stairwell an emergency-egress route, and does "most lights failed, poor visibility" meet D8's "materially unsafe"? | 0021 | Yes ⇒ URGENT | No ⇒ keep HIGH |
| **U-05** | An entrance that cannot be secured at closing, with no time given: HIGH or URGENT? | 0033 | Keep HIGH | URGENT |
| **U-06** | "Unsafe" with no described threat (R-SAF-4): HIGH plus welfare check, or URGENT? | 0111 | Keep HIGH | URGENT |
| **U-07** | Is repeated loss on a single machine "material impact" under D5? | 0119 | No ⇒ MEDIUM | Yes ⇒ keep HIGH |
| **U-08** | **D5 side effects outside the 30 flagged cases** (found by lint). | 0057, 0059, 0214 | Keep all three (0057 food safety; 0059 unresolved infestation; 0214 depends on whether water waste counts) | 0059 → MEDIUM; 0214 → LOW (its `label_reason` encodes the old v2 rule) |
| **U-09** | Issue type for threats: dangerous found item; stolen ID in active use. | 0154, 0142 | Security Concern for both | 0154 → Safety Hazard; 0142 → keep ID Card |
| **U-10** | Dual-valid issue types: obstructing vehicle (Parking vs Safety Hazard); refund question (Financial Aid vs Billing). Adopt an optional `alternate_issue_types` scoring field? | 0136, 0192 | Keep v2 labels; no new field | Add the field, or relabel 0192 → Billing |
| **U-11** | D6 representation: keep the equipment issue type with a Facilities override, or relabel the issue as Electrical? Should visible smoke go to Campus Safety instead of Facilities? | 0178, 0118 | Equipment issue + Facilities | Electrical / Facilities; or Campus Safety for 0118 |
| **U-12** | D7 conflicts with production, which rewrites every review decision to `Other / Other / Review`. Change production (separate review), or score the category leniently? | 0203 | Benchmark keeps Elevator; production unchanged here | Keep `Other` in the benchmark to match production |
| **U-13** | D2 action vocabulary and follow-up: is `AUTO_ROUTE + safety_escalation` sufficient, or add an `ESCALATE` action? How is the secondary issue scored and ticketed? | 0205, 0207 | AUTO_ROUTE + flag; secondary not scored | New action value; score secondary as a separate check |
| **U-14** | Does D9 apply to a *planned* outage of the only accessible route? | 0216 (LOW, planned elevator outage) | Not assessed | Accessibility escalation with LOW priority |
| **U-15** | `safety_escalation` and `accessibility_escalation` are blank (not assessed) on the 157 rows outside this review. Is a labeling pass needed before v3, or is blank acceptable for v3.0? | 157 rows | Blank allowed in 3.0.0 | Full pass before release |
| **U-16** | Confirm v3 is **development-only**, and that a new holdout will be written under policy v3. | all | Development-only | Include frozen holdout rows unchanged (not recommended) |
| **U-17** | Evaluator compatibility: `analyze_development.py` is pinned to v2. Scoring old predictions against v3 changes the target, so results are not comparable with the v2 baseline. | — | Report v2 and v3 scores side by side after approval | — |

## 6. Approval procedure

1. Decide U-01 to U-17. Where a decision differs from the encoded proposal, edit that row in `proposed_v3_labels.csv`.
2. Set `approval` to `APPROVED` or `REJECTED` on every row of `proposed_v3_labels.csv` and `lint_exceptions.csv`.
3. Run the commands below. The builder refuses to write if any hard check fails, any lint warning lacks an approved exception, or (for the final file name) anything is still PENDING.
   ```powershell
   python evaluation/dev_audit/build_v3_candidate.py --write-changelog --preview
   python -m unittest discover -s evaluation -v
   python evaluation/dev_audit/build_v3_candidate.py --out evaluation/campusfix_benchmark_v3.csv
   ```
4. Hash the new file in a new manifest. Leave `artifacts.sha256.json` (v2) untouched.
5. Re-score the saved development predictions against v3, and report v2 and v3 metrics side by side (U-17).
