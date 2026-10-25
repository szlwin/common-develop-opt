"""Behavior checks for the auto-remediation implementation boundary."""
from __future__ import annotations

import unittest

from document_core import auto_remediation


class AutoRemediationMigrationTests(unittest.TestCase):
    def test_new_layer_exports_policy_decision_api(self) -> None:
        for name in ("policy_payload", "classify", "diagnose_log", "production_preflight", "architecture_decision", "convergence_decision"):
            self.assertTrue(callable(getattr(auto_remediation, name, None)))

    def test_new_layer_classifies_transient_failure_without_revision_change(self) -> None:
        result = auto_remediation.classify(
            signal="ci_failure", deterministic=True, safe=True, ambiguous=False,
            transient=True, changes_code_config_or_test=False, architecture_change=False,
            same_signature_count=0, new_progress_evidence=False,
        )
        self.assertEqual("RERUN_SAME_REVISION", result["decision"])
        self.assertFalse(result["requiresNewExactRevision"])


if __name__ == "__main__":
    unittest.main()
