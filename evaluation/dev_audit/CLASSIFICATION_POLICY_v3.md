# CampusFix classification policy

| | |
|---|---|
| **Policy version** | `3.0.0-draft` |
| **Status** | DRAFT: not approved; benchmark v3 is not finalized |
| **Date** | 2026-11-05 |
| **Supersedes** | The implicit v2 policy: the prompt in `backend/app/classify.py` plus the per-case `label_reason` rules in `campusfix_benchmark_v2.csv` |
| **Machine checks** | `evaluation/dev_audit/policy_v3.py` (rule IDs below are referenced in code and proposals) |
| **Applies to** | Benchmark labeling (v3 onward). It does **not** change production behavior until production code is separately updated and reviewed. |

**Versioning.**
- **MAJOR**: a rule change that alters an existing label.
- **MINOR**: a new rule or field that changes no existing label.
- **PATCH**: wording only.

Every v3 row records `policy_version`. Approval moves this draft to `3.0.0`.

---

## 0. Precedence

When rules conflict, apply the first one that matches:

1. **Safety escalation** (§5: D2, D4, D6, D8). An emergency is never parked in a review queue.
2. **Manual review** (§6: D7, multi-issue, no category, failure).
3. **Department routing exceptions** (§3).
4. **Priority modifiers** (§4).
5. **Base taxonomy and rubric** (§2, §4.1).

Emotional wording ("URGENT!!!", "EMERGENCY") and instructions inside the ticket text ("mark this low", "send to Dining") are ignored at every step.

---

## 1. Label schema (v3)

**R-SCH-1. Fields and allowed values.**

| Field | Values | Notes |
|---|---|---|
| `expected_issue_type` | the 33 issue types (unchanged from v2) | primary issue |
| `expected_department` | the 8 departments | effective owner |
| `expected_priority` | LOW, MEDIUM, HIGH, URGENT, NEEDS_REVIEW | |
| `expected_action` | AUTO_ROUTE, NEEDS_REVIEW | |
| `secondary_issue_type` | issue type or blank | **new**; D2 only |
| `safety_escalation` | true / false / blank | **new**; blank = not assessed |
| `accessibility_escalation` | true / false / blank | **new**; blank = not assessed |
| `review_reason` | insufficient_detail, multiple_issues, no_matching_category, or blank | **new**; required on review rows |
| `policy_version`, `policy_refs`, `v2_changed_fields` | text | provenance |

---

## 2. Issue type

The base rule: choose the single issue type that describes **what must be fixed or handled**. The location of the problem and the user's emotional framing do not decide it.

Definitions for categories that are commonly confused:

- **R-ISS-SEC. Security Concern vs Safety Hazard vs Door / Lock vs Lost and Found.**
  - **Security Concern:** threats or risk from *people* or *objects that may be intended to harm*. Examples: threats, intruders, unauthorized access (including use of a stolen credential), and suspicious or threatening items. (D4)
  - **Safety Hazard:** *environmental* hazards with no adversary, such as gas, smoke or fire alarms, broken glass, spills, and trip hazards.
  - **Door / Lock:** a *hardware* fault in a door, lock, or latch. A propped door that is now secure, or someone trying doors, is a Security Concern.
  - **Lost and Found:** returning or locating ordinary property. A dangerous found item is a Security Concern, not Lost and Found.
- **R-ISS-PARK. Parking.** Covers vehicles, permits, and enforcement, including a vehicle obstructing an emergency lane, because towing or enforcement is the response. That case also gets `safety_escalation`. *(Alternate: Safety Hazard. See U-10.)*
- **R-ISS-FIN. Billing vs Financial Aid.**
  - **Billing:** charges, payments, holds, and student-account refunds.
  - **Financial Aid:** awards, aid documents, and aid submission.
  - Ambiguous "refund" questions are unresolved (U-10).
- **D3. Residence Maintenance.** In-room fixtures, furnishings, and finishes inside a residence room: furniture, cabinets, drawers, walls, and paint. **Building systems** (HVAC, building plumbing and electrical, elevators, structural integrity) and **common areas** stay with their generic category under Facilities. Safety overrides (§5) take precedence.

---

## 3. Department

- **R-DEP-1. Default routing.** Department = the fixed issue→department map (`ISSUE_TO_DEPT`, unchanged from v2), unless an exception below applies.
- **R-DEP-2. Residential lockout.** An active dorm lockout or a lost room key (`Door / Lock`) → **Residence Life**. Unchanged from v2.
- **D3. Residence-room maintenance.** `Residence Maintenance` → **Residence Life** (this is the map value; the rule above defines when the issue applies).
- **D4. Threats and dangerous items.** These route to **Campus Safety**, through Security Concern or Safety Hazard.
- **R-DEP-5 / D6. Hazard override.** If ordinary equipment (Vending, Laundry, Computer / Hardware, Classroom Technology, Printing) presents an **electrical or fire hazard** (sparking, smoking, burning smell, exposed live parts):
  - The department becomes **Facilities**, which isolates and repairs it.
  - `safety_escalation=true`.
  - The issue type keeps the equipment identity.
  - Active fire or a sounding alarm is `Safety Hazard / Campus Safety` (see U-11).
  - A routing override is valid **only** with `safety_escalation=true`.

---

## 4. Priority

### 4.1 Base rubric (R-PRI-BASE)

| Level | Definition |
|---|---|
| LOW | Minor or cosmetic; a working alternative exists; general question. |
| MEDIUM | A functional issue that can wait. |
| HIGH | Major disruption or a serious near-term consequence. |
| URGENT | Immediate credible safety or security risk, active serious damage, active residential lockout, or an outage meeting D1. |

