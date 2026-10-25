"""Tests for the canonical common-develop delivery gate."""

from __future__ import annotations

import ast
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import quality_gate


class QualityGateTest(unittest.TestCase):
    def test_clean_transient_removes_only_known_generated_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "scripts" / "__pycache__").mkdir(parents=True)
            (root / "scripts" / "__pycache__" / "x.pyc").write_bytes(b"x")
            (root / "keep.tmp.txt").write_text("keep", encoding="utf-8")
            (root / "remove.tmp").write_text("remove", encoding="utf-8")
            removed = quality_gate.clean_transient(root)
            self.assertIn("scripts/__pycache__", removed)
            self.assertIn("remove.tmp", removed)
            self.assertTrue((root / "keep.tmp.txt").is_file())

    def test_discovery_report_explains_canonical_and_internal_scope(self) -> None:
        report = quality_gate.test_discovery_report()
        paths = {item["path"] for item in report["discoveredTests"]}
        self.assertIn("tests/test_quality_gate.py", paths)
        self.assertEqual("TOP_LEVEL_TEST_MODULES_ONLY", report["policy"])
        self.assertEqual("tests/internal", report["excludedTests"][0]["path"])
        self.assertIn("tests/internal/test_markers.py", report["excludedTests"][0]["files"])
        self.assertTrue(report["excludedTests"][0]["reason"])

    def test_subprocess_start_failure_is_structured(self) -> None:
        with mock.patch.object(
            quality_gate.subprocess, "run", side_effect=FileNotFoundError("python missing")
        ):
            result = quality_gate._run("semantic_audit", ["missing-python"], {})
        self.assertEqual("FAILED", result["status"])
        self.assertEqual("QUALITY_GATE_SUBPROCESS_START_FAILED", result["errorCode"])
        self.assertEqual("FileNotFoundError", result["errorType"])

    def test_cleanup_report_keeps_concurrent_missing_file_nonfatal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            target = root / "remove.tmp"
            target.write_text("x", encoding="utf-8")
            with mock.patch.object(quality_gate, "transient_paths", return_value=[target]):
                with mock.patch.object(Path, "unlink", side_effect=FileNotFoundError):
                    result = quality_gate.clean_transient_report(root)
        self.assertEqual([], result["errors"])

    def test_cleanup_failure_is_a_blocking_structured_step(self) -> None:
        completed = mock.Mock(returncode=0, stdout="ok\n")
        cleanup = {
            "removed": [],
            "errors": [{"path": "x.tmp", "errorType": "PermissionError", "message": "denied"}],
        }
        with mock.patch.object(quality_gate.subprocess, "run", return_value=completed):
            with mock.patch.object(quality_gate, "clean_transient_report", return_value=cleanup):
                with mock.patch.object(quality_gate.agent_context, "authorize_static"):
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output):
                        code = quality_gate.main([
                            "-g", "ProjectManagerAgent", "--profile", "static",
                            "--clean-transient", "--json",
                        ])
        self.assertEqual(1, code)
        payload = json.loads(output.getvalue())
        step = next(item for item in payload["steps"] if item["name"] == "cleanup-before")
        self.assertEqual("QUALITY_GATE_CLEANUP_FAILED", step["errorCode"])
        self.assertEqual("CLEANUP", step["phase"])
        self.assertEqual("PermissionError", step["errors"][0]["errorType"])

    def test_unexpected_discovery_failure_is_internal_error(self) -> None:
        completed = mock.Mock(returncode=0, stdout="ok\n")
        with mock.patch.object(quality_gate.subprocess, "run", return_value=completed):
            with mock.patch.object(quality_gate, "test_modules", return_value=["test_one"]):
                with mock.patch.object(quality_gate, "test_ids", side_effect=AssertionError("gate bug")):
                    with mock.patch.object(quality_gate.agent_context, "authorize_static"):
                        output = io.StringIO()
                        with contextlib.redirect_stdout(output):
                            code = quality_gate.main([
                                "-g", "ProjectManagerAgent", "--profile", "full", "--json",
                            ])
        self.assertEqual(1, code)
        payload = json.loads(output.getvalue())
        step = next(item for item in payload["steps"] if item["name"] == "unit:test_one")
        self.assertEqual("QUALITY_GATE_INTERNAL_ERROR", step["errorCode"])
        self.assertEqual("DISCOVERY", step["phase"])

    def test_static_profile_runs_only_static_steps(self) -> None:
        completed = mock.Mock(returncode=0, stdout="ok\n")
        with mock.patch.object(quality_gate.subprocess, "run", return_value=completed) as run:
            with mock.patch.object(quality_gate.agent_context, "authorize_static"):
                code = quality_gate.main(["-g", "ProjectManagerAgent", "--profile", "static"])
        self.assertEqual(0, code)
        self.assertEqual(3, run.call_count)

    def test_full_profile_runs_unit_tests_after_static_success(self) -> None:
        completed = mock.Mock(returncode=0, stdout="ok\n")
        with mock.patch.object(quality_gate.subprocess, "run", return_value=completed) as run:
            with mock.patch.object(quality_gate, "test_modules", return_value=["test_one", "test_merge_docs"]):
                with mock.patch.object(quality_gate, "test_ids", return_value=["test_merge_docs.Case.test_a", "test_merge_docs.Case.test_b"]):
                    with mock.patch.object(quality_gate.agent_context, "authorize_static"):
                        code = quality_gate.main(["-g", "ProjectManagerAgent", "--profile", "full"])
        self.assertEqual(0, code)
        self.assertEqual(5, run.call_count)
        group_command = run.call_args_list[-2].args[0]
        merge_command = run.call_args_list[-1].args[0]
        self.assertIn("test_one", group_command)
        self.assertIn("test_merge_docs.Case.test_a", merge_command)
        self.assertIn("test_merge_docs.Case.test_b", merge_command)

    def test_fast_profile_passes_only_non_slow_test_ids_to_worker(self) -> None:
        completed = mock.Mock(returncode=0, stdout="ok\n")
        with mock.patch.object(quality_gate.subprocess, "run", return_value=completed) as run:
            with mock.patch.object(quality_gate, "test_modules", return_value=["test_one"]):
                with mock.patch.object(
                    quality_gate, "test_ids",
                    return_value=["test_one.Case.test_fast"],
                ) as discover:
                    with mock.patch.object(quality_gate.agent_context, "authorize_static"):
                        code = quality_gate.main(["-g", "ProjectManagerAgent", "--profile", "fast"])
        self.assertEqual(0, code)
        discover.assert_called_once_with("test_one", include_slow=False)
        self.assertIn("test_one.Case.test_fast", run.call_args_list[-1].args[0])

    def test_full_profile_runs_unit_tests_even_when_static_check_fails(self) -> None:
        failed = mock.Mock(returncode=1, stdout="stale manifest\n")
        passed = mock.Mock(returncode=0, stdout="ok\n")
        with mock.patch.object(quality_gate.subprocess, "run", side_effect=[failed, passed, passed, passed]) as run:
            with mock.patch.object(quality_gate, "test_modules", return_value=["test_one"]):
                with mock.patch.object(quality_gate, "test_ids", return_value=["test_one.Case.test_a"]):
                    with mock.patch.object(quality_gate.agent_context, "authorize_static"):
                        code = quality_gate.main(["-g", "ProjectManagerAgent", "--profile", "full"])
        self.assertEqual(1, code)
        self.assertEqual(4, run.call_count)
        unit_command = run.call_args_list[-1].args[0]
        self.assertEqual(quality_gate.sys.executable, unit_command[0])
        self.assertIn("test_one", unit_command)

    def test_fail_fast_stops_after_first_static_failure(self) -> None:
        failed = mock.Mock(returncode=1, stdout="failed\n")
        with mock.patch.object(quality_gate.subprocess, "run", return_value=failed) as run:
            with mock.patch.object(quality_gate, "test_modules", return_value=["test_one"]):
                with mock.patch.object(quality_gate.agent_context, "authorize_static"):
                    code = quality_gate.main([
                        "-g", "ProjectManagerAgent", "--profile", "full", "--fail-fast",
                    ])
        self.assertEqual(1, code)
        self.assertEqual(1, run.call_count)

    def test_full_profile_runs_unittest_modules_from_tests_directory(self) -> None:
        completed = mock.Mock(returncode=0, stdout="ok\n")
        with mock.patch.object(quality_gate.subprocess, "run", return_value=completed) as run:
            with mock.patch.object(quality_gate, "test_modules", return_value=["test_one"]):
                with mock.patch.object(quality_gate, "test_ids", return_value=["test_one.Case.test_a"]):
                    with mock.patch.object(quality_gate.agent_context, "authorize_static"):
                        code = quality_gate.main(["-g", "ProjectManagerAgent", "--profile", "full"])
        self.assertEqual(0, code)
        unit_call = run.call_args_list[-1]
        self.assertEqual(quality_gate.ROOT, unit_call.kwargs["cwd"])
        self.assertEqual(quality_gate.sys.executable, unit_call.args[0][0])
        self.assertIn(str(quality_gate.UNITTEST_WORKER), unit_call.args[0])
        self.assertIn("test_one", unit_call.args[0])

    def test_test_modules_are_discovered_only_from_tests_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "scripts").mkdir()
            (root / "tests").mkdir()
            (root / "scripts" / "runtime.py").write_text("", encoding="utf-8")
            (root / "tests" / "test_real.py").write_text("", encoding="utf-8")
            self.assertEqual(["test_real"], quality_gate.test_modules(root))

    def test_test_ids_returns_individual_cases(self) -> None:
        ids = quality_gate.test_ids("test_quality_gate")
        self.assertTrue(any(item.endswith("test_clean_transient_removes_only_known_generated_artifacts") for item in ids))

    def test_discover_tests_marks_function_and_class_slow_decorators(self) -> None:
        tree = ast.parse("""
@slow
def test_function():
    pass

@module.slow
class SlowCase:
    def test_method(self):
        pass

class MixedCase:
    @slow()
    def test_marked(self):
        pass
""")
        functions = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))]
        self.assertTrue(quality_gate._has_slow_decorator(functions[0]))
        self.assertTrue(quality_gate._has_slow_decorator(functions[1]))
        self.assertTrue(quality_gate._has_slow_decorator(functions[2].body[0]))

    def test_test_ids_can_exclude_slow_tests_without_importing_module(self) -> None:
        all_ids = quality_gate.test_ids("test_merge_docs")
        fast_ids = quality_gate.test_ids("test_merge_docs", include_slow=False)
        self.assertTrue(all_ids)
        self.assertLess(len(fast_ids), len(all_ids))
        self.assertTrue(set(fast_ids).issubset(all_ids))
        self.assertFalse(any(item.endswith("test_closed_lightweight_mode_can_preview_document_archive") for item in fast_ids))
        self.assertTrue(any(item.endswith("test_preview_apply_and_idempotency") for item in fast_ids))

    def test_archive_rejection_integration_cases_are_slow_except_authorization_short_circuit(self) -> None:
        all_ids = quality_gate.test_ids("test_merge_docs", include_slow=True)
        fast_ids = quality_gate.test_ids("test_merge_docs", include_slow=False)
        archive_ids = [item for item in all_ids if ".test_archive_rejects_" in item]
        fast_archive_ids = [item for item in fast_ids if ".test_archive_rejects_" in item]

        self.assertEqual(15, len(archive_ids))
        self.assertEqual(
            ["test_merge_docs.MergeDocsTest.test_archive_rejects_wrong_current_agent"],
            fast_archive_ids,
        )
        self.assertTrue(set(archive_ids) - set(fast_archive_ids) <= set(all_ids) - set(fast_ids))

    def test_fast_batches_target_individual_cases_and_skip_empty_modules(self) -> None:
        discovered = {
            "test_one": ["test_one.Case.test_fast"],
            "test_two": [],
        }
        batches = quality_gate.unit_batch_definitions(
            ["test_one", "test_two"], discovered, individual_targets=True,
        )
        self.assertEqual([["test_one.Case.test_fast"]], [item["targets"] for item in batches])
        self.assertEqual(1, batches[0]["testCaseCount"])

    def test_summary_reports_test_counts_and_timing(self) -> None:
        summary = quality_gate.summarize_steps([
            {"name": "check_skill", "category": "static", "status": "PASSED", "durationMs": 12.5},
            {
                "name": "unit:test_one", "category": "unit", "status": "FAILED",
                "durationMs": 40.0, "testCaseCount": 3,
            },
        ])
        self.assertEqual(2, summary["stepCount"])
        self.assertEqual(1, summary["failedSteps"])
        self.assertEqual(3, summary["unitTestCases"])
        self.assertEqual("unit:test_one", summary["slowestSteps"][0]["name"])


    def test_subprocess_environment_disables_notebook_preheat(self) -> None:
        completed = mock.Mock(returncode=0, stdout="ok\n")
        with mock.patch.object(quality_gate.subprocess, "run", return_value=completed) as run:
            with mock.patch.object(quality_gate.agent_context, "authorize_static"):
                code = quality_gate.main(["-g", "ProjectManagerAgent", "--profile", "static"])
        self.assertEqual(0, code)
        for call in run.call_args_list:
            self.assertEqual("0", call.kwargs["env"]["OAI_IS_JUPYTER_KERNEL"])
            self.assertEqual("1", call.kwargs["env"]["PYTHONDONTWRITEBYTECODE"])
            self.assertNotIn("PYTHONPYCACHEPREFIX", call.kwargs["env"])


    def test_default_merge_docs_batches_use_bounded_groups(self) -> None:
        discovered = {"test_merge_docs": [
            "test_merge_docs.Case.test_a",
            "test_merge_docs.Case.test_b",
        ]}
        batches = quality_gate.unit_batch_definitions(["test_merge_docs"], discovered)
        self.assertEqual([2], [item["testCaseCount"] for item in batches])
        self.assertTrue(all(len(item["targets"]) <= quality_gate.MERGE_DOCS_BATCH_SIZE for item in batches))
        script = quality_gate.build_unit_worker_script(batches, fail_fast=False)
        self.assertIn(f"timeout --foreground {quality_gate.UNIT_BATCH_TIMEOUT_SECONDS}s", script)
        self.assertIn('PYTHONDONTWRITEBYTECODE=1', script)
        self.assertNotIn('PYTHONPYCACHEPREFIX', script)

    def test_merge_docs_cases_are_split_into_fixed_batches(self) -> None:
        ids = [f"test_merge_docs.Case.test_{index:02d}" for index in range(21)]
        batches = quality_gate.chunked_test_ids(ids, 10)
        self.assertEqual([10, 10, 1], [len(batch) for batch in batches])
        self.assertEqual(ids, [item for batch in batches for item in batch])

    def test_summary_counts_batched_module_once(self) -> None:
        summary = quality_gate.summarize_steps([
            {"name": "unit:test_merge_docs:batch-01", "category": "unit", "status": "PASSED", "durationMs": 1, "testCaseCount": 10, "testModules": ["test_merge_docs"]},
            {"name": "unit:test_merge_docs:batch-02", "category": "unit", "status": "PASSED", "durationMs": 1, "testCaseCount": 5, "testModules": ["test_merge_docs"]},
        ])
        self.assertEqual(1, summary["unitTestModules"])
        self.assertEqual(15, summary["unitTestCases"])


    def test_known_unit_modules_are_grouped_without_losing_order(self) -> None:
        modules = [
            "test_agent_context", "test_ai_context", "test_merge_docs",
            "test_request_intake", "test_wk",
        ]
        groups = quality_gate.unit_module_groups(modules)
        flattened = [item for group in groups for item in group]
        self.assertEqual(["test_agent_context", "test_ai_context", "test_request_intake", "test_wk"], flattened)
        self.assertNotIn("test_merge_docs", flattened)


    def test_unit_worker_output_is_parsed_into_structured_steps(self) -> None:
        batches = [{
            "name": "unit:group-01",
            "targets": ["test_one"],
            "testModules": ["test_one"],
            "testCaseCount": 2,
        }]
        steps = quality_gate.parse_unit_worker_output(
            "__QG_STEP__|unit:group-01|1|123|2|test_one\nfailure details\n",
            batches,
        )
        self.assertEqual(1, len(steps))
        self.assertEqual("FAILED", steps[0]["status"])
        timeout_steps = quality_gate.parse_unit_worker_output(
            "__QG_STEP__|unit:group-01|124|240000|2|test_one\n",
            batches,
        )
        self.assertEqual("TIMEOUT", timeout_steps[0]["status"])
        self.assertEqual(2, steps[0]["testCaseCount"])
        self.assertEqual("failure details", steps[0]["output"])


if __name__ == "__main__":
    unittest.main()
