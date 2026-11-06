"""Policy v3 consistency checks and v3-migration gate tests. Tracked files only; no models."""
from __future__ import annotations

import copy
import csv
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import analyze_development as ad  # noqa: E402
import build_v3_candidate as b  # noqa: E402
import policy_v3 as pol  # noqa: E402

POLICY_DOC = HERE / "CLASSIFICATION_POLICY_v3.md"
MIGRATION_DOC = HERE / "v3_proposal" / "MIGRATION_PROPOSAL.md"

# The 30 CHANGE/ADJUDICATE cases from LABEL_REVIEW.md.
FLAGGED_30 = {
    "CFB-0021", "CFB-0233", "CFB-0033", "CFB-0111", "CFB-0158", "CFB-0248", "CFB-0063", "CFB-0065",
    "CFB-0100", "CFB-0130", "CFB-0172", "CFB-0235", "CFB-0184", "CFB-0190", "CFB-0246", "CFB-0017",
    "CFB-0029", "CFB-0119", "CFB-0167", "CFB-0205", "CFB-0207", "CFB-0203", "CFB-0112", "CFB-0154",
    "CFB-0142", "CFB-0136", "CFB-0236", "CFB-0192", "CFB-0178", "CFB-0118",
}
# Lint findings on v2 (regression snapshot: update policy docs if this changes).
V2_LINT_SNAPSHOT = {
    ("CFB-0021", "D8"), ("CFB-0057", "D5"), ("CFB-0059", "D5"), ("CFB-0063", "D9"), ("CFB-0065", "D9"),
    ("CFB-0100", "D1"), ("CFB-0112", "D4"), ("CFB-0118", "D6"), ("CFB-0119", "D5"), ("CFB-0142", "D4"),
    ("CFB-0154", "D4"), ("CFB-0178", "D6"), ("CFB-0205", "D2"), ("CFB-0205", "D4"), ("CFB-0207", "D2"),
    ("CFB-0214", "D5"), ("CFB-0236", "D3"),
}


def row(**overrides):
    base = {"case_id": "T1", "text": "sink leaking", "expected_issue_type": "Plumbing",
            "expected_department": "Facilities", "expected_priority": "MEDIUM", "expected_action": "AUTO_ROUTE"}
    base.update(overrides)
    return base


def rules(findings):
    return {f.rule for f in findings}


class HardRuleTests(unittest.TestCase):
    def test_valid_row_passes(self):
        self.assertEqual(pol.hard_violations(row()), [])

    def test_taxonomy_and_flag_values(self):
        self.assertIn("R-SCH-1", rules(pol.hard_violations(row(expected_issue_type="HVAC"))))
        self.assertIn("R-SCH-1", rules(pol.hard_violations(row(expected_priority="CRITICAL"))))
        self.assertIn("R-SCH-1", rules(pol.hard_violations(row(safety_escalation="yes"))))

    def test_review_priority_and_action_coincide(self):
        self.assertIn("R-REV-1", rules(pol.hard_violations(row(expected_priority="NEEDS_REVIEW"))))

    def test_department_exceptions(self):
        self.assertIn("R-DEP-1", rules(pol.hard_violations(row(expected_department="IT"))))
        lockout = row(expected_issue_type="Door / Lock", expected_department="Residence Life", expected_priority="URGENT")
        self.assertEqual(pol.hard_violations(lockout), [])
        hazard = row(expected_issue_type="Vending", expected_department="Facilities", expected_priority="URGENT")
        self.assertTrue({"R-DEP-1", "R-DEP-5"} <= rules(pol.hard_violations(hazard)))
        self.assertEqual(pol.hard_violations(dict(hazard, safety_escalation="true")), [])

    def test_safety_requires_urgent(self):
        self.assertIn("R-SAF-1", rules(pol.hard_violations(row(safety_escalation="true"))))

    def test_secondary_issue_rules(self):
        ok = row(expected_priority="URGENT", safety_escalation="true", secondary_issue_type="Wi-Fi / Network")
        self.assertEqual(pol.hard_violations(ok), [])
        for bad in (dict(ok, secondary_issue_type="Plumbing"), dict(ok, secondary_issue_type="Other"),
                    dict(ok, secondary_issue_type="Nonsense"), dict(ok, safety_escalation="")):
            with self.subTest(bad=bad):
                self.assertIn("R-SAF-2", rules(pol.hard_violations(bad)))

    def test_review_reason_rules(self):
        review = row(expected_issue_type="Other", expected_department="Other / Review",
                     expected_priority="NEEDS_REVIEW", expected_action="NEEDS_REVIEW")
        self.assertEqual(pol.hard_violations(review), [])  # v2 rows: column absent, not enforced
        self.assertIn("R-REV-2", rules(pol.hard_violations(dict(review, review_reason=""))))
        self.assertEqual(pol.hard_violations(dict(review, review_reason="multiple_issues")), [])
        self.assertIn("R-REV-2", rules(pol.hard_violations(row(review_reason="multiple_issues"))))
        vague = dict(review, expected_issue_type="Elevator", expected_department="Facilities")
        self.assertEqual(pol.hard_violations(dict(vague, review_reason="insufficient_detail")), [])
        self.assertIn("R-REV-3", rules(pol.hard_violations(dict(vague, review_reason="multiple_issues"))))
        self.assertIn("R-REV-4", rules(pol.hard_violations(dict(review, review_reason="insufficient_detail"))))

    def test_accessibility_requires_ordinal_priority(self):
        bad = row(expected_priority="NEEDS_REVIEW", expected_action="NEEDS_REVIEW", accessibility_escalation="true")
        self.assertIn("R-ACC-1", rules(pol.hard_violations(bad)))
        self.assertEqual(pol.hard_violations(row(expected_priority="HIGH", accessibility_escalation="true")), [])


