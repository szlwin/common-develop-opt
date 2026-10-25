"""Regression checks for Stage 5 maintainability boundaries."""
from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import cli_help
import context_resolver
from context_resolver import ResolvedMode
import long_task
import wk
from _long_task import state_access


ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent.parent


def _subcommands(parser: argparse.ArgumentParser) -> dict[str, argparse.ArgumentParser]:
    action = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction))
    return dict(action.choices)


class Stage5MaintainabilityTests(unittest.TestCase):
    def test_public_wk_parser_delegation_preserves_surface_and_callbacks(self) -> None:
        commands = _subcommands(wk.build_parser())
        self.assertEqual(
            sorted(commands),
            [
                "begin", "close", "confirm", "context", "delivery", "doctor", "document-archive",
                "env", "finish", "help", "impact", "next", "packs", "progress", "reconcile", "record-result", "start", "status", "step",
            ],
        )
        self.assertIs(commands["start"].get_default("func"), wk.command_start)
        self.assertIs(commands["help"].get_default("func"), wk.command_help)
        self.assertIs(commands["document-archive"].get_default("func"), wk.command_document_archive)

    def test_long_task_parser_is_grouped_without_surface_drift(self) -> None:
        commands = _subcommands(long_task.build_parser())
        self.assertEqual(len(commands), 29)
        self.assertIn("amend-phase", commands)
        self.assertIn("finish-attempt", commands)
        self.assertIn("compact-events", commands)
        self.assertIn("validate-work-record", commands)
        self.assertIs(commands["finish-attempt"].get_default("func"), long_task.command_finish_attempt)
        self.assertIs(commands["help"].get_default("func"), long_task.command_help)

    def test_quick_start_and_maintenance_entrypoints_are_explicit(self) -> None:
        readme = (REPO_ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        maintenance = (ROOT / "references/maintenance-entrypoints.md").read_text(encoding="utf-8")
        for path in ("scripts/wk.py", "scripts/long_task.py", "scripts/ai_context.py"):
            self.assertIn(path, readme)
        for path in ("scripts/check_skill.py", "scripts/quality_gate.py", "scripts/baseline_guard.py"):
            self.assertIn(path, maintenance)
        self.assertIn("只读查询", maintenance)
        self.assertIn("写入必须串行", maintenance)
        self.assertNotIn("docs/README.md 或 version/{VERSION}/version_summary.md 阅读并沿相对链接跳转", readme)

    def test_git_checkpoint_index_and_post_commit_state_contract_is_explicit(self) -> None:
        reference = (ROOT / "references/git-checkpoint.md").read_text(encoding="utf-8")
        self.assertIn("真实 Git index 必须没有 staged 内容且其 tree 必须等于当前 `HEAD` tree", reference)
        self.assertIn("使用隔离的临时 index", reference)
        self.assertIn("`git commit` 提交的是临时 index 的快照", reference)
        self.assertIn("原子刷新真实 index 到新 `HEAD`", reference)
        self.assertIn("不表示前一个 checkpoint 漏提交", reference)
        self.assertIn("git restore --staged :/", reference)

    def test_repository_and_package_boundaries_are_explicit(self) -> None:
        english = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (REPO_ROOT / "README.zh-CN.md").read_text(encoding="utf-8")
        self.assertIn("skills/common-develop/", english)
        self.assertIn("the only installable Skill source", english)
        self.assertIn("skills/common-develop/", chinese)
        self.assertIn("唯一可安装 Skill 源", chinese)
        self.assertIn("project_doc/", chinese)
        self.assertIn("当前仓库任务事实", chinese)
        self.assertTrue((ROOT / "SKILL.md").is_file())
        self.assertFalse((REPO_ROOT / "scripts").exists())
        self.assertFalse((REPO_ROOT / "tests").exists())

    def test_resource_aliases_and_priority_classes_are_closed(self) -> None:
        routing = json.loads((ROOT / "context-routing.json").read_text(encoding="utf-8"))
        catalog = routing["resourceCatalog"]
        allowed = {"mandatory", "required-if-triggered", "helpful", "example-only"}
        aliases = {key: value["aliasOf"] for key, value in catalog.items() if "aliasOf" in value}
        for resource_id, definition in catalog.items():
            self.assertIn(definition.get("priorityClass", "helpful"), allowed, resource_id)
            self.assertIn(definition.get("status"), {"active", "deprecated", "internal", "on_demand"})
            self.assertTrue(definition.get("owner"), resource_id)
            self.assertIn("replacement", definition)
            self.assertRegex(definition.get("lastReviewed", ""), r"^\d{4}-\d{2}-\d{2}$")
            if resource_id not in aliases:
                continue
            target = aliases[resource_id]
            self.assertIn(target, catalog)
            self.assertNotIn(target, aliases, f"alias chain: {resource_id} -> {target}")
            self.assertEqual(
                definition.get("path"),
                catalog[target].get("path", f"references/{target}.md"),
            )
        for resource_id in routing.get("fixedResources", []):
            self.assertEqual(catalog[resource_id].get("priorityClass"), "mandatory")

    def test_hard_constraint_levels_are_separate_from_resource_priority(self) -> None:
        routing = json.loads((ROOT / "context-routing.json").read_text(encoding="utf-8"))
        levels = routing["governancePolicy"]["hardConstraintLevels"]
        self.assertEqual({"HARD_GATE", "REQUIRED", "RECOMMENDED", "EXAMPLE"}, set(levels))
        self.assertEqual({1, 2, 3, 4}, {item["rank"] for item in levels.values()})
        self.assertIn("priorityClass", routing["governancePolicy"]["resourcePrioritySeparation"])

    def test_aliases_use_explicit_stable_section_ids(self) -> None:
        routing = json.loads((ROOT / "context-routing.json").read_text(encoding="utf-8"))
        import reference_reader
        for resource_id, definition in routing["resourceCatalog"].items():
            if "aliasOf" not in definition:
                continue
            indexed = {item["id"]: item for item in reference_reader.index_resource(definition["aliasOf"])["sections"]}
            for section_id in definition["sectionIds"]:
                self.assertIn(section_id, indexed)
                self.assertTrue(indexed[section_id]["stable"], f"{resource_id}: {section_id}")

    def test_context_resolver_exposes_priority_reason_for_omissions(self) -> None:
        payload = context_resolver.resolve_context(
            task_type="completion_verification",
            prompt="verify the completed task",
            mode=ResolvedMode(minimal=True),
        )
        for item in payload.get("omittedResources", []):
            self.assertIn(item.get("priorityClass"), {"mandatory", "required-if-triggered", "helpful", "example-only"})
            self.assertTrue(item.get("reason"))


    def test_wk_facade_and_default_output_boundary_are_explicit(self) -> None:
        self.assertLess(len((ROOT / "scripts/wk.py").read_text(encoding="utf-8").splitlines()), 120)
        self.assertTrue((ROOT / "scripts/_core/wk_commands.py").is_file())
        payload = {"status": "BLOCKED", "reason": "需要 Review", "nextCommand": "wk next"}
        text = io.StringIO()
        with contextlib.redirect_stdout(text):
            wk.emit_result(payload, argparse.Namespace(json=False))
        self.assertNotIn("{", text.getvalue())
        machine = io.StringIO()
        with contextlib.redirect_stdout(machine):
            wk.emit_result(payload, argparse.Namespace(json=True))
        self.assertEqual(payload, json.loads(machine.getvalue()))

    def test_task_reference_resolution_has_project_containment_and_symlink_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp) / "project"
            task = project / "project_doc" / "version" / "V_TEST" / "task" / "TARGET"
            task.mkdir(parents=True)
            (task / "safe.txt").write_text("safe", encoding="utf-8")
            outside = Path(temp) / "outside.txt"
            outside.write_text("outside", encoding="utf-8")
            link = task / "escape.txt"
            link.symlink_to(outside)
            self.assertEqual((task / "safe.txt").resolve(), state_access.resolve_task_ref(task, "safe.txt"))
            with self.assertRaisesRegex(ValueError, "escapes project root"):
                state_access.resolve_task_ref(task, "../" * 5 + "outside.txt")
            with self.assertRaisesRegex(ValueError, "escapes project root"):
                state_access.resolve_task_ref(task, "escape.txt")

    def test_static_and_runtime_cli_surfaces_remain_discoverable(self) -> None:
        self.assertEqual(len(cli_help.command_names(wk.build_parser())), 19)
        self.assertEqual(len(cli_help.command_names(long_task.build_parser())), 29)
        self.assertTrue((ROOT / "scripts/_core/wk_cli.py").is_file())
        self.assertLess(max(_function_line_count(ROOT / "scripts/_long_task/cli.py")), 250)


def _function_line_count(path: Path) -> list[int]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    return [
        node.end_lineno - node.lineno + 1
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


if __name__ == "__main__":
    unittest.main()
