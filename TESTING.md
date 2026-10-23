# CampusFix — Manual Test Script

How to exercise the whole product by hand, and what each step actually proves.
IDs cross-reference `verification.md`.

## Test accounts

Created for local verification only. **Delete before any real deployment** —
see Teardown at the bottom.

| Email | Role | Department |
|---|---|---|
| *your own account* | SYSTEM_ADMIN | all |
| `fac.admin@caldwell.edu` | DEPARTMENT_ADMIN | Facilities |
| `fac.staff@caldwell.edu` | DEPARTMENT_STAFF | Facilities |
| `it.staff@caldwell.edu` | DEPARTMENT_STAFF | IT |
| `student2@caldwell.edu` | STUDENT | — |

Create local test accounts through normal signup and choose separate private passwords.
No shared test passwords are stored in this repository.

Use a private window for the second account so two sessions can be open at once.

---

## 1. Student submission — V-001

As **student2**: Report an issue, with a title, a description of 20+
characters, category **Plumbing**, and a building and floor. Submit.

**Expect:** you land on the ticket detail page. Department reads
**Facilities**, status **New**, and History already has a `Created` entry.

**Proves:** deterministic routing works with AI switched off entirely. The
`default_department_id` on the Plumbing issue type did that, not a model.

**1b. Minimum information (V-004).** Submit with a 5-character description.
The button stays disabled and the hint explains why.

**1c. Attachment validation.** Attach a `.pdf`, or an image over 10 MB.
Rejected before upload, with a readable message.

---

## 2. Emergency detection — V-005

As **student2**, title a report "Smoke in the stairwell" and describe smoke
coming from a stairwell.

**Expect:** a red banner appears *as you type*, before you submit. Submit
raises an interstitial naming 911 and Campus Safety at (973) 618-3259.
Choosing "I've called — file the report" creates the ticket flagged
**Emergency**, routed to **Campus Safety**, priority **Urgent**.

**Proves:** emergency handling is client-side and instant. Stop the backend
and repeat — behaviour is identical. That is the entire point of doing this
outside the AI layer.

---

## 3. Cross-user isolation — V-003

The headline security requirement.

1. As **student2**, note your ticket number from the URL, e.g. `/tickets/4`.
2. Sign in as **it.staff** — IT department, not Facilities.
3. Navigate directly to `/tickets/4`.

**Expect:** "Ticket not available."

**Proves:** this is not a hidden button. The database returned zero rows.
Verify at the SQL level:

```sql
begin;
set local role authenticated;
set local request.jwt.claims = '{"sub":"<it.staff user_id>","role":"authenticated"}';
select ticket_id from public.tickets;   -- the Facilities ticket is absent
rollback;
```

Repeat as **fac.staff**: the same ticket *is* visible, because it belongs to
their department.

---

## 4. Staff workflow — V-020 through V-023

As **fac.staff**:

1. **Queue** lists the Plumbing ticket. Emergencies sort to the top whatever
   sort order is selected.
2. Filter by status, priority, building — the count updates.
3. Open the ticket, **Claim** it. Status becomes Assigned and History records it.
   The requester gets a notification (bell in the header).
4. **Move to In Progress**. Set a **Priority** from the dropdown.
5. Add a public comment, then tick **Internal note** and add a second.
6. **Resolve** with resolution notes.

**Expect:** every step appends to History with an actor and a timestamp.

**4c. Status rules are enforced by the database.** Only legal next steps are
offered as buttons, and a crafted request for an illegal one (NEW straight to
RESOLVED, say) fails with "A ticket cannot move from NEW to RESOLVED".

**4d. Reassignment and transfer (V-§8).** As **fac.admin**, reassign a ticket
with the **Assign to** dropdown, then **Unassign** it: History shows both. Use
**Transfer** to move it to IT. It disappears from Facilities and appears,
unassigned, in **it.staff**'s queue. **fac.staff** has no Transfer button, and
the `transfer_ticket` RPC refuses them if called directly.

**4e. Staff management.** As **fac.admin**, open **Staff** and add
**it.staff** to Facilities, then remove them. As the system admin the page is
**Users** and also changes roles and deactivates accounts.

**4b. Internal notes really are hidden (V-002).** Sign back in as **student2**
and open the same ticket. The public comment is there; the internal note is
not. It is filtered by RLS, not by CSS — check the network response if you
want to be certain.

---

## 5. Reopen flow — V-024, V-025, V-026

As **student2**, on the resolved ticket:

1. **Request reopen**, with a reason.
2. Try again — blocked. One pending request per ticket, enforced by a partial
   unique index.
3. Sign in as **fac.admin**: the bell shows the request, the queue shows a
   "reopen requests waiting" banner, and the ticket offers Approve / Decline
   with an optional note.
4. Approve. The ticket returns to **Reopened** and re-enters the queue.
   Declining instead leaves it resolved and tells the requester why.

**fac.staff** does not see Approve/Deny. That is a DEPARTMENT_ADMIN action by
design — `verification.md` §8.

