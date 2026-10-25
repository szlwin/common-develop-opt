#!/usr/bin/env python3
"""Tests for the StageResult to StageOutcome boundary."""
from __future__ import annotations

import unittest

from _long_task import stage_result


class StageResultTests(unittest.TestCase):
    def result(self) -> dict:
        return {
            "phase": "development",
            "produced_by_agent": "DevelopAgent",
            "input_revisions": {"implementation_plan": "PLAN-R01"},
            "output_revision": "DEV-R01",
            "evidence_ids": ["EVD-000001"],
            "trace_refs": ["TR-001"],
            "changed_files": ["src/app.py"],
            "open_issue_ids": [],
            "risks": ["manual verification remains"],
            "next_action": "review DEV-R01",
            "next_agent": "SpecComplianceReviewAgent",
        }

    def test_valid_result_maps_only_runtime_fields(self) -> None:
        value = self.result()
        self.assertEqual([], stage_result.validate_stage_result(value))
        outcome = stage_result.map_to_stage_outcome(value, {
            "id": "SO-1", "status": "RUNNING", "round": "DEV-I001",
        })
        self.assertEqual(["EVD-000001"], outcome["evidence_ids"])
        self.assertEqual(["manual verification remains"], outcome["open_risks"])
        self.assertNotIn("changed_files", outcome)
        self.assertNotIn("open_issue_ids", outcome)

    def test_unknown_field_and_revision_mismatch_are_rejected(self) -> None:
        value = self.result()
        value["unexpected"] = True
        errors = stage_result.validate_stage_result(value, expected_revision="DEV-R02")
        self.assertTrue(any("unexpected" in error for error in errors))
        self.assertTrue(any("output_revision" in error for error in errors))

    def test_evidence_and_owner_mismatch_are_rejected(self) -> None:
        errors = stage_result.validate_stage_result(
            self.result(),
            expected_agent="TestAgent",
            expected_evidence_ids=["EVD-000002"],
        )
        self.assertTrue(any("produced_by_agent" in error for error in errors))
        self.assertTrue(any("evidence_ids" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
