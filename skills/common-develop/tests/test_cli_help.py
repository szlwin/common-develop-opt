#!/usr/bin/env python3
"""Tests for parser-derived, machine-readable CLI help."""
from __future__ import annotations

import contextlib
import io
import json
import unittest
from unittest import mock

import cli_help
import long_task
import wk


class CliHelpTests(unittest.TestCase):
    def test_contract_separates_cli_entrypoints_from_module_interfaces(self) -> None:
        paths = set(cli_help.declared_cli_paths())
        self.assertIn("scripts/wk.py", paths)
        self.assertIn("scripts/cli_help.py", paths)
        self.assertIn("scripts/long_task.py", paths)
        self.assertNotIn("scripts/task_summary.py", paths)
        self.assertNotIn("scripts/doc_migration.py", paths)
        self.assertIn("scripts/case_doc.py", paths)
        self.assertIn("scripts/quality_gate.py", paths)
        self.assertNotIn("scripts/document_facts.py", paths)
        self.assertNotIn("scripts/work_record.py", paths)
        self.assertNotIn("scripts/unittest_worker.py", paths)

    def test_long_task_command_catalog_is_parser_derived(self) -> None:
        payload = cli_help.catalog_for_script("long_task", "advance-phase")
        self.assertEqual("scripts/long_task.py", payload["script"])
        self.assertEqual("advance-phase", payload["command"])
        self.assertTrue(payload["usage"].startswith("usage: long_task.py advance-phase"))
        self.assertTrue(payload["mutation"])
        self.assertFalse(payload["previewSupported"])
        self.assertEqual("LIFECYCLE_EVENT_WRITE", payload["effect"])
        task_dir = next(item for item in payload["arguments"] if item["name"] == "task_dir")
        self.assertTrue(task_dir["required"])
        self.assertIn("--task-dir", task_dir["flags"])
        self.assertEqual("ProjectManagerAgent", payload["agentDefault"])

    def test_long_task_help_subcommand_returns_json(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = long_task.main([
                "help", "-g", "ProjectManagerAgent", "--command", "advance-phase", "--json",
            ])
        self.assertEqual(0, code)
        self.assertEqual("advance-phase", json.loads(output.getvalue())["command"])

    def test_wk_help_can_query_declared_script_command(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = wk.main([
                "help", "-g", "ProjectManagerAgent", "--script", "long_task",
                "--command", "advance-phase", "--json",
            ])
        self.assertEqual(0, code)
        payload = json.loads(output.getvalue())
        self.assertEqual("long_task.advance-phase", payload["permissionCommandId"])

    def test_independent_test_environment_uses_canonical_permission_id(self) -> None:
        payload = cli_help.catalog_for_script("independent_test_environment", "status")
        self.assertEqual(
            "independent_test_environment.status",
            payload["permissionCommandId"],
        )
        permissions = cli_help.declared_permission_ids()
        self.assertIn("independent_test_environment.status", permissions)
        self.assertNotIn("test_environment.status", permissions)

    def test_help_validation_executes_top_level_and_subcommand_help(self) -> None:
        completed = mock.Mock(returncode=0, stdout="usage: command\n")
        with mock.patch.object(cli_help, "declared_cli_paths", return_value=["scripts/long_task.py"]):
            with mock.patch.object(cli_help.subprocess, "run", return_value=completed) as run:
                payload = cli_help.validate_declared_help()
        self.assertEqual("PASSED", payload["status"])
        self.assertEqual(1 + len(cli_help.command_names(long_task.build_parser())), run.call_count)

    def test_command_metadata_must_cover_every_long_task_subcommand(self) -> None:
        self.assertEqual(
            set(cli_help.command_names(long_task.build_parser())),
            set(long_task.COMMAND_HELP_METADATA),
        )

    def test_apg_support_clis_are_parser_and_permission_declared(self) -> None:
        paths = set(cli_help.declared_cli_paths())
        permissions = cli_help.declared_permission_ids()
        self.assertIn("scripts/governance_profile.py", paths)
        self.assertIn("scripts/project_constraints.py", paths)
        self.assertTrue({
            "governance_profile.recommend",
            "project_constraints.validate",
            "project_constraints.status",
        } <= permissions)

    def test_permission_catalog_is_parser_derived_for_new_evidence_commands(self) -> None:
        permissions = cli_help.declared_permission_ids()
        self.assertIn("evidence.query", permissions)
        self.assertIn("evidence.snapshot-register", permissions)
        self.assertIn("evidence.bundle-register", permissions)
        self.assertNotIn("evidence.gc-storage", permissions)
        self.assertIn("git_checkpoint.connector-request", permissions)
        self.assertIn("task_layout.view", permissions)

if __name__ == "__main__":
    unittest.main()
