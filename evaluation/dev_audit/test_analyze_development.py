"""Tests for the offline development-split audit. No network, models, or holdout scoring."""
from __future__ import annotations

import ast
import copy
import csv
import hashlib
import json
import math
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import analyze_development as ad  # noqa: E402
import reproduce_metrics  # noqa: E402

try:
    from sklearn.metrics import f1_score  # type: ignore
except Exception:  # pragma: no cover - optional
    f1_score = None

SPEC5 = ad.ModelSpec("m5", "M5", "unused.csv", ad.PRIORITIES_5, "fixed", "test")
SPEC4 = ad.ModelSpec("m4", "M4", "unused.csv", ad.PRIORITIES_4, "fixed", "test")


def inputs_available() -> bool:
    manifest = json.loads(ad.INPUT_MANIFEST.read_text(encoding="utf-8"))
    return all((ad.REPO_ROOT / p).exists() for p in manifest)


def synthetic_development():
    rows = [
        ("D1", "sink leaking", "Plumbing", "Facilities", "HIGH", "AUTO_ROUTE"),
        ("D2", "wifi down", "Wi-Fi / Network", "IT", "URGENT", "AUTO_ROUTE"),
        ("D3", "help??", "Other", "Other / Review", "NEEDS_REVIEW", "NEEDS_REVIEW"),
    ]
    return {
        cid: {"case_id": cid, "split": "development", "text": text, "expected_issue_type": issue,
              "expected_department": dept, "expected_priority": prio, "expected_action": action,
              "difficulty": "easy", "noise_type": "none"}
        for cid, text, issue, dept, prio, action in rows
    }


def prediction_rows(dev):
    return [
        {"case_id": cid, "text": c["text"], "expected_issue_type": c["expected_issue_type"],
         "expected_department": c["expected_department"], "expected_priority": c["expected_priority"],
         "pred_issue_type": c["expected_issue_type"], "pred_department": c["expected_department"],
         "pred_priority": c["expected_priority"], "latency_seconds": "1.5"}
        for cid, c in dev.items()
    ]


class LabelMetricTests(unittest.TestCase):
    def test_hand_computed_accuracy_and_macro_f1(self):
        # A: tp1 fp0 fn1 -> 2/3 ; B: tp1 fp1 fn0 -> 2/3 ; C: tp0 fp1 fn0 -> 0
        exp = ["A", "A", "B", "D"]
        pred = ["A", "B", "B", "C"]
        m = ad.label_metrics(exp, pred)
        self.assertEqual((m["correct"], m["total"]), (2, 4))
        self.assertEqual(m["accuracy"], 0.5)
        # D: tp0 fp0 fn1 -> 0 ; macro over A,B,C,D
        self.assertAlmostEqual(m["macro_f1"], (2 / 3 + 2 / 3 + 0 + 0) / 4)

    def test_label_only_in_predictions_counts_with_zero_f1(self):
        self.assertAlmostEqual(ad.label_metrics(["A", "B"], ["A", "C"])["macro_f1"], 1 / 3)

    def test_perfect_and_all_wrong(self):
        self.assertEqual(ad.label_metrics(["A", "B"], ["A", "B"])["macro_f1"], 1.0)
        self.assertEqual(ad.label_metrics(["A", "A"], ["B", "B"])["macro_f1"], 0.0)

    def test_empty_and_mismatched_inputs_raise(self):
        with self.assertRaises(ValueError):
            ad.label_metrics([], [])
        with self.assertRaises(ValueError):
            ad.label_metrics(["A"], ["A", "B"])

    def test_matches_existing_frozen_scorer_on_random_inputs(self):
        rng = random.Random(20261105)
        for _ in range(200):
            n = rng.randint(1, 40)
            labels = [chr(65 + i) for i in range(rng.randint(1, 6))]
            exp = [rng.choice(labels) for _ in range(n)]
            pred = [rng.choice(labels + ["Z"]) for _ in range(n)]
            ours, theirs = ad.label_metrics(exp, pred), reproduce_metrics.label_metrics(exp, pred)
            self.assertEqual(ours["correct"], theirs["correct"])
            self.assertAlmostEqual(ours["macro_f1"], theirs["macro_f1"], places=12)

    @unittest.skipIf(f1_score is None, "scikit-learn not installed")
    def test_matches_sklearn_macro_f1(self):
        rng = random.Random(7)
        for _ in range(100):
            n = rng.randint(1, 30)
            exp = [rng.choice("ABCD") for _ in range(n)]
            pred = [rng.choice("ABCDE") for _ in range(n)]
            expected = f1_score(exp, pred, average="macro", zero_division=0)
            self.assertAlmostEqual(ad.label_metrics(exp, pred)["macro_f1"], expected, places=12)

    def test_per_class_zero_division_is_zero(self):
        c = ad.per_class(["A", "A"], ["B", "B"])
        self.assertEqual(c["A"], {"support": 2, "predicted": 0, "tp": 0, "precision": 0.0, "recall": 0.0, "f1": 0.0})
        self.assertEqual(c["B"]["precision"], 0.0)
        self.assertEqual(c["B"]["support"], 0)


