#!/usr/bin/env python3
"""Self-tests for the package consistency checker."""

from __future__ import annotations

import json
import re
import unittest

import check_skill
import review_contract
import contract_normalization
from _check_skill import invariant_checks, mode_combination_checks


class CheckSkillTests(unittest.TestCase):
    def test_current_package_passes_all_static_checks(self) -> None:
        result = check_skill.run_checks()
        self.assertEqual([], result.errors, "\n".join(result.errors))
        self.assertGreaterEqual(len(result.checks), 8)

    def test_work_mode_combination_contract_is_satisfiable(self) -> None:
        contract = contract_normalization.load_runtime_contract(check_skill.ASSETS / "runtime-contract.json")
        result = check_skill.CheckResult()
        mode_combination_checks.check_work_mode_combination_satisfiability(contract, result)
        self.assertEqual([], result.errors, "\n".join(result.errors))
        details = result.details["workModeCombinationCheck"]
        self.assertEqual(512, details["enumeratedCombinations"])
        self.assertEqual(384, details["dependencyValidCombinations"])
        self.assertEqual(0, details["conflicts"])

    def test_predecessor_graph_cycle_is_rejected(self) -> None:
        contract = json.loads(json.dumps(contract_normalization.load_runtime_contract(check_skill.ASSETS / "runtime-contract.json")))
        phases = {phase["id"]: phase for phase in contract["canonicalPhases"]}
        phases["requirement_confirmation"]["requiredPredecessors"] = ["design"]

        result = check_skill.CheckResult()
        mode_combination_checks.check_work_mode_combination_satisfiability(contract, result)

        self.assertTrue(
            any("skipped phase predecessor graph contains a cycle" in error for error in result.errors),
            "expected the injected predecessor cycle to be reported",
        )

    def test_work_mode_combination_check_catches_column_conflict(self) -> None:
        contract = json.loads(json.dumps(contract_normalization.load_runtime_contract(check_skill.ASSETS / "runtime-contract.json")))
        contract["commandInterface"]["workModes"]["lightweight"]["workColumns"].append("架构代码")
        result = check_skill.CheckResult()
        mode_combination_checks.check_work_mode_combination_satisfiability(contract, result)
        self.assertTrue(any("architectureReview" in error and "lightweight" in error for error in result.errors))

    def test_work_mode_combination_check_catches_missing_profile_mutex(self) -> None:
        contract = json.loads(json.dumps(contract_normalization.load_runtime_contract(check_skill.ASSETS / "runtime-contract.json")))
        contract["workflowProfiles"]["SIMPLE_OPTIMIZATION_FLOW"]["incompatibleWorkModes"].remove("lightweight")
        result = check_skill.CheckResult()
        mode_combination_checks.check_work_mode_combination_satisfiability(contract, result)
        self.assertTrue(any("SIMPLE_OPTIMIZATION_FLOW" in error and "lightweight" in error for error in result.errors))

    def test_document_invariant_registry_and_markers_pass(self) -> None:
        result = check_skill.CheckResult()
        invariant_checks.check_doc_invariant_drift(result)
        self.assertEqual([], result.errors, "\n".join(result.errors))
        details = result.details["docInvariantDrift"]
        self.assertEqual(5, details["invariants"])
        self.assertEqual({"references/work-mode-core.md": 5}, details["owners"])

    def test_document_invariant_checker_rejects_repeated_owner_text(self) -> None:
        registry = json.loads((check_skill.ASSETS / "doc-invariants.json").read_text(encoding="utf-8"))
        item = next(item for item in registry["invariants"] if item["id"] == "LIGHTWEIGHT_CLOSE_NON_RETROACTIVE")
        marker = invariant_checks.MARKER_TEMPLATE.format(id=item["id"])
        owner_text = "\n".join(item["ownerPhrases"]) + "\n" + marker
        reference_text = "\n".join(item["forbiddenReferencePhrases"]) + "\n" + marker
        result = check_skill.CheckResult()
        invariant_checks._check_invariant(
            item,
            {item["owner"]: owner_text, item["referenceFiles"][0]: reference_text},
            result,
        )
        self.assertTrue(any("fully repeated" in error for error in result.errors))

    def test_document_invariant_checker_rejects_unmarked_trigger(self) -> None:
        registry = json.loads((check_skill.ASSETS / "doc-invariants.json").read_text(encoding="utf-8"))
        item = next(item for item in registry["invariants"] if item["id"] == "GIT_PUSH_REQUIRES_CHECKPOINT")
        marker = invariant_checks.MARKER_TEMPLATE.format(id=item["id"])
        owner_text = "\n".join(item["ownerPhrases"]) + "\n" + marker
        result = check_skill.CheckResult()
        invariant_checks._check_invariant(
            item,
            {item["owner"]: owner_text, "references/unmarked.md": "`wk -gp` 依赖 checkpoint"},
            result,
        )
        self.assertTrue(any("without its marker" in error for error in result.errors))

    def test_document_examples_use_implicit_existing_task_sync(self) -> None:
        documents = [check_skill.ROOT / "SKILL.md", *check_skill.REFERENCES.rglob("*.md"), check_skill.ASSETS / "work.template.md"]
        explicit = []
        for path in documents:
            for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "--sync-existing-tasks" in line and "--no-sync-existing-tasks" not in line:
                    explicit.append(f"{path}:{line_no}")
        self.assertEqual([], explicit)

    def test_scripts_directory_contains_no_unittest_modules(self) -> None:
        self.assertEqual([], sorted(path.name for path in (check_skill.ROOT / "scripts").glob("test_*.py")))
        self.assertTrue((check_skill.ROOT / "scripts" / "independent_test_environment.py").is_file())
        self.assertTrue((check_skill.ROOT / "tests" / "test_independent_test_environment.py").is_file())
        self.assertTrue((check_skill.ROOT / "tests" / "internal" / "unittest_worker.py").is_file())

    def test_focused_tdd_references_preserve_execution_rules(self) -> None:
        required = {
            "tdd-core.md": ["wk -min", "纯重构不人为制造 RED"],
            "test-matrix-design.md": ["FLOW/STEP", "CMI/CMSTEP"],
            "red-green-refactor.md": ["有效 RED", "BUG 回归至少证明"],
            "test-seam.md": ["Highest stable seam", "Test Double"],
            "test-persistence.md": ["READY", "SKIPPED", "independent_test_environment.py"],
            "test-data-isolation.md": ["禁止无条件清空业务表"],
            "test-coverage-complexity.md": ["harness_env.md", "复杂度阈值"],
            "tdd-evidence.md": ["StageOutcome", "旧 GREEN 不能证明新 revision"],
            "tdd-role-boundary.md": ["实现者不能作为唯一测试证据 Reviewer"],
        }
        for filename, tokens in required.items():
            text = (check_skill.REFERENCES / filename).read_text(encoding="utf-8")
            for token in tokens:
                self.assertIn(token, text, filename)

    def test_references_use_consolidated_section_aware_cores(self) -> None:
        routing = contract_normalization.load_context_routing(check_skill.ROOT / "context-routing.json")
        catalog = routing["resourceCatalog"]
        consolidated = {
            "orchestration-core": ["orchestration-dispatch", "orchestration-review", "orchestration-retry", "orchestration-handoff", "orchestration-recording", "agent-orchestration"],
            "requirement-confirmation-core": ["requirement-confirmation", "requirement-fact-location", "requirement-id-revision", "requirement-scope-lock"],
            "archive-core": ["archive-preview", "archive-apply", "archive-review-gate", "archive-reference-check", "archive-recovery"],
            "html-core": ["html-command-semantics", "html-page-structure", "html-accessibility", "html-project-process", "html-test-case", "html-bug-report"],
            "lifecycle-core": ["lifecycle-phases", "lifecycle-rework"],
            "long-task-core": ["long-task-review-evidence", "long-task-recovery"],
            "review-core": ["review-execution-core", "review-finding-lifecycle"],
            "review-evidence": ["review-evidence-contract"],
            "documentation-core": ["work-documentation", "engineering-documents", "document-rendering"],
        }
        for core_id, fragmented_ids in consolidated.items():
            self.assertEqual("relevant_sections", catalog[core_id]["readStrategy"])
            for fragmented_id in fragmented_ids:
                self.assertNotIn(fragmented_id, catalog)
                self.assertFalse((check_skill.REFERENCES / f"{fragmented_id}.md").exists())

    def test_runtime_assets_exclude_test_fixtures(self) -> None:
        self.assertFalse(list(check_skill.STRUCTURED.glob("*.yaml")))
        self.assertTrue((check_skill.TEST_FIXTURES / "context-routing-golden.json").is_file())
        self.assertTrue((check_skill.STRUCTURED_FIXTURES / "dependency_impact.yaml").is_file())

    def test_initial_task_state_is_programmatic_and_contract_driven(self) -> None:
        result = check_skill.CheckResult()
        long_task = check_skill.import_long_task(result)
        self.assertIsNotNone(long_task)
        self.assertEqual([], result.errors)
        state = long_task.build_initial_task_state(
            "TARGET", "V_TEST", "ProjectManagerAgent",
            {
                "ref": "version/V_TEST/work.md", "digest": "0" * 64,
                "model_code": False, "page_design": False, "minimal": False,
                "auto": False, "architecture_review": False, "git_checkpoint": False,
            },
            "2026-01-01T00:00:00Z",
        )
        self.assertEqual(long_task.PHASES, list(state["artifact_revisions"]))
        self.assertEqual(set(long_task.COLLABORATION_REVIEWERS), set(state["collaboration_reviews"]))
        self.assertEqual("RequirementConfirmationAgent", state["next_agent"])

    def test_current_contract_sources_are_normalized_and_materialize(self) -> None:
        runtime_raw = json.loads((check_skill.ASSETS / "runtime-contract.json").read_text(encoding="utf-8"))
        review_raw = json.loads((check_skill.ASSETS / "review-contract.json").read_text(encoding="utf-8"))
        routing_raw = json.loads((check_skill.ROOT / "context-routing.json").read_text(encoding="utf-8"))
        self.assertEqual(2, runtime_raw["schemaVersion"])
        self.assertNotIn("scriptPermissions", runtime_raw)
        self.assertIn("scriptPermissionProfiles", runtime_raw)
        self.assertIn("scriptPermissionAssignments", runtime_raw)
        self.assertEqual(2, review_raw["schemaVersion"])
        self.assertEqual(False, review_raw["criterionDefaults"]["allowNotApplicable"])
        self.assertTrue(all("profileId" not in profile and "phase" not in profile and "reviewerAgent" not in profile for profile in review_raw["profiles"].values()))
        self.assertEqual(2, routing_raw["schemaVersion"])
        self.assertEqual("references/{id}.md", routing_raw["resourceDefaults"]["pathTemplate"])
        self.assertNotIn("baseRouteTokenBudgetTargets", routing_raw["routingPolicy"])
        self.assertIn("baseRouteBudgetGroups", routing_raw["routingPolicy"])
        runtime = contract_normalization.materialize_runtime_contract(runtime_raw)
        review = contract_normalization.materialize_review_contract(review_raw)
        routing = contract_normalization.materialize_context_routing(routing_raw)
        self.assertIn("check_skill.check", runtime["scriptPermissions"])
        self.assertEqual("requirement_confirmation", review["profiles"]["requirement_confirmation:RequirementAnalysisAgent"]["phase"])
        self.assertEqual("references/runtime-contract.md", routing["resourceCatalog"]["runtime-contract"]["path"])
        self.assertIn("tdd", routing["routingPolicy"]["baseRouteTokenBudgetTargets"])

    def test_runtime_contract_declares_current_agent_selector(self) -> None:
        contract = contract_normalization.load_runtime_contract(check_skill.ASSETS / "runtime-contract.json")
        selector = contract["commandInterface"]["currentAgent"]
        self.assertEqual(["-g", "--agent"], selector["flags"])
        self.assertEqual("ProjectManagerAgent", selector["default"])
        self.assertTrue(selector["enforcePhaseOwnerOrReviewer"])
        self.assertTrue(selector["requiredForScripts"])
        self.assertEqual(
            {
                "taskState": "current_agent",
                "stageResult": "produced_by_agent",
                "handoff": "from_agent",
            },
            selector["recordFields"],
        )
        self.assertEqual("#/scriptPermissions", selector["authorizationRef"])
        self.assertEqual(2, contract["schemaVersion"])
        self.assertEqual("1.0", contract["packageVersion"])
        self.assertEqual(2, contract["longTaskSchemaVersion"])
        self.assertEqual(4, contract["workRecordSchemaVersion"])
        self.assertEqual(
            {
                "mode": "SEQUENTIAL",
                "allowParallelTasks": False,
                "oneActiveTargetAtATime": True,
                "multiTargetOrder": "DECLARED_ORDER",
                "multiTargetOrderSource": "version/{VERSION}/project_process.md",
                "description": "All targets and plan tasks execute sequentially. A, B, and C may be grouped by lifecycle stage, but only one target or task may be active at a time in the declared order.",
            },
            contract["executionPolicy"],
        )
        project_management = contract["projectManagement"]
        self.assertEqual("project_manager_agent", project_management["taskStateField"])
        self.assertEqual("ProjectManagerAgent", project_management["defaultAgent"])
        self.assertIn("CHANGE_CONTROL", project_management["capabilities"])
        work_modes = contract["commandInterface"]["workModes"]
        self.assertEqual(["-mc"], work_modes["modelCode"]["flags"])
        self.assertEqual(["前端伪代码", "后端伪代码"], work_modes["modelCode"]["workColumns"])
        self.assertEqual(["-v"], work_modes["pageDesign"]["flags"])
        self.assertEqual(["页面设计"], work_modes["pageDesign"]["workColumns"])
        self.assertFalse(work_modes["pageDesign"]["default"])
        self.assertEqual(["-min"], work_modes["minimal"]["flags"])
        self.assertEqual(["测试用例", "测试"], work_modes["minimal"]["workColumns"])
        minimal = work_modes["minimal"]
        auto = work_modes["auto"]
        architecture_review = work_modes["architectureReview"]
        git_checkpoint = work_modes["gitCheckpoint"]
        self.assertEqual(["test_design", "testing"], minimal["skippedPhases"])
        self.assertEqual("SKIPPED", minimal["phaseOutcome"])
        self.assertEqual("NOT_APPLICABLE", minimal["archiveGateStatus"])
        self.assertIn("wk -min", minimal["skipReason"])
        self.assertEqual(
            {
                "requirement_confirmation": ["TestDesignAgent"],
                "requirement_analysis": ["TestDesignAgent"],
                "business_model": ["TestDesignAgent"],
                "design": ["TestDesignAgent"],
                "implementation_plan": ["TestDesignAgent"],
                "development": ["TDDReviewAgent"],
            },
            minimal["suppressedReviewerAssignments"],
        )
        self.assertEqual(
            [
                "compile",
                "type_check",
                "static_analysis",
                "build",
                "spec_compliance_review",
                "engineering_standards_review",
                "untested_risk_statement",
            ],
            minimal["requiredNonTestEvidence"],
        )
        phases = {phase["id"]: phase for phase in contract["canonicalPhases"]}
        for phase_id in minimal["skippedPhases"]:
            self.assertEqual("workMode.minimal", phases[phase_id]["skipWhen"])
        self.assertEqual(["-auto"], auto["flags"])
        self.assertFalse(auto["default"])
        self.assertEqual("AUTOMATED", auto["confirmationPolicy"]["mode"])
        self.assertFalse(auto["confirmationPolicy"]["userConfirmationRequired"])
        self.assertTrue(auto["confirmationPolicy"]["autoAdvance"])
        self.assertEqual("BATCH_FORWARD_ONLY", auto["confirmationPolicy"]["stopScope"])
        self.assertIn("authority_conflict", auto["confirmationPolicy"]["remediationStopWhen"])
        self.assertTrue(auto["remediationPolicy"]["enabled"])
        self.assertTrue(auto["remediationPolicy"]["revisionPolicy"]["codeConfigOrTestChangeRequiresNewExactRevision"])
        self.assertEqual("DETECT_BEFORE_AR", auto["architectureBoundaryPolicy"]["mode"])
        self.assertFalse(auto["architectureBoundaryPolicy"]["ordinaryDevelopmentRepeatsSkeletonGate"])
        self.assertTrue(auto["finalHeadGatePolicy"]["bindToTrueFinalPrHead"])
        self.assertEqual(["-ar"], architecture_review["flags"])
        self.assertFalse(architecture_review["default"])
        self.assertEqual(["架构代码"], architecture_review["workColumns"])
        self.assertEqual("ARCHITECTURE_SKELETON", architecture_review["policy"]["firstStep"]["id"])
        self.assertEqual("CONCRETE_IMPLEMENTATION", architecture_review["policy"]["secondStep"]["id"])
        self.assertEqual("IMPLEMENTATION_READY_FOR_REVIEW", architecture_review["policy"]["secondStep"]["readyForFinalReviewStatus"])
        self.assertEqual("IMPLEMENTATION_REVIEW_PASSED", architecture_review["policy"]["finalCodeReview"]["passedStatus"])
        self.assertEqual(["-gc", "--git-checkpoint"], git_checkpoint["flags"])
        self.assertFalse(git_checkpoint["default"])
        self.assertEqual("assets/runtime-contract.json#/executionPolicy", git_checkpoint["policy"]["executionPolicyRef"])
        self.assertFalse(git_checkpoint["policy"]["autoPush"])
        self.assertFalse(git_checkpoint["policy"]["autoMerge"])
        self.assertTrue(work_modes["combinationAllowed"])
        independent_db = contract["testExecution"]["independentDatabase"]
        self.assertEqual(
            {
                "url": ["TEST_DB_URL"],
                "username": ["TEST_DB_USER"],
                "password": ["TEST_DB_PASSWORD"],
            },
            independent_db["requiredEnvironmentGroups"],
        )
        self.assertEqual("SKIPPED", independent_db["missingStatus"])
        self.assertTrue(independent_db["neverFallbackToBusinessDatabase"])

    def test_routing_has_single_path_and_budget_catalog(self) -> None:
        routing = contract_normalization.load_context_routing(check_skill.ROOT / "context-routing.json")
        self.assertEqual(2, routing["schemaVersion"])
        self.assertIn("resourceCatalog", routing)
        self.assertNotIn("resourceTokenBudgets", routing)
        entry = routing["resourceCatalog"]["runtime-contract"]
        self.assertEqual("references/runtime-contract.md", entry["path"])
        self.assertGreater(entry["tokenBudget"], 0)
        self.assertEqual(
            ["fixedResources", "exactTaskRoute", "modeOverrides", "matchingPromptConditions"],
            routing["routingPolicy"]["order"],
        )
        self.assertEqual(6500, routing["routingPolicy"]["targetResolvedTokenBudget"])
        self.assertEqual(5500, routing["routingPolicy"]["reservedTaskFactTokenBudget"])
        self.assertEqual(1000, routing["routingPolicy"]["maxFixedTokenBudget"])
        expected_targets = {
            task_type: group["tokenBudget"]
            for group in routing["routingPolicy"]["baseRouteBudgetGroups"]
            for task_type in group["taskTypes"]
        }
        self.assertEqual(
            expected_targets,
            routing["routingPolicy"]["baseRouteTokenBudgetTargets"],
        )
        self.assertEqual(
            ["execution-core"],
            routing["fixedResources"],
        )
        self.assertEqual(
            "priority_order_stop_before_exceeding",
            routing["routingPolicy"]["budgetSelection"],
        )
        minimal = routing["modeOverrides"]["minimal"]
        self.assertEqual(["test_design", "testing"], minimal["skipTaskTypes"])
        for resource_id in (
            "testing-core", "test-environment", "test-evidence",
            "integration-testing", "test-completion", "tdd-core", "test-matrix-design",
            "red-green-refactor", "test-seam", "tdd-evidence", "completion-gate-core",
        ):
            self.assertIn(resource_id, minimal["excludeResources"])
        auto = routing["modeOverrides"]["auto"]
        self.assertIn("confirmationPolicyRef", auto)
        self.assertEqual("BATCH_FORWARD_ONLY", auto["stopScope"])
        self.assertIn("failed_or_blocked_review", auto["stopOn"])
        self.assertIn("ci_failure", auto["stopOn"])
        self.assertIn("authority_conflict", auto["remediationStopOn"])
        self.assertIn("remediationPolicyRef", auto)
        self.assertIn("semanticPreflightPolicyRef", auto)
        self.assertIn("architectureBoundaryPolicyRef", auto)
        self.assertIn("finalHeadGatePolicyRef", auto)
        architecture_review = routing["modeOverrides"]["architectureReview"]
        self.assertEqual(["implementation_plan", "development", "code_review"], architecture_review["taskTypes"])
        self.assertEqual(["wk-ar-state-core"], architecture_review["includeResources"])
        checkpoint = routing["modeOverrides"]["gitCheckpoint"]
        self.assertEqual("assets/runtime-contract.json#/executionPolicy", checkpoint["executionPolicyRef"])
        self.assertIn("git_checkpoint.py commit", checkpoint["beforeAdvanceCommand"])
        git_push = routing["modeOverrides"]["gitPush"]
        self.assertEqual("gitCheckpoint", git_push["requiresMode"])
        self.assertEqual("assets/runtime-contract.json#/executionPolicy", git_push["executionPolicyRef"])
        self.assertIn("git_checkpoint.py push", git_push["afterCheckpointCommand"])

    def test_work_mode_routes_are_split_by_mode(self) -> None:
        routing = contract_normalization.load_context_routing(check_skill.ROOT / "context-routing.json")
        routes = {
            task: route["resources"]
            for route in routing["routes"]
            for task in route["taskTypes"]
            if task in {"work_mode", "wk_mc", "wk_v", "wk_min", "wk_auto", "wk_ar", "wk_gc", "wk_gp"}
        }
        self.assertEqual(
            ["model-code-core", "implementation-contract-summary"],
            routes["wk_mc"],
        )
        self.assertEqual(
            ["page-design-core", "work-mode-core"],
            routes["wk_v"],
        )
        self.assertEqual(
            ["work-mode-core", "test-completion"],
            routes["wk_min"],
        )
        self.assertNotIn("implementation-contract", routes["wk_min"])
        self.assertEqual(
            ["auto-mode-core", "orchestration-core", "completion-gate-core"],
            routes["wk_auto"],
        )
        self.assertEqual(
            ["wk-ar-state-core", "review-result-contract"],
            routes["wk_ar"],
        )
        self.assertEqual(
            ["git-checkpoint-core", "work-mode-core"],
            routes["wk_gc"],
        )
        self.assertEqual(
            ["git-checkpoint-core", "work-mode-core"],
            routes["wk_gp"],
        )

    def test_review_route_uses_minimal_resources(self) -> None:
        routing = contract_normalization.load_context_routing(check_skill.ROOT / "context-routing.json")
        route = next(item for item in routing["routes"] if "code_review" in item["taskTypes"])
        self.assertEqual(
            ["review-core", "review-result-contract", "review-evidence"],
            route["resources"],
        )


    def test_long_task_record_contract_uses_only_current_schema_fields(self) -> None:
        record = check_skill.load_json(
            check_skill.ASSETS / "long-task" / "record-contract.json",
            check_skill.CheckResult(),
        )
        self.assertEqual(2, record["schemaVersion"])
        self.assertEqual(2, record["longTaskSchemaVersion"])
        fields = {
            field
            for definition in record["records"].values()
            for field in definition["allowedFields"]
        }
        self.assertTrue({
            "verification_evidence_ids",
            "command_evidence_ids",
            "evidence_ids",
        }.issubset(fields))
        self.assertNotIn("validation_evidence_ids", record["records"]["taskPlanItem"]["allowedFields"])
        self.assertTrue({
            "validation_evidence",
            "verification_evidence",
            "command_refs",
            "evidence_refs",
        }.isdisjoint(fields))
        self.assertNotIn("parallel_group", fields)

    def test_merge_plan_schema_records_page_design_mode(self) -> None:
        schema = json.loads((check_skill.STRUCTURED / "merge-plan.schema.json").read_text(encoding="utf-8"))
        work_mode = schema["$defs"]["workMode"]
        self.assertEqual(
            ["modelCode", "pageDesign", "minimal", "auto", "architectureReview", "gitCheckpoint", "gitPush", "reviewOnly", "lightweight", "lightweightWaived", "skippedPhases", "workRef", "workDigest"],
            work_mode["required"],
        )
        self.assertEqual(work_mode["required"], list(work_mode["properties"]))
        self.assertIn("artifactMerges", schema["required"])
        self.assertIn("conflictDecisions", schema["required"])
        self.assertNotIn("documentMerges", schema["properties"])

    def test_git_push_connector_fallback_is_fail_closed_and_dual_sha(self) -> None:
        runtime = contract_normalization.load_runtime_contract(check_skill.ASSETS / "runtime-contract.json")
        policy = runtime["commandInterface"]["workModes"]["gitPush"]["policy"]
        self.assertEqual(["NATIVE_GIT", "GITHUB_CONNECTOR"], policy["transportOrder"])
        self.assertEqual("EXACT_COMMIT_SHA", policy["nativeTransport"]["identityMode"])
        self.assertEqual(
            ["native_transport_unavailable", "native_auth_unavailable"],
            policy["nativeTransport"]["fallbackOn"],
        )
        self.assertEqual(
            ["git_divergence", "remote_checkpoint_verification_failed"],
            policy["nativeTransport"]["neverFallbackOn"],
        )
        connector = policy["connectorFallback"]
        self.assertTrue(connector["enabled"])
        self.assertEqual("github.com", connector["supportedRemoteHost"])
        self.assertEqual("EXACT_TREE_MESSAGE_EQUIVALENT", connector["identityMode"])
        self.assertTrue(connector["remoteCommitShaMayDifferFromLocal"])
        self.assertFalse(connector["rewriteLocalHistory"])
        self.assertTrue(connector["receiptRequiredBeforeRemoteCheckpointed"])
        self.assertFalse(connector["phaseAdvanceWhileConnectorPending"])
        self.assertEqual(900, connector["verifiedReceiptTtlSeconds"])
        self.assertTrue((check_skill.ASSETS / "git-connector-request.schema.json").is_file())
        self.assertTrue((check_skill.ASSETS / "git-connector-receipt.schema.json").is_file())
        self.assertTrue((check_skill.ROOT / "scripts" / "git_connector_transport.py").is_file())

    def test_git_checkpoint_schema_is_compact_v2(self) -> None:
        schema = json.loads((check_skill.ASSETS / "git-checkpoint.schema.json").read_text(encoding="utf-8"))
        self.assertEqual({"const": 2}, schema["properties"]["schema_version"])
        fields = schema["properties"]["checkpoints"]["items"]["properties"]
        self.assertNotIn("decision", fields)
        self.assertNotIn("commit_locator", fields)
        self.assertIn("stage_outcome_ref", fields)
        self.assertIn("content_digest", fields)

    def test_requirement_document_contract_supports_single_and_multi_feature_requirements(self) -> None:
        template = (check_skill.STRUCTURED / "requirement.template.md").read_text(encoding="utf-8")
        for heading in (
            "## 1. 需求信息", "## 5. 功能列表", "## 6. 功能详细需求",
            "## 7. 跨功能规则", "## 9. 验收标准", "## 13. 追踪关系", "## 14. 变更记录",
        ):
            self.assertIn(heading, template)
        self.assertIn("{REQUIREMENT_ID}-F01", template)
        self.assertIn("BR-{REQUIREMENT_ID}-001", template)
        self.assertIn("AC-{REQUIREMENT_ID}-001", template)
        self.assertIn("TR-{REQUIREMENT_ID}-001", template)
        example = (check_skill.STRUCTURED_FIXTURES / "requirement.multi-feature.example.md").read_text(encoding="utf-8")
        self.assertIn("DEMO1001-F01", example)
        self.assertIn("DEMO1001-F02", example)
        self.assertIn("DEMO1001-F03", example)
        contract = contract_normalization.load_runtime_contract(check_skill.ASSETS / "runtime-contract.json")
        self.assertEqual(
            {"mode": "STATIC", "allowedAgents": ["ProjectManagerAgent", "RequirementConfirmationAgent"]},
            contract["scriptPermissions"]["requirement_doc.init"],
        )
        self.assertEqual("DECLARED_AGENT", contract["scriptPermissions"]["requirement_doc.validate"]["mode"])
        self.assertEqual("DECLARED_AGENT", contract["scriptPermissions"]["requirement_doc.status"]["mode"])
        self.assertEqual(
            {"mode": "STATIC", "allowedAgents": ["ProjectManagerAgent", "RequirementAnalysisAgent"]},
            contract["scriptPermissions"]["requirement_doc.sync-traceability"],
        )

    def test_design_document_contract_is_implementation_ready(self) -> None:
        template = (check_skill.STRUCTURED / "design.template.md").read_text(encoding="utf-8")
        for token in (
            "DESIGN-TABLE-CHANGES", "DESIGN-FIELD-CHANGES", "DESIGN-API-CHANGES",
            "DESIGN-DATA-ACCESS", "DESIGN-BEHAVIOR-ADJUSTMENTS", "DESIGN-CODE-BLUEPRINT",
            "写入时机与保存方式", "当前行为与证据", "具体改动",
        ):
            self.assertIn(token, template)
        contract = contract_normalization.load_runtime_contract(check_skill.ASSETS / "runtime-contract.json")
        self.assertEqual(
            {"mode": "STATIC", "allowedAgents": ["ProjectManagerAgent", "DesignAgent"]},
            contract["scriptPermissions"]["design_doc.init"],
        )
        self.assertEqual("DECLARED_AGENT", contract["scriptPermissions"]["design_doc.validate"]["mode"])

    def test_project_description_file_is_absent_from_active_references(self) -> None:
        enum_text = (check_skill.REFERENCES / "enum.md").read_text(encoding="utf-8")
        retired_name = "project_" + "dev_desc.md"
        self.assertNotIn(retired_name, enum_text)
        self.assertIn(
            "DOC_ROOT/version/{VERSION}/doc/{MODULE_CODE}/{MODULE_CODE}_desc.md",
            enum_text,
        )

    def test_context_catalog_is_self_describing_and_does_not_repeat_fixed_resources(self) -> None:
        routing = contract_normalization.load_context_routing(check_skill.ROOT / "context-routing.json")
        fixed = set(routing["fixedResources"])
        for resource_id, definition in routing["resourceCatalog"].items():
            self.assertTrue(definition["purpose"], resource_id)
            self.assertIn(definition["readStrategy"], {"full", "relevant_sections"})
        for route in routing["routes"]:
            self.assertTrue(fixed.isdisjoint(route["resources"]), route)
        for condition in routing["promptConditions"]:
            self.assertTrue(fixed.isdisjoint(condition["resources"]), condition)
            self.assertTrue(condition["id"])
            self.assertIsInstance(condition["priority"], int)


    def test_selective_reference_reader_is_declared_and_version_neutral(self) -> None:
        contract = contract_normalization.load_runtime_contract(check_skill.ASSETS / "runtime-contract.json")
        self.assertEqual({"mode": "DECLARED_AGENT"}, contract["scriptPermissions"]["reference_reader.index"])
        self.assertEqual({"mode": "DECLARED_AGENT"}, contract["scriptPermissions"]["reference_reader.read"])
        reader_contract = contract["taskContextOptimization"]["context"]["referenceReader"]
        self.assertEqual("scripts/reference_reader.py", reader_contract["script"])
        self.assertIn("--section {SECTION_ID}", reader_contract["readSectionCommand"])
        self.assertIn("--query {TOPIC}", reader_contract["readQueryCommand"])
        routing = contract_normalization.load_context_routing(check_skill.ROOT / "context-routing.json")
        relevant = [
            resource_id for resource_id, definition in routing["resourceCatalog"].items()
            if definition["readStrategy"] == "relevant_sections"
        ]
        self.assertTrue(relevant)
        for resource_id in relevant:
            index = check_skill.reference_reader.index_resource(resource_id)
            self.assertTrue(index["sections"], resource_id)

    def test_documentation_separates_fixed_on_demand_and_generated_paths(self) -> None:
        text = (check_skill.REFERENCES / "documentation-core.md").read_text(encoding="utf-8")
        self.assertIn("### 2.1 最小固定骨架", text)
        self.assertIn("### 2.2 按需事实产物", text)
        self.assertIn("### 2.3 生成产物与阅读副本", text)
        self.assertIn("不得把目录示例当成初始化清单", text)

    def test_manifest_ignores_itself_and_test_caches(self) -> None:
        paths = {path.relative_to(check_skill.ROOT).as_posix() for path in check_skill.manifest_files()}
        self.assertNotIn("FILE_MANIFEST.sha256", paths)
        self.assertFalse(any(
            "__pycache__" in path or ".pytest_cache" in path or ".git/" in path or path.endswith(".pyc")
            for path in paths
        ))

    def test_manifest_diff_reports_changed_missing_and_unexpected_files(self) -> None:
        expected = {
            "./changed.py": "a" * 64,
            "./new.py": "b" * 64,
        }
        actual = "\n".join([
            f"{'c' * 64}  ./changed.py",
            f"{'d' * 64}  ./removed.py",
        ]) + "\n"
        diff = check_skill.manifest_diff(actual, expected)
        self.assertEqual(["./changed.py"], diff["changedFiles"])
        self.assertEqual(["./removed.py"], diff["missingFiles"])
        self.assertEqual(["./new.py"], diff["unexpectedFiles"])
        self.assertEqual([], diff["invalidEntries"])

    def test_every_review_criterion_has_an_execution_path(self) -> None:
        contract = review_contract.load_contract()
        profile_criteria = {
            criterion_id
            for profile in contract["profiles"].values()
            for criterion_id in profile["criterionIds"]
        }
        for risk in contract.get("riskReviewTemplates", {}).values():
            criterion_ids = risk.get("criterionIds")
            if criterion_ids is None and risk.get("templateRef"):
                criterion_ids = contract["profileTemplates"][risk["templateRef"]]["criterionIds"]
            profile_criteria.update(criterion_ids or [])
        machine_gate_criteria = set(contract["machineGateCriterionIds"])
        self.assertTrue(profile_criteria.isdisjoint(machine_gate_criteria))
        self.assertEqual(
            set(contract["criteriaCatalog"]),
            profile_criteria | machine_gate_criteria,
        )
        self.assertEqual({"RC-BFLOW-006"}, machine_gate_criteria)

    def test_ai_bootstrap_is_index_only_and_authorized(self) -> None:
        source_map = json.loads((check_skill.ROOT / "ai" / "source-map.json").read_text(encoding="utf-8"))
        self.assertEqual("INDEX_ONLY", source_map["kind"])
        implementation_policy = source_map["skillImplementationReadPolicy"]
        self.assertEqual("FORBIDDEN", implementation_policy["default"])
        self.assertIn("scripts/*.py", implementation_policy["forbiddenGlobs"])
        self.assertIn("scripts/**/*.py", implementation_policy["forbiddenGlobs"])
        self.assertEqual("EXECUTE_ONLY", implementation_policy["entrypointPolicy"]["scripts/design_doc.py"])
        self.assertEqual(
            "INTERNAL_IMPLEMENTATION_FORBIDDEN",
            implementation_policy["entrypointPolicy"]["scripts/_core/document_utils.py"],
        )
        self.assertEqual("assets/runtime-contract.json", source_map["authoritativeSources"]["runtime"])
        self.assertEqual("assets/semantic-contract.json", source_map["authoritativeSources"]["semantic"])
        self.assertEqual("scripts/reference_reader.py", source_map["authoritativeSources"]["referenceReader"])
        self.assertEqual("docs/_relations/dependency_impact.yaml#crossModuleImplementations", source_map["authoritativeSources"]["crossModuleImplementation"])
        self.assertEqual(
            {
                "machine": "assets/review-contract.json#/evidencePolicyCatalog",
                "profileTemplates": "assets/review-contract.json#/profileTemplates",
                "riskReviewTemplates": "assets/review-contract.json#/riskReviewTemplates",
                "reviewSessionPolicy": "assets/review-contract.json#/reviewSessionPolicy",
                "query": "scripts/review_contract.py",
            },
            source_map["sources"]["reviewPolicyCatalog"],
        )
        self.assertNotIn("canonicalPhases", source_map)
        contract = contract_normalization.load_runtime_contract(check_skill.ASSETS / "runtime-contract.json")
        self.assertEqual("DECLARED_AGENT", contract["scriptPermissions"]["ai_context.resolve"]["mode"])
        self.assertEqual("DECLARED_AGENT", contract["scriptPermissions"]["task_verify.verify"]["mode"])
        self.assertEqual("DECLARED_AGENT", contract["scriptPermissions"]["task_layout.sync"]["mode"])
        self.assertEqual("DECLARED_AGENT", contract["scriptPermissions"]["task_layout.validate"]["mode"])
        optimization = contract["taskContextOptimization"]
        self.assertEqual(["summary", "failures", "full"], optimization["verification"]["levels"])
        self.assertEqual("CURRENT_MANIFEST", optimization["projection"]["kind"])
        self.assertEqual(["evidence", "review", "snapshot"], optimization["context"]["defaultLazyObjectKinds"])
        skill = (check_skill.ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("python3 scripts/ai_context.py resolve", skill)
        self.assertLessEqual(len(skill.splitlines()), 210)
        agent_interface = check_skill.load_yaml(check_skill.ROOT / "agents" / "openai.yaml", check_skill.CheckResult())
        prompt = agent_interface["interface"]["default_prompt"]
        self.assertLessEqual(len(prompt), 600)
        self.assertIn("scripts/ai_context.py resolve", prompt)
        for token in ("wk start", "wk begin", "progress", "-gp", "-gc"):
            self.assertIn(token, prompt)
        for token in ("新建任务", "恢复任务", "普通执行", "维护诊断", "仅在修改本 Skill 源时"):
            self.assertIn(token, skill)
        self.assertIn("ai/source-map.json", prompt)
        for duplicated in ("publish-artifact", "finalize-phase", "reviews.jsonl", "TEST_DB_URL"):
            self.assertNotIn(duplicated, prompt)
        ai_readme = (check_skill.ROOT / "ai" / "README.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(ai_readme.splitlines()), 70)
        self.assertNotIn("## 业务流程", ai_readme)
        self.assertNotIn("## 大需求任务规划", ai_readme)
        self.assertNotIn("## Git 阶段检查点", ai_readme)


    def test_lifecycle_is_rendered_once_and_core_points_to_runtime(self) -> None:
        contract = contract_normalization.load_runtime_contract(check_skill.ASSETS / "runtime-contract.json")
        lifecycle = [item["id"] for item in contract["canonicalPhases"]]
        pattern = "\n".join([lifecycle[0], *[f"→ {phase}" for phase in lifecycle[1:]]])
        runtime = (check_skill.REFERENCES / "runtime-contract.md").read_text(encoding="utf-8")
        core = (check_skill.REFERENCES / "lifecycle-core.md").read_text(encoding="utf-8")
        self.assertIn(pattern, runtime)
        self.assertNotIn(pattern, core)
        self.assertTrue("canonicalPhases" in core or "runtime-contract" in core)

    def test_semantic_contract_separates_business_and_integration_namespaces(self) -> None:
        semantic = json.loads((check_skill.ASSETS / "semantic-contract.json").read_text(encoding="utf-8"))
        self.assertEqual("1.0", semantic["packageVersion"])
        prefixes = {item.get("prefix") for item in semantic["idNamespaces"] if item.get("prefix")}
        self.assertTrue({"FLOW-", "STEP-", "CMI-", "CMSTEP-", "CASE-", "CP-"}.issubset(prefixes))
        contract = contract_normalization.load_runtime_contract(check_skill.ASSETS / "runtime-contract.json")
        self.assertEqual(
            {"mode": "STATIC", "allowedAgents": ["ProjectManagerAgent", "EngineeringStandardsReviewAgent", "CompletionVerificationAgent"]},
            contract["scriptPermissions"]["semantic_audit.check"],
        )

    def test_task_plan_reuses_canonical_dependency_status_and_severity_vocabularies(self) -> None:
        contract = contract_normalization.load_runtime_contract(check_skill.ASSETS / "runtime-contract.json")
        policy = contract["commandInterface"]["taskPlanning"]["reviewPolicy"]
        self.assertEqual("#/statuses/review", policy["statusDomainRef"])
        self.assertEqual("#/statuses/reviewConclusion", policy["resultDomainRef"])
        self.assertEqual(["P0", "P1", "P2"], policy["findingSeverityDomain"])
        self.assertIn("query", contract["commandInterface"]["taskPlanning"]["commands"])
        self.assertIn("depends_on", contract["commandInterface"]["taskPlanning"]["requiredTaskFields"])
        self.assertNotIn("dependencies", contract["commandInterface"]["taskPlanning"]["requiredTaskFields"])
        plan_schema = json.loads((check_skill.ASSETS / "development-task-plan.schema.json").read_text(encoding="utf-8"))
        task_def = plan_schema["$defs"]["task"]
        self.assertIn("depends_on", task_def["required"])
        self.assertNotIn("dependencies", task_def["properties"])
        review_schema = json.loads((check_skill.ASSETS / "development-task-review.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(["PASSED", "NEEDS_CHANGES", "BLOCKED"], review_schema["properties"]["result"]["enum"])
        self.assertEqual(["P0", "P1", "P2"], review_schema["properties"]["findings"]["items"]["properties"]["severity"]["enum"])

    def test_git_checkpoint_contract_uses_yaml_and_sequential_local_commits(self) -> None:
        self.assertTrue((check_skill.ASSETS / "long-task" / "git_checkpoints.yaml").is_file())
        self.assertFalse((check_skill.ASSETS / "long-task" / "git_checkpoints.md").exists())
        self.assertTrue((check_skill.ASSETS / "git-checkpoint.schema.json").is_file())
        self.assertTrue((check_skill.REFERENCES / "git-checkpoint.md").is_file())
        contract = contract_normalization.load_runtime_contract(check_skill.ASSETS / "runtime-contract.json")
        self.assertEqual(["ProjectManagerAgent"], contract["scriptPermissions"]["git_checkpoint.commit"]["allowedAgents"])
        self.assertEqual(["ProjectManagerAgent"], contract["scriptPermissions"]["git_checkpoint.push"]["allowedAgents"])
        self.assertEqual("task/{TARGET_ID}/git_checkpoints.yaml", contract["commandInterface"]["workModes"]["gitCheckpoint"]["policy"]["recordFile"])
        self.assertFalse(contract["commandInterface"]["workModes"]["gitCheckpoint"]["policy"]["autoPush"])

    def test_page_design_and_minimal_mode_are_not_conflated(self) -> None:
        routing = contract_normalization.load_context_routing(check_skill.ROOT / "context-routing.json")
        minimal = next(item for item in routing["promptConditions"] if item["id"] == "minimal")
        page_design = next(item for item in routing["promptConditions"] if item["id"] == "page-design")
        self.assertNotIn("跳过页面设计", minimal["keywords"])
        self.assertIn("wk -v", page_design["keywords"])
        scope = (check_skill.REFERENCES / "scope.md").read_text(encoding="utf-8")
        self.assertIn("`wk -min` 只影响测试阶段，不关闭页面设计", scope)
        self.assertNotIn("`wk -min` 时当前版本跳过", scope)

    def test_request_intake_and_release_contracts_are_current(self) -> None:
        contract = contract_normalization.load_runtime_contract(check_skill.ASSETS / "runtime-contract.json")
        release = json.loads((check_skill.ASSETS / "release-contract.json").read_text(encoding="utf-8"))
        schema = json.loads((check_skill.ASSETS / "request-intake.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(contract["packageVersion"], release["packageVersion"])
        self.assertEqual("STABLE", release["releaseStage"])
        self.assertEqual(contract["storageModelVersion"], release["storageModelVersion"])
        simple = contract["workflowProfiles"]["SIMPLE_OPTIMIZATION_FLOW"]
        self.assertEqual(["development", "code_review", "testing", "completion_verification"], simple["requiredSequence"])
        self.assertEqual("NONE", simple["requiredImpactValue"])
        self.assertEqual(9, len(simple["requiredImpactFields"]))
        self.assertEqual({"minimal", "lightweight"}, set(simple["incompatibleWorkModes"]))
        self.assertEqual(
            ["测试用例", "Review", "测试"],
            contract["commandInterface"]["workModes"]["lightweight"]["workColumns"],
        )
        self.assertEqual({"const": 1}, schema["properties"]["schemaVersion"])
        record_contract = json.loads((check_skill.ASSETS / "long-task" / "record-contract.json").read_text(encoding="utf-8"))
        self.assertIn("minimal_skipped_phases", record_contract["records"]["workModeState"]["allowedFields"])
        self.assertTrue((check_skill.ROOT / "scripts" / "wk.py").is_file())
        self.assertTrue((check_skill.ROOT / "scripts" / "request_intake.py").is_file())
        self.assertRegex(release["baselineTag"], r"^[0-9a-f]{40}$")
        self.assertIn("SKILL.md", release["criticalBaselineFiles"])
        self.assertTrue((check_skill.ROOT / "scripts" / "baseline_guard.py").is_file())
        self.assertNotIn("当前 Skill 包版本", (check_skill.ROOT / "SKILL.md").read_text(encoding="utf-8"))

    def test_ai_rule_convergence_command_surface_and_routes(self) -> None:
        runtime = contract_normalization.load_runtime_contract(check_skill.ROOT / "assets/runtime-contract.json")
        routing = contract_normalization.load_context_routing(check_skill.ROOT / "context-routing.json")
        self.assertIn("context", runtime["commandInterface"]["orchestration"])
        expected = {
            "wk_context": ["wk-cli", "lifecycle-core"],
            "wk_reconcile": ["wk-cli", "lifecycle-core"],
            "wk_close": ["wk-cli", "lifecycle-core"],
            "wk_impact": ["wk-cli", "review-evidence"],
            "wk_env": ["wk-cli", "test-environment"],
        }
        for task_type, resources in expected.items():
            matches = [route for route in routing["routes"] if task_type in route["taskTypes"]]
            self.assertEqual(1, len(matches))
            self.assertEqual(resources, matches[0]["resources"])
        projection = runtime["taskContextOptimization"]["projection"]
        self.assertEqual(1, projection["physicalFileBudget"])
        self.assertEqual("CURRENT_MANIFEST", projection["kind"])

if __name__ == "__main__":
    unittest.main()
