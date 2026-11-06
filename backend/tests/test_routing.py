"""Offline routing regression tests using the real Supabase HTTP client.

Run from backend: python -m unittest discover -s tests -v
No request in this suite reaches a real database or Ollama server.
"""

import asyncio
import copy
import json
import logging
import os
import sys
import unittest
from collections import Counter
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi.testclient import TestClient
from supabase import ClientOptions, create_client

# Keep app import and lifespan independent of the developer's .env.
os.environ.update(
    SUPABASE_URL="https://routing-test.supabase.co",
    SUPABASE_PUBLISHABLE_KEY="test-publishable-key",
    SUPABASE_SERVICE_ROLE_KEY="",
    SMTP_HOST="",
)

from app import classify as routing
from app import enrich, main
from app.auth import Caller, admin_client, current_caller
from app.config import Settings, get_settings

# Keep test results readable; expected failure branches still log warnings.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("campusfix").setLevel(logging.WARNING)

def config(**overrides):
    values = {
        "supabase_url": "https://routing-test.supabase.co",
        "supabase_publishable_key": "test-publishable-key",
        "supabase_service_role_key": "test-service-key",
        "smtp_host": "",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


class Database:
    """In-memory PostgREST responses; all query building uses supabase-py."""

    def __init__(self, project="https://routing-test.supabase.co"):
        self.rows = {
            "departments": [
                {"department_id": 1, "name": "Facilities", "active": True},
                {"department_id": 2, "name": "IT", "active": True},
                {"department_id": 3, "name": "Residence Life", "active": True},
                {"department_id": 4, "name": "Other / Review", "active": True},
                {"department_id": 5, "name": "Campus Safety", "active": True},
            ],
            "issue_types": [
                {"issue_type_id": 11, "name": "Plumbing", "default_department_id": 1},
                {"issue_type_id": 12, "name": "Door / Lock", "default_department_id": 1},
                {"issue_type_id": 13, "name": "Vending", "default_department_id": 1},
                {"issue_type_id": 14, "name": "Other", "default_department_id": 4},
            ],
            "priorities": [
                {"priority_id": i + 1, "name": name, "level": i + 1}
                for i, name in enumerate(["LOW", "MEDIUM", "HIGH", "URGENT"])
            ],
            "statuses": [
                {"status_id": 21, "name": "ASSIGNED"},
                {"status_id": 22, "name": "NEEDS_REVIEW"},
            ],
            "tickets": [{
                "ticket_id": 1,
                "reported_by": "student-1",
                "title": "Bathroom sink leaking",
                "original_title": "  sink pipe dripping!!!  ",
                "description": "Water drips from the sink pipe under the basin.",
                "original_text": "  My bathroom sink pipe keeps dripping.  \n",
                "ai_routing_status": "PENDING",
                "department_id": 1,
                "issue_type_id": None,
                "priority_id": 2,
                "status_id": 20,
                "is_emergency": False,
            }],
        }
        self.reads = Counter()
        self.updates = []
        self.rpc_calls = []
        self.jobs = {}
        self.http = httpx.Client(transport=httpx.MockTransport(self.handle))
        self.client = create_client(
            project, "test-service-key", options=ClientOptions(httpx_client=self.http)
        )

    @property
    def ticket(self):
        return self.rows["tickets"][0]

    # -- RPC test double ------------------------------------------------------
    # Mirrors the contract of the SQL functions in
    # supabase/migrations/20261105120000_ai_orchestration_safety.sql for these
    # single-threaded tests. Leases, concurrency, and the human-decision guard are
    # verified against real PostgreSQL in supabase/tests/local/.

    def _job(self, ticket_id):
        return self.jobs.setdefault(ticket_id, {
            "token": None, "attempts": 0, "requester": 0, "staff": 0, "last_manual": None})

    def _ticket_by_id(self, ticket_id):
        return next((t for t in self.rows["tickets"] if t["ticket_id"] == ticket_id), None)

    def _status(self, name):
        return next(s["status_id"] for s in self.rows["statuses"] if s["name"] == name)

    def _patch(self, ticket, update):
        self.updates.append(("tickets", update))
        ticket.update(update)

    def rpc(self, name, params):
        self.rpc_calls.append((name, params))
        ticket = self._ticket_by_id(params["p_ticket_id"])
        if ticket is None:
            return {"claimed": False, "ok": False, "allowed": False, "reason": "not_found", "result": "NOT_FOUND"}
        job = self._job(ticket["ticket_id"])
        if name == "ai_claim_ticket":
            allowed = ("PENDING", "FAILED") if params["p_allow_failed"] else ("PENDING",)
            if ticket.get("deleted_at"):
                return {"claimed": False, "reason": "withdrawn"}
            if ticket["ai_routing_status"] not in allowed:
                return {"claimed": False, "reason": "not_pending", "ai_routing_status": ticket["ai_routing_status"]}
            if job["token"]:
                return {"claimed": False, "reason": "in_progress"}
            if job["attempts"] >= params["p_max_attempts"]:
                self._patch(ticket, {"ai_routing_status": "FAILED"})
                return {"claimed": False, "reason": "attempts_exhausted"}
            job.update(token=f"token-{len(self.rpc_calls)}", attempts=job["attempts"] + 1)
            return {"claimed": True, "claim_token": job["token"], "attempts": job["attempts"],
                    "ticket": copy.deepcopy(ticket)}
        if name == "ai_commit_classification":
            if job["token"] != params["p_claim_token"]:
                return {"result": "LEASE_LOST"}
            job["token"] = None
            outcome, emergency = params["p_outcome"], ticket.get("is_emergency")
            active = {d["department_id"] for d in self.rows["departments"] if d.get("active")}
            if outcome == "ROUTED" and params["p_department_id"] not in active:
                outcome = "REVIEW"
            if outcome == "FAILED":
                update = {"ai_routing_status": "FAILED"}
                if not emergency:
                    update["status_id"] = self._status("NEEDS_REVIEW")
                self._patch(ticket, update)
                return {"result": "COMMITTED_FAILED", "outcome": outcome}
            update = {
                "ai_suggested_department_id": params["p_department_id"],
                "ai_suggested_issue_type_id": params["p_issue_type_id"],
                "ai_suggested_priority_id": params["p_priority_id"],
                "ai_confidence": params["p_confidence"],
            }
            if outcome == "REVIEW":
                update["ai_routing_status"] = "NEEDS_REVIEW"
                if not emergency:
                    update["status_id"] = self._status("NEEDS_REVIEW")
                self._patch(ticket, update)
                return {"result": "COMMITTED_NEEDS_REVIEW", "outcome": outcome}
            update["ai_routing_status"] = "AUTO_ROUTED"
            if params["p_issue_type_id"]:
                update["issue_type_id"] = params["p_issue_type_id"]
            if not emergency:
                update.update(department_id=params["p_department_id"], priority_id=params["p_priority_id"],
                              status_id=self._status("ASSIGNED"))
            self._patch(ticket, update)
            return {"result": "COMMITTED_AUTO_ROUTED", "outcome": outcome, "urgent_notifications": 0}
        if name == "ai_release_claim":
            released = job["token"] == params["p_claim_token"]
            if released:
                job["token"] = None
            return released
        if name == "ai_request_reclassification":
            if job["token"]:
                return {"ok": False, "reason": "in_progress"}
            if ticket["ai_routing_status"] != "PENDING":
                self._patch(ticket, {"ai_routing_status": "PENDING"})
            job["attempts"] = 0
            return {"ok": True}
        if name == "ai_register_manual_request":
            now = self.clock()
            cooldown = params["p_cooldown_seconds"]
            if job["last_manual"] is not None and job["last_manual"] + cooldown > now:
                return {"allowed": False, "reason": "cooldown",
                        "retry_after_seconds": max(1, int(job["last_manual"] + cooldown - now))}
            if not params["p_is_staff"] and job["requester"] >= params["p_requester_limit"]:
                return {"allowed": False, "reason": "limit_reached"}
            job["staff" if params["p_is_staff"] else "requester"] += 1
            job["last_manual"] = now
            return {"allowed": True}
        raise AssertionError(f"unexpected rpc {name}")

    clock = staticmethod(lambda: 0.0)

    def handle(self, request):
        if "/rpc/" in request.url.path:
            name = request.url.path.rsplit("/", 1)[-1]
            return httpx.Response(200, json=self.rpc(name, json.loads(request.content)))
        table = request.url.path.rsplit("/", 1)[-1]
        rows = self.rows[table]
        for key, expression in request.url.params.multi_items():
            if expression.startswith("eq."):
                expected = expression[3:]
                rows = [r for r in rows if str(r.get(key)).lower() == expected.lower()]
        if request.method == "PATCH":
            update = json.loads(request.content)
            self.updates.append((table, update))
            for row in rows:
                row.update(update)
        else:
            self.reads[table] += 1
        result = copy.deepcopy(rows)
        if "application/vnd.pgrst.object+json" in request.headers.get("accept", ""):
            result = result[0] if result else None
        return httpx.Response(200, json=result)


def model_response(obj):
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps({
        "message": {"content": json.dumps(obj)}
    }).encode()
    return response


class DeepSeekRequestTests(unittest.TestCase):
    def test_classification_uses_shared_model_url_and_timeout(self):
        cfg = config(ollama_model="local-test-model", ollama_url="http://localhost:1234/api/chat", ollama_timeout_seconds=12)
        with patch.object(routing.urllib.request, "urlopen", return_value=model_response({
            "issue_type": "Plumbing", "priority": "HIGH"
        })) as send:
            result = routing._classify_with_deepseek("The sink is leaking", ["Plumbing", "Other"], cfg)
        self.assertEqual(result, ("Plumbing", "HIGH"))
        request = send.call_args.args[0]
        payload = json.loads(request.data)
        self.assertEqual(payload["model"], cfg.ollama_model)
        self.assertEqual(request.full_url, cfg.ollama_url)
        self.assertEqual(send.call_args.kwargs["timeout"], 12)
        self.assertFalse(payload["stream"])
        self.assertFalse(payload["think"])
        self.assertEqual(payload["options"]["temperature"], 0)

    def test_enrichment_uses_same_configuration(self):
        cfg = config(ollama_model="local-test-model", ollama_url="http://localhost:1234/api/chat", ollama_timeout_seconds=12)
        with patch.object(enrich.urllib.request, "urlopen", return_value=model_response({
            "title": "Leaking bathroom sink", "description": "The bathroom sink drips.", "explanation": "A plumbing repair is needed."
        })) as send:
            result = enrich._generate("The sink is leaking", "Plumbing", "MEDIUM", cfg)
        request = send.call_args.args[0]
        self.assertEqual(json.loads(request.data)["model"], cfg.ollama_model)
        self.assertEqual(request.full_url, cfg.ollama_url)
        self.assertEqual(send.call_args.kwargs["timeout"], 12)
        self.assertEqual(result["explanation"], "A plumbing repair is needed.")

    def test_invalid_json_and_transport_failures_are_reported(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"message":{"content":"no JSON"}}'
        with patch.object(routing.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(ValueError):
                routing._classify_with_deepseek("The sink is leaking", ["Plumbing"], config())
        with patch.object(routing.urllib.request, "urlopen", side_effect=TimeoutError()):
            with self.assertRaises(TimeoutError):
                routing._classify_with_deepseek("The sink is leaking", ["Plumbing"], config())

    def test_enrichment_rejects_missing_required_text(self):
        for obj in [{"title": "", "description": "A problem."}, {"title": "Problem", "description": None}]:
            with self.subTest(obj=obj):
                with patch.object(enrich.urllib.request, "urlopen", return_value=model_response(obj)):
                    with self.assertRaises(ValueError):
                        enrich._generate("The sink is leaking", "Plumbing", "MEDIUM", config())


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        routing._REF_CACHE = None
        self.db = Database()
        self.addCleanup(self.db.http.close)

    def test_taxonomy_cache_reuses_lookup_reads(self):
        first = routing._reference_data(self.db.client)
        second = routing._reference_data(self.db.client)
        self.assertIs(first, second)
        self.assertEqual(self.db.reads, {"departments": 1, "issue_types": 1, "priorities": 1})

    def test_invalid_labels_become_explicit_review(self):
        for labels in [("Invented", "HIGH"), ("Plumbing", "Invented"), ("Other", "MEDIUM"), ("Plumbing", "NEEDS_REVIEW")]:
            with self.subTest(labels=labels):
                with patch.object(routing, "_classify_with_deepseek", return_value=labels):
                    result = routing.classify(self.db.ticket, self.db.client, config())
                self.assertTrue(result.needs_review)
                self.assertEqual(result.confidence, 0)
                self.assertEqual((result.department_id, result.issue_type_id, result.priority_id), (4, 14, 2))

    def test_disabled_ai_and_empty_input_do_not_call_model(self):
        with patch.object(routing, "_classify_with_deepseek") as predict:
            self.assertIsNone(routing.classify(self.db.ticket, self.db.client, config(ai_classification_enabled=False)))
            self.assertIsNone(routing.classify({}, self.db.client, config()))
        predict.assert_not_called()

    def test_failed_taxonomy_query_returns_unavailable(self):
        with patch.object(self.db.client, "table", side_effect=TimeoutError("Reference data unavailable")):
            self.assertIsNone(routing.classify(self.db.ticket, self.db.client, config()))
        self.assertIsNone(routing._REF_CACHE)

    def test_production_imports_do_not_load_historical_model_packages(self):
        self.assertNotIn("laya", sys.modules)
        self.assertNotIn("anthropic", sys.modules)
        self.assertNotIn("app.qwen_issue", sys.modules)


class EnrichmentTests(unittest.TestCase):
    def setUp(self):
        self.db = Database()
        self.addCleanup(self.db.http.close)
        self.cfg = config()
        self.polished = {
            "title": "Leaking bathroom sink", "description": "The bathroom sink pipe keeps dripping.",
            "explanation": "The reported leak requires a plumbing repair."
        }

    def enrich(self, ticket=None):
        return enrich.enrich_ticket(
            ticket if ticket is not None else self.db.ticket, self.db.client,
            issue_type_id=11, priority_id=2, settings=self.cfg,
        )

    def test_preserves_original_text_exactly_and_only_updates_text_fields(self):
        original = self.db.ticket["original_text"]
        original_title = self.db.ticket["original_title"]
        with patch.object(enrich, "_generate", return_value=self.polished) as generate:
            self.assertTrue(self.enrich())
        self.assertEqual(self.db.ticket["original_text"], original)
        self.assertEqual(self.db.ticket["original_title"], original_title)
        self.assertEqual(self.db.ticket["title"], self.polished["title"])
        self.assertNotIn("original_title", self.db.updates[-1][1])
        self.assertEqual(generate.call_args.args, (original, "Plumbing", "MEDIUM", self.cfg))
        self.assertEqual(set(self.db.updates[-1][1]), {"original_text", "title", "description", "ai_explanation"})
        self.assertEqual(self.db.ticket["ai_explanation"], self.polished["explanation"])
        self.assertEqual(self.db.ticket["status_id"], 20)

    def test_repeated_enrichment_never_writes_original_title(self):
        original_title = self.db.ticket["original_title"]
        result = dict(self.polished, original_title="Model must not replace this")
        with patch.object(enrich, "_generate", return_value=result):
            self.assertTrue(self.enrich())
            self.assertTrue(self.enrich())
        self.assertEqual(self.db.ticket["original_title"], original_title)
        self.assertTrue(all("original_title" not in update for _, update in self.db.updates))

    def test_legacy_missing_or_null_original_title_remains_valid(self):
        for present in (False, True):
            with self.subTest(present=present):
                self.db.ticket.pop("original_title", None)
                if present:
                    self.db.ticket["original_title"] = None
                with patch.object(enrich, "_generate", return_value=self.polished):
                    self.assertTrue(self.enrich())
                self.assertIsNone(self.db.ticket.get("original_title"))
                self.assertEqual("original_title" in self.db.ticket, present)
                self.assertNotIn("original_title", self.db.updates[-1][1])

    def test_generates_original_text_when_missing(self):
        self.db.ticket.pop("original_text")
        expected = self.db.ticket["title"] + "\n" + self.db.ticket["description"]
        with patch.object(enrich, "_generate", return_value=self.polished):
            self.assertTrue(self.enrich())
        self.assertEqual(self.db.ticket["original_text"], expected)

    def test_model_failure_leaves_all_existing_fields_intact(self):
        before = copy.deepcopy(self.db.ticket)
        with patch.object(enrich, "_generate", side_effect=TimeoutError("Enrichment timed out")):
            self.assertFalse(self.enrich())
        self.assertEqual(self.db.ticket, before)
        self.assertEqual(self.db.updates, [])

    def test_reference_or_write_failure_does_not_escape(self):
        with patch.object(self.db.client, "table", side_effect=RuntimeError("Database unavailable")):
            self.assertFalse(self.enrich())
        self.assertEqual(self.db.updates, [])

    def test_empty_ticket_skips_enrichment(self):
        with patch.object(enrich, "_generate") as generate:
            self.assertFalse(self.enrich({"ticket_id": 1}))
        generate.assert_not_called()


class RoutingAPITests(unittest.TestCase):
    def setUp(self):
        routing._REF_CACHE = None
        self.db = Database()
        # These tests classify the same ticket repeatedly on purpose; request
        # limits have dedicated tests in ManualRequestLimitTests.
        self.cfg = config(ai_manual_cooldown_seconds=0, ai_requester_manual_limit=100)
        self.caller = Caller("student-1", "student@caldwell.edu", "STUDENT", [])
        main.app.dependency_overrides.update({
            admin_client: lambda: self.db.client,
            current_caller: lambda: self.caller,
            get_settings: lambda: self.cfg,
        })
        self.settings_patch = patch.object(main, "settings", self.cfg)
        self.settings_patch.start()
        self.http = TestClient(main.app)
        self.http.__enter__()
        self.model = patch.object(routing, "_classify_with_deepseek", return_value=("Plumbing", "MEDIUM"))
        self.predict = self.model.start()
        self.enrichment_patch = patch.object(main, "enrich_ticket", return_value=False)
        self.enrichment = self.enrichment_patch.start()
        self.addCleanup(main.app.dependency_overrides.clear)
        self.addCleanup(self.db.http.close)
        self.addCleanup(self.settings_patch.stop)
        self.addCleanup(self.http.__exit__, None, None, None)
        self.addCleanup(self.model.stop)
        self.addCleanup(self.enrichment_patch.stop)

    def classify(self):
        response = self.http.post("/api/tickets/1/classify")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_authenticated_classification_and_unauthenticated_rejection(self):
        # Exercise the real authentication dependency. Only the remote Auth
        # service and database transport are mocked; no real tokens or writes.
        main.app.dependency_overrides.pop(current_caller)
        self.db.rows["users"] = [{"user_id": "student-1", "email": "student@caldwell.edu",
                                 "active": True, "roles": {"name": "STUDENT"}}]
        self.db.rows["user_departments"] = []
        anon = MagicMock()
        anon.auth.get_user.return_value.user.id = "student-1"
        with patch("app.auth.create_client", side_effect=[anon, self.db.client]):
            response = self.http.post("/api/tickets/1/classify", headers={
                "Authorization": "Bearer test-user-session",
                "Content-Type": "application/json",
            })
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["routing_status"], "AUTO_ROUTED")
        anon.auth.get_user.assert_called_once_with("test-user-session")
        self.predict.assert_called_once()
        self.assertEqual(self.http.post("/api/tickets/1/classify").status_code, 401)

    def test_success_auto_routes_and_persists_effective_and_suggested_values(self):
        body = self.classify()
        self.assertEqual(body["routing_status"], "AUTO_ROUTED")
        self.assertEqual(body["confidence"], 1)
        self.assertEqual(self.db.ticket["issue_type_id"], 11)
        self.assertEqual(self.db.ticket["ai_suggested_issue_type_id"], 11)
        self.assertEqual(self.db.ticket["department_id"], 1)
        self.assertEqual(self.db.ticket["status_id"], 21)
        self.assertIs(self.predict.call_args.args[-1], self.cfg)
        self.assertIs(self.enrichment.call_args.kwargs["settings"], self.cfg)

    def test_real_classification_helper_and_enrichment_run_after_route_is_saved(self):
        self.model.stop()
        self.enrichment_patch.stop()
        self.cfg.ollama_model = "configured-model"
        self.cfg.ollama_url = "http://localhost:1234/api/chat"
        responses = [
            model_response({"issue_type": "Plumbing", "priority": "HIGH"}),
            model_response({"title": "Leaking bathroom sink", "description": "The sink pipe drips.", "explanation": "A plumbing issue disrupts use."}),
        ]
        original = self.db.ticket["original_text"]
        original_title = self.db.ticket["original_title"]
        with patch.object(routing.urllib.request, "urlopen", side_effect=responses) as send:
            body = self.classify()
        self.assertEqual(body["routing_status"], "AUTO_ROUTED")
        self.assertEqual(self.db.updates[0][1]["ai_routing_status"], "AUTO_ROUTED")
        self.assertEqual(set(self.db.updates[1][1]), {"original_text", "title", "description", "ai_explanation"})
        self.assertEqual(self.db.ticket["original_text"], original)
        self.assertEqual(self.db.ticket["title"], "Leaking bathroom sink")
        self.assertEqual(self.db.ticket["original_title"], original_title)
        for call in send.call_args_list:
            self.assertEqual(json.loads(call.args[0].data)["model"], self.cfg.ollama_model)
            self.assertEqual(call.args[0].full_url, self.cfg.ollama_url)

    def test_vending_and_dorm_department_exceptions(self):
        for labels, title, expected in [
            (("Vending", "MEDIUM"), "Vending machine took my payment", 2),
            (("Door / Lock", "URGENT"), "Locked out of my dorm room", 3),
        ]:
            with self.subTest(labels=labels):
                self.predict.return_value = labels
                self.db.ticket.update(title=title, ai_routing_status="PENDING")
                self.assertEqual(self.classify()["department_id"], expected)

    def test_review_remains_review_even_at_zero_threshold(self):
        self.cfg.ai_confidence_threshold = 0
        for labels in [("Plumbing", "NEEDS_REVIEW"), ("Other", "LOW")]:
            with self.subTest(labels=labels):
                self.db.ticket.update(ai_routing_status="PENDING", department_id=1)
                self.predict.return_value = labels
                body = self.classify()
                self.assertEqual(body["routing_status"], "NEEDS_REVIEW")
                self.assertEqual(body["issue_type_id"], 14)
                self.assertEqual(self.db.ticket["department_id"], 1)
                self.assertEqual(self.db.ticket["status_id"], 22)
        self.enrichment.assert_not_called()

    def test_enrichment_false_or_unexpected_exception_preserves_success(self):
        original = self.db.ticket["original_text"]
        for failure in [None, RuntimeError("Unexpected enrichment error")]:
            with self.subTest(failure=failure):
                self.db.ticket["ai_routing_status"] = "PENDING"
                self.enrichment.side_effect = failure
                self.assertEqual(self.classify()["routing_status"], "AUTO_ROUTED")
                self.assertEqual(self.db.ticket["ai_routing_status"], "AUTO_ROUTED")
                self.assertEqual(self.db.ticket["department_id"], 1)
                self.assertEqual(self.db.ticket["original_text"], original)

    def test_model_failure_leaves_ticket_available_for_review(self):
        self.predict.side_effect = TimeoutError("Ollama timed out")
        self.assertEqual(self.classify()["routing_status"], "FAILED")
        self.assertEqual(self.db.ticket["status_id"], 22)
        self.assertEqual(self.db.ticket["department_id"], 1)
        self.enrichment.assert_not_called()

    def test_emergency_routing_survives_success_review_and_failure(self):
        for outcome in ["success", "review", "failure"]:
            with self.subTest(outcome=outcome):
                self.db.ticket.update(is_emergency=True, department_id=5, priority_id=4, status_id=21, ai_routing_status="PENDING")
                self.predict.return_value = ("Other", "NEEDS_REVIEW") if outcome == "review" else ("Plumbing", "MEDIUM")
                self.predict.side_effect = TimeoutError() if outcome == "failure" else None
                self.classify()
                for name, expected in [("department_id", 5), ("priority_id", 4), ("status_id", 21)]:
                    self.assertEqual(self.db.ticket[name], expected)

    def test_inactive_department_cannot_auto_route(self):
        self.db.rows["departments"][0]["active"] = False
        body = self.classify()
        self.assertIsNone(body["department_id"])
        self.assertEqual(body["routing_status"], "NEEDS_REVIEW")
        self.enrichment.assert_not_called()

    def test_missing_taxonomy_and_disabled_ai_degrade_gracefully(self):
        for disabled in [False, True]:
            with self.subTest(disabled=disabled):
                self.cfg.ai_classification_enabled = not disabled
                self.db.rows["issue_types"] = []
                self.db.ticket["ai_routing_status"] = "PENDING"
                self.assertEqual(self.classify()["routing_status"], "FAILED")
        self.predict.assert_not_called()

    def test_already_classified_ticket_does_not_run_again(self):
        self.db.ticket["ai_routing_status"] = "ACCEPTED"
        self.assertEqual(self.classify()["routing_status"], "ACCEPTED")
        self.assertEqual(self.db.updates, [])
        self.predict.assert_not_called()
        self.enrichment.assert_not_called()

    def test_authorization_and_staff_only_reclassification(self):
        self.caller.user_id = "different-student"
        self.assertEqual(self.http.post("/api/tickets/1/classify").status_code, 404)
        self.assertEqual(self.http.post("/api/tickets/1/reclassify").status_code, 403)
        self.assertEqual(self.db.updates, [])
        self.predict.assert_not_called()
        main.app.dependency_overrides.pop(current_caller)
        self.assertEqual(self.http.post("/api/tickets/1/classify").status_code, 401)

    def test_staff_can_reclassify_failed_ticket(self):
        self.caller.role = "SYSTEM_ADMIN"
        self.db.ticket["ai_routing_status"] = "FAILED"
        response = self.http.post("/api/tickets/1/reclassify")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["routing_status"], "AUTO_ROUTED")
        self.assertEqual(self.db.updates[0][1], {"ai_routing_status": "PENDING"})

    def test_health_and_original_text_fallback(self):
        self.assertTrue(self.http.get("/api/health").json()["ai_enabled"])
        self.db.ticket.update(title="", description="")
        self.assertEqual(self.classify()["issue_type_id"], 11)
        self.assertIn(self.db.ticket["original_text"].strip(), self.predict.call_args.args[0])
        self.cfg.ai_classification_enabled = False
        self.assertFalse(self.http.get("/api/health").json()["ai_enabled"])


class PendingClassificationTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_loop_processes_queued_pending_tickets(self):
        admin = MagicMock()
        query = admin.table.return_value
        query.select.return_value = query
        query.eq.return_value = query
        query.is_.return_value = query
        query.lt.return_value = query
        query.order.return_value = query
        query.limit.return_value = query
        query.execute.return_value.data = [{"ticket_id": 334}]
        cfg = config()

        with (
            patch("app.main.create_client", return_value=admin) as create_admin,
            patch("app.main._classify_pending_ticket") as process,
            patch(
                "app.main.asyncio.sleep",
                new=AsyncMock(side_effect=[None, asyncio.CancelledError]),
            ),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await main.classification_loop(cfg)

        create_admin.assert_called_once_with(cfg.supabase_url, cfg.supabase_service_role_key)
        query.eq.assert_called_once_with("ai_routing_status", "PENDING")
        query.is_.assert_called_once_with("deleted_at", "null")
        query.order.assert_called_once_with("created_at", desc=True)
        query.limit.assert_called_once_with(main.CLASSIFICATION_BATCH_SIZE)
        process.assert_called_once_with(334, admin, cfg)


class ManualRequestLimitTests(unittest.TestCase):
    """Server-enforced limits on user-triggered classification (audit F-4).

    The limits themselves are enforced atomically in the database and verified
    against real PostgreSQL in supabase/tests/local; these tests cover the API
    contract: status codes, Retry-After, what consumes quota, and authorization.
    """

    def setUp(self):
        routing._REF_CACHE = None
        self.db = Database()
        self.now = [1000.0]
        self.db.clock = lambda: self.now[0]
        self.cfg = config(ai_manual_cooldown_seconds=60, ai_requester_manual_limit=3)
        self.caller = Caller("student-1", "student@caldwell.edu", "STUDENT", [])
        main.app.dependency_overrides.update({
            admin_client: lambda: self.db.client,
            current_caller: lambda: self.caller,
            get_settings: lambda: self.cfg,
        })
        self.addCleanup(main.app.dependency_overrides.clear)
        self.addCleanup(self.db.http.close)
        patches = [
            patch.object(main, "settings", self.cfg),
            patch.object(routing, "_classify_with_deepseek", side_effect=TimeoutError("model down")),
            patch.object(main, "enrich_ticket", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.http = TestClient(main.app)

    def post(self, path="/api/tickets/1/classify"):
        return self.http.post(path)

    def manual_calls(self):
        return [p for name, p in self.db.rpc_calls if name == "ai_register_manual_request"]

    def test_requester_retries_on_failed_ticket_are_capped(self):
        statuses = []
        for _ in range(5):
            statuses.append(self.post().status_code)
            self.now[0] += 61  # outside the cooldown each time
        self.assertEqual(statuses, [200, 200, 200, 429, 429])
        self.assertEqual(self.db.ticket["ai_routing_status"], "FAILED")
        self.assertIn("limit", self.post().json()["detail"])
        self.assertEqual(self.db.jobs[1]["requester"], 3)

    def test_cooldown_returns_retry_after_and_does_not_consume_quota(self):
        self.assertEqual(self.post().status_code, 200)
        self.now[0] += 10
        response = self.post()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "50")
        self.assertEqual(self.db.jobs[1]["requester"], 1)

    def test_already_classified_and_withdrawn_tickets_consume_nothing(self):
        self.db.ticket["ai_routing_status"] = "AUTO_ROUTED"
        self.assertEqual(self.post().json()["routing_status"], "AUTO_ROUTED")
        self.db.ticket.update(ai_routing_status="PENDING", deleted_at="2026-11-05T10:00:00Z")
        self.assertEqual(self.post().status_code, 409)
        self.assertEqual(self.manual_calls(), [])
        self.assertEqual([n for n, _ in self.db.rpc_calls if n == "ai_claim_ticket"], [])

    def test_staff_reclassify_is_cooldown_limited_but_not_capped(self):
        self.caller.role = "SYSTEM_ADMIN"
        codes = []
        for _ in range(5):
            codes.append(self.post("/api/tickets/1/reclassify").status_code)
            self.now[0] += 61
        self.assertEqual(codes, [200] * 5)
        self.assertEqual(self.post("/api/tickets/1/reclassify").status_code, 200)
        self.assertEqual(self.post("/api/tickets/1/reclassify").status_code, 429)
        self.assertTrue(all(p["p_is_staff"] for p in self.manual_calls()))

    def test_unauthorized_callers_never_reach_the_limiter_or_model(self):
        self.caller.user_id = "someone-else"
        self.assertEqual(self.post().status_code, 404)
        self.assertEqual(self.post("/api/tickets/1/reclassify").status_code, 403)
        self.assertEqual(self.db.rpc_calls, [])


class GuardedCommitHandlingTests(unittest.TestCase):
    """The worker reacts safely to every commit outcome the database can return."""

    def setUp(self):
        routing._REF_CACHE = None
        self.db = Database()
        self.cfg = config()
        self.model = patch.object(routing, "_classify_with_deepseek", return_value=("Plumbing", "URGENT"))
        self.model.start()
        self.addCleanup(self.model.stop)
        self.enrich = patch.object(main, "enrich_ticket", return_value=True)
        self.enrich_mock = self.enrich.start()
        self.addCleanup(self.enrich.stop)
        self.addCleanup(self.db.http.close)

    def run_with_commit(self, result):
        with patch.object(main.ai_jobs, "commit", return_value={"result": result}) as commit:
            outcome = main._classify_pending_ticket(1, self.db.client, self.cfg)
        return outcome, commit

    def test_results_not_applied_never_enrich_or_write(self):
        for result, expected in [("SUPERSEDED_BY_HUMAN", "OVERRIDDEN"), ("STALE_SNAPSHOT", "PENDING"),
                                 ("LEASE_LOST", "PENDING"), ("WITHDRAWN", "PENDING"), ("ALREADY_FINAL", "PENDING")]:
            with self.subTest(result=result):
                self.db.jobs.clear()
                outcome, commit = self.run_with_commit(result)
                self.assertEqual(outcome.routing_status, expected)
                self.assertEqual(commit.call_args.args[3], "ROUTED")
                self.enrich_mock.assert_not_called()
                self.assertEqual(self.db.updates, [])

    def test_claim_refusals_do_not_call_the_model(self):
        for reason, expected in [("in_progress", "PENDING"), ("superseded_by_human", "OVERRIDDEN"),
                                 ("attempts_exhausted", "FAILED")]:
            with self.subTest(reason=reason), \
                    patch.object(main.ai_jobs, "claim", return_value={"claimed": False, "reason": reason}), \
                    patch.object(main, "classify") as classify:
                self.assertEqual(main._classify_pending_ticket(1, self.db.client, self.cfg).routing_status, expected)
                classify.assert_not_called()
        for reason, code in [("not_found", 404), ("withdrawn", 409)]:
            with self.subTest(reason=reason), \
                    patch.object(main.ai_jobs, "claim", return_value={"claimed": False, "reason": reason}):
                with self.assertRaises(main.HTTPException) as ctx:
                    main._classify_pending_ticket(1, self.db.client, self.cfg)
                self.assertEqual(ctx.exception.status_code, code)

    def test_commit_failure_releases_the_claim_and_reraises(self):
        with patch.object(main.ai_jobs, "commit", side_effect=ConnectionError("db unreachable")):
            with self.assertRaises(ConnectionError):
                main._classify_pending_ticket(1, self.db.client, self.cfg)
        releases = [p for n, p in self.db.rpc_calls if n == "ai_release_claim"]
        self.assertEqual(len(releases), 1)
        self.assertIn("db unreachable", releases[0]["p_error"])
        self.assertIsNone(self.db.jobs[1]["token"])

    def test_claim_uses_configured_lease_and_attempt_limit(self):
        self.cfg.ollama_timeout_seconds = 90
        self.cfg.ai_max_attempts = 5
        main._classify_pending_ticket(1, self.db.client, self.cfg)
        params = next(p for n, p in self.db.rpc_calls if n == "ai_claim_ticket")
        self.assertEqual((params["p_lease_seconds"], params["p_max_attempts"], params["p_allow_failed"]), (150, 5, False))


if __name__ == "__main__":
    unittest.main()
