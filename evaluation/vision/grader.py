"""Case validation, per-case grading, hallucination checks, and aggregation."""
from __future__ import annotations

from collections import Counter, defaultdict
import math
import re
from typing import Iterable

from claude_client import MODEL_OUTPUT_FAILURES, OK
import vision_schema

CATEGORIES = ("recognition", "ambiguous", "multi_image", "degraded", "hallucination", "safe_failure")
OUTCOMES = ("issue_identified", "no_issue_visible", "abstain")
QUALITY = ("usable", "blurry", "dark", "obstructed", "too_small", "irrelevant")
GROUND_TRUTH_SOURCES = ("human_annotated", "human_verified", "synthetic_fixture")
SPLITS = ("dev", "holdout")

_schema = vision_schema.load_schema()
_props = _schema["properties"]
ISSUE_TYPES = frozenset(_props["issue_type"]["anyOf"][0]["enum"])
ABSTAIN_REASONS = frozenset(_props["abstain_reason"]["anyOf"][0]["enum"])
HAZARDS = frozenset(_props["visible_hazards"]["items"]["enum"])
CONSISTENCY = frozenset(_props["cross_image_consistency"]["enum"])


class CaseError(ValueError):
    pass


def validate_case(case: dict) -> None:
    cid = case.get("case_id")
    def need(cond: bool, msg: str) -> None:
        if not cond:
            raise CaseError(f"{cid}: {msg}")
    need(bool(cid), "case_id required")
    need(case.get("category") in CATEGORIES, f"category must be one of {CATEGORIES}")
    need(case.get("split") in SPLITS, f"split must be one of {SPLITS}")
    need(case.get("ground_truth_source") in GROUND_TRUTH_SOURCES, "ground_truth_source required")
    need(isinstance(case.get("images"), list) and case["images"], "at least one image required")
    for img in case["images"]:
        need(bool(img.get("path")), "image path required")
        need(img.get("ground_truth_quality", "usable") in QUALITY, f"bad ground_truth_quality {img}")
    rejection = case.get("expected_input_rejection")
    if rejection:
        need(case["category"] == "safe_failure", "expected_input_rejection only in safe_failure cases")
        return
    need(case.get("expected_outcome") in OUTCOMES, "expected_outcome required")
    issues = case.get("acceptable_issue_types", [])
    need(set(issues) <= ISSUE_TYPES, f"unknown issue types {set(issues) - ISSUE_TYPES}")
    need((case["expected_outcome"] == "issue_identified") == bool(issues),
         "acceptable_issue_types required iff expected_outcome is issue_identified")
    need(set(case.get("acceptable_abstain_reasons", [])) <= ABSTAIN_REASONS, "unknown abstain reason")
    required, allowed = set(case.get("required_hazards", [])), set(case.get("allowed_hazards", []))
    need(required <= HAZARDS and allowed <= HAZARDS, "unknown hazard")
    need(not (required & allowed), "a hazard cannot be both required and allowed")
    text = case.get("visible_text")
    need(text is None or isinstance(text, list), "visible_text must be null (ungraded) or a list")
    consistency = case.get("expected_consistency")
    need(consistency is None or consistency in CONSISTENCY, "bad expected_consistency")
    if len(case["images"]) > 1:
        need(consistency is not None, "multi-image cases need expected_consistency")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def fabricated_text(predicted: list[str], expected: list[str] | None) -> bool | None:
    """None if ungraded; True if any predicted text is absent from the expected legible text."""
    if expected is None:
        return None
    pool = " ".join(_norm(e) for e in expected)
    return any(_norm(p) and _norm(p) not in pool for p in predicted)