class ConfusionMatrixTests(unittest.TestCase):
    def test_rows_are_expected_and_columns_predicted(self):
        cm = ad.confusion_matrix(["A", "A", "B"], ["A", "B", "B"])
        self.assertEqual(cm["labels"], ["A", "B"])
        self.assertEqual(cm["matrix"], [[1, 1], [0, 1]])
        self.assertEqual(sum(map(sum, cm["matrix"])), 3)

    def test_explicit_labels_and_unknown_label(self):
        cm = ad.confusion_matrix(["A"], ["A"], labels=["A", "B"])
        self.assertEqual(cm["matrix"], [[1, 0], [0, 0]])
        with self.assertRaises(ValueError):
            ad.confusion_matrix(["A"], ["C"], labels=["A", "B"])


class OrdinalPriorityTests(unittest.TestCase):
    def test_outcomes_distances_and_rates(self):
        exp = ["LOW", "MEDIUM", "HIGH", "URGENT", "URGENT", "URGENT", "MEDIUM", "NEEDS_REVIEW"]
        pred = ["LOW", "HIGH", "LOW", "HIGH", "NEEDS_REVIEW", ad.INVALID, "URGENT", "LOW"]
        o = ad.ordinal_priority(exp, pred)
        self.assertEqual(o["n_ordinal_expected"], 7)  # NEEDS_REVIEW expected excluded
        self.assertEqual(o["n_scorable"], 5)
        self.assertEqual(o["counts"], {"exact": 1, "under": 2, "over": 2, "review": 1, "invalid": 1})
        self.assertAlmostEqual(sum(o[k] for k in ("exact_rate", "under_triage_rate", "over_triage_rate",
                                                  "review_deferral_rate", "invalid_rate")), 1.0)
        # signed distances: 0, +1, -2, -1, +2
        self.assertEqual(o["signed_distance_histogram"], {"-2": 1, "-1": 1, "0": 1, "1": 1, "2": 1})
        self.assertAlmostEqual(o["mean_abs_distance"], 6 / 5)
        self.assertAlmostEqual(o["mean_signed_distance"], 0.0)
        self.assertAlmostEqual(o["within_one_scorable"], 3 / 5)
        self.assertAlmostEqual(o["within_one_strict"], 3 / 7)
        self.assertEqual((o["severe_under_triage"], o["severe_over_triage"]), (1, 1))
        u = o["urgent"]
        self.assertEqual((u["n"], u["under"], u["review"], u["invalid"]), (3, 1, 1, 1))
        self.assertAlmostEqual(u["under_triage_rate"], 1 / 3)
        self.assertAlmostEqual(u["not_urgent_rate"], 1.0)
        self.assertEqual(o["per_expected_level"]["MEDIUM"], {"n": 2, "exact": 0, "under": 0, "over": 2, "review": 0, "invalid": 0})

    def test_order_is_low_medium_high_urgent(self):
        self.assertEqual(ad.ordinal_priority(["URGENT"], ["LOW"])["mean_signed_distance"], -3)
        self.assertEqual(ad.ordinal_priority(["LOW"], ["URGENT"])["mean_signed_distance"], 3)
        self.assertEqual(ad.ordinal_priority(["MEDIUM"], ["HIGH"])["counts"]["over"], 1)

    def test_no_ordinal_expected_cases_gives_none_rates(self):
        o = ad.ordinal_priority(["NEEDS_REVIEW"], ["LOW"])
        self.assertEqual(o["n_ordinal_expected"], 0)
        self.assertIsNone(o["under_triage_rate"])
        self.assertIsNone(o["mean_abs_distance"])
        self.assertIsNone(o["urgent"]["under_triage_rate"])

    def test_all_deferred_has_no_scorable_distance(self):
        o = ad.ordinal_priority(["LOW", "URGENT"], ["NEEDS_REVIEW", "NEEDS_REVIEW"])
        self.assertEqual(o["n_scorable"], 0)
        self.assertIsNone(o["mean_abs_distance"])
        self.assertIsNone(o["within_one_scorable"])
        self.assertEqual(o["within_one_strict"], 0.0)
        self.assertEqual(o["urgent"]["not_urgent_rate"], 1.0)
        self.assertEqual(o["urgent"]["under_triage_rate"], 0.0)

    def test_length_mismatch_raises(self):
        with self.assertRaises(ValueError):
            ad.ordinal_priority(["LOW"], [])


