"""Reproduce frozen holdout metrics offline. Never calls a model or writes artifacts."""
from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent
AXES = ("issue_type", "department", "priority")
MODELS = {
    "deepseek-r1:8b": "frozen/deepseek-r1_8b_holdout_predictions.csv",
    "qwen3:8b": "frozen/qwen3_8b_holdout_predictions.csv",
}


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def label_metrics(expected: list[str], predicted: list[str]) -> dict:
    if not expected or len(expected) != len(predicted):
        raise ValueError("Metrics require nonempty, equally sized label lists")
    labels = sorted(set(expected) | set(predicted))
    scores = []
    for label in labels:
        tp = sum(a == b == label for a, b in zip(expected, predicted))
        fp = sum(b == label and a != label for a, b in zip(expected, predicted))
        fn = sum(a == label and b != label for a, b in zip(expected, predicted))
        denominator = 2 * tp + fp + fn
        scores.append(2 * tp / denominator if denominator else 0.0)
    correct = sum(a == b for a, b in zip(expected, predicted))
    return {
        "correct": correct,
        "total": len(expected),
        "accuracy": correct / len(expected),
        "macro_f1": math.fsum(scores) / len(labels),
    }


def validate_predictions(rows: list[dict], holdout: dict[str, dict]) -> None:
    ids = [row["case_id"] for row in rows]
    if len(ids) != len(set(ids)) or set(ids) != set(holdout):
        raise ValueError("Predictions must contain each holdout case exactly once")
    for row in rows:
        case = holdout[row["case_id"]]
        for axis in AXES:
            if row["expected_" + axis] != case["expected_" + axis]:
                raise ValueError(f"Expected label differs from dataset: {row['case_id']} / {axis}")
            if not row["pred_" + axis]:
                raise ValueError("Prediction labels cannot be empty")
        latency = float(row["latency_seconds"])
        if not math.isfinite(latency) or latency < 0:
            raise ValueError("Recorded latency must be finite and nonnegative")


def reproduce(root: Path = HERE) -> dict:
    manifest = json.loads((root / "artifacts.sha256.json").read_text(encoding="utf-8"))
    required = {"campusfix_benchmark_v2.csv", "evaluate_holdout_models.py", *MODELS.values()}
    if set(manifest) != required:
        raise ValueError("Artifact manifest must list exactly the four frozen source artifacts")
    for relative, expected_hash in manifest.items():
        actual = hashlib.sha256((root / relative).read_bytes()).hexdigest()
        if actual != expected_hash:
            raise ValueError(f"Frozen artifact checksum mismatch: {relative}")

    dataset = read_rows(root / "campusfix_benchmark_v2.csv")
    splits = dict(Counter(row["split"] for row in dataset))
    if len(dataset) != 249 or len({row["case_id"] for row in dataset}) != 249:
        raise ValueError("The benchmark must contain 249 unique cases")
    if splits != {"development": 187, "holdout": 62}:
        raise ValueError("The frozen 187/62 split has changed")
    holdout = {row["case_id"]: row for row in dataset if row["split"] == "holdout"}
    report = {"case_count": len(dataset), "split_counts": splits, "models": {}}
    for model, relative in MODELS.items():
        rows = read_rows(root / relative)
        validate_predictions(rows, holdout)
        metrics = {
            axis: label_metrics(
                [row["expected_" + axis] for row in rows],
                [row["pred_" + axis] for row in rows],
            )
            for axis in AXES
        }
        latencies = [float(row["latency_seconds"]) for row in rows]
        metrics["latency"] = {
            "mean_seconds": math.fsum(latencies) / len(latencies),
            "median_seconds": statistics.median(latencies),
        }
        report["models"][model] = metrics
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Also compare against committed metrics.json")
    args = parser.parse_args()
    report = reproduce()
    if args.check:
        recorded = json.loads((HERE / "metrics.json").read_text(encoding="utf-8"))
        if report != recorded:
            raise ValueError("Computed metrics differ from the frozen report")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
