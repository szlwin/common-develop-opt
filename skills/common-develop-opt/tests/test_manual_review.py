"""Regression tests for guided manual Review context freshness."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from runtime.manual_review import canonical_digest, evidence_context_bindings


class ManualReviewContextDigestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.entry = {
            "evidence_id": "EVD-000001",
            "type": "flow_ref",
            "used_as_types": ["flow_ref", "schema_ref"],
            "ref": "evidence/objects/content.yaml",
            "source_ref": "../../doc/_flows/FLOW.preview.yaml",
            "capture_mode": "SNAPSHOT",
            "digest": "a" * 64,
            "revision": "REQAN-R01",
            "phase": "requirement_analysis",
            "generated_by_agent": "ProjectManagerAgent",
            "generated_at": "2026-09-15T13:40:00+08:00",
            "scope_refs": ["RC-XMOD-001", "AC-001"],
            "used_by_agents": ["ProjectManagerAgent"],
            "command_result_ref": "",
            "command_result_digest": "",
            "status": "ACTIVE",
            "superseded_by": "",
            "metadata": {},
        }

    def digest(self, entries: list[dict]) -> str:
        return canonical_digest(evidence_context_bindings(
            entries,
            criterion_ids=["RC-XMOD-001", "RC-XMOD-002"],
        ))

    def test_other_reviewer_usage_does_not_expire_prepared_context(self) -> None:
        changed = copy.deepcopy(self.entry)
        changed["used_by_agents"].append("ImpactAnalysisReviewAgent")
        changed["scope_refs"].extend([
            "RC-IMP-001",
            "ASRT-REVIEW-IMPACT",
            "AC-001",
        ])

        self.assertEqual(self.digest([self.entry]), self.digest([changed]))

    def test_current_profile_scope_change_expires_prepared_context(self) -> None:
        changed = copy.deepcopy(self.entry)
        changed["scope_refs"].append("RC-XMOD-002")

        self.assertNotEqual(self.digest([self.entry]), self.digest([changed]))

    def test_evidence_content_change_expires_prepared_context(self) -> None:
        changed = copy.deepcopy(self.entry)
        changed["digest"] = "b" * 64

        self.assertNotEqual(self.digest([self.entry]), self.digest([changed]))

    def test_evidence_registry_order_does_not_expire_prepared_context(self) -> None:
        second = copy.deepcopy(self.entry)
        second["evidence_id"] = "EVD-000002"
        second["digest"] = "c" * 64

        self.assertEqual(
            self.digest([self.entry, second]),
            self.digest([second, self.entry]),
        )


if __name__ == "__main__":
    unittest.main()
