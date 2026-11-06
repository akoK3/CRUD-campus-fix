"""Offline stand-ins for the Claude API. They never touch the network and measure the
harness, not Claude. Response objects mimic only the attributes the harness reads."""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Callable

from claude_client import VisionAPIError, VisionRequest


def make_response(payload: Any = None, *, text: str | None = None, stop_reason: str = "end_turn",
                  model: str = "claude-opus-5-5", input_tokens: int = 1500, output_tokens: int = 400,
                  stop_details: Any = None, fallback: bool = False) -> SimpleNamespace:
    body = text if text is not None else (json.dumps(payload) if payload is not None else "")
    iterations = [SimpleNamespace(type="fallback_message")] if fallback else []
    return SimpleNamespace(
        content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=body)],
        stop_reason=stop_reason, stop_details=stop_details, model=model, _request_id="req_mock",
        usage=SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens,
                              cache_read_input_tokens=0, cache_creation_input_tokens=0, iterations=iterations),
    )


def base_result(n_images: int, **overrides) -> dict:
    result = {
        "schema_version": "1.0", "outcome": "abstain", "abstain_reason": "insufficient_detail",
        "issue_type": None, "confidence": "low", "summary": "",
        "image_assessments": [{"image_index": i, "usable": True, "quality_problems": [], "description": ""}
                              for i in range(1, n_images + 1)],
        "evidence": [], "visible_hazards": [], "safety_escalation_suggested": False, "visible_text": [],
        "cross_image_consistency": "single_image" if n_images == 1 else "consistent", "uncertainties": [],
    }
    result.update(overrides)
    return result


def oracle_result(case: dict) -> dict:
    """The answer a perfect analyst would give, derived from the case's ground truth."""
    n = len(case["images"])
    quality = [img.get("ground_truth_quality", "usable") for img in case["images"]]
    problem_map = {"blurry": "blurry", "dark": "too_dark", "obstructed": "obstructed", "too_small": "too_small",
                   "irrelevant": "irrelevant_subject"}
    assessments = [{"image_index": i, "usable": q == "usable",
                    "quality_problems": [] if q == "usable" else [problem_map[q]], "description": "oracle"}
                   for i, q in enumerate(quality, start=1)]
    usable_index = next((i for i, q in enumerate(quality, start=1) if q == "usable"), 1)
    hazards = list(case.get("required_hazards", []))
    outcome = case["expected_outcome"]
    result = base_result(
        n, outcome=outcome, image_assessments=assessments, visible_hazards=hazards,
        safety_escalation_suggested=bool(hazards), visible_text=list(case.get("visible_text") or []),
        cross_image_consistency=case.get("expected_consistency") or ("single_image" if n == 1 else "consistent"),
        confidence="high" if outcome == "issue_identified" else "medium", summary="oracle",
    )
    if outcome == "issue_identified":
        result.update(issue_type=case["acceptable_issue_types"][0], abstain_reason=None,
                      evidence=[{"image_index": usable_index, "finding": "oracle evidence"}])
    elif outcome == "abstain":
        result.update(issue_type=None, abstain_reason=(case.get("acceptable_abstain_reasons") or ["insufficient_detail"])[0])
    else:
        result.update(issue_type=None, abstain_reason=None)
    return result


class CaseAwareClient:
    """Base for mock clients that need the case behind a request."""

    def __init__(self, cases: dict[str, dict]):
        self.cases = cases
        self.calls: list[VisionRequest] = []

    def analyze(self, request: VisionRequest) -> Any:
        self.calls.append(request)
        return make_response(self.answer(self.cases[request.case_id], request), model=request.model)

    def answer(self, case: dict, request: VisionRequest) -> dict:
        raise NotImplementedError


class OracleClient(CaseAwareClient):
    def answer(self, case, request):
        return oracle_result(case)


class AlwaysAbstainClient(CaseAwareClient):
    """Null baseline: abstains on everything."""

    def answer(self, case, request):
        n = len(request.images)
        return base_result(n, cross_image_consistency="single_image" if n == 1 else "consistent")


class OverconfidentClient(CaseAwareClient):
    """Degenerate policy: always 'Plumbing', high confidence, no hazards."""

    def answer(self, case, request):
        n = len(request.images)
        return base_result(n, outcome="issue_identified", abstain_reason=None, issue_type="Plumbing",
                           confidence="high", evidence=[{"image_index": 1, "finding": "water"}])


class ScriptedClient:
    """Returns scripted responses or raises scripted errors per case_id (sequence per call)."""

    def __init__(self, script: dict[str, list[Any | Callable[[], Any]]]):
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[VisionRequest] = []

    def analyze(self, request: VisionRequest) -> Any:
        self.calls.append(request)
        item = self.script[request.case_id].pop(0)
        if isinstance(item, VisionAPIError):
            raise item
        return item() if callable(item) else item
