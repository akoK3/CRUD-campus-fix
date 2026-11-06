"""Real-PostgreSQL tests for supabase/migrations/20261105120000_ai_orchestration_safety.sql.

Every migration is applied to a throwaway local PostgreSQL (see harness.py). Users are
simulated exactly as supabase/tests/workflow_and_security.sql does it (JWT claims +
role switch), so RLS, grants, and triggers are the real ones. Concurrency tests use
separate connections and threads, i.e. real row locks.

Run:  python -m unittest discover -s supabase/tests/local -v
(requires the packages in supabase/tests/local/requirements.txt)
"""
from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import harness  # noqa: E402

# Exact signatures, so test calls resolve the same overload PostgREST would.
SIGNATURES = {
    "ai_claim_ticket": [("p_ticket_id", "bigint"), ("p_worker", "text"), ("p_lease_seconds", "integer"),
                        ("p_allow_failed", "boolean"), ("p_max_attempts", "integer")],
    "ai_commit_classification": [("p_ticket_id", "bigint"), ("p_claim_token", "uuid"), ("p_outcome", "text"),
                                 ("p_department_id", "integer"), ("p_issue_type_id", "integer"),
                                 ("p_priority_id", "integer"), ("p_confidence", "numeric")],
    "ai_release_claim": [("p_ticket_id", "bigint"), ("p_claim_token", "uuid"), ("p_error", "text")],
    "ai_request_reclassification": [("p_ticket_id", "bigint"), ("p_requested_by", "uuid")],
    "ai_register_manual_request": [("p_ticket_id", "bigint"), ("p_is_staff", "boolean"),
                                   ("p_cooldown_seconds", "integer"), ("p_requester_limit", "integer")],
}

FUNCS = [
    "ai_claim_ticket(1, 'w', 60)",
    "ai_commit_classification(1, gen_random_uuid(), 'FAILED')",
    "ai_release_claim(1, gen_random_uuid(), 'x')",
    "ai_request_reclassification(1, gen_random_uuid())",
    "ai_register_manual_request(1, false, 0, 3)",
]


