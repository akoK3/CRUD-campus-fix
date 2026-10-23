"""CampusFix final classifier: DeepSeek-R1 8B issue + priority."""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from dataclasses import dataclass

from supabase import Client

from .config import Settings

log = logging.getLogger(__name__)

_REF_CACHE = None


@dataclass
class Classification:
    department_id: int | None
    issue_type_id: int | None
    priority_id: int | None
    confidence: float
    needs_review: bool = False


def _reference_data(admin: Client) -> dict:
    global _REF_CACHE
    if _REF_CACHE is None:
        _REF_CACHE = {
            "departments": admin.table("departments").select(
                "department_id,name,description"
            ).eq("active", True).execute().data or [],
            "issue_types": admin.table("issue_types").select(
                "issue_type_id,name,description,default_department_id"
            ).execute().data or [],
            "priorities": admin.table("priorities").select(
                "priority_id,name,level"
            ).execute().data or [],
        }
    return _REF_CACHE

def _department_id(departments, name):
    for row in departments:
        if row.get("name") == name:
            return row.get("department_id")
    return None

def _classify_with_deepseek(
    text: str, issue_names: list[str], settings: Settings
) -> tuple[str, str]:
    prompt = (
        "You classify CampusFix university support tickets. "
        "Return ONLY valid JSON: {\"issue_type\":\"...\",\"priority\":\"...\"}. "
        "Allowed issue types: " + ", ".join(issue_names) + ". "
        "Allowed priorities: LOW, MEDIUM, HIGH, URGENT, NEEDS_REVIEW. "
        "LOW=minor/cosmetic inconvenience. "
        "MEDIUM=functional problem that can wait. "
        "HIGH=major disruption or serious near-term consequence. "
        "URGENT=immediate credible safety/security risk, active serious damage, "
        "active dorm lockout/access emergency, or critical large-scope outage. "
        "NEEDS_REVIEW=too vague, multiple distinct problems, or no matching category. "
        "Ignore emotional wording like ASAP/urgent unless facts justify it. "
        "Real deadlines, recurrence, accessibility, and scope may raise priority. "
        "Planned outages lower priority. "
        "Dorm lockout/lost room key => Door / Lock + URGENT. "
        "Vending problems => Vending."
    )

    payload = {
        "model": settings.ollama_model,
        "stream": False,
        "think": False,
        "keep_alive": "30m",
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": text},
        ],
        "options": {"temperature": 0},
    }

    req = urllib.request.Request(
        settings.ollama_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=settings.ollama_timeout_seconds) as response:
        data = json.loads(response.read().decode("utf-8"))

    content = data["message"]["content"].strip()
    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        raise ValueError(f"DeepSeek returned no JSON: {content[:300]}")

    obj = json.loads(match.group(0))
    return str(obj.get("issue_type", "")).strip(), str(obj.get("priority", "")).strip()

def classify(ticket: dict, admin: Client, settings: Settings) -> Classification | None:
    if not settings.ai_enabled:
        return None

    try:
        ref = _reference_data(admin)
        departments = ref["departments"]
        issue_types = ref["issue_types"]
        priorities = ref["priorities"]

        if not departments or not issue_types or not priorities:
            return None

        issue_lookup = {row["name"]: row for row in issue_types}
        priority_lookup = {row["name"]: row for row in priorities}
        valid_depts = {row["department_id"] for row in departments}

        text = "\n".join(
            part
            for part in [
                str(ticket.get("title") or "").strip(),
                str(ticket.get("description") or ticket.get("original_text") or "").strip(),
            ]
            if part
        )
        if not text:
            return None

        issue_name, priority_name = _classify_with_deepseek(
            text, list(issue_lookup.keys()), settings
        )

        if issue_name not in issue_lookup:
            issue_name = "Other"
        if priority_name not in {"LOW", "MEDIUM", "HIGH", "URGENT", "NEEDS_REVIEW"}:
            priority_name = "NEEDS_REVIEW"

        # Any explicit review decision routes safely to Other / Review.
        if priority_name == "NEEDS_REVIEW" or issue_name == "Other":
            issue = issue_lookup.get("Other")
            priority = priority_lookup.get("MEDIUM")
            dept = _department_id(departments, "Other / Review")
            if not issue or not priority:
                return None
            return Classification(
                dept,
                issue["issue_type_id"],
                priority["priority_id"],
                0.0,
                True,
            )

        issue = issue_lookup[issue_name]
        priority = priority_lookup.get(priority_name)
        if not priority:
            return None

        dept = issue.get("default_department_id")

        # Campus-specific routing exceptions.
        if issue_name == "Vending":
            dept = _department_id(departments, "IT")
        elif issue_name == "Door / Lock" and re.search(
            r"\b(?:dorm|residence|housing|room)\b.*\b(?:locked out|lost .*key)\b|"
            r"\b(?:locked out|lost .*key)\b.*\b(?:dorm|residence|housing|room)\b",
            text, re.I
        ):
            dept = _department_id(departments, "Residence Life")

        if dept not in valid_depts:
            dept = None

        # DeepSeek's output is not calibrated probability.
        # Non-review results are marked 1.0 only to preserve existing main.py routing flow.
        return Classification(
            dept,
            issue["issue_type_id"],
            priority["priority_id"],
            1.0,
            False,
        )

    except Exception as exc:
        log.warning(
            "DeepSeek classification failed for ticket %s: %s",
            ticket.get("ticket_id"),
            exc,
        )
        return None
