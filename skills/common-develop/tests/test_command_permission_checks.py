#!/usr/bin/env python3
"""Tests for the command-surface to permission-contract check."""
from __future__ import annotations

import copy
import unittest

import check_skill
import contract_normalization
from _check_skill import command_permission_checks
from _check_skill.model import CheckResult


class CommandPermissionChecksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = contract_normalization.load_runtime_contract(
            check_skill.ASSETS / "runtime-contract.json"
        )

    def run_check(self, contract=None) -> CheckResult:
        result = CheckResult()
        command_permission_checks.check_command_permission_consistency(
            contract or self.contract, result
        )
        return result

    def test_current_contract_passes_and_discovers_non_parser_surfaces(self) -> None:
        result = self.run_check()
        self.assertEqual([], result.errors, "\n".join(result.errors))
        details = result.details["commandPermissionConsistency"]
        self.assertIn("long_task", details["publicScripts"])
        self.assertIn("check_skill", details["nonParserCommands"])
        self.assertIn("run", details["nonParserCommands"]["quality_gate"])
        self.assertEqual(1, len(details["aliases"]))

    def test_missing_assignment_is_rejected(self) -> None:
        contract = copy.deepcopy(self.contract)
        profile = next(
            profile for profile, commands in contract["scriptPermissionAssignments"].items()
            if "wk.status" in commands
        )
        contract["scriptPermissionAssignments"][profile].remove("wk.status")
        result = self.run_check(contract)
        self.assertTrue(any("wk.status" in error and "missing" in error for error in result.errors))

    def test_stale_assignment_is_rejected(self) -> None:
        contract = copy.deepcopy(self.contract)
        profile = next(iter(contract["scriptPermissionAssignments"]))
        contract["scriptPermissionAssignments"][profile].append("wk.not-a-command")
        result = self.run_check(contract)
        self.assertTrue(any("wk.not-a-command" in error and "unknown" in error for error in result.errors))

    def test_risk_trigger_catalog_has_independent_permission(self) -> None:
        contract = copy.deepcopy(self.contract)
        result = self.run_check(contract)
        self.assertEqual([], result.errors, "\n".join(result.errors))
        permissions = contract_normalization.materialize_runtime_contract(contract)["scriptPermissions"]
        self.assertEqual("STATIC", permissions["long_task.list-risk-triggers"]["mode"])

    def test_absolute_long_task_path_maps_to_long_task(self) -> None:
        path = check_skill.ROOT / "scripts" / "_long_task" / "cli.py"
        self.assertEqual("long_task", command_permission_checks._script_name(path))

    def test_excluded_implementation_surfaces_do_not_create_missing_assignments(self) -> None:
        result = self.run_check()
        self.assertFalse(any("delivery_readiness" in error for error in result.errors))
        details = result.details["commandPermissionConsistency"]
        self.assertNotIn("delivery_readiness", details["publicScripts"])

    def test_missing_mutation_declaration_is_rejected(self) -> None:
        contract = copy.deepcopy(self.contract)
        mutability = contract["scriptCommandSurfaces"]["mutability"]
        mutability.pop("wk.status")
        result = self.run_check(contract)
        self.assertTrue(any("wk.status" in error and "missing mutation" in error for error in result.errors))

    def test_invalid_mutation_value_is_rejected(self) -> None:
        contract = copy.deepcopy(self.contract)
        contract["scriptCommandSurfaces"]["mutability"]["wk.status"] = "MAYBE"
        result = self.run_check(contract)
        self.assertTrue(any("wk.status" in error and "READ_ONLY or MUTATING" in error for error in result.errors))

    def test_read_only_profile_cannot_contain_mutating_command(self) -> None:
        contract = copy.deepcopy(self.contract)
        profile_id = "PERM-920C8FBA64"
        contract["scriptPermissionAssignments"][profile_id].append("merge_docs.apply")
        result = self.run_check(contract)
        self.assertTrue(any("merge_docs.apply" in error and "mutation mismatch" in error for error in result.errors))

    def test_mutating_profile_cannot_contain_read_only_command(self) -> None:
        contract = copy.deepcopy(self.contract)
        profile_id = "PERM-39C1E5DDF1"
        contract["scriptPermissionAssignments"][profile_id].append("merge_docs.status")
        result = self.run_check(contract)
        self.assertTrue(any("merge_docs.status" in error and "mutation mismatch" in error for error in result.errors))

    def test_alias_cannot_have_mutation_declaration(self) -> None:
        contract = copy.deepcopy(self.contract)
        contract["scriptCommandSurfaces"]["mutability"]["long_task.render-work-record"] = "READ_ONLY"
        result = self.run_check(contract)
        self.assertTrue(any("mutability must omit aliases" in error for error in result.errors))

    def test_stale_mutation_declaration_is_rejected(self) -> None:
        contract = copy.deepcopy(self.contract)
        contract["scriptCommandSurfaces"]["mutability"]["wk.not-a-command"] = "READ_ONLY"
        result = self.run_check(contract)
        self.assertTrue(any("wk.not-a-command" in error and "unknown command" in error for error in result.errors))

    def test_profile_without_mutation_declaration_fails_closed(self) -> None:
        contract = copy.deepcopy(self.contract)
        profile_id = "PERM-920C8FBA64"
        contract["scriptPermissionProfiles"][profile_id].pop("mutation")
        result = self.run_check(contract)
        self.assertTrue(any(profile_id in error and "must declare mutation" in error for error in result.errors))

    def test_unknown_profile_and_duplicate_assignment_are_rejected(self) -> None:
        contract = copy.deepcopy(self.contract)
        assignments = contract["scriptPermissionAssignments"]
        first, second = list(assignments)[:2]
        assignments["PERM-UNKNOWN"] = ["wk.status"]
        assignments[second].append(assignments[first][0])
        result = self.run_check(contract)
        self.assertTrue(any("unknown profile" in error for error in result.errors))
        self.assertTrue(any("duplicated" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()
