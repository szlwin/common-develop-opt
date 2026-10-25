#!/usr/bin/env python3
"""Tests for wk -mc, wk -v, wk -min, wk -auto, wk -ar, wk -gc, and wk -gp work.md modes."""

from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from unittest import mock
from contextlib import redirect_stdout
from pathlib import Path

import agent_context
import work_mode


ROW = "| REQ-001 | 示例需求 | 示例功能 | MOD0001 | AIW | AIW | AIW | AIW | AIW | AIW | AIW | AIW | AIW | AIW | AIW | AIW |"


class WorkModeTests(unittest.TestCase):
    def make_work(self, root: Path) -> Path:
        path = work_mode.ensure_work_file(root, "V_2.13")
        text = path.read_text(encoding="utf-8")
        text = text.replace("\n## 使用说明", "\n" + ROW + "\n## 使用说明")
        path.write_text(text, encoding="utf-8")
        return path

    def row_values(self, path: Path) -> dict[str, str]:
        lines = path.read_text(encoding="utf-8").splitlines()
        header_index, indexes = work_mode._find_work_table(lines)
        for line in lines[header_index + 1 :]:
            cells = work_mode._cells(line)
            if cells and not work_mode._separator(cells):
                return {name: cells[index] for name, index in indexes.items()}
        self.fail("missing work row")

    def test_default_mode_disables_frontend_and_backend_pseudocode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_work(Path(temp))
            work_mode.apply_mode(path, work_mode.WorkMode())
            values = self.row_values(path)
            self.assertEqual("无", values["前端伪代码"])
            self.assertEqual("无", values["后端伪代码"])
            self.assertEqual("无", values["页面设计"])
            self.assertEqual("无", values["架构代码"])
            self.assertEqual("AIW", values["测试用例"])
            self.assertEqual(work_mode.WorkMode(), work_mode.validate_work_file(path))

    def test_mc_requires_both_pseudocode_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_work(Path(temp))
            work_mode.apply_mode(path, work_mode.WorkMode(model_code=True))
            values = self.row_values(path)
            self.assertEqual("AIW", values["前端伪代码"])
            self.assertEqual("AIW", values["后端伪代码"])
            self.assertTrue(work_mode.validate_work_file(path).model_code)


    def test_v_controls_page_design_column(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_work(Path(temp))
            work_mode.apply_mode(path, work_mode.WorkMode(page_design=True))
            values = self.row_values(path)
            self.assertEqual("AIW", values["页面设计"])
            self.assertTrue(work_mode.validate_work_file(path).page_design)

    def test_v_and_min_can_be_combined(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_work(Path(temp))
            mode = work_mode.WorkMode(page_design=True, minimal=True)
            work_mode.apply_mode(path, mode)
            values = self.row_values(path)
            self.assertEqual("AIW", values["页面设计"])
            self.assertEqual("无", values["测试用例"])
            self.assertEqual("无", values["测试"])
            self.assertEqual(mode, work_mode.validate_work_file(path))

    def test_min_disables_all_test_columns(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_work(Path(temp))
            work_mode.apply_mode(path, work_mode.WorkMode(minimal=True))
            values = self.row_values(path)
            for name in work_mode.MINIMAL_COLUMNS:
                self.assertEqual("无", values[name])
            mode = work_mode.validate_work_file(path)
            self.assertTrue(mode.minimal)
            self.assertEqual(("test_design", "testing"), work_mode.MINIMAL_PHASES)

    def test_mc_and_min_can_be_combined(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_work(Path(temp))
            mode = work_mode.WorkMode(model_code=True, minimal=True)
            work_mode.apply_mode(path, mode)
            values = self.row_values(path)
            self.assertEqual("AIW", values["前端伪代码"])
            self.assertEqual("AIW", values["后端伪代码"])
            for name in work_mode.MINIMAL_COLUMNS:
                self.assertEqual("无", values[name])
            self.assertEqual(mode, work_mode.validate_work_file(path))

    def test_validate_rejects_disabled_mode_with_pseudocode_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_work(Path(temp))
            with self.assertRaisesRegex(work_mode.WorkModeError, "前端伪代码必须填写为无"):
                work_mode.validate_work_file(path)

    def test_validate_rejects_minimal_mode_with_test_work(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_work(Path(temp))
            work_mode.apply_mode(path, work_mode.WorkMode(model_code=True, minimal=True))
            lines = path.read_text(encoding="utf-8").splitlines()
            header_index, indexes = work_mode._find_work_table(lines)
            for line_index, row in work_mode._iter_work_rows(lines, header_index, len(work_mode._cells(lines[header_index]) or [])):
                row[indexes["测试用例"]] = "AIW"
                lines[line_index] = work_mode._render_row(row)
                break
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(work_mode.WorkModeError, "测试用例必须填写为无"):
                work_mode.validate_work_file(path)

    def test_ar_controls_architecture_code_column_and_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_work(Path(temp))
            mode = work_mode.WorkMode(architecture_review=True)
            work_mode.apply_mode(path, mode)
            values = self.row_values(path)
            self.assertEqual("AIW", values["架构代码"])
            resolved = work_mode.validate_work_file(path)
            self.assertTrue(resolved.architecture_review)
            policy = work_mode.resolved_mode_policy(resolved)["architectureReviewPolicy"]
            self.assertEqual("ARCHITECTURE_SKELETON", policy["firstStep"]["id"])
            self.assertEqual("CONCRETE_IMPLEMENTATION", policy["secondStep"]["id"])
            self.assertFalse(policy["autoAdvanceAcrossReviewBoundary"])

    def test_init_command_creates_version_work_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            args = argparse.Namespace(
                doc_root=temp,
                version="V_2.13",
                model_code=True,
                page_design=True,
                minimal=True,
                auto=True,
                architecture_review=True,
                git_checkpoint=True,
                git_push=True,
                agent="ProjectManagerAgent",
            )
            self.assertEqual(0, work_mode.command_init(args))
            path = work_mode.work_path(Path(temp), "V_2.13")
            self.assertTrue(path.is_file())
            self.assertEqual(
                work_mode.WorkMode(model_code=True, page_design=True, minimal=True, auto=True, architecture_review=True, git_checkpoint=True, git_push=True),
                work_mode.validate_work_file(path),
            )

    def test_parser_accepts_wk_mode_flags(self) -> None:
        parsed = work_mode.build_parser().parse_args([
            "apply", "-g", "ProjectManagerAgent",
            "--doc-root", "project_doc", "--version", "V_2.13",
            "-mc", "-v", "-min", "-auto", "-ar", "-gc", "-gp",
        ])
        self.assertTrue(parsed.model_code)
        self.assertTrue(parsed.page_design)
        self.assertTrue(parsed.minimal)
        self.assertTrue(parsed.auto)
        self.assertTrue(parsed.architecture_review)
        self.assertTrue(parsed.git_checkpoint)
        self.assertTrue(parsed.git_push)


    def test_mode_patch_preserves_unmentioned_modes_and_supports_explicit_disable(self) -> None:
        current = work_mode.WorkMode(
            model_code=False, auto=True, git_checkpoint=True, lightweight=True
        )
        parsed = work_mode.build_parser().parse_args([
            "apply", "-g", "ProjectManagerAgent",
            "--doc-root", "project_doc", "--version", "V_2.13",
            "-mc", "--no-auto", "-wc",
        ])

        resolved = work_mode.mode_from_args(parsed, current)

        self.assertTrue(resolved.model_code)
        self.assertFalse(resolved.auto)
        self.assertTrue(resolved.git_checkpoint)
        self.assertFalse(resolved.lightweight)

    def test_mode_parser_rejects_enable_and_disable_for_the_same_mode(self) -> None:
        common = [
            "apply", "-g", "ProjectManagerAgent",
            "--doc-root", "project_doc", "--version", "V_2.13",
        ]
        with self.assertRaises(SystemExit):
            work_mode.build_parser().parse_args(common + ["-mc", "--no-mc"])
        with self.assertRaises(SystemExit):
            work_mode.build_parser().parse_args(common + ["-ws", "-wc"])

    def test_minimal_policy_is_derived_from_runtime_contract(self) -> None:
        policy = work_mode.resolved_mode_policy(work_mode.WorkMode(minimal=True))
        self.assertEqual(["test_design", "testing"], policy["skippedPhases"])
        self.assertEqual("SKIPPED", policy["phaseOutcome"])
        self.assertEqual("NOT_APPLICABLE", policy["archiveGateStatus"])
        self.assertEqual(["testDesign", "testing"], policy["archiveGateKeys"])
        self.assertIn("wk -min", policy["skipReason"])
        self.assertEqual(
            ["TestDesignAgent"],
            policy["suppressedReviewerAssignments"]["design"],
        )
        self.assertIn("untested_risk_statement", policy["requiredNonTestEvidence"])

    def test_status_json_exposes_resolved_policy_for_ai(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_work(Path(temp))
            work_mode.apply_mode(path, work_mode.WorkMode(model_code=True, page_design=True, minimal=True, auto=True, architecture_review=True, git_checkpoint=True, git_push=True))
            args = argparse.Namespace(
                doc_root=temp,
                version="V_2.13",
                agent="ProjectManagerAgent",
                json=True,
            )
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(0, work_mode.command_status(args))
            payload = json.loads(output.getvalue())
            self.assertTrue(payload["modelCode"])
            self.assertTrue(payload["pageDesign"])
            self.assertTrue(payload["minimal"])
            self.assertTrue(payload["auto"])
            self.assertTrue(payload["architectureReview"])
            self.assertEqual("ARCHITECTURE_SKELETON", payload["architectureReviewPolicy"]["firstStep"]["id"])
            self.assertTrue(payload["gitCheckpoint"])
            self.assertEqual("SEQUENTIAL", payload["executionPolicy"]["mode"])
            self.assertEqual("assets/runtime-contract.json#/executionPolicy", payload["gitCheckpointPolicy"]["executionPolicyRef"])
            self.assertTrue(payload["gitPush"])
            self.assertTrue(payload["gitPushPolicy"]["verifyRemoteHead"])
            self.assertTrue(payload["gitPushPolicy"]["failureBlocksPhaseAdvance"])
            self.assertEqual("AUTOMATED", payload["confirmationPolicy"]["mode"])
            self.assertFalse(payload["confirmationPolicy"]["userConfirmationRequired"])
            self.assertEqual("SKIPPED", payload["phaseOutcome"])
            self.assertEqual(["testing"], payload["skippedPhases"][1:])
            self.assertIn("design", payload["suppressedReviewerAssignments"])

    def test_auto_mode_resolves_automated_confirmation_policy(self) -> None:
        policy = work_mode.resolved_mode_policy(work_mode.WorkMode(auto=True))
        self.assertTrue(policy["auto"])
        self.assertEqual("AUTOMATED", policy["confirmationPolicy"]["mode"])
        self.assertFalse(policy["confirmationPolicy"]["userConfirmationRequired"])
        self.assertTrue(policy["confirmationPolicy"]["autoAdvance"])
        self.assertEqual("BATCH_FORWARD_ONLY", policy["confirmationPolicy"]["stopScope"])
        self.assertIn("failed_or_blocked_review", policy["confirmationPolicy"]["stopWhen"])
        self.assertIn("ci_failure", policy["confirmationPolicy"]["stopWhen"])
        self.assertIn("authority_conflict", policy["confirmationPolicy"]["remediationStopWhen"])
        self.assertTrue(policy["autoRemediationPolicy"]["enabled"])
        self.assertEqual("FROZEN", policy["autoRemediationPolicy"]["p1Policy"]["batchForward"])
        self.assertEqual("DETECT_BEFORE_AR", policy["architectureBoundaryPolicy"]["mode"])
        self.assertTrue(policy["finalHeadGatePolicy"]["bindToTrueFinalPrHead"])

    def test_git_checkpoint_mode_is_independent_from_auto(self) -> None:
        policy = work_mode.resolved_mode_policy(work_mode.WorkMode(git_checkpoint=True))
        self.assertTrue(policy["gitCheckpoint"])
        self.assertFalse(policy["auto"])
        self.assertEqual("SEQUENTIAL", policy["executionPolicy"]["mode"])
        self.assertEqual("assets/runtime-contract.json#/executionPolicy", policy["gitCheckpointPolicy"]["executionPolicyRef"])
        self.assertFalse(policy["gitCheckpointPolicy"]["autoPush"])
        self.assertFalse(policy["gitCheckpointPolicy"]["autoMerge"])

    def test_git_push_requires_git_checkpoint(self) -> None:
        with self.assertRaisesRegex(work_mode.WorkModeError, "requires wk -gc"):
            work_mode.resolved_mode_policy(work_mode.WorkMode(git_push=True))

    def test_git_push_policy_is_independent_from_auto_and_requires_remote_verification(self) -> None:
        policy = work_mode.resolved_mode_policy(work_mode.WorkMode(git_checkpoint=True, git_push=True))
        self.assertTrue(policy["gitCheckpoint"])
        self.assertTrue(policy["gitPush"])
        self.assertFalse(policy["auto"])
        self.assertTrue(policy["gitPushPolicy"]["verifyRemoteHead"])
        self.assertTrue(policy["gitPushPolicy"]["failureBlocksPhaseAdvance"])
        self.assertEqual(["NATIVE_GIT", "GITHUB_CONNECTOR"], policy["gitPushPolicy"]["transportOrder"])
        self.assertEqual("EXACT_COMMIT_SHA", policy["gitPushPolicy"]["nativeTransport"]["identityMode"])
        self.assertTrue(policy["gitPushPolicy"]["connectorFallback"]["enabled"])
        self.assertEqual("EXACT_TREE_MESSAGE_EQUIVALENT", policy["gitPushPolicy"]["connectorFallback"]["identityMode"])
        self.assertFalse(policy["gitPushPolicy"]["connectorFallback"]["rewriteLocalHistory"])
        self.assertTrue(policy["gitPushPolicy"]["connectorFallback"]["receiptRequiredBeforeRemoteCheckpointed"])
        self.assertFalse(policy["gitPushPolicy"]["forceAllowed"])
        self.assertFalse(policy["gitPushPolicy"]["pushTags"])
        self.assertFalse(policy["gitPushPolicy"]["autoMerge"])

    def test_validate_rejects_stale_human_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_work(Path(temp))
            work_mode.apply_mode(path, work_mode.WorkMode())
            text = path.read_text(encoding="utf-8").replace(
                "前后端伪代码=关闭", "前后端伪代码=启用", 1
            )
            path.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(work_mode.WorkModeError, "摘要与 common-develop 标记不一致"):
                work_mode.validate_work_file(path)

    def test_apply_defaults_to_sync_existing_task_states_when_flag_is_omitted(self) -> None:
        import long_task
        with tempfile.TemporaryDirectory() as temp:
            doc_root = Path(temp) / "project_doc"
            long_task.command_init(argparse.Namespace(
                doc_root=str(doc_root), version="V_2.13", target="TARGET", reuse=False, agent="ProjectManagerAgent"
            ))
            args = argparse.Namespace(
                doc_root=str(doc_root), version="V_2.13", model_code=True,
                agent="ProjectManagerAgent",
            )

            self.assertEqual(0, work_mode.command_apply(args))

            task_dir = doc_root / "version" / "V_2.13" / "task" / "TARGET"
            state = long_task.runtime_state(task_dir)
            self.assertTrue(state["work_mode"]["model_code"])

    def test_apply_can_skip_syncing_existing_task_states_explicitly(self) -> None:
        import long_task
        with tempfile.TemporaryDirectory() as temp:
            doc_root = Path(temp) / "project_doc"
            long_task.command_init(argparse.Namespace(
                doc_root=str(doc_root), version="V_2.13", target="TARGET", reuse=False, agent="ProjectManagerAgent"
            ))
            args = argparse.Namespace(
                doc_root=str(doc_root), version="V_2.13", model_code=True,
                sync_existing_tasks=False, agent="ProjectManagerAgent",
            )

            self.assertEqual(0, work_mode.command_apply(args))

            task_dir = doc_root / "version" / "V_2.13" / "task" / "TARGET"
            state = long_task.runtime_state(task_dir)
            self.assertFalse(state["work_mode"]["model_code"])

    def test_apply_can_atomically_sync_existing_task_states(self) -> None:
        import long_task
        with tempfile.TemporaryDirectory() as temp:
            doc_root = Path(temp) / "project_doc"
            long_task.command_init(argparse.Namespace(
                doc_root=str(doc_root), version="V_2.13", target="TARGET", reuse=False, agent="ProjectManagerAgent"
            ))
            args = argparse.Namespace(
                doc_root=str(doc_root), version="V_2.13", model_code=True, page_design=False,
                minimal=False, auto=False, git_checkpoint=False, sync_existing_tasks=True,
                agent="ProjectManagerAgent",
            )
            self.assertEqual(0, work_mode.command_apply(args))
            task_dir = doc_root / "version" / "V_2.13" / "task" / "TARGET"
            state = long_task.runtime_state(task_dir)
            self.assertTrue(state["work_mode"]["model_code"])
            self.assertEqual(
                long_task.sha256_file(doc_root / "version" / "V_2.13" / "work.md"),
                state["work_mode"]["digest"],
            )

    def test_apply_rolls_back_work_and_task_runtime_files_when_sync_fails(self) -> None:
        import long_task
        with tempfile.TemporaryDirectory() as temp:
            doc_root = Path(temp) / "project_doc"
            long_task.command_init(argparse.Namespace(
                doc_root=str(doc_root), version="V_2.13", target="TARGET", reuse=False, agent="ProjectManagerAgent"
            ))
            task_dir = doc_root / "version" / "V_2.13" / "task" / "TARGET"
            paths = [
                doc_root / "version" / "V_2.13" / "work.md",
                task_dir / "cache" / "current_manifest.json",
                task_dir / "task_events.jsonl",
            ]
            before = {path: path.read_bytes() for path in paths}

            def fail_after_mutation(args):
                (Path(args.task_dir) / "cache" / "current_manifest.json").write_text("corrupted", encoding="utf-8")
                raise ValueError("sync failed")

            args = argparse.Namespace(
                doc_root=str(doc_root), version="V_2.13", model_code=True, page_design=False,
                minimal=False, auto=False, git_checkpoint=False, sync_existing_tasks=True,
                agent="ProjectManagerAgent",
            )
            with mock.patch.object(long_task, "command_sync_work_mode", side_effect=fail_after_mutation):
                with self.assertRaisesRegex(ValueError, "sync failed"):
                    work_mode.command_apply(args)
            self.assertEqual(before, {path: path.read_bytes() for path in paths})


    def test_apply_rejects_non_project_manager(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            args = argparse.Namespace(
                doc_root=temp,
                version="V_2.13",
                model_code=False,
                page_design=False,
                minimal=False,
                auto=False,
                git_checkpoint=False,
                agent="DesignAgent",
            )
            with self.assertRaises(agent_context.AgentAuthorizationError):
                work_mode.command_apply(args)

    def test_lightweight_and_architecture_review_can_be_combined(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_work(Path(temp))
            mode = work_mode.WorkMode(lightweight=True, architecture_review=True)
            work_mode.apply_mode(path, mode)
            values = self.row_values(path)
            self.assertEqual("AIW", values["架构代码"])
            for name in ("测试用例", "Review", "测试"):
                self.assertEqual("无", values[name])
            self.assertEqual(mode, work_mode.validate_work_file(path))

    def test_lightweight_preserves_architecture_review_and_restores_assignments(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_work(Path(temp))
            original = self.row_values(path)
            work_mode.apply_mode(path, work_mode.WorkMode(architecture_review=True))
            before = self.row_values(path)
            work_mode.apply_mode(path, work_mode.WorkMode(architecture_review=True, lightweight=True))
            during = self.row_values(path)
            self.assertEqual("AIW", during["架构代码"])
            self.assertEqual("无", during["测试用例"])
            work_mode.apply_mode(path, work_mode.WorkMode(architecture_review=True))
            after = self.row_values(path)
            self.assertEqual(before["测试用例"], after["测试用例"])
            self.assertEqual(before["Review"], after["Review"])
            self.assertEqual(before["测试"], after["测试"])
            self.assertEqual(before["架构代码"], after["架构代码"])
            self.assertNotEqual(original["测试用例"], during["测试用例"])

    def test_lightweight_assignment_snapshot_supports_multiple_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_work(Path(temp))
            with path.open("a", encoding="utf-8") as handle:
                handle.write(ROW.replace("REQ-001", "REQ-002").replace("示例需求", "第二需求") + "\n")
            work_mode.apply_mode(path, work_mode.WorkMode())
            work_mode.apply_mode(path, work_mode.WorkMode(lightweight=True))
            work_mode.apply_mode(path, work_mode.WorkMode())
            values = path.read_text(encoding="utf-8")
            self.assertEqual(2, sum(1 for line in values.splitlines() if line.startswith("| REQ-00")))
            self.assertEqual("AIW", self.row_values(path)["测试用例"])

    def test_corrupt_lightweight_assignment_snapshot_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = self.make_work(Path(temp))
            work_mode.apply_mode(path, work_mode.WorkMode(lightweight=True))
            text = path.read_text(encoding="utf-8").replace('"rows":[[', '"rows":oops[[', 1)
            path.write_text(text, encoding="utf-8")
            with self.assertRaisesRegex(work_mode.WorkModeError, "快照格式无效"):
                work_mode.apply_mode(path, work_mode.WorkMode())


if __name__ == "__main__":
    unittest.main()
