"""Validation for context-routing.json and its reachable reference catalog."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from _check_skill.io import load_json
import contract_normalization


def load_materialized_routing(result) -> dict[str, Any] | None:
    """Load and normalize the routing contract for any static check module."""
    raw = load_json(ROOT / "context-routing.json", result)
    if not isinstance(raw, dict):
        return None
    try:
        return contract_normalization.materialize_context_routing(raw)
    except contract_normalization.ContractNormalizationError as exc:
        result.errors.append(f"context-routing.json normalization failed: {exc}")
        return None

ROOT = Path(__file__).resolve().parents[2]
REFERENCES = ROOT / "references"
RESOURCE_STATUSES = {"active", "deprecated", "internal", "on_demand"}
HARD_CONSTRAINT_LEVELS = {"HARD_GATE", "REQUIRED", "RECOMMENDED", "EXAMPLE"}


def reference_resource_ids() -> set[str]:
    return {
        path.relative_to(REFERENCES).with_suffix("").as_posix()
        for path in REFERENCES.rglob("*.md")
        if path.name != "README.md"
    }


def _check_prompt_conditions(routing, fixed, max_dynamic, used, result) -> None:
    conditions = routing.get("promptConditions", [])
    if not isinstance(conditions, list):
        result.errors.append("context-routing.json promptConditions must be an array")
        conditions = []
    condition_ids: set[str] = set()
    condition_keywords: set[str] = set()
    forbidden_generic_keywords = {"完成", "通过", "验证", "证据", "接口", "依赖", "范围"}
    for index, condition in enumerate(conditions):
        if not isinstance(condition, dict):
            result.errors.append(f"context-routing.json promptConditions[{index}] must be an object")
            continue
        condition_id = condition.get("id")
        if not isinstance(condition_id, str) or not condition_id:
            result.errors.append(f"context-routing.json promptConditions[{index}] id must be non-empty")
        elif condition_id in condition_ids:
            result.errors.append(f"context-routing.json duplicate prompt condition id: {condition_id}")
        else:
            condition_ids.add(condition_id)
        priority = condition.get("priority")
        if not isinstance(priority, int) or isinstance(priority, bool):
            result.errors.append(f"context-routing.json promptConditions[{index}] priority must be an integer")
        keywords = condition.get("keywords", [])
        resources = condition.get("resources", [])
        match_mode = condition.get("match")
        if match_mode not in {None, "any", "all", "threshold"}:
            result.errors.append(f"context-routing.json promptConditions[{index}] match is invalid")
        min_hits = condition.get("minKeywordHits", 1)
        if not isinstance(min_hits, int) or isinstance(min_hits, bool) or min_hits <= 0:
            result.errors.append(f"context-routing.json promptConditions[{index}] minKeywordHits is invalid")
        keyword_match = condition.get("keywordMatch")
        if keyword_match is not None and keyword_match not in {"stable_id", "exact_phrase", "token", "substring"}:
            result.errors.append(f"context-routing.json promptConditions[{index}] keywordMatch is invalid")
        if not isinstance(keywords, list) or not keywords or any(not isinstance(item, str) for item in keywords):
            result.errors.append(f"context-routing.json promptConditions[{index}] keywords must be non-empty")
            keywords = []
        normalized_keywords = [item.casefold() for item in keywords if isinstance(item, str)]
        if len(normalized_keywords) != len(set(normalized_keywords)):
            result.errors.append(f"context-routing.json promptConditions[{index}] has duplicate keywords")
        repeated_keywords = sorted(set(normalized_keywords) & condition_keywords)
        if repeated_keywords:
            result.errors.append(
                f"context-routing.json promptConditions[{index}] repeats keywords: {repeated_keywords}"
            )
        condition_keywords.update(normalized_keywords)
        generic = sorted(set(keywords) & forbidden_generic_keywords)
        if generic:
            result.errors.append(
                f"context-routing.json promptConditions[{index}] uses overly broad keywords: {generic}"
            )
        if not isinstance(resources, list) or any(not isinstance(item, str) for item in resources):
            result.errors.append(f"context-routing.json promptConditions[{index}] resources must be a string array")
            resources = []
        if len(resources) != len(set(resources)):
            result.errors.append(f"context-routing.json promptConditions[{index}] has duplicate resources")
        if isinstance(max_dynamic, int) and len(resources) > max_dynamic:
            result.errors.append(f"context-routing.json promptConditions[{index}] exceeds maxDynamicResources")
        fixed_overlap = set(resources) & set(fixed)
        if fixed_overlap:
            result.errors.append(
                f"context-routing.json promptConditions[{index}] repeats fixed resources: {sorted(fixed_overlap)}"
            )
        used.update(resources)


def _check_mode_overrides(
    routing, fixed, used, catalog, active_task_types, max_default_budget, routes, result
) -> None:
    mode_overrides = routing.get("modeOverrides")
    expected_mode_override_keys = {"pageDesign", "minimal", "lightweight", "auto", "architectureReview", "gitCheckpoint", "gitPush"}
    if not isinstance(mode_overrides, dict) or set(mode_overrides) != expected_mode_override_keys:
        result.errors.append(
            "context-routing.json modeOverrides must contain pageDesign, minimal, lightweight, auto, architectureReview, gitCheckpoint and gitPush"
        )
    else:
        page_design_override = mode_overrides.get("pageDesign")
        expected_page_design = {
            "stateSource": "version/{VERSION}/work.md",
            "taskTypes": ["design", "implementation_contract"],
            "includeResources": ["page-design"],
            "outputPattern": "version/{VERSION}/doc/{MODULE_CODE}/{MODULE_CODE}_page_design.md",
        }
        if page_design_override != expected_page_design:
            result.errors.append("context-routing.json pageDesign mode override is invalid")
        elif isinstance(page_design_override, dict):
            used.update(page_design_override.get("includeResources", []))
        minimal_override = mode_overrides.get("minimal")
        if not isinstance(minimal_override, dict):
            result.errors.append("context-routing.json modeOverrides.minimal must be an object")
        else:
            required_minimal = {
                "stateSource": "version/{VERSION}/work.md",
                "skipTaskTypes": ["test_design", "testing"],
                "suppressedReviewerAssignmentsRef": (
                    "assets/runtime-contract.json#/commandInterface/workModes/minimal/"
                    "suppressedReviewerAssignments"
                ),
                "requiredNonTestEvidenceRef": (
                    "assets/runtime-contract.json#/commandInterface/workModes/minimal/"
                    "requiredNonTestEvidence"
                ),
                "requiredResources": ["completion-gate-minimal"],
            }
            if any(minimal_override.get(key) != value for key, value in required_minimal.items()):
                result.errors.append("context-routing.json minimal mode override is invalid")
            required_excluded = {
                "testing-core", "test-environment", "test-evidence",
                "integration-testing", "test-completion", "tdd-core", "test-matrix-design",
                "red-green-refactor", "test-seam", "tdd-evidence", "completion-gate-core",
            }
            if not required_excluded.issubset(set(minimal_override.get("excludeResources") or [])):
                result.errors.append("context-routing.json minimal excludeResources are incomplete")
            if any(resource not in catalog for resource in minimal_override.get("requiredResources", [])):
                result.errors.append("context-routing.json minimal requiredResources reference unknown resources")
            if any(task not in active_task_types for task in minimal_override.get("skipTaskTypes", [])):
                result.errors.append("context-routing.json minimal skipTaskTypes reference unknown task types")
            if any(resource not in catalog for resource in minimal_override.get("excludeResources", [])):
                result.errors.append("context-routing.json minimal excludeResources reference unknown resources")
        lightweight_override = mode_overrides.get("lightweight")
        if not isinstance(lightweight_override, dict):
            result.errors.append("context-routing.json modeOverrides.lightweight must be an object")
        else:
            expected_lightweight = {
                "stateSource": "version/{VERSION}/work.md",
                "skipTaskTypes": ["test_design", "tdd", "code_review", "testing"],
                "suppressedReviewerAssignmentsRef": "assets/runtime-contract.json#/commandInterface/workModes/lightweight/suppressedReviewerAssignments",
                "requiredNonTestEvidenceRef": "assets/runtime-contract.json#/commandInterface/workModes/lightweight/requiredNonTestEvidence",
                "interactionPolicyRef": "assets/runtime-contract.json#/commandInterface/workModes/lightweight/interactionPolicy",
                "archivePolicyRef": "assets/runtime-contract.json#/commandInterface/workModes/lightweight/archivePolicy",
                "requiredResources": ["completion-gate-minimal"],
            }
            if any(lightweight_override.get(key) != value for key, value in expected_lightweight.items()):
                result.errors.append("context-routing.json lightweight mode override is invalid")
            if any(resource not in catalog for resource in lightweight_override.get("requiredResources", [])):
                result.errors.append("context-routing.json lightweight requiredResources reference unknown resources")
            if any(task not in active_task_types for task in lightweight_override.get("skipTaskTypes", [])):
                result.errors.append("context-routing.json lightweight skipTaskTypes reference unknown task types")
            if any(resource not in catalog for resource in lightweight_override.get("excludeResources", [])):
                result.errors.append("context-routing.json lightweight excludeResources reference unknown resources")
        auto_override = mode_overrides.get("auto")
        if not isinstance(auto_override, dict):
            result.errors.append("context-routing.json modeOverrides.auto must be an object")
        else:
            expected_auto = {
                "stateSource": "version/{VERSION}/work.md",
                "confirmationPolicyRef": (
                    "assets/runtime-contract.json#/commandInterface/workModes/auto/confirmationPolicy"
                ),
                "autoAdvanceWhen": [
                    "current_phase_outcome_passed",
                    "required_reviews_passed",
                    "required_evidence_present",
                    "no_open_p0_or_p1",
                    "no_blocked_or_failed_gate",
                ],
                "stopOn": [
                    "scope_or_authorization_violation",
                    "missing_required_fact_or_evidence",
                    "failed_or_blocked_review",
                    "open_p0_or_p1",
                    "ci_failure",
                    "authority_stale",
                    "safety_block",
                    "unsafe_or_irreversible_external_action",
                ],
                "stopScope": "BATCH_FORWARD_ONLY",
                "remediationPolicyRef": (
                    "assets/runtime-contract.json#/commandInterface/workModes/auto/remediationPolicy"
                ),
                "remediationStopOn": [
                    "authority_conflict",
                    "ambiguous_business_decision",
                    "ambiguous_architecture_decision",
                    "safety_bypass_required",
                    "unauthorized_external_action",
                    "non_converging_remediation",
                ],
                "semanticPreflightPolicyRef": (
                    "assets/runtime-contract.json#/commandInterface/workModes/auto/semanticPreflightPolicy"
                ),
                "architectureBoundaryPolicyRef": (
                    "assets/runtime-contract.json#/commandInterface/workModes/auto/architectureBoundaryPolicy"
                ),
                "finalHeadGatePolicyRef": (
                    "assets/runtime-contract.json#/commandInterface/workModes/auto/finalHeadGatePolicy"
                ),
            }
            if auto_override != expected_auto:
                result.errors.append("context-routing.json auto mode override is invalid")
        git_push_override = mode_overrides.get("gitPush")
        expected_git_push = {
            "stateSource": "version/{VERSION}/work.md",
            "policyRef": "assets/runtime-contract.json#/commandInterface/workModes/gitPush/policy",
            "includeResources": ["git-checkpoint"],
            "afterCheckpointCommand": "python3 scripts/git_checkpoint.py push -g ProjectManagerAgent --task-dir {TASK_DIR}",
            "requiresMode": "gitCheckpoint",
            "executionPolicyRef": "assets/runtime-contract.json#/executionPolicy",
        }
        if git_push_override != expected_git_push:
            result.errors.append("context-routing.json gitPush mode override is invalid")
        elif isinstance(git_push_override, dict):
            if any(resource not in catalog for resource in git_push_override.get("includeResources", [])):
                result.errors.append("context-routing.json gitPush includeResources reference unknown resources")
            used.update(git_push_override.get("includeResources", []))

        architecture_review_override = mode_overrides.get("architectureReview")
        expected_architecture_review = {
            "stateSource": "version/{VERSION}/work.md",
            "policyRef": (
                "assets/runtime-contract.json#/commandInterface/workModes/architectureReview/policy"
            ),
            "taskTypes": ["implementation_plan", "development", "code_review"],
            "includeResources": ["wk-ar-state-core"],
        }
        if architecture_review_override != expected_architecture_review:
            result.errors.append("context-routing.json architectureReview mode override is invalid")
        elif isinstance(architecture_review_override, dict):
            if any(task not in active_task_types for task in architecture_review_override.get("taskTypes", [])):
                result.errors.append("context-routing.json architectureReview taskTypes reference unknown task types")
            if any(resource not in catalog for resource in architecture_review_override.get("includeResources", [])):
                result.errors.append("context-routing.json architectureReview includeResources reference unknown resources")
            if isinstance(max_default_budget, int):
                for index, route in enumerate(routes):
                    task_types = route.get("taskTypes", []) if isinstance(route, dict) else []
                    if not set(task_types) & set(architecture_review_override.get("taskTypes", [])):
                        continue
                    resources = route.get("resources", []) if isinstance(route, dict) else []
                    combined = list(dict.fromkeys([*fixed, *resources, *architecture_review_override["includeResources"]]))
                    budget = sum(int((catalog.get(resource_id) or {}).get("tokenBudget") or 0) for resource_id in combined)
                    if budget > max_default_budget:
                        result.errors.append(
                            f"context-routing.json route[{index}] with architectureReview budget {budget} "
                            f"exceeds maxDefaultTokenBudget {max_default_budget}"
                        )



def _check_catalog_definition(resource_id, definition, catalog, external_catalog_ids, result) -> None:
    if not isinstance(definition, dict):
        result.errors.append(f"context-routing.json resourceCatalog.{resource_id} must be an object")
        return
    expected_path = f"references/{resource_id}.md"
    actual_path = definition.get("path")
    alias_of = definition.get("aliasOf")
    valid_path = actual_path == expected_path
    if isinstance(alias_of, str):
        target = catalog.get(alias_of) or {}
        valid_path = (
            alias_of != resource_id
            and isinstance(target, dict)
            and isinstance(actual_path, str)
            and actual_path == target.get("path")
            and (ROOT / actual_path).is_file()
        )
    if resource_id in external_catalog_ids:
        valid_path = (
            isinstance(actual_path, str)
            and actual_path.startswith("assets/")
            and actual_path.endswith(".json")
            and (ROOT / actual_path).is_file()
        )
    if not valid_path:
        result.errors.append(
            f"context-routing.json resourceCatalog.{resource_id}.path must be {expected_path}, "
            "match aliasOf target path, or be an existing assets/*.json machine contract"
        )
    limit = definition.get("tokenBudget")
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        result.errors.append(f"context-routing.json invalid token budget for {resource_id}: {limit}")
    purpose = definition.get("purpose")
    if not isinstance(purpose, str) or not purpose.strip():
        result.errors.append(f"context-routing.json resourceCatalog.{resource_id}.purpose is required")
    if definition.get("readStrategy") not in {"full", "relevant_sections"}:
        result.errors.append(f"context-routing.json resourceCatalog.{resource_id}.readStrategy is invalid")
    on_demand_only = definition.get("onDemandOnly", False)
    if not isinstance(on_demand_only, bool):
        result.errors.append(
            f"context-routing.json resourceCatalog.{resource_id}.onDemandOnly must be boolean"
        )
    status = definition.get("status")
    if status not in RESOURCE_STATUSES:
        result.errors.append(
            f"context-routing.json resourceCatalog.{resource_id}.status must be one of {sorted(RESOURCE_STATUSES)}"
        )
    owner = definition.get("owner")
    if not isinstance(owner, str) or not owner.strip():
        result.errors.append(f"context-routing.json resourceCatalog.{resource_id}.owner is required")
    if "replacement" not in definition:
        result.errors.append(f"context-routing.json resourceCatalog.{resource_id}.replacement is required")
    elif status == "deprecated":
        replacement = definition.get("replacement")
        if not isinstance(replacement, str) or replacement not in catalog:
            result.errors.append(
                f"context-routing.json resourceCatalog.{resource_id}.replacement must name an active catalog resource"
            )
    elif definition.get("replacement") is not None:
        result.errors.append(
            f"context-routing.json resourceCatalog.{resource_id}.replacement must be null unless deprecated"
        )
    last_reviewed = definition.get("lastReviewed")
    if not isinstance(last_reviewed, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", last_reviewed):
        result.errors.append(
            f"context-routing.json resourceCatalog.{resource_id}.lastReviewed must be YYYY-MM-DD"
        )
    if not isinstance(alias_of, str):
        return
    try:
        from reference_reader import index_resource
        target_sections = {item["id"]: item for item in index_resource(alias_of).get("sections", [])}
    except (OSError, ValueError, KeyError) as exc:
        result.errors.append(f"context-routing.json alias {resource_id} target sections cannot be indexed: {exc}")
        target_sections = {}
    section_ids = definition.get("sectionIds")
    if not isinstance(section_ids, list) or not section_ids or any(not isinstance(item, str) for item in section_ids):
        result.errors.append(
            f"context-routing.json alias {resource_id}.sectionIds must be a non-empty string array"
        )
        return
    for section_id in section_ids:
        section = target_sections.get(section_id)
        if section is None:
            result.errors.append(
                f"context-routing.json alias {resource_id}.sectionIds references unknown section {section_id}"
            )
        elif not section.get("stable", False):
            result.errors.append(
                f"context-routing.json alias {resource_id}.sectionIds must reference explicit stable IDs: {section_id}"
            )


def _check_hard_constraint_levels(routing, result) -> None:
    governance = routing.get("governancePolicy")
    levels = governance.get("hardConstraintLevels") if isinstance(governance, dict) else None
    if not isinstance(levels, dict) or set(levels) != HARD_CONSTRAINT_LEVELS:
        result.errors.append(
            "context-routing.json governancePolicy.hardConstraintLevels must define exactly "
            f"{sorted(HARD_CONSTRAINT_LEVELS)}"
        )
        return
    ranks = []
    for level in sorted(HARD_CONSTRAINT_LEVELS):
        definition = levels.get(level)
        if not isinstance(definition, dict):
            result.errors.append(f"hard constraint level {level} must be an object")
            continue
        rank = definition.get("rank")
        if not isinstance(rank, int) or isinstance(rank, bool) or rank not in {1, 2, 3, 4}:
            result.errors.append(f"hard constraint level {level}.rank must be 1..4")
        else:
            ranks.append(rank)
        if not isinstance(definition.get("description"), str) or not definition["description"].strip():
            result.errors.append(f"hard constraint level {level}.description is required")
        if not isinstance(definition.get("blocksAction"), bool):
            result.errors.append(f"hard constraint level {level}.blocksAction must be boolean")
        if not isinstance(definition.get("requiresEvidence"), bool):
            result.errors.append(f"hard constraint level {level}.requiresEvidence must be boolean")
    if len(ranks) == len(HARD_CONSTRAINT_LEVELS) and len(set(ranks)) != len(ranks):
        result.errors.append("hard constraint levels must have unique ranks")

def check_routing(result: Any) -> None:
    raw_routing = load_json(ROOT / "context-routing.json", result)
    if not isinstance(raw_routing, dict):
        return
    try:
        routing = contract_normalization.materialize_context_routing(raw_routing)
    except contract_normalization.ContractNormalizationError as exc:
        result.errors.append(f"context-routing.json normalization failed: {exc}")
        return
    if routing.get("schemaVersion") != 2:
        result.errors.append("context-routing.json schemaVersion must be 2")

    policy = routing.get("routingPolicy")
    if not isinstance(policy, dict):
        result.errors.append("context-routing.json routingPolicy must be an object")
        policy = {}
    expected_order = ["fixedResources", "exactTaskRoute", "modeOverrides", "matchingPromptConditions"]
    if policy.get("order") != expected_order:
        result.errors.append("context-routing.json routingPolicy.order is not canonical")
    if policy.get("resolver") != "scripts/context_resolver.py":
        result.errors.append("context-routing.json routingPolicy.resolver must be scripts/context_resolver.py")
    if policy.get("conditionMatch") not in {"typed", "case_insensitive_substring"}:
        result.errors.append("context-routing.json routingPolicy.conditionMatch is invalid")
    if policy.get("conditionMatch") == "typed":
        allowed_match_modes = {"stable_id", "exact_phrase", "token", "substring"}
        modes = policy.get("conditionMatchModes")
        if not isinstance(modes, list) or not modes or any(mode not in allowed_match_modes for mode in modes):
            result.errors.append("context-routing.json routingPolicy.conditionMatchModes is invalid")
    if policy.get("maxMatchedPromptConditionsMode") not in {"advisory", "hard"}:
        result.errors.append("context-routing.json maxMatchedPromptConditionsMode must be advisory or hard")
    if policy.get("budgetStrategy") not in {"best_effort", "strict"}:
        result.errors.append("context-routing.json routingPolicy.budgetStrategy is invalid")
    if policy.get("conditionSelection") != "priority_then_order":
        result.errors.append("context-routing.json routingPolicy.conditionSelection is invalid")
    max_dynamic = policy.get("maxDynamicResources")
    if not isinstance(max_dynamic, int) or isinstance(max_dynamic, bool) or max_dynamic <= 0:
        result.errors.append("context-routing.json routingPolicy.maxDynamicResources must be positive")
    max_conditions = policy.get("maxMatchedPromptConditions")
    if not isinstance(max_conditions, int) or isinstance(max_conditions, bool) or max_conditions <= 0:
        result.errors.append("context-routing.json routingPolicy.maxMatchedPromptConditions must be positive")
    max_total = policy.get("maxTotalResources")
    if not isinstance(max_total, int) or isinstance(max_total, bool) or max_total <= 0:
        result.errors.append("context-routing.json routingPolicy.maxTotalResources must be positive")
    elif isinstance(max_dynamic, int) and max_total < len(routing.get("fixedResources", [])) + max_dynamic:
        result.errors.append("context-routing.json maxTotalResources is smaller than fixed plus exact route limit")
    max_default_budget = policy.get("maxDefaultTokenBudget")
    if not isinstance(max_default_budget, int) or isinstance(max_default_budget, bool) or max_default_budget <= 0:
        result.errors.append("context-routing.json routingPolicy.maxDefaultTokenBudget must be positive")
    target_budget = policy.get("targetResolvedTokenBudget")
    if not isinstance(target_budget, int) or isinstance(target_budget, bool) or target_budget <= 0:
        result.errors.append("context-routing.json routingPolicy.targetResolvedTokenBudget must be positive")
    elif isinstance(max_default_budget, int) and target_budget > max_default_budget:
        result.errors.append("context-routing.json targetResolvedTokenBudget exceeds maxDefaultTokenBudget")
    reserved_budget = policy.get("reservedTaskFactTokenBudget")
    if not isinstance(reserved_budget, int) or isinstance(reserved_budget, bool) or reserved_budget < 0:
        result.errors.append("context-routing.json routingPolicy.reservedTaskFactTokenBudget is invalid")
    elif isinstance(max_default_budget, int) and isinstance(target_budget, int) and max_default_budget - target_budget < reserved_budget:
        result.errors.append("context-routing.json does not reserve the declared task-fact token budget")
    max_fixed_budget = policy.get("maxFixedTokenBudget")
    if not isinstance(max_fixed_budget, int) or isinstance(max_fixed_budget, bool) or max_fixed_budget <= 0:
        result.errors.append("context-routing.json routingPolicy.maxFixedTokenBudget must be positive")
    base_route_targets = policy.get("baseRouteTokenBudgetTargets")
    if not isinstance(base_route_targets, dict) or not base_route_targets:
        result.errors.append("context-routing.json routingPolicy.baseRouteTokenBudgetTargets must be a non-empty object")
        base_route_targets = {}
    elif any(
        not isinstance(task_type, str) or not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0
        for task_type, limit in base_route_targets.items()
    ):
        result.errors.append("context-routing.json baseRouteTokenBudgetTargets entries are invalid")
    if policy.get("budgetSelection") != "priority_order_stop_before_exceeding":
        result.errors.append("context-routing.json routingPolicy.budgetSelection is invalid")
    catalog = routing.get("resourceCatalog")
    if not isinstance(catalog, dict):
        result.errors.append("context-routing.json resourceCatalog must be an object")
        catalog = {}
    external_catalog_ids: set[str] = set()
    for resource_id, definition in catalog.items():
        if not isinstance(resource_id, str) or not isinstance(definition, dict):
            continue
        resource_path = definition.get("path")
        if (
            isinstance(resource_path, str)
            and resource_path.startswith("assets/")
            and resource_path.endswith(".json")
            and (ROOT / resource_path).is_file()
        ):
            external_catalog_ids.add(resource_id)
    aliases = {
        resource_id
        for resource_id, definition in catalog.items()
        if isinstance(definition, dict) and isinstance(definition.get("aliasOf"), str)
    }
    available = reference_resource_ids() | external_catalog_ids | aliases
    fixed = routing.get("fixedResources", [])
    if not isinstance(fixed, list) or any(not isinstance(item, str) for item in fixed):
        result.errors.append("context-routing.json fixedResources must be a string array")
        fixed = []
    fixed_budget = sum(
        int((catalog.get(resource_id) or {}).get("tokenBudget") or 0)
        for resource_id in fixed if isinstance(resource_id, str)
    )
    if isinstance(max_fixed_budget, int) and fixed_budget > max_fixed_budget:
        result.errors.append(
            f"context-routing.json fixed resource budget {fixed_budget} exceeds maxFixedTokenBudget {max_fixed_budget}"
        )
    used: set[str] = set(fixed)
    active_task_types: set[str] = set()
    active_route_resources: set[str] = set()

    routes = routing.get("routes", [])
    if not isinstance(routes, list):
        result.errors.append("context-routing.json routes must be an array")
        routes = []
    for index, route in enumerate(routes):
        if not isinstance(route, dict):
            result.errors.append(f"context-routing.json route[{index}] must be an object")
            continue
        task_types = route.get("taskTypes", [])
        resources = route.get("resources", [])
        if not isinstance(task_types, list) or any(not isinstance(item, str) for item in task_types):
            result.errors.append(f"context-routing.json route[{index}] taskTypes must be a string array")
            task_types = []
        if not isinstance(resources, list) or any(not isinstance(item, str) for item in resources):
            result.errors.append(f"context-routing.json route[{index}] resources must be a string array")
            resources = []
        if len(task_types) != len(set(task_types)):
            result.errors.append(f"context-routing.json route[{index}] has duplicate taskTypes")
        if len(resources) != len(set(resources)):
            result.errors.append(f"context-routing.json route[{index}] has duplicate resources")
        if isinstance(max_dynamic, int) and len(resources) > max_dynamic:
            result.errors.append(f"context-routing.json route[{index}] exceeds maxDynamicResources")
        if isinstance(max_default_budget, int):
            route_budget = sum(
                int((catalog.get(resource_id) or {}).get("tokenBudget") or 0)
                for resource_id in [*fixed, *resources]
                if isinstance(resource_id, str)
            )
            if route_budget > max_default_budget:
                result.errors.append(
                    f"context-routing.json route[{index}] default token budget {route_budget} "
                    f"exceeds maxDefaultTokenBudget {max_default_budget}"
                )
            if isinstance(target_budget, int) and route_budget > target_budget:
                result.errors.append(
                    f"context-routing.json route[{index}] token budget {route_budget} "
                    f"exceeds targetResolvedTokenBudget {target_budget}"
                )
            for task_type in task_types:
                route_target = base_route_targets.get(task_type) if isinstance(base_route_targets, dict) else None
                if isinstance(route_target, int) and route_budget > route_target:
                    result.errors.append(
                        f"context-routing.json task type {task_type} base token budget {route_budget} "
                        f"exceeds configured target {route_target}"
                    )
        fixed_overlap = set(resources) & set(fixed)
        if fixed_overlap:
            result.errors.append(
                f"context-routing.json route[{index}] repeats fixed resources: {sorted(fixed_overlap)}"
            )
        overlap = active_task_types & set(task_types)
        if overlap:
            result.errors.append(f"context-routing.json taskTypes appear in multiple active routes: {sorted(overlap)}")
        active_task_types.update(task_types)
        active_route_resources.update(resources)
        used.update(resources)

    _check_prompt_conditions(routing, fixed, max_dynamic, used, result)

    preliminary_mode_overrides = routing.get("modeOverrides")
    if isinstance(preliminary_mode_overrides, dict):
        for override in preliminary_mode_overrides.values():
            if isinstance(override, dict):
                used.update(item for item in override.get("includeResources", []) if isinstance(item, str))
                used.update(item for item in override.get("requiredResources", []) if isinstance(item, str))
                used.update(item for item in override.get("excludeResources", []) if isinstance(item, str))

    missing = sorted(used - available)
    if missing:
        result.errors.append(f"context-routing.json references missing resources: {missing}")

    missing_catalog = sorted(available - set(catalog))
    extra_catalog = sorted(set(catalog) - available)
    if missing_catalog or extra_catalog:
        result.errors.append(
            f"context-routing.json resourceCatalog must cover references exactly: "
            f"missing={missing_catalog} extra={extra_catalog}"
        )
    for resource_id, definition in catalog.items():
        _check_catalog_definition(resource_id, definition, catalog, external_catalog_ids, result)
    missing_used_catalog = sorted(used - set(catalog))
    if missing_used_catalog:
        result.errors.append(f"context-routing.json used resources missing from catalog: {missing_used_catalog}")
    unused_catalog = sorted(
        resource_id for resource_id in set(catalog) - used
        if not bool((catalog.get(resource_id) or {}).get("onDemandOnly"))
    )
    if unused_catalog:
        result.errors.append(f"context-routing.json catalog contains unreachable resources: {unused_catalog}")
    _check_mode_overrides(
        routing, fixed, used, catalog, active_task_types, max_default_budget, routes, result
    )
    _check_hard_constraint_levels(routing, result)
    if "resourceTokenBudgets" in routing:
        result.errors.append("context-routing.json must not maintain a second resourceTokenBudgets table")

    result.ok("Context routing resolves a minimal current resource catalog with mode overrides")

__all__ = ["reference_resource_ids", "check_routing"]
