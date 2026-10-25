"""Offline integrity/scoring tests; no Ollama or database connections."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from reproduce_metrics import label_metrics, read_rows, reproduce, validate_predictions

HERE = Path(__file__).resolve().parent


class FrozenMetricsTests(unittest.TestCase):
    def test_artifacts_and_recorded_report_reproduce_without_network(self):
        with patch("socket.create_connection", side_effect=AssertionError("No network allowed")):
            report = reproduce()
        recorded = json.loads((HERE / "metrics.json").read_text(encoding="utf-8"))
        self.assertEqual(report, recorded)
        self.assertEqual(report["split_counts"], {"development": 187, "holdout": 62})
        for model, expected in [("deepseek-r1:8b", [51, 51, 40]), ("qwen3:8b", [50, 53, 34])]:
            self.assertEqual([report["models"][model][axis]["correct"] for axis in ("issue_type", "department", "priority")], expected)

    def test_macro_f1_includes_labels_seen_only_in_predictions(self):
        result = label_metrics(["A", "B"], ["A", "C"])
        self.assertEqual(result["accuracy"], 0.5)
        self.assertAlmostEqual(result["macro_f1"], 1 / 3)
        with self.assertRaises(ValueError):
            label_metrics([], [])

    def test_duplicate_missing_or_relabelled_cases_are_rejected(self):
        dataset = read_rows(HERE / "campusfix_benchmark_v2.csv")
        holdout = {row["case_id"]: row for row in dataset if row["split"] == "holdout"}
        original = read_rows(HERE / "frozen/deepseek-r1_8b_holdout_predictions.csv")
        duplicate = copy.deepcopy(original)
        duplicate[-1] = duplicate[0]
        relabelled = copy.deepcopy(original)
        relabelled[0]["expected_priority"] = "CHANGED"
        for rows in (duplicate, original[:-1], relabelled):
            with self.subTest(rows=len(rows)):
                with self.assertRaises(ValueError):
                    validate_predictions(rows, holdout)


if __name__ == "__main__":
    unittest.main()
