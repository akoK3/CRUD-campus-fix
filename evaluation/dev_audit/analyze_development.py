"""Offline audit of historical CampusFix AI predictions on the 187-case development split.

Guarantees
----------
* Read-only with respect to every existing artifact. Outputs go only to an explicit
  ``--out`` directory, which may not be the frozen archive or any hashed artifact.
* Never calls a model, the network, Supabase, or Ollama. Standard library only.
* Never scores the holdout. The benchmark is hash-checked against the frozen
  manifest, holdout rows are discarded immediately after their IDs are recorded,
  and any prediction file containing a holdout case ID is rejected outright.
  Paths under ``evaluation/frozen`` or whose name mentions "holdout" are refused.

Usage (from the repository root)::

    python evaluation/dev_audit/analyze_development.py                 # print JSON
    python evaluation/dev_audit/analyze_development.py --out evaluation/dev_audit/results
    python evaluation/dev_audit/analyze_development.py --check         # compare to results/
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import statistics
from typing import Iterable, Sequence

HERE = Path(__file__).resolve().parent
EVAL_DIR = HERE.parent
REPO_ROOT = EVAL_DIR.parent
DATASET = EVAL_DIR / "campusfix_benchmark_v2.csv"
FROZEN_MANIFEST = EVAL_DIR / "artifacts.sha256.json"
INPUT_MANIFEST = HERE / "dev_inputs.sha256.json"
FROZEN_DIR = EVAL_DIR / "frozen"
RESULTS_DIR = HERE / "results"

AXES = ("issue_type", "department", "priority")
ORDINAL = ("LOW", "MEDIUM", "HIGH", "URGENT")
RANK = {label: index for index, label in enumerate(ORDINAL)}
REVIEW = "NEEDS_REVIEW"
AUTO = "AUTO_ROUTE"
PRIORITIES_5 = frozenset(ORDINAL + (REVIEW,))
PRIORITIES_4 = frozenset(ORDINAL)
INVALID = "__INVALID__"
EXPECTED_SPLITS = {"development": 187, "holdout": 62}

# Copied verbatim from the archived evaluator (evaluation/evaluate_holdout_models.py).
# A test asserts equality by parsing that file's source, without importing it.
ISSUE_TO_DEPT = {
    "Plumbing": "Facilities", "Electrical": "Facilities", "Heating / Cooling": "Facilities",
    "Lighting": "Facilities", "Furniture": "Facilities", "Door / Lock": "Facilities",
    "Window": "Facilities", "Structural": "Facilities", "Cleaning / Custodial": "Facilities",
    "Pest Control": "Facilities", "Elevator": "Facilities", "Wi-Fi / Network": "IT",
    "Classroom Technology": "IT", "Computer / Hardware": "IT", "Account / Login": "IT",
    "Software": "IT", "Printing": "IT", "Residence Maintenance": "Residence Life",
    "Housing / Roommate": "Residence Life", "Laundry": "Residence Life",
    "Event Support": "Student Engagement", "Club / Organization": "Student Engagement",
    "Parking": "Campus Safety", "ID Card": "Campus Safety", "Safety Hazard": "Campus Safety",
    "Lost and Found": "Campus Safety", "Security Concern": "Campus Safety",
    "Dining Service": "Dining", "Meal Plan": "Dining", "Vending": "IT", "Billing": "Finance",
    "Financial Aid": "Finance", "Other": "Other / Review",
}
ISSUE_TYPES = frozenset(ISSUE_TO_DEPT)
DEPARTMENTS = frozenset(ISSUE_TO_DEPT.values())
REQUIRED_PREDICTION_COLUMNS = (
    "case_id", "expected_issue_type", "expected_department", "expected_priority",
    "pred_issue_type", "pred_department", "pred_priority", "latency_seconds",
)


class ValidationError(ValueError):
    """Inputs are inconsistent; nothing should be scored."""


class HoldoutLeakError(ValidationError):
    """A holdout case or frozen holdout artifact reached the development audit."""


@dataclass(frozen=True)
class ModelSpec:
    key: str
    label: str
    path: str  # repository-relative
    priority_space: frozenset
    department_rule: str
    provenance: str


MODELS = (
    ModelSpec(
        key="deepseek-r1:8b",
        label="DeepSeek-R1 8B",
        path="backend/evaluation_results_ollama/deepseek_r1_8b_predictions.csv",
        priority_space=PRIORITIES_5,
        department_rule="fixed issue->department map",
        provenance="Ollama run saved 2026-10-20; generating script/prompt not preserved",
    ),
    ModelSpec(
        key="qwen3:14b",
        label="Qwen3 14B",
        path="backend/evaluation_results_ollama/qwen3_14b_development_predictions.csv",
        priority_space=PRIORITIES_5,
        department_rule="fixed issue->department map",
        provenance="Ollama run saved 2026-10-20; generating script/prompt not preserved",
    ),
    ModelSpec(
        key="laya",
        label="Laya",
        path="backend/evaluation_results_v2/laya_development_predictions.csv",
        # backend/test_laya.py declares only LOW/MEDIUM/HIGH/URGENT priority choices.
        priority_space=PRIORITIES_4,
        department_rule="fixed map + dorm-lockout Door / Lock -> Residence Life exception",
        provenance="Laya Router run saved 2026-10-22; generating script not preserved",
    ),
)


# --------------------------------------------------------------------------- I/O


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def guard_prediction_path(path: Path) -> None:
    resolved = path.resolve()
    if resolved == FROZEN_DIR.resolve() or FROZEN_DIR.resolve() in resolved.parents:
        raise HoldoutLeakError(f"Refusing to read frozen holdout archive: {path}")
    if "holdout" in resolved.name.lower() or any("holdout" in p.name.lower() for p in resolved.parents):
        raise HoldoutLeakError(f"Refusing to read a holdout-named prediction path: {path}")


def guard_output_dir(path: Path) -> None:
    resolved = path.resolve()
    protected = {EVAL_DIR.resolve(), FROZEN_DIR.resolve(), REPO_ROOT.resolve()}
    if resolved in protected or FROZEN_DIR.resolve() in resolved.parents:
        raise ValidationError(f"Refusing to write outputs into protected directory: {path}")
    if (REPO_ROOT / "backend").resolve() in [resolved, *resolved.parents]:
        raise ValidationError("Refusing to write next to historical backend artifacts")


def load_development(
    dataset_path: Path = DATASET, manifest_path: Path | None = FROZEN_MANIFEST
) -> tuple[dict[str, dict[str, str]], frozenset[str]]:
    """Return (development rows keyed by case_id in file order, holdout case IDs).

    Holdout rows are dropped immediately; only their IDs are retained, solely so
    prediction files can be rejected if they contain one.
    """
    if manifest_path is not None:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if sha256(dataset_path) != manifest["campusfix_benchmark_v2.csv"]:
            raise ValidationError("Benchmark checksum differs from the frozen manifest")
    rows = read_rows(dataset_path)
    ids = [row["case_id"] for row in rows]
    if any(not case_id for case_id in ids) or len(ids) != len(set(ids)):
        raise ValidationError("Benchmark case IDs must be nonempty and unique")
    splits = dict(Counter(row["split"] for row in rows))
    if splits != EXPECTED_SPLITS:
        raise ValidationError(f"Unexpected split sizes: {splits}")
    development: dict[str, dict[str, str]] = {}
    holdout_ids = set()
    for row in rows:
        if row["split"] == "holdout":
            holdout_ids.add(row["case_id"])
            continue
        validate_expected_labels(row)
        development[row["case_id"]] = row
    del rows
    return development, frozenset(holdout_ids)


def validate_expected_labels(row: dict[str, str]) -> None:
    case_id = row["case_id"]
    if row["expected_issue_type"] not in ISSUE_TYPES:
        raise ValidationError(f"{case_id}: unknown expected issue type {row['expected_issue_type']!r}")
    if row["expected_department"] not in DEPARTMENTS:
        raise ValidationError(f"{case_id}: unknown expected department {row['expected_department']!r}")
    if row["expected_priority"] not in PRIORITIES_5:
        raise ValidationError(f"{case_id}: unknown expected priority {row['expected_priority']!r}")
    if row["expected_action"] not in (AUTO, REVIEW):
        raise ValidationError(f"{case_id}: unknown expected action {row['expected_action']!r}")
    # Benchmark convention: priority NEEDS_REVIEW <=> action NEEDS_REVIEW.
    if (row["expected_priority"] == REVIEW) != (row["expected_action"] == REVIEW):
        raise ValidationError(f"{case_id}: expected priority and expected action disagree")


def validate_predictions(
    rows: Sequence[dict[str, str]],
    development: dict[str, dict[str, str]],
    holdout_ids: Iterable[str],
    spec: ModelSpec,
) -> list[dict]:
    """Validate a prediction file and return normalized records in dataset order.

    Raises on structural problems (leakage, missing/duplicate/unknown IDs, label or
    text drift, bad latency). Out-of-vocabulary predictions such as ``FAILED`` are a
    legitimate historical outcome: they are kept, flagged invalid, and scored wrong.
    """
    if not rows:
        raise ValidationError(f"{spec.key}: prediction file is empty")
    missing_cols = [c for c in REQUIRED_PREDICTION_COLUMNS if c not in rows[0]]
    if missing_cols:
        raise ValidationError(f"{spec.key}: missing columns {missing_cols}")
    holdout_ids = frozenset(holdout_ids)
    ids = [row["case_id"] for row in rows]
    leaked = sorted(set(ids) & holdout_ids)
    if leaked:
        raise HoldoutLeakError(f"{spec.key}: {len(leaked)} holdout case IDs present; refusing to score")
    if any(not case_id for case_id in ids):
        raise ValidationError(f"{spec.key}: empty case_id")
    duplicates = sorted(k for k, v in Counter(ids).items() if v > 1)
    if duplicates:
        raise ValidationError(f"{spec.key}: duplicate case IDs {duplicates[:5]}")
    unknown = sorted(set(ids) - set(development))
    if unknown:
        raise ValidationError(f"{spec.key}: case IDs not in development split {unknown[:5]}")
    absent = sorted(set(development) - set(ids))
    if absent:
        raise ValidationError(f"{spec.key}: {len(absent)} development cases missing, e.g. {absent[:5]}")

    by_id = {row["case_id"]: row for row in rows}
    records = []
    for case_id, case in development.items():
        row = by_id[case_id]
        for axis in AXES:
            if row["expected_" + axis] != case["expected_" + axis]:
                raise ValidationError(f"{spec.key}: {case_id} expected {axis} differs from benchmark")
            if not row["pred_" + axis].strip():
                raise ValidationError(f"{spec.key}: {case_id} empty predicted {axis}")
        if "text" in row and row["text"] != case["text"]:
            raise ValidationError(f"{spec.key}: {case_id} text differs from benchmark")
        try:
            latency = float(row["latency_seconds"])
        except ValueError as error:
            raise ValidationError(f"{spec.key}: {case_id} latency is not numeric") from error
        if not math.isfinite(latency) or latency < 0:
            raise ValidationError(f"{spec.key}: {case_id} latency must be finite and nonnegative")

        issue, dept, prio = row["pred_issue_type"], row["pred_department"], row["pred_priority"]
        invalid_axes = [
            axis for axis, value, allowed in (
                ("issue_type", issue, ISSUE_TYPES),
                ("department", dept, DEPARTMENTS),
                ("priority", prio, spec.priority_space),
            ) if value not in allowed
        ]
        records.append({
            "case_id": case_id,
            "text": case["text"],
            "difficulty": case["difficulty"],
            "noise_type": case["noise_type"],
            "expected_action": case["expected_action"],
            "expected": {axis: case["expected_" + axis] for axis in AXES},
            "raw": {"issue_type": issue, "department": dept, "priority": prio},
            "pred": {
                "issue_type": issue if issue in ISSUE_TYPES else INVALID,
                "department": dept if dept in DEPARTMENTS else INVALID,
                "priority": prio if prio in spec.priority_space else INVALID,
            },
            "invalid_axes": invalid_axes,
            "latency": latency,
        })
    return records


def verify_input_hashes(manifest_path: Path = INPUT_MANIFEST, root: Path = REPO_ROOT) -> dict[str, str]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for relative, expected in manifest.items():
        path = root / relative
        if not path.exists():
            raise FileNotFoundError(f"Historical development input not found: {relative}")
        if sha256(path) != expected:
            raise ValidationError(f"Historical development input changed: {relative}")
    return manifest


# ----------------------------------------------------------------------- metrics


def _check_pair(expected: Sequence[str], predicted: Sequence[str]) -> None:
    if len(expected) != len(predicted):
        raise ValueError("expected and predicted must have equal length")


def label_metrics(expected: Sequence[str], predicted: Sequence[str]) -> dict:
    """Accuracy and macro-F1 over the union of labels; zero-division scores 0.

    Matches ``sklearn.metrics.f1_score(average="macro", zero_division=0)`` and the
    convention used by ``evaluation/reproduce_metrics.py``.
    """
    _check_pair(expected, predicted)
    if not expected:
        raise ValueError("Metrics require at least one case")
    classes = per_class(expected, predicted)
    correct = sum(a == b for a, b in zip(expected, predicted))
    return {
        "correct": correct,
        "total": len(expected),
        "accuracy": correct / len(expected),
        "macro_f1": math.fsum(c["f1"] for c in classes.values()) / len(classes),
    }


def per_class(expected: Sequence[str], predicted: Sequence[str]) -> dict[str, dict]:
    _check_pair(expected, predicted)
    result = {}
    for label in sorted(set(expected) | set(predicted)):
        tp = sum(a == b == label for a, b in zip(expected, predicted))
        support = sum(a == label for a in expected)
        predicted_count = sum(b == label for b in predicted)
        fp, fn = predicted_count - tp, support - tp
        result[label] = {
            "support": support,
            "predicted": predicted_count,
            "tp": tp,
            "precision": tp / predicted_count if predicted_count else 0.0,
            "recall": tp / support if support else 0.0,
            "f1": 2 * tp / (2 * tp + fp + fn) if (2 * tp + fp + fn) else 0.0,
        }
    return result


def confusion_matrix(
    expected: Sequence[str], predicted: Sequence[str], labels: Sequence[str] | None = None
) -> dict:
    """Rows are expected labels, columns predicted labels."""
    _check_pair(expected, predicted)
    labels = list(labels) if labels is not None else sorted(set(expected) | set(predicted))
    index = {label: i for i, label in enumerate(labels)}
    matrix = [[0] * len(labels) for _ in labels]
    for a, b in zip(expected, predicted):
        if a not in index or b not in index:
            raise ValueError(f"Label outside matrix labels: {a!r} / {b!r}")
        matrix[index[a]][index[b]] += 1
    return {"labels": labels, "matrix": matrix}


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def ordinal_priority(expected: Sequence[str], predicted: Sequence[str]) -> dict:
    """Ordinal triage metrics on cases whose expected priority is LOW..URGENT.

    Every ordinal-expected case falls into exactly one outcome:
    exact, under (predicted lower), over (predicted higher), review (predicted
    NEEDS_REVIEW, i.e. deferred to a human with no level), or invalid.
    Rates use all ordinal-expected cases as the denominator, so the five outcome
    rates sum to 1. Distance metrics use only scorable pairs (both ordinal); the
    ``within_one_strict`` variant counts review/invalid as misses.
    Expected NEEDS_REVIEW cases are excluded here and scored by ``review_handling``.
    """
    _check_pair(expected, predicted)
    pairs = [(a, b) for a, b in zip(expected, predicted) if a in RANK]
    outcome = Counter()
    signed = []
    per_level = {level: Counter() for level in ORDINAL}
    for a, b in pairs:
        if b in RANK:
            d = RANK[b] - RANK[a]
            signed.append(d)
            kind = "exact" if d == 0 else ("under" if d < 0 else "over")
        elif b == REVIEW:
            kind = "review"
        else:
            kind = "invalid"
        outcome[kind] += 1
        per_level[a][kind] += 1
    n = len(pairs)
    scorable = len(signed)
    within = sum(abs(d) <= 1 for d in signed)
    urgent = per_level["URGENT"]
    n_urgent = sum(urgent.values())
    return {
        "n_ordinal_expected": n,
        "n_scorable": scorable,
        "counts": {k: outcome[k] for k in ("exact", "under", "over", "review", "invalid")},
        "exact_rate": _rate(outcome["exact"], n),
        "under_triage_rate": _rate(outcome["under"], n),
        "over_triage_rate": _rate(outcome["over"], n),
        "review_deferral_rate": _rate(outcome["review"], n),
        "invalid_rate": _rate(outcome["invalid"], n),
        "severe_under_triage": sum(d <= -2 for d in signed),
        "severe_over_triage": sum(d >= 2 for d in signed),
        "mean_abs_distance": math.fsum(abs(d) for d in signed) / scorable if scorable else None,
        "mean_signed_distance": math.fsum(signed) / scorable if scorable else None,
        "within_one_scorable": _rate(within, scorable),
        "within_one_strict": _rate(within, n),
        "signed_distance_histogram": {str(k): v for k, v in sorted(Counter(signed).items())},
        "urgent": {
            "n": n_urgent,
            "under": urgent["under"],
            "review": urgent["review"],
            "invalid": urgent["invalid"],
            "under_triage_rate": _rate(urgent["under"], n_urgent),
            "not_urgent_rate": _rate(urgent["under"] + urgent["review"] + urgent["invalid"], n_urgent),
        },
        "per_expected_level": {
            level: {"n": sum(c.values()), **{k: c[k] for k in ("exact", "under", "over", "review", "invalid")}}
            for level, c in per_level.items()
        },
    }


def derive_action(pred_issue: str, pred_priority: str, valid: bool = True) -> str:
    """Production review rule (backend/app/classify.py): review on NEEDS_REVIEW
    priority, an ``Other`` issue type, or any failed/invalid classification."""
    if not valid or pred_priority == REVIEW or pred_issue == "Other":
        return REVIEW
    return AUTO


def review_handling(expected_actions: Sequence[str], predicted_actions: Sequence[str]) -> dict:
    """Binary NEEDS_REVIEW detection; positive class is NEEDS_REVIEW."""
    _check_pair(expected_actions, predicted_actions)
    for value in (*expected_actions, *predicted_actions):
        if value not in (AUTO, REVIEW):
            raise ValueError(f"Unknown action {value!r}")
    pairs = list(zip(expected_actions, predicted_actions))
    tp = pairs.count((REVIEW, REVIEW))
    fn = pairs.count((REVIEW, AUTO))
    fp = pairs.count((AUTO, REVIEW))
    tn = pairs.count((AUTO, AUTO))
    precision, recall = _rate(tp, tp + fp), _rate(tp, tp + fn)
    f1 = (2 * tp / (2 * tp + fp + fn)) if (2 * tp + fp + fn) else None
    return {
        "tp": tp, "fn_false_auto_route": fn, "fp_unnecessary_review": fp, "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_auto_route_rate": _rate(fn, tp + fn),
        "unnecessary_review_rate": _rate(fp, fp + tn),
        "review_volume_rate": _rate(tp + fp, len(pairs)),
    }


def mcnemar_exact(correct_a: Sequence[bool], correct_b: Sequence[bool]) -> dict:
    """Exact two-sided McNemar test on paired correctness vectors."""
    _check_pair(correct_a, correct_b)
    b = sum(x and not y for x, y in zip(correct_a, correct_b))
    c = sum(y and not x for x, y in zip(correct_a, correct_b))
    n = b + c
    if n == 0:
        p = 1.0
    else:
        tail = math.fsum(math.comb(n, k) for k in range(min(b, c) + 1)) / 2 ** n
        p = min(1.0, 2 * tail)
    return {"a_only_correct": b, "b_only_correct": c, "p_value": p}


# ---------------------------------------------------------------------- analysis


def analyze_model(spec: ModelSpec, records: list[dict]) -> dict:
    exp = {axis: [r["expected"][axis] for r in records] for axis in AXES}
    pred = {axis: [r["pred"][axis] for r in records] for axis in AXES}
    common_dept = [ISSUE_TO_DEPT.get(p, INVALID) for p in pred["issue_type"]]
    valid = [not r["invalid_axes"] for r in records]
    derived = [derive_action(i, p, v) for i, p, v in zip(pred["issue_type"], pred["priority"], valid)]
    expected_actions = [r["expected_action"] for r in records]
    ordinal_idx = [i for i, r in enumerate(records) if r["expected"]["priority"] in RANK]

    result: dict = {
        "label": spec.label,
        "source": spec.path,
        "priority_space": sorted(spec.priority_space, key=lambda x: (x not in RANK, RANK.get(x, 9))),
        "department_rule": spec.department_rule,
        "provenance": spec.provenance,
        "n": len(records),
        "invalid_rows": [
            {"case_id": r["case_id"], "axes": r["invalid_axes"], "raw": r["raw"]}
            for r in records if r["invalid_axes"]
        ],
    }
    for axis in AXES:
        result[axis] = {
            **label_metrics(exp[axis], pred[axis]),
            "per_class": per_class(exp[axis], pred[axis]),
            "confusion": confusion_matrix(exp[axis], pred[axis]),
        }
    # Priority on ordinal-expected cases only (comparable across 4- and 5-class models).
    result["priority_ordinal_subset"] = label_metrics(
        [exp["priority"][i] for i in ordinal_idx], [pred["priority"][i] for i in ordinal_idx]
    )
    result["priority_ordinal"] = ordinal_priority(exp["priority"], pred["priority"])
    result["department_common_map"] = label_metrics(exp["department"], common_dept)

    dept_wrong = [i for i in range(len(records)) if pred["department"][i] != exp["department"][i]]
    result["department_error_decomposition"] = {
        "department_errors": len(dept_wrong),
        "issue_also_wrong": sum(pred["issue_type"][i] != exp["issue_type"][i] for i in dept_wrong),
        "issue_correct_routing_rule": sum(pred["issue_type"][i] == exp["issue_type"][i] for i in dept_wrong),
    }
    result["review_derived"] = review_handling(expected_actions, derived)
    if REVIEW in spec.priority_space:
        label_only = [REVIEW if p == REVIEW else AUTO for p in pred["priority"]]
        result["review_priority_label_only"] = review_handling(expected_actions, label_only)
    else:
        result["review_priority_label_only"] = None
    urgent_idx = [i for i in ordinal_idx if exp["priority"][i] == "URGENT"]
    result["urgent_routed_to_review"] = sum(derived[i] == REVIEW for i in urgent_idx)

    latencies = [r["latency"] for r in records]
    result["latency"] = {"mean_seconds": statistics.fmean(latencies), "median_seconds": statistics.median(latencies)}

    result["slices"] = {}
    for field in ("difficulty", "noise_type"):
        groups = defaultdict(list)
        for i, r in enumerate(records):
            groups[r[field]].append(i)
        result["slices"][field] = {
            name: {
                "n": len(idx),
                "issue_correct": sum(pred["issue_type"][i] == exp["issue_type"][i] for i in idx),
                "department_correct": sum(pred["department"][i] == exp["department"][i] for i in idx),
                "priority_correct": sum(pred["priority"][i] == exp["priority"][i] for i in idx),
                "under_triage": sum(
                    exp["priority"][i] in RANK and pred["priority"][i] in RANK
                    and RANK[pred["priority"][i]] < RANK[exp["priority"][i]] for i in idx
                ),
            }
            for name, idx in sorted(groups.items())
        }
    top = Counter(
        (a, b) for a, b in zip(exp["issue_type"], pred["issue_type"]) if a != b
    ).most_common(8)
    result["top_issue_confusions"] = [{"expected": a, "predicted": b, "count": n} for (a, b), n in top]
    ptop = Counter((a, b) for a, b in zip(exp["priority"], pred["priority"]) if a != b).most_common(8)
    result["top_priority_confusions"] = [{"expected": a, "predicted": b, "count": n} for (a, b), n in ptop]
    return result


def compatibility(specs: Sequence[ModelSpec], records: dict[str, list[dict]]) -> dict:
    keys = [s.key for s in specs]
    case_sets = {k: tuple(r["case_id"] for r in records[k]) for k in keys}
    same_cases = len(set(case_sets.values())) == 1
    five = [s.key for s in specs if REVIEW in s.priority_space]
    rules = {s.key: s.department_rule for s in specs}
    return {
        "identical_case_ids_and_order": same_cases,
        "identical_expected_labels": same_cases,  # each file was validated against the benchmark
        "axes": {
            "issue_type": {"models": keys, "note": "same 33-label taxonomy; invalid outputs scored wrong"},
            "department_recorded": {
                "models": keys,
                "confounded": len(set(rules.values())) > 1,
                "note": "pipelines applied different issue->department rules; see department_common_map",
            },
            "department_common_map": {"models": keys, "note": "department re-derived from predicted issue with one map"},
            "priority_5class": {"models": five, "note": "only models whose output space includes NEEDS_REVIEW"},
            "priority_ordinal": {"models": keys, "note": "expected LOW..URGENT cases only"},
            "review_derived": {"models": keys, "note": "production rule: NEEDS_REVIEW priority, Other issue, or failure"},
            "review_priority_label_only": {"models": five, "note": "requires a NEEDS_REVIEW priority output"},
            "latency": {"models": [], "note": "different runs/hardware/services; descriptive only, not compared"},
        },
    }


def pairwise_tests(specs: Sequence[ModelSpec], records: dict[str, list[dict]], compat: dict) -> list[dict]:
    tests = []
    getters = {
        "issue_type": lambda r: r["pred"]["issue_type"] == r["expected"]["issue_type"],
        "department_common_map": lambda r: ISSUE_TO_DEPT.get(r["pred"]["issue_type"]) == r["expected"]["department"],
        "priority_5class": lambda r: r["pred"]["priority"] == r["expected"]["priority"],
    }
    for axis, get in getters.items():
        allowed = compat["axes"][axis]["models"]
        for i, a in enumerate(allowed):
            for b in allowed[i + 1:]:
                tests.append({"axis": axis, "a": a, "b": b,
                              **mcnemar_exact([get(r) for r in records[a]], [get(r) for r in records[b]])})
    # Ordinal-subset exact priority, comparable for every model.
    allowed = compat["axes"]["priority_ordinal"]["models"]
    for i, a in enumerate(allowed):
        for b in allowed[i + 1:]:
            ca = [r["pred"]["priority"] == r["expected"]["priority"] for r in records[a] if r["expected"]["priority"] in RANK]
            cb = [r["pred"]["priority"] == r["expected"]["priority"] for r in records[b] if r["expected"]["priority"] in RANK]
            tests.append({"axis": "priority_ordinal_subset", "a": a, "b": b, **mcnemar_exact(ca, cb)})
    return tests


def case_views(specs: Sequence[ModelSpec], records: dict[str, list[dict]]) -> list[dict]:
    views = []
    for i, base in enumerate(records[specs[0].key]):
        views.append({
            "case_id": base["case_id"],
            "text": base["text"],
            "difficulty": base["difficulty"],
            "noise_type": base["noise_type"],
            "expected": base["expected"],
            "expected_action": base["expected_action"],
            "pred": {s.key: records[s.key][i]["raw"] for s in specs},
        })
    return views


def select_errors(specs: Sequence[ModelSpec], views: list[dict]) -> dict:
    keys = [s.key for s in specs]
    space = {s.key: s.priority_space for s in specs}

    def prio_rank(key, value):
        return RANK.get(value) if value in space[key] else None

    def action(view, key):
        p = view["pred"][key]
        valid = p["issue_type"] in ISSUE_TYPES and p["priority"] in space[key]
        return derive_action(p["issue_type"], p["priority"], valid)

    sel = {
        "urgent_not_urgent_any_model": [], "severe_under_triage_any_model": [],
        "false_auto_route_any_model": [], "issue_wrong_all_models": [],
        "priority_wrong_all_models": [], "department_routing_rule": [],
    }
    for v in views:
        e = v["expected"]
        ids = v["case_id"]
        if e["priority"] == "URGENT" and any(v["pred"][k]["priority"] != "URGENT" for k in keys):
            sel["urgent_not_urgent_any_model"].append(ids)
        if e["priority"] in RANK and any(
            prio_rank(k, v["pred"][k]["priority"]) is not None
            and prio_rank(k, v["pred"][k]["priority"]) - RANK[e["priority"]] <= -2 for k in keys
        ):
            sel["severe_under_triage_any_model"].append(ids)
        if v["expected_action"] == REVIEW and any(action(v, k) == AUTO for k in keys):
            sel["false_auto_route_any_model"].append(ids)
        if all(v["pred"][k]["issue_type"] != e["issue_type"] for k in keys):
            sel["issue_wrong_all_models"].append(ids)
        if all(v["pred"][k]["priority"] != e["priority"] for k in keys):
            sel["priority_wrong_all_models"].append(ids)
        if ISSUE_TO_DEPT[e["issue_type"]] != e["department"]:
            sel["department_routing_rule"].append(ids)
    return sel


def run(root: Path = REPO_ROOT, specs: Sequence[ModelSpec] = MODELS, verify_hashes: bool = True) -> dict:
    if verify_hashes:
        input_hashes = verify_input_hashes(root=root)
    else:
        input_hashes = {}
    development, holdout_ids = load_development()
    records = {}
    for spec in specs:
        path = root / spec.path
        guard_prediction_path(path)
        records[spec.key] = validate_predictions(read_rows(path), development, holdout_ids, spec)
    compat = compatibility(specs, records)
    views = case_views(specs, records)
    return {
        "scope": {
            "split": "development",
            "n_cases": len(development),
            "holdout_scored": False,
            "benchmark_sha256": sha256(DATASET),
            "input_sha256": input_hashes,
            "expected_priority_counts": dict(sorted(Counter(r["expected_priority"] for r in development.values()).items())),
            "expected_action_counts": dict(sorted(Counter(r["expected_action"] for r in development.values()).items())),
        },
        "compatibility": compat,
        "models": {spec.key: analyze_model(spec, records[spec.key]) for spec in specs},
        "pairwise_mcnemar": pairwise_tests(specs, records, compat),
        "error_case_ids": select_errors(specs, views),
        "cases": views,
    }


# ----------------------------------------------------------------------- report


def _pct(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{100 * value:.{digits}f}%"


def _md_table(header: Sequence[str], rows: Iterable[Sequence]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _short(label: str) -> str:
    return {INVALID: "INVALID", "Other / Review": "Other/Rev", "Student Engagement": "StuEng",
            "Campus Safety": "CampSafety", "Residence Life": "ResLife", "NEEDS_REVIEW": "REVIEW"}.get(label, label)


def render_tables(report: dict) -> str:
    models = report["models"]
    keys = list(models)
    out = ["# Development-split tables (generated)", "",
           "Generated by `evaluation/dev_audit/analyze_development.py`. Do not edit by hand.",
           f"Scope: {report['scope']['n_cases']} development cases; holdout scored: "
           f"{report['scope']['holdout_scored']}.", ""]

    out += ["## Headline metrics", ""]
    out.append(_md_table(
        ["Metric", *[models[k]["label"] for k in keys]],
        [
            ["Issue accuracy", *[f"{_pct(models[k]['issue_type']['accuracy'])} ({models[k]['issue_type']['correct']}/187)" for k in keys]],
            ["Issue macro-F1", *[f"{models[k]['issue_type']['macro_f1']:.4f}" for k in keys]],
            ["Dept accuracy (recorded)", *[f"{_pct(models[k]['department']['accuracy'])} ({models[k]['department']['correct']}/187)" for k in keys]],
            ["Dept macro-F1 (recorded)", *[f"{models[k]['department']['macro_f1']:.4f}" for k in keys]],
            ["Dept accuracy (common map)", *[f"{_pct(models[k]['department_common_map']['accuracy'])}" for k in keys]],
            ["Priority exact, 5-class (187)", *[
                f"{_pct(models[k]['priority']['accuracy'])} ({models[k]['priority']['correct']}/187)"
                + ("" if models[k]["review_priority_label_only"] is not None else " ‡") for k in keys]],
            ["Priority macro-F1, 5-class", *[f"{models[k]['priority']['macro_f1']:.4f}" + ("" if models[k]["review_priority_label_only"] is not None else " ‡") for k in keys]],
            ["Priority exact, ordinal subset (176)", *[f"{_pct(models[k]['priority_ordinal_subset']['accuracy'])} ({models[k]['priority_ordinal_subset']['correct']}/176)" for k in keys]],
            ["Priority macro-F1, ordinal subset", *[f"{models[k]['priority_ordinal_subset']['macro_f1']:.4f}" for k in keys]],
            ["Invalid/failed rows", *[str(len(models[k]["invalid_rows"])) for k in keys]],
            ["Mean / median latency (s) †", *[f"{models[k]['latency']['mean_seconds']:.2f} / {models[k]['latency']['median_seconds']:.2f}" for k in keys]],
        ],
    ))
    out += ["", "‡ Not comparable: this model's output space has no NEEDS_REVIEW priority, so it cannot "
            "match the 11 expected-review cases. Use the ordinal-subset rows.",
            "† Descriptive only; runs used different hardware/services and are not compared.", ""]

    out += ["## Ordinal priority (expected LOW..URGENT, n=176)", ""]
    rows = []
    for k in keys:
        o = models[k]["priority_ordinal"]
        rows.append([models[k]["label"], _pct(o["exact_rate"]), _pct(o["under_triage_rate"]), _pct(o["over_triage_rate"]),
                     _pct(o["review_deferral_rate"]), _pct(o["invalid_rate"]), f"{o['mean_abs_distance']:.3f}",
                     f"{o['mean_signed_distance']:+.3f}", _pct(o["within_one_scorable"]), _pct(o["within_one_strict"]),
                     o["severe_under_triage"], o["severe_over_triage"]])
    out.append(_md_table(["Model", "Exact", "Under", "Over", "Deferred (REVIEW)", "Invalid", "MAD", "Mean signed",
                          "Within-1 (scorable)", "Within-1 (strict)", "Under by ≥2", "Over by ≥2"], rows))
    out += ["", "Outcome rates (Exact+Under+Over+Deferred+Invalid) sum to 100% of the 176 cases. MAD = mean absolute "
            "level distance over scorable pairs.", "", "### Urgent cases (expected URGENT)", ""]
    rows = []
    for k in keys:
        u = models[k]["priority_ordinal"]["urgent"]
        rows.append([models[k]["label"], u["n"], f"{u['under']} ({_pct(u['under_triage_rate'])})", u["review"], u["invalid"],
                     f"{u['under'] + u['review'] + u['invalid']} ({_pct(u['not_urgent_rate'])})",
                     models[k]["urgent_routed_to_review"]])
    out.append(_md_table(["Model", "n", "Under-triaged", "Deferred (REVIEW)", "Invalid", "Not labelled URGENT",
                          "Routed to review (derived)"], rows))
    out += ["", "### Per expected level", ""]
    rows = []
    for k in keys:
        for level, c in models[k]["priority_ordinal"]["per_expected_level"].items():
            rows.append([models[k]["label"], level, c["n"], c["exact"], c["under"], c["over"], c["review"], c["invalid"]])
    out.append(_md_table(["Model", "Expected", "n", "Exact", "Under", "Over", "Review", "Invalid"], rows))

    out += ["", "## NEEDS_REVIEW handling (expected review n=11, auto-route n=176)", ""]
    rows = []
    for k in keys:
        for name in ("review_derived", "review_priority_label_only"):
            r = models[k][name]
            if r is None:
                rows.append([models[k]["label"], name, "not applicable (no NEEDS_REVIEW output)", "", "", "", "", "", ""])
                continue
            rows.append([models[k]["label"], name, r["tp"], r["fn_false_auto_route"], r["fp_unnecessary_review"], r["tn"],
                         _pct(r["recall"]), _pct(r["precision"]), _pct(r["unnecessary_review_rate"])])
    out.append(_md_table(["Model", "Rule", "TP", "FN (false auto-route)", "FP (unneeded review)", "TN", "Recall",
                          "Precision", "Unneeded-review rate"], rows))
    out += ["", "`review_derived` applies the production rule (NEEDS_REVIEW priority, `Other` issue, or failure). "
            "`review_priority_label_only` uses only a NEEDS_REVIEW priority label.", ""]

    out += ["## Department error decomposition (recorded departments)", ""]
    out.append(_md_table(["Model", "Dept errors", "Issue also wrong", "Issue right, routing rule wrong"],
                         [[models[k]["label"], *models[k]["department_error_decomposition"].values()] for k in keys]))

    out += ["", "## Paired comparisons (exact McNemar, two-sided)", "",
            "Only axes where both models are comparable. Uncorrected p-values; 187 (or 176) paired cases.", ""]
    labels = {k: models[k]["label"] for k in keys}
    out.append(_md_table(["Axis", "A", "B", "Only A correct", "Only B correct", "p"],
                         [[t["axis"], labels[t["a"]], labels[t["b"]], t["a_only_correct"], t["b_only_correct"],
                           f"{t['p_value']:.4f}"] for t in report["pairwise_mcnemar"]]))

    out += ["", "## Slices", ""]
    for field in ("difficulty", "noise_type"):
        out += [f"### By {field}", ""]
        names = sorted(models[keys[0]]["slices"][field])
        rows = []
        for name in names:
            n = models[keys[0]]["slices"][field][name]["n"]
            cells = []
            for k in keys:
                s = models[k]["slices"][field][name]
                cells.append(f"{s['issue_correct']}/{s['department_correct']}/{s['priority_correct']}/{s['under_triage']}")
            rows.append([name, n, *cells])
        out.append(_md_table([field, "n", *[f"{labels[k]} (iss/dept/prio correct/under)" for k in keys]], rows))
        out.append("")

    for axis in AXES:
        out += [f"## Per-class performance: {axis}", ""]
        all_labels = sorted(set().union(*[models[k][axis]["per_class"] for k in keys]))
        rows = []
        for label in all_labels:
            support = next((models[k][axis]["per_class"][label]["support"] for k in keys if label in models[k][axis]["per_class"]), 0)
            cells = []
            for k in keys:
                c = models[k][axis]["per_class"].get(label)
                cells.append("—" if c is None else f"{c['precision']:.2f}/{c['recall']:.2f}/{c['f1']:.2f}")
            rows.append([_short(label), support, *cells])
        out.append(_md_table([axis, "support", *[f"{labels[k]} P/R/F1" for k in keys]], rows))
        out.append("")

    for axis in ("department", "priority", "issue_type"):
        for k in keys:
            cm = models[k][axis]["confusion"]
            out += [f"## Confusion matrix: {axis} — {labels[k]}", "", "Rows = expected, columns = predicted.", ""]
            if axis == "issue_type":
                # Sparse listing; a 34x34 grid is unreadable in Markdown.
                cells = [(cm["labels"][i], cm["labels"][j], v) for i, row in enumerate(cm["matrix"])
                         for j, v in enumerate(row) if v and i != j]
                diag = {cm["labels"][i]: cm["matrix"][i][i] for i in range(len(cm["labels"]))}
                out.append(f"Diagonal (correct) total: {sum(diag.values())}. Off-diagonal cells:")
                out.append("")
                out.append(_md_table(["Expected", "Predicted", "Count"],
                                     sorted(([a, b, v] for a, b, v in cells), key=lambda r: (-r[2], r[0], r[1]))))
            else:
                out.append(_md_table(["exp \\ pred", *[_short(l) for l in cm["labels"]]],
                                     [[_short(cm["labels"][i]), *row] for i, row in enumerate(cm["matrix"])]))
            out.append("")

    out += ["## Error case lists", ""]
    by_id = {c["case_id"]: c for c in report["cases"]}
    for name, ids in report["error_case_ids"].items():
        out += [f"### {name} ({len(ids)})", ""]
        rows = []
        for case_id in ids:
            c = by_id[case_id]
            text = c["text"].replace("|", "/").replace("\n", " ")
            rows.append([case_id, text[:160] + ("…" if len(text) > 160 else ""),
                         f"{c['expected']['issue_type']} / {c['expected']['department']} / {c['expected']['priority']}",
                         *[f"{c['pred'][k]['issue_type']} / {c['pred'][k]['priority']}" for k in keys]])
        out.append(_md_table(["Case", "Text", "Expected (issue/dept/prio)", *[labels[k] for k in keys]], rows))
        out.append("")
    return "\n".join(out) + "\n"


def _dump(report: dict) -> str:
    return json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, help="Directory for dev_metrics.json and dev_tables.md")
    parser.add_argument("--check", action="store_true", help="Compare against committed results/ outputs")
    args = parser.parse_args(argv)
    report = run()
    text, tables = _dump(report), render_tables(report)
    if args.check:
        if (RESULTS_DIR / "dev_metrics.json").read_text(encoding="utf-8") != text:
            raise ValidationError("Computed development metrics differ from results/dev_metrics.json")
        if (RESULTS_DIR / "dev_tables.md").read_text(encoding="utf-8") != tables:
            raise ValidationError("Generated tables differ from results/dev_tables.md")
        print("OK: development audit outputs reproduce exactly")
        return 0
    if args.out:
        guard_output_dir(args.out)
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "dev_metrics.json").write_text(text, encoding="utf-8", newline="\n")
        (args.out / "dev_tables.md").write_text(tables, encoding="utf-8", newline="\n")
        print(f"Wrote {args.out / 'dev_metrics.json'} and {args.out / 'dev_tables.md'}")
        return 0
    summary = {k: {axis: round(m[axis]["accuracy"], 4) for axis in AXES} for k, m in report["models"].items()}
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
