"""CampusFix Claude Vision evaluation runner.

Modes (exactly one):
  --mock {oracle,abstain,overconfident}   offline; measures the harness, never Claude
  --estimate                              preflight + token/cost estimate; no API calls
  --live                                  real Claude API calls; requires approval gates

Live gates: --live, environment CAMPUSFIX_VISION_LIVE_APPROVED=yes, and --budget-usd.
The run refuses to start if the estimate exceeds the budget and stops once recorded
spend reaches it.

Examples (repository root):
  python evaluation/vision/synthetic_fixtures.py OUT_DIR
  python evaluation/vision/run_vision_eval.py --cases OUT_DIR/cases.jsonl --mock oracle --out RESULTS_DIR
  python evaluation/vision/run_vision_eval.py --cases evaluation/vision/cases/live_cases.jsonl --estimate
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import claude_client as cc  # noqa: E402
import grader  # noqa: E402
import mock_clients  # noqa: E402
import preflight  # noqa: E402
from prompts import PROMPT_VERSION, SYSTEM_PROMPT  # noqa: E402
import vision_schema  # noqa: E402

REPO_ROOT = HERE.parent.parent
APPROVAL_ENV = "CAMPUSFIX_VISION_LIVE_APPROVED"
PROTECTED = [REPO_ROOT / "backend", REPO_ROOT / "evaluation" / "frozen", REPO_ROOT / "evaluation" / "dev_audit",
             REPO_ROOT / "frontend", REPO_ROOT / "supabase"]
EST_PROMPT_TOKENS = 1800   # system prompt + schema + task text, rough; verify with count_tokens
EST_OUTPUT_TOKENS = 2500   # thinking + JSON; rough upper-middle estimate


class RunError(RuntimeError):
    pass


def load_cases(path: Path) -> list[dict]:
    cases, seen = [], set()
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("//"):
            continue
        case = json.loads(line)
        grader.validate_case(case)
        if case["case_id"] in seen:
            raise grader.CaseError(f"duplicate case_id {case['case_id']} (line {line_no})")
        seen.add(case["case_id"])
        for img in case["images"]:
            img["_resolved"] = str((path.parent / img["path"]).resolve())
        cases.append(case)
    return sorted(cases, key=lambda c: c["case_id"])


def missing_images(cases: list[dict]) -> list[str]:
    return [f"{c['case_id']}: {img['path']}" for c in cases for img in c["images"] if not Path(img["_resolved"]).exists()]


def preflight_case(case: dict) -> list[preflight.ImageCheck]:
    n = len(case["images"])
    return [preflight.check_image(Path(img["_resolved"]), n) for img in case["images"]]


def estimate(cases: list[dict], model: str, reps: int) -> dict:
    tier = preflight.tier_for(model)
    image_tokens, rejected = 0, []
    for case in cases:
        for check in preflight_case(case):
            if check.ok:
                image_tokens += preflight.visual_tokens(check.width, check.height, tier)[0]
            else:
                rejected.append(f"{case['case_id']}: {check.rejection}")
    runnable = len(cases) - len({r.split(":")[0] for r in rejected})
    input_tokens = (image_tokens + EST_PROMPT_TOKENS * runnable) * reps
    output_tokens = EST_OUTPUT_TOKENS * runnable * reps
    cost = cc.cost_usd({"input_tokens": input_tokens, "output_tokens": output_tokens}, model)
    return {"model": model, "tier": tier, "cases": len(cases), "runnable_cases": runnable, "reps": reps,
            "image_tokens_per_rep": image_tokens, "est_input_tokens": input_tokens,
            "est_output_tokens": output_tokens, "est_cost_usd": cost, "preflight_rejections": rejected,
            "note": "Estimate only: prompt and output tokens are assumptions; use count_tokens for input."}


def guard_out_dir(out: Path, mode: str) -> None:
    resolved = out.resolve()
    for p in PROTECTED:
        if resolved == p.resolve() or p.resolve() in resolved.parents:
            raise RunError(f"Refusing to write results under protected path {p}")
    if mode != "live" and "live" in {part.lower() for part in resolved.parts}:
        raise RunError("Mock results may not be written to a 'live' directory")
    if resolved.exists() and any(resolved.iterdir()):
        raise RunError(f"Output directory {out} is not empty; results are never overwritten")


def _redact(kwargs: dict) -> dict:
    """Trajectory copy of the request with base64 image data replaced by its hash."""
    clone = json.loads(json.dumps(kwargs))
    clone["system"] = [{"type": "text", "text": f"<{PROMPT_VERSION}>", "cache_control": {"type": "ephemeral"}}]
    for block in clone["messages"][0]["content"]:
        if block["type"] == "image":
            data = block["source"].pop("data")
            block["source"]["sha256"] = hashlib.sha256(data.encode()).hexdigest()
            block["source"]["base64_bytes"] = len(data)
    clone["output_config"]["format"] = {"type": "json_schema", "schema": "<vision_result.schema.json>"}
    return clone


def run(cases: list[dict], client, out: Path, mode: str, model: str = cc.DEFAULT_MODEL,
        effort: str = cc.DEFAULT_EFFORT, reps: int = 1, budget_usd: float | None = None,
        sleep=time.sleep) -> dict:
    guard_out_dir(out, mode)
    missing = missing_images(cases)
    if missing:
        raise RunError(f"{len(missing)} image files missing, e.g. {missing[:3]}")
    out.mkdir(parents=True, exist_ok=True)
    schema = vision_schema.load_schema()
    by_id = {c["case_id"]: c for c in cases}
    results, errors, spent = [], [], 0.0
    with (out / "results.jsonl").open("w", encoding="utf-8") as res_f, \
            (out / "errors.jsonl").open("w", encoding="utf-8") as err_f, \
            (out / "trajectories.jsonl").open("w", encoding="utf-8") as traj_f:
        for rep in range(reps):
            for case in cases:
                if budget_usd is not None and spent >= budget_usd:
                    raise RunError(f"Budget reached (${spent:.4f} of ${budget_usd}); stopping")
                checks = preflight_case(case)
                row = {"case_id": case["case_id"], "rep": rep, "category": case["category"],
                       "preflight": [{k: v for k, v in vars(c).items() if k != "path"} for c in checks]}
                bad = next((c for c in checks if not c.ok), None)
                if bad:
                    row.update(status="input_rejected", rejection=bad.rejection,
                               routing={"action": "MANUAL_REVIEW", "safety_escalation": False,
                                        "reason": f"input_{bad.rejection}"})
                    results.append(row)
                    res_f.write(json.dumps(row) + "\n")
                    continue
                request = cc.VisionRequest(
                    case_id=case["case_id"], model=model, effort=effort, ticket_text=case.get("ticket_text"),
                    images=[(c.media_type, preflight.encode(Path(img["_resolved"])))
                            for c, img in zip(checks, case["images"])])
                started = time.perf_counter()
                try:
                    response, attempts = cc.call_with_retries(lambda: client.analyze(request), sleep=sleep)
                except cc.VisionAPIError as error:
                    err = {"case_id": case["case_id"], "rep": rep, "kind": error.kind, "message": str(error)[:500],
                           "attempts": getattr(error, "attempts", 1),
                           "routing": cc.routing_decision("infra_error", None)}
                    errors.append(err)
                    err_f.write(json.dumps(err) + "\n")
                    if error.kind in ("authentication", "permission"):
                        raise RunError(f"Systematic API failure ({error.kind}); aborting run") from error
                    continue
                latency = time.perf_counter() - started
                interp = cc.interpret_response(response, model, len(checks), schema)
                row_cost = cc.cost_usd(interp.usage, model)
                spent += row_cost or 0.0
                row.update(status=interp.status, result=interp.result, errors=interp.errors,
                           stop_reason=interp.stop_reason, served_model=interp.served_model, usage=interp.usage,
                           cost_usd=row_cost, attempts=attempts, latency_seconds=latency,
                           request_id=interp.request_id, routing=cc.routing_decision(interp.status, interp.result))
                results.append(row)
                res_f.write(json.dumps(row) + "\n")
                traj_f.write(json.dumps({"case_id": case["case_id"], "rep": rep,
                                         "request": _redact(cc.build_request_kwargs(request, schema)),
                                         "raw_text": interp.raw_text}) + "\n")

    grades = [grader.grade(by_id[r["case_id"]], r) for r in results]
    metrics = grader.aggregate(grades, errors, mode)
    metrics["config"] = {
        "mode": mode, "model": model, "effort": effort, "reps": reps, "prompt_version": PROMPT_VERSION,
        "system_prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
        "schema_sha256": hashlib.sha256(vision_schema.SCHEMA_PATH.read_bytes()).hexdigest(),
        "cases": len(cases), "recorded_cost_usd": round(spent, 6),
    }
    (out / "grades.jsonl").write_text("".join(json.dumps(g) + "\n" for g in grades), encoding="utf-8")
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "report.md").write_text(render_report(metrics), encoding="utf-8")
    return metrics


def _fmt(r: dict) -> str:
    if not r or not r.get("n"):
        return "n/a"
    lo, hi = r["ci95"]
    return f"{100 * r['rate']:.1f}% ({r['k']}/{r['n']}; 95% CI {100 * lo:.0f}-{100 * hi:.0f}%)"


def render_report(m: dict) -> str:
    banner = ("**LIVE Claude evaluation.**" if m["is_model_evaluation"] else
              f"**{m['mode'].upper()} RUN: these numbers measure the harness, not Claude. Do not cite them as model accuracy.**")
    keys = ["valid_output", "outcome_correct", "issue_correct", "coverage_on_answerable", "selective_issue_accuracy",
            "any_hallucination", "confident_misidentification", "fabricated_text", "hazard_false_alarm",
            "safety_miss", "consistency_correct", "safe_input_rejection_correct", "routed_to_human_on_failure",
            "refusal", "truncated"]
    lines = ["# CampusFix vision evaluation report", "", banner, "",
             f"Config: `{json.dumps(m['config'], sort_keys=True)}`", "", "## Overall", "",
             "| Metric | Value |", "|---|---|"]
    lines += [f"| {k} | {_fmt(m['overall'][k])} |" for k in keys]
    ab = m["overall"]["abstention"]
    lines += [f"| abstention precision / recall | {ab['precision']} / {ab['recall']} (tp={ab['tp']}, fp={ab['fp']}, fn={ab['fn']}) |",
              "", f"Status counts: `{m['overall']['status_counts']}`  ", f"Infrastructure errors (not scored): `{m['infra_errors']}`",
              "", "## By category", "", "| Category | rows | outcome correct | hallucination | safety miss |", "|---|---|---|---|---|"]
    for cat, b in m["by_category"].items():
        lines.append(f"| {cat} | {b['rows']} | {_fmt(b['outcome_correct'])} | {_fmt(b['any_hallucination'])} | {_fmt(b['safety_miss'])} |")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--cases", type=Path, required=True)
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", choices=["oracle", "abstain", "overconfident"])
    mode.add_argument("--estimate", action="store_true")
    mode.add_argument("--live", action="store_true")
    p.add_argument("--out", type=Path)
    p.add_argument("--model", default=cc.DEFAULT_MODEL)
    p.add_argument("--effort", default=cc.DEFAULT_EFFORT, choices=["low", "medium", "high", "xhigh", "max"])
    p.add_argument("--reps", type=int, default=1)
    p.add_argument("--budget-usd", type=float)
    p.add_argument("--split", choices=["dev", "holdout"], default="dev",
                   help="Which split to run. Holdout requires --allow-holdout (use once, at the end).")
    p.add_argument("--allow-holdout", action="store_true")
    p.add_argument("--fallbacks", action="store_true",
                   help="Enable server-side refusal fallbacks. Fallback-served rows are recorded as model_mismatch.")
    args = p.parse_args(argv)

    cases = [c for c in load_cases(args.cases) if c["split"] == args.split]
    if args.split == "holdout" and not args.allow_holdout:
        raise RunError("Holdout cases are reserved for a single final evaluation; pass --allow-holdout deliberately")
    if args.estimate:
        missing = missing_images(cases)
        report = {"missing_images": missing}
        if not missing:
            report.update(estimate(cases, args.model, args.reps))
        print(json.dumps(report, indent=2))
        return 0
    if args.out is None:
        raise RunError("--out is required for --mock and --live")
    if args.mock:
        clients = {"oracle": mock_clients.OracleClient, "abstain": mock_clients.AlwaysAbstainClient,
                   "overconfident": mock_clients.OverconfidentClient}
        client = clients[args.mock]({c["case_id"]: c for c in cases})
        metrics = run(cases, client, args.out, f"mock-{args.mock}", args.model, args.effort, args.reps, sleep=lambda s: None)
    else:
        if os.environ.get(APPROVAL_ENV) != "yes":
            raise RunError(f"Live run not approved: set {APPROVAL_ENV}=yes after approving the cost estimate")
        if args.budget_usd is None:
            raise RunError("--budget-usd is required for live runs")
        missing = missing_images(cases)
        if missing:
            raise RunError(f"{len(missing)} image files missing, e.g. {missing[:3]}")
        est = estimate(cases, args.model, args.reps)
        if est["est_cost_usd"] is None or est["est_cost_usd"] > args.budget_usd:
            raise RunError(f"Estimated ${est['est_cost_usd']} exceeds budget ${args.budget_usd}")
        client = cc.LiveClaudeVisionClient(vision_schema.load_schema(), fallbacks=args.fallbacks)
        metrics = run(cases, client, args.out, "live", args.model, args.effort, args.reps, args.budget_usd)
    print(render_report(metrics))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RunError, grader.CaseError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(2)
