#!/usr/bin/env python3
"""Tests for normalized machine-contract storage."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import contract_normalization

ROOT = Path(__file__).resolve().parent.parent


class ContractNormalizationTests(unittest.TestCase):
    def test_runtime_permission_profiles_materialize_all_commands(self) -> None:
        raw = json.loads((ROOT / "assets" / "runtime-contract.json").read_text(encoding="utf-8"))
        self.assertEqual(2, raw["schemaVersion"])
        self.assertNotIn("scriptPermissions", raw)
        materialized = contract_normalization.materialize_runtime_contract(raw)
        permissions = materialized["scriptPermissions"]
        assigned = sum(len(commands) for commands in raw["scriptPermissionAssignments"].values())
        self.assertEqual(assigned, len(permissions))
        self.assertEqual(len(contract_normalization.load_runtime_contract()["scriptPermissions"]), len(permissions))
        self.assertEqual("STATIC", permissions["check_skill.check"]["mode"])
        self.assertEqual("DECLARED_AGENT", permissions["review_contract.list"]["mode"])
        self.assertEqual("TASK_CONTEXT", permissions["long_task.task-events"]["mode"])
        self.assertEqual("TASK_CONTEXT", permissions["wk.reconcile"]["mode"])
        self.assertEqual("STATIC", permissions["wk.close"]["mode"])
        self.assertEqual("STATIC", permissions["git_checkpoint.push"]["mode"])
        self.assertEqual("TASK_CONTEXT", permissions["execution_evidence.run"]["mode"])
        self.assertEqual("STATIC", permissions["design_doc.init"]["mode"])
        self.assertEqual("DECLARED_AGENT", permissions["design_doc.validate"]["mode"])
        self.assertNotIn("mutation", permissions["wk.status"])
        self.assertNotIn("mutation", permissions["wk.start"])
        self.assertEqual("READ_ONLY", raw["scriptPermissionProfiles"]["PERM-920C8FBA64"]["mutation"])
        self.assertNotIn("task_summary.sync", permissions)
        self.assertNotIn("doc_migration.preview", permissions)

    def test_review_defaults_and_profile_identity_materialize(self) -> None:
        raw = json.loads((ROOT / "assets" / "review-contract.json").read_text(encoding="utf-8"))
        self.assertEqual(2, raw["schemaVersion"])
        materialized = contract_normalization.materialize_review_contract(raw)
        self.assertEqual(83, len(materialized["evidencePolicyCatalog"]))
        self.assertEqual(147, len(materialized["criteriaCatalog"]))
        self.assertEqual(17, len(materialized["profiles"]))
        self.assertEqual(8, len(materialized["riskReviewTemplates"]))
        for policy in materialized["evidencePolicyCatalog"].values():
            self.assertEqual({"minimumEvidenceCount", "allOfTypes", "anyOfTypes"}, set(policy))
        for criterion in materialized["criteriaCatalog"].values():
            self.assertIn("allowNotApplicable", criterion)
            self.assertIn("failureSeverityFloor", criterion)
        for key, profile in materialized["profiles"].items():
            phase, reviewer = key.split(":", 1)
            self.assertEqual(key, profile["profileId"])
            self.assertEqual(phase, profile["phase"])
            self.assertEqual(reviewer, profile["reviewerAgent"])

    def test_routing_defaults_and_budget_groups_materialize(self) -> None:
        raw = json.loads((ROOT / "context-routing.json").read_text(encoding="utf-8"))
        self.assertEqual(2, raw["schemaVersion"])
        self.assertNotIn("baseRouteTokenBudgetTargets", raw["routingPolicy"])
        materialized = contract_normalization.materialize_context_routing(raw)
        self.assertEqual(97, len(materialized["resourceCatalog"]))
        expected_target_count = len(materialized["routingPolicy"]["baseRouteTokenBudgetTargets"])
        self.assertEqual(expected_target_count, len(set(materialized["routingPolicy"]["baseRouteTokenBudgetTargets"])))
        self.assertEqual("references/runtime-contract.md", materialized["resourceCatalog"]["runtime-contract"]["path"])
        self.assertEqual("full", materialized["resourceCatalog"]["runtime-contract"]["readStrategy"])
        task_types = [task for route in materialized["routes"] for task in route["taskTypes"]]
        self.assertEqual(len(task_types), len(set(task_types)))

    def test_normalized_contracts_stay_under_current_size_ceilings(self) -> None:
        ceilings = {
            # Doctor repair and standard user-skip authorization policies are
            # and AI orchestration risk/cache policies are explicit machine facts.
            # Keep a bounded allowance for these additional command declarations.
            "assets/runtime-contract.json": 76_000,
            "assets/review-contract.json": 90_000,
            "context-routing.json": 55_000,
        }
        for relative, ceiling in ceilings.items():
            with self.subTest(relative=relative):
                self.assertLessEqual((ROOT / relative).stat().st_size, ceiling)

    def test_invalid_duplicate_assignment_fails_closed(self) -> None:
        raw = json.loads((ROOT / "assets" / "runtime-contract.json").read_text(encoding="utf-8"))
        first_profile = next(iter(raw["scriptPermissionAssignments"]))
        second_profile = next(profile for profile in raw["scriptPermissionAssignments"] if profile != first_profile)
        duplicate = raw["scriptPermissionAssignments"][first_profile][0]
        raw["scriptPermissionAssignments"][second_profile].append(duplicate)
        with self.assertRaises(contract_normalization.ContractNormalizationError):
            contract_normalization.materialize_runtime_contract(raw)


if __name__ == "__main__":
    unittest.main()
