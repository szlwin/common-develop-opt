#!/usr/bin/env python3
"""Tests for wk -b / wk -f request classification and binding."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import long_task
import request_intake
import wk
from _long_task import task_events


class RequestIntakeTests(unittest.TestCase):
    def test_bug_description_is_always_bug_flow_with_stable_id(self) -> None:
        first = request_intake.classify_bug("登录按钮点击无反应", agent="ProjectManagerAgent")
        second = request_intake.classify_bug("  登录按钮点击无反应  ", agent="ProjectManagerAgent")
        self.assertEqual("BUG", first["classification"])
        self.assertEqual("BUG_FLOW", first["workflowProfile"])
        self.assertEqual("DESCRIPTION", first["inputType"])
        self.assertEqual(first["targetId"], second["targetId"])
        self.assertTrue(first["targetId"].startswith("BUG-DESC-"))

    def test_bug_id_is_preserved(self) -> None:
        result = request_intake.classify_bug("BUG-LOGIN-001", agent="ProjectManagerAgent")
        self.assertEqual("BUG-LOGIN-001", result["targetId"])
        self.assertEqual("ID", result["inputType"])

    def test_feature_description_defaults_to_blocked_confirmation(self) -> None:
        result = request_intake.classify_feature("优化标题颜色", agent="ProjectManagerAgent")
        self.assertEqual("NEEDS_CONFIRMATION", result["classification"])
        self.assertEqual("BLOCKED", result["workflowProfile"])
        self.assertEqual("", result["entryPhase"])

    def test_existing_feature_id_is_preserved(self) -> None:
        result = request_intake.classify_feature("PRD0013-F01", agent="ProjectManagerAgent")
        self.assertEqual("PRD0013-F01", result["targetId"])
        self.assertEqual("EXISTING_FEATURE", result["classification"])
        self.assertEqual("FEATURE_FLOW", result["workflowProfile"])

    def test_simple_optimization_requires_related_feature_and_all_none(self) -> None:
        with self.assertRaisesRegex(request_intake.RequestIntakeError, "relatedFeature"):
            request_intake.classify_feature(
                "优化标题颜色", agent="ProjectManagerAgent",
                classification="simple-optimization",
                impact_assessment=request_intake.default_impact("NONE"),
            )
        impacts = request_intake.default_impact("NONE")
        impacts["businessLogic"] = "CHANGED"
        with self.assertRaisesRegex(request_intake.RequestIntakeError, "every impact"):
            request_intake.classify_feature(
                "优化标题颜色", agent="ProjectManagerAgent",
                classification="simple-optimization", related_feature="PRD0013-F01",
                impact_assessment=impacts,
            )

    def test_simple_optimization_binds_directly_to_development_with_skipped_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            intake = request_intake.classify_feature(
                "优化标题颜色", agent="ProjectManagerAgent",
                classification="simple-optimization", related_feature="PRD0013-F01",
                impact_assessment=request_intake.default_impact("NONE"),
                reason="Only presentation changes are required.",
            )
            intake_path = Path(temp) / "intake.json"
            request_intake.write(intake_path, intake)
            args = argparse.Namespace(
                doc_root=str(root), version="V_TEST", target=intake["targetId"],
                reuse=False, request_intake=str(intake_path), agent="ProjectManagerAgent",
            )
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, long_task.command_init(args))
            task_dir = root / "version" / "V_TEST" / "task" / intake["targetId"]
            state = long_task.runtime_state(task_dir)
            outcomes = long_task.load_task_history(task_dir)["outcomes"]
            self.assertEqual("development", state["current_phase"])
            self.assertEqual("SIMPLE_OPTIMIZATION_FLOW", state["request_intake"]["workflow_profile"])
            self.assertEqual(set(request_intake.SIMPLE_SKIPPED_PHASES), {
                phase for phase, item in state["artifact_revisions"].items()
                if item["status"] == "NOT_APPLICABLE"
            })
            self.assertEqual(set(request_intake.SIMPLE_SKIPPED_PHASES), {
                item["phase"] for item in outcomes if item["status"] == "SKIPPED"
            })
            errors, _, _, _ = long_task.validate_task(task_dir)
            self.assertEqual([], errors)

    def test_simple_optimization_rejects_minimal_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            target = "FEATURE-DESC-TEST00000001"
            with contextlib.redirect_stdout(io.StringIO()):
                long_task.command_init(argparse.Namespace(
                    doc_root=str(root), version="V_TEST", target=target,
                    reuse=False, request_intake="", agent="ProjectManagerAgent",
                ))
            task_dir = root / "version" / "V_TEST" / "task" / target
            work_path = root / "version" / "V_TEST" / "work.md"
            long_task.work_mode.apply_mode(work_path, long_task.work_mode.WorkMode(minimal=True))
            with contextlib.redirect_stdout(io.StringIO()):
                long_task.command_sync_work_mode(argparse.Namespace(task_dir=str(task_dir), agent="ProjectManagerAgent"))
            intake = request_intake.classify_feature(
                "优化标题颜色", agent="ProjectManagerAgent",
                classification="simple-optimization", related_feature="PRD0013-F01",
                impact_assessment=request_intake.default_impact("NONE"),
            )
            intake["targetId"] = target
            with self.assertRaisesRegex(request_intake.RequestIntakeError, "wk -min is incompatible with SIMPLE_OPTIMIZATION_FLOW"):
                request_intake.bind_to_task(task_dir, intake, agent="ProjectManagerAgent")


if __name__ == "__main__":
    unittest.main()

class RequestIntakeReclassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project_doc"
        self.intake = request_intake.classify_feature(
            "优化标题颜色", agent="ProjectManagerAgent",
            classification="simple-optimization", related_feature="PRD0013-F01",
            impact_assessment=request_intake.default_impact("NONE"),
        )
        intake_path = Path(self.temp.name) / "intake.json"
        request_intake.write(intake_path, self.intake)
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.command_init(argparse.Namespace(
                doc_root=str(self.root), version="V_TEST", target=self.intake["targetId"],
                reuse=False, request_intake=str(intake_path), agent="ProjectManagerAgent",
            ))
        self.task_dir = self.root / "version" / "V_TEST" / "task" / self.intake["targetId"]

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _confirmation_ref(self, reason: str) -> str:
        with contextlib.redirect_stdout(io.StringIO()):
            wk.command_confirm(argparse.Namespace(
                task_dir=str(self.task_dir), until="", reopen_from="requirement_confirmation",
                changed_phase="development", source_revision="DEV-R01@observed",
                reason=reason, agent="ProjectManagerAgent", preview=False, apply=True, json=True,
            ))
        return task_events.read_events(self.task_dir)[-1]["payload"]["requestFingerprint"]

    def test_reclassify_preserves_history_and_reopens_requirement_confirmation(self) -> None:
        cache_state = self.task_dir / "cache" / "task_state.md"
        cache_state.unlink(missing_ok=True)
        reason = "Implementation revealed a business rule change."
        confirmation_ref = self._confirmation_ref(reason)
        updated = request_intake.reclassify_task(
            self.task_dir, agent="ProjectManagerAgent", classification="requirement-change",
            reason=reason, related_requirement="PRD0013", evidence_refs=["EVD-000001"],
            confirmation_ref=confirmation_ref,
        )
        self.assertEqual("REQUIREMENT_CHANGE", updated["classification"])
        self.assertEqual(2, updated["classificationRevision"])
        self.assertTrue((self.task_dir / "request_intake_history" / "RI-0001.json").is_file())
        state = long_task.runtime_state(self.task_dir)
        self.assertEqual("requirement_confirmation", state["current_phase"])
        self.assertEqual("REWORK", state["task_status"])
        self.assertEqual("REQUIREMENT_CHANGE_FLOW", state["request_intake"]["workflow_profile"])
        self.assertEqual("IN_PROGRESS", state["artifact_revisions"]["requirement_confirmation"]["status"])
        self.assertEqual("STALE", state["artifact_revisions"]["development"]["status"])
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertEqual([], errors)

    def test_reclassify_cannot_create_its_own_boundary_authorization(self) -> None:
        before_events = task_events.read_events(self.task_dir)
        before_intake = request_intake.load(self.task_dir / "request_intake.json")
        with self.assertRaisesRegex(request_intake.RequestIntakeError, "existing --confirmation-ref"):
            request_intake.reclassify_task(
                self.task_dir, agent="ProjectManagerAgent", classification="requirement-change",
                reason="Implementation revealed a business rule change.", related_requirement="PRD0013",
            )
        self.assertEqual(before_events, task_events.read_events(self.task_dir))
        self.assertEqual(before_intake, request_intake.load(self.task_dir / "request_intake.json"))

    def test_reclassify_uses_shared_task_writer_lock_for_whole_transaction(self) -> None:
        reason = "Implementation revealed a business rule change."
        confirmation_ref = self._confirmation_ref(reason)
        intake_path = self.task_dir / "request_intake.json"
        before_intake = intake_path.read_bytes()
        before_events = task_events.event_path(self.task_dir).read_bytes()
        lock_path = self.task_dir / "cache" / "amend-phase.lock"
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, "task lifecycle writer"):
                request_intake.reclassify_task(
                    self.task_dir, agent="ProjectManagerAgent", classification="requirement-change",
                    reason=reason, related_requirement="PRD0013", confirmation_ref=confirmation_ref,
                )
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        self.assertEqual(before_intake, intake_path.read_bytes())
        self.assertEqual(before_events, task_events.event_path(self.task_dir).read_bytes())

    def test_reclassify_rollback_removes_new_history_file_and_directory(self) -> None:
        reason = "Implementation revealed a business rule change."
        confirmation_ref = self._confirmation_ref(reason)
        intake_path = self.task_dir / "request_intake.json"
        state_path = long_task.task_state_path(self.task_dir)
        event_path = task_events.event_path(self.task_dir)
        history_dir = self.task_dir / "request_intake_history"
        history_path = history_dir / "RI-0001.json"
        before_intake = intake_path.read_bytes()
        before_state = state_path.read_bytes()
        before_events = event_path.read_bytes()
        original_transactional_write = long_task.transactional_text_write

        def fail_after_history(payloads: dict[Path, str]) -> None:
            if history_path.exists():
                raise RuntimeError("injected final state write failure")
            original_transactional_write(payloads)

        with mock.patch.object(long_task, "transactional_text_write", side_effect=fail_after_history):
            with self.assertRaisesRegex(RuntimeError, "injected final state write failure") as raised:
                request_intake.reclassify_task(
                    self.task_dir, agent="ProjectManagerAgent", classification="requirement-change",
                    reason=reason, related_requirement="PRD0013", confirmation_ref=confirmation_ref,
                )

        self.assertEqual("SIMPLE_OPTIMIZATION", request_intake.load(intake_path)["classification"])
        self.assertEqual(before_intake, intake_path.read_bytes())
        self.assertEqual(before_state, state_path.read_bytes())
        self.assertEqual(before_events, event_path.read_bytes())
        self.assertFalse(history_path.exists())
        self.assertFalse(history_dir.exists())
        self.assertEqual("COMPLETE", raised.exception.rollback_details["rollbackStatus"])
        self.assertIn(str(history_dir), raised.exception.rollback_details["removedDirectories"])

        retried = request_intake.reclassify_task(
            self.task_dir, agent="ProjectManagerAgent", classification="requirement-change",
            reason=reason, related_requirement="PRD0013", confirmation_ref=confirmation_ref,
        )
        self.assertEqual("REQUIREMENT_CHANGE", retried["classification"])
        self.assertTrue(history_path.is_file())

    def test_reclassify_rollback_preserves_preexisting_history_directory(self) -> None:
        reason = "Implementation revealed a business rule change."
        confirmation_ref = self._confirmation_ref(reason)
        history_dir = self.task_dir / "request_intake_history"
        history_dir.mkdir()
        marker_path = history_dir / "existing-record.json"
        marker_path.write_text("existing\n", encoding="utf-8")
        history_path = history_dir / "RI-0001.json"
        original_transactional_write = long_task.transactional_text_write

        def fail_after_history(payloads: dict[Path, str]) -> None:
            if history_path.exists():
                raise RuntimeError("injected final state write failure")
            original_transactional_write(payloads)

        with mock.patch.object(long_task, "transactional_text_write", side_effect=fail_after_history):
            with self.assertRaisesRegex(RuntimeError, "injected final state write failure") as raised:
                request_intake.reclassify_task(
                    self.task_dir, agent="ProjectManagerAgent", classification="requirement-change",
                    reason=reason, related_requirement="PRD0013", confirmation_ref=confirmation_ref,
                )

        self.assertEqual("SIMPLE_OPTIMIZATION", request_intake.load(
            self.task_dir / "request_intake.json"
        )["classification"])
        self.assertFalse(history_path.exists())
        self.assertTrue(history_dir.is_dir())
        self.assertEqual("existing\n", marker_path.read_text(encoding="utf-8"))
        self.assertEqual([], raised.exception.rollback_details["removedDirectories"])

    def test_reclassify_rejects_invalid_target(self) -> None:
        with self.assertRaisesRegex(request_intake.RequestIntakeError, "target must"):
            request_intake.reclassify_task(
                self.task_dir, agent="ProjectManagerAgent", classification="simple-optimization",
                reason="invalid",
            )

    def test_same_phase_reclassify_does_not_require_or_accept_confirmation_ref(self) -> None:
        document, state = long_task.read_task_state_cache(self.task_dir, authoritative=True)
        state["current_phase"] = "requirement_confirmation"
        payloads, _, _ = long_task.append_task_events_payloads(
            self.task_dir, long_task.task_state_path(self.task_dir), document, state, [],
            synced_at=long_task.now(),
        )
        long_task.transactional_text_write(payloads)
        with self.assertRaisesRegex(request_intake.RequestIntakeError, "only valid for cross-phase"):
            request_intake.reclassify_task(
                self.task_dir, agent="ProjectManagerAgent", classification="requirement-change",
                reason="same phase", related_requirement="PRD0013", confirmation_ref="FAKE",
            )
        updated = request_intake.reclassify_task(
            self.task_dir, agent="ProjectManagerAgent", classification="requirement-change",
            reason="same phase", related_requirement="PRD0013",
        )
        self.assertEqual("REQUIREMENT_CHANGE", updated["classification"])
