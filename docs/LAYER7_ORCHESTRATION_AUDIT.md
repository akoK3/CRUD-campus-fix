# CampusFix Layer 7: AI orchestration audit

| | |
|---|---|
| **Date** | 2026-11-05 |
| **Type** | Audit only. No code, configuration, dependency, or database change was made. No paid API call was made. |
| **Scope** | `backend/app/*`, `backend/tests/*`, `supabase/migrations/*`, the frontend submission path, `evaluation/dev_audit/` (Layer 5), and `evaluation/vision/` (Layer 6) |
| **Branch** | `akaki` (uncommitted Layer 5/6 work present) |

**Evidence labels used throughout:**
- **[VERIFIED]**: read in the code or migration at the cited line.
- **[INFERRED]**: follows from verified facts but was not executed.
- **[PROPOSED]**: does not exist yet.

---

## 1. Executive summary

1. **What exists today [VERIFIED].**
   - One local FastAPI process polls Supabase every 7 s for `PENDING` tickets.
   - It classifies the ticket text with DeepSeek-R1 8B through Ollama, writes the suggested and effective routing, and optionally rewrites the title and description.
   - Failures land in `FAILED` / `NEEDS_REVIEW`, so a human can always act.
   - There is **no image analysis, no Claude integration, and no use of Layer 5 policy v3** in production.
2. **Layer 6 is evaluation-only [VERIFIED].**
   - `evaluation/vision/` contains a tested schema, preflight, prompt, response interpreter and safe-routing function.
   - It has never run live, so there is **no measured vision accuracy**. Integrating vision before a live evaluation would ship an unmeasured model into a safety-relevant path.
3. **Highest-severity orchestration defects already in production [VERIFIED].** These should be fixed before any new AI stage is added:
   - **F-1.** The AI result is written unconditionally after a model call that can take up to 180 s (360 s with enrichment). It can overwrite a staff transfer or assignment made in the meantime.
   - **F-2.** AI-detected `URGENT` never pages anyone. Emergency paging fires only from the browser regex at insert.
   - **F-3.** Duplicate-processing protection is an in-memory set, valid only while exactly one process runs.
   - **F-4.** A requester can re-trigger model calls without limit on their own `FAILED` ticket. This is harmless with local Ollama, but a cost-abuse vector once a paid API is involved.
4. **Photos arrive after the ticket [VERIFIED].** The ticket is committed first and the photo uploaded afterwards. The worker starts on tickets older than 5 s, and there is no "uploads complete" signal. Vision must therefore be a separate, attachment-triggered stage that can **escalate or flag**, never silently re-route.
5. **LangGraph is not justified now.** The pipeline is short, mostly linear, single-tenant, and its durable state belongs in Postgres, which already has the state column. A small explicit state machine in plain Python, with DB leases and idempotent stages, is safer, cheaper and fits the 156-hour budget. The conditions that would change this are listed in §7.
6. **`ANTHROPIC_API_KEY`:** keep it in the gitignored `backend/.env` and load it as a `SecretStr` setting.
   - `pydantic-settings` loads `.env` into the settings object, not into `os.environ`, so the key must be passed to the SDK explicitly (§9).
   - Sending student photos to a third-party API also needs a privacy decision (consent, face/plate handling, retention).

---

## 2. Current architecture [VERIFIED]

### 2.1 Components

