"""Regression tests for reducer-first recovery and non-retroactive wk -wc."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import long_task
import work_mode


class CrossSessionLightweightTests(unittest.TestCase):
    def _init(self, root: Path) -> tuple[Path, Path, Path]:
        doc_root = root / "project_doc"
        long_task.command_init(argparse.Namespace(
            doc_root=str(doc_root), version="V_TEST", target="TASK-1",
            reuse=False, agent="ProjectManagerAgent",
        ))
        return (
            doc_root,
            doc_root / "version" / "V_TEST" / "work.md",
            doc_root / "version" / "V_TEST" / "task" / "TASK-1",
        )

    def test_sync_work_mode_ignores_stale_cache_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, work_path, task_dir = self._init(Path(temp))
            state_path = long_task.task_state_path(task_dir)
            document, cached = long_task.read_task_state_cache(task_dir)
            cached["current_phase"] = "testing"
            cached["next_agent"] = "TestAgent"
            state_path.write_text(json.dumps(cached, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            work_mode.apply_mode(work_path, work_mode.WorkMode(lightweight=True))
            long_task.command_sync_work_mode(argparse.Namespace(
                task_dir=str(task_dir), agent="ProjectManagerAgent",
            ))
            state = long_task.runtime_state(task_dir)
            self.assertEqual("requirement_confirmation", state["current_phase"])
            self.assertEqual("RequirementConfirmationAgent", state["next_agent"])

    def test_lightweight_and_architecture_review_sync_together(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, work_path, task_dir = self._init(Path(temp))
            work_mode.apply_mode(work_path, work_mode.WorkMode(lightweight=True, architecture_review=True))
            long_task.command_sync_work_mode(argparse.Namespace(
                task_dir=str(task_dir), agent="ProjectManagerAgent",
            ))
            state = long_task.runtime_state(task_dir)
            self.assertTrue(state["work_mode"]["lightweight"])
            self.assertTrue(state["work_mode"]["architecture_review"])
            self.assertTrue(state["architecture_review"]["enabled"])
            self.assertEqual("SKELETON", state["architecture_review"]["step"])

    def test_lightweight_can_be_closed_and_reenabled_without_losing_mode_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, work_path, task_dir = self._init(Path(temp))
            work_mode.apply_mode(work_path, work_mode.WorkMode(architecture_review=True, page_design=True))
            original_text = work_path.read_text(encoding="utf-8")

            work_mode.apply_mode(work_path, work_mode.WorkMode(architecture_review=True, page_design=True, lightweight=True))
            long_task.command_sync_work_mode(argparse.Namespace(task_dir=str(task_dir), agent="ProjectManagerAgent"))
            state = long_task.runtime_state(task_dir)
            self.assertTrue(state["work_mode"]["lightweight"])
            self.assertTrue(state["work_mode"]["architecture_review"])

            work_mode.apply_mode(work_path, work_mode.WorkMode(architecture_review=True, page_design=True))
            long_task.command_sync_work_mode(argparse.Namespace(task_dir=str(task_dir), agent="ProjectManagerAgent"))
            state = long_task.runtime_state(task_dir)
            self.assertFalse(state["work_mode"]["lightweight"])
            self.assertTrue(state["work_mode"]["architecture_review"])
            self.assertTrue(state["work_mode"]["lightweight_waived"])
            self.assertNotIn("common-develop-lightweight-backup", work_path.read_text(encoding="utf-8"))

            work_mode.apply_mode(work_path, work_mode.WorkMode(architecture_review=True, page_design=True, lightweight=True))
            long_task.command_sync_work_mode(argparse.Namespace(task_dir=str(task_dir), agent="ProjectManagerAgent"))
            state = long_task.runtime_state(task_dir)
            self.assertTrue(state["work_mode"]["lightweight"])
            self.assertTrue(state["work_mode"]["architecture_review"])
            self.assertTrue(state["work_mode"]["page_design"])
            self.assertNotEqual(original_text, work_path.read_text(encoding="utf-8"))

    def test_repeated_mode_sync_does_not_duplicate_skip_outcomes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, work_path, task_dir = self._init(Path(temp))
            work_mode.apply_mode(work_path, work_mode.WorkMode(lightweight=True))
            for _ in range(3):
                long_task.command_sync_work_mode(argparse.Namespace(task_dir=str(task_dir), agent="ProjectManagerAgent"))
            outcomes = long_task.load_task_history(task_dir)["outcomes"]
            for phase in set(work_mode.LIGHTWEIGHT_PHASES) & set(long_task.PHASES):
                self.assertEqual(1, sum(1 for item in outcomes if item.get("phase") == phase and item.get("status") == "SKIPPED"))
            # tdd is not a canonical artifact phase, but it is part of the
            # lightweight policy and therefore has no StageOutcome row.
            self.assertNotIn("tdd", {item.get("phase") for item in outcomes})

    def test_minimal_skip_provenance_still_blocks_pure_minimal_close(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, work_path, task_dir = self._init(Path(temp))
            work_mode.apply_mode(work_path, work_mode.WorkMode(minimal=True))
            long_task.command_sync_work_mode(argparse.Namespace(task_dir=str(task_dir), agent="ProjectManagerAgent"))
            work_mode.apply_mode(work_path, work_mode.WorkMode())
            with self.assertRaisesRegex(ValueError, "cannot disable wk -min"):
                long_task.command_sync_work_mode(argparse.Namespace(task_dir=str(task_dir), agent="ProjectManagerAgent"))

    def test_reopen_uses_reducer_iteration_after_stale_cache_edit(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, _, task_dir = self._init(Path(temp))
            state_path = long_task.task_state_path(task_dir)
            document, cached = long_task.read_task_state_cache(task_dir)
            cached["artifact_revisions"]["requirement_confirmation"]["iteration_no"] = 99
            state_path.write_text(json.dumps(cached, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                long_task.command_reopen_phase(argparse.Namespace(
                    task_dir=str(task_dir), from_phase="requirement_confirmation",
                    reason="跨会话恢复需求迭代", source_revision="", round="",
                    agent="ProjectManagerAgent",
                ))
            state = long_task.runtime_state(task_dir)
            self.assertEqual(
                2,
                state["artifact_revisions"]["requirement_confirmation"]["iteration_no"],
            )

    def test_lightweight_close_then_minimal_close_does_not_misattribute_skips(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, work_path, task_dir = self._init(Path(temp))
            work_mode.apply_mode(work_path, work_mode.WorkMode(lightweight=True))
            long_task.command_sync_work_mode(argparse.Namespace(
                task_dir=str(task_dir), agent="ProjectManagerAgent",
            ))
            work_mode.apply_mode(work_path, work_mode.WorkMode())
            long_task.command_sync_work_mode(argparse.Namespace(
                task_dir=str(task_dir), agent="ProjectManagerAgent",
            ))
            work_mode.apply_mode(work_path, work_mode.WorkMode(minimal=True))
            long_task.command_sync_work_mode(argparse.Namespace(
                task_dir=str(task_dir), agent="ProjectManagerAgent",
            ))
            work_mode.apply_mode(work_path, work_mode.WorkMode())
            long_task.command_sync_work_mode(argparse.Namespace(
                task_dir=str(task_dir), agent="ProjectManagerAgent",
            ))
            state = long_task.runtime_state(task_dir)
            self.assertFalse(state["work_mode"]["minimal"])
            self.assertEqual([], state["work_mode"]["minimal_skipped_phases"])
            self.assertEqual(
                {"test_design", "code_review", "testing"},
                {phase for phase, artifact in state["artifact_revisions"].items() if artifact["status"] == "NOT_APPLICABLE"},
            )

    def test_closing_lightweight_does_not_restore_review_or_test_gates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, work_path, task_dir = self._init(Path(temp))
            work_mode.apply_mode(work_path, work_mode.WorkMode(lightweight=True))
            long_task.command_sync_work_mode(argparse.Namespace(
                task_dir=str(task_dir), agent="ProjectManagerAgent",
            ))
            work_mode.apply_mode(work_path, work_mode.WorkMode())
            long_task.command_sync_work_mode(argparse.Namespace(
                task_dir=str(task_dir), agent="ProjectManagerAgent",
            ))
            errors, _, state, _ = long_task.validate_task(task_dir)
            self.assertEqual([], errors)
            self.assertFalse(state["work_mode"]["lightweight"])
            self.assertTrue(state["work_mode"]["lightweight_waived"])
            self.assertEqual(
                {"test_design", "tdd", "code_review", "testing"},
                set(state["work_mode"]["skipped_phases"]),
            )
            self.assertEqual(
                {"test_design", "code_review", "testing"},
                {
                    phase for phase, artifact in state["artifact_revisions"].items()
                    if artifact["status"] == "NOT_APPLICABLE"
                },
            )

    def test_lightweight_active_rejects_reopening_test_review_phases(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, work_path, task_dir = self._init(Path(temp))
            work_mode.apply_mode(work_path, work_mode.WorkMode(lightweight=True))
            long_task.command_sync_work_mode(argparse.Namespace(
                task_dir=str(task_dir), agent="ProjectManagerAgent",
            ))
            before_state = long_task.runtime_state(task_dir)
            before_history = long_task.load_task_history(task_dir)
            skipped = {
                phase for phase, artifact in before_state["artifact_revisions"].items()
                if artifact.get("status") == "NOT_APPLICABLE"
            }
            self.assertEqual({"test_design", "code_review", "testing"}, skipped)

            for phase in ("test_design", "code_review", "testing"):
                with self.subTest(phase=phase), self.assertRaisesRegex(
                    ValueError, rf"cannot reopen {phase} while wk -ws is active"
                ):
                    long_task.command_reopen_phase(argparse.Namespace(
                        task_dir=str(task_dir), from_phase=phase,
                        reason="用户要求恢复测试流程", source_revision="",
                        round="", agent="ProjectManagerAgent",
                    ))

            after_state = long_task.runtime_state(task_dir)
            after_history = long_task.load_task_history(task_dir)
            self.assertTrue(after_state["work_mode"]["lightweight"])
            self.assertEqual(before_state["artifact_revisions"], after_state["artifact_revisions"])
            self.assertEqual(before_state["current_phase"], after_state["current_phase"])
            self.assertEqual(before_history, after_history)

    def test_explicit_reopen_restores_validation_from_requested_phase(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            _, work_path, task_dir = self._init(Path(temp))
            work_mode.apply_mode(work_path, work_mode.WorkMode(lightweight=True))
            long_task.command_sync_work_mode(argparse.Namespace(
                task_dir=str(task_dir), agent="ProjectManagerAgent",
            ))
            work_mode.apply_mode(work_path, work_mode.WorkMode())
            long_task.command_sync_work_mode(argparse.Namespace(
                task_dir=str(task_dir), agent="ProjectManagerAgent",
            ))
            with contextlib.redirect_stdout(io.StringIO()):
                long_task.command_reopen_phase(argparse.Namespace(
                    task_dir=str(task_dir), from_phase="test_design",
                    reason="用户明确要求恢复测试流程", source_revision="",
                    round="", agent="ProjectManagerAgent",
                ))
            state = long_task.runtime_state(task_dir)
            self.assertFalse(state["work_mode"]["lightweight_waived"])
            self.assertEqual([], state["work_mode"]["skipped_phases"])
            self.assertEqual("test_design", state["current_phase"])
            self.assertEqual("IN_PROGRESS", state["artifact_revisions"]["test_design"]["status"])


if __name__ == "__main__":
    unittest.main()