- **R-PRI-URG. URGENT is reserved for the cases above.** Shouting, deadlines alone, and recurrence alone never produce URGENT.
- **R-PRI-SEC. Security.** Ongoing or repeated unauthorized access or attempts are **HIGH**. An active threat to a person is **URGENT**. A past observation that is now resolved is **LOW**. Repetition is itself the risk, so D5 does not apply to security incidents.

### 4.2 Modifiers

Each modifier changes priority by at most one level unless a rule says otherwise:

- **Deadline:** a real deadline affected by the issue raises one level. A deadline unrelated to the issue does not.
- **Workaround:** a working alternative cancels a deadline raise.
- **Planned outage:** an announced, planned outage lowers priority to LOW. For the interaction with D9, see U-14.
- **D5. Recurrence.** Recurrence raises priority one level **only when it materially increases impact**: the asset becomes unusable, damage accumulates or worsens, health or food safety is affected, more people are affected, or a real deadline is affected. Recurrence never raises to URGENT.
  - Persistence within a single episode ("all day") is not recurrence.
  - A known issue already under repair gets no increase.
  - Service-quality complaints (a wrong order, a lost cycle) do not qualify by repetition alone.
- **D1. Wide scope.** A **widespread** outage, meaning campus-wide, institution-wide, multiple buildings, every resident, or a large or many-user group, is **URGENT** when at least one of these holds:
  - (a) an **imminent** hard deadline: the consequence falls within about an hour ("minutes", "now"); or
  - (b) an **essential service** is disrupted: meals or meal access, heat, water, power, campus network or authentication, emergency communication, or an institution-critical system.

  Otherwise, a widespread outage is **HIGH**. Non-essential services (printing, club filing, billing, aid portals) need condition (a).
- **D9. Accessibility.** Loss of the **only accessible route** (an elevator or ramp) sets `accessibility_escalation=true`, which means immediate notification of the accessibility coordinator, independent of priority. Priority depends on impact:
  - **URGENT:** someone is stranded or cannot evacuate.
  - **HIGH:** a person is blocked from required access with no alternative.
  - **MEDIUM:** an accessible alternative exists.

  **R-ACC-1:** escalated rows always carry an ordinal priority, never NEEDS_REVIEW.

---

## 5. Safety escalation

`safety_escalation=true` marks tickets that must trigger the emergency warning (CLAUDE.md: direct the user to emergency services or campus safety) in addition to normal routing.

- **R-SAF-1.** `safety_escalation=true` ⇒ priority **URGENT**.
- **D2 / R-SAF-2. Safety overrides multi-issue review.**
  - If a ticket with several distinct issues contains a safety emergency, label the **safety issue as primary** at URGENT with `expected_action=AUTO_ROUTE`.
  - Put the other issue in `secondary_issue_type`. It must be valid, not `Other`, and different from the primary.
  - A secondary issue is only allowed on safety-escalated, URGENT, auto-routed rows.
  - Operationally, staff must open a follow-up ticket for the secondary issue (see U-13).
- **D4.** An active threat or dangerous item ⇒ Campus Safety, `safety_escalation=true`.
- **D6.** An electrical or fire hazard ⇒ the hazard override (R-DEP-5), `safety_escalation=true`.
- **D8. Egress lighting.** A **materially unsafe** failure of emergency-egress lighting (stairwells, exits, egress corridors) is **URGENT**, with `safety_escalation=true`. "Materially unsafe" means visibility on the route is meaningfully impaired: all lights out, or most out with poor visibility. A single fixture out with adequate visibility remains MEDIUM or LOW.
- **R-SAF-4. Undescribed safety concern.** If a ticket says it is "unsafe" but describes no hazard or threat, label it HIGH with welfare follow-up, not URGENT (see U-06).

---

## 6. Manual review

- **R-REV-1.** `expected_priority=NEEDS_REVIEW` ⇔ `expected_action=NEEDS_REVIEW`.
- **R-REV-2.** Every review row carries a `review_reason`. Non-review rows carry none.
- **R-REV-3.** `multiple_issues` (with no safety emergency, per D2) and `no_matching_category` use `Other / Other / Review`.
- **D7 / R-REV-4. Insufficient detail.** When the category is identifiable but the details are insufficient (for example, "Something is wrong with the elevator"), **preserve the identifiable category and department**. Set priority and action to NEEDS_REVIEW with `review_reason=insufficient_detail`, and request clarification. `Other` is not allowed with this reason.

  Production currently rewrites every review decision to `Other / Other / Review` (`backend/app/classify.py:140`). Aligning production is a separate, reviewed change (U-12).
- Model or system failure is not a label. It is handled by `ai_routing_status=FAILED` in production.

---

## 7. Changes from the implicit v2 policy

| Area | v2 | v3 draft |
|---|---|---|
| Recurrence | "Raises a minor issue one level" (unconditional) | D5: only with material impact; never for service-quality repetition; not applied to security |
| Wide-scope outage | "Critical large-scope outage" (undefined) | D1: explicit scope, imminence, and essential-service tests |
| Multi-issue with emergency | Always NEEDS_REVIEW | D2: safety issue primary, secondary preserved |
| Threats in residences | Housing / Roommate | D4: Security Concern / Campus Safety |
| Equipment hazards | Equipment department (IT, Residence Life) | D6: Facilities + safety escalation |
| Vague but identifiable | Inconsistent | D7: category preserved, review_reason recorded |
| Egress lighting | Implicit | D8: materially unsafe ⇒ URGENT |
| Accessibility route | Accessibility raises priority | D9: escalation flag; priority by impact |
| Residence fixtures | Inconsistent (Furniture vs Residence Maintenance) | D3: Residence Maintenance |