| Component | File | What it does |
|---|---|---|
| Settings | `backend/app/config.py:7-57` | `pydantic-settings` reading `backend/.env`: Supabase URL and keys, Ollama URL/model/timeout (default 180 s), confidence threshold 0.75, SMTP. `ai_enabled` reports configuration, not model liveness (`:42-49`). |
| API and worker | `backend/app/main.py` | `/api/health`, `/api/tickets/{id}/classify` (`:254`), `/api/tickets/{id}/reclassify` (staff, `:278`), `/api/notifications/dispatch`. The lifespan starts `classification_loop` (`:223`) and the email `dispatch_loop`. |
| Text classifier | `backend/app/classify.py:102-190` | Builds text from title and description, calls Ollama `/api/chat` (`:51-100`), and extracts JSON by regex. Unknown labels become `Other` / `NEEDS_REVIEW` (`:134-152`). Department comes from `issue_types.default_department_id` plus the Vending→IT and dorm-lockout→Residence Life regex (`:159-169`). The confidence is a 1.0/0.0 flag, not a probability (`:174-182`). |
| Enrichment | `backend/app/enrich.py:95-167` | A second Ollama call rewrites `title` and `description` and writes `ai_explanation`. `original_text` is preserved, and `original_title` is protected by a trigger (`migrations/20261025203513_ticket_original_title.sql`). Runs only on confident routing (`main.py:167-178`). |
| Auth | `backend/app/auth.py` | Validates the JWT through Supabase Auth (`:46-95`). `assert_can_access_ticket` mirrors the RLS helper (`:98-121`). The service role bypasses RLS, so these checks are the only guard for backend endpoints. |
| Notifications | `backend/app/notify.py` | Emails PENDING/FAILED notification rows with `2^attempts`-minute backoff and `MAX_ATTEMPTS`. A good existing pattern for retries. |
| Worker launch | `scripts/start-ai-worker.ps1:96-97` | A single `uvicorn app.main:app --host 127.0.0.1` with no `--workers`. `docs/LOCAL_AI_WORKER.md` says "Run exactly one backend worker." |
| Frontend submission | `frontend/src/pages/SubmitTicketPage.tsx:121-148` | Inserts the ticket (the commit point), *then* uploads one photo (`lib/attachments.ts:19-41`). It does not call the backend. Routing is picked up by the poller. |
| Emergency detection | `frontend/src/lib/emergency.ts:8-31` | Client-side regex sets `is_emergency` at insert. |
| Schema | `migrations/20260928000000_baseline_schema.sql:120-185` | `ai_suggested_*`, `ai_confidence numeric(5,4)`, `ai_routing_status IN (PENDING, AUTO_ROUTED, NEEDS_REVIEW, ACCEPTED, OVERRIDDEN, FAILED)`; `attachments(file_url, file_type, file_size_bytes)` at `:210-222`. |
| Status transitions | `migrations/20260930211253_ticket_workflow.sql:32-48, 51-104` | Enforced for users. **Skipped for the service role** (`auth.uid() is null`, `:62-64`). An `AI_PROCESSING` status exists (`baseline:381`) but the backend never uses it. |
| Audit trail | `migrations/20260928211144_ticket_routing_and_audit.sql:69-118` | Trigger-written history for status, priority, department and `ai_routing_status`. **Not logged:** `issue_type_id`, `ai_suggested_*`, `ai_confidence`, AI text rewrites, model or prompt version. |
| Emergency paging | `ticket_workflow.sql:273-299` | `notify_ticket_created`: **after insert only**, only when `is_emergency`. |
| Storage | `migrations/20260928211102_attachments_storage_bucket.sql` | A private bucket with a 10 MB limit; MIME types include `image/heic`; policies keyed on the `ticket_id/` path prefix. |

### 2.2 Current flow [VERIFIED]

```
Browser ──insert ticket (NEW, ai_routing_status=PENDING, is_emergency from regex)──► Supabase
   │                         └─ trigger: emergency ⇒ page department staff (insert only)
   └─upload 1 photo (after insert, best effort) ──► Storage + attachments row

Local worker (every 7 s): PENDING ∧ not deleted ∧ created > 5 s ago, newest first, limit 3
   └─ classify(text) ─ Ollama DeepSeek (≤180 s)
        ├─ None (failure)   ⇒ FAILED (+ status NEEDS_REVIEW unless emergency)
        ├─ review / Other   ⇒ NEEDS_REVIEW
        └─ confident        ⇒ AUTO_ROUTED + effective dept/priority/issue + status ASSIGNED
                              (emergency: effective fields untouched)
                              └─ enrich_ticket ─ Ollama (≤180 s), failure ignored
```

---

## 3. Reusable components

| Component | Source | Reuse verdict |
|---|---|---|
| Fail-safe routing contract (every branch leaves a human-actionable state) | `main.py:116-187` | **Keep.** Extend it to the vision stage. |
| Suggested vs effective columns, `ai_routing_status` | Baseline schema | **Keep.** Vision suggestions need their own columns or table (§6.4). |
| Text classifier and department exceptions | `classify.py` | **Keep as the primary classifier** (frozen pre-RAG baseline, `docs/AI_BASELINE.md`). |
| Notification retry pattern | `notify.py:29-34` | **Reuse** the backoff idea for AI retries. |
| Vision output schema (structured-outputs-compatible) | `evaluation/vision/schema/vision_result.schema.json` | **Promote** a reviewed copy into the backend. Pin it with a hash-equality test against the frozen Layer 6 copy. |
| Preflight (magic-byte sniffing, base64 size, dimensions, docs resize/token reference) | `evaluation/vision/preflight.py` | **Promote** (stdlib only). |
| Response interpretation (refusal, truncation, model mismatch, invalid JSON, schema, semantic rules S1–S11) | `evaluation/vision/claude_client.py:interpret_response`, `vision_schema.py` | **Promote.** |
| Safe vision routing decision | `evaluation/vision/claude_client.py:routing_decision` | **Promote.** It already maps every failure to `MANUAL_REVIEW` and hazards to `safety_escalation`. |
| Frozen prompt `vision-1.0` | `evaluation/vision/prompts.py` | **Promote** only the version that passes the live evaluation. |
| Policy v3 rules (D1–D9) and the lint/hard checks | `evaluation/dev_audit/policy_v3.py`, `CLASSIFICATION_POLICY_v3.md` | **Do not adopt yet.** Still a draft pending approval (`v3_proposal/MIGRATION_PROPOSAL.md`). The production prompt stays v2. |

