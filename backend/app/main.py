"""CampusFix backend.

Scope is deliberately narrow. The React client talks to Supabase directly for
reads and writes, where RLS enforces authorization. This service exists only
for work that needs a secret or a model call:

  - AI classification and routing
  - email delivery and retry for notification rows (app/notify.py)

Because it holds the service_role key it runs with RLS disabled, so every
endpoint re-checks access explicitly. See app/auth.py.
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel
from supabase import Client, create_client

from . import ai_jobs
from .auth import Caller, admin_client, assert_can_access_ticket, current_caller
from .classify import classify
from .config import Settings, get_settings
from .enrich import enrich_ticket
from .notify import dispatch_loop, dispatch_pending

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("campusfix")

settings = get_settings()

PENDING_CLASSIFICATION_DELAY = timedelta(seconds=5)
CLASSIFICATION_CHECK_INTERVAL_SECONDS = 7
CLASSIFICATION_BATCH_SIZE = 3


@asynccontextmanager
async def lifespan(_: FastAPI):
    tasks = []
    if settings.ai_enabled:
        tasks.append(asyncio.create_task(classification_loop(settings)))
    if settings.email_enabled:
        tasks.append(asyncio.create_task(dispatch_loop(settings)))
    if not settings.email_enabled:
        log.info("Email notifications disabled (SMTP_HOST or service role key not set).")
    yield
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="CampusFix API", version="0.1.0", lifespan=lifespan)


class Health(BaseModel):
    status: str
    ai_enabled: bool
    admin_enabled: bool
    email_enabled: bool


class ClassifyResult(BaseModel):
    ticket_id: int
    routing_status: str
    department_id: int | None = None
    priority_id: int | None = None
    issue_type_id: int | None = None
    confidence: float | None = None
    detail: str


@app.get("/api/health", response_model=Health)
def health() -> Health:
    return Health(
        status="ok",
        ai_enabled=settings.ai_enabled,
        admin_enabled=settings.admin_enabled,
        email_enabled=settings.email_enabled,
    )


class DispatchResult(BaseModel):
    sent: int
    failed: int
    skipped: int


@app.post("/api/notifications/dispatch", response_model=DispatchResult)
def dispatch_notifications(
    caller: Caller = Depends(current_caller),
    admin: Client = Depends(admin_client),
    cfg: Settings = Depends(get_settings),
) -> DispatchResult:
    """Send pending notification emails now instead of waiting for the loop.

    System admins only: it acts on every user's notifications.
    """
    if not caller.is_system_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "System admins only")
    if not cfg.smtp_host:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "SMTP_HOST is not configured.")
    return DispatchResult(**dispatch_pending(admin, cfg))


# Outcomes of a commit that did not apply the model's result, as API responses.
_NOT_APPLIED = {
    ai_jobs.SUPERSEDED_BY_HUMAN: (
        "OVERRIDDEN", "A staff member already routed this ticket; the AI result was kept as a suggestion only."),
    ai_jobs.STALE_SNAPSHOT: (
        "PENDING", "The ticket changed while it was being classified; it will be classified again."),
    ai_jobs.LEASE_LOST: (
        "PENDING", "Classification outlived its lease; the result was discarded and will be retried."),
}

# Claim refusals, as API responses (not_found and withdrawn are raised as errors).
_NOT_CLAIMED = {
    "in_progress": ("PENDING", "Classification is already in progress."),
    "superseded_by_human": ("OVERRIDDEN", "A staff member already routed this ticket."),
    "attempts_exhausted": ("FAILED", "Automatic classification failed repeatedly; routed for manual review."),
}


def _process_classification(
    ticket: dict, admin: Client, cfg: Settings, claim_token: str
) -> ClassifyResult:
    """Classify a claimed ticket and commit through the database's guarded write.

    The commit applies nothing if the lease was lost, a person has routed the
    ticket, or the ticket changed since it was read. Every branch leaves the
    ticket in a state a human can act on.
    """
    ticket_id = ticket["ticket_id"]
    result = classify(ticket, admin, cfg)

    if result is None:
        committed = ai_jobs.commit(admin, ticket_id, claim_token, "FAILED")
        if committed["result"] == ai_jobs.COMMITTED_FAILED:
            return ClassifyResult(
                ticket_id=ticket_id,
                routing_status="FAILED",
                detail="Classifier unavailable; routed for manual review.",
            )
        return _not_applied(ticket, committed)

    confident = (
        not result.needs_review
        and result.confidence > 0
        and result.confidence >= cfg.ai_confidence_threshold
        and result.department_id is not None
    )
    committed = ai_jobs.commit(
        admin,
        ticket_id,
        claim_token,
        "ROUTED" if confident else "REVIEW",
        department_id=result.department_id,
        issue_type_id=result.issue_type_id,
        priority_id=result.priority_id,
        confidence=result.confidence,
    )
    outcome = committed["result"]
    if outcome not in (ai_jobs.COMMITTED_AUTO_ROUTED, ai_jobs.COMMITTED_NEEDS_REVIEW):
        return _not_applied(ticket, committed)
    if committed.get("urgent_notifications"):
        log.info("AI-URGENT ticket %s paged %s staff", ticket_id, committed["urgent_notifications"])
    if committed.get("notification_error"):
        log.warning("AI-URGENT notification failed for ticket %s: %s", ticket_id, committed["notification_error"])

    routed = outcome == ai_jobs.COMMITTED_AUTO_ROUTED
    # Writing enrichment is intentionally separate from routing. If it fails,
    # the classification above remains valid and the student's text is untouched.
    if routed:
        try:
            enrich_ticket(
                ticket,
                admin,
                issue_type_id=result.issue_type_id,
                priority_id=result.priority_id,
                settings=cfg,
            )
        except Exception as exc:
            log.warning("Enrichment failed after routing ticket %s: %s", ticket_id, exc)

    return ClassifyResult(
        ticket_id=ticket_id,
        routing_status="AUTO_ROUTED" if routed else "NEEDS_REVIEW",
        department_id=result.department_id,
        priority_id=result.priority_id,
        issue_type_id=result.issue_type_id,
        confidence=round(result.confidence, 4),
        detail="Routed automatically." if routed else "Classification requires manual review.",
    )


def _not_applied(ticket: dict, committed: dict) -> ClassifyResult:
    outcome = committed.get("result")
    log.info("AI result for ticket %s not applied: %s", ticket["ticket_id"], outcome)
    if outcome in _NOT_APPLIED:
        routing_status, detail = _NOT_APPLIED[outcome]
    else:  # WITHDRAWN, ALREADY_FINAL, NOT_FOUND
        routing_status, detail = ticket.get("ai_routing_status", "PENDING"), f"Not applied ({outcome})."
    return ClassifyResult(ticket_id=ticket["ticket_id"], routing_status=routing_status, detail=detail)


def _classify_pending_ticket(
    ticket_id: int, admin: Client, cfg: Settings, *, allow_failed: bool = False
) -> ClassifyResult:
    claimed = ai_jobs.claim(admin, ticket_id, cfg, allow_failed=allow_failed)
    if not claimed.get("claimed"):
        reason = claimed.get("reason")
        if reason == "not_found":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
        if reason == "withdrawn":
            raise HTTPException(status.HTTP_409_CONFLICT, "Ticket was withdrawn")
        if reason == "not_pending":
            return ClassifyResult(
                ticket_id=ticket_id,
                routing_status=claimed.get("ai_routing_status", "PENDING"),
                detail="Already classified.",
            )
        routing_status, detail = _NOT_CLAIMED.get(reason, ("PENDING", f"Not claimed ({reason})."))
        return ClassifyResult(ticket_id=ticket_id, routing_status=routing_status, detail=detail)

    token = claimed["claim_token"]
    try:
        return _process_classification(claimed["ticket"], admin, cfg, token)
    except Exception as exc:
        # The lease would expire on its own; releasing it lets the next attempt
        # start promptly. Releasing never applies a partial result.
        try:
            ai_jobs.release(admin, ticket_id, token, f"{type(exc).__name__}: {exc}")
        except Exception:
            log.exception("Could not release AI claim for ticket %s", ticket_id)
        raise


async def classification_loop(cfg: Settings) -> None:
    """Route saved pending tickets without requiring a browser request."""
    admin = create_client(cfg.supabase_url, cfg.supabase_service_role_key)
    while True:
        await asyncio.sleep(CLASSIFICATION_CHECK_INTERVAL_SECONDS)
        try:
            cutoff = (datetime.now(timezone.utc) - PENDING_CLASSIFICATION_DELAY).isoformat()
            rows = (
                admin.table("tickets")
                .select("ticket_id")
                .eq("ai_routing_status", "PENDING")
                .is_("deleted_at", "null")
                .lt("created_at", cutoff)
                .order("created_at", desc=True)
                .limit(CLASSIFICATION_BATCH_SIZE)
                .execute()
            ).data or []
            for row in rows:
                result = await asyncio.to_thread(
                    _classify_pending_ticket, row["ticket_id"], admin, cfg
                )
                log.info(
                    "Pending ticket recovery completed ticket=%s routing_status=%s",
                    result.ticket_id,
                    result.routing_status,
                )
        except Exception:
            log.exception("Pending ticket recovery failed")


@app.post("/api/tickets/{ticket_id}/classify", response_model=ClassifyResult)
def classify_ticket(
    ticket_id: int,
    caller: Caller = Depends(current_caller),
    admin: Client = Depends(admin_client),
    cfg: Settings = Depends(get_settings),
) -> ClassifyResult:
    """Classify a ticket and route it on request.

    The browser does not call this (the worker routes pending tickets); it is
    for authorized manual requests. Every branch leaves the ticket in a state a
    human can act on. Requests are rate-limited per ticket in the database.
    """
    ticket = assert_can_access_ticket(admin, caller, ticket_id)
    if ticket.get("deleted_at"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Ticket was withdrawn")

    # Only the requester or staff may trigger this, and only while unrouted.
    # Checked before the rate limit so a no-op request costs nothing.
    if ticket["ai_routing_status"] not in ("PENDING", "FAILED"):
        return ClassifyResult(
            ticket_id=ticket_id,
            routing_status=ticket["ai_routing_status"],
            detail="Already classified.",
        )
    _enforce_manual_limit(admin, ticket_id, caller, cfg)
    return _classify_pending_ticket(ticket_id, admin, cfg, allow_failed=True)


def _enforce_manual_limit(admin: Client, ticket_id: int, caller: Caller, cfg: Settings) -> None:
    decision = ai_jobs.register_manual_request(admin, ticket_id, is_staff=caller.is_staff, cfg=cfg)
    if decision.get("allowed"):
        return
    reason = decision.get("reason")
    if reason == "not_found":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
    if reason == "cooldown":
        retry_after = str(decision.get("retry_after_seconds", cfg.ai_manual_cooldown_seconds))
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Classification was requested recently for this ticket. Try again shortly.",
            headers={"Retry-After": retry_after},
        )
    raise HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "The limit of classification requests for this ticket has been reached; staff will review it.",
    )


@app.post("/api/tickets/{ticket_id}/reclassify", response_model=ClassifyResult)
def reclassify_ticket(
    ticket_id: int,
    caller: Caller = Depends(current_caller),
    admin: Client = Depends(admin_client),
    cfg: Settings = Depends(get_settings),
) -> ClassifyResult:
    """Staff-only re-run, for a ticket that failed or was misrouted.

    The request becomes the new baseline: earlier routing decisions no longer
    block the AI, but anything a person does after this request still does, and
    a ticket that is already being worked on is never re-routed.
    """
    if not caller.is_staff:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Staff only")

    ticket = assert_can_access_ticket(admin, caller, ticket_id)
    if ticket.get("deleted_at"):
        raise HTTPException(status.HTTP_409_CONFLICT, "Ticket was withdrawn")
    _enforce_manual_limit(admin, ticket_id, caller, cfg)

    requested = ai_jobs.request_reclassification(admin, ticket_id, caller.user_id)
    if not requested.get("ok"):
        reason = requested.get("reason")
        if reason == "not_found":
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Ticket not found")
        if reason == "withdrawn":
            raise HTTPException(status.HTTP_409_CONFLICT, "Ticket was withdrawn")
        return ClassifyResult(
            ticket_id=ticket_id, routing_status="PENDING", detail="Classification is already in progress."
        )
    return _classify_pending_ticket(ticket_id, admin, cfg)
