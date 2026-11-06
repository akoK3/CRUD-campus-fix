"""Claude Vision client, response interpretation, and safe failure handling.

``anthropic`` is imported only inside ``LiveClaudeVisionClient`` so mocked tests, CI, and
the production backend never load it (backend/tests/test_routing.py asserts the backend
does not import it).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import random
import time
from typing import Any, Protocol

from prompts import SYSTEM_PROMPT, build_content
import vision_schema

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "high"            # Opus 5.5 defaults to medium; set explicitly and record it
DEFAULT_MAX_TOKENS = 16000         # thinking counts toward max_tokens; avoid truncation
# USD per million tokens (Claude API, cached 2026-10-06). Verify before relying on cost numbers.
PRICES = {"claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20, "cache_write": 5.00}}

OK = "ok"
MODEL_OUTPUT_FAILURES = ("empty_output", "invalid_json", "schema_invalid", "semantic_invalid")
STATUSES = (OK, "refusal", "truncated", "model_mismatch", "unexpected_stop", *MODEL_OUTPUT_FAILURES)


class VisionAPIError(Exception):
    """Infrastructure failure (never scored as a model answer)."""

    def __init__(self, kind: str, message: str, retryable: bool):
        super().__init__(message)
        self.kind, self.retryable = kind, retryable


@dataclass
class VisionRequest:
    case_id: str
    images: list[tuple[str, str]]  # (media_type, base64)
    ticket_text: str | None
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    max_tokens: int = DEFAULT_MAX_TOKENS


class VisionClient(Protocol):
    def analyze(self, request: VisionRequest) -> Any: ...


@dataclass
class Interpretation:
    status: str
    result: dict | None = None
    errors: list[str] = field(default_factory=list)
    stop_reason: str | None = None
    served_model: str | None = None
    usage: dict = field(default_factory=dict)
    raw_text: str | None = None
    request_id: str | None = None
    fallback_used: bool = False


def _usage(response: Any) -> dict:
    u = getattr(response, "usage", None)
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    return {k: int(getattr(u, k, 0) or 0) for k in keys} if u is not None else {}


def interpret_response(response: Any, requested_model: str, n_images: int, schema: dict) -> Interpretation:
    """Classify a Messages API response. Never raises on malformed model output."""
    out = Interpretation(status="unexpected_stop", stop_reason=getattr(response, "stop_reason", None),
                         served_model=getattr(response, "model", None), usage=_usage(response),
                         request_id=getattr(response, "_request_id", None))
    iterations = getattr(getattr(response, "usage", None), "iterations", None) or []
    out.fallback_used = any(getattr(i, "type", None) == "fallback_message" for i in iterations)
    if out.served_model != requested_model or out.fallback_used:
        out.status = "model_mismatch"
        out.errors.append(f"requested {requested_model}, served {out.served_model}, fallback={out.fallback_used}")
        return out
    if out.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        out.status = "refusal"
        out.errors.append(f"refusal category={getattr(details, 'category', None)}")
        return out
    if out.stop_reason == "max_tokens":
        out.status = "truncated"
        return out
    if out.stop_reason != "end_turn":
        out.errors.append(f"stop_reason={out.stop_reason}")
        return out
    text = next((b.text for b in getattr(response, "content", []) if getattr(b, "type", None) == "text"), None)
    out.raw_text = text
    if not text or not text.strip():
        out.status = "empty_output"
        return out
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        out.status = "invalid_json"
        out.errors.append(str(error))
        return out
    schema_errors = vision_schema.validate(parsed, schema)
    if schema_errors:
        out.status = "schema_invalid"
        out.errors = schema_errors
        return out
    out.result = parsed
    semantic = vision_schema.semantic_errors(parsed, n_images)
    if semantic:
        out.status = "semantic_invalid"
        out.errors = semantic
        return out
    out.status = OK
    return out


def routing_decision(status: str, result: dict | None) -> dict:
    """What CampusFix should do with a vision analysis. Safe by construction:
    anything other than a valid, confident identification goes to a human."""
    if status != OK or result is None:
        return {"action": "MANUAL_REVIEW", "safety_escalation": False, "reason": f"vision_{status}"}
    safety = bool(result["safety_escalation_suggested"])
    if result["outcome"] == "abstain":
        retake = result["abstain_reason"] in ("too_blurry", "too_dark", "obstructed")
        action = "REQUEST_NEW_PHOTO" if retake and not safety else "MANUAL_REVIEW"
        return {"action": action, "safety_escalation": safety, "reason": result["abstain_reason"]}
    if result["outcome"] == "no_issue_visible":
        return {"action": "MANUAL_REVIEW", "safety_escalation": safety, "reason": "no_issue_visible"}
    if result["confidence"] == "low":
        return {"action": "MANUAL_REVIEW", "safety_escalation": safety, "reason": "low_confidence"}
    return {"action": "SUGGEST_ISSUE_TYPE", "safety_escalation": safety, "issue_type": result["issue_type"],
            "reason": f"confidence_{result['confidence']}"}


def cost_usd(usage: dict, model: str) -> float | None:
    price = PRICES.get(model)
    if price is None or not usage:
        return None
    return (usage.get("input_tokens", 0) * price["input"] + usage.get("output_tokens", 0) * price["output"]
            + usage.get("cache_read_input_tokens", 0) * price["cache_read"]
            + usage.get("cache_creation_input_tokens", 0) * price["cache_write"]) / 1_000_000


def build_request_kwargs(request: VisionRequest, schema: dict) -> dict:
    """Messages API arguments. The system prompt carries the cache breakpoint; per-case images
    come after it, so they never invalidate (or needlessly write) the cached prefix."""
    return {
        "model": request.model,
        "max_tokens": request.max_tokens,
        "system": [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": build_content(request.images, request.ticket_text)}],
        "output_config": {"effort": request.effort,
                          "format": {"type": "json_schema", "schema": vision_schema.schema_for_api(schema)}},
    }


def call_with_retries(fn, max_attempts: int = 4, base_delay: float = 2.0, sleep=time.sleep):
    """Retry retryable VisionAPIErrors with jittered exponential backoff. Returns (value, attempts)."""
    for attempt in range(1, max_attempts + 1):
        try:
            return fn(), attempt
        except VisionAPIError as error:
            if not error.retryable or attempt == max_attempts:
                error.attempts = attempt
                raise
            sleep(base_delay * 2 ** (attempt - 1) * (0.5 + random.random()))
    raise AssertionError("unreachable")


class LiveClaudeVisionClient:
    """Real Claude API calls. Constructed only by the runner in --live mode."""

    def __init__(self, schema: dict, timeout_seconds: float = 180.0, fallbacks: bool = False):
        import anthropic  # local import: keep the SDK out of every other code path

        self._anthropic = anthropic
        # SDK retries disabled: the runner retries with jitter and records attempt counts.
        self._client = anthropic.Anthropic(timeout=timeout_seconds, max_retries=0)
        self._schema = schema
        self._fallbacks = fallbacks

    def analyze(self, request: VisionRequest) -> Any:
        a = self._anthropic
        kwargs = build_request_kwargs(request, self._schema)
        try:
            if self._fallbacks:
                return self._client.beta.messages.create(
                    betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
            return self._client.messages.create(**kwargs)
        except a.RateLimitError as e:
            raise VisionAPIError("rate_limited", str(e), True) from e
        except a.APITimeoutError as e:
            raise VisionAPIError("timeout", str(e), True) from e
        except a.APIConnectionError as e:
            raise VisionAPIError("connection", str(e), True) from e
        except a.AuthenticationError as e:
            raise VisionAPIError("authentication", str(e), False) from e
        except a.PermissionDeniedError as e:
            raise VisionAPIError("permission", str(e), False) from e
        except a.BadRequestError as e:
            raise VisionAPIError("bad_request", str(e), False) from e
        except a.APIStatusError as e:
            retryable = e.status_code >= 500
            raise VisionAPIError("server_error" if retryable else "api_error", str(e), retryable) from e