Layer 6 modules use `sys.path` insertion and flat imports (`from prompts import ...`). They should be **copied into a backend package with tests**, not imported from `evaluation/`. The evaluation tree stays frozen and is never a runtime dependency.

---

## 4. Verified findings and risks

Severity: **H** = can cause a wrong routing or safety outcome; **M** = reliability or cost; **L** = hygiene.

| # | Sev | Finding | Evidence |
|---|---|---|---|
| **F-1** | H | **Lost update against staff action.** The ticket is read, then the model runs (≤180 s, plus ≤180 s for enrichment), then `update(...).eq("ticket_id", id)` writes `department_id`, `priority_id` and `status_id` unconditionally. The service role skips transition checks. A staff transfer, assignment or override made in that window is silently overwritten. | `main.py:118,151,163`; `ticket_workflow.sql:62-64` |
| **F-2** | H | **AI-detected urgency is never escalated.** Paging fires only on insert with `is_emergency` (the browser regex). A ticket the classifier rates `URGENT` is routed like any other. Layer 5 measured DeepSeek under-triaging 8 of 36 URGENT development cases. | `ticket_workflow.sql:283-296`; `classify.py:154-182`; `evaluation/dev_audit/DEV_AUDIT_REPORT.md` §3.2 |
| **F-3** | M | **No database claim or lease.** Concurrency protection is a process-local `set` plus a read-then-write status check (a TOCTOU race). Safe only with exactly one process. A second worker, a manual `/classify`, and the loop can all process the same ticket. | `main.py:38-39,190-220,254-275` |
| **F-4** | M | **Unbounded requester-triggered retries.** `/classify` accepts `PENDING` **or** `FAILED` and permits the requester. There is no attempt counter or rate limit. With a paid vision API, each call costs money. | `main.py:254-275`; `auth.py:114-115` |
| **F-5** | M | **FAILED is never retried automatically.** The loop selects only `PENDING`. A transient Ollama outage leaves tickets in `FAILED` until a staff member reclassifies. | `main.py:229-237` |
| **F-6** | M | **Starvation under load.** Each tick takes the newest 3 pending tickets. During a backlog, older tickets wait indefinitely. | `main.py:236-237` |
| **F-7** | M | **Photo/classification race.** The photo uploads after the ticket insert. The worker starts on tickets older than 5 s. A 10 MB mobile upload can easily exceed 5 s, and later photos (ticket detail page) never trigger analysis. | `SubmitTicketPage.tsx:121-148`; `main.py:35,230` |
| **F-8** | M | **Format and size mismatch with Claude Vision.** The bucket accepts HEIC and 10 MB *raw*. Claude accepts JPEG/PNG/GIF/WebP and 10 MB *base64* (about 7.5 MB raw). The bucket MIME check trusts the browser's declared type. | `attachments_storage_bucket.sql`; `lib/attachments.ts:28`; `evaluation/vision/TEST_PLAN.md` §3 |
| **F-9** | M | **Reference-data cache never refreshes.** `_REF_CACHE` holds departments for the life of the process. A department deactivated at runtime stays routable until restart (documented in `AI_BASELINE.md`, but unsafe for CLAUDE.md's deactivation rule). | `classify.py:17,29-43` |
| **F-10** | M | **AI provenance is not recorded.** History logs `ai_routing_status` changes only. It does not log the suggested values, model, prompt version, latency or error. Overrides are auditable for department and priority, not for issue type. | `ticket_routing_and_audit.sql:69-118` |
| **F-11** | L | **Blocking I/O inside the async loop.** The Supabase query in `classification_loop` runs synchronously on the event loop. Only `_classify_pending_ticket` is offloaded. A hung query stalls the API. | `main.py:223-250` |
| **F-12** | L | **Withdrawn tickets can be classified through the endpoint.** The loop filters `deleted_at`; `/classify` and `_classify_pending_ticket` do not. | `main.py:200-216,254-275` |
| **F-13** | L | **The confidence threshold is decorative.** The classifier emits only 1.0 or 0.0, so `AI_CONFIDENCE_THRESHOLD=0.75` has no effect between those values. | `classify.py:174-182`; `.env.example` |
| **F-14** | M | **Vision would add a production dependency the tests currently forbid.** `test_production_imports_do_not_load_historical_model_packages` asserts `anthropic` is not imported. Integration must change this deliberately: import lazily, only when vision is enabled. | `backend/tests/test_routing.py:208-211` |
| **F-15** | H (policy) | **Privacy and data governance.** Report photos may contain faces, licence plates or documents. Sending them to a third-party API is new data processing that CLAUDE.md ("avoid collecting unnecessary sensitive information") does not yet cover. | Product decision; no code yet |

Already handled well [VERIFIED]:
- AI failure never blocks ticket creation (the browser commits first).
- An emergency ticket's effective routing is never changed by the model (`main.py:156-161`, tested at `test_routing.py:416`).
- An inactive department cannot be auto-routed when the cache is fresh (`test_routing.py:426`).
- Enrichment failure never undoes routing (`test_routing.py:398`).
- The original wording is preserved (`enrich.py:146-153`, plus the trigger).

---

## 5. Missing integrations

| Missing | Needed for |
|---|---|
| A server-side fetch of attachments from private Storage, using the service role and base64 (never a signed URL handed to a third party) | Vision input |
| Attachment-arrival trigger or polling (`attachments` rows newer than the last vision run) | F-7 |
| Server-side preflight: magic-byte type, size, dimensions; HEIC conversion or skip | F-8 |
| A Claude client in the backend with lazy import, timeouts, retries, refusal and fallback handling | Vision call |
| Persisted vision results and AI run provenance | F-10, staff UI |
| A safety-escalation path for AI- or vision-detected hazards that pages staff after insert | F-2 |
| A database claim/lease, attempt counter and backoff for AI stages | F-3, F-4, F-5 |
| Conditional writes that refuse to overwrite human decisions | F-1 |
| A staff UI for vision suggestions and disagreements | Human review |
| Live vision evaluation (Layer 6, steps L3–L4) | Any production enablement |

---

## 6. Proposed workflow [PROPOSED]

### 6.1 Principles

1. **Ticket creation never depends on AI.** This is unchanged.
2. **Text classification stays primary.** Vision is *evidence*: it can raise safety, add a suggestion, or flag disagreement. It never silently re-routes an auto-routed ticket.
3. **Every stage is idempotent and leased in the database.** Durable state lives in Postgres, not in process memory or a graph checkpointer.
4. **AI never overwrites a human decision.** Writes are conditional on the state the stage read.
5. **Safety errs toward escalation.** A false alarm pages a human; a miss can hurt someone.

### 6.2 Pipeline

```
 ┌───────────────┐   ┌─────────────┐   ┌───────────────────┐   ┌──────────────────┐   ┌────────────────┐
 │ 1 Intake      │──►│ 2 Validate  │──►│ 3 Text classify   │──►│ 5 Safety checks  │──►│ 6 Decide/write │
 │ (browser,     │   │ text non-   │   │ DeepSeek (exists) │   │ regex flag ∪     │   │ AUTO_ROUTED /  │
 │  existing)    │   │ empty, not  │   │                   │   │ URGENT ∪ vision  │   │ NEEDS_REVIEW / │
 └───────────────┘   │ deleted,    │   └───────────────────┘   │ hazards ⇒ page   │   │ FAILED         │
        │            │ lease taken │             ▲              └──────────────────┘   └────────────────┘
        │ photo      └─────────────┘             │ fused suggestion        ▲
        ▼                                        │                         │
 ┌───────────────┐   ┌─────────────┐   ┌───────────────────┐               │
 │ attachments   │──►│ 4a Preflight│──►│ 4b Claude Vision  │───────────────┘
 │ row inserted  │   │ (Layer 6)   │   │ (Layer 6 schema)  │  escalate / flag disagreement /
 └───────────────┘   └─────────────┘   └───────────────────┘  REQUEST_NEW_PHOTO
```

The stages:

1. **Intake (existing).** The browser inserts the ticket, then the attachment rows.
2. **Validate (worker).** Claim a lease (§6.3). Skip withdrawn tickets. Reject empty text by sending it to review.
3. **Text classification (existing classifier).** Unchanged prompt (policy v2) until policy v3 is approved.
4. **Vision (new, conditional).** Runs when the ticket has at least one attachment that passes preflight, *independently* of stage 3:
   - **(a) Preflight:** fetch with the service role, sniff the magic bytes, check base64 size and dimensions. HEIC is converted or skipped with a reason.
   - **(b) Claude:** structured output validated with the Layer 6 schema and semantic rules.
   - Runs for photos present at classification time **and** for photos added later (attachment trigger or poll).
5. **Safety checks (new, deterministic, no model).** `safety = is_emergency ∨ text priority URGENT ∨ vision.safety_escalation_suggested`. On a *new* escalation, call an escalation RPC (§6.4) that pages the owning department and Campus Safety.
   - Effective department and priority are not changed automatically.
   - Because the reporter has already left the page, the CLAUDE.md warning cannot be shown at that point. Escalating to humans is the substitute.
6. **Decide and write (conditional update).** The fusion rules:

| Text result | Vision result | Ticket action |
|---|---|---|
| confident | absent / failed / abstain / request-new-photo | Existing behavior (AUTO_ROUTED). Record the vision status. |
| confident | same issue type, or a compatible department | AUTO_ROUTED. Record vision as corroborating. |
| confident | **different issue type at medium or high confidence** | **NEEDS_REVIEW** if not yet auto-routed. If already routed: keep the route, add a staff-visible flag and an internal comment, and do not re-route. |
| review / Other / failed | issue identified at high confidence | NEEDS_REVIEW with the vision suggestion stored. No auto-route from vision alone in v1. |
| any | hazards present | Safety escalation (stage 5), whatever the routing |
| any | `REQUEST_NEW_PHOTO` (blurry, dark, obstructed) | Notify the requester to add a clearer photo. Routing is unchanged. |

### 6.3 State machine and leasing [PROPOSED]

Keep `ai_routing_status` as the routing outcome. Add an orchestration state so a crash never strands a ticket:

```
PENDING ──claim(lease)──► PROCESSING ──► AUTO_ROUTED | NEEDS_REVIEW
   ▲                          │
   └── lease expired ◄────────┤ (crash / timeout)
FAILED ◄── attempts exhausted ┘     FAILED ──backoff (attempts < N)──► PENDING
```

- **Claim:** `update tickets set ai_stage='PROCESSING', ai_lease_until=now()+interval '5 min', ai_attempts=ai_attempts+1 where ticket_id=$1 and ai_stage in ('PENDING','RETRY') and (ai_lease_until is null or ai_lease_until < now()) returning *`.
  - Implement it as a `security definer` RPC, with `EXECUTE` revoked from `public`/`anon`/`authenticated` (CLAUDE.md: every function is an RPC endpoint by default).
- **Write:** the final update includes `where ticket_id=$1 and ai_run_id=$run and status_id in (NEW, NEEDS_REVIEW) and department_id is not distinct from $read_department`. Zero rows updated means a human acted first: log it and discard the result. This fixes **F-1**.
- **Ordering:** oldest first, `limit N` (fixes **F-6**).
- **Retry:** transient failures move to `RETRY` with `next_attempt_at = now() + 2^attempts min` and a maximum of 3 attempts. After that, `FAILED` / `NEEDS_REVIEW` (fixes **F-5**).
- **The `/classify` endpoint:** staff only, or requester only while `PENDING` and `attempts = 0`. Rate-limited per ticket (fixes **F-4**).

### 6.4 Data model additions [PROPOSED, a migration with RLS in the same file]

| Object | Purpose |
|---|---|
| `tickets.ai_stage`, `ai_lease_until`, `ai_attempts`, `ai_next_attempt_at`, `ai_run_id` | Leasing and retry (or a separate `ticket_ai_jobs` table to keep `tickets` lean; recommended) |
| `ai_analysis_runs(run_id, ticket_id, stage{text,vision,enrich}, model, prompt_version, schema_version, status, error_kind, latency_ms, input_tokens, output_tokens, cost_usd, request_id, created_at)` | Provenance and observability (fixes **F-10**). RLS: staff of the ticket's department and system admins can read; no client writes. |
| `ticket_vision_results(run_id, ticket_id, attachment_ids[], outcome, issue_type_id, confidence, hazards[], abstain_reason, result jsonb)` | Stored vision suggestion for staff. Same RLS. |
| RPC `escalate_ticket_safety(ticket_id, source, reason)` | Sets an `ai_safety_flag` and pages department staff and Campus Safety. Service role only. Logs to `ticket_history`. |
| History: log `issue_type_id`, `ai_suggested_*` and safety-flag changes | Override auditability |

Grants: no `authenticated` write grants on these tables. Re-run `supabase/tests/workflow_and_security.sql` and extend it.

---

## 7. LangGraph versus plain Python orchestration

| Criterion | This pipeline | LangGraph value |
|---|---|---|
| Shape | Six mostly linear stages; two optional branches (vision, enrichment); fusion is a pure function | Low: a graph adds indirection without new capability |
| Durable state | Must be in Postgres anyway (RLS, staff UI, audit, crash recovery across a laptop worker) | A checkpointer duplicates DB state and creates two sources of truth |
| Human-in-the-loop | Staff act through the existing UI and RPCs, often hours later | LangGraph interrupts suit synchronous agent sessions, not ticket queues |
| Agentic tool use | None; fixed calls with structured outputs | None needed |
| Dependencies | The backend deliberately avoids extra AI packages (F-14) | Adds `langgraph` / `langchain-core` and their upgrade churn |
| Team budget | ~156 developer-hours for the whole project | A learning curve plus debugging an abstraction layer |
| Testability | Existing tests use simple fakes (`test_routing.py`) | Graph tests need more harness |

**Recommendation: plain Python.** Use a small `pipeline.py` with explicit stage functions, a `PipelineState` dataclass, and DB-leased jobs. Each stage is a pure-ish function `(state, deps) -> state`, which keeps it as testable as the current code.

**Revisit LangGraph only if** CampusFix adds multi-turn clarification with the reporter (the model asks questions and waits), dynamic multi-tool agents (for example RAG plus asset lookup plus duplicate detection with model-chosen order), or many parallel model branches with complex joins. None of these is in the MVP scope (CLAUDE.md, Scope Discipline).

---

## 8. Timeouts, retries, fallbacks, logging, and error handling [PROPOSED]

### 8.1 Timeouts

| Call | Today | Proposed |
|---|---|---|
| Ollama classify | 180 s (`config.py:24`) | 90 s per attempt. The recorded DeepSeek holdout mean was 19.4 s; check p99 locally before settling. |
| Ollama enrich | 180 s, sequential after routing | 60 s; run after routing is committed (as today) |
| Claude Vision | — | 60 s per attempt (SDK `timeout`), `max_retries=0` in the SDK, with the pipeline retrying (records attempts) |
| Per-ticket deadline | None | Lease 5 min. When the lease expires, the job is reclaimable and the attempt counted |
| Supabase calls in the loop | Blocking on the event loop (F-11) | Run in `asyncio.to_thread` with the client's HTTP timeout set |

### 8.2 Retries

- **Retryable:** connection error, timeout, 429, 5xx/529, Ollama connection refused. Use jittered exponential backoff (2, 4, 8 s) within an attempt, then job-level backoff (`2^attempts` minutes, at most 3 attempts), following `notify.py`.
- **Not retryable:** 400, 401, 403, schema-invalid output after one re-ask, refusal. 401 and 403 should stop the worker's vision stage and raise an alert, because a bad key is systematic.
- Do not retry a successful-but-unconfident result. Low confidence is an answer, not an error.

### 8.3 Fallbacks

| Failure | Fallback |
|---|---|
| Ollama down | `FAILED` → `RETRY` with backoff; ticket in `NEEDS_REVIEW` with the user-selected category's default department (existing `normalize_new_ticket`) |
| Claude down / timeout / refusal | Continue text-only; vision status recorded; no routing impact |
| Claude refusal on the production path | Enable server-side fallback (`fallbacks: "default"`, beta `server-side-fallback-2026-07-01`) and record the served model in `ai_analysis_runs`. Keep it **off** in the Layer 6 evaluation (refusals are measured there). |
| Preflight rejects the photo (HEIC, oversize) | Skip vision and record the reason. Optionally ask the requester for a JPEG or PNG. |
| Schema-invalid or semantic-invalid vision output | Treat as a vision failure → manual review; never auto-route |
| Escalation RPC fails | Log at error level and retry; the ticket stays visible in the queue (notification isolation, as for existing triggers) |

### 8.4 Logging and observability

- **Structured logs** (JSON) per stage with `ticket_id`, `run_id`, `stage`, `model`, `prompt_version`, `attempt`, `status`, `latency_ms`, and token counts.
- **Never log** ticket text, image bytes, base64, signed URLs, or keys. Do not run `ANTHROPIC_LOG=debug` in production.
- **Persist** the same fields in `ai_analysis_runs` (§6.4). Cost comes from `usage`, never estimates.
- **Health:** extend `/api/health` with `worker_last_tick`, `oldest_pending_age_s`, `failed_last_hour`, and `vision_enabled`. Keep the model-liveness probe separate and cheap; never use a paid call as a health check.
- **Alerts** (even if it's a log line on a demo machine): the oldest pending ticket is older than 10 min; more than 3 consecutive failures; any 401/403; daily vision spend over budget.

### 8.5 Error handling rules

- Catch the SDK's typed exceptions in a most-specific-first chain (the pattern already in `evaluation/vision/claude_client.py`). Never catch bare `Exception` around a whole stage except at the job boundary, where the job is marked failed and the error recorded.
- Check `stop_reason` before reading content (`refusal`, `max_tokens`), and assert the served model.
- Every exception path ends in a human-actionable state. This is the existing contract; keep it as a test invariant.

---

## 9. Configuring `ANTHROPIC_API_KEY` securely [PROPOSED]

1. **Where it lives:** `backend/.env` only. It is gitignored (`.gitignore:2-4`; only `.env.example` files are tracked, verified with `git ls-files`).
   - Never in `frontend/.env` or any `VITE_*` variable, never in `evaluation/`, never committed.
   - On the worker machine, restrict the file to the service account (NTFS ACL).
2. **Template:** add a commented, empty entry to `backend/.env.example`. That is a future change, not made in this audit:
   ```dotenv
   # Claude Vision (optional). Server-side only. Leave blank to disable vision.
   ANTHROPIC_API_KEY=
   VISION_ENABLED=false
   ```
3. **Settings:** declare it as `anthropic_api_key: SecretStr | None = None` and `vision_enabled: bool = False` in `Settings`. `SecretStr` keeps it out of `repr()` and logs. Add `vision_enabled_effective = vision_enabled and anthropic_api_key is not None and admin_enabled`.
4. **Pass it explicitly.** `pydantic-settings` reads `.env` into the settings object but does **not** export values to `os.environ`. [VERIFIED 2026-11-05 with the backend venv's pydantic-settings 2.16.1 and a dummy key in a scratch directory: loaded into settings = true; `repr` hides it = true; present in `os.environ` = false.] A bare `anthropic.Anthropic()` would therefore not see a key that exists only in `backend/.env`. Construct the client as `anthropic.Anthropic(api_key=settings.anthropic_api_key.get_secret_value(), timeout=60, max_retries=0)`. A process environment variable also works (the SDK reads `ANTHROPIC_API_KEY`), but one documented source is less error-prone.
5. **Key hygiene:**
   - Use a dedicated key in a separate Anthropic workspace for CampusFix, with a monthly spend limit.
   - Use one key per environment (dev / demo / prod).
   - Rotate on staff change or suspected exposure, and revoke the old key.
   - Do not reuse a personal key.
6. **CI:** never put the key in CI. Mocked tests do not need it (Layer 6 pattern). If an optional live smoke job is ever added, store the key as a protected repository secret and run that job manually, never on pull requests from forks.
7. **Verification without spending money:** a unit test asserts that `Settings` loads the key from a temporary `.env`, that `repr(settings)` hides it, that the frontend build contains no `ANTHROPIC` string, and that vision stays disabled when the key is blank.

---

## 10. Integration tests required before production

None of these exist today; existing coverage is noted where relevant.

| # | Test | Type | Guards |
|---|---|---|---|
| T-1 | Staff transfers a ticket while classification is in flight; the AI result is discarded (zero-row conditional update) | DB integration (local Supabase) | F-1 |
| T-2 | Two workers or a manual `/classify` race on one ticket; exactly one run commits and one model call is billed | DB integration | F-3 |
| T-3 | A worker crash mid-run (lease expires); the ticket is reclaimed, attempts incremented, and it reaches `FAILED` after N attempts | Integration with fake model | F-5, leases |
| T-4 | Requester spams `/classify` on a `FAILED` ticket; rejected or rate-limited, no model call | API test | F-4 |
| T-5 | Backlog of 20 pending tickets; processed oldest first, none starved | Worker test | F-6 |
| T-6 | Photo uploaded 30 s after the ticket insert; vision runs on arrival and cannot re-route an `AUTO_ROUTED` ticket, only flag it | Integration with fake Claude | F-7, fusion |
| T-7 | HEIC, an oversize file (7.6 MB raw), a mislabelled MIME type, a non-image file; preflight rejects before any Claude call, reason recorded, ticket unaffected | Unit + integration | F-8 |
| T-8 | Vision reports a hazard on a non-emergency ticket; escalation RPC pages department staff and Campus Safety exactly once; effective routing unchanged | DB integration | F-2 |
| T-9 | Text says `URGENT`; escalation fires (policy decision §12) | DB integration | F-2 |
| T-10 | Every vision failure mode (timeout, 429 then success, 529 exhausted, refusal, `max_tokens`, invalid JSON, schema-invalid, semantic-invalid, model mismatch) ends in the documented state with an `ai_analysis_runs` row | Mocked client | §8 |
| T-11 | 401 from Claude disables the vision stage, raises an alert, and text routing continues | Mocked client | §8.2 |
| T-12 | A department deactivated at runtime is not auto-routed (cache TTL or invalidation) | Integration | F-9 |
| T-13 | Emergency ticket plus a confident text result plus a vision disagreement; the Campus Safety route is preserved | Extend `test_routing.py:416` | Existing invariant |
| T-14 | No ticket text, image data, URL or key appears in logs (capture handlers) | Unit | §8.4 |
| T-15 | The backend imports `anthropic` only when vision is enabled; replace the blanket assertion at `test_routing.py:208-211` with a conditional one | Unit | F-14 |
| T-16 | Promoted schema and prompt hashes equal the frozen Layer 6 copies, or record an intentional version bump | Unit | Layer 6 freeze |
| T-17 | RLS and grants for the new tables and RPCs: students cannot read vision results for other tickets; no client can write runs; the RPC is not callable by `anon` or `authenticated` | Extend `supabase/tests/workflow_and_security.sql` | CLAUDE.md security model |
| T-18 | The withdrawn-ticket endpoint path makes no model call | API test | F-12 |
| T-19 | **Live gate (paid, approved separately):** the Layer 6 live dev evaluation meets the TEST_PLAN §6.1 acceptance criteria before `VISION_ENABLED=true` anywhere | Evaluation | Unmeasured model |
| T-20 | Shadow mode: vision runs on real tickets, results are stored and not shown or acted on, and agreement with staff decisions is reviewed for 2 weeks | Staged rollout | Production safety |

---

## 11. Prioritized implementation plan [PROPOSED]

Effort is in developer-hours, against the project's ~156-hour total budget.

| Phase | Work | Fixes | Est. h | Gate to next phase |
|---|---|---|---|---|
| **P0: Harden what exists** (no new AI) | Conditional writes (F-1); DB lease and claim RPC (F-3); oldest-first ordering (F-6); job-level retry with backoff (F-5); `/classify` limits (F-4); skip withdrawn tickets (F-12); offload blocking I/O (F-11); tests T-1 to T-5, T-12, T-18 | F-1, F-3 to F-6, F-9, F-11, F-12 | 14–18 | Workflow and security SQL tests pass; T-1 to T-5 green |
| **P1: Safety escalation** (no new AI) | `escalate_ticket_safety` RPC plus migration with RLS; escalate on text `URGENT`; tests T-8, T-9, T-13, T-17 | F-2 | 6–8 | A product owner approves who gets paged |
| **P2: Observability** | `ai_analysis_runs` table, structured logs, health fields, history for issue-type and suggestion changes; T-14 | F-10 | 6–8 | Can answer "why was ticket X routed?" from the DB |
| **P3: Privacy decision and live vision evaluation** | Privacy review (consent, faces/plates, retention); collect and annotate photos; Layer 6 `--estimate`, then the approved live dev run | F-15, T-19 | 10–14 (mostly non-code) | Acceptance criteria met; budget and privacy approved |
| **P4: Vision integration, shadow mode** | Promote the schema, preflight, interpreter and routing into `backend/app/vision/`; lazy Claude client with `SecretStr` key; attachment-triggered stage; store results only; tests T-6, T-7, T-10, T-11, T-15, T-16 | F-7, F-8, F-14 | 14–18 | Shadow results reviewed (T-20) |
| **P5: Vision fusion live** | Enable the fusion table (§6.2) and hazard escalation from vision; staff UI for suggestions and flags; `REQUEST_NEW_PHOTO` notification | — | 8–12 | Two weeks of shadow data without safety misses |
| Deferred | Policy v3 prompt adoption (after approval and a new holdout); HEIC conversion (`pillow-heif`); LangGraph re-evaluation (§7 triggers) | — | — | — |

**Total for P0–P5: about 58–78 h.** P0 to P2 (26–34 h) improve the current production path even if vision is never shipped, and should come first.

---

## 12. Decisions needed from the project owner

1. **Who gets paged on AI-detected urgency.** On text `URGENT`, a vision hazard, or both: Campus Safety, the owning department, or both? Is a false-alarm rate acceptable?
2. **Privacy approval** for sending report photos to the Claude API. Should faces and plates be blurred client-side first?
3. **Can vision alone ever auto-route** (for example, the text is vague but the photo is unambiguous)? This audit recommends *no* for v1.
4. **HEIC:** convert server-side (adds a dependency) or ask the requester for JPEG/PNG?
5. **Where the worker runs in production.** It is currently a local laptop process (`docs/LOCAL_AI_WORKER.md`). Leases (P0) make a second or hosted worker safe, but the hosting choice affects key storage (§9).
6. **Policy v3 approval timeline.** The production prompt should change only together with a new holdout evaluation (Layer 5 lesson).
