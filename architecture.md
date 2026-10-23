# CampusFix — Architecture

## 1. System Goal

CampusFix is a role-based campus issue-management system with a classification/routing layer and an analytics layer.

The architecture separates:

1. User-facing issue submission
2. Ticket management
3. AI classification and routing
4. Department operations
5. Notifications
6. Analytics and administrative visualization

## 2. High-Level Architecture

```text
┌──────────────────────────────────────────────────────┐
│                 React / TypeScript                   │
│                                                      │
│   Student Portal  |  Staff Queue  |  Admin Panel     │
└───────────┬──────────────────────────────┬───────────┘
            │ supabase-js (user JWT)       │ HTTPS / REST
            │                              │
            ▼                              ▼
┌───────────────────────────┐  ┌───────────────────────┐
│         SUPABASE          │  │   FastAPI Backend     │
│                           │  │                       │
│  Auth       (auth.users)  │  │  AI Classification    │
│  Postgres   (public.*)    │  │  Routing Decision     │
│  RLS        (authz)       │  │  Analytics Aggregates │
│  Storage    (attachments) │  │  Notification Sender  │
│  Realtime   (optional)    │  │                       │
└───────────▲───────────────┘  └───────────┬───────────┘
            │                              │
            │      service_role key        │
            └──────────────────────────────┘
                     (bypasses RLS)
```

Two paths reach the data, and they have different security properties:

- **React → Supabase** carries the signed-in user's JWT. Every query runs under that user's RLS context. This path is safe by construction.
- **FastAPI → Supabase** uses the `service_role` key, which bypasses RLS completely. This path is only as safe as the Python code, so every endpoint must perform its own authorization check.

## 3. Authorization: Row Level Security

RLS is the backbone of the security model, and it is what makes the project's headline requirement — a student must never read another student's ticket — hold even in the presence of application bugs.

### Policy shape

Policies are expressed in terms of helper functions that read the current user's role and department membership:

```sql
-- Current user's role name, from the JWT's subject.
create function auth.user_role() returns text ...

-- Department ids the current user belongs to.
create function auth.user_departments() returns setof integer ...
```

Ticket visibility then reduces to three rules:

| Who | Can select which tickets |
|---|---|
| Student | `reported_by = auth.uid()` |
| Department staff / admin | `department_id in (select auth.user_departments())` |
| System admin | all |

Write access is narrower than read access. A student may insert a ticket and update nothing on it afterward except by adding comments; staff may update status, priority, assignment, and resolution fields on tickets within their department.

### Rules

- Every table has RLS enabled. A table with RLS enabled and no policy denies all access; a table without RLS enabled is readable by anyone holding the anon key.
- Policies ship in the same migration as the table they protect.
- `service_role` bypasses all of the above. Guard it accordingly.

## 4. Request Flow

### Ticket creation

1. User authenticates through Supabase Auth.
2. User submits title, description, location, and optional attachment.
3. Client-side validation runs; server-side constraints are authoritative.
4. Ticket is inserted with `status = NEW` and `ai_routing_status = PENDING`.
5. The insert succeeds and the user receives confirmation. **This is the commit point.** Nothing after this step may cause the ticket to be lost.
6. Classification is requested from FastAPI asynchronously.
7. Local DeepSeek-R1 8B through Ollama predicts issue type and priority. FastAPI derives department from the taxonomy, applies the existing CampusFix exceptions, and writes the suggestions.
8. Successful decisions use confidence `1.0` and review decisions `0.0` as compatibility flags, not calibrated probabilities:
   - successful decision with a valid department → `ai_routing_status = AUTO_ROUTED`, status becomes `ASSIGNED`
   - ambiguous, vague, multiple-issue, or `Other` decision → `ai_routing_status = NEEDS_REVIEW`, status becomes `NEEDS_REVIEW`
   - service unavailable or error → `ai_routing_status = FAILED`, status becomes `NEEDS_REVIEW`
9. After routing is saved, a separate enrichment call preserves `original_text` and generates cleaned `title`, `description`, and `ai_explanation`. Enrichment failure leaves the routing decision intact. Both calls share the Ollama configuration in `app/config.py`.

Emergency tickets retain their existing effective department, priority, and status across every branch. Classification and enrichment do not override these protections.

All three AI outcomes leave a usable ticket in a queue a human can act on. AI failure degrades routing quality; it never costs the user their submission.

### Attachment upload

1. Client requests a signed upload URL for the ticket's storage path.
2. Client uploads directly to Supabase Storage.
3. Client inserts an `attachments` row referencing the stored object.
4. Reads go through short-lived signed download URLs. The bucket is private; object paths are never guessable public URLs.

## 5. Ticket Lifecycle

```text
                    NEW
                     │
                     ▼
              AI_PROCESSING
                     │
        ┌────────────┴────────────┐
        │                         │
  high confidence          low confidence,
        │                  or AI failure
        │                         │
        │                         ▼
        │                   NEEDS_REVIEW
        │                         │
        │                  staff picks dept
        │                         │
        └────────────┬────────────┘
                     ▼
                  ASSIGNED
                     │
                     ▼
                IN_PROGRESS ◄──────┐
                  │      │         │
                  │      └──► WAITING_FOR_USER
                  ▼
               RESOLVED
                  │
        ┌─────────┴─────────┐
        │                   │
   reopen approved       no action
        │                   │
        ▼                   ▼
    REOPENED ──────►      CLOSED
        │
        └──► IN_PROGRESS
```

