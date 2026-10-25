#!/usr/bin/env python3
"""Tests for the bounded ``wk doctor --fix`` repair surface."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import current_manifest
import doctor
import long_task
import task_layout
import wk
import work_mode


class DoctorFixTests(unittest.TestCase):
    def _init_task(self, root: Path, target: str = "TASK") -> Path:
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.command_init(argparse.Namespace(
                doc_root=str(root),
                version="V_TEST",
                target=target,
                reuse=False,
                agent="ProjectManagerAgent",
            ))
        return root / "version" / "V_TEST" / "task" / target

    def _doctor_args(self, root: Path, *flags: str, agent: str = "ProjectManagerAgent") -> list[str]:
        return [
            "doctor", "-g", agent,
            "--doc-root", str(root), "--version", "V_TEST", "--json",
            *flags,
        ]

    def _json_command(self, args: list[str]) -> tuple[int, dict]:
        output = io.StringIO()
        error = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
            rc = wk.main(args)
        text = output.getvalue().strip()
        if not text:
            text = error.getvalue().strip()
        return rc, json.loads(text)

    def test_default_doctor_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            self._init_task(root)
            paths = sorted(path for path in root.rglob("*") if path.is_file())
            before = {path: path.read_bytes() for path in paths}
            rc, payload = self._json_command(self._doctor_args(root))
            after = {path: path.read_bytes() for path in paths}
            self.assertEqual(1, rc)  # request_intake is not bound by raw init.
            self.assertTrue(payload["readOnly"])
            self.assertEqual(before, after)

    def test_fix_preview_does_not_write_and_marks_unregistered_repairs_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            task_dir = self._init_task(root)
            manifest = current_manifest.manifest_path(task_dir)
            manifest.unlink()
            before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            rc, payload = self._json_command(self._doctor_args(root, "--fix"))
            after = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
            self.assertEqual(1, rc)
            self.assertEqual("PREVIEW", payload["status"])
            self.assertTrue(payload["readOnly"])
            self.assertEqual(before, after)
            layout = next(item for item in payload["repairs"] if item["id"] == "task-layout-sync")
            self.assertEqual("READY", layout["status"])
            self.assertTrue(layout["safe"])

    def test_fix_apply_executes_task_layout_sync_and_rechecking_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            task_dir = self._init_task(root)
            manifest = current_manifest.manifest_path(task_dir)
            manifest.unlink()
            rc, payload = self._json_command(self._doctor_args(root, "--fix", "--apply"))
            self.assertEqual(1, rc)  # the intentionally raw task still lacks intake.
            self.assertIn(payload["status"], {"PARTIAL", "FIXED"})
            self.assertTrue(manifest.is_file())
            self.assertTrue(any(item["id"] == "task-layout-sync" for item in payload["appliedRepairs"]))

            first = json.loads(manifest.read_text(encoding="utf-8"))
            second = json.loads(manifest.read_text(encoding="utf-8"))
            sync_result = task_layout.sync(task_dir)
            self.assertEqual(first, second)
            self.assertEqual(first, json.loads(manifest.read_text(encoding="utf-8")))
            self.assertEqual("SYNCED", sync_result["status"])

    def test_fix_apply_executes_mode_sync(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            task_dir = self._init_task(root)
            work_path = root / "version" / "V_TEST" / "work.md"
            work_mode.apply_mode_transaction(
                root,
                "V_TEST",
                work_path,
                work_mode.WorkMode(auto=True),
                sync_tasks=False,
                agent="ProjectManagerAgent",
            )
            before = long_task.runtime_state(task_dir)["work_mode"]
            self.assertFalse(before["auto"])
            preview = doctor.fix(root, "V_TEST", agent="ProjectManagerAgent", apply=False)
            self.assertEqual("PREVIEW", preview["status"])
            self.assertTrue(any(item["id"] == "sync-work-mode" for item in preview["repairs"]))
            applied = doctor.fix(root, "V_TEST", agent="ProjectManagerAgent", apply=True)
            self.assertTrue(any(item["id"] == "sync-work-mode" for item in applied["appliedRepairs"]))
            self.assertTrue(long_task.runtime_state(task_dir)["work_mode"]["auto"])
            self.assertEqual("PASSED", next(item for item in applied["checks"] if item["id"] == "task-mode-sync:TASK")["status"])

    def test_apply_requires_project_manager_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            self._init_task(root)
            rc, payload = self._json_command(self._doctor_args(root, "--fix", "--apply", agent="CompletionVerificationAgent"))
            self.assertEqual(1, rc)
            self.assertEqual("DOCTOR_FIX_AGENT_FORBIDDEN", payload["error"]["code"])

    def test_apply_flag_requires_fix(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            self._init_task(root)
            rc, payload = self._json_command(self._doctor_args(root, "--apply"))
            self.assertEqual(1, rc)
            self.assertEqual("DOCTOR_FIX_REQUIRES_FIX", payload["error"]["code"])

    def test_safe_repair_registry_matches_runtime_contract(self) -> None:
        contract = json.loads((Path(__file__).resolve().parent.parent / "assets" / "runtime-contract.json").read_text(encoding="utf-8"))
        policy = contract["commandInterface"]["orchestration"]["doctor"]["safeRepairPolicy"]
        self.assertEqual("--fix", policy["previewFlag"])
        self.assertEqual("--apply", policy["applyFlag"])
        self.assertEqual("ProjectManagerAgent", policy["requiresAgent"])
        self.assertEqual(set(doctor.SAFE_REPAIRS), set(policy["safeRepairs"]))
        forbidden = set(policy["forbiddenSideEffects"])
        for repair_id, definition in doctor.SAFE_REPAIRS.items():
            self.assertTrue(definition["idempotent"])
            self.assertEqual(forbidden, set(definition["forbiddenSideEffects"]))
            self.assertEqual(definition["commandId"], policy["safeRepairs"][repair_id]["command"])

    def test_unregistered_repairs_are_blocked_in_preview_plan(self) -> None:
        repair = doctor._repair_record({
            "id": "work-file",
            "message": "缺少 work.md",
            "repairCommand": "python3 scripts/work_mode.py init --doc-root /tmp/project",
        })
        plan = {}
        doctor._merge_repair_plan(plan, [repair], status="READY")
        item = next(iter(plan.values()))
        self.assertEqual("BLOCKED", item["status"])
        self.assertFalse(item["safe"])
        self.assertFalse(item["registered"])

    def test_only_registered_repairs_are_executable(self) -> None:
        self.assertEqual({"sync-work-mode", "task-layout-sync"}, set(doctor.SAFE_REPAIRS))
        forbidden = {"reopen-phase", "skip-phase", "set-task-policy", "git-push", "archive"}
        self.assertTrue(forbidden.isdisjoint(doctor.SAFE_REPAIRS))
        blocked = doctor._repair_record({
            "id": "task-runtime:TASK",
            "message": "人工验证",
            "repairCommand": "python3 scripts/long_task.py reopen-phase --task-dir /tmp/task",
        })
        self.assertFalse(blocked["safe"])
        self.assertFalse(blocked["registered"])
        with self.assertRaises(doctor.DoctorFixError):
            doctor._invoke_safe_repair(blocked, agent="ProjectManagerAgent")

    def test_failed_repair_is_not_reported_as_applied(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            task_dir = self._init_task(root)
            manifest = current_manifest.manifest_path(task_dir)
            manifest.unlink()
            with mock.patch.object(doctor, "_invoke_safe_repair", side_effect=RuntimeError("synthetic failure")):
                payload = doctor.fix(root, "V_TEST", agent="ProjectManagerAgent", apply=True)
            self.assertEqual("FAILED", payload["status"])
            self.assertFalse(payload["appliedRepairs"])
            self.assertEqual("FAILED", payload["failedRepairs"][0]["status"])
            self.assertFalse(manifest.exists())

    def test_apply_rechecks_after_each_repair_and_can_queue_new_safe_finding(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            self._init_task(root)
            first = {"status": "DRIFT", "repairs": [{
                "id": "sync-work-mode", "check": "task-mode-sync:TASK", "taskDir": str(root / "task"),
                "safe": True, "registered": True,
            }]}
            # This is a focused orchestration test: the first inspection
            # exposes one repair, the post-repair inspection exposes another,
            # and the final inspection is clean.
            second = {"status": "DRIFT", "repairs": [{
                "id": "task-layout-sync", "check": "current-manifest:TASK", "taskDir": str(root / "task"),
                "safe": True, "registered": True,
            }]}
            clean = {"status": "PASSED", "repairs": []}
            with mock.patch.object(doctor, "inspect", side_effect=[first, second, clean, clean]) as inspect:
                with mock.patch.object(doctor, "_invoke_safe_repair", return_value={}) as invoke:
                    payload = doctor.fix(root, "V_TEST", agent="ProjectManagerAgent", apply=True)
            self.assertEqual("FIXED", payload["status"])
            self.assertEqual(2, invoke.call_count)
            self.assertEqual(4, inspect.call_count)


if __name__ == "__main__":
    unittest.main()
