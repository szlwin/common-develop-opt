#!/usr/bin/env python3
"""Tests for deterministic minimal Reference routing."""
from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import context_resolver
import work_mode


class ContextResolverTests(unittest.TestCase):
    def test_triggered_required_resources_cannot_be_silently_omitted(self) -> None:
        prompt = "后端 数据库事务 跨模块 集成测试 失败重试 Review"
        for strategy in ("best_effort", "strict"):
            with self.subTest(strategy=strategy):
                result = context_resolver.resolve_context("development", prompt=prompt, budget_strategy=strategy)
                self.assertEqual("CONTEXT_INSUFFICIENT", result["status"])
                missing = {item["id"]: item for item in result["insufficientMandatoryResources"]}
                self.assertIn("integration-testing", missing)
                for resource_id in ("implementation-contract", "integration-testing"):
                    if resource_id in result["resourceIds"]:
                        selected = next(item for item in result["resources"] if item["id"] == resource_id)
                        self.assertEqual("mandatory", selected["priorityClass"])
                        continue
                    self.assertIn(resource_id, missing)
                    self.assertEqual("mandatory", missing[resource_id]["priorityClass"])
                    self.assertEqual("MAX_DYNAMIC_RESOURCES", missing[resource_id]["reason"])

    def test_triggered_resource_is_mandatory_for_every_hard_limit(self) -> None:
        for key, value, reason in (("maxDynamicResources", 2, "MAX_DYNAMIC_RESOURCES"),
                                   ("maxTotalResources", 3, "MAX_TOTAL_RESOURCES"),
                                   ("maxDefaultTokenBudget", 1500, "MAX_DEFAULT_TOKEN_BUDGET")):
            with self.subTest(limit=key):
                routing = context_resolver.load_routing()
                routing["routingPolicy"][key] = value
                result = context_resolver.resolve_context("development", prompt="后端 Java", routing=routing)
                self.assertEqual("CONTEXT_INSUFFICIENT", result["status"])
                missing = next(item for item in result["insufficientMandatoryResources"] if item["id"] == "backend-code-reference")
                self.assertEqual(reason, missing["reason"])

    def test_triggered_priority_survives_deduplication_and_soft_budget(self) -> None:
        routing = context_resolver.load_routing()
        routing["routingPolicy"]["targetResolvedTokenBudget"] = 1500
        result = context_resolver.resolve_context("development", prompt="后端 Java 前端 Vue", routing=routing)
        self.assertEqual("RESOLVED", result["status"])
        self.assertEqual(1, result["resourceIds"].count("implementation-contract"))
        self.assertTrue(any(item["id"] == "implementation-contract" for item in result["deduplicatedResources"]))
        self.assertTrue(all(item["priorityClass"] == "mandatory" for item in result["resources"]))
        self.assertGreater(result["totalTokenBudget"], 1500)
        self.assertLessEqual(result["totalTokenBudget"], result["limits"]["maxDefaultTokenBudget"])

    def test_mode_exclusion_precedes_conditional_promotion(self) -> None:
        for mode in (context_resolver.ResolvedMode(minimal=True), context_resolver.ResolvedMode(lightweight=True)):
            with self.subTest(mode=mode):
                result = context_resolver.resolve_context("development", prompt="集成测试", mode=mode)
                self.assertNotIn("integration-testing", result["resourceIds"])
                self.assertFalse(any(item["id"] == "integration-testing" for item in result["insufficientMandatoryResources"]))
                excluded = next(item for item in result["omittedResources"] if item["id"] == "integration-testing")
                self.assertTrue(excluded["reason"].startswith("MODE_EXCLUDED_"))

    def test_exact_route_starts_with_fixed_resources(self) -> None:
        result = context_resolver.resolve_context("development")
        self.assertEqual(["execution-core"], result["resourceIds"][:1])
        self.assertIn("development-core", result["resourceIds"])
        self.assertLessEqual(len(result["resourceIds"]), result["limits"]["maxTotalResources"])

    def test_default_development_route_stays_within_budget(self) -> None:
        result = context_resolver.resolve_context("development")
        self.assertEqual(
            ["execution-core", "development-core", "implementation-contract-summary"],
            result["resourceIds"],
        )
        self.assertLessEqual(
            result["totalTokenBudget"], result["limits"]["targetResolvedTokenBudget"]
        )
        self.assertGreaterEqual(
            result["limits"]["reservedTaskFactTokenBudget"], 5500
        )
        self.assertEqual(1000, result["limits"]["maxFixedTokenBudget"])
        self.assertEqual(6500, result["limits"]["targetResolvedTokenBudget"])

    def test_tdd_route_uses_focused_references_not_broad_compatibility_index(self) -> None:
        result = context_resolver.resolve_context("tdd")
        self.assertEqual(
            ["execution-core", "tdd-core", "red-green-refactor", "test-seam", "tdd-evidence"],
            result["resourceIds"],
        )
        self.assertNotIn("tdd", result["resourceIds"])
        self.assertLessEqual(result["baseRouteTokenBudget"], 2350)

    def test_test_resources_are_loaded_only_when_prompt_requires_them(self) -> None:
        base = context_resolver.resolve_context("development")
        self.assertNotIn("tdd", base["resourceIds"])
        self.assertNotIn("harness", base["resourceIds"])
        resolved = context_resolver.resolve_context(
            "development", prompt="需要执行回归测试和测试用例"
        )
        self.assertIn("test-execution", resolved["matchedConditionIds"])
        self.assertIn("test-environment", resolved["resourceIds"])
        self.assertNotIn("tdd", resolved["resourceIds"])
        self.assertLessEqual(
            resolved["totalTokenBudget"], resolved["limits"]["targetResolvedTokenBudget"]
        )

    def test_all_exact_routes_respect_default_token_budget(self) -> None:
        routing = context_resolver.load_routing()
        limit = routing["routingPolicy"]["targetResolvedTokenBudget"]
        for route in routing["routes"]:
            result = context_resolver.resolve_context(route["taskTypes"][0])
            self.assertLessEqual(result["totalTokenBudget"], limit, route["taskTypes"])

    def test_prompt_conditions_are_deterministic_and_not_hard_truncated(self) -> None:
        result = context_resolver.resolve_context(
            "development",
            prompt="这是一个跨模块退款补偿问题，需要人工 Review 和证据注册表。",
        )
        self.assertGreater(len(result["matchedConditionIds"]), 2)
        self.assertIn("dependency-impact", result["matchedConditionIds"])
        self.assertEqual(
            result["matchedConditionIds"],
            ["dependency-impact", "machine-verification", "manual-review"],
        )
        self.assertEqual(len(result["resourceIds"]), len(set(result["resourceIds"])))

    def test_page_design_mode_adds_page_design_reference_for_design(self) -> None:
        result = context_resolver.resolve_context(
            "design",
            mode=context_resolver.ResolvedMode(page_design=True),
        )
        self.assertIn("page-design", result["resourceIds"])
        without = context_resolver.resolve_context("design")
        self.assertNotIn("page-design", without["resourceIds"])

    def test_architecture_review_mode_adds_two_stage_development_reference(self) -> None:
        for task_type in ("implementation_plan", "development", "code_review"):
            result = context_resolver.resolve_context(
                task_type,
                mode=context_resolver.ResolvedMode(architecture_review=True),
            )
            self.assertIn("wk-ar-state-core", result["resourceIds"])
            self.assertTrue(result["mode"]["architectureReview"])
            self.assertLessEqual(result["totalTokenBudget"], result["limits"]["targetResolvedTokenBudget"])
        without = context_resolver.resolve_context("development")
        self.assertNotIn("wk-ar-state-core", without["resourceIds"])

    def test_git_checkpoint_mode_adds_checkpoint_reference_to_phase_context(self) -> None:
        result = context_resolver.resolve_context(
            "design",
            mode=context_resolver.ResolvedMode(git_checkpoint=True),
        )
        self.assertIn("git-checkpoint", result["resourceIds"])
        without = context_resolver.resolve_context("design")
        self.assertNotIn("git-checkpoint", without["resourceIds"])

    def test_minimal_mode_rejects_skipped_task_type(self) -> None:
        with self.assertRaisesRegex(context_resolver.ContextResolutionError, "skipped"):
            context_resolver.resolve_context(
                "testing",
                mode=context_resolver.ResolvedMode(minimal=True),
            )

    def test_minimal_mode_excludes_tdd_and_harness(self) -> None:
        result = context_resolver.resolve_context(
            "development",
            mode=context_resolver.ResolvedMode(minimal=True),
        )
        self.assertNotIn("tdd", result["resourceIds"])
        self.assertNotIn("harness", result["resourceIds"])

    def test_mode_can_be_loaded_from_work_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            path = work_mode.ensure_work_file(root, "V_TEST")
            work_mode.apply_mode(path, work_mode.WorkMode(model_code=True, page_design=True, minimal=False, auto=True, architecture_review=True, git_checkpoint=True, git_push=True))
            mode = context_resolver.resolve_mode(str(root), "V_TEST")
            self.assertTrue(mode.model_code)
            self.assertTrue(mode.auto)
            self.assertTrue(mode.architecture_review)
            self.assertTrue(mode.git_checkpoint)
            self.assertTrue(mode.git_push)
            self.assertEqual(str(path), mode.source)
            self.assertTrue(context_resolver.resolve_context("wk_gc", mode=mode)["mode"]["gitCheckpoint"])
            gp_context = context_resolver.resolve_context("wk_gp", mode=mode)
            self.assertTrue(gp_context["mode"]["gitPush"])
            self.assertIn("git-checkpoint-core", gp_context["resourceIds"])
            ar_route = context_resolver.resolve_context("wk_ar", mode=mode)
            self.assertIn("wk-ar-state-core", ar_route["resourceIds"])
            self.assertTrue(ar_route["mode"]["architectureReview"])


    def test_relevant_section_resources_expose_executable_read_commands(self) -> None:
        result = context_resolver.resolve_context("development", prompt="后端 Java Repository")
        backend = next(item for item in result["resources"] if item["id"] == "backend-code-reference")
        self.assertEqual("relevant_sections", backend["readStrategy"])
        self.assertIn("reference_reader.py index", backend["sectionIndexCommand"])
        self.assertIn("--section {SECTION_ID}", backend["readCommand"])
        execution = next(item for item in result["resources"] if item["id"] == "execution-core")
        self.assertEqual("full", execution["readStrategy"])
        self.assertIn("--full", execution["readCommand"])
        self.assertTrue(result["executionCore"]["machineContextFirst"])

    def test_optimized_base_routes_stay_below_compact_budget(self) -> None:
        routing = context_resolver.load_routing()
        optimized = routing["routingPolicy"]["optimizedBaseRouteTaskTypes"]
        limit = routing["routingPolicy"]["maxBaseRouteTokenBudget"]
        for task_type in optimized:
            with self.subTest(task_type=task_type):
                result = context_resolver.resolve_context(task_type)
                self.assertLessEqual(result["baseRouteTokenBudget"], limit)
                self.assertLessEqual(
                    result["baseRouteTokenBudget"],
                    routing["routingPolicy"]["baseRouteTokenBudgetTargets"][task_type],
                )

    def test_unregistered_task_type_is_rejected(self) -> None:
        with self.assertRaisesRegex(context_resolver.ContextResolutionError, "unknown task type"):
            context_resolver.resolve_context("unregistered_phase")

    def test_cli_json_is_machine_readable(self) -> None:
        args = argparse.Namespace(
            agent="ProjectManagerAgent",
            task_type="requirement_analysis",
            prompt="需求关联影响",
            doc_root="",
            version="",
            json=True,
        )
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(0, context_resolver.command_resolve(args))
        payload = json.loads(output.getvalue())
        self.assertEqual("requirement_analysis", payload["taskType"])
        self.assertIn("requirement-analysis-core", payload["resourceIds"])

    def test_budget_omission_is_explicit_and_preserves_task_fact_headroom(self) -> None:
        result = context_resolver.resolve_context(
            "design",
            prompt="完整生命周期矩阵 工作模式矩阵 ID命名空间 范围排除清单",
            mode=context_resolver.ResolvedMode(page_design=True, git_checkpoint=True),
        )
        self.assertLessEqual(result["totalTokenBudget"], result["limits"]["targetResolvedTokenBudget"])
        self.assertEqual(
            result["limits"]["maxDefaultTokenBudget"] - result["limits"]["targetResolvedTokenBudget"],
            result["limits"]["reservedTaskFactTokenBudget"],
        )
        self.assertTrue(result["omittedResources"])
        self.assertTrue(all(item["reason"] for item in result["omittedResources"]))
        self.assertTrue(all("selectedBy" in item for item in result["resources"]))


if __name__ == "__main__":
    unittest.main()
