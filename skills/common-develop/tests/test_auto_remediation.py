#!/usr/bin/env python3
"""Tests for wk -auto deterministic remediation policy helpers."""

from __future__ import annotations

import unittest

import auto_remediation


class AutoRemediationTests(unittest.TestCase):
    def test_p1_freezes_forward_but_keeps_remediation_active(self) -> None:
        result = auto_remediation.classify(
            signal="p1_open",
            deterministic=True,
            safe=True,
            ambiguous=False,
            transient=False,
            changes_code_config_or_test=True,
            architecture_change=False,
            same_signature_count=0,
            new_progress_evidence=True,
        )
        self.assertEqual("FROZEN", result["batchForward"])
        self.assertEqual("ACTIVE", result["autoRemediation"])
        self.assertEqual("AUTO_FIX", result["decision"])
        self.assertTrue(result["requiresNewExactRevision"])
        self.assertTrue(result["requiresP0"])
        self.assertTrue(result["requiresRelatedP1Review"])

    def test_transient_ci_failure_reruns_same_revision(self) -> None:
        result = auto_remediation.classify(
            signal="ci_failure",
            deterministic=True,
            safe=True,
            ambiguous=False,
            transient=True,
            changes_code_config_or_test=False,
            architecture_change=False,
            same_signature_count=0,
            new_progress_evidence=True,
        )
        self.assertEqual("RERUN_SAME_REVISION", result["decision"])
        self.assertFalse(result["requiresNewExactRevision"])
        self.assertTrue(result["requiresP0"])

    def test_authority_stale_only_auto_reconciles_when_deterministic(self) -> None:
        repaired = auto_remediation.classify(
            signal="authority_stale", deterministic=True, safe=True, ambiguous=False,
            transient=False, changes_code_config_or_test=False, architecture_change=False,
            same_signature_count=0, new_progress_evidence=True,
        )
        self.assertEqual("RECONCILE", repaired["authorityAction"])
        blocked = auto_remediation.classify(
            signal="authority_stale", deterministic=False, safe=True, ambiguous=False,
            transient=False, changes_code_config_or_test=False, architecture_change=False,
            same_signature_count=0, new_progress_evidence=True,
        )
        self.assertEqual("STOP", blocked["decision"])
        self.assertEqual("authority_conflict", blocked["stopReason"])

    def test_safety_block_forbids_unsafe_bypass(self) -> None:
        result = auto_remediation.classify(
            signal="safety_block", deterministic=True, safe=False, ambiguous=False,
            transient=False, changes_code_config_or_test=True, architecture_change=False,
            same_signature_count=0, new_progress_evidence=True,
        )
        self.assertEqual("STOP", result["decision"])
        self.assertEqual("safety_bypass_required", result["stopReason"])

    def test_production_mutation_preflight_uses_fixed_order(self) -> None:
        result = auto_remediation.production_preflight({
            "authority_current": True,
            "test_oracle_authoritative": False,
            "fixture_representative": True,
            "configuration_semantically_valid": True,
            "environment_stable": True,
        })
        self.assertFalse(result["productionMutationAllowed"])
        self.assertEqual("test_oracle_authoritative", result["firstBlockingCheck"])

    def test_architecture_gate_only_for_real_boundary_change(self) -> None:
        ordinary = auto_remediation.architecture_decision(["test_fixture_correction"])
        self.assertEqual("SKIP_AR", ordinary["decision"])
        self.assertFalse(ordinary["requiresAr"])
        boundary = auto_remediation.architecture_decision(["trust_boundary_change"])
        self.assertEqual("RUN_AR", boundary["decision"])
        self.assertTrue(boundary["requiresAr"])

    def test_convergence_continues_when_failure_moves_deeper(self) -> None:
        result = auto_remediation.convergence_decision(
            same_signature_count=3,
            previous_failure_eliminated=False,
            failure_moved_deeper=True,
            new_deterministic_evidence=False,
        )
        self.assertTrue(result["continueRemediation"])
        stopped = auto_remediation.convergence_decision(
            same_signature_count=2,
            previous_failure_eliminated=False,
            failure_moved_deeper=False,
            new_deterministic_evidence=False,
        )
        self.assertFalse(stopped["continueRemediation"])

    def test_log_diagnosis_is_conservative(self) -> None:
        result = auto_remediation.diagnose_log(
            "AssertionFailedError: missing compiled access rule: system:payment/target:view:OrderInfo/status/READ"
        )
        self.assertEqual("CONFIG_OR_POLICY_BINDING", result["primaryClass"])
        self.assertFalse(result["productionMutationAllowed"])


if __name__ == "__main__":
    unittest.main()