@unittest.skipUnless(harness.available(), "pgserver/psycopg not installed (see requirements.txt)")
class AiOrchestrationDbTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.uri = harness.create_database()
        c = harness.connect(cls.uri)
        q = lambda sql, *a: c.execute(sql, a).fetchone()  # noqa: E731
        cls.dept = {r["name"]: r["department_id"] for r in c.execute("select name, department_id from public.departments")}
        cls.prio = {r["name"]: r["priority_id"] for r in c.execute("select name, priority_id from public.priorities")}
        cls.status = {r["name"]: r["status_id"] for r in c.execute("select name, status_id from public.statuses")}
        cls.issue = {r["name"]: r["issue_type_id"] for r in c.execute("select name, issue_type_id from public.issue_types")}
        cls.users = {r["email"]: r["user_id"] for r in c.execute("select email, user_id from public.users")}
        cls.fac_issue = q("select issue_type_id from public.issue_types where name = 'Plumbing'")["issue_type_id"]
        c.close()

    # ---- helpers -----------------------------------------------------------

    def conn(self, as_email=None):
        c = harness.connect(self.uri)
        harness.act_as(c, as_email)
        self.addCleanup(c.close)
        return c

    def new_ticket(self, *, emergency=False, issue="Plumbing", text="Sink leaking under the basin"):
        c = self.conn("student2@caldwell.edu")
        row = c.execute(
            "insert into public.tickets (reported_by, original_text, title, issue_type_id, status_id, is_emergency) "
            "values (%s, %s, %s, %s, %s, %s) returning ticket_id",
            (self.users["student2@caldwell.edu"], text, text[:40], self.issue[issue], self.status["NEW"], emergency),
        ).fetchone()
        return row["ticket_id"]

    def svc(self):
        return self.conn(None)

    def rpc(self, c, fn, *args):
        placeholders = ", ".join(f"%s::{typ}" for (_, typ), _a in zip(SIGNATURES[fn], args))
        return c.execute(f"select public.{fn}({placeholders}) as r", args).fetchone()["r"]

    def fresh(self):
        """A new service-role connection, closed at test cleanup (one per thread)."""
        c = harness.connect(self.uri)
        harness.act_as(c, None)
        self.addCleanup(c.close)
        return c

    def claim(self, ticket, *, lease=60, allow_failed=False, max_attempts=3, c=None):
        return self.rpc(c or self.svc(), "ai_claim_ticket", ticket, "test-worker", lease, allow_failed, max_attempts)

    def commit(self, ticket, token, outcome="ROUTED", dept="Facilities", prio="HIGH", issue="Plumbing", conf=1.0, c=None):
        return self.rpc(c or self.svc(), "ai_commit_classification", ticket, token, outcome,
                        self.dept.get(dept) if dept else None, self.issue.get(issue) if issue else None,
                        self.prio.get(prio) if prio else None, conf)

    def ticket(self, ticket_id):
        c = harness.connect(self.uri)
        try:
            return c.execute(
                "select t.*, s.name as status_name from public.tickets t join public.statuses s using (status_id) "
                "where ticket_id = %s", (ticket_id,)).fetchone()
        finally:
            c.close()

    def notifications(self, ticket_id, type_):
        c = harness.connect(self.uri)
        try:
            return c.execute("select user_id from public.notifications where ticket_id = %s and type = %s order by user_id",
                             (ticket_id, type_)).fetchall()
        finally:
            c.close()

    def parallel(self, fn, n):
        barrier = threading.Barrier(n)
        results, errors = [None] * n, []

        def run(i):
            try:
                barrier.wait()
                results[i] = fn(i)
            except Exception as error:  # pragma: no cover - surfaced below
                errors.append(error)
        threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertEqual(errors, [])
        return results

    # ---- 1. security / privileges -------------------------------------------

    def test_rpcs_are_not_callable_by_clients(self):
        for role in ("student2@caldwell.edu", "fac.admin@caldwell.edu", "anon"):
            for call in FUNCS:
                with self.subTest(role=role, call=call):
                    c = harness.connect(self.uri)
                    self.addCleanup(c.close)
                    if role == "anon":
                        c.execute("select set_config('request.jwt.claims', '{\"role\":\"anon\"}', false)")
                        c.execute("set role anon")
                    else:
                        harness.act_as(c, role)
                    with self.assertRaisesRegex(Exception, "permission denied"):
                        c.execute(f"select public.{call}")

    def test_job_table_is_read_only_for_staff_and_hidden_from_others(self):
        t = self.new_ticket()
        self.claim(t)
        fac = self.conn("fac.staff@caldwell.edu")
        self.assertEqual(len(fac.execute("select * from public.ticket_ai_jobs where ticket_id = %s", (t,)).fetchall()), 1)
        for email in ("student2@caldwell.edu", "it.staff@caldwell.edu"):
            with self.subTest(email=email):
                c = self.conn(email)
                self.assertEqual(c.execute("select * from public.ticket_ai_jobs where ticket_id = %s", (t,)).fetchall(), [])
        with self.assertRaisesRegex(Exception, "permission denied"):
            fac.execute("update public.ticket_ai_jobs set attempts = 0 where ticket_id = %s", (t,))
        with self.assertRaisesRegex(Exception, "permission denied"):
            fac.execute("insert into public.ticket_ai_jobs (ticket_id) values (%s)", (t,))

    # ---- 2. atomic claims (F-3) ---------------------------------------------

    def test_concurrent_claims_grant_exactly_one_lease(self):
        t = self.new_ticket()
        results = self.parallel(lambda i: self.claim(t, c=self.fresh()), 12)
        claimed = [r for r in results if r["claimed"]]
        self.assertEqual(len(claimed), 1)
        self.assertEqual({r["reason"] for r in results if not r["claimed"]}, {"in_progress"})

    def test_expired_lease_is_reclaimable_and_old_holder_cannot_write(self):
        t = self.new_ticket()
        first = self.claim(t, lease=1)
        time.sleep(1.3)
        second = self.claim(t, lease=60)
        self.assertTrue(second["claimed"])
        before = self.ticket(t)
        self.assertEqual(self.commit(t, first["claim_token"])["result"], "LEASE_LOST")
        after = self.ticket(t)
        self.assertEqual((after["department_id"], after["ai_routing_status"], after["updated_at"]),
                         (before["department_id"], "PENDING", before["updated_at"]))
        self.assertEqual(self.commit(t, second["claim_token"])["result"], "COMMITTED_AUTO_ROUTED")

    def test_crash_loop_ends_in_human_review(self):
        t = self.new_ticket()
        for attempt in (1, 2, 3):
            self.assertEqual(self.claim(t, lease=1)["attempts"], attempt)
            time.sleep(1.2)  # worker "crashes": lease expires, nothing committed
        result = self.claim(t, lease=1)
        self.assertEqual(result["reason"], "attempts_exhausted")
        row = self.ticket(t)
        self.assertEqual((row["ai_routing_status"], row["status_name"]), ("FAILED", "NEEDS_REVIEW"))

    def test_release_frees_lease_only_for_its_holder(self):
        t = self.new_ticket()
        claim = self.claim(t)
        c = self.svc()
        self.assertFalse(self.rpc(c, "ai_release_claim", t, "00000000-0000-0000-0000-000000000000", "x"))
        self.assertEqual(self.claim(t)["reason"], "in_progress")
        self.assertTrue(self.rpc(c, "ai_release_claim", t, claim["claim_token"], "boom"))
        self.assertTrue(self.claim(t)["claimed"])

    # ---- 3. never overwrite staff decisions (F-1) ----------------------------

    def test_department_transfer_during_model_call_is_never_overwritten(self):
        # The audit's F-1 scenario: transfer_ticket leaves ai_routing_status PENDING
        # when no AI suggestion exists yet.
        t = self.new_ticket()
        claim = self.claim(t)
        admin = self.conn("fac.admin@caldwell.edu")
        admin.execute("select public.transfer_ticket(%s, %s, 'belongs to IT')", (t, self.dept["IT"]))
        self.assertEqual(self.ticket(t)["ai_routing_status"], "PENDING")
        result = self.commit(t, claim["claim_token"], dept="Facilities", prio="URGENT")
        self.assertEqual(result["result"], "SUPERSEDED_BY_HUMAN")
        row = self.ticket(t)
        self.assertEqual(row["department_id"], self.dept["IT"])
        self.assertEqual(row["ai_routing_status"], "OVERRIDDEN")
        self.assertEqual(row["ai_suggested_department_id"], self.dept["Facilities"])  # kept for audit
        self.assertEqual(self.notifications(t, "AI_URGENT"), [])                       # no page for a discarded result

    def test_racing_staff_update_blocks_then_wins(self):
        t = self.new_ticket()
        claim = self.claim(t)
        staff = harness.connect(self.uri, autocommit=False)
        self.addCleanup(staff.close)
        harness.act_as(staff, "fac.staff@caldwell.edu")
        staff.execute("update public.tickets set priority_id = %s where ticket_id = %s", (self.prio["LOW"], t))
        # Staff transaction holds the row lock; the AI commit must wait for it.
        timings = {}

        def ai_commit():
            timings["start"] = time.monotonic()
            r = self.commit(t, claim["claim_token"], prio="URGENT", c=self.fresh())
            timings["end"] = time.monotonic()
            return r
        holder = {}
        worker = threading.Thread(target=lambda: holder.setdefault("r", ai_commit()))
        worker.start()
        time.sleep(1.0)
        self.assertTrue(worker.is_alive(), "AI commit should be blocked by the staff row lock")
        staff.commit()
        worker.join(10)
        self.assertEqual(holder["r"]["result"], "SUPERSEDED_BY_HUMAN")
        self.assertGreaterEqual(timings["end"] - timings["start"], 0.9)
        row = self.ticket(t)
        self.assertEqual(row["priority_id"], self.prio["LOW"])
        self.assertEqual(row["status_name"], "NEW")

    def test_ai_commit_first_then_staff_change_is_kept(self):
        t = self.new_ticket()
        claim = self.claim(t)
        self.assertEqual(self.commit(t, claim["claim_token"])["result"], "COMMITTED_AUTO_ROUTED")
        self.conn("fac.staff@caldwell.edu").execute(
            "update public.tickets set priority_id = %s where ticket_id = %s", (self.prio["LOW"], t))
        self.assertEqual(self.ticket(t)["priority_id"], self.prio["LOW"])
        # A late duplicate commit with the same token is rejected.
        self.assertEqual(self.commit(t, claim["claim_token"], prio="URGENT")["result"], "LEASE_LOST")
        self.assertEqual(self.ticket(t)["priority_id"], self.prio["LOW"])

    def test_staff_issue_type_change_is_logged_and_respected(self):
        t = self.new_ticket()
        self.conn("fac.staff@caldwell.edu").execute(
            "update public.tickets set issue_type_id = %s where ticket_id = %s", (self.issue["Electrical"], t))
        c = harness.connect(self.uri)
        self.addCleanup(c.close)
        hist = c.execute("select changed_by, old_value, new_value from public.ticket_history "
                         "where ticket_id = %s and action = 'ISSUE_TYPE_CHANGED'", (t,)).fetchall()
        self.assertEqual(len(hist), 1)
        self.assertEqual((hist[0]["changed_by"], hist[0]["old_value"], hist[0]["new_value"]),
                         (self.users["fac.staff@caldwell.edu"], "Plumbing", "Electrical"))
        self.assertEqual(self.claim(t)["reason"], "superseded_by_human")
        row = self.ticket(t)
        self.assertEqual((row["issue_type_id"], row["ai_routing_status"]), (self.issue["Electrical"], "OVERRIDDEN"))

    def test_non_routing_change_makes_result_stale_then_retry_applies(self):
        t = self.new_ticket()
        claim = self.claim(t)
        self.conn("fac.staff@caldwell.edu").execute(
            "update public.tickets set description = 'clarified by staff' where ticket_id = %s", (t,))
        self.assertEqual(self.commit(t, claim["claim_token"])["result"], "STALE_SNAPSHOT")
        self.assertEqual(self.ticket(t)["ai_routing_status"], "PENDING")
        retry = self.claim(t)
        self.assertEqual(retry["ticket"]["description"], "clarified by staff")
        self.assertEqual(self.commit(t, retry["claim_token"])["result"], "COMMITTED_AUTO_ROUTED")

    def test_withdrawn_during_model_call_is_untouched(self):
        t = self.new_ticket()
        claim = self.claim(t)
        self.conn("student2@caldwell.edu").execute("select public.withdraw_own_ticket(%s)", (t,))
        self.assertEqual(self.commit(t, claim["claim_token"])["result"], "WITHDRAWN")
        row = self.ticket(t)
        self.assertEqual((row["ai_routing_status"], row["status_name"]), ("PENDING", "NEW"))
        self.assertEqual(self.claim(t)["reason"], "withdrawn")

    def test_work_in_progress_is_never_rerouted_even_after_reclassify(self):
        t = self.new_ticket()
        c = self.conn("fac.staff@caldwell.edu")
        c.execute("select public.assign_ticket(%s, %s)", (t, self.users["fac.staff@caldwell.edu"]))
        c.execute("update public.tickets set status_id = %s where ticket_id = %s", (self.status["IN_PROGRESS"], t))
        self.assertTrue(self.rpc(self.svc(), "ai_request_reclassification", t, self.users["fac.admin@caldwell.edu"])["ok"])
        self.assertEqual(self.claim(t)["reason"], "superseded_by_human")
        row = self.ticket(t)
        self.assertEqual((row["status_name"], row["department_id"]), ("IN_PROGRESS", self.dept["Facilities"]))

    def test_staff_reclassify_resets_baseline_but_later_human_action_still_wins(self):
        t = self.new_ticket()
        self.conn("fac.admin@caldwell.edu").execute("select public.transfer_ticket(%s, %s)", (t, self.dept["IT"]))
        self.assertEqual(self.claim(t)["reason"], "superseded_by_human")      # transfer blocks auto-routing
        self.assertTrue(self.rpc(self.svc(), "ai_request_reclassification", t, self.users["it.staff@caldwell.edu"])["ok"])
        claim = self.claim(t)
        self.assertTrue(claim["claimed"])                                      # explicit request: AI may route
        self.conn("it.staff@caldwell.edu").execute(
            "update public.tickets set priority_id = %s where ticket_id = %s", (self.prio["HIGH"], t))
        self.assertEqual(self.commit(t, claim["claim_token"])["result"], "SUPERSEDED_BY_HUMAN")
        self.assertEqual(self.ticket(t)["department_id"], self.dept["IT"])

    def test_existing_routing_semantics_preserved(self):
        t = self.new_ticket()
        self.assertEqual(self.commit(t, self.claim(t)["claim_token"], dept="IT", prio="MEDIUM", issue="Vending")["result"],
                         "COMMITTED_AUTO_ROUTED")
        row = self.ticket(t)
        self.assertEqual((row["department_id"], row["priority_id"], row["issue_type_id"], row["status_name"], row["ai_routing_status"]),
                         (self.dept["IT"], self.prio["MEDIUM"], self.issue["Vending"], "ASSIGNED", "AUTO_ROUTED"))
        r = self.new_ticket()
        self.commit(r, self.claim(r)["claim_token"], outcome="REVIEW", dept="Other / Review", prio="MEDIUM", issue="Other", conf=0)
        row = self.ticket(r)
        self.assertEqual((row["ai_routing_status"], row["status_name"], row["department_id"]),
                         ("NEEDS_REVIEW", "NEEDS_REVIEW", self.dept["Facilities"]))
        f = self.new_ticket()
        self.commit(f, self.claim(f)["claim_token"], outcome="FAILED", dept=None, prio=None, issue=None, conf=None)
        row = self.ticket(f)
        self.assertEqual((row["ai_routing_status"], row["status_name"]), ("FAILED", "NEEDS_REVIEW"))

    def test_inactive_department_downgrades_to_review(self):
        c = harness.connect(self.uri)
        self.addCleanup(c.close)
        c.execute("insert into public.departments (name, active) values ('Retired Dept', false)")
        dept = c.execute("select department_id from public.departments where name = 'Retired Dept'").fetchone()["department_id"]
        self.dept["Retired Dept"] = dept
        t = self.new_ticket()
        result = self.commit(t, self.claim(t)["claim_token"], dept="Retired Dept", prio="URGENT")
        self.assertEqual((result["result"], result["outcome"]), ("COMMITTED_NEEDS_REVIEW", "REVIEW"))
        row = self.ticket(t)
        self.assertEqual((row["department_id"], row["status_name"]), (self.dept["Facilities"], "NEEDS_REVIEW"))

    # ---- 4. AI-URGENT paging (F-2) -------------------------------------------

    def test_ai_urgent_pages_active_department_members_once(self):
        c = harness.connect(self.uri)
        self.addCleanup(c.close)
        c.execute("update public.users set active = false where email = 'fac.staff2@caldwell.edu'")
        self.addCleanup(lambda: harness.connect(self.uri).execute(
            "update public.users set active = true where email = 'fac.staff2@caldwell.edu'"))
        t = self.new_ticket()
        r = self.commit(t, self.claim(t)["claim_token"], prio="URGENT")
        self.assertEqual(r["urgent_notifications"], 2)
        expected = sorted([self.users["fac.admin@caldwell.edu"], self.users["fac.staff@caldwell.edu"]])
        self.assertEqual([n["user_id"] for n in self.notifications(t, "AI_URGENT")], expected)
        # Staff re-run with the same verdict: nobody is paged twice.
        self.rpc(self.svc(), "ai_request_reclassification", t, self.users["fac.admin@caldwell.edu"])
        r = self.commit(t, self.claim(t)["claim_token"], prio="URGENT")
        self.assertEqual((r["result"], r["urgent_notifications"]), ("COMMITTED_AUTO_ROUTED", 0))
        self.assertEqual(len(self.notifications(t, "AI_URGENT")), 2)
        # The database itself refuses a duplicate, whatever the caller.
        with self.assertRaisesRegex(Exception, "ux_notifications_ai_urgent_once"):
            c.execute("insert into public.notifications (user_id, ticket_id, type, message) values (%s, %s, 'AI_URGENT', 'dup')",
                      (expected[0], t))

    def test_emergency_ticket_is_not_paged_twice_and_keeps_campus_safety(self):
        t = self.new_ticket(emergency=True, text="Smoke coming from the outlet")
        insert_pages = self.notifications(t, "EMERGENCY")
        r = self.commit(t, self.claim(t)["claim_token"], dept="Facilities", prio="URGENT", issue="Electrical")
        self.assertEqual((r["result"], r["urgent_notifications"]), ("COMMITTED_AUTO_ROUTED", 0))
        self.assertEqual(self.notifications(t, "AI_URGENT"), [])
        self.assertEqual(self.notifications(t, "EMERGENCY"), insert_pages)
        row = self.ticket(t)
        self.assertEqual((row["department_id"], row["priority_id"], row["status_name"]),
                         (self.dept["Campus Safety"], self.prio["URGENT"], "NEW"))

    def test_non_urgent_review_and_failed_results_page_nobody(self):
        for outcome, prio in (("ROUTED", "HIGH"), ("REVIEW", "URGENT"), ("FAILED", None)):
            with self.subTest(outcome=outcome):
                t = self.new_ticket()
                self.commit(t, self.claim(t)["claim_token"], outcome=outcome, prio=prio,
                            dept=None if outcome == "REVIEW" else "Facilities")
                self.assertEqual(self.notifications(t, "AI_URGENT"), [])

    def test_concurrent_commits_cannot_double_page(self):
        t = self.new_ticket()
        token = self.claim(t)["claim_token"]
        results = self.parallel(lambda i: self.commit(t, token, prio="URGENT", c=self.fresh()), 6)
        self.assertEqual(sorted(r["result"] for r in results).count("COMMITTED_AUTO_ROUTED"), 1)
        self.assertEqual(len(self.notifications(t, "AI_URGENT")), 3)  # fac.admin, fac.staff, fac.staff2

    # ---- 5. manual request limits (F-4) --------------------------------------

    def test_concurrent_requester_requests_respect_the_cap(self):
        t = self.new_ticket()
        results = self.parallel(lambda i: self.rpc(self.fresh(), "ai_register_manual_request", t, False, 0, 3), 10)
        self.assertEqual(sum(1 for r in results if r["allowed"]), 3)
        self.assertEqual({r["reason"] for r in results if not r["allowed"]}, {"limit_reached"})

    def test_cooldown_applies_to_everyone_and_denials_are_free(self):
        t = self.new_ticket()
        c = self.svc()
        self.assertTrue(self.rpc(c, "ai_register_manual_request", t, True, 60, 3)["allowed"])
        denied = self.rpc(c, "ai_register_manual_request", t, False, 60, 3)
        self.assertEqual(denied["reason"], "cooldown")
        self.assertTrue(1 <= denied["retry_after_seconds"] <= 60)
        job = c.execute("select requester_manual_requests, staff_manual_requests from public.ticket_ai_jobs "
                        "where ticket_id = %s", (t,)).fetchone()
        self.assertEqual((job["requester_manual_requests"], job["staff_manual_requests"]), (0, 1))

    def test_staff_are_not_bound_by_the_requester_cap(self):
        t = self.new_ticket()
        c = self.svc()
        for _ in range(5):
            self.assertTrue(self.rpc(c, "ai_register_manual_request", t, True, 0, 0)["allowed"])
        self.assertEqual(self.rpc(c, "ai_register_manual_request", t, False, 0, 0)["reason"], "limit_reached")