class LintRuleTests(unittest.TestCase):
    def lint(self, text, **kw):
        return rules(pol.lint_warnings(row(text=text, **kw)))

    def test_d1_scope_rules(self):
        self.assertIn("D1", self.lint("Meal swipes are down for every resident at dinner now", expected_priority="HIGH"))
        self.assertIn("D1", self.lint("Printing is unavailable campus-wide this week", expected_priority="URGENT"))
        self.assertNotIn("D1", self.lint("Printing is unavailable campus-wide this week", expected_priority="HIGH"))

    def test_d2_d4_d6(self):
        review = dict(expected_issue_type="Other", expected_department="Other / Review",
                      expected_priority="NEEDS_REVIEW", expected_action="NEEDS_REVIEW")
        self.assertIn("D2", self.lint("Sink flooding and my login fails", **review))
        self.assertNotIn("D2", self.lint("Sink leaking and my login fails", **review))
        self.assertIn("D4", self.lint("There is a threat in my hall", expected_issue_type="Housing / Roommate",
                                      expected_department="Residence Life"))
        self.assertIn("D6", self.lint("The washer is sparking", expected_issue_type="Laundry",
                                      expected_department="Residence Life"))
        self.assertNotIn("D6", self.lint("The washer is sparking", expected_issue_type="Laundry",
                                         expected_department="Facilities"))

    def test_d3_d5_d8_d9(self):
        self.assertIn("D3", self.lint("my dorm desk is broken", expected_issue_type="Furniture"))
        self.assertNotIn("D3", self.lint("classroom desk is broken", expected_issue_type="Furniture"))
        self.assertIn("D5", self.lint("printer jams again", expected_priority="HIGH", expected_issue_type="Printing",
                                      expected_department="IT"))
        self.assertNotIn("D5", self.lint("printer jams again and the room is unusable", expected_priority="HIGH"))
        self.assertNotIn("D5", self.lint("someone keeps trying doors", expected_priority="HIGH",
                                         expected_issue_type="Security Concern", expected_department="Campus Safety"))
        self.assertIn("D8", self.lint("stair lights out", expected_issue_type="Lighting", expected_priority="HIGH"))
        self.assertIn("D9", self.lint("wheelchair user cannot get up", expected_priority="HIGH"))
        self.assertNotIn("D9", self.lint("wheelchair user cannot get up", expected_priority="HIGH",
                                         accessibility_escalation="true"))


