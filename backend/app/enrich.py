"""AI writing enrichment for CampusFix tickets.

Separate from routing/classification:
- routing stays valid even if enrichment fails
- original_text preserves the student's raw wording
- title/description are polished for staff readability
"""

from __future__ import annotations

import json
import logging
import re
import urllib.request

from .config import Settings, get_settings

log = logging.getLogger(__name__)


def _generate(
    text: str,
    issue_name: str | None,
    priority_name: str | None,
    settings: Settings,
) -> dict:
    system = """You rewrite CampusFix university support tickets for staff.

Return ONLY valid JSON with exactly these keys:
{
  "title": "...",
  "description": "...",
  "explanation": "..."
}

Rules:
- Preserve all facts from the student's submission.
- Do NOT invent locations, causes, people, deadlines, hazards, or technical details.
- title: concise, professional, ideally 4-10 words.
- description: clear professional rewrite, normally 1-3 sentences.
- explanation: one concise sentence explaining why the ticket fits the supplied issue type and priority.
- Do not mention AI, models, confidence scores, prompts, or internal systems.
- If the student's wording is already clear, improve it lightly rather than changing meaning.
"""

    user = (
        f"Student submission:\n{text}\n\n"
        f"Selected issue type: {issue_name or 'Unknown'}\n"
        f"Selected priority: {priority_name or 'Unknown'}"
    )

    payload = {
        "model": settings.ollama_model,
        "stream": False,
        "think": False,
        "keep_alive": "30m",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
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

    title = str(obj.get("title") or "").strip()
    description = str(obj.get("description") or "").strip()
    explanation = str(obj.get("explanation") or "").strip()

    if not title or not description:
        raise ValueError("DeepSeek enrichment omitted title or description")

    return {
        "title": title[:180],
        "description": description,
        "explanation": explanation or None,
    }


def enrich_ticket(
    ticket: dict,
    admin,
    *,
    issue_type_id: int | None,
    priority_id: int | None,
    settings: Settings | None = None,
) -> bool:
    """Polish one ticket after routing. Returns True on success, False on failure."""

    try:
        raw_title = str(ticket.get("title") or "").strip()
        raw_description = str(ticket.get("description") or "").strip()

        # Preserve an existing submission exactly, including its whitespace.
        raw_text = str(ticket.get("original_text") or "")
        if not raw_text.strip():
            raw_text = "\n".join(
                part for part in (raw_title, raw_description) if part
            ).strip()

        if not raw_text:
            return False

        issue_name = None
        priority_name = None

        if issue_type_id is not None:
            r = (
                admin.table("issue_types")
                .select("name")
                .eq("issue_type_id", issue_type_id)
                .maybe_single()
                .execute()
            )
            if r and r.data:
                issue_name = r.data.get("name")

        if priority_id is not None:
            r = (
                admin.table("priorities")
                .select("name")
                .eq("priority_id", priority_id)
                .maybe_single()
                .execute()
            )
            if r and r.data:
                priority_name = r.data.get("name")

        polished = _generate(raw_text, issue_name, priority_name, settings or get_settings())

        update = {
            "original_text": raw_text,
            "title": polished["title"],
            "description": polished["description"],
            "ai_explanation": polished["explanation"],
        }

        admin.table("tickets").update(update).eq(
            "ticket_id", ticket["ticket_id"]
        ).execute()

        return True

    except Exception as exc:
        log.warning(
            "AI enrichment failed for ticket %s: %s",
            ticket.get("ticket_id"),
            exc,
        )
        return False
