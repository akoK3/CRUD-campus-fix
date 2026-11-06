"""Database-enforced control of AI classification runs.

The rules live in Postgres (supabase/migrations/20261105120000_ai_orchestration_safety.sql)
so they hold across processes, restarts, and concurrent requests:

  - claim:   atomic lease; at most one run per ticket at a time
  - commit:  applies a result only for the current lease holder, only if no
             person has routed the ticket, and only if the ticket is unchanged
             since the run read it; pages staff once for AI-URGENT tickets
  - release: frees a lease after a handled error (a crash lets it expire)
  - manual-request limits: cooldown and requester cap, checked atomically

This module is a thin wrapper over those service-role RPCs.
"""

from __future__ import annotations

import os
import socket
import uuid

from supabase import Client

from .config import Settings

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"

# Commit results returned by public.ai_commit_classification.
COMMITTED_AUTO_ROUTED = "COMMITTED_AUTO_ROUTED"
COMMITTED_NEEDS_REVIEW = "COMMITTED_NEEDS_REVIEW"
COMMITTED_FAILED = "COMMITTED_FAILED"
SUPERSEDED_BY_HUMAN = "SUPERSEDED_BY_HUMAN"
STALE_SNAPSHOT = "STALE_SNAPSHOT"
LEASE_LOST = "LEASE_LOST"
WITHDRAWN = "WITHDRAWN"
ALREADY_FINAL = "ALREADY_FINAL"


def _rpc(admin: Client, name: str, params: dict) -> dict:
    response = admin.rpc(name, params).execute()
    data = response.data
    if not isinstance(data, dict):
        raise RuntimeError(f"{name} returned an unexpected payload: {data!r}")
    return data


def claim(admin: Client, ticket_id: int, cfg: Settings, *, allow_failed: bool = False) -> dict:
    return _rpc(admin, "ai_claim_ticket", {
        "p_ticket_id": ticket_id,
        "p_worker": WORKER_ID,
        "p_lease_seconds": cfg.ai_lease_seconds,
        "p_allow_failed": allow_failed,
        "p_max_attempts": cfg.ai_max_attempts,
    })


def commit(
    admin: Client,
    ticket_id: int,
    claim_token: str,
    outcome: str,
    *,
    department_id: int | None = None,
    issue_type_id: int | None = None,
    priority_id: int | None = None,
    confidence: float | None = None,
) -> dict:
    return _rpc(admin, "ai_commit_classification", {
        "p_ticket_id": ticket_id,
        "p_claim_token": claim_token,
        "p_outcome": outcome,
        "p_department_id": department_id,
        "p_issue_type_id": issue_type_id,
        "p_priority_id": priority_id,
        "p_confidence": None if confidence is None else round(confidence, 4),
    })


def release(admin: Client, ticket_id: int, claim_token: str, error: str) -> None:
    admin.rpc("ai_release_claim", {
        "p_ticket_id": ticket_id,
        "p_claim_token": claim_token,
        "p_error": error[:1000],
    }).execute()


def request_reclassification(admin: Client, ticket_id: int, requested_by: str) -> dict:
    return _rpc(admin, "ai_request_reclassification", {
        "p_ticket_id": ticket_id,
        "p_requested_by": requested_by,
    })


def register_manual_request(admin: Client, ticket_id: int, *, is_staff: bool, cfg: Settings) -> dict:
    return _rpc(admin, "ai_register_manual_request", {
        "p_ticket_id": ticket_id,
        "p_is_staff": is_staff,
        "p_cooldown_seconds": cfg.ai_manual_cooldown_seconds,
        "p_requester_limit": cfg.ai_requester_manual_limit,
    })