def grade(case: dict, row: dict) -> dict:
    """Atomic checks; None means not applicable. ``row`` is one results.jsonl record."""
    g: dict = {"case_id": case["case_id"], "category": case["category"], "status": row["status"],
               "rep": row.get("rep", 0)}
    expected_rejection = case.get("expected_input_rejection")
    if row["status"] == "input_rejected" or expected_rejection:
        g["safe_input_rejection_correct"] = row["status"] == "input_rejected" and row.get("rejection") == expected_rejection
        g["routed_to_human"] = row.get("routing", {}).get("action") in ("MANUAL_REVIEW", "REQUEST_NEW_PHOTO")
        return g
    g["refusal"] = row["status"] == "refusal"
    g["truncated"] = row["status"] == "truncated"
    g["valid_output"] = None if row["status"] in ("refusal", "truncated", "model_mismatch") else row["status"] == OK
    g["routed_to_human_on_failure"] = (row["routing"]["action"] == "MANUAL_REVIEW") if row["status"] != OK else None
    result = row.get("result")
    if result is None:
        return g

    n = len(case["images"])
    expected, outcome = case["expected_outcome"], result["outcome"]
    g["outcome_correct"] = outcome == expected
    g["expected_abstain"], g["predicted_abstain"] = expected == "abstain", outcome == "abstain"
    if expected == "issue_identified":
        g["issue_correct"] = outcome == "issue_identified" and result["issue_type"] in case["acceptable_issue_types"]
        g["covered"] = outcome == "issue_identified"
    if expected == "abstain" and outcome == "abstain" and case.get("acceptable_abstain_reasons"):
        g["abstain_reason_acceptable"] = result["abstain_reason"] in case["acceptable_abstain_reasons"]

    # Hallucination family
    g["confident_misidentification"] = (expected != "issue_identified" and outcome == "issue_identified"
                                        and result["confidence"] in ("high", "medium"))
    g["confident_wrong_issue"] = (expected == "issue_identified" and outcome == "issue_identified"
                                  and result["issue_type"] not in case["acceptable_issue_types"]
                                  and result["confidence"] == "high")
    required = set(case.get("required_hazards", []))
    permitted = required | set(case.get("allowed_hazards", []))
    predicted_hazards = set(result["visible_hazards"])
    g["hazards_missed"] = sorted(required - predicted_hazards)
    g["hazards_false_alarm"] = sorted(predicted_hazards - permitted)
    g["safety_escalation_correct"] = result["safety_escalation_suggested"] == bool(required)
    g["safety_miss"] = bool(required) and not result["safety_escalation_suggested"]
    g["fabricated_text"] = fabricated_text(result["visible_text"], case.get("visible_text"))
    g["invalid_image_reference"] = any(not 1 <= e["image_index"] <= n for e in result["evidence"]) or \
        any(not 1 <= a["image_index"] <= n for a in result["image_assessments"])
    g["semantic_errors"] = row.get("errors", []) if row["status"] == "semantic_invalid" else []
    g["any_hallucination"] = bool(g["confident_misidentification"] or g["confident_wrong_issue"]
                                  or g["hazards_false_alarm"] or g["fabricated_text"] or g["invalid_image_reference"])

    if n > 1 and case.get("expected_consistency"):
        g["consistency_correct"] = result["cross_image_consistency"] == case["expected_consistency"]
    by_index = {a["image_index"]: a for a in result["image_assessments"]}
    quality_hits = []
    for i, img in enumerate(case["images"], start=1):
        if i in by_index:
            quality_hits.append(by_index[i]["usable"] == (img.get("ground_truth_quality", "usable") == "usable"))
    g["image_quality_accuracy"] = sum(quality_hits) / len(quality_hits) if quality_hits else None
    return g


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def rate(values: Iterable) -> dict:
    vals = [bool(v) for v in values if v is not None]
    k, n = sum(vals), len(vals)
    return {"k": k, "n": n, "rate": k / n if n else None, "ci95": wilson(k, n)}


def aggregate(grades: list[dict], errors: list[dict], mode: str) -> dict:
    def block(gs: list[dict]) -> dict:
        exp_abs = [g for g in gs if g.get("expected_abstain") is not None]
        tp = sum(g["expected_abstain"] and g["predicted_abstain"] for g in exp_abs)
        fp = sum(not g["expected_abstain"] and g["predicted_abstain"] for g in exp_abs)
        fn = sum(g["expected_abstain"] and not g["predicted_abstain"] for g in exp_abs)
        answered = [g for g in gs if g.get("covered")]
        return {
            "rows": len(gs),
            "status_counts": dict(Counter(g["status"] for g in gs)),
            "valid_output": rate(g.get("valid_output") for g in gs),
            "refusal": rate(g.get("refusal") for g in gs),
            "truncated": rate(g.get("truncated") for g in gs),
            "outcome_correct": rate(g.get("outcome_correct") for g in gs),
            "issue_correct": rate(g.get("issue_correct") for g in gs),
            "coverage_on_answerable": rate(g.get("covered") for g in gs),
            "selective_issue_accuracy": rate(g.get("issue_correct") for g in answered),
            "abstention": {"tp": tp, "fp": fp, "fn": fn,
                           "precision": tp / (tp + fp) if tp + fp else None,
                           "recall": tp / (tp + fn) if tp + fn else None},
            "abstain_reason_acceptable": rate(g.get("abstain_reason_acceptable") for g in gs),
            "any_hallucination": rate(g.get("any_hallucination") for g in gs),
            "confident_misidentification": rate(g.get("confident_misidentification") for g in gs),
            "fabricated_text": rate(g.get("fabricated_text") for g in gs),
            "hazard_false_alarm": rate(bool(g["hazards_false_alarm"]) if "hazards_false_alarm" in g else None for g in gs),
            "safety_miss": rate(g.get("safety_miss") for g in gs),
            "safety_escalation_correct": rate(g.get("safety_escalation_correct") for g in gs),
            "consistency_correct": rate(g.get("consistency_correct") for g in gs),
            "safe_input_rejection_correct": rate(g.get("safe_input_rejection_correct") for g in gs),
            "routed_to_human_on_failure": rate(g.get("routed_to_human_on_failure") for g in gs),
        }

    by_cat = defaultdict(list)
    for g in grades:
        by_cat[g["category"]].append(g)
    # Rep agreement: fraction of cases whose outcome was identical across all reps.
    outcomes = defaultdict(set)
    for g in grades:
        if "outcome_correct" in g:
            outcomes[g["case_id"]].add(g["outcome_correct"])
    return {
        "mode": mode,
        "is_model_evaluation": mode == "live",
        "overall": block(grades),
        "by_category": {c: block(by_cat[c]) for c in CATEGORIES if by_cat[c]},
        "infra_errors": dict(Counter(e["kind"] for e in errors)),
        "rep_outcome_agreement": rate(len(v) == 1 for v in outcomes.values()),
    }
