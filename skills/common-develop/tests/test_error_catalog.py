#!/usr/bin/env python3
"""Regression tests for stable machine-readable skill errors."""
from __future__ import annotations

import contextlib
import io
import shlex
import unittest
import tempfile
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import error_catalog
import wk
import work_mode
import long_task
import request_intake
import merge_docs


class ErrorCatalogTests(unittest.TestCase):
    def test_work_mode_error_exposes_code_and_chinese_message(self) -> None:
        error = work_mode.WorkModeError("工作模式无效", code="WORKMODE_TABLE_INVALID")

        self.assertIsInstance(error, error_catalog.SkillError)
        self.assertEqual("WORKMODE_TABLE_INVALID", error.code)
        self.assertEqual("工作模式无效", error.message)
        self.assertEqual("WORKMODE_TABLE_INVALID: 工作模式无效", str(error))
        self.assertEqual(
            {"code": "WORKMODE_TABLE_INVALID", "message": "工作模式无效"},
            error.to_record(),
        )

    def test_repair_command_binds_installation_path_and_preserves_arguments(self) -> None:
        error = error_catalog.WorkModeError(
            "模式与工作表不一致",
            code="WORKMODE_SUMMARY_DRIFT",
            repair_command="python3 scripts/work_mode.py validate",
        )

        record = error.to_record()
        self.assertEqual("WORKMODE_SUMMARY_DRIFT", record["code"])
        self.assertEqual("模式与工作表不一致", record["message"])
        expected_script = Path(__file__).resolve().parent.parent / "scripts" / "work_mode.py"
        self.assertEqual(["python3", str(expected_script), "validate"], shlex.split(record["repairCommand"]))

    def test_wk_stderr_keeps_error_prefix_and_stable_code(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = wk.main([
                "start",
                "-g", "ProjectManagerAgent",
                "--doc-root", "/tmp/common-develop-error-test-missing",
                "--version", "V_TEST",
                "-f", "test feature",
                "--classification", "simple-optimization",
                "--related-feature", "PRD0013-F01",
                "--business-logic-impact", "NONE",
                "--data-model-impact", "NONE",
                "--api-contract-impact", "NONE",
                "--permission-impact", "NONE",
                "--workflow-impact", "NONE",
                "--persistence-impact", "NONE",
                "--external-side-effect-impact", "NONE",
                "--migration-impact", "NONE",
                "--dependency-impact", "NONE",
                "-gp",
                "--apply",
            ])

        self.assertEqual(1, result)
        self.assertIn("ERROR: WORKMODE_GIT_PUSH_REQUIRES_CHECKPOINT:", stderr.getvalue())

    def test_work_mode_error_without_explicit_code_uses_fallback_code(self) -> None:
        error = error_catalog.WorkModeError("内部模式错误")
        self.assertEqual("WORKMODE_ERROR", error.code)
        self.assertEqual("WORKMODE_ERROR: 内部模式错误", str(error))

    def test_domain_errors_have_stable_codes_and_records(self) -> None:
        cases = (
            (error_catalog.LifecycleError("cannot advance phase"), "LIFECYCLE_PHASE_TRANSITION_INVALID"),
            (error_catalog.ArchiveError("merge plan is missing"), "ARCHIVE_REQUIRED_DATA_MISSING"),
            (error_catalog.RequestIntakeError("request description must not be empty"), "INTAKE_VALIDATION_FAILED"),
        )
        for error, expected_code in cases:
            with self.subTest(error=type(error).__name__):
                self.assertEqual(expected_code, error.code)
                self.assertEqual(expected_code, error.to_record()["code"])
                self.assertTrue(error.message)

    def test_json_error_boundary_is_machine_readable(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = long_task.main([
                "validate", "-g", "ProjectManagerAgent",
                "--task-dir", "/tmp/no-such-long-task", "--json",
            ])
        self.assertEqual(1, result)
        payload = __import__("json").loads(stderr.getvalue())
        self.assertEqual("FAILED", payload["status"])
        self.assertEqual("CLI_ERROR", payload["error"]["code"])
        self.assertIn("task directory", payload["error"]["message"])

    def test_lifecycle_json_error_boundary_is_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir = Path(temp) / "project_doc" / "version" / "V_TEST" / "task" / "T1"
            task_dir.mkdir(parents=True)
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                result = long_task.main([
                    "sync-risk-reviewers", "-g", "ProjectManagerAgent",
                    "--task-dir", str(task_dir), "--json",
                ])
            self.assertEqual(1, result)
            payload = __import__("json").loads(stderr.getvalue())
            self.assertEqual("FAILED", payload["status"])
            self.assertEqual("LIFECYCLE_PRECONDITION_FAILED", payload["error"]["code"])
            self.assertIn("生命周期校验失败", payload["error"]["message"])

    def test_archive_json_error_boundary_is_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stderr = io.StringIO()
            with mock.patch.object(sys, "argv", [
                "merge_docs.py", "archive", "-g", "IncrementalArchiveAgent",
                "--doc-root", str(Path(temp) / "project_doc"),
                "--module", "orders", "--version", "V_TEST", "--json",
            ]):
                with contextlib.redirect_stderr(stderr):
                    result = merge_docs.main()
            self.assertEqual(1, result)
            payload = __import__("json").loads(stderr.getvalue())
            self.assertEqual("FAILED", payload["status"])
            self.assertEqual("ARCHIVE_REQUIRED_DATA_MISSING", payload["error"]["code"])
            self.assertIn("归档校验失败", payload["error"]["message"])

    def test_archive_text_error_boundary_keeps_stable_code_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            stderr = io.StringIO()
            with mock.patch.object(sys, "argv", [
                "merge_docs.py", "archive", "-g", "IncrementalArchiveAgent",
                "--doc-root", str(Path(temp) / "project_doc"),
                "--module", "orders", "--version", "V_TEST",
            ]):
                with contextlib.redirect_stderr(stderr):
                    result = merge_docs.main()
            self.assertEqual(1, result)
            self.assertTrue(stderr.getvalue().startswith("ERROR: ARCHIVE_REQUIRED_DATA_MISSING:"))

    def test_request_intake_json_error_boundary_is_machine_readable(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            result = request_intake.main([
                "validate", "-g", "ProjectManagerAgent",
                "--path", "/tmp/no-such-request-intake.json", "--json",
            ])
        self.assertEqual(1, result)
        payload = __import__("json").loads(stderr.getvalue())
        self.assertEqual("FAILED", payload["status"])
        self.assertTrue(payload["error"]["code"].startswith("INTAKE_"))
        self.assertIn("请求受理校验失败", payload["error"]["message"])


if __name__ == "__main__":
    unittest.main()