class ReviewHandlingTests(unittest.TestCase):
    def test_derive_action_matches_production_rule(self):
        self.assertEqual(ad.derive_action("Plumbing", "HIGH"), ad.AUTO)
        self.assertEqual(ad.derive_action("Plumbing", "NEEDS_REVIEW"), ad.REVIEW)
        self.assertEqual(ad.derive_action("Other", "LOW"), ad.REVIEW)
        self.assertEqual(ad.derive_action("Plumbing", "HIGH", valid=False), ad.REVIEW)

    def test_confusion_counts_and_rates(self):
        R, A = ad.REVIEW, ad.AUTO
        r = ad.review_handling([R, R, R, A, A, A, A], [R, A, A, R, A, A, A])
        self.assertEqual((r["tp"], r["fn_false_auto_route"], r["fp_unnecessary_review"], r["tn"]), (1, 2, 1, 3))
        self.assertAlmostEqual(r["recall"], 1 / 3)
        self.assertAlmostEqual(r["precision"], 1 / 2)
        self.assertAlmostEqual(r["f1"], 2 / 5)
        self.assertAlmostEqual(r["false_auto_route_rate"], 2 / 3)
        self.assertAlmostEqual(r["unnecessary_review_rate"], 1 / 4)
        self.assertAlmostEqual(r["review_volume_rate"], 2 / 7)

    def test_no_review_cases_or_predictions(self):
        r = ad.review_handling([ad.AUTO], [ad.AUTO])
        self.assertIsNone(r["recall"])
        self.assertIsNone(r["precision"])
        self.assertIsNone(r["f1"])

    def test_unknown_action_raises(self):
        with self.assertRaises(ValueError):
            ad.review_handling(["AUTO_ROUTED"], [ad.AUTO])


