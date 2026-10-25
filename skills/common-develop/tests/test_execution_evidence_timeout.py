#!/usr/bin/env python3
"""Tests for bounded command Evidence execution."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import execution_evidence
from _long_task import command_evidence


class ExecutionEvidenceTimeoutTests(unittest.TestCase):
    def test_cli_default_timeout_is_bounded(self) -> None:
        args = execution_evidence.build_parser().parse_args([
            "run", "-g", "ProjectManagerAgent", "--task-dir", "task",
            "--phase", "development", "--revision", "DEV-R01",
            "--command", "true", "--mutation-scope", "taskFacts",
        ])
        self.assertEqual(command_evidence.DEFAULT_COMMAND_TIMEOUT_SECONDS, args.timeout)

    def test_non_positive_timeout_is_rejected_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(command_evidence.ExecutionEvidenceError, "positive"):
                command_evidence.run_and_register(
                    Path(temp), agent="ProjectManagerAgent", command="true",
                    revision="DEV-R01", phase="development", cwd=Path(temp),
                    mutation_scope="taskFacts", timeout=0, scope_refs=[],
                )


if __name__ == "__main__":
    unittest.main()
