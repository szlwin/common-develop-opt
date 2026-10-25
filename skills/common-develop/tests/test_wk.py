#!/usr/bin/env python3
"""Tests for the unified wk entry point."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import wk
import work_mode
import doctor


class WKTests(unittest.TestCase):
    def base_args(self, root: Path, *, apply: bool) -> list[str]:
        args = [
            "start", "-g", "ProjectManagerAgent", "--doc-root", str(root), "--version", "V_TEST",
            "-f", "优化页面标题颜色", "--classification", "simple-optimization",
            "--related-feature", "PRD0013-F01",
        ]
        for flag in (
            "business-logic", "data-model", "api-contract", "permission", "workflow",
            "persistence", "external-side-effect", "migration", "dependency",
        ):
            args.extend([f"--{flag}-impact", "NONE"])
        if apply:
            args.append("--apply")
        return args

    def test_start_defaults_to_preview(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, wk.main(self.base_args(Path(temp) / "project_doc", apply=False)))
            payload = json.loads(output.getvalue())
            self.assertEqual("PREVIEW", payload["status"])
            self.assertFalse(Path(payload["taskDir"]).exists())

    def test_start_parser_uses_incremental_patch_and_syncs_by_default(self) -> None:
        parsed = wk.build_parser().parse_args(self.base_args(Path("project_doc"), apply=False) + ["-mc", "--no-auto", "-wc"])
        current = work_mode.WorkMode(
            model_code=False, auto=True, git_checkpoint=True, lightweight=True
        )

        resolved_from_wk = wk._mode_from_args(parsed, current)
        resolved_from_work_mode = work_mode.mode_from_args(parsed, current)

        self.assertEqual(resolved_from_work_mode, resolved_from_wk)
        self.assertTrue(resolved_from_wk.model_code)
        self.assertFalse(resolved_from_wk.auto)
        self.assertTrue(resolved_from_wk.git_checkpoint)
        self.assertFalse(resolved_from_wk.lightweight)
        self.assertTrue(parsed.sync_existing_tasks)

        no_sync = wk.build_parser().parse_args(
            self.base_args(Path("project_doc"), apply=False) + ["--no-sync-existing-tasks"]
        )
        self.assertFalse(no_sync.sync_existing_tasks)

    def test_start_apply_syncs_existing_tasks_by_default(self) -> None:
        import long_task
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            long_task.command_init(argparse.Namespace(
                doc_root=str(root), version="V_TEST", target="EXISTING", reuse=False, agent="ProjectManagerAgent"
            ))

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(
                    0, wk.main(self.base_args(root, apply=True) + ["-auto"] + ["--no-sync-existing-tasks"]),
                )

            existing = root / "version" / "V_TEST" / "task" / "EXISTING"
            self.assertFalse(long_task.runtime_state(existing)["work_mode"]["auto"])

            # A second version proves the default path without the opt-out flag.
            root_default = Path(temp) / "project_doc_default"
            long_task.command_init(argparse.Namespace(
                doc_root=str(root_default), version="V_TEST", target="EXISTING", reuse=False, agent="ProjectManagerAgent"
            ))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, wk.main(self.base_args(root_default, apply=True) + ["-auto"]))

            existing_default = root_default / "version" / "V_TEST" / "task" / "EXISTING"
            self.assertTrue(long_task.runtime_state(existing_default)["work_mode"]["auto"])

    def test_start_apply_creates_valid_task_and_next_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, wk.main(self.base_args(root, apply=True)))
            # long_task init prints its task path before the final JSON; parse the final object.
            text = output.getvalue()
            payload = json.loads(text[text.index("{"):])
            task_dir = Path(payload["taskDir"])
            self.assertTrue((task_dir / "request_intake.json").is_file())
            next_output = io.StringIO()
            with contextlib.redirect_stdout(next_output):
                self.assertEqual(0, wk.main(["next", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir)]))
            next_payload = json.loads(next_output.getvalue())
            self.assertIn("recommendedCommand", next_payload)
            self.assertEqual("development", next_payload["currentGate"])


    def test_doctor_is_read_only_and_reports_json_health_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = wk.main([
                    "doctor", "-g", "ProjectManagerAgent",
                    "--doc-root", str(root), "--version", "V_TEST", "--json",
                ])
            self.assertEqual(1, code)
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["readOnly"])
            self.assertEqual("DRIFT", payload["status"])
            self.assertTrue(any(item["id"] == "work-file" for item in payload["checks"]))
            self.assertFalse(root.exists())

    def test_doctor_reports_valid_project_and_does_not_call_mutators(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            with contextlib.redirect_stdout(io.StringIO()):
                wk.main(self.base_args(root, apply=True))
            with mock.patch.object(doctor.work_mode, "ensure_work_file", side_effect=AssertionError("mutator called")):
                with mock.patch.object(doctor.long_task, "command_sync_work_mode", side_effect=AssertionError("mutator called")):
                    payload = doctor.inspect(root, "V_TEST")
            self.assertTrue(payload["readOnly"])
            self.assertIn(payload["status"], {"PASSED", "DRIFT"})
            self.assertFalse(any(item["status"] == "FAILED" for item in payload["checks"] if item["id"] == "work-file"))

            task_id = next(path.name for path in (root / "version" / "V_TEST" / "task").iterdir() if path.is_dir())
            ids = {item["id"] for item in payload["checks"]}
            expected_prefixes = {
                "task-schema:", "task-current-phase:", "task-mode-sync:",
                "mode-profile-compatibility:", "request-intake:", "current-manifest:",
            }
            for prefix in expected_prefixes:
                self.assertIn(prefix + task_id, ids)
            self.assertIn("archive-plan", ids)
            self.assertIn("archive-manifest", ids)
            archive_manifest = next(item for item in payload["checks"] if item["id"] == "archive-manifest")
            self.assertEqual("SKIPPED", archive_manifest["status"])
            self.assertEqual("DOCTOR_ARCHIVE_MANIFEST_NOT_APPLICABLE", archive_manifest["code"])
            self.assertIn("work-mode-events", ids)

    def test_doctor_reports_project_constraints_without_mutating_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            env = root / "project_env.md"
            env.parent.mkdir(parents=True)
            (root / "policy.md").write_text("policy\n", encoding="utf-8")
            env.write_text(
                "```json common-develop-constraints\n"
                + json.dumps({"constraints": [{
                    "id": "DATA-SAFETY",
                    "type": "HARD",
                    "statement": "数据变更必须经过测试门禁",
                    "owner": "ProjectManagerAgent",
                    "source": "policy.md",
                    "gate": "testing",
                    "status": "VALIDATED",
                }]}, ensure_ascii=False)
                + "\n```\n",
                encoding="utf-8",
            )
            before = env.read_bytes()
            payload = doctor.inspect(root, "V_TEST")
            finding = next(item for item in payload["checks"] if item["id"] == "project-constraints")
            self.assertEqual("PASSED", finding["status"])
            self.assertEqual(before, env.read_bytes())
            self.assertTrue(payload["readOnly"])

    def test_doctor_marks_invalid_task_runtime_as_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            with contextlib.redirect_stdout(io.StringIO()):
                wk.main(self.base_args(root, apply=True))
            task_dir = next((root / "version" / "V_TEST" / "task").iterdir())
            (task_dir / "task_events.jsonl").write_text("not-json\n", encoding="utf-8")
            payload = doctor.inspect(root, "V_TEST")
            item = next(item for item in payload["checks"] if item["id"].startswith("task-runtime:"))
            self.assertEqual("FAILED", item["status"])
            self.assertEqual("DOCTOR_TASK_RUNTIME_INVALID", item["code"])

    def test_doctor_contains_yaml_parse_failures_in_archive_plan_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            plan = root / "version" / "V_TEST" / "doc" / "orders" / "generated" / "merge_plan.yaml"
            plan.parent.mkdir(parents=True)
            plan.write_text("archiveGate: [unterminated\n", encoding="utf-8")

            payload = doctor.inspect(root, "V_TEST")
            finding = next(item for item in payload["checks"] if item["id"] == "archive-plan:orders")
            self.assertEqual("FAILED", finding["status"])
            self.assertEqual("DOCTOR_ARCHIVE_PLAN_INVALID", finding["code"])
            self.assertIn("YAML", finding["message"])

    def test_doctor_reports_missing_archive_manifest_after_plan_generation_as_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            plan = root / "version" / "V_TEST" / "doc" / "orders" / "generated" / "merge_plan.yaml"
            plan.parent.mkdir(parents=True)
            plan.write_text("version: V_TEST\narchiveGate:\n  status: PENDING\n", encoding="utf-8")

            payload = doctor.inspect(root, "V_TEST")
            finding = next(item for item in payload["checks"] if item["id"] == "archive-manifest")
            self.assertEqual("DRIFT", finding["status"])
            self.assertEqual("DOCTOR_ARCHIVE_MANIFEST_MISSING", finding["code"])

    def test_doctor_reports_invalid_archive_manifest_content(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            root.mkdir(parents=True)
            (root / "archive_manifest.yaml").write_text(
                "schemaVersion: 1\nappliedChangeSets: [\n", encoding="utf-8"
            )

            payload = doctor.inspect(root, "V_TEST")
            finding = next(item for item in payload["checks"] if item["id"] == "archive-manifest")
            self.assertEqual("FAILED", finding["status"])
            self.assertEqual("DOCTOR_ARCHIVE_MANIFEST_INVALID", finding["code"])

    def test_doctor_reports_invalid_version_work_mode_event_chain_with_stable_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            with contextlib.redirect_stdout(io.StringIO()):
                wk.main(self.base_args(root, apply=True) + ["-auto"])
            event_path = root / "version" / "V_TEST" / "work_mode_events.jsonl"
            self.assertTrue(event_path.is_file())
            event = json.loads(event_path.read_text(encoding="utf-8"))
            event["after"]["auto"] = not event["after"].get("auto", False)
            event_path.write_text(json.dumps(event) + "\n", encoding="utf-8")

            payload = doctor.inspect(root, "V_TEST")
            finding = next(item for item in payload["checks"] if item["id"] == "work-mode-events")
            self.assertEqual("FAILED", finding["status"])
            self.assertEqual("WORKMODE_EVENT_LEDGER_INVALID", finding["code"])
            self.assertTrue(payload["readOnly"])

    def test_doctor_parser_and_contract_catalog_include_command(self) -> None:
        parsed = wk.build_parser().parse_args([
            "doctor", "-g", "ProjectManagerAgent",
            "--doc-root", "project_doc", "--version", "V_TEST",
        ])
        self.assertEqual("doctor", parsed.command)
        catalog = wk.runtime_contract.command_catalog()
        self.assertTrue(any(item.get("id") == "doctor" and item.get("category") == "orchestration" for item in catalog))

    def test_simple_optimization_rejects_minimal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            args = self.base_args(Path(temp) / "project_doc", apply=False) + ["-min"]
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                self.assertEqual(1, wk.main(args))
            self.assertIn("incompatible", error.getvalue())

    def test_workflow_mode_error_preserves_domain_code_in_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            args = self.base_args(Path(temp) / "project_doc", apply=False) + ["-ws", "--json"]
            error = io.StringIO()
            with contextlib.redirect_stderr(error):
                self.assertEqual(1, wk.main(args))

            payload = json.loads(error.getvalue())
            self.assertEqual("FAILED", payload["status"])
            self.assertEqual("WORKMODE_PROFILE_INCOMPATIBLE", payload["error"]["code"])
            self.assertNotIn("WORKMODE_PROFILE_INCOMPATIBLE:", payload["error"]["message"])

    def test_help_discovers_flags_from_runtime_contract(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, wk.main(["help", "-g", "ProjectManagerAgent", "--flag", "-ar", "--json"]))
        payload = json.loads(output.getvalue())
        self.assertEqual(1, payload["count"])
        self.assertEqual("architectureReview", payload["commands"][0]["id"])
        self.assertEqual("assets/runtime-contract.json#/commandInterface", payload["machineSource"])

    def test_help_category_lists_request_flags(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, wk.main(["help", "-g", "ProjectManagerAgent", "--category", "request", "--json"]))
        flags = {flag for item in json.loads(output.getvalue())["commands"] for flag in item["flags"]}
        self.assertTrue({"-b", "-f"} <= flags)

    def test_review_next_command_requires_an_isolated_execution_context(self) -> None:
        command = wk._structured_next_command({
            "decision": {"classification": "REVIEW_REQUIRED"},
            "recommendedCommand": "python3 scripts/manual_review.py prepare",
            "requiredAgent": "ArchitectureReviewAgent",
            "allowedCommands": ["manual_review.prepare"],
        })
        self.assertEqual("ISOLATED_REVIEW_SESSION_REQUIRED", command["executionContext"])
        self.assertFalse(command["ownerContextReuseAllowed"])

    def test_packs_resolves_internal_modules_through_unified_entry(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, wk.main([
                "packs", "-g", "ProjectManagerAgent", "--task-type", "design",
                "--phase", "design", "--json",
            ]))
        payload = json.loads(output.getvalue())
        self.assertEqual("common-develop", payload["singleAuthoritySkill"])
        self.assertEqual("ATOMIC", payload["releaseUnit"])
        self.assertEqual(
            ["document-facts", "task-runtime", "review-verification"],
            [item["id"] for item in payload["packs"]],
        )

    def test_status_next_context_are_reducer_driven_when_state_cache_is_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, wk.main(self.base_args(root, apply=True)))
            payload = json.loads(output.getvalue()[output.getvalue().index("{"):])
            task_dir = Path(payload["taskDir"])
            cache_state = task_dir / "cache" / "current_manifest.json"
            self.assertTrue(cache_state.is_file())
            cache_state.unlink()

            status_out = io.StringIO()
            with contextlib.redirect_stdout(status_out):
                self.assertEqual(0, wk.main(["status", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir), "--full"]))
            status = json.loads(status_out.getvalue())
            self.assertNotEqual("INVALID", status["status"])
            self.assertEqual("development", status["taskState"]["current_phase"])

            summary_out = io.StringIO()
            with contextlib.redirect_stdout(summary_out):
                self.assertEqual(0, wk.main([
                    "status", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir),
                    "--summary", "--json",
                ]))
            summary = json.loads(summary_out.getvalue())
            self.assertEqual("development", summary["currentPhase"])
            self.assertIn("nextAgent", summary)
            self.assertFalse(summary["diagnosticsRun"])
            self.assertNotIn("currentManifest", summary)
            self.assertNotIn("health", summary)

            next_out = io.StringIO()
            with contextlib.redirect_stdout(next_out):
                self.assertEqual(0, wk.main(["next", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir)]))
            next_payload = json.loads(next_out.getvalue())
            self.assertEqual("development", next_payload["currentGate"])

            context_out = io.StringIO()
            with contextlib.redirect_stdout(context_out):
                self.assertEqual(0, wk.main(["context", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir), "--json"]))
            context = json.loads(context_out.getvalue())
            self.assertEqual("task_events.jsonl reducer", context["source"])
            self.assertEqual("development", context["currentPhase"])

    def test_auto_open_p1_routes_to_remediation_before_blocked_exit(self) -> None:
        payload = {
            "status": "BLOCKED",
            "errors": [],
            "openCriticalIssueIds": ["ISSUE-P1-001"],
            "autoExecution": {"enabled": True, "batchForward": "FROZEN", "remediationActive": True},
        }
        output = io.StringIO()
        with mock.patch.object(wk, "_status_payload", return_value=payload):
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, wk.main([
                    "next", "-g", "ProjectManagerAgent", "--task-dir", "/tmp/fake-task"
                ]))
        decision = json.loads(output.getvalue())
        self.assertEqual("AUTO_REMEDIATION", decision["currentGate"])
        self.assertIn("AUTO_BATCH_FORWARD_FROZEN", decision["reasonCodes"])
        self.assertIn("auto_remediation.py classify", decision["recommendedCommand"])


if __name__ == "__main__":
    unittest.main()
