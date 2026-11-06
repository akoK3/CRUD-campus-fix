# Development label quality review (proposal, not applied)

**Date:** 2026-11-05
**Scope:** The 187 development cases in `evaluation/campusfix_benchmark_v2.csv`. No holdout case was opened.
**Status:** **Nothing has been changed.** The benchmark file and its hash are untouched. Every item below is a proposal for joint review. Approved corrections should go into a new versioned file (for example `campusfix_benchmark_v3.csv`), never an edit of v2, because v2 is hash-pinned by the frozen archive.

## How this review was done

1. **Reference policy.** No standalone policy document exists in the repository. The labels cite "Finalized Campus Fix policy", and the operative rubric is the classification prompt (`backend/app/classify.py`, identical in `evaluation/evaluate_holdout_models.py`):
   - **LOW** = minor or cosmetic
   - **MEDIUM** = functional issue that can wait
   - **HIGH** = major disruption or serious near-term consequence
   - **URGENT** = immediate credible safety or security risk, active serious damage, active dorm lockout/access emergency, or critical large-scope outage
   - **NEEDS_REVIEW** = too vague, multiple distinct problems, or no matching category
   - Real deadlines, recurrence, accessibility, and scope may raise priority. Planned outages lower it.

   The benchmark's own `label_reason` column adds edge-case rules:
   - recurrence raises a minor issue one level
   - a known issue already under repair gets no recurrence increase
   - a workaround limits impact despite a deadline
   - an unrelated deadline does not raise priority
   - deadline plus large scope stack to URGENT
2. **Candidate selection.** I read all 187 cases. Candidates were prioritized where **two or three of the three models disagreed with the label** (development predictions from `results/dev_metrics.json`). I also included every HIGH/URGENT boundary case, every multi-issue case, and every NEEDS_REVIEW case, and compared near-duplicate texts against each other for consistency.
3. **Model agreement is evidence, not proof.** Models share biases; for example, all of them over-react to "URGENT!!!". A label is proposed for change only when the policy text or an internal inconsistency supports it.

### Legend

- **Existing**: `issue type / department / priority`, plus the expected action when it is NEEDS_REVIEW.
- **Models**: DS = DeepSeek-R1 8B, QW = Qwen3 14B, LA = Laya. Values are issue / priority.
- **Proposal**:
  - **KEEP**: no change recommended.
  - **CHANGE →**: a specific correction is recommended.
  - **ADJUDICATE**: a human decision is needed, usually because the answer depends on a policy choice (§1).
- **Confidence** that the proposal is right: **High** (policy text or a direct contradiction supports it), **Medium** (defensible either way, but one reading is clearly better), **Low** (genuinely ambiguous).

### Summary

| Outcome | Cases |
|---|---|
| CHANGE proposed, High confidence | 2 (CFB-0112, CFB-0158) |
| CHANGE proposed, Medium confidence | 6 (CFB-0154, CFB-0017, CFB-0029, CFB-0236, CFB-0205, CFB-0207) |
| ADJUDICATE (policy decision or genuine ambiguity) | 22 |
| **Flagged total (CHANGE + ADJUDICATE)** | **30** |
| KEEP after review (examined because models disagreed or the case is on a boundary; label is policy-consistent) | 17 |
| **Total reviewed in detail** | **47** |

A further 6 cases were spot-checked and found consistent, and 3 have noise-type metadata problems (Appendix A).

---

## 1. Policy decisions needed first

Many items cannot be settled case by case; they need one rule. Deciding these first settles 24 of the reviewed cases consistently.