@unittest.skipUnless(harness.available(), "pgserver/psycopg not installed (see requirements.txt)")
class BackendWorkerEndToEndTests(unittest.TestCase):
    """Runs the real backend worker code (app.main) against the real database.

    Only the Ollama call is replaced. A minimal adapter maps the supabase-py calls
    the worker path makes (table().select().eq().execute(), rpc().execute()) to SQL
    executed as service_role, so claims and commits use the real migration.
    """

    @classmethod
    def setUpClass(cls):
        backend = harness.REPO / "backend"
        sys.path.insert(0, str(backend))
        import os
        os.environ.setdefault("SUPABASE_URL", "https://local-harness.supabase.co")
        os.environ.setdefault("SUPABASE_PUBLISHABLE_KEY", "local-harness")
        try:
            from app import classify as routing, main  # noqa: F401
        except Exception as error:  # pragma: no cover
            raise unittest.SkipTest(f"backend dependencies unavailable: {error}")
        cls.routing, cls.main = routing, main
        from app.config import Settings
        cls.cfg = Settings(_env_file=None, supabase_url="https://local-harness.supabase.co",
                           supabase_publishable_key="x", supabase_service_role_key="local", smtp_host="")
        cls.uri = harness.create_database()

    def admin(self):
        return _SqlAdmin(self.uri)

    def new_ticket(self):
        c = harness.connect(self.uri)
        harness.act_as(c, "student2@caldwell.edu")
        try:
            return c.execute(
                "insert into public.tickets (reported_by, original_text, title, issue_type_id, status_id) "
                "values ((select auth.uid()), 'Water pouring from ceiling', 'Ceiling leak', "
                "(select issue_type_id from public.issue_types where name = 'Plumbing'), "
                "(select status_id from public.statuses where name = 'NEW')) returning ticket_id").fetchone()["ticket_id"]
        finally:
            c.close()

    def test_parallel_workers_call_the_model_once_and_commit_once(self):
        from unittest.mock import patch
        t = self.new_ticket()
        self.routing._REF_CACHE = None
        calls = []

        def slow_model(text, names, cfg):
            calls.append(text)
            time.sleep(0.5)
            return "Plumbing", "URGENT"
        barrier = threading.Barrier(6)
        results = []
        with patch.object(self.routing, "_classify_with_deepseek", side_effect=slow_model), \
                patch.object(self.main, "enrich_ticket", return_value=False):
            def run():
                barrier.wait()
                results.append(self.main._classify_pending_ticket(t, self.admin(), self.cfg))
            threads = [threading.Thread(target=run) for _ in range(6)]
            for th in threads:
                th.start()
            for th in threads:
                th.join(30)
        self.assertEqual(len(calls), 1)
        statuses = sorted(r.routing_status for r in results)
        self.assertEqual(statuses.count("AUTO_ROUTED"), 1)
        self.assertEqual(statuses.count("PENDING"), 5)
        c = harness.connect(self.uri)
        try:
            row = c.execute("select ai_routing_status from public.tickets where ticket_id = %s", (t,)).fetchone()
            pages = c.execute("select count(*) n from public.notifications where ticket_id = %s and type = 'AI_URGENT'",
                              (t,)).fetchone()["n"]
        finally:
            c.close()
        self.assertEqual(row["ai_routing_status"], "AUTO_ROUTED")
        self.assertEqual(pages, 3)

    def test_staff_transfer_while_model_runs_is_preserved_by_backend(self):
        from unittest.mock import patch
        t = self.new_ticket()
        self.routing._REF_CACHE = None

        def model_with_staff_action(text, names, cfg):
            c = harness.connect(self.uri)
            harness.act_as(c, "fac.admin@caldwell.edu")
            c.execute("select public.transfer_ticket(%s, (select department_id from public.departments where name = 'IT'))", (t,))
            c.close()
            return "Plumbing", "HIGH"
        with patch.object(self.routing, "_classify_with_deepseek", side_effect=model_with_staff_action), \
                patch.object(self.main, "enrich_ticket") as enrich:
            result = self.main._classify_pending_ticket(t, self.admin(), self.cfg)
        self.assertEqual(result.routing_status, "OVERRIDDEN")
        enrich.assert_not_called()
        c = harness.connect(self.uri)
        try:
            dept = c.execute("select d.name from public.tickets t join public.departments d using (department_id) "
                             "where ticket_id = %s", (t,)).fetchone()["name"]
        finally:
            c.close()
        self.assertEqual(dept, "IT")

    def test_exception_after_claim_releases_lease(self):
        from unittest.mock import patch
        t = self.new_ticket()
        self.routing._REF_CACHE = None
        with patch.object(self.routing, "_classify_with_deepseek", return_value=("Plumbing", "HIGH")), \
                patch.object(self.main.ai_jobs, "commit", side_effect=ConnectionError("network down")):
            with self.assertRaises(ConnectionError):
                self.main._classify_pending_ticket(t, self.admin(), self.cfg)
        c = harness.connect(self.uri)
        try:
            job = c.execute("select claim_token, last_result, last_error from public.ticket_ai_jobs where ticket_id = %s",
                            (t,)).fetchone()
        finally:
            c.close()
        self.assertIsNone(job["claim_token"])
        self.assertEqual(job["last_result"], "RELEASED_AFTER_ERROR")
        self.assertIn("network down", job["last_error"])
        with patch.object(self.routing, "_classify_with_deepseek", return_value=("Plumbing", "HIGH")), \
                patch.object(self.main, "enrich_ticket", return_value=False):
            self.assertEqual(self.main._classify_pending_ticket(t, self.admin(), self.cfg).routing_status, "AUTO_ROUTED")