class McNemarTests(unittest.TestCase):
    def test_known_values(self):
        self.assertEqual(ad.mcnemar_exact([True], [True])["p_value"], 1.0)
        r = ad.mcnemar_exact([False] * 5, [True] * 5)
        self.assertEqual((r["a_only_correct"], r["b_only_correct"]), (0, 5))
        self.assertAlmostEqual(r["p_value"], 2 / 32)
        # b=1, c=1 -> p = 2 * (1 + 2) / 4 capped at 1
        self.assertEqual(ad.mcnemar_exact([True, False], [False, True])["p_value"], 1.0)

    def test_symmetric(self):
        a = [True, True, False, True, False, False, True]
        b = [False, True, True, False, False, True, True]
        self.assertAlmostEqual(ad.mcnemar_exact(a, b)["p_value"], ad.mcnemar_exact(b, a)["p_value"])


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.dev = synthetic_development()
        self.rows = prediction_rows(self.dev)
        self.holdout = frozenset({"H1"})

    def assertRejects(self, rows, error=ad.ValidationError, spec=SPEC5):
        with self.assertRaises(error):
            ad.validate_predictions(rows, self.dev, self.holdout, spec)

    def test_valid_rows_return_dataset_order(self):
        records = ad.validate_predictions(list(reversed(self.rows)), self.dev, self.holdout, SPEC5)
        self.assertEqual([r["case_id"] for r in records], ["D1", "D2", "D3"])
        self.assertTrue(all(not r["invalid_axes"] for r in records))

    def test_holdout_id_is_a_leak(self):
        leak = copy.deepcopy(self.rows) + [dict(self.rows[0], case_id="H1")]
        self.assertRejects(leak, ad.HoldoutLeakError)

    def test_duplicate_missing_unknown_ids(self):
        self.assertRejects(self.rows + [copy.deepcopy(self.rows[0])])
        self.assertRejects(self.rows[:-1])
        self.assertRejects(self.rows + [dict(self.rows[0], case_id="X9")])
        self.assertRejects([dict(self.rows[0], case_id="")] + self.rows[1:])

    def test_label_text_and_prediction_drift(self):
        for field, value in [("expected_priority", "LOW"), ("expected_department", "IT"),
                             ("expected_issue_type", "Electrical"), ("text", "edited"),
                             ("pred_priority", "  "), ("latency_seconds", "nan"),
                             ("latency_seconds", "-1"), ("latency_seconds", "fast")]:
            with self.subTest(field=field, value=value):
                rows = copy.deepcopy(self.rows)
                rows[0][field] = value
                self.assertRejects(rows)

    def test_missing_column_and_empty_file(self):
        rows = [{k: v for k, v in r.items() if k != "pred_priority"} for r in self.rows]
        self.assertRejects(rows)
        self.assertRejects([])

    def test_failed_outputs_are_kept_and_flagged_invalid(self):
        rows = copy.deepcopy(self.rows)
        rows[0].update(pred_issue_type="FAILED", pred_department="FAILED", pred_priority="FAILED")
        records = ad.validate_predictions(rows, self.dev, self.holdout, SPEC5)
        self.assertEqual(records[0]["invalid_axes"], ["issue_type", "department", "priority"])
        self.assertEqual(records[0]["pred"]["priority"], ad.INVALID)
        self.assertEqual(records[0]["raw"]["priority"], "FAILED")

    def test_needs_review_is_invalid_for_four_class_model(self):
        records = ad.validate_predictions(self.rows, self.dev, self.holdout, SPEC4)
        self.assertEqual(records[2]["invalid_axes"], ["priority"])

    def test_expected_label_consistency(self):
        bad = dict(self.dev["D1"], expected_action="NEEDS_REVIEW")
        with self.assertRaises(ad.ValidationError):
            ad.validate_expected_labels(bad)
        with self.assertRaises(ad.ValidationError):
            ad.validate_expected_labels(dict(self.dev["D1"], expected_issue_type="HVAC"))


class GuardTests(unittest.TestCase):
    def test_frozen_and_holdout_paths_refused(self):
        with self.assertRaises(ad.HoldoutLeakError):
            ad.guard_prediction_path(ad.FROZEN_DIR / "deepseek-r1_8b_holdout_predictions.csv")
        with self.assertRaises(ad.HoldoutLeakError):
            ad.guard_prediction_path(ad.REPO_ROOT / "backend/evaluation_results_holdout/x.csv")
        with self.assertRaises(ad.HoldoutLeakError):
            ad.guard_prediction_path(ad.REPO_ROOT / "some/dir/model_holdout.csv")
        ad.guard_prediction_path(ad.REPO_ROOT / "backend/evaluation_results_ollama/x.csv")

    def test_output_dir_guard(self):
        for bad in (ad.EVAL_DIR, ad.FROZEN_DIR, ad.FROZEN_DIR / "x", ad.REPO_ROOT,
                    ad.REPO_ROOT / "backend/evaluation_results_v2"):
            with self.subTest(bad=bad), self.assertRaises(ad.ValidationError):
                ad.guard_output_dir(bad)
        ad.guard_output_dir(ad.RESULTS_DIR)

    def test_department_map_matches_archived_evaluator_source(self):
        tree = ast.parse((ad.EVAL_DIR / "evaluate_holdout_models.py").read_text(encoding="utf-8"))
        mapping = next(ast.literal_eval(n.value) for n in ast.walk(tree)
                       if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "ISSUE_TO_DEPT")
        self.assertEqual(mapping, ad.ISSUE_TO_DEPT)


