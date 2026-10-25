"""Regression tests for the compact common-develop AI execution context."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
import contextlib
import io
from pathlib import Path

import _bootstrap
import ai_context
import long_task
import evidence
import work_mode
import argparse
import simple_yaml

ROOT = Path(__file__).resolve().parent.parent


class AIContextTests(unittest.TestCase):
    def _work_root(self, marker: str) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        version_dir = root / "version" / "V_TEST"
        version_dir.mkdir(parents=True)
        (version_dir / "work.md").write_text(
            marker + "\n# V_TEST 工作\n\n| 需求编号 | 需求名称 | 功能名称 | 功能编号 | 需求分析 | 页面设计 | 设计 | 架构设计 | 测试用例 | 前端伪代码 | 后端伪代码 | 开发 | 架构代码 | Review | 测试 | 归档 |\n"
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|\n",
            encoding="utf-8",
        )
        return temp, root

    def test_resolve_exposes_phase_contract_permissions_and_minimal_resources(self) -> None:
        temp, root = self._work_root(
            "<!-- common-develop-work-mode: model_code=false page_design=true minimal=false auto=false architecture_review=false git_checkpoint=false git_push=false lightweight=false -->"
        )
        self.addCleanup(temp.cleanup)
        result = ai_context.resolve_ai_context(
            agent="DesignAgent",
            task_type="design",
            phase="design",
            prompt="页面业务设计",
            doc_root=str(root),
            version="V_TEST",
            target="REQ-001",
        )
        self.assertEqual("DesignAgent", result["phaseContract"]["ownerAgent"])
        self.assertEqual("OWNER", result["phaseContract"]["selectedAgentRole"])
        resource_ids = [item["id"] for item in result["resources"]]
        self.assertIn("page-design", resource_ids)
        self.assertIn("execution-core", resource_ids)
        self.assertIn("evidence.register", result["allowedCommands"])
        self.assertIn("long_task.start-attempt", result["taskContextCommands"])
        self.assertEqual("references/roles.md#DesignAgent", result["agentContract"]["roleReference"])
        self.assertIn("design:ArchitectureReviewAgent", result["reviewProfileIds"])
        self.assertTrue(result["projectFactPaths"]["requirement"].endswith("doc/REQ-001/requirement.md"))
        self.assertEqual("DesignAgent", result["executionCore"]["selectedAgent"])
        self.assertIn("scripted_state_transitions_only", result["executionCore"]["hardGates"])
        self.assertEqual(
            ["document-facts", "task-runtime", "review-verification"],
            [item["id"] for item in result["capabilityPacks"]["packs"]],
        )
        self.assertEqual(
            result["executionCore"]["capabilityPackIds"],
            [item["id"] for item in result["capabilityPacks"]["packs"]],
        )

    def test_normal_project_context_forbids_skill_implementation_reads(self) -> None:
        result = ai_context.resolve_ai_context(
            agent="DesignAgent",
            task_type="design",
            phase="design",
            prompt="页面业务设计",
        )
        policy = result["executionCore"]["skillImplementationReadPolicy"]
        self.assertEqual("FORBIDDEN", policy["default"])
        self.assertIn("scripts/*.py", policy["forbiddenGlobs"])
        self.assertIn("scripts/**/*.py", policy["forbiddenGlobs"])
        self.assertIn("scripts/_core/**", policy["forbiddenGlobs"])
        self.assertEqual("EXECUTE_ONLY", policy["entrypointPolicy"]["scripts/design_doc.py"])
        self.assertEqual(
            "INTERNAL_IMPLEMENTATION_FORBIDDEN",
            policy["entrypointPolicy"]["scripts/_core/document_utils.py"],
        )
        self.assertNotIn("scripts/design_doc.py", [item["path"] for item in result["resources"]])
        self.assertNotIn("scripts/_core/document_utils.py", [item["path"] for item in result["resources"]])

    def test_wk_ar_exposes_current_step_and_dynamic_reviewers(self) -> None:
        temp, root = self._work_root(
            "<!-- common-develop-work-mode: model_code=false page_design=false minimal=false auto=false architecture_review=true git_checkpoint=false git_push=false lightweight=false -->"
        )
        self.addCleanup(temp.cleanup)
        result = ai_context.resolve_ai_context(
            agent="ArchitectureReviewAgent",
            task_type="development",
            phase="development",
            doc_root=str(root),
            version="V_TEST",
            target="REQ-AR-001",
        )
        self.assertEqual("SKELETON", result["architectureReviewContext"]["step"])
        self.assertFalse(result["architectureReviewContext"]["canBeginConcreteImplementation"])
        self.assertEqual(
            ["ArchitectureReviewAgent", "SpecComplianceReviewAgent"],
            result["phaseContract"]["requiredReviewerAgents"],
        )
        self.assertEqual("REQUIRED_REVIEWER", result["phaseContract"]["selectedAgentRole"])
        self.assertEqual(
            ["development:ArchitectureReviewAgent", "development:SpecComplianceReviewAgent"],
            result["reviewProfileIds"],
        )
        self.assertIn("wk-ar-state-core", [item["id"] for item in result["resources"]])
        self.assertIn("advance-development-step", result["architectureReviewContext"]["transitionCommand"])

    def test_wk_ar_route_is_resolvable_with_unique_rule(self) -> None:
        result = ai_context.resolve_ai_context(agent="ProjectManagerAgent", task_type="wk_ar")
        ids = [item["id"] for item in result["resources"]]
        self.assertIn("wk-ar-state-core", ids)
        self.assertIn("review-result-contract", ids)

    def test_routing_budget_and_omissions_are_visible_to_ai(self) -> None:
        result = ai_context.resolve_ai_context(
            agent="DevelopAgent",
            task_type="development",
            prompt="执行回归测试并核对机器证据",
        )
        self.assertLessEqual(result["totalTokenBudget"], result["routingLimits"]["targetResolvedTokenBudget"])
        self.assertGreaterEqual(result["budgetHeadroom"], 0)
        self.assertIsInstance(result["omittedResources"], list)
        self.assertTrue(all(item.get("reason") for item in result["omittedResources"]))

    def test_completion_summary_is_machine_generated(self) -> None:
        result = ai_context.resolve_ai_context(
            agent="CompletionVerificationAgent",
            task_type="completion_verification",
            phase="completion_verification",
        )
        self.assertIn("completionGateSummary", result)
        self.assertEqual("NOT_REQUESTED", result["completionGateSummary"]["taskVerificationStatus"])
        self.assertIn("acceptance-core", result["completionGateSummary"]["detailResourceIds"])

    def test_evidence_context_ignores_missing_historical_source(self) -> None:
        temp, root = self._work_root("<!-- common-develop-work-mode: model_code=false page_design=false minimal=false auto=false architecture_review=false git_checkpoint=false git_push=false lightweight=false -->")
        self.addCleanup(temp.cleanup)
        work_path = root / "version" / "V_TEST" / "work.md"
        work_text = work_path.read_text(encoding="utf-8")
        mode = work_mode.WorkMode()
        work_path.write_text(
            work_text + mode.mutation_marker() + "\n" + mode.summary() + "\n",
            encoding="utf-8",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.command_init(argparse.Namespace(
                doc_root=str(root), version="V_TEST", target="CTX-TARGET", reuse=False,
                request_intake="", agent="ProjectManagerAgent",
            ))
        task_dir = root / "version" / "V_TEST" / "task" / "CTX-TARGET"
        state = long_task.runtime_state(task_dir)
        state["artifact_revisions"]["requirement_confirmation"].update({
            "status": "IN_PROGRESS", "revision": "REQCONF-R01@current",
        })
        document, _ = long_task.read_task_state_cache(task_dir)
        payloads, _, _ = long_task.append_task_events_payloads(
            task_dir, long_task.task_state_path(task_dir), document, state, [], synced_at=long_task.now()
        )
        long_task.transactional_text_write(payloads)
        current = task_dir / "evidence" / "current.txt"
        old = task_dir / "evidence" / "old.txt"
        current.write_text("current", encoding="utf-8")
        old.write_text("old", encoding="utf-8")
        evidence.register_evidence(
            task_dir, agent="RequirementConfirmationAgent", evidence_type="document_ref",
            ref="evidence/current.txt", revision="REQCONF-R01@current",
            phase="requirement_confirmation", scope_refs=["current"],
        )
        evidence.register_evidence(
            task_dir, agent="RequirementConfirmationAgent", evidence_type="document_ref",
            ref="evidence/old.txt", revision="REQCONF-R00@historical",
            phase="requirement_confirmation", scope_refs=["old"],
        )
        old.unlink()
        context = ai_context._evidence_context(
            str(root), "V_TEST", "CTX-TARGET", "requirement_confirmation",
            current_revision="REQCONF-R01@current",
        )
        self.assertEqual("PASSED", context["status"])
        self.assertEqual(2, context["currentPhaseArtifactCount"])

    def test_wk_v_route_is_resolvable_without_loading_all_references(self) -> None:
        result = ai_context.resolve_ai_context(agent="ProjectManagerAgent", task_type="wk_v")
        ids = [item["id"] for item in result["resources"]]
        self.assertIn("page-design-core", ids)
        self.assertLessEqual(len(ids), 12)

    def test_business_flow_context_loads_only_prompt_matched_group(self) -> None:
        temp, root = self._work_root(
            "<!-- common-develop-work-mode: model_code=false page_design=false minimal=false auto=false architecture_review=false git_checkpoint=false git_push=false lightweight=false -->"
        )
        self.addCleanup(temp.cleanup)
        flow_root = root / "docs" / "_flows"
        flow_root.mkdir(parents=True)
        simple_yaml.dump(
            {
                "schemaVersion": 1,
                "groups": [
                    {"code": "ORDER_LIFECYCLE", "name": "订单生命周期", "revision": "FLOW-R03@order"},
                    {"code": "USER_ACCESS", "name": "用户访问", "revision": "FLOW-R02@user"},
                ],
                "flows": [
                    {
                        "id": "FLOW-PAYMENT",
                        "name": "订单支付",
                        "group": "ORDER_LIFECYCLE",
                        "ownerModule": "PAY",
                        "participantModules": ["ORD", "PAY"],
                        "featureRefs": ["PAY0008-F02"],
                    },
                    {
                        "id": "FLOW-USER-LOGIN",
                        "name": "用户登录",
                        "group": "USER_ACCESS",
                        "ownerModule": "USR",
                        "participantModules": ["USR"],
                        "featureRefs": ["USR0001-F02"],
                    },
                ],
            },
            flow_root / "flow_index.yaml",
        )
        result = ai_context.resolve_ai_context(
            agent="RequirementAnalysisAgent",
            task_type="business_flow",
            phase="requirement_analysis",
            prompt="调整订单支付 FLOW-PAYMENT",
            doc_root=str(root),
            version="V_TEST",
            target="REQ-001",
        )
        self.assertEqual(["ORDER_LIFECYCLE"], [item["code"] for item in result["businessFlowContext"]["matchedGroups"]])
        self.assertEqual(["FLOW-PAYMENT"], [item["id"] for item in result["businessFlowContext"]["matchedFlows"]])
        self.assertIn("business-flow", [item["id"] for item in result["resources"]])


    def test_business_flow_context_matches_chinese_name_without_stable_id(self) -> None:
        temp, root = self._work_root(
            "<!-- common-develop-work-mode: model_code=false page_design=false minimal=false auto=false architecture_review=false git_checkpoint=false git_push=false lightweight=false -->"
        )
        self.addCleanup(temp.cleanup)
        flow_root = root / "docs" / "_flows"
        flow_root.mkdir(parents=True)
        simple_yaml.dump(
            {
                "schemaVersion": 1,
                "groups": [{"code": "ORDER_LIFECYCLE", "name": "订单生命周期", "revision": "FLOW-R03@order"}],
                "flows": [{
                    "id": "FLOW-PAYMENT", "name": "订单支付", "group": "ORDER_LIFECYCLE",
                    "ownerModule": "PAY", "participantModules": ["ORD", "PAY"],
                    "featureRefs": ["PAY0008-F02"],
                }],
            },
            flow_root / "flow_index.yaml",
        )
        result = ai_context.resolve_ai_context(
            agent="RequirementAnalysisAgent", task_type="business_flow",
            prompt="请调整订单支付流程", doc_root=str(root), version="V_TEST", target="REQ-001",
        )
        self.assertEqual(["FLOW-PAYMENT"], [item["id"] for item in result["businessFlowContext"]["matchedFlows"]])

    def test_cross_module_implementation_context_extracts_distinct_ids(self) -> None:
        temp, root = self._work_root(
            "<!-- common-develop-work-mode: model_code=false page_design=false minimal=false auto=false architecture_review=false git_checkpoint=false git_push=false lightweight=false -->"
        )
        self.addCleanup(temp.cleanup)
        result = ai_context.resolve_ai_context(
            agent="DesignAgent", task_type="design", phase="design",
            prompt="检查 CMI-ORDER-CREATE 的 CMSTEP-ORDER-03 补偿",
            doc_root=str(root), version="V_TEST", target="ORD0002",
        )
        context = result["crossModuleImplementationContext"]
        self.assertTrue(context["required"])
        self.assertEqual(["CMI-ORDER-CREATE"], context["mappingRefs"])
        self.assertEqual(["CMSTEP-ORDER-03"], context["stepRefs"])
        self.assertEqual("crossModuleImplementations", context["sourceField"])
        self.assertTrue(result["projectFactPaths"]["dependencyImpact"].endswith("docs/_relations/dependency_impact.yaml"))

    def test_cross_module_context_matches_chinese_mapping_name(self) -> None:
        temp, root = self._work_root(
            "<!-- common-develop-work-mode: model_code=false page_design=false minimal=false auto=false architecture_review=false git_checkpoint=false git_push=false lightweight=false -->"
        )
        self.addCleanup(temp.cleanup)
        relation_root = root / "docs" / "_relations"
        relation_root.mkdir(parents=True)
        simple_yaml.dump({
            "schemaVersion": 1,
            "crossModuleImplementations": [{
                "id": "CMI-ORDER-CREATE",
                "name": "跨模块订购实现映射",
                "businessFlowRef": "FLOW-ORDER-E2E",
                "businessStepRefs": ["STEP-ORDER-E2E-03"],
                "participants": [
                    {"id": "CMPART-ORDER", "module": "ORD", "responsibility": "编排"},
                    {"id": "CMPART-PAY", "module": "PAY", "responsibility": "支付"},
                ],
                "steps": [{"id": "CMSTEP-ORDER-01"}],
            }],
        }, relation_root / "dependency_impact.yaml")
        result = ai_context.resolve_ai_context(
            agent="DesignAgent", task_type="design", phase="design",
            prompt="请检查跨模块订购实现映射的补偿",
            doc_root=str(root), version="V_TEST", target="ORD0002",
        )
        context = result["crossModuleImplementationContext"]
        self.assertEqual(["CMI-ORDER-CREATE"], context["mappingRefs"])
        self.assertEqual("跨模块订购实现映射", context["matchedMappings"][0]["name"])
        self.assertEqual(["ORD", "PAY"], context["matchedMappings"][0]["participantModules"])

    def test_git_checkpoint_context_exposes_only_latest_checkpoint(self) -> None:
        temp, root = self._work_root(
            "<!-- common-develop-work-mode: model_code=false page_design=false minimal=false auto=true architecture_review=false git_checkpoint=true git_push=false lightweight=false -->"
        )
        self.addCleanup(temp.cleanup)
        task_dir = root / "version" / "V_TEST" / "task" / "REQ-001"
        task_dir.mkdir(parents=True)
        simple_yaml.dump(
            {
                "schema_version": 2,
                "target_id": "REQ-001",
                "version": "V_TEST",
                "checkpoints": [
                    {
                        "checkpoint_id": "CP-V_TEST-REQ-001-DESIGN-001",
                        "phase": "design",
                        "iteration_id": "ITER-REQ-001-DESIGN-001",
                        "iteration_no": 1,
                        "stage_revision": "DESIGN-R01@abc",
                        "stage_outcome_ref": "SO-DESIGN-001",
                        "branch": "wk/V_TEST/REQ-001",
                        "base_commit": "a" * 40,
                        "changed_paths": ["docs/design.md"],
                        "content_digest": "sha256:" + "b" * 64,
                        "commit_title": "docs(design): complete REQ-001 design",
                        "created_by_agent": "ProjectManagerAgent",
                        "created_at": "2026-07-23T00:00:00Z",
                        "supersedes_checkpoint_id": "",
                    },
                    {
                        "checkpoint_id": "CP-V_TEST-REQ-001-DEVELOPMENT-001",
                        "phase": "development",
                        "iteration_id": "ITER-REQ-001-DEVELOPMENT-001",
                        "iteration_no": 1,
                        "stage_revision": "DEV-R01@def",
                        "stage_outcome_ref": "SO-DEVELOPMENT-001",
                        "branch": "wk/V_TEST/REQ-001",
                        "base_commit": "c" * 40,
                        "changed_paths": ["src/change.py"],
                        "content_digest": "sha256:" + "d" * 64,
                        "commit_title": "feat(development): complete REQ-001 development",
                        "created_by_agent": "ProjectManagerAgent",
                        "created_at": "2026-07-23T01:00:00Z",
                        "supersedes_checkpoint_id": "",
                    },
                ],
            },
            task_dir / "git_checkpoints.yaml",
        )
        result = ai_context.resolve_ai_context(
            agent="ProjectManagerAgent", task_type="wk_gc", prompt="阶段提交",
            doc_root=str(root), version="V_TEST", target="REQ-001",
        )
        context = result["gitCheckpointContext"]
        self.assertTrue(context["enabled"])
        self.assertFalse(context["historyLoaded"])
        self.assertEqual("CP-V_TEST-REQ-001-DEVELOPMENT-001", context["latestCheckpoint"]["checkpoint_id"])
        self.assertNotIn("changed_paths", context["latestCheckpoint"])
        self.assertEqual("SEQUENTIAL", result["executionPolicy"]["mode"])

    def test_cli_outputs_json(self) -> None:
        completed = subprocess.run(
            [
                "python3",
                str(ROOT / "scripts" / "ai_context.py"),
                "resolve",
                "-g",
                "ProjectManagerAgent",
                "--task-type",
                "project_management",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(completed.stdout)
        self.assertEqual("ProjectManagerAgent", payload["selectedAgent"])
        self.assertEqual("INDEX_ONLY", json.loads((ROOT / "ai" / "source-map.json").read_text())["kind"])

    def _initialized_task_root(self) -> tuple[tempfile.TemporaryDirectory[str], Path, str]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name) / "project_doc"
        args = argparse.Namespace(doc_root=str(root), version="V_TEST", target="MOD0001", reuse=False)
        self.assertEqual(0, long_task.command_init(args))
        return temp, root, "MOD0001"

    def test_task_verification_and_lazy_context_view_are_embedded(self) -> None:
        temp, root, target = self._initialized_task_root()
        self.addCleanup(temp.cleanup)
        result = ai_context.resolve_ai_context(
            agent="RequirementConfirmationAgent",
            task_type="requirement_confirmation",
            phase="requirement_confirmation",
            doc_root=str(root),
            version="V_TEST",
            target=target,
        )
        self.assertEqual("PASSED", result["taskVerification"]["status"])
        self.assertEqual(target, result["contextView"]["targetId"])
        self.assertFalse(result["contextView"]["lazyObjectPolicy"]["loadEvidenceBodiesByDefault"])
        self.assertFalse(result["contextView"]["lazyObjectPolicy"]["loadReviewBodiesByDefault"])

    def test_project_fact_policy_prefers_manifest_and_trace_summary(self) -> None:
        result = ai_context.resolve_ai_context(
            agent="RequirementConfirmationAgent", task_type="requirement_confirmation",
            phase="requirement_confirmation", doc_root="", version="", target="",
        )
        self.assertNotIn("taskState", result.get("projectFactPaths", {}))
        result = ai_context.resolve_ai_context(
            agent="RequirementConfirmationAgent", task_type="requirement_confirmation",
            phase="requirement_confirmation", doc_root="", version="", target="",
        )
        self.assertIn("executionCore", result)

    def test_project_fact_policy_exposes_only_compact_direct_read_exceptions(self) -> None:
        temp, root = self._work_root(
            "<!-- common-develop-work-mode: model_code=false page_design=false minimal=false auto=false architecture_review=false git_checkpoint=false git_push=false lightweight=false -->"
        )
        self.addCleanup(temp.cleanup)
        result = ai_context.resolve_ai_context(
            agent="RequirementConfirmationAgent", task_type="requirement_confirmation",
            phase="requirement_confirmation", doc_root=str(root), version="V_TEST", target="REQ-001",
        )
        policy = result["projectFactReadPolicy"]
        self.assertEqual(["wk begin"], policy["defaultReadSet"])
        self.assertEqual(
            ["cache/current_manifest.json"],
            policy["directReadExceptions"],
        )
        self.assertEqual(["cache/current_manifest.json"], policy["conditionalReadSet"])
        self.assertEqual(["cache/current_impact.json"], policy["indirectReadSet"])
        self.assertIn("evidence.py query/show", policy["onDemandOnly"]["evidenceRegistry"])
        runtime_files = policy["runtimeFiles"]
        self.assertEqual("QUERY_ONLY", runtime_files["traceability.json"]["readMode"])
        self.assertEqual("CONDITIONAL_POINTER", runtime_files["cache/current_manifest.json"]["readMode"])
        self.assertEqual("FORBIDDEN_BY_DEFAULT", runtime_files["evidence/registry.jsonl"]["fullRead"])
        self.assertIn("evidence_id", runtime_files["evidence/registry.jsonl"]["selectors"])
        self.assertEqual("QUERY_ONLY", runtime_files["development_task_plan.json"]["readMode"])
        self.assertIn("task_plan.py status/query", policy["onDemandOnly"]["developmentTaskPlan"])
        self.assertNotIn("taskEvents", result["projectFactPaths"])

        implementation = ai_context.resolve_ai_context(
            agent="ImplementationPlanAgent", task_type="implementation_plan",
            phase="implementation_plan", doc_root=str(root), version="V_TEST", target="REQ-001",
        )
        self.assertNotIn("developmentTaskPlan", implementation["projectFactPaths"])
        semantic_inputs = ai_context._task_semantic_inputs(
            str(root), "V_TEST", "REQ-001", "implementation_plan", implementation["projectFactPaths"]
        )
        plan_input = next(item for item in semantic_inputs if item["id"] == "developmentTaskPlan")
        self.assertEqual("QUERY_ONLY", plan_input["readMode"])
        self.assertIn("task_plan.py query", plan_input["ref"])

    def test_token_budget_compacts_context_without_dropping_gate_and_view(self) -> None:
        temp, root, target = self._initialized_task_root()
        self.addCleanup(temp.cleanup)
        result = ai_context.resolve_ai_context(
            agent="RequirementConfirmationAgent",
            task_type="requirement_confirmation",
            phase="requirement_confirmation",
            doc_root=str(root),
            version="V_TEST",
            target=target,
            token_budget=1200,
        )
        self.assertIn("taskVerification", result)
        self.assertIn("contextView", result)
        self.assertLessEqual(result["contextBudget"]["estimatedTokens"], 1500)
        self.assertGreater(len(result["contextBudget"]["omitted"]), 0)

    def test_since_checkpoint_returns_unchanged_context_on_second_read(self) -> None:
        temp, root, target = self._initialized_task_root()
        self.addCleanup(temp.cleanup)
        first = ai_context.resolve_ai_context(
            agent="RequirementConfirmationAgent", task_type="requirement_confirmation",
            phase="requirement_confirmation", doc_root=str(root), version="V_TEST", target=target,
            since_checkpoint=True,
        )
        second = ai_context.resolve_ai_context(
            agent="RequirementConfirmationAgent", task_type="requirement_confirmation",
            phase="requirement_confirmation", doc_root=str(root), version="V_TEST", target=target,
            since_checkpoint=True,
        )
        self.assertFalse(first["contextDelta"]["unchanged"])
        self.assertTrue(second["contextDelta"]["unchanged"])
        self.assertEqual("UNCHANGED", second["contextView"]["status"])


if __name__ == "__main__":
    unittest.main()