**5b. A comment does not reopen (V-026).** On a resolved ticket, comment as the
requester. Status does not change; the placeholder text says so.

**5c. Window expiry (V-025).**

```sql
update public.tickets set resolved_at = now() - interval '8 days'
 where ticket_id = <id>;
```

Reload: the Reopen button is gone and the hint explains why. The RLS policy
also rejects a direct insert, so a crafted request cannot get around it.

**5d. Auto-close.** A nightly pg_cron job closes resolved tickets once the
window passes. To see it immediately, after the SQL above run
`select private.close_expired_resolved_tickets();` and reload: **Closed**.

---

## 6. Withdraw

As **student2**, submit a fresh ticket and **Withdraw** it. It leaves your list.

Now have staff move a ticket to In Progress and try to withdraw that one. The
RPC refuses: work has begun. Requesters have no UPDATE policy on tickets at
all — withdrawal exists only through `withdraw_own_ticket()`, which can set
nothing but `deleted_at`.

---

## 7. Analytics

Compare Analytics as **fac.staff** against your **SYSTEM_ADMIN** account.

**Expect:** staff see Facilities figures only; the system admin sees every
department. Same page, same query, different rows — RLS filters the underlying
tickets, so the chart code never needs to know about permissions.

Students have no Analytics tab, and `/analytics` redirects them away.

For a realistic demo, load `supabase/seed/demo_tickets.sql` in the SQL editor
first (about 300 tickets over four months), and remove it afterwards with
`supabase/seed/demo_tickets_cleanup.sql`.

---

## 8. Privilege escalation — all of these must fail

**Automated:** paste `supabase/tests/workflow_and_security.sql` into the SQL
editor and run it. It impersonates the test accounts, runs 28 checks covering
this section and the workflow rules, and rolls everything back. Every row must
say PASS. The manual version:

```sql
begin;
set local role authenticated;
set local request.jwt.claims = '{"sub":"<student2 id>","role":"authenticated"}';

-- Promote self: blocked by a column-level grant
update public.users set role_id = 4 where user_id = '<student2 id>';

-- Edit own ticket directly: 0 rows, because no UPDATE policy applies
update public.tickets set title = 'hijacked' where reported_by = '<student2 id>';

rollback;
```

Signed out, as `anon`, every table returns permission denied.

Also run the Supabase security advisors after any migration. Expected
findings: the workflow RPCs (`withdraw_own_ticket`, `assign_ticket`,
`unassign_ticket`, `transfer_ticket`, `decide_reopen`, `admin_set_user_role`,
`admin_set_user_active`, `set_department_member`) are callable by
authenticated users. That is their purpose; each checks the caller's role and
access internally. Anything else is a real finding.

---

## 9. AI classification

**With local Ollama stopped or `AI_CLASSIFICATION_ENABLED=false`:** submit a
ticket leaving category blank. The backend marks classification `FAILED` and
routes to **Needs Review**. The ticket exists and the user sees no submission
error. This is V-012 and remains a release gate.

**With Ollama running and `deepseek-r1:8b` available:** the model predicts issue
type and priority. The backend derives department from the taxonomy and existing
CampusFix exceptions. Successful decisions auto-route; vague, ambiguous,
multiple-issue, and `Other` decisions go to **Needs Review**. The confidence
values `1.0`/`0.0` are routing flags, not probability estimates. A staff override
remains separate from the original suggestion (V-010, V-011, V-013).

After an automatic route, verify `original_text` retains the submission and
`title`, `description`, and `ai_explanation` contain the writing enrichment.
If enrichment fails, routing must remain successful and the existing text must
survive. An emergency ticket must retain its effective department, priority,
and status regardless of classification or enrichment outcome.

Stop the backend mid-test and submit again. Ticket creation is unaffected.

---

## Teardown

```sql
delete from public.ticket_history where ticket_id in (
  select ticket_id from public.tickets where reported_by in (
    select user_id from public.users where email in (
      'student2@caldwell.edu','fac.staff@caldwell.edu',
      'fac.admin@caldwell.edu','it.staff@caldwell.edu')));

delete from public.tickets where reported_by in (
  select user_id from public.users where email in (
    'student2@caldwell.edu','fac.staff@caldwell.edu',
    'fac.admin@caldwell.edu','it.staff@caldwell.edu'));

delete from auth.users where email in (
  'student2@caldwell.edu','fac.staff@caldwell.edu',
  'fac.admin@caldwell.edu','it.staff@caldwell.edu');
```

## If you create test users by hand again

These accounts were inserted straight into `auth.users`. If you do that again,
set `confirmation_token`, `recovery_token`, `email_change_token_new` and
`email_change` to `''` rather than leaving them NULL, and insert a matching
`auth.identities` row. Otherwise sign-in fails with an opaque
`500 Database error querying schema`, because GoTrue cannot scan NULL into a
string field.

Signing up through the UI avoids all of this and is the better default.
