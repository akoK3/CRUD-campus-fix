"""Mocked tests: request building, response interpretation, safe failure handling, grading,
hallucination checks, the runner, and the live-run gates. No network; anthropic not required."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import claude_client as cc  # noqa: E402
import grader  # noqa: E402
import mock_clients as mc  # noqa: E402
import run_vision_eval as rv  # noqa: E402
import synthetic_fixtures as fx  # noqa: E402
import vision_schema as vs  # noqa: E402

SCHEMA = vs.load_schema()
NO_NET = patch("socket.create_connection", side_effect=AssertionError("network disabled in mocked tests"))


def ok_result(**kw):
    r = mc.base_result(1, outcome="issue_identified", abstain_reason=None, issue_type="Plumbing", confidence="high",
                       evidence=[{"image_index": 1, "finding": "water"}])
    r.update(kw)
    return r


class RequestTests(unittest.TestCase):
    def test_request_shape(self):
        req = cc.VisionRequest("C1", [("image/png", "AAAA"), ("image/jpeg", "BBBB")], "sink leaking")
        kw = cc.build_request_kwargs(req, SCHEMA)
        self.assertEqual(kw["model"], "claude-opus-5-5")
        self.assertEqual(kw["system"][0]["cache_control"], {"type": "ephemeral"})
        content = kw["messages"][0]["content"]
        self.assertEqual([b["type"] for b in content], ["text", "image", "text", "image", "text"])
        self.assertEqual(content[0]["text"], "Image 1:")
        self.assertEqual(content[3]["source"], {"type": "base64", "media_type": "image/jpeg", "data": "BBBB"})
        self.assertIn("<reporter_description>\nsink leaking\n</reporter_description>", content[-1]["text"])
        self.assertTrue(all("cache_control" not in b for b in content))  # per-case content never cached
        self.assertEqual(kw["output_config"]["effort"], "high")
        self.assertEqual(kw["output_config"]["format"]["type"], "json_schema")
        self.assertNotIn("$schema", kw["output_config"]["format"]["schema"])
        for forbidden in ("temperature", "top_p", "top_k", "thinking", "tool_choice"):
            self.assertNotIn(forbidden, kw)

    def test_no_ticket_text(self):
        content = cc.build_request_kwargs(cc.VisionRequest("C", [("image/png", "A")], None), SCHEMA)["messages"][0]["content"]
        self.assertNotIn("reporter_description", content[-1]["text"])


class InterpretTests(unittest.TestCase):
    def interp(self, response, n=1):
        return cc.interpret_response(response, "claude-opus-5-5", n, SCHEMA)

    def test_ok(self):
        out = self.interp(mc.make_response(ok_result()))
        self.assertEqual(out.status, "ok")
        self.assertEqual(out.result["issue_type"], "Plumbing")
        self.assertEqual(out.usage["input_tokens"], 1500)

    def test_failure_statuses(self):
        cases = {
            "refusal": mc.make_response(stop_reason="refusal", text=""),
            "truncated": mc.make_response(ok_result(), stop_reason="max_tokens"),
            "model_mismatch": mc.make_response(ok_result(), model="claude-sonnet-5-5"),
            "unexpected_stop": mc.make_response(ok_result(), stop_reason="tool_use"),
            "empty_output": mc.make_response(text="  "),
            "invalid_json": mc.make_response(text="{not json"),
            "schema_invalid": mc.make_response({"outcome": "abstain"}),
            "semantic_invalid": mc.make_response(ok_result(evidence=[])),
        }
        for status, response in cases.items():
            with self.subTest(status):
                out = self.interp(response)
                self.assertEqual(out.status, status)
                if status != "semantic_invalid":
                    self.assertIsNone(out.result)

    def test_fallback_served_is_mismatch(self):
        self.assertEqual(self.interp(mc.make_response(ok_result(), fallback=True)).status, "model_mismatch")

    def test_thinking_blocks_are_skipped(self):
        response = mc.make_response(ok_result())
        self.assertEqual(response.content[0].type, "thinking")
        self.assertEqual(self.interp(response).status, "ok")


class RoutingTests(unittest.TestCase):
    def test_every_failure_goes_to_a_human(self):
        for status in cc.STATUSES:
            if status != cc.OK:
                self.assertEqual(cc.routing_decision(status, None)["action"], "MANUAL_REVIEW", status)
        self.assertEqual(cc.routing_decision("semantic_invalid", ok_result())["action"], "MANUAL_REVIEW")

    def test_decisions(self):
        blur = mc.base_result(1, abstain_reason="too_blurry")
        self.assertEqual(cc.routing_decision("ok", blur)["action"], "REQUEST_NEW_PHOTO")
        hazard_blur = mc.base_result(1, abstain_reason="too_blurry", visible_hazards=["fire_or_smoke"],
                                     safety_escalation_suggested=True)
        d = cc.routing_decision("ok", hazard_blur)
        self.assertEqual((d["action"], d["safety_escalation"]), ("MANUAL_REVIEW", True))
        self.assertEqual(cc.routing_decision("ok", ok_result(confidence="low"))["action"], "MANUAL_REVIEW")
        d = cc.routing_decision("ok", ok_result(confidence="medium"))
        self.assertEqual((d["action"], d["issue_type"]), ("SUGGEST_ISSUE_TYPE", "Plumbing"))


class RetryAndCostTests(unittest.TestCase):
    def test_retry_then_success_records_attempts(self):
        calls, sleeps = [], []
        def fn():
            calls.append(1)
            if len(calls) < 3:
                raise cc.VisionAPIError("rate_limited", "429", True)
            return "ok"
        self.assertEqual(cc.call_with_retries(fn, sleep=sleeps.append), ("ok", 3))
        self.assertEqual(len(sleeps), 2)
        self.assertGreater(sleeps[1], sleeps[0] * 0.3)

    def test_non_retryable_and_exhausted(self):
        with self.assertRaises(cc.VisionAPIError) as ctx:
            cc.call_with_retries(lambda: (_ for _ in ()).throw(cc.VisionAPIError("bad_request", "400", False)),
                                 sleep=lambda s: None)
        self.assertEqual(ctx.exception.attempts, 1)
        with self.assertRaises(cc.VisionAPIError) as ctx:
            cc.call_with_retries(lambda: (_ for _ in ()).throw(cc.VisionAPIError("server_error", "529", True)),
                                 max_attempts=3, sleep=lambda s: None)
        self.assertEqual(ctx.exception.attempts, 3)

    def test_cost(self):
        self.assertAlmostEqual(cc.cost_usd({"input_tokens": 1_000_000, "output_tokens": 100_000}, "claude-opus-5-5"), 6.0)
        self.assertIsNone(cc.cost_usd({"input_tokens": 1}, "unknown-model"))


class GraderTests(unittest.TestCase):
    def test_case_validation(self):
        good = {"case_id": "X", "category": "recognition", "split": "dev", "ground_truth_source": "human_annotated",
                "images": [{"path": "a.jpg"}], "expected_outcome": "issue_identified", "acceptable_issue_types": ["Plumbing"]}
        grader.validate_case(good)
        bad_variants = [
            dict(good, category="vibes"), dict(good, split="test"), dict(good, ground_truth_source="model"),
            dict(good, acceptable_issue_types=[]), dict(good, acceptable_issue_types=["HVAC"]),
            dict(good, expected_outcome="abstain"), dict(good, required_hazards=["x"]),
            dict(good, required_hazards=["broken_glass"], allowed_hazards=["broken_glass"]),
            dict(good, images=[{"path": "a"}, {"path": "b"}]),  # multi-image needs expected_consistency
            dict(good, expected_input_rejection="unsupported_media_type"),
        ]
        for bad in bad_variants:
            with self.subTest(bad=bad), self.assertRaises(grader.CaseError):
                grader.validate_case(bad)

    def test_fabricated_text(self):
        self.assertIsNone(grader.fabricated_text(["ROOM 214"], None))
        self.assertTrue(grader.fabricated_text(["ROOM 214"], []))
        self.assertFalse(grader.fabricated_text(["no signal"], ["No Signal"]))
        self.assertTrue(grader.fabricated_text(["No Signal", "Exit"], ["No Signal"]))
        self.assertFalse(grader.fabricated_text([], []))

    def test_hallucination_checks(self):
        case = {"case_id": "H", "category": "hallucination", "images": [{"path": "a"}], "expected_outcome": "no_issue_visible",
                "visible_text": [], "allowed_hazards": []}
        row = {"status": "ok", "routing": {"action": "SUGGEST_ISSUE_TYPE"},
               "result": ok_result(visible_hazards=["fire_or_smoke"], safety_escalation_suggested=True,
                                   visible_text=["ROOM 214"])}
        g = grader.grade(case, row)
        self.assertTrue(g["confident_misidentification"])
        self.assertEqual(g["hazards_false_alarm"], ["fire_or_smoke"])
        self.assertTrue(g["fabricated_text"])
        self.assertTrue(g["any_hallucination"])
        self.assertFalse(g["outcome_correct"])

    def test_safety_miss_and_low_confidence_not_hallucination(self):
        case = {"case_id": "S", "category": "recognition", "images": [{"path": "a"}], "expected_outcome": "abstain",
                "required_hazards": ["broken_glass"]}
        row = {"status": "ok", "routing": {"action": "MANUAL_REVIEW"}, "result": ok_result(confidence="low")}
        g = grader.grade(case, row)
        self.assertTrue(g["safety_miss"])
        self.assertFalse(g["confident_misidentification"])

    def test_refusal_and_truncation_are_not_wrong_answers(self):
        case = {"case_id": "R", "category": "recognition", "images": [{"path": "a"}], "expected_outcome": "issue_identified",
                "acceptable_issue_types": ["Plumbing"]}
        for status in ("refusal", "truncated"):
            g = grader.grade(case, {"status": status, "routing": {"action": "MANUAL_REVIEW"}, "result": None})
            self.assertIsNone(g["valid_output"])
            self.assertNotIn("outcome_correct", g)
        g = grader.grade(case, {"status": "invalid_json", "routing": {"action": "MANUAL_REVIEW"}, "result": None})
        self.assertFalse(g["valid_output"])
        self.assertTrue(g["routed_to_human_on_failure"])

    def test_wilson(self):
        lo, hi = grader.wilson(8, 36)
        self.assertAlmostEqual(lo, 0.117, places=3)
        self.assertAlmostEqual(hi, 0.381, places=3)
        self.assertIsNone(grader.wilson(0, 0))


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cases = rv.load_cases(fx.build(self.root / "fx"))
        self.by_id = {c["case_id"]: c for c in self.cases}

    def tearDown(self):
        self.tmp.cleanup()

    def run_mock(self, client, name, **kw):
        with NO_NET:
            return rv.run(self.cases, client, self.root / name, "mock", sleep=lambda s: None, **kw)

    def test_oracle_scores_perfectly_and_null_baselines_fail(self):
        oracle = self.run_mock(mc.OracleClient(self.by_id), "oracle")["overall"]
        self.assertEqual(oracle["outcome_correct"]["rate"], 1.0)
        self.assertEqual(oracle["any_hallucination"]["k"], 0)
        self.assertEqual(oracle["safety_miss"]["k"], 0)
        self.assertEqual(oracle["safe_input_rejection_correct"]["rate"], 1.0)
        abstain = self.run_mock(mc.AlwaysAbstainClient(self.by_id), "abstain")["overall"]
        self.assertEqual(abstain["issue_correct"]["k"], 0)          # both-directions coverage
        self.assertEqual(abstain["abstention"]["recall"], 1.0)
        self.assertLess(abstain["outcome_correct"]["rate"], 0.7)
        self.assertGreater(abstain["safety_miss"]["k"], 0)
        over = self.run_mock(mc.OverconfidentClient(self.by_id), "over")["overall"]
        self.assertGreater(over["any_hallucination"]["rate"], 0.5)
        self.assertEqual(over["abstention"]["recall"], 0.0)

    def test_mock_report_is_labelled_not_a_model_evaluation(self):
        metrics = self.run_mock(mc.OracleClient(self.by_id), "oracle")
        self.assertFalse(metrics["is_model_evaluation"])
        self.assertIn("measure the harness, not Claude", (self.root / "oracle" / "report.md").read_text(encoding="utf-8"))

    def test_input_rejections_never_call_the_model(self):
        client = mc.OracleClient(self.by_id)
        self.run_mock(client, "oracle")
        called = {r.case_id for r in client.calls}
        self.assertNotIn("SYN-SAFE-01", called)
        self.assertNotIn("SYN-SAFE-02", called)

    def test_infra_errors_are_separated_and_failures_routed_to_humans(self):
        script = {c["case_id"]: [lambda c=c: mc.make_response(mc.oracle_result(c))] for c in self.cases}
        script["SYN-REC-01"] = [cc.VisionAPIError("server_error", "529", True)] * 4
        script["SYN-REC-02"] = [mc.make_response(text="{broken")]
        script["SYN-AMB-01"] = [mc.make_response(stop_reason="refusal", text="")]
        metrics = self.run_mock(mc.ScriptedClient(script), "scripted")
        errors = [json.loads(l) for l in (self.root / "scripted" / "errors.jsonl").read_text().splitlines()]
        results = [json.loads(l) for l in (self.root / "scripted" / "results.jsonl").read_text().splitlines()]
        self.assertEqual([(e["case_id"], e["kind"], e["attempts"]) for e in errors], [("SYN-REC-01", "server_error", 4)])
        self.assertNotIn("SYN-REC-01", {r["case_id"] for r in results})
        by_case = {r["case_id"]: r for r in results}
        self.assertEqual(by_case["SYN-REC-02"]["routing"]["action"], "MANUAL_REVIEW")
        self.assertEqual(by_case["SYN-AMB-01"]["status"], "refusal")
        self.assertEqual(metrics["infra_errors"], {"server_error": 1})
        self.assertEqual(metrics["overall"]["refusal"]["k"], 1)
        self.assertEqual(metrics["overall"]["routed_to_human_on_failure"]["rate"], 1.0)

    def test_authentication_failure_aborts(self):
        script = {c["case_id"]: [cc.VisionAPIError("authentication", "401", False)] for c in self.cases}
        with self.assertRaisesRegex(rv.RunError, "Systematic"):
            self.run_mock(mc.ScriptedClient(script), "auth")

    def test_budget_stops_run(self):
        expensive = {c["case_id"]: [lambda c=c: mc.make_response(mc.oracle_result(c), input_tokens=2_000_000)]
                     for c in self.cases}
        with self.assertRaisesRegex(rv.RunError, "Budget reached"):
            self.run_mock(mc.ScriptedClient(expensive), "budget", budget_usd=5.0)

    def test_trajectories_never_contain_image_data(self):
        self.run_mock(mc.OracleClient(self.by_id), "oracle")
        text = (self.root / "oracle" / "trajectories.jsonl").read_text()
        encoded = rv.preflight.encode(Path(self.by_id["SYN-REC-01"]["images"][0]["_resolved"]))
        self.assertNotIn(encoded[:200], text)
        first = json.loads(text.splitlines()[0])
        image_block = next(b for b in first["request"]["messages"][0]["content"] if b["type"] == "image")
        self.assertIn("sha256", image_block["source"])
        self.assertNotIn("data", image_block["source"])

    def test_output_guards(self):
        self.run_mock(mc.OracleClient(self.by_id), "oracle")
        with self.assertRaisesRegex(rv.RunError, "not empty"):
            self.run_mock(mc.OracleClient(self.by_id), "oracle")
        with self.assertRaisesRegex(rv.RunError, "live"):
            self.run_mock(mc.OracleClient(self.by_id), "results/live/x")
        for protected in rv.PROTECTED:
            with self.subTest(protected=protected), self.assertRaisesRegex(rv.RunError, "protected"):
                rv.guard_out_dir(protected / "vision_out", "mock")

    def test_mocked_paths_never_import_anthropic(self):
        self.run_mock(mc.OracleClient(self.by_id), "oracle")
        self.assertNotIn("anthropic", sys.modules)


class LiveGateTests(unittest.TestCase):
    """The live path must refuse to start without every approval gate. No client is built."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.manifest = fx.build(Path(self.tmp.name) / "fx")
        self.out = Path(self.tmp.name) / "out"
        self.client_patch = patch.object(cc, "LiveClaudeVisionClient", side_effect=AssertionError("live client built"))
        self.client_patch.start()

    def tearDown(self):
        self.client_patch.stop()
        self.tmp.cleanup()

    def main(self, *args, env=None):
        import contextlib
        import io
        with patch.dict(os.environ, env or {}, clear=False), NO_NET, contextlib.redirect_stdout(io.StringIO()):
            if env is None:
                os.environ.pop(rv.APPROVAL_ENV, None)
            return rv.main(["--cases", str(self.manifest), *args])

    def test_requires_approval_env(self):
        with self.assertRaisesRegex(rv.RunError, "not approved"):
            self.main("--live", "--out", str(self.out), "--budget-usd", "100")

    def test_requires_budget(self):
        with self.assertRaisesRegex(rv.RunError, "budget-usd"):
            self.main("--live", "--out", str(self.out), env={rv.APPROVAL_ENV: "yes"})

    def test_estimate_over_budget_refused(self):
        with self.assertRaisesRegex(rv.RunError, "exceeds budget"):
            self.main("--live", "--out", str(self.out), "--budget-usd", "0.01", env={rv.APPROVAL_ENV: "yes"})

    def test_holdout_requires_explicit_flag(self):
        with self.assertRaisesRegex(rv.RunError, "Holdout"):
            self.main("--estimate", "--split", "holdout")

    def test_estimate_makes_no_calls(self):
        self.assertEqual(self.main("--estimate"), 0)


class TemplateTests(unittest.TestCase):
    def test_live_case_template_is_valid_and_photos_are_pending(self):
        cases = rv.load_cases(HERE / "cases" / "cases_template.jsonl")
        self.assertEqual({c["category"] for c in cases}, set(grader.CATEGORIES))
        self.assertTrue(rv.missing_images(cases))  # real photos not yet collected

    @unittest.skipUnless(__import__("importlib").util.find_spec("anthropic"), "anthropic SDK not installed")
    def test_live_client_maps_sdk_errors(self):  # pragma: no cover - runs only where the SDK exists
        import anthropic
        client = cc.LiveClaudeVisionClient.__new__(cc.LiveClaudeVisionClient)
        client._anthropic, client._schema, client._fallbacks = anthropic, SCHEMA, False

        class Boom:
            class messages:
                @staticmethod
                def create(**kw):
                    raise anthropic.APIConnectionError(request=None)
        client._client = Boom
        with self.assertRaises(cc.VisionAPIError) as ctx:
            client.analyze(cc.VisionRequest("C", [("image/png", "A")], None))
        self.assertEqual(ctx.exception.kind, "connection")


if __name__ == "__main__":
    unittest.main()