| # | Question | Cases affected | Reviewer's recommendation |
|---|---|---|---|
| **D1** | Can a **non-safety administrative or service outage** (printing, club filing, billing, aid, meal plan) be URGENT through scope plus an imminent deadline? Or is URGENT reserved for safety, security, active damage, dorm lockout, and critical *infrastructure* (network, authentication, institution-wide systems)? | 0100, 0130, 0172, 0184, 0190, 0235, 0246 | Keep scope + deadline → URGENT, but **write the rule down** with thresholds: (a) affects many users or campus-wide, **and** (b) consequence within about 1 hour, **or** (c) loss of a basic need (meals). The current labels follow this pattern consistently; the prompt just doesn't say it. |
| **D2** | When a multi-issue ticket contains an **URGENT safety issue**, is the label NEEDS_REVIEW (multi-issue rule) or the safety issue at URGENT (safety rule)? | 0205, 0207 | **Safety dominates.** Label the safety issue as URGENT, and record the secondary issue in a new `secondary_issue` column. Routing a flood or a threat to a review queue contradicts the CLAUDE.md emergency rule. |
| **D3** | Does an **in-residence fixture** belong to `Residence Maintenance / Residence Life` or to its generic category (`Furniture`, `Structural`) under Facilities? | 0103, 0108, 0236 | In-residence fixture → **Residence Maintenance**. Two of the three existing labels already do this; 0236 is the outlier. |
| **D4** | Do **threats and dangerous items** go to `Security Concern / Campus Safety` regardless of where they occur or what object is involved? | 0112, 0154, 0142 | **Yes** for threats and dangerous items. For a stolen ID being misused, either label is defensible because the department is the same. |
| **D5** | Does the **recurrence +1 rule** apply to every category whenever the text gives an explicit repeat count? | 0119, 0167 | Yes, but cap it: recurrence raises at most one level and never to URGENT. |
| **D6** | Does the **"Vending → IT" policy** still apply when the vending or laundry machine is an **electrical or fire hazard**? | 0178, 0118 | Keep the issue type, but add a hazard exception that routes physical hazards to Facilities or Campus Safety. This is a routing policy change, not a label typo. |
| **D7** | If the **category is clear but the details are missing**, is the label NEEDS_REVIEW, or the known category at a default priority? | 0203 | Keep NEEDS_REVIEW for ambiguity, but make the representation consistent. Today 0203 is `Elevator / Facilities / NEEDS_REVIEW`, while production converts every NEEDS_REVIEW to `Other / Other / Review`. |
| **D8** | Is **partial** loss of stairwell lighting HIGH and **total** loss of emergency-egress lighting URGENT? | 0021, 0233 | Adopt this rule. Under it, both existing labels are correct. |
| **D9** | Is an outage of the **only accessible route** (an elevator for a wheelchair user) HIGH or URGENT? | 0063, 0065 | HIGH, which matches the existing labels, unless campus ADA policy says otherwise. Ask the Facilities stakeholder. |

---

## 2. HIGH vs URGENT boundary

### 2.1 Egress lighting (D8)

| Case | Text | Existing | Models | Proposal | Confidence |
|---|---|---|---|---|---|
| CFB-0021 | Most lights on a stairwell have failed, creating poor visibility. | Lighting / Facilities / HIGH | DS URGENT, QW URGENT, LA MEDIUM | **ADJUDICATE (D8).** Keep HIGH if the rule is "partial = HIGH, total egress loss = URGENT". | Low |
| CFB-0233 | There are no lights on the emergency stairs rn 😬 | Lighting / Facilities / URGENT | DS HIGH, QW URGENT, LA MEDIUM | **ADJUDICATE (D8).** Keep URGENT under the same rule. | Medium |

**Justification.** These are near-duplicates with opposite labels, and two models put 0021 at URGENT. The difference ("most" vs "no" lights; "a stairwell" vs "emergency stairs") is real but subtle. Without an explicit rule, annotators and models will split, so the pair should be decided together.

### 2.2 Access and security

