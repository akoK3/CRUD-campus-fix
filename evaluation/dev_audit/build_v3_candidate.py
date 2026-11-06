"""Build a benchmark v3 *candidate* from v2 plus reviewed proposals. Never edits v2.

Gates (all enforced in code):

* Proposals are validated against the v2 development split, the taxonomy, and the
  policy rule IDs before anything is applied.
* Writing a candidate uses only rows whose ``approval`` is APPROVED. It refuses when
  nothing is approved, when the result fails any hard policy check, when the target
  exists, or when the target is protected (v2, the frozen archive, ``backend/``).
* ``evaluation/campusfix_benchmark_v3.csv`` (the final name) may only be written once
  every proposal and lint exception has been decided (APPROVED or REJECTED).
* The candidate is development-only. The frozen 62-case holdout is never read for
  content and is not relabeled.

Usage (repository root)::

    python evaluation/dev_audit/build_v3_candidate.py --preview          # apply all PENDING, check, print
    python evaluation/dev_audit/build_v3_candidate.py --write-changelog  # regenerate the changelog
    python evaluation/dev_audit/build_v3_candidate.py --out PATH         # approved rows only
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter
from pathlib import Path
import sys
from typing import Iterable, Mapping, Sequence

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import analyze_development as ad  # noqa: E402
import policy_v3 as pol  # noqa: E402

PROPOSAL_DIR = HERE / "v3_proposal"
PROPOSALS = PROPOSAL_DIR / "proposed_v3_labels.csv"
LINT_EXCEPTIONS = PROPOSAL_DIR / "lint_exceptions.csv"
CHANGELOG = PROPOSAL_DIR / "changelog_v2_to_v3.csv"
FINAL_V3 = ad.EVAL_DIR / "campusfix_benchmark_v3.csv"

LABEL_FIELDS = {
    "proposed_issue_type": "expected_issue_type",
    "proposed_department": "expected_department",
    "proposed_priority": "expected_priority",
    "proposed_action": "expected_action",
}
ANNOTATION_FIELDS = ("secondary_issue_type", "safety_escalation", "accessibility_escalation", "review_reason")
METADATA_FIELDS = {"proposed_noise_type": "noise_type"}
DECISIONS = {"CHANGE", "KEEP", "ANNOTATE", "UNRESOLVED"}
APPROVALS = {"PENDING", "APPROVED", "REJECTED"}
CONFIDENCE = {"High", "Medium", "Low"}
V3_COLUMNS = [
    "case_id", "split", "text", "expected_issue_type", "expected_department", "expected_priority",
    "expected_action", "secondary_issue_type", "safety_escalation", "accessibility_escalation", "review_reason",
    "difficulty", "noise_type", "label_reason", "source_basis", "human_review_status",
    "policy_version", "policy_refs", "v2_changed_fields",
]


class ProposalError(ValueError):
    pass


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def validate_proposals(proposals: Sequence[Mapping[str, str]], dev: Mapping[str, Mapping[str, str]],
                       holdout_ids: Iterable[str]) -> None:
    ids = [p["case_id"] for p in proposals]
    if len(ids) != len(set(ids)):
        raise ProposalError("duplicate case_id in proposals")
    if set(ids) & set(holdout_ids):
        raise ad.HoldoutLeakError("proposals reference holdout cases")
    for p in proposals:
        cid = p["case_id"]
        if cid not in dev:
            raise ProposalError(f"{cid}: not a development case")
        v2 = dev[cid]
        if p["decision"] not in DECISIONS or p["approval"] not in APPROVALS or p["confidence"] not in CONFIDENCE:
            raise ProposalError(f"{cid}: bad decision/approval/confidence")
        if p["flagged"] not in ("yes", "no"):
            raise ProposalError(f"{cid}: flagged must be yes/no")
        if not p["justification"].strip():
            raise ProposalError(f"{cid}: justification required")
        refs = [r for r in p["policy_refs"].split(";") if r]
        if not refs or any(r not in pol.RULE_IDS for r in refs):
            raise ProposalError(f"{cid}: unknown or missing policy_refs {refs}")
        label_changes = {k: v for k, v in p.items() if k in LABEL_FIELDS and v}
        for key, value in label_changes.items():
            if value == v2[LABEL_FIELDS[key]]:
                raise ProposalError(f"{cid}: {key} repeats the v2 value")
        annotations = [k for k in ANNOTATION_FIELDS if p[k]] + [k for k in METADATA_FIELDS if p[k]]
        if p["decision"] == "CHANGE" and not label_changes:
            raise ProposalError(f"{cid}: CHANGE without a label change")
        if p["decision"] in ("KEEP", "ANNOTATE", "UNRESOLVED") and label_changes:
            raise ProposalError(f"{cid}: {p['decision']} must not change labels")
        if p["decision"] == "KEEP" and annotations:
            raise ProposalError(f"{cid}: KEEP must not add fields (use ANNOTATE)")
        if p["decision"] == "ANNOTATE" and not annotations:
            raise ProposalError(f"{cid}: ANNOTATE without annotations")
        if p["decision"] == "UNRESOLVED" and not p["unresolved_ref"]:
            raise ProposalError(f"{cid}: UNRESOLVED requires unresolved_ref")
        if p["unresolved_ref"] and p["unresolved_ref"] not in pol.UNRESOLVED_IDS:
            raise ProposalError(f"{cid}: unknown unresolved_ref {p['unresolved_ref']}")


def apply(dev: Mapping[str, Mapping[str, str]], proposals: Sequence[Mapping[str, str]],
          approvals: frozenset[str]) -> list[dict[str, str]]:
    """Return development-only v3 rows applying proposals whose approval is in ``approvals``."""
    by_id = {p["case_id"]: p for p in proposals if p["approval"] in approvals}
    rows = []
    for cid, v2 in dev.items():
        row = {c: v2.get(c, "") for c in V3_COLUMNS}
        for c in ANNOTATION_FIELDS:
            row[c] = ""
        row.update(policy_version=pol.POLICY_VERSION, policy_refs="", v2_changed_fields="")
        p = by_id.get(cid)
        if p:
            changed = []
            for key, target in {**LABEL_FIELDS, **METADATA_FIELDS}.items():
                if p[key]:
                    row[target] = p[key]
                    changed.append(target)
            for key in ANNOTATION_FIELDS:
                row[key] = p[key]
            row["policy_refs"] = p["policy_refs"]
            row["v2_changed_fields"] = ";".join(changed)
            if p["approval"] == "APPROVED":
                row["human_review_status"] = "policy_v3_approved"
        rows.append(row)
    return rows


def changelog(dev: Mapping[str, Mapping[str, str]], proposals: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    out = []
    for p in proposals:
        v2 = dev[p["case_id"]]
        entries = [(t, v2[t], p[k], "label") for k, t in LABEL_FIELDS.items() if p[k]]
        entries += [(t, v2[t], p[k], "metadata") for k, t in METADATA_FIELDS.items() if p[k]]
        entries += [(k, "(new field)", p[k], "annotation") for k in ANNOTATION_FIELDS if p[k]]
        if not entries:
            entries = [("(none)", "", "", "reviewed-no-change")]
        for field, old, new, kind in entries:
            out.append({
                "case_id": p["case_id"], "field": field, "v2_value": old, "proposed_v3_value": new,
                "change_type": kind, "decision": p["decision"], "approval": p["approval"],
                "policy_refs": p["policy_refs"], "confidence": p["confidence"],
                "unresolved_ref": p["unresolved_ref"],
            })
    return out


def check_candidate(rows: Sequence[Mapping[str, str]], exceptions: Sequence[Mapping[str, str]]) -> dict:
    hard, lint = pol.check_rows(rows)
    allowed = {(e["case_id"], e["rule"]) for e in exceptions if e["approval"] != "REJECTED"}
    found = {(f.case_id, f.rule) for f in lint}
    return {
        "hard": hard,
        "unexplained_lint": [f for f in lint if (f.case_id, f.rule) not in allowed],
        "stale_exceptions": sorted(allowed - found),
    }


def guard_target(path: Path) -> None:
    resolved = path.resolve()
    protected_files = {ad.DATASET.resolve()}
    if resolved in protected_files or resolved.exists():
        raise ProposalError(f"Refusing to overwrite {path}")
    if ad.FROZEN_DIR.resolve() in resolved.parents or (ad.REPO_ROOT / "backend").resolve() in resolved.parents:
        raise ProposalError(f"Refusing to write into a protected directory: {path}")


def write_candidate(out: Path, proposals: Sequence[Mapping[str, str]], exceptions: Sequence[Mapping[str, str]],
                    dev: Mapping[str, Mapping[str, str]]) -> list[dict[str, str]]:
    guard_target(out)
    if not any(p["approval"] == "APPROVED" for p in proposals):
        raise ProposalError("No approved proposals: benchmark v3 is not finalized")
    if out.resolve() == FINAL_V3.resolve():
        undecided = [p["case_id"] for p in proposals if p["approval"] == "PENDING"]
        undecided += [e["case_id"] for e in exceptions if e["approval"] == "PENDING"]
        if undecided:
            raise ProposalError(f"Final v3 requires every proposal decided; pending: {undecided[:8]}")
    rows = apply(dev, proposals, frozenset({"APPROVED"}))
    result = check_candidate(rows, [e for e in exceptions if e["approval"] == "APPROVED"])
    if result["hard"]:
        raise ProposalError(f"Candidate fails hard policy checks: {[str(f) for f in result['hard'][:5]]}")
    if result["unexplained_lint"]:
        raise ProposalError(f"Candidate has unexplained lint: {[str(f) for f in result['unexplained_lint'][:5]]}")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=V3_COLUMNS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    return rows


def render_changelog(rows: Sequence[Mapping[str, str]]) -> str:
    import io
    buffer = io.StringIO()
    fields = ["case_id", "field", "v2_value", "proposed_v3_value", "change_type", "decision", "approval",
              "policy_refs", "confidence", "unresolved_ref"]
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def load_all():
    dev, holdout_ids = ad.load_development()
    proposals = read_csv(PROPOSALS)
    validate_proposals(proposals, dev, holdout_ids)
    exceptions = read_csv(LINT_EXCEPTIONS)
    return dev, holdout_ids, proposals, exceptions


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--preview", action="store_true", help="Apply all non-rejected proposals in memory and check")
    parser.add_argument("--write-changelog", action="store_true")
    parser.add_argument("--out", type=Path, help="Write a candidate from APPROVED proposals only")
    args = parser.parse_args(argv)
    dev, _, proposals, exceptions = load_all()
    if args.write_changelog:
        CHANGELOG.write_text(render_changelog(changelog(dev, proposals)), encoding="utf-8", newline="")
        print(f"Wrote {CHANGELOG}")
    if args.preview:
        rows = apply(dev, proposals, frozenset({"PENDING", "APPROVED"}))
        result = check_candidate(rows, exceptions)
        v2_hard, v2_lint = pol.check_rows(dev.values())
        print(f"policy {pol.POLICY_VERSION}; proposals {len(proposals)} "
              f"({dict(Counter(p['decision'] for p in proposals))}); approvals "
              f"{dict(Counter(p['approval'] for p in proposals))}")
        print(f"v2:      hard={len(v2_hard)} lint={len(v2_lint)}")
        print(f"preview: hard={len(result['hard'])} unexplained_lint={len(result['unexplained_lint'])} "
              f"stale_exceptions={len(result['stale_exceptions'])}")
        for finding in result["hard"] + result["unexplained_lint"]:
            print("  ", finding)
        print("label changes:", sum(1 for p in proposals if p["decision"] == "CHANGE"))
    if args.out:
        rows = write_candidate(args.out, proposals, exceptions, dev)
        print(f"Wrote {len(rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
