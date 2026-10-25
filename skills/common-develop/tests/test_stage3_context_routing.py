#!/usr/bin/env python3
"""Focused regression coverage for Stage 3 context and storage contracts."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import ai_context
import context_resolver
import storage


class Stage3ContextRoutingTests(unittest.TestCase):
    def test_ai_context_preserves_triggered_mandatory_insufficiency(self) -> None:
        result = ai_context.resolve_ai_context(
            agent="DevelopAgent", task_type="development", phase="development",
            prompt="后端 数据库事务 跨模块 集成测试 失败重试 Review",
        )
        self.assertEqual("CONTEXT_INSUFFICIENT", result["contextStatus"])
        self.assertEqual("CONTEXT_INSUFFICIENT", result["contextBudget"]["status"])

    def test_exact_route_resources_precede_mode_resources(self) -> None:
        result = context_resolver.resolve_context(
            "design", mode=context_resolver.ResolvedMode(page_design=True)
        )
        self.assertEqual(
            ["execution-core", "design-core", "implementation-contract-summary", "enum-summary"],
            result["resourceIds"][:4],
        )
        self.assertEqual("page-design", result["resourceIds"][4])

    def test_minimal_and_lightweight_keep_completion_gate_context(self) -> None:
        for mode in (
            context_resolver.ResolvedMode(minimal=True),
            context_resolver.ResolvedMode(lightweight=True),
        ):
            with self.subTest(mode=mode):
                result = context_resolver.resolve_context("development", mode=mode)
                self.assertIn("completion-gate-minimal", result["resourceIds"])
                self.assertNotIn("completion-gate-core", result["resourceIds"])
                self.assertNotIn("test-completion", result["resourceIds"])
                self.assertEqual([], result["omittedResources"])
                self.assertEqual("RESOLVED", result["status"])

    def test_all_matched_prompt_conditions_are_retained_and_omissions_explainable(self) -> None:
        result = context_resolver.resolve_context(
            "design",
            prompt="完整生命周期矩阵 工作模式矩阵 ID命名空间 范围排除清单",
            mode=context_resolver.ResolvedMode(page_design=True, git_checkpoint=True),
        )
        self.assertGreater(len(result["matchedConditionIds"]), 2)
        self.assertEqual(
            result["matchedConditionIds"],
            ["runtime-details", "scope-details", "semantic-details"],
        )
        self.assertEqual(["scope", "semantic-contract"], [x["id"] for x in result["omittedResources"]])
        for item in result["omittedResources"]:
            self.assertTrue(item["reason"])
            self.assertIn(item["source"].split(":", 1)[-1], {"scope-details", "semantic-details"})

    def test_typed_matching_does_not_confuse_stable_id_with_create_token(self) -> None:
        mapping = context_resolver.resolve_context("development", prompt="CMI-ORDER-CREATE")
        create = context_resolver.resolve_context("development", prompt="CREATE")
        self.assertIn("cross-module-impact", mapping["resourceIds"])
        self.assertNotIn("implementation-strategy", mapping["resourceIds"])
        self.assertIn("implementation-strategy", create["resourceIds"])

    def test_strict_ai_budget_reports_context_insufficient_without_losing_status(self) -> None:
        result = ai_context.resolve_ai_context(
            agent="RequirementConfirmationAgent",
            task_type="requirement_confirmation",
            phase="requirement_confirmation",
            token_budget=1,
            budget_strategy="strict",
        )
        self.assertEqual("CONTEXT_INSUFFICIENT", result["contextStatus"])
        budget = result["contextBudget"]
        self.assertEqual("strict", budget["strategy"])
        self.assertEqual(1, budget["requested"])
        self.assertEqual(500, budget["effective"])
        self.assertTrue(budget["minimumApplied"])
        self.assertFalse(budget["withinBudget"])
        self.assertEqual("CONTEXT_INSUFFICIENT", budget["status"])
        self.assertTrue(budget["omitted"])

    def test_storage_runtime_release_mapping_is_canonical(self) -> None:
        root = Path(__file__).resolve().parent.parent
        storage_contract = json.loads((root / "assets/storage-contract.json").read_text())
        runtime_contract = json.loads((root / "assets/runtime-contract.json").read_text())
        release_contract = json.loads((root / "assets/release-contract.json").read_text())
        version = storage_contract["modelVersion"]
        identity = storage_contract["modelIdentity"]
        self.assertEqual(version, runtime_contract["storageModelVersion"])
        self.assertEqual(version, release_contract["storageModelVersion"])
        self.assertEqual(identity, runtime_contract["storageModelIdentity"])
        self.assertEqual(identity, release_contract["storageModelIdentity"])
        self.assertEqual("assets/storage-contract.json#/modelVersion", runtime_contract["storageModelSource"])
        self.assertEqual("assets/storage-contract.json#/modelVersion", release_contract["storageModelSource"])

    def test_incompatible_project_marker_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            task_dir = root / "version" / "V_TEST" / "task" / "TASK-001"
            task_dir.mkdir(parents=True)
            (task_dir / "task_events.jsonl").write_text("", encoding="utf-8")
            marker = storage.ensure_project_marker(root)
            value = json.loads(marker.read_text(encoding="utf-8"))
            value["storageModelVersion"] = storage.CONTRACT["modelVersion"] + 1
            marker.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(storage.StorageError):
                storage.assert_new_project(task_dir)


if __name__ == "__main__":
    unittest.main()
