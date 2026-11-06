"""Mocked tests: JSON schema, validator, semantic rules, and image preflight. No network."""
from __future__ import annotations

import ast
import copy
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import mock_clients  # noqa: E402
import preflight  # noqa: E402
import synthetic_fixtures as fx  # noqa: E402
import vision_schema as vs  # noqa: E402

try:
    import jsonschema  # type: ignore
except Exception:  # pragma: no cover - optional cross-check
    jsonschema = None

SCHEMA = vs.load_schema()


def valid_result(n=1, **kw):
    r = mock_clients.base_result(n, outcome="issue_identified", abstain_reason=None, issue_type="Plumbing",
                                 confidence="high", evidence=[{"image_index": 1, "finding": "water on floor"}],
                                 visible_hazards=["active_water_leak_or_flooding"], safety_escalation_suggested=True)
    r.update(kw)
    return r


class SchemaTests(unittest.TestCase):
    def test_schema_uses_only_structured_output_compatible_keywords(self):
        self.assertEqual(vs.keyword_violations(SCHEMA), [])
        api = vs.schema_for_api(SCHEMA)
        self.assertNotIn("$schema", api)
        self.assertNotIn("$id", api)

    def test_issue_taxonomy_matches_campusfix_minus_other(self):
        tree = ast.parse((HERE.parent / "evaluate_holdout_models.py").read_text(encoding="utf-8"))
        mapping = next(ast.literal_eval(n.value) for n in ast.walk(tree)
                       if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "ISSUE_TO_DEPT")
        enum = SCHEMA["properties"]["issue_type"]["anyOf"][0]["enum"]
        self.assertEqual(set(enum), set(mapping) - {"Other"})
        self.assertEqual(len(enum), 32)

    def test_valid_result_passes_both_layers(self):
        self.assertEqual(vs.validate(valid_result(), SCHEMA), [])
        self.assertEqual(vs.semantic_errors(valid_result(), 1), [])

    def test_validator_rejects_structural_errors(self):
        good = valid_result()
        mutations = {
            "missing field": lambda r: r.pop("summary"),
            "extra field": lambda r: r.update(department="Facilities"),
            "bad enum": lambda r: r.update(issue_type="HVAC"),
            "other not allowed": lambda r: r.update(issue_type="Other"),
            "wrong type": lambda r: r.update(confidence=0.9),
            "null not allowed": lambda r: r.update(summary=None),
            "bool is not integer": lambda r: r["evidence"][0].update(image_index=True),
            "nested extra": lambda r: r["image_assessments"][0].update(person_name="x"),
            "bad hazard": lambda r: r.update(visible_hazards=["gas_leak"]),
            "array as string": lambda r: r.update(visible_text="ROOM 214"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name):
                bad = copy.deepcopy(good)
                mutate(bad)
                self.assertTrue(vs.validate(bad, SCHEMA), name)

    @unittest.skipIf(jsonschema is None, "jsonschema not installed")
    def test_validator_agrees_with_jsonschema_on_random_mutations(self):
        rng = random.Random(6)
        fields = list(SCHEMA["properties"])
        junk = [None, 1, 1.5, True, "x", [], {}, ["fire_or_smoke"], "Plumbing", "abstain"]
        checker = jsonschema.Draft202012Validator(SCHEMA)
        for _ in range(400):
            r = valid_result(n=rng.randint(1, 3))
            for _ in range(rng.randint(0, 3)):
                op = rng.random()
                if op < 0.4:
                    r[rng.choice(fields)] = rng.choice(junk)
                elif op < 0.6:
                    r.pop(rng.choice(fields), None)
                elif op < 0.7:
                    r["unexpected"] = 1
            self.assertEqual(not vs.validate(r, SCHEMA), checker.is_valid(r), r)


class SemanticRuleTests(unittest.TestCase):
    def assertRule(self, result, n, code):
        errs = vs.semantic_errors(result, n)
        self.assertTrue(any(e.startswith(code) for e in errs), (code, errs))

    def test_each_rule(self):
        self.assertRule(valid_result(issue_type=None), 1, "S1")
        self.assertRule(valid_result(evidence=[]), 1, "S1")
        self.assertRule(valid_result(outcome="abstain", issue_type=None, abstain_reason=None, evidence=[]), 1, "S2")
        self.assertRule(valid_result(outcome="abstain", abstain_reason="ambiguous"), 1, "S2")
        self.assertRule(valid_result(outcome="no_issue_visible", abstain_reason=None), 1, "S3")
        self.assertRule(valid_result(), 2, "S4")
        self.assertRule(valid_result(evidence=[{"image_index": 3, "finding": "x"}]), 1, "S5")
        r = valid_result(n=2, cross_image_consistency="consistent")
        r["image_assessments"][0].update(usable=False, quality_problems=["blurry"])
        self.assertRule(r, 2, "S5")
        self.assertRule(valid_result(safety_escalation_suggested=False), 1, "S6")
        self.assertRule(valid_result(cross_image_consistency="consistent"), 1, "S7")
        r = valid_result()
        r["image_assessments"][0]["usable"] = False
        self.assertRule(r, 1, "S8")
        r["image_assessments"][0]["quality_problems"] = ["too_dark"]
        self.assertRule(r, 1, "S9")
        self.assertRule(valid_result(visible_hazards=["broken_glass", "broken_glass"]), 1, "S10")
        r = valid_result(n=2, cross_image_consistency="conflicting")
        self.assertRule(r, 2, "S11")

    def test_oracle_outputs_are_semantically_valid_for_all_synthetic_cases(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            manifest = fx.build(Path(tmp))
            for line in manifest.read_text(encoding="utf-8").splitlines():
                case = json.loads(line)
                if case.get("expected_input_rejection"):
                    continue
                result = mock_clients.oracle_result(case)
                self.assertEqual(vs.validate(result, SCHEMA), [], case["case_id"])
                self.assertEqual(vs.semantic_errors(result, len(case["images"])), [], case["case_id"])


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, data):
        path = self.dir / name
        path.write_bytes(data)
        return path

    def test_sniff_and_dimensions(self):
        cases = {
            "image/png": fx.png_bytes(321, 123),
            "image/jpeg": fx.jpeg_header_bytes(1024, 768),
            "image/webp": fx.webp_vp8x_bytes(800, 600),
            "image/gif": b"GIF89a" + (640).to_bytes(2, "little") + (480).to_bytes(2, "little") + b"\x00" * 10,
        }
        expected = {"image/png": (321, 123), "image/jpeg": (1024, 768), "image/webp": (800, 600), "image/gif": (640, 480)}
        for media, data in cases.items():
            with self.subTest(media):
                self.assertEqual(preflight.sniff(data), media)
                self.assertEqual(preflight.dimensions(data, media), expected[media])
        self.assertEqual(preflight.sniff(fx.HEIC_BYTES), "image/heic")
        self.assertIsNone(preflight.sniff(b"hello"))

    def test_extension_is_ignored(self):
        check = preflight.check_image(self.write("looks_like.jpg", fx.png_bytes(300, 300)))
        self.assertTrue(check.ok)
        self.assertEqual(check.media_type, "image/png")

    def test_rejections(self):
        self.assertEqual(preflight.check_image(self.write("a.heic", fx.HEIC_BYTES)).rejection, "unsupported_media_type")
        self.assertEqual(preflight.check_image(self.write("a.txt", b"text")).rejection, "unrecognized_format")
        self.assertEqual(preflight.check_image(self.write("big.png", fx.png_bytes(8001, 10))).rejection,
                         "exceeds_dimension_limit")
        mid = self.write("mid.png", fx.png_bytes(3001, 10))
        self.assertTrue(preflight.check_image(mid, images_in_request=20).ok)
        self.assertEqual(preflight.check_image(mid, images_in_request=21).rejection, "exceeds_dimension_limit")
        with patch.object(preflight, "MAX_BASE64_BYTES", 100):
            self.assertEqual(preflight.check_image(self.write("p.png", fx.png_bytes(300, 300))).rejection,
                             "exceeds_base64_size_limit")

    def test_base64_size_is_what_is_limited(self):
        check = preflight.check_image(self.write("p.png", fx.png_bytes(300, 300)))
        self.assertEqual(check.base64_bytes, len(preflight.encode(Path(check.path))))
        self.assertGreater(check.base64_bytes, check.raw_bytes)

    def test_warnings(self):
        self.assertIn("small_image_accuracy_risk", preflight.check_image(self.write("t.png", fx.png_bytes(150, 400))).warnings)
        gif = b"GIF89a" + (400).to_bytes(2, "little") + (400).to_bytes(2, "little") + b"\x00" * 10
        self.assertIn("gif_first_frame_only", preflight.check_image(self.write("g.gif", gif)).warnings)

    def test_visual_tokens_match_documentation_table(self):
        # (size, standard tokens, standard dims, high tokens, high dims) from the vision docs.
        table = [
            ((200, 200), 64, (200, 200), 64, (200, 200)),
            ((1000, 1000), 1296, (1000, 1000), 1296, (1000, 1000)),
            ((1092, 1092), 1521, (1092, 1092), 1521, (1092, 1092)),
            ((1920, 1080), 1560, (1456, 819), 2691, (1920, 1080)),
            ((2000, 1500), 1564, (1269, 952), 3888, (2000, 1500)),
            ((3840, 2160), 1560, (1456, 819), 4784, (2576, 1449)),
        ]
        # Known docs inconsistency: the table lists 2000x1500 -> 1269x952 on the standard tier, but the
        # docs' own reference implementation (which preflight uses verbatim) returns 1270x952. The
        # visual-token cost is identical (1,564), so cost estimates are unaffected.
        reference_dims_override = {((2000, 1500), "standard"): (1270, 952)}
        for (w, h), st, sdims, ht, hdims in table:
            with self.subTest(size=(w, h)):
                sdims = reference_dims_override.get(((w, h), "standard"), sdims)
                self.assertEqual(preflight.visual_tokens(w, h, "standard"), (st, *sdims))
                self.assertEqual(preflight.visual_tokens(w, h, "high"), (ht, *hdims))
        self.assertEqual(preflight.visual_tokens(1080, 1920, "high")[0], 2691)  # portrait symmetric
        # A4 scan example from the coordinates guide: token limit forces a resize on standard tier only.
        self.assertEqual(preflight.resized_size(1075, 1520, 1568, 1568), (924, 1307))
        self.assertEqual(preflight.resized_size(1075, 1520, 2576, 4784), (1075, 1520))

    def test_tier_for(self):
        self.assertEqual(preflight.tier_for("claude-opus-5-5"), "high")
        self.assertEqual(preflight.tier_for("claude-haiku-4-5"), "standard")


if __name__ == "__main__":
    unittest.main()