The status list is seeded in `statuses` rather than hard-coded. Transition rules are enforced in the service layer, because a CHECK constraint cannot express "which transitions are legal from the current state" cleanly.

`REOPENED` is reachable only from `RESOLVED`, only within the reopen window, and only through an approved `ticket_reopen_requests` row.

## 6. Role-Based Data Access

### Student/User

Can read and write their own tickets, comments, attachments, and notifications.

Cannot read other users' tickets, campus-wide analytics, department queues, or the administrative map.

### Department Staff

Can access tickets belonging to their departments, via `user_departments`. Can accept and assign, update status, add comments and internal notes, and resolve.

### Department Admin

Everything staff can do, plus transfer tickets between departments, view department analytics, and manage department staff membership.

### System Admin

All departments, user management, routing configuration, and system-wide analytics.

## 7. Schema

The schema is implemented. See `supabase/migrations/` for the authoritative definition and `CLAUDE.md` for the table-by-table summary.

### Relationship model

```text
auth.users
    │ 1:1
    ▼
public.users ──────< user_departments >────── departments
    │                                              │
    │ reported_by                                  │ department_id
    ▼                                              │
  tickets ◄─────────────────────────────────────────
    │
    ├── issue_types        ├── comments
    ├── priorities         ├── attachments
    ├── statuses           ├── ticket_assignments
    ├── locations          ├── ticket_history
    │     └── floors       ├── ticket_reopen_requests
    │           └── buildings
    └── notifications
```

### Open schema decisions

These are the gaps between the schema as built and the requirements as written. Each needs a decision before the milestone that depends on it.

**7.1 — Nullable `locations.floor_id`.** The column is nullable, but V-030 asserts every location belongs to a floor. Two coherent options: make it `NOT NULL` and require a floor for every location, or keep it nullable to allow outdoor and building-level locations and soften V-030. Recommendation: keep nullable — a pothole in a parking lot has no floor — and rewrite V-030 to assert that a non-null `floor_id` references a valid floor.

**7.2 — Notification delivery tracking.** V-032 requires a failed notification to be logged and retryable. Add:

```text
notifications.delivery_status   -- PENDING | SENT | FAILED
notifications.attempts          -- integer, default 0
notifications.last_attempt_at
notifications.error_message
```

**7.3 — Routing rules.** System Admin is specified to manage routing rules, with no table behind it. For the MVP the cheapest honest option is a deterministic fallback map from issue type to department, which 7.4 provides. A full user-editable rules engine should be deferred to stretch.

**7.4 — `issue_types.department_id`.** Add a nullable default department to each issue type. This gives routing a sane non-AI fallback and makes the "categories belong to departments" relationship real.

**7.5 — `updated_at` maintenance.** Add a shared trigger function and attach it to every table carrying `updated_at`.

**7.6 — One pending reopen request per ticket.** Mirror the existing assignment constraint:

```sql
create unique index ux_one_pending_reopen
  on ticket_reopen_requests(ticket_id)
  where decision = 'PENDING';
```

**7.7 — Emergency flag.** Emergency handling is currently UI-only, which means it cannot be audited or reported on. Add `tickets.is_emergency boolean not null default false`, set when emergency language is detected or when the user confirms the warning.

## 8. Location Hierarchy

```text
Building → Floor → Location → Ticket
```

Example:

```text
Mother Joseph Residence Hall → 3 → Room 312 / Shower → Ticket #1042
```

`locations` stores `floor_id` only. The building is derived by joining through `floors`. Storing both would permit a location whose building and floor contradict each other.

## 9. Analytics

Analytics derive from ticket and history data. No metric has its own write path; every number is computed from the same rows the operational UI uses.

- tickets by building, category, department, status
- open vs. resolved
- average resolution time, from `created_at` to `resolved_at`
- aging open tickets
- recurring issue locations and maintenance hotspots
- trends over time

Implementation: Postgres views or FastAPI aggregate endpoints. Views are preferable where RLS can be applied to them; anything crossing department boundaries must go through FastAPI with an explicit system-admin check.

The map uses location coordinates and is restricted to staff and administrative views.

## 10. External Services

| Service | Used for | Failure behavior |
|---|---|---|
| Supabase Auth | sign-in, JWTs | hard dependency; nothing works without it |
| Supabase Postgres | all persistence | hard dependency |
| Supabase Storage | attachment files | ticket still submits; attachment retried |
| AI classification | routing suggestions | ticket routes to `NEEDS_REVIEW` |
| Email / notifications | requester updates | ticket change stays committed; delivery retried |
| OpenStreetMap tiles | map background | map degrades; list views unaffected |

Only the first two are allowed to be hard dependencies. Every other failure must degrade gracefully and leave the core ticket lifecycle intact.
