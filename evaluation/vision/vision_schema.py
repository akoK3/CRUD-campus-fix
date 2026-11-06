"""Schema loading, a stdlib JSON-Schema validator for the subset we use, and semantic rules.

The validator is deliberately small: it supports exactly the keywords that Claude
structured outputs accepts and that ``vision_result.schema.json`` uses. Tests cross-check
it against the ``jsonschema`` package when that package is installed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE / "schema" / "vision_result.schema.json"

# Keywords we allow anywhere in the schema (structured-outputs-compatible subset).
ALLOWED_KEYWORDS = frozenset({
    "$schema", "$id", "title", "description", "type", "properties", "required",
    "additionalProperties", "items", "enum", "anyOf",
})
_TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool, "null": type(None),
}


def load_schema(path: Path = SCHEMA_PATH) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def schema_for_api(schema: dict) -> dict:
    """The schema sent in ``output_config.format`` (metadata keys removed)."""
    return {k: v for k, v in schema.items() if k not in ("$schema", "$id")}


def keyword_violations(schema: Any, path: str = "$") -> list[str]:
    """Keywords outside the supported subset; objects missing additionalProperties:false."""
    out: list[str] = []
    if isinstance(schema, dict):
        for key in schema:
            if key not in ALLOWED_KEYWORDS:
                out.append(f"{path}: unsupported keyword {key!r}")
        if schema.get("type") == "object" and schema.get("additionalProperties") is not False:
            out.append(f"{path}: object without additionalProperties: false")
        if schema.get("type") == "object" and set(schema.get("required", [])) != set(schema.get("properties", {})):
            out.append(f"{path}: every property must be required")
        for key, value in schema.get("properties", {}).items():
            out += keyword_violations(value, f"{path}.{key}")
        if "items" in schema:
            out += keyword_violations(schema["items"], f"{path}[]")
        for i, option in enumerate(schema.get("anyOf", [])):
            out += keyword_violations(option, f"{path}|{i}")
    return out


def _is_type(value: Any, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if name in ("object", "array", "string", "null"):
        return isinstance(value, _TYPES[name])
    if name == "boolean":
        return isinstance(value, bool)
    raise ValueError(f"unsupported type {name}")


def validate(instance: Any, schema: dict, path: str = "$") -> list[str]:
    """Return schema errors (empty list = valid)."""
    if "anyOf" in schema:
        if any(not validate(instance, option, path) for option in schema["anyOf"]):
            return []
        return [f"{path}: matches no anyOf option"]
    errors: list[str] = []
    expected = schema.get("type")
    if expected is not None and not _is_type(instance, expected):
        return [f"{path}: expected {expected}, got {type(instance).__name__}"]
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} not in enum")
    if expected == "object":
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required {key!r}")
        if schema.get("additionalProperties") is False:
            for key in instance:
                if key not in props:
                    errors.append(f"{path}: unexpected property {key!r}")
        for key, value in instance.items():
            if key in props:
                errors += validate(value, props[key], f"{path}.{key}")
    if expected == "array" and "items" in schema:
        for i, item in enumerate(instance):
            errors += validate(item, schema["items"], f"{path}[{i}]")
    return errors


def semantic_errors(result: dict, n_images: int) -> list[str]:
    """Business rules a schema cannot express. Assumes ``result`` is schema-valid."""
    errs: list[str] = []
    outcome, issue, reason = result["outcome"], result["issue_type"], result["abstain_reason"]
    assessments = result["image_assessments"]

    if outcome == "issue_identified":
        if issue is None:
            errs.append("S1: issue_identified requires issue_type")
        if reason is not None:
            errs.append("S1: issue_identified must not carry abstain_reason")
        if not result["evidence"]:
            errs.append("S1: issue_identified requires at least one evidence item")
    elif outcome == "abstain":
        if reason is None:
            errs.append("S2: abstain requires abstain_reason")
        if issue is not None:
            errs.append("S2: abstain must not carry issue_type")
    else:  # no_issue_visible
        if issue is not None or reason is not None:
            errs.append("S3: no_issue_visible carries neither issue_type nor abstain_reason")

    indices = [a["image_index"] for a in assessments]
    if sorted(indices) != list(range(1, n_images + 1)):
        errs.append(f"S4: image_assessments must cover images 1..{n_images} exactly once, got {indices}")
    unusable = {a["image_index"] for a in assessments if not a["usable"]}
    for item in result["evidence"]:
        if not 1 <= item["image_index"] <= n_images:
            errs.append(f"S5: evidence cites nonexistent image {item['image_index']}")
        elif item["image_index"] in unusable and outcome == "issue_identified":
            errs.append(f"S5: evidence cites image {item['image_index']} that was assessed unusable")

    if result["safety_escalation_suggested"] != bool(result["visible_hazards"]):
        errs.append("S6: safety_escalation_suggested must equal (visible_hazards non-empty)")
    if (result["cross_image_consistency"] == "single_image") != (n_images == 1):
        errs.append("S7: cross_image_consistency single_image iff exactly one image")
    for a in assessments:
        if not a["usable"] and not a["quality_problems"]:
            errs.append(f"S8: image {a['image_index']} unusable without a quality problem")
        if len(set(a["quality_problems"])) != len(a["quality_problems"]):
            errs.append(f"S10: duplicate quality problems on image {a['image_index']}")
    if assessments and len(unusable) == len(assessments) and outcome != "abstain":
        errs.append("S9: when every image is unusable the outcome must be abstain")
    if len(set(result["visible_hazards"])) != len(result["visible_hazards"]):
        errs.append("S10: duplicate hazards")
    if result["cross_image_consistency"] == "conflicting" and outcome != "abstain":
        errs.append("S11: conflicting images must abstain (multi-issue goes to review)")
    return errs
