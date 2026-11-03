#!/usr/bin/env python3
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent
SCRIPT = ROOT / ".codex/skills/kernel-backport-difficulty/scripts/score_backport.py"
SPEC = importlib.util.spec_from_file_location("score_backport", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ScoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.trivial_path = FIXTURES / "trivial.patch"
        cls.complex_path = FIXTURES / "complex.patch"
        cls.trivial = MODULE.parse_patch(cls.trivial_path)
        cls.complex = MODULE.parse_patch(cls.complex_path)
        cls.evidence = json.loads((FIXTURES / "complex-evidence.json").read_text(encoding="utf-8"))

    def report(self, patch=None, evidence=None, path=None):
        return MODULE.build_report(path or self.complex_path, patch or self.complex, evidence or {})

    def test_patch_parser_counts_only_hunks(self):
        self.assertEqual(self.complex["changed_hunks"], 3)
        self.assertEqual(self.complex["additions"], 11)
        self.assertEqual(self.complex["deletions"], 6)
        self.assertEqual(len(self.complex["files"]), 3)

    def test_unknown_kabi_is_not_zero(self):
        report = self.report(self.trivial, {}, self.trivial_path)
        self.assertEqual(report["dimensions"]["kabi"]["status"], "unknown")
        self.assertEqual(report["dimensions"]["kabi"]["score"], 50)
        self.assertEqual(report["top_risks"], [])

    def test_complete_complex_score_reconciles(self):
        report = self.report(evidence=self.evidence)
        self.assertEqual(report["total_score"], 84.6)
        self.assertEqual(report["total_score"], round(sum(item["weighted_contribution"] for item in report["dimensions"].values()), 1))
        self.assertFalse(report["provisional"])
        self.assertEqual(report["dimensions"]["subsystem"]["status"], "manual")

    def test_partial_dimension_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "api_adaptations"):
            self.report(evidence={"semantic": {"dependency_commits": 1}})

    def test_null_dimension_value_is_rejected(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["kabi"]["layout_changes"] = None
        with self.assertRaises(ValueError):
            self.report(evidence=evidence)

    def test_zero_conflict_requires_success(self):
        evidence = {"conflict": {"apply_attempted": 1, "apply_succeeded": 0, "conflicted_hunks": 0, "conflicted_files": 0, "conflict_lines": 0, "source": "test"}}
        with self.assertRaisesRegex(ValueError, "零冲突"):
            self.report(evidence=evidence)
        evidence["conflict"]["apply_succeeded"] = 1
        report = self.report(evidence=evidence)
        self.assertEqual(report["dimensions"]["conflict"]["score"], 0)

    def test_conflict_consistency_is_validated(self):
        evidence = copy.deepcopy(self.evidence)
        evidence["conflict"]["conflicted_hunks"] = 4
        with self.assertRaisesRegex(ValueError, "不能超过"):
            self.report(evidence=evidence)

    def test_export_attribute_change_is_not_add_plus_remove(self):
        signals = MODULE.automatic_signals(self.complex)[1]
        self.assertEqual(signals["added_exports"], [])
        self.assertEqual(signals["removed_exports"], [])
        self.assertEqual(signals["export_attribute_changes"], ["example_lookup"])

    def test_irq_signal_detected(self):
        signals = MODULE.automatic_signals(self.complex)[0]
        self.assertEqual(signals["interrupt_context_changes"], 1)

    def test_override_requires_reason(self):
        with self.assertRaisesRegex(ValueError, "override_reasons"):
            self.report(evidence={"overrides": {"subsystem": 80}})

    def test_notes_must_be_string_array(self):
        with self.assertRaisesRegex(ValueError, "notes"):
            self.report(evidence={"notes": "bad"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