class V2AndProposalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v2_hash = hashlib.sha256(ad.DATASET.read_bytes()).hexdigest()
        cls.dev, cls.holdout_ids, cls.proposals, cls.exceptions = b.load_all()
        cls.preview = b.apply(cls.dev, cls.proposals, frozenset({"PENDING", "APPROVED"}))

    def test_v2_benchmark_and_frozen_artifacts_untouched(self):
        manifest = json.loads(ad.FROZEN_MANIFEST.read_text(encoding="utf-8"))
        for relative, digest in manifest.items():
            self.assertEqual(hashlib.sha256((ad.EVAL_DIR / relative).read_bytes()).hexdigest(), digest, relative)
        self.assertEqual(self.v2_hash, manifest["campusfix_benchmark_v2.csv"])
        self.assertFalse(b.FINAL_V3.exists(), "benchmark v3 must not be finalized before approval")

    def test_v2_passes_hard_checks_and_lint_snapshot(self):
        hard, lint = pol.check_rows(self.dev.values())
        self.assertEqual(hard, [])
        self.assertEqual({(f.case_id, f.rule) for f in lint}, V2_LINT_SNAPSHOT)

    def test_all_flagged_cases_have_exactly_one_decision(self):
        flagged = {p["case_id"] for p in self.proposals if p["flagged"] == "yes"}
        self.assertEqual(flagged, FLAGGED_30)

    def test_preview_satisfies_policy(self):
        result = b.check_candidate(self.preview, self.exceptions)
        self.assertEqual(result["hard"], [])
        self.assertEqual(result["unexplained_lint"], [])
        self.assertEqual(result["stale_exceptions"], [])

    def test_preview_scope_and_text_preserved(self):
        self.assertEqual(len(self.preview), 187)
        self.assertFalse({r["case_id"] for r in self.preview} & self.holdout_ids)
        for r in self.preview:
            self.assertEqual(r["text"], self.dev[r["case_id"]]["text"])
            self.assertEqual(r["policy_version"], pol.POLICY_VERSION)

    def test_changelog_is_generated_and_matches_v2(self):
        committed = b.CHANGELOG.read_text(encoding="utf-8")
        self.assertEqual(committed, b.render_changelog(b.changelog(self.dev, self.proposals)))
        for entry in csv.DictReader(committed.splitlines()):
            if entry["change_type"] in ("label", "metadata"):
                self.assertEqual(entry["v2_value"], self.dev[entry["case_id"]][entry["field"]])
                self.assertNotEqual(entry["v2_value"], entry["proposed_v3_value"])

    def test_docs_define_every_rule_and_unresolved_id(self):
        policy = POLICY_DOC.read_text(encoding="utf-8")
        migration = MIGRATION_DOC.read_text(encoding="utf-8")
        self.assertIn(pol.POLICY_VERSION, policy)
        for rule_id in pol.RULE_IDS:
            self.assertRegex(policy, rf"(?<![\w-]){re.escape(rule_id)}(?![\w-])", rule_id)
        for uid in pol.UNRESOLVED_IDS:
            self.assertIn(f"**{uid}**", migration, uid)
        used = {p["unresolved_ref"] for p in self.proposals if p["unresolved_ref"]}
        used |= {e["unresolved_ref"] for e in self.exceptions}
        self.assertTrue(used <= pol.UNRESOLVED_IDS)

    def test_every_label_change_in_migration_table(self):
        migration = MIGRATION_DOC.read_text(encoding="utf-8")
        for p in self.proposals:
            if p["decision"] == "CHANGE":
                self.assertRegex(migration, rf"\| {p['case_id']} \| (issue|dept|priority)", p["case_id"])


class ProposalValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dev, cls.holdout_ids, cls.proposals, cls.exceptions = b.load_all()

    def assertInvalid(self, mutate, error=b.ProposalError):
        proposals = copy.deepcopy(self.proposals)
        mutate(proposals)
        with self.assertRaises(error):
            b.validate_proposals(proposals, self.dev, self.holdout_ids)

    def test_rejects_malformed_proposals(self):
        first_change = next(i for i, p in enumerate(self.proposals) if p["decision"] == "CHANGE")
        keep = next(i for i, p in enumerate(self.proposals) if p["decision"] == "KEEP")
        cases = {
            "duplicate": lambda ps: ps.append(dict(ps[0])),
            "unknown case": lambda ps: ps[0].update(case_id="CFB-9999"),
            "unknown rule": lambda ps: ps[0].update(policy_refs="D10"),
            "no justification": lambda ps: ps[0].update(justification=" "),
            "bad approval": lambda ps: ps[0].update(approval="MAYBE"),
            "change repeats v2": lambda ps: ps[first_change].update(
                proposed_priority=self.dev[ps[first_change]["case_id"]]["expected_priority"],
                proposed_issue_type="", proposed_department="", proposed_action=""),
            "keep with label": lambda ps: ps[keep].update(proposed_priority="LOW"),
            "keep with annotation": lambda ps: ps[keep].update(safety_escalation="true"),
        }
        for name, mutate in cases.items():
            with self.subTest(name):
                self.assertInvalid(mutate)

    def test_rejects_holdout_reference(self):
        holdout_id = sorted(self.holdout_ids)[0]
        self.assertInvalid(lambda ps: ps[0].update(case_id=holdout_id), ad.HoldoutLeakError)


class BuildGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dev, _, cls.proposals, cls.exceptions = b.load_all()

    def approved(self, proposals):
        out = copy.deepcopy(proposals)
        for p in out:
            p["approval"] = "APPROVED"
        return out

    def test_refuses_without_approvals(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(b.ProposalError, "No approved proposals"):
                b.write_candidate(Path(tmp) / "v3.csv", self.proposals, self.exceptions, self.dev)
            self.assertFalse((Path(tmp) / "v3.csv").exists())

    def test_partial_approval_fails_hard_checks(self):
        partial = copy.deepcopy(self.proposals)
        next(p for p in partial if p["case_id"] == "CFB-0158")["approval"] = "APPROVED"
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(b.ProposalError, "R-REV-2"):
            b.write_candidate(Path(tmp) / "v3.csv", partial, self.exceptions, self.dev)

    def test_unapproved_lint_exceptions_block_write(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(b.ProposalError, "unexplained lint"):
            b.write_candidate(Path(tmp) / "v3.csv", self.approved(self.proposals), self.exceptions, self.dev)

    def test_full_approval_writes_valid_candidate_to_temp(self):
        exceptions = [dict(e, approval="APPROVED") for e in self.exceptions]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "v3.csv"
            rows = b.write_candidate(out, self.approved(self.proposals), exceptions, self.dev)
            written = b.read_csv(out)
        self.assertEqual(len(written), 187)
        self.assertEqual(list(written[0]), b.V3_COLUMNS)
        by_id = {r["case_id"]: r for r in written}
        self.assertEqual(by_id["CFB-0112"]["expected_department"], "Campus Safety")
        self.assertEqual(by_id["CFB-0205"]["secondary_issue_type"], "Laundry")
        self.assertEqual(by_id["CFB-0001"]["v2_changed_fields"], "")
        self.assertEqual(sum(1 for r in rows if r["human_review_status"] == "policy_v3_approved"), len(self.proposals))

    def test_protected_targets_refused(self):
        for target in (ad.DATASET, ad.FROZEN_DIR / "x.csv", ad.REPO_ROOT / "backend" / "x.csv"):
            with self.subTest(target=target), self.assertRaises(b.ProposalError):
                b.guard_target(target)

    def test_final_name_requires_every_decision(self):
        approved = self.approved(self.proposals)
        approved[0]["approval"] = "PENDING"
        exceptions = [dict(e, approval="APPROVED") for e in self.exceptions]
        if b.FINAL_V3.exists():
            self.skipTest("final v3 already exists")
        with self.assertRaisesRegex(b.ProposalError, "every proposal decided"):
            b.write_candidate(b.FINAL_V3, approved, exceptions, self.dev)
        self.assertFalse(b.FINAL_V3.exists())


if __name__ == "__main__":
    unittest.main()
