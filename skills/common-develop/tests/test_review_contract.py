"""Tests for the current machine-verifiable review acceptance contract."""
from __future__ import annotations
import argparse
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
import long_task
import review_contract


class ReviewContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.doc_root = Path(self.tmp.name) / "project_doc"
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.command_init(argparse.Namespace(
                doc_root=str(self.doc_root), version="V_TEST", target="REVIEWS", reuse=False,
                agent="ProjectManagerAgent",
            ))
        self.task_dir = self.doc_root / "version" / "V_TEST" / "task" / "REVIEWS"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def evidence_for(self, criterion_id: str, revision: str, phase: str, reviewer: str) -> list[dict]:
        policy = long_task.REVIEW_CRITERIA[criterion_id]["evidencePolicy"]
        types: list[str] = []
        for value in policy.get("allOfTypes", []):
            if value not in types:
                types.append(value)
        any_types = policy.get("anyOfTypes", [])
        if any_types and not any(value in types for value in any_types):
            types.append(any_types[0])
        while len(types) < policy.get("minimumEvidenceCount", 1):
            types.append(any_types[0] if any_types else "document_ref")
        result = []
        for index, value in enumerate(types, start=1):
            path = self.task_dir / "evidence" / "review-contract" / f"{criterion_id}-{index}.txt"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"Evidence for {criterion_id}.", encoding="utf-8")
            entry = long_task.evidence.register_evidence(
                self.task_dir, agent=reviewer, evidence_type=value,
                ref=str(path.relative_to(self.task_dir)), revision=revision, phase=phase,
                scope_refs=[criterion_id],
            )
            result.append({"evidence_id": entry["evidence_id"], "claim": f"Evidence for {criterion_id}."})
        return result

    def test_source_contract_uses_policy_catalog_and_shared_profile_templates(self) -> None:
        source = review_contract.load_source_contract()
        self.assertTrue(source["evidencePolicyCatalog"])
        self.assertTrue(source["profileTemplates"])
        self.assertTrue(all("evidencePolicyRef" in item for item in source["criteriaCatalog"].values()))
        self.assertTrue(all("evidencePolicy" not in item for item in source["criteriaCatalog"].values()))
        expanded = review_contract.load_contract()
        self.assertTrue(all("evidencePolicy" in item for item in expanded["criteriaCatalog"].values()))
        template_users = {
            template_id: (
                [profile_id for profile_id, profile in source["profiles"].items() if profile.get("templateRef") == template_id]
                + [
                    f"risk:{risk_id}"
                    for risk_id, risk in source.get("riskReviewTemplates", {}).items()
                    if risk.get("templateRef") == template_id
                ]
            )
            for template_id in source["profileTemplates"]
        }
        self.assertTrue(all(users for users in template_users.values()))
        self.assertEqual(17, len(source["profiles"]))
        self.assertEqual(8, len(source.get("riskReviewTemplates", {})))
        self.assertTrue(all("criterionIds" in profile for profile in expanded["profiles"].values()))

    def test_every_runtime_reviewer_assignment_has_exactly_one_profile(self) -> None:
        expected = set()
        for phase, reviewers in long_task.COLLABORATION_REVIEWERS.items():
            expected.update((phase, reviewer) for reviewer in reviewers)
        for phase, mapping in long_task.CONDITIONAL_REVIEWERS.items():
            expected.update((phase, reviewer) for reviewer in mapping)
        for reviewer, (_, phases) in long_task.RISK_REVIEWERS_BY_AGENT.items():
            expected.update((phase, reviewer) for phase in phases)
        for profile_id in (long_task.ARCHITECTURE_REVIEW_POLICY.get("firstStep") or {}).get("reviewProfiles", []):
            phase, reviewer = profile_id.split(":", 1)
            expected.add((phase, reviewer))
        core = {(profile["phase"], profile["reviewerAgent"]) for profile in long_task.REVIEW_PROFILES.values()}
        runtime_core = set()
        for phase, reviewers in long_task.COLLABORATION_REVIEWERS.items():
            runtime_core.update((phase, reviewer) for reviewer in reviewers)
        self.assertEqual(runtime_core, core)
        for phase, reviewer in expected - core:
            profile = review_contract.resolve_profile(review_contract.load_contract(), phase, reviewer)
            self.assertIsNotNone(profile, f"missing dynamic profile {phase}:{reviewer}")

    def test_every_profile_can_form_a_structurally_valid_passed_conclusion(self) -> None:
        for profile_id, profile in long_task.REVIEW_PROFILES.items():
            revision = f"{profile['phase'].upper()}-R01@test"
            conclusion = {
                "profile_id": profile_id,
                "revision": revision,
                "conclusion": "PASSED",
                "checked_scope": [f"scope/{profile_id}"],
                "criteria_results": [
                    {
                        "criterion_id": criterion_id,
                        "status": "PASSED",
                        "evidence": self.evidence_for(criterion_id, revision, profile["phase"], profile["reviewerAgent"]),
                        "finding_ids": [],
                        "reason": "",
                    }
                    for criterion_id in profile["criterionIds"]
                ],
                "finding_ids": [],
                "limitations": [],
                "reviewed_at": "2026-07-20T10:00:00-05:00",
            }
            errors: list[str] = []
            long_task.validate_independent_conclusion(
                self.task_dir, profile["phase"], profile["reviewerAgent"], conclusion, revision, [], errors
            )
            self.assertEqual([], errors, f"{profile_id}: {errors}")

    def test_all_profile_criteria_exist_and_are_unique(self) -> None:
        for profile_id, profile in long_task.REVIEW_PROFILES.items():
            criterion_ids = profile["criterionIds"]
            self.assertTrue(criterion_ids, profile_id)
            self.assertEqual(len(criterion_ids), len(set(criterion_ids)), profile_id)
            self.assertTrue(all(value in long_task.REVIEW_CRITERIA for value in criterion_ids), profile_id)

    def test_requirement_confirmation_does_not_require_test_artifacts(self) -> None:
        source = review_contract.load_source_contract()
        profile = source["profiles"]["requirement_confirmation:RequirementAnalysisAgent"]

        self.assertEqual(
            ["RC-REQ-001", "RC-REQ-002", "RC-REQ-003", "RC-REQ-004", "RC-REQ-005"],
            profile["criterionIds"],
        )
        self.assertFalse(any(value.startswith("RC-TEST-") for value in profile["criterionIds"]))

        for criterion_id in profile["criterionIds"]:
            policy_id = source["criteriaCatalog"][criterion_id]["evidencePolicyRef"]
            policy = source["evidencePolicyCatalog"][policy_id]
            evidence_types = set(policy.get("allOfTypes", [])) | set(policy.get("anyOfTypes", []))
            self.assertNotIn("test_ref", evidence_types, criterion_id)

        for criterion_id in ("RC-REQ-003", "RC-REQ-004"):
            policy_id = source["criteriaCatalog"][criterion_id]["evidencePolicyRef"]
            policy = source["evidencePolicyCatalog"][policy_id]
            evidence_types = set(policy.get("allOfTypes", [])) | set(policy.get("anyOfTypes", []))
            self.assertEqual({"requirement_ref"}, evidence_types, criterion_id)

    def test_test_artifact_contract_starts_in_test_design(self) -> None:
        source = review_contract.load_source_contract()
        pre_test_design_profiles = (
            "requirement_analysis:TestDesignAgent",
            "business_model:TestDesignAgent",
            "design:TestDesignAgent",
        )
        for profile_id in pre_test_design_profiles:
            profile = source["profiles"][profile_id]
            self.assertFalse(any(value.startswith("RC-TEST-") for value in profile["criterionIds"]), profile_id)
            for criterion_id in profile["criterionIds"]:
                criterion = source["criteriaCatalog"][criterion_id]
                policy = source["evidencePolicyCatalog"][criterion["evidencePolicyRef"]]
                self.assertNotIn("test_ref", policy.get("allOfTypes", []), f"{profile_id}:{criterion_id}")
                self.assertNotEqual(["test_ref"], policy.get("anyOfTypes", []), f"{profile_id}:{criterion_id}")

        test_design_profile = source["profiles"]["test_design:TestEvidenceReviewAgent"]
        self.assertTrue(
            {f"RC-TEST-{index:03d}" for index in range(1, 8)}
            <= set(test_design_profile["criterionIds"])
        )
        self.assertIn("testing:TestEvidenceReviewAgent", source["profiles"])
        testing_profile = source["profiles"]["testing:TestEvidenceReviewAgent"]
        self.assertTrue(
            {f"RC-TEST-{index:03d}" for index in range(1, 8)}
            <= set(testing_profile["criterionIds"])
        )
        self.assertIn("RC-EVID-001", testing_profile["criterionIds"])

    def test_evidence_policies_only_use_declared_types(self) -> None:
        for criterion_id, criterion in long_task.REVIEW_CRITERIA.items():
            policy = criterion["evidencePolicy"]
            referenced = set(policy.get("anyOfTypes", [])) | set(policy.get("allOfTypes", []))
            self.assertTrue(referenced <= long_task.REVIEW_EVIDENCE_TYPES, criterion_id)

    def test_focused_criterion_policy_and_required_queries(self) -> None:
        source = review_contract.load_source_contract()
        criterion_id = next(iter(source["criteriaCatalog"]))
        policy_id = source["criteriaCatalog"][criterion_id]["evidencePolicyRef"]
        criterion = review_contract.criterion_payload(review_contract.load_contract(), criterion_id)
        policy = review_contract.policy_payload(source, policy_id)
        self.assertEqual(criterion_id, criterion["criterionId"])
        self.assertIn(criterion_id, policy["criterionIds"])
        profile = next(iter(review_contract.load_contract()["profiles"].values()))
        required = review_contract.required_payload(
            review_contract.load_contract(), profile["phase"], profile["reviewerAgent"]
        )
        self.assertEqual(profile["profileId"], required["profile"]["profileId"])
        self.assertTrue(required["criteria"])


if __name__ == "__main__":
    unittest.main()