class _Result:
    def __init__(self, data):
        self.data = data


class _SqlAdmin:
    """Just enough of the supabase-py client for the worker path, as service_role."""

    def __init__(self, uri):
        self.uri = uri

    def _run(self, sql, params=()):
        c = harness.connect(self.uri)
        try:
            harness.act_as(c, None)
            return c.execute(sql, params).fetchall()
        finally:
            c.close()

    def rpc(self, name, params):
        admin = self

        class Call:
            def execute(self):
                types = dict(SIGNATURES[name])
                keys = list(params)
                args = ", ".join(f"{k} => %s::{types[k]}" for k in keys)
                rows = admin._run(f"select public.{name}({args}) as r", [params[k] for k in keys])
                return _Result(rows[0]["r"])
        return Call()

    def table(self, name):
        return _Query(self, name)


class _Query:
    def __init__(self, admin, table):
        self.admin, self.table_name, self.cols, self.filters = admin, table, "*", []

    def select(self, cols):
        self.cols = cols
        return self

    def eq(self, col, value):
        self.filters.append((col, value))
        return self

    def execute(self):
        where = " and ".join(f"{c} = %s" for c, _ in self.filters) or "true"
        rows = self.admin._run(f"select {self.cols} from public.{self.table_name} where {where}",
                               [v for _, v in self.filters])
        return _Result([dict(r) for r in rows])


if __name__ == "__main__":
    unittest.main()
