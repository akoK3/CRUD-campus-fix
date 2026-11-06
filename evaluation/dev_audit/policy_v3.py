"""CampusFix classification policy v3 (draft): machine-checkable rules.

Two kinds of checks:

* ``hard_violations`` - structural rules every v3 label row must satisfy. These are
  exact and must return no violations for an approved benchmark.
* ``lint_warnings`` - text heuristics that point a human at rows that *may* violate
  a policy decision (D1-D9). They are deliberately conservative prompts for review,
  not classifiers; a warning is resolved either by a label change or by an explicit,
  justified entry in ``v3_proposal/lint_exceptions.csv``.

Rule IDs match ``CLASSIFICATION_POLICY_v3.md``. Standard library only; no models.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import sys
from pathlib import Path
from typing import Iterable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze_development import (  # noqa: E402
    AUTO, DEPARTMENTS, ISSUE_TO_DEPT, ISSUE_TYPES, ORDINAL, PRIORITIES_5, RANK, REVIEW,
)

POLICY_VERSION = "3.0.0-draft"

# Every ID below must be defined in CLASSIFICATION_POLICY_v3.md (checked by tests).
DECISION_IDS = frozenset(f"D{i}" for i in range(1, 10))
RULE_IDS = DECISION_IDS | frozenset({
    "R-SCH-1",
    "R-ISS-SEC", "R-ISS-PARK", "R-ISS-FIN",
    "R-DEP-1", "R-DEP-2", "R-DEP-5",
    "R-PRI-BASE", "R-PRI-SEC", "R-PRI-URG",
    "R-SAF-1", "R-SAF-2", "R-SAF-4",
    "R-REV-1", "R-REV-2", "R-REV-3", "R-REV-4",
    "R-ACC-1",
})
UNRESOLVED_IDS = frozenset(f"U-{i:02d}" for i in range(1, 18))

REVIEW_REASONS = frozenset({"insufficient_detail", "multiple_issues", "no_matching_category"})
BOOL_VALUES = {"true": True, "false": False, "": None}  # blank = not assessed
SAFETY_DEPARTMENTS = frozenset({"Facilities", "Campus Safety"})
# Issue types that are "ordinary equipment" whose routing D6 may override.
EQUIPMENT_ISSUES = frozenset({"Vending", "Laundry", "Computer / Hardware", "Classroom Technology", "Printing"})
# In-room fixtures/finishes covered by D3 when inside a residence room.
RESIDENCE_ROOM_ISSUES = frozenset({"Furniture", "Structural"})


@dataclass(frozen=True)
class Finding:
    rule: str
    case_id: str
    message: str


def _flag(row: Mapping[str, str], name: str) -> bool | None:
    value = (row.get(name) or "").strip().lower()
    if value not in BOOL_VALUES:
        raise ValueError(f"{row.get('case_id')}: {name} must be true/false/blank, got {value!r}")
    return BOOL_VALUES[value]


# ------------------------------------------------------------------ hard checks


def hard_violations(row: Mapping[str, str]) -> list[Finding]:
    """Exact structural rules. Works on v2 rows (new columns absent) and v3 rows."""
    cid = row["case_id"]
    issue, dept = row["expected_issue_type"], row["expected_department"]
    prio, action = row["expected_priority"], row["expected_action"]
    secondary = (row.get("secondary_issue_type") or "").strip()
    reason = (row.get("review_reason") or "").strip()
    out: list[Finding] = []

    def bad(rule: str, message: str) -> None:
        out.append(Finding(rule, cid, message))

    # R-SCH-1 taxonomy
    if issue not in ISSUE_TYPES:
        bad("R-SCH-1", f"unknown issue type {issue!r}")
    if dept not in DEPARTMENTS:
        bad("R-SCH-1", f"unknown department {dept!r}")
    if prio not in PRIORITIES_5:
        bad("R-SCH-1", f"unknown priority {prio!r}")
    if action not in (AUTO, REVIEW):
        bad("R-SCH-1", f"unknown action {action!r}")
    try:
        safety = _flag(row, "safety_escalation")
        access = _flag(row, "accessibility_escalation")
    except ValueError as error:
        bad("R-SCH-1", str(error))
        safety = access = None
    if out:
        return out

    # R-REV-1 priority NEEDS_REVIEW <=> action NEEDS_REVIEW
    if (prio == REVIEW) != (action == REVIEW):
        bad("R-REV-1", "priority NEEDS_REVIEW and action NEEDS_REVIEW must coincide")

    # R-DEP-1 department = map(issue) unless a declared exception applies
    mapped = ISSUE_TO_DEPT[issue]
    if dept != mapped:
        lockout = issue == "Door / Lock" and dept == "Residence Life"          # R-DEP-2
        residence = issue == "Residence Maintenance" and dept == "Residence Life"  # mapped anyway
        hazard = issue in EQUIPMENT_ISSUES and dept in SAFETY_DEPARTMENTS and safety is True  # R-DEP-5 (D6)
        if not (lockout or residence or hazard):
            bad("R-DEP-1", f"department {dept!r} differs from {mapped!r} with no declared exception")
    if issue in EQUIPMENT_ISSUES and dept in SAFETY_DEPARTMENTS and dept != mapped and safety is not True:
        bad("R-DEP-5", "hazard routing override requires safety_escalation=true")

    # R-SAF-1 safety escalation implies URGENT
    if safety is True and prio != "URGENT":
        bad("R-SAF-1", "safety_escalation=true requires URGENT priority")

    # R-SAF-2 (D2) secondary issue only for safety-dominant multi-issue tickets
    if secondary:
        if secondary not in ISSUE_TYPES or secondary == "Other":
            bad("R-SAF-2", f"invalid secondary issue {secondary!r}")
        if secondary == issue:
            bad("R-SAF-2", "secondary issue must differ from primary")
        if safety is not True or prio != "URGENT" or action != AUTO:
            bad("R-SAF-2", "secondary issue requires safety_escalation=true, URGENT, AUTO_ROUTE")

    # R-REV-2 (D7) review reason present <=> NEEDS_REVIEW (enforced when the column exists)
    if "review_reason" in row:
        if action == REVIEW and reason not in REVIEW_REASONS:
            bad("R-REV-2", f"NEEDS_REVIEW requires a review_reason in {sorted(REVIEW_REASONS)}")
        if action != REVIEW and reason:
            bad("R-REV-2", "review_reason only allowed on NEEDS_REVIEW rows")
        if reason in ("multiple_issues", "no_matching_category") and (issue, dept) != ("Other", "Other / Review"):
            bad("R-REV-3", f"{reason} rows use Other / Other / Review")
        if reason == "insufficient_detail" and issue == "Other":
            bad("R-REV-4", "insufficient_detail must preserve an identifiable category (D7); use "
                            "no_matching_category if none")

    # R-ACC-1 (D9) accessibility escalation carries an ordinal priority
    if access is True and prio not in RANK:
        bad("R-ACC-1", "accessibility_escalation=true requires an ordinal priority")
    return out


# ----------------------------------------------------------------------- lint

_W = lambda *words: re.compile("|".join(words), re.IGNORECASE)  # noqa: E731

SCOPE = _W(r"campus-wide", r"institution-wide", r"\bevery (resident|residence)", r"\bentire\b", r"widespread",
           r"many (students|resident|registered)", r"large group", r"multiple buildings", r"across multiple")
IMMINENT = _W(r"\bminutes?\b", r"right now", r"\bnow\b", r"\brn\b", r"shortly before")
ESSENTIAL = _W(r"\bmeals?\b", r"meal-plan", r"\bheat\b", r"wi-?fi", r"network", r"authentication",
               r"emergency communication", r"critical campus application")
SAFETY_TERMS = _W(r"threat", r"weapon", r"gas smell", r"\bsmoke\b", r"smoking", r"\bfire\b", r"spark",
                  r"flooding", r"water everywhere", r"trapped", r"exposed electrical", r"burning")
THREAT = _W(r"threat", r"weapon", r"dangerous or threatening", r"unauthorized access")
HAZARD = _W(r"spark", r"smoking", r"burning", r"exposed electrical")
RESIDENCE_ROOM = _W(r"\bdorm\b", r"residence room", r"my residence")
EGRESS = _W(r"stair", r"\bexit\b", r"egress")
ACCESSIBILITY = _W(r"wheelchair", r"relies on it", r"accessible route", r"mobility")
RECURRENCE = _W(r"\bagain\b", r"repeatedly", r"\bkeeps?\b", r"every day", r"times this week", r"third time",
                r"recurr")
MATERIAL = _W(r"unusable", r"damag", r"worse", r"cannot", r"can'?t", r"interrupt", r"near-fall", r"dietary",
              r"cold weather", r"affecting", r"deadline", r"multiple", r"several", r"\ball\b", r"whole", r"entire")
MINOR = _W(r"\btiny\b", r"\bminor\b", r"\bsmall\b", r"slight")


def lint_warnings(row: Mapping[str, str]) -> list[Finding]:
    cid, text = row["case_id"], row["text"]
    issue, dept, prio = row["expected_issue_type"], row["expected_department"], row["expected_priority"]
    action = row["expected_action"]
    reason = (row.get("review_reason") or "").strip()
    out: list[Finding] = []

    def warn(rule: str, message: str) -> None:
        out.append(Finding(rule, cid, message))

    scoped, imminent, essential = bool(SCOPE.search(text)), bool(IMMINENT.search(text)), bool(ESSENTIAL.search(text))
    if scoped and (imminent or essential) and prio in RANK and prio != "URGENT":
        warn("D1", "widespread outage with imminent deadline or essential service is not URGENT")
    if scoped and prio == "URGENT" and not (imminent or essential) and not SAFETY_TERMS.search(text):
        warn("D1", "URGENT outage without an imminent deadline or essential-service disruption")

    if action == REVIEW and SAFETY_TERMS.search(text) and (reason in ("", "multiple_issues")):
        warn("D2", "safety term in a review ticket; safety emergencies override multi-issue review")

    if RESIDENCE_ROOM.search(text) and issue in RESIDENCE_ROOM_ISSUES and prio != "URGENT":
        warn("D3", "residence-room maintenance should route to Residence Maintenance / Residence Life")

    if THREAT.search(text) and (dept != "Campus Safety" or issue not in ("Security Concern", "Safety Hazard")):
        warn("D4", "active threat or dangerous item should be Security Concern/Safety Hazard at Campus Safety")

    if HAZARD.search(text) and dept not in SAFETY_DEPARTMENTS:
        warn("D6", "electrical/fire hazard routed to a non-safety department")

    # D5 scope: security incidents are rated by R-PRI-SEC, where repetition is itself the risk.
    if RECURRENCE.search(text) and issue != "Security Concern":
        if prio == "HIGH" and not MATERIAL.search(text):
            warn("D5", "recurrence-raised HIGH without stated material impact")
        if prio == "MEDIUM" and MINOR.search(text):
            warn("D5", "recurrence raised a minor issue without stated material impact")

    if issue == "Lighting" and EGRESS.search(text) and prio != "URGENT":
        warn("D8", "egress lighting failure below URGENT; confirm it is not materially unsafe")

    if ACCESSIBILITY.search(text):
        if "accessibility_escalation" not in row:
            warn("D9", "accessibility-route loss; accessibility_escalation field absent")
        elif _flag(row, "accessibility_escalation") is not True:
            warn("D9", "accessibility-route loss without accessibility_escalation=true")
    return out


def check_rows(rows: Iterable[Mapping[str, str]]) -> tuple[list[Finding], list[Finding]]:
    hard, lint = [], []
    for row in rows:
        hard += hard_violations(row)
        lint += lint_warnings(row)
    return hard, lint