| Case | Text | Existing | Models | Proposal | Confidence |
|---|---|---|---|---|---|
| CFB-0141 | My ID access failed and I cannot enter the building for a final in 30 minutes. | ID Card / Campus Safety / HIGH | DS ID Card URGENT, QW ID Card URGENT, LA Door / Lock URGENT | **KEEP HIGH**, and clarify that "access emergency" in the URGENT definition means *residential* lockout only. | Medium |
| CFB-0033 | The main academic building entrance lock has failed and cannot secure after closing. | Door / Lock / Facilities / HIGH | DS URGENT, QW URGENT, LA HIGH | **ADJUDICATE.** HIGH if "cannot secure after closing" is a near-term risk; URGENT if the building is unsecured *tonight* with no guard ("immediate credible security risk"). The text doesn't say when closing is. | Low |
| CFB-0111 | My roommate conflict is seriously preventing me from safely using my assigned room tonight. | Housing / Roommate / Residence Life / HIGH | DS URGENT, QW URGENT, LA MEDIUM | **ADJUDICATE.** "Safely" signals a possible safety risk, but there is no explicit threat (contrast CFB-0112). Recommend keeping HIGH and adding guidance: "unsafe" without a described threat is HIGH plus a welfare check, not URGENT. | Low |
| CFB-0158 | Someone has been repeatedly entering a restricted area without authorization. | Security Concern / Campus Safety / **MEDIUM** | DS HIGH, QW URGENT, LA HIGH | **CHANGE → HIGH.** | **High** |

