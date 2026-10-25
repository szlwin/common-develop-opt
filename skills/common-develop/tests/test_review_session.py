"""Regression tests for the slim Core/Risk/Delta Review orchestration."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import agent_context
import long_task
import review_contract
import review_session


class ReviewSessionArchitectureTests(unittest.TestCase):
    def test_active_contract_is_17_core_plus_8_risk_templates(self) -> None:
        source = review_contract.load_source_contract()
        self.assertEqual(17, len(source["profiles"]))
        self.assertEqual(8, len(source["riskReviewTemplates"]))
        self.assertFalse(any(profile_id.startswith("tdd:") for profile_id in source["profiles"]))

    def test_tdd_is_embedded_development_subgate_not_selectable_phase(self) -> None:
        runtime = agent_context.RUNTIME_CONTRACT
        tdd = next(item for item in runtime["canonicalPhases"] if item["id"] == "tdd")
        self.assertEqual("DEVELOPMENT_SUBGATE", tdd["phaseKind"])
        self.assertEqual("development", tdd["embeddedIn"])
        self.assertFalse(tdd["newTaskSelectable"])
        self.assertNotIn("tdd", long_task.PHASES)
        self.assertEqual(["implementation_plan", "test_design"], long_task.phase_predecessors("development", False))

    def test_code_review_session_materializes_only_detected_risks(self) -> None:
        session = review_session.plan_session(
            phase="code_review",
            revision="DEV-R01@abc",
            explicit_risks=["concurrency", "security"],
            risk_report=None,
            impact_plan=None,
        )
        self.assertEqual([], review_session.validate_session(session))
        self.assertEqual(4, len(session["axes"]))
        self.assertEqual(2, sum(1 for item in session["axes"] if item["origin"] == "CORE"))
        self.assertEqual(
            {"concurrency", "security"},
            {item["riskType"] for item in session["axes"] if item["origin"] == "RISK"},
        )
        decisions = session["applicabilityDecisions"]
        self.assertEqual(8, len(decisions))
        self.assertEqual(2, sum(1 for item in decisions if item["applicable"]))
        self.assertTrue(all(not item["reviewResultEmittedWhenNotApplicable"] for item in decisions))
        self.assertTrue(session["executionIsolation"]["required"])
        self.assertFalse(session["executionIsolation"]["sameOwnerContextAgentSwitchAllowed"])

    def test_delta_plan_partitions_affected_and_unaffected_criteria(self) -> None:
        contract = review_contract.load_contract()
        profile_id = "code_review:SpecComplianceReviewAgent"
        criteria = list(contract["profiles"][profile_id]["criterionIds"])
        impact_plan = {
            "minimumRevalidation": {"reviews": [{"profileId": profile_id, "criterionIds": criteria[:2]}]},
            "unaffected": {"reviewCriteria": [{"profileId": profile_id, "criterionIds": criteria[2:]}]},
            "provenance": {"revision": "DEV-R02@new"},
        }
        session = review_session.plan_session(
            phase="code_review", revision="DEV-R02@new", explicit_risks=[],
            risk_report=None, impact_plan=impact_plan,
        )
        axis = next(item for item in session["axes"] if item["profileId"] == profile_id)
        self.assertEqual(criteria[:2], axis["affectedCriterionIds"])
        self.assertEqual(criteria[2:], axis["unaffectedCriterionIds"])
        self.assertTrue(axis["independentReviewResultRequired"])
        self.assertFalse(session["deltaPolicy"]["reviewResultReuseAcrossExactRevision"])
        self.assertEqual("RECAPTURE_OR_REBIND_CURRENT_REVISION_EVIDENCE", session["deltaPolicy"]["unaffectedCriterionAction"])
        self.assertEqual([], review_session.validate_session(session))

    def test_dynamic_risk_profile_resolves_without_becoming_core_profile(self) -> None:
        contract = review_contract.load_contract()
        self.assertNotIn("design:ConcurrencyReviewAgent", contract["profiles"])
        dynamic = review_contract.resolve_profile(
            contract, "design", "ConcurrencyReviewAgent"
        )
        self.assertEqual("RISK_TEMPLATE", dynamic["origin"])
        self.assertEqual("concurrency", dynamic["riskType"])
        self.assertTrue(dynamic["criterionIds"])

    def test_session_validator_rejects_overlapping_delta_partition(self) -> None:
        session = review_session.plan_session(
            phase="code_review", revision="DEV-R03@bad", explicit_risks=[],
            risk_report=None, impact_plan=None,
        )
        axis = session["axes"][0]
        axis["affectedCriterionIds"] = list(axis["criterionIds"])
        axis["unaffectedCriterionIds"] = list(axis["criterionIds"][:1])
        self.assertTrue(any("delta partition is invalid" in item for item in review_session.validate_session(session)))


if __name__ == "__main__":
    unittest.main()