class DatasetTests(unittest.TestCase):
    def test_only_development_rows_are_retained(self):
        dev, holdout_ids = ad.load_development()
        self.assertEqual(len(dev), 187)
        self.assertEqual(len(holdout_ids), 62)
        self.assertTrue(all(r["split"] == "development" for r in dev.values()))
        self.assertFalse(set(dev) & holdout_ids)

    def test_checksum_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "m.json"
            manifest.write_text(json.dumps({"campusfix_benchmark_v2.csv": "0" * 64}), encoding="utf-8")
            with self.assertRaises(ad.ValidationError):
                ad.load_development(manifest_path=manifest)

    def test_changed_split_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.csv"
            with ad.DATASET.open(encoding="utf-8-sig", newline="") as src:
                rows = list(csv.DictReader(src))
            rows[0]["split"] = "holdout"
            with path.open("w", encoding="utf-8", newline="") as dst:
                writer = csv.DictWriter(dst, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaises(ad.ValidationError):
                ad.load_development(path, manifest_path=None)


@unittest.skipUnless(inputs_available(), "gitignored historical development predictions not present")
class HistoricalDevelopmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frozen_before = {p: hashlib.sha256(p.read_bytes()).hexdigest()
                             for p in [ad.DATASET, *ad.FROZEN_DIR.glob("*"), ad.EVAL_DIR / "metrics.json"]}
        opened = []
        real_open, real_read_bytes = Path.open, Path.read_bytes

        def tracking_open(self, *a, **k):
            opened.append(self.resolve())
            return real_open(self, *a, **k)

        def tracking_read_bytes(self):
            opened.append(self.resolve())
            return real_read_bytes(self)

        with patch("socket.create_connection", side_effect=AssertionError("no network")), \
                patch.object(Path, "open", tracking_open), patch.object(Path, "read_bytes", tracking_read_bytes):
            cls.report = ad.run()
        cls.opened = opened

    def test_never_opens_frozen_holdout_predictions(self):
        frozen = ad.FROZEN_DIR.resolve()
        self.assertFalse([p for p in self.opened if frozen in p.parents])
        self.assertFalse([p for p in self.opened if "holdout" in str(p).lower()])

    def test_frozen_artifacts_unchanged(self):
        for path, digest in self.frozen_before.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest, path)

    def test_scope_is_development_only(self):
        _, holdout_ids = ad.load_development()
        self.assertFalse(self.report["scope"]["holdout_scored"])
        self.assertEqual(len(self.report["cases"]), 187)
        self.assertFalse({c["case_id"] for c in self.report["cases"]} & holdout_ids)

    def test_reproduces_laya_self_reported_metrics(self):
        path = ad.REPO_ROOT / "backend/evaluation_results_v2/laya_development_metrics.csv"
        if not path.exists():
            self.skipTest("Laya metrics file absent")
        with path.open(encoding="utf-8-sig", newline="") as stream:
            recorded = {r["task"]: r for r in csv.DictReader(stream)}
        laya = self.report["models"]["laya"]
        for task, ours in (("issue_type", laya["issue_type"]), ("department", laya["department"]),
                           ("priority", laya["priority_ordinal_subset"])):
            self.assertEqual(ours["total"], int(recorded[task]["n_scored"]))
            self.assertAlmostEqual(ours["accuracy"], float(recorded[task]["accuracy"]), places=12)
            self.assertAlmostEqual(ours["macro_f1"], float(recorded[task]["macro_f1"]), places=12)

    def test_internal_consistency(self):
        for key, m in self.report["models"].items():
            with self.subTest(model=key):
                o = m["priority_ordinal"]
                self.assertEqual(sum(o["counts"].values()), 176)
                self.assertTrue(math.isclose(sum(o[k] for k in ("exact_rate", "under_triage_rate", "over_triage_rate",
                                                                "review_deferral_rate", "invalid_rate")), 1.0))
                for axis in ad.AXES:
                    self.assertEqual(sum(map(sum, m[axis]["confusion"]["matrix"])), 187)
                    diag = sum(m[axis]["confusion"]["matrix"][i][i] for i in range(len(m[axis]["confusion"]["labels"])))
                    self.assertEqual(diag, m[axis]["correct"])
                r = m["review_derived"]
                self.assertEqual(r["tp"] + r["fn_false_auto_route"], 11)
                self.assertEqual(r["fp_unnecessary_review"] + r["tn"], 176)

    def test_laya_excluded_from_five_class_priority_comparisons(self):
        axes = self.report["compatibility"]["axes"]
        self.assertNotIn("laya", axes["priority_5class"]["models"])
        self.assertTrue(axes["department_recorded"]["confounded"])
        self.assertFalse([t for t in self.report["pairwise_mcnemar"]
                          if t["axis"] == "priority_5class" and "laya" in (t["a"], t["b"])])

    def test_committed_outputs_reproduce_exactly(self):
        committed = (ad.RESULTS_DIR / "dev_metrics.json").read_text(encoding="utf-8")
        self.assertEqual(committed, ad._dump(self.report))
        self.assertEqual((ad.RESULTS_DIR / "dev_tables.md").read_text(encoding="utf-8"), ad.render_tables(self.report))


if __name__ == "__main__":
    unittest.main()