**Justification for 0141.** All three models chose URGENT, but the label is consistent with the deadline rule elsewhere in the set. CFB-0087 (login blocked, exam in 1 h) is HIGH, CFB-0231 (can't log in, final in 30 min) is HIGH, and CFB-0245 (aid blocked, 20 min) is HIGH. The models are likely generalizing "access emergency" from the dorm-lockout rule.

**Justification for 0158.** The label is internally inconsistent:
- CFB-0161 ("someone keeps trying locked office doors": attempts only) is **HIGH**.
- CFB-0159 ("a security issue has recurred several times") is **HIGH**.
- Repeated *successful* unauthorized entry is at least as serious as either.

MEDIUM ("functional issue that can wait") does not describe an ongoing security breach.

### 2.3 Deadline and scope stacking

| Case | Text | Existing | Models | Proposal | Confidence |
|---|---|---|---|---|---|
| CFB-0248 | All printers are down, deadline in 20 minutes, and no alternative submission is allowed. | Printing / IT / HIGH | DS URGENT, QW URGENT, LA HIGH | **ADJUDICATE.** The text is ambiguous about scope: "All printers" in one room, building, or campus? Under the set's own stacking rule (0246: "campus-wide + 20 min → URGENT"), a campus-wide reading makes this URGENT. Recommend **rewriting the text in v3** to state scope, rather than relabeling. | Medium |
| CFB-0063 | The only elevator serving an upper floor is down and a student who relies on it cannot reach class. | Elevator / Facilities / HIGH | DS HIGH, QW URGENT, LA HIGH | **ADJUDICATE (D9).** Recommend keeping HIGH. | Medium |
| CFB-0065 | elevator dead again, wheelchair user cant get upstairs for class | Elevator / Facilities / HIGH | DS HIGH, QW URGENT, LA HIGH | **ADJUDICATE (D9).** Recommend keeping HIGH. | Medium |

### 2.4 Non-safety large-scope outages (D1)

Every case below is internally consistent (scope + imminent consequence → URGENT). The question is whether that is the intended policy. DeepSeek labels 6 of these 7 as HIGH, which is most of its URGENT under-triage. **One decision resolves all seven.**

| Case | Text | Existing | Models | Proposal | Confidence |
|---|---|---|---|---|---|
| CFB-0100 | Printing is unavailable campus-wide for an essential time-sensitive administrative process. | Printing / IT / URGENT | DS HIGH, QW URGENT, LA URGENT | **ADJUDICATE (D1).** This is the weakest URGENT of the group: printing is rarely "critical", and "time-sensitive" gives no time window. If D1 adds a time threshold, this becomes HIGH. | Low |
| CFB-0130 | A critical organization filing will close in minutes and a system/process failure affects many registered clubs. | Club / Organization / Student Engagement / URGENT | DS Software HIGH, QW Club / Organization URGENT, LA Other HIGH | **ADJUDICATE (D1).** Priority is consistent with D1. The issue label is also debatable ("system/process failure" → Software?), but the club filing is the user-facing problem, so keep Club / Organization. | Low |
| CFB-0172 | A widespread meal-plan system outage is preventing many resident students from accessing meals. | Meal Plan / Dining / URGENT | DS HIGH, QW URGENT, LA Dining Service HIGH | **ADJUDICATE (D1).** Recommend keeping URGENT (basic-need access at scale). | Medium |
| CFB-0235 | Meal-plan system is down for every resident and dinner service is happening now. | Meal Plan / Dining / URGENT | DS Dining Service HIGH, QW Meal Plan URGENT, LA Dining Service MEDIUM | **ADJUDICATE (D1).** Recommend keeping URGENT. Issue label `Meal Plan` is correct: the swipe system is down, not the food service. | Medium |
| CFB-0184 | A billing-system failure is incorrectly blocking a large group of students minutes before a hard payment/registration cutoff. | Billing / Finance / URGENT | DS HIGH, QW URGENT, LA HIGH | **ADJUDICATE (D1).** Consistent with D1 if adopted. | Medium |
| CFB-0190 | A system error affecting many students is preventing aid submission shortly before a hard deadline closes. | Financial Aid / Finance / URGENT | DS HIGH, QW URGENT, LA HIGH | **ADJUDICATE (D1).** "Shortly before" has no time window; tighten the text in v3. | Low |
| CFB-0246 | A campus-wide aid submission outage hits 20 minutes before the hard deadline. | Financial Aid / Finance / URGENT | DS HIGH, QW URGENT, LA Other URGENT | **ADJUDICATE (D1).** This is the clearest member: explicit scope, explicit 20-minute window, and `label_reason` "deadline + large scope stack". It is the anchor case for the rule. | Medium |

---

## 3. Other priority inconsistencies

| Case | Text | Existing | Models | Proposal | Confidence |
|---|---|---|---|---|---|
| CFB-0017 | hvac keeps turning off n on all day, room getting v uncomfortable | Heating / Cooling / Facilities / **HIGH** | DS MEDIUM, QW HIGH, LA MEDIUM | **CHANGE → MEDIUM.** | Medium |
| CFB-0026 | A classroom desk leg is loose and the desk cannot be used safely. | Furniture / Facilities / MEDIUM | DS URGENT, QW HIGH, LA HIGH | **KEEP MEDIUM.** A single desk can be taken out of service, so the issue "can wait". See the pair note below. | Medium |
| CFB-0029 | desk wobbly af but still standing | Furniture / Facilities / **MEDIUM** | DS LOW, QW LOW, LA MEDIUM | **CHANGE → LOW.** | Medium |
| CFB-0119 | washer keeps eating cycles 3 times this week | Laundry / Residence Life / HIGH | DS MEDIUM, QW HIGH, LA MEDIUM | **ADJUDICATE (D5).** Recommend keeping HIGH under the recurrence rule. | Medium |
| CFB-0167 | food line messed up my order again third time | Dining Service / Dining / MEDIUM | DS MEDIUM, QW HIGH, LA HIGH | **ADJUDICATE (D5).** It has an explicit count ("third time") but recurrence was *not* applied, unlike 0119. Under D5 as worded it becomes HIGH. Alternatively, exempt service-quality complaints from recurrence. | Low |

**0017.** The persistence ("all day") is within a single episode, not recurrence across episodes, and the consequence is discomfort. Comparable cases:
- CFB-0014 ("AC in our classroom is not cooling") is MEDIUM.
- HIGH examples elsewhere involve unusable spaces, damage, or vulnerable occupants, such as CFB-0015's repeated heating failure *in cold weather*.

**0026 and 0029 together.** As labeled, a desk that *cannot be used safely* (0026) and a desk that is merely *wobbly but standing* (0029) share the same priority. One of them is wrong. Lowering 0029 is better supported than raising 0026:
- Two models put 0029 at LOW.
- The LOW definition ("minor") fits "still standing".

---

## 4. Multi-issue and NEEDS_REVIEW cases

All 11 expected-review cases were examined.

| Case | Text | Existing | Models | Proposal | Confidence |
|---|---|---|---|---|---|
| CFB-0205 | My roommate is threatening me and the laundry machine ate my card. | Other / Other / Review / NEEDS_REVIEW | DS Security Concern URGENT, QW Door / Lock URGENT, LA Other URGENT | **CHANGE → Security Concern / Campus Safety / URGENT** (secondary issue: Laundry), **conditional on D2**. | Medium |
| CFB-0207 | The Wi-Fi is down in my room. Also the sink is flooding. | Other / Other / Review / NEEDS_REVIEW | DS Plumbing URGENT, QW Wi-Fi / Network URGENT, LA Other MEDIUM | **CHANGE → Plumbing / Facilities / URGENT** (secondary: Wi-Fi / Network), **conditional on D2**. | Medium |
| CFB-0203 | Something is wrong with the elevator. | Elevator / Facilities / NEEDS_REVIEW (`label_reason`: insufficient detail) | DS HIGH, QW URGENT, LA MEDIUM | **ADJUDICATE (D7).** | Low |
| CFB-0208 | The vending machine took my money; also I lost my ID. | Other / Other / Review / NEEDS_REVIEW | DS Vending NEEDS_REVIEW, QW Vending MEDIUM, LA Vending MEDIUM | **KEEP.** Two distinct issues with two different departments (IT and Campus Safety), neither urgent. This is the canonical multi-issue case. | High |

**0205.** A personal threat is an "immediate credible safety/security risk". Every model escalated it, and every model ignored the laundry complaint. Labeling the whole ticket NEEDS_REVIEW (normal review queue) would penalize the safest behavior, and it conflicts with the CLAUDE.md rule that emergency language must not rely on the normal queue. QW's `Door / Lock` is wrong under any policy.

**0207.** Active flooding is URGENT elsewhere in this set (CFB-0232 "water everywhere rn" → URGENT; CFB-0212 flooding → URGENT). The Wi-Fi complaint should not downgrade a flood to the review queue.

**0203.** Two readings:
- (a) Keep as review, but represent it as `Other / Other / Review / NEEDS_REVIEW`, the way production stores review decisions. Note that this loses the correct "Elevator" signal.
- (b) Treat it as `Elevator / Facilities / MEDIUM` with AUTO_ROUTE, since the department is unambiguous and only severity is unknown.

Elevator problems can include entrapment, which argues for (b) plus a prompt for detail. The current hybrid label is valid under neither convention, and it makes every model look wrong even though all three identified the elevator.

Also in this group:

| Case | Text | Existing | Proposal | Confidence |
|---|---|---|---|---|
| CFB-0204 | Sink leaking AND I also cannot log into Canvas. | Other / Other / Review / NEEDS_REVIEW | **KEEP.** "Leaking", not flooding: no safety override under D2. | High |
| CFB-0206 | Projector broken, also my tuition bill is wrong. | Other / Other / Review / NEEDS_REVIEW | **KEEP.** | High |

The out-of-scope cases (CFB-0193, -0194, -0195, -0196, -0198) are correctly labeled NEEDS_REVIEW. They have metadata problems, listed in Appendix A.

---

## 5. Issue type and department boundaries

| Case | Text | Existing | Models | Proposal | Confidence |
|---|---|---|---|---|---|
| CFB-0112 | There is an immediate credible threat from someone in my residence hall. | **Housing / Roommate / Residence Life** / URGENT | Security Concern URGENT (all 3) | **CHANGE → Security Concern / Campus Safety / URGENT.** | **High** |
| CFB-0154 | I found an item that appears immediately dangerous or threatening. | **Lost and Found** / Campus Safety / URGENT | DS Safety Hazard, QW Safety Hazard, LA Security Concern (all URGENT) | **CHANGE → Security Concern** (alternative: Safety Hazard; ADJUDICATE between the two). | Medium |
| CFB-0142 | My campus ID was stolen and is being actively used for unauthorized access. | ID Card / Campus Safety / URGENT | DS Security Concern URGENT, QW ID Card URGENT, LA Security Concern MEDIUM | **ADJUDICATE (D4).** Lean Security Concern, because active unauthorized access is the urgent part. The department is the same either way, so routing is unaffected. | Low |
| CFB-0136 | A vehicle is blocking an emergency access lane right now. | Parking / Campus Safety / URGENT | DS Safety Hazard URGENT, QW Parking URGENT, LA Safety Hazard MEDIUM | **ADJUDICATE.** Both labels are reasonable and route to the same department. Consider accepting either label in scoring (a multi-label key). | Low |
| CFB-0103 | A cabinet handle in my residence room is loose. | Residence Maintenance / Residence Life / LOW | Furniture (all 3) | **KEEP under D3.** All models disagree because the prompt never defines Residence Maintenance. Fix the prompt, not the label. | Medium |
| CFB-0108 | minor dent in dorm wall from move-in | Residence Maintenance / Residence Life / LOW | DS Other, QW Structural, LA Other | **KEEP under D3.** | Medium |
| CFB-0236 | I have a final tomorrow and my dorm dresser drawer is stuck. | **Furniture / Facilities** / MEDIUM | DS Furniture HIGH, QW Furniture HIGH, LA Door / Lock HIGH | **CHANGE → Residence Maintenance / Residence Life / MEDIUM**, conditional on D3. Priority MEDIUM is correct (`label_reason`: unrelated deadline must not raise it). | Medium |
| CFB-0157 | I noticed a door propped open earlier but it is secure now. | Security Concern / Campus Safety / LOW | DS Door / Lock MEDIUM, QW Door / Lock LOW, LA Door / Lock MEDIUM | **KEEP.** Nothing is broken; it is a past security observation. Add a prompt definition: Door / Lock = hardware fault; Security Concern = people or behavior. | Medium |
| CFB-0161 | someone keeps trying locked office doors | Security Concern / Campus Safety / HIGH | Door / Lock (all 3) | **KEEP.** Same rule as 0157: the subject is a person, and the locks work. | High |
| CFB-0192 | EMERGENCY!!! I just want to know when refunds usually arrive | Financial Aid / Finance / LOW | DS Billing LOW, QW Billing NEEDS_REVIEW, LA Other MEDIUM | **ADJUDICATE.** "Refunds" usually means student-account (Billing) refunds; aid refunds are a subset. Lean Billing, or accept both. Department is unaffected. Priority LOW is correct (shouting ignored). | Low |
| CFB-0178 | A vending machine has exposed electrical parts and is sparking. | Vending / **IT** / URGENT | DS Vending URGENT, QW Vending URGENT, LA Electrical MEDIUM | **ADJUDICATE (D6).** The label follows policy, but the policy sends a live electrical hazard to IT. Recommend a hazard routing exception rather than a label edit. | Medium |
| CFB-0118 | A laundry machine is smoking and smells like burning electrical insulation. | Laundry / **Residence Life** / URGENT | DS Laundry URGENT, QW Laundry URGENT, LA Electrical MEDIUM | **ADJUDICATE (D6).** Same issue as 0178: a fire risk routed to Residence Life. | Medium |

**0112.** The text describes a threat, not a housing or roommate matter; nothing mentions a roommate. All three models agree. The department matters: threats are Campus Safety's remit. This is also one of the 10 cases where all three models got the issue type "wrong".

**0154.** Lost and Found is a logistics queue for returning property. A dangerous or threatening item is a security response, and every model rejected Lost and Found. The department is unchanged (Campus Safety), so only the issue type moves.

---

## 6. KEEP after review: models disagree, label is correct

These were examined because two or more models disagreed. The labels follow the stated rules, and changing them would mean tuning labels toward model behavior.

| Case | Text | Existing | Rule supporting the label |
|---|---|---|---|
| CFB-0247 | One printer is down, deadline in 20 minutes, but three other printers next to it work. | Printing / IT / MEDIUM | Workaround limits impact despite the deadline. Consistent with CFB-0097 and -0102 (printer issue + working alternative = LOW base). |
| CFB-0215 | The same printer jam is already being repaired and happened again. | Printing / IT / MEDIUM | `label_reason`: no recurrence increase for a known issue already under repair. |
| CFB-0072 | URGENT!!! my wifi is slow while watching youtube | Wi-Fi / Network / IT / LOW | Emotional wording is ignored (prompt). All models over-triage this. |
| CFB-0230 | WiFi sux lol not rly broken tho just slow in corner | Wi-Fi / Network / IT / LOW | Minor local inconvenience. |
| CFB-0015 | The residence hall heating has failed repeatedly during cold weather. | Heating / Cooling / Facilities / HIGH | Recurring but not stated as currently out. Contrast CFB-0016 (no heat now, dangerous cold) = URGENT. |
| CFB-0144 | lost my ID yesterday but I still have access and no immediate deadline. | ID Card / Campus Safety / MEDIUM | A lost credential needs deactivation (a security consideration), so above LOW is defensible. DS and QW said LOW. Low-stakes; leave as is. |
| CFB-0137 | permit says invalid but i paid already | Parking / Campus Safety / MEDIUM | The user-facing problem is the permit. Billing is a plausible secondary label. Same handling as 0136 if a multi-label key is adopted. |
| CFB-0145 | A floor mat edge is slightly curled in an unused corner. | Safety Hazard / Campus Safety / LOW | A minor trip hazard. Models chose Other or Furniture, but the hazard framing is intended. |

---

## Appendix A: Metadata issues (not labels)

`noise_type` drives the slice analysis in `results/dev_tables.md`. These tags look mis-assigned:

| Case | Text | `noise_type` | Issue |
|---|---|---|---|
| CFB-0195 | Can someone help with my immigration paperwork? | recurrence/context | No recurrence or context modifier. Should be `none` or `out-of-scope`. |
| CFB-0196 | I need academic advising about which course to take. | safety/scope | No safety or scope element. Should be `none` or `out-of-scope`. |
| CFB-0198 | Please route this somewhere: question about counseling services. | sentiment/adversarial | Mild. "Please route this somewhere" is not adversarial. Consider `none`. |

Also spot-checked and consistent: CFB-0193, -0194 (out of scope → review); CFB-0212, -0213 (prompt injection ignored); CFB-0216, -0217 (planned outage lowered).

Also note: `human_review_status` is `candidate` for all 187 development rows. Whatever is approved here should set that field to a reviewed value in v3.

## Appendix B: Proposed process for approval

1. Decide D1–D9 together (about 30 minutes).
2. Walk through the CHANGE and ADJUDICATE rows. For each, record **approve / reject / modify** and a reviewer initial.
3. Ideally have a second reviewer label the 30 flagged cases blind (without seeing these proposals), and report agreement (Cohen's κ) per axis.
4. Write approved changes to `campusfix_benchmark_v3.csv`, with a changelog column (`v2_value`, `v3_value`, `decision_ref`). Leave v2 and every frozen hash unchanged.
5. Re-score the saved development predictions against v3 using `analyze_development.py` (parameterized by dataset path). Report v2 and v3 metrics side by side.
6. Apply the same policy decisions (D1–D9) when writing any **new** holdout set. Do not relabel the frozen 62-case holdout, which remains a historical artifact.

**Expected effect, if all High and Medium CHANGE proposals and the recommended D-decisions are accepted:** about 8 label changes across issue, department, and priority. No metrics were recomputed, so that the proposals are not influenced by how they would move model scores.
