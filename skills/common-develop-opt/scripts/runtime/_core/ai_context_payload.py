"""Pure payload builders for :mod:`ai_context`.

These helpers deliberately contain no filesystem or contract lookups.  Keeping
payload assembly separate makes the resolver easier to audit while preserving
all of the resolver's existing module-level lookup hooks.
"""
from __future__ import annotations

from typing import Any, Iterable


def build_phase_contract(
    phase_definition: dict[str, Any] | None,
    architecture_review_context: dict[str, Any],
    agent: str,
    project_manager_agents: Iterable[str],
) -> dict[str, Any] | None:
    if phase_definition is None:
        return None
    required = list(phase_definition.get("requiredReviewerAgents") or [])
    conditional = dict(phase_definition.get("conditionalReviewerAgents") or {})
    dynamic_reviewers = architecture_review_context.get("requiredReviewerAgents")
    if architecture_review_context.get("enabled") and phase_definition.get("id") in {"development", "code_review"}:
        required = list(dynamic_reviewers or [])
        required_set = set(required)
        conditional = {key: value for key, value in conditional.items() if key not in required_set}
    project_manager_set = set(project_manager_agents)
    owner = phase_definition.get("ownerAgent")
    phase_id = phase_definition.get("id")
    phase_contract = {
        "id": phase_id,
        "artifact": phase_definition.get("artifact"),
        "ownerAgent": owner,
        "requiredReviewerAgents": required,
        "conditionalReviewerAgents": conditional,
        "requiredPredecessors": phase_definition.get("requiredPredecessors", []),
        "minimalModePredecessors": phase_definition.get("minimalModePredecessors", []),
        "skipWhen": phase_definition.get("skipWhen", {}),
    }
    if agent == owner:
        role = "OWNER"
    elif agent in required:
        role = "REQUIRED_REVIEWER"
    elif agent in conditional:
        role = "CONDITIONAL_OR_ADDITIONAL_REVIEWER"
    elif agent in project_manager_set:
        role = "PROJECT_MANAGER"
    else:
        role = "NOT_ASSIGNED"
    phase_contract["selectedAgentRole"] = role
    return phase_contract


def build_review_profile_ids(phase_contract: dict[str, Any] | None) -> list[str]:
    if phase_contract is None:
        return []
    reviewer_agents = list(phase_contract.get("requiredReviewerAgents") or [])
    conditional_agents = list((phase_contract.get("conditionalReviewerAgents") or {}).keys())
    phase_id = phase_contract["id"]
    return [f"{phase_id}:{name}" for name in reviewer_agents + conditional_agents]


def build_execution_core(
    *,
    agent: str,
    phase_id: str,
    phase_contract: dict[str, Any] | None,
    routed: dict[str, Any],
    direct_commands: list[str],
    contextual_commands: list[str],
    capability_pack_context: dict[str, Any],
    implementation_read_policy: dict[str, Any],
) -> dict[str, Any]:
    execution_core = routed.get("executionCore") or {}
    return {
        "selectedAgent": agent,
        "phase": phase_id,
        "selectedAgentRole": (phase_contract or {}).get("selectedAgentRole", "UNRESOLVED"),
        "workMode": routed.get("mode"),
        "allowedCommandCount": len(direct_commands),
        "taskContextCommandCount": len(contextual_commands),
        "hardGates": list(execution_core.get("hardGates") or []),
        "authoritativeRefs": list(execution_core.get("authoritativeRefs") or []),
        "capabilityPackIds": [item["id"] for item in capability_pack_context["packs"]],
        "skillImplementationReadPolicy": implementation_read_policy,
    }


def build_result_payload(
    *,
    agent: str,
    task_type: str,
    phase_contract: dict[str, Any] | None,
    phase_id: str,
    routed: dict[str, Any],
    capability_pack_context: dict[str, Any],
    architecture_review_context: dict[str, Any],
    request_context: dict[str, Any],
    completion_gate_summary: dict[str, Any],
    direct_commands: list[str],
    contextual_commands: list[str],
    project_fact_paths: dict[str, str],
    project_fact_read_policy: dict[str, Any],
    traceability_summary: dict[str, Any],
    verification: dict[str, Any],
    context_view: dict[str, Any],
    evidence_context: dict[str, Any],
    business_flow_context: dict[str, Any],
    cross_module_context: dict[str, Any],
    git_checkpoint_context: dict[str, Any],
    source_map: dict[str, Any],
    runtime_contract: dict[str, Any],
    is_project_manager: bool,
    execution_order: Iterable[str],
    forbidden_shortcuts: Iterable[str],
) -> dict[str, Any]:
    execution_core = build_execution_core(
        agent=agent,
        phase_id=phase_id,
        phase_contract=phase_contract,
        routed=routed,
        direct_commands=direct_commands,
        contextual_commands=contextual_commands,
        capability_pack_context=capability_pack_context,
        implementation_read_policy=source_map.get("skillImplementationReadPolicy", {}),
    )
    return {
        "schemaVersion": 1,
        "packageVersion": runtime_contract.get("packageVersion"),
        "selectedAgent": agent,
        "taskType": task_type,
        "agentContract": {
            "name": agent,
            "roleReference": f"references/review-agents/roles.md#{agent}",
            "permissionSource": "assets/runtime/runtime-contract.json#/scriptPermissions",
            "isProjectManager": is_project_manager,
            "directCommands": direct_commands,
            "taskContextCommands": contextual_commands,
        },
        "phaseContract": phase_contract,
        "reviewProfileIds": build_review_profile_ids(phase_contract),
        "workMode": routed.get("mode"),
        "capabilityPacks": capability_pack_context,
        "executionCore": execution_core,
        "architectureReviewContext": architecture_review_context,
        "requestIntakeContext": request_context,
        "completionGateSummary": completion_gate_summary,
        "resources": routed.get("resources"),
        "totalTokenBudget": routed.get("totalTokenBudget"),
        "budgetHeadroom": routed.get("budgetHeadroom"),
        "routingLimits": routed.get("limits"),
        "omittedResources": routed.get("omittedResources", []),
        "deduplicatedResources": routed.get("deduplicatedResources", []),
        "matchedConditionIds": routed.get("matchedConditionIds"),
        "omittedConditionIds": routed.get("omittedConditionIds", []),
        "omittedConditionReasons": routed.get("omittedConditionReasons", []),
        "insufficientMandatoryResources": routed.get("insufficientMandatoryResources", []),
        "allowedCommands": direct_commands,
        "taskContextCommands": contextual_commands,
        "projectFactPaths": project_fact_paths,
        "projectFactReadPolicy": project_fact_read_policy,
        "traceabilitySummary": traceability_summary,
        "taskVerification": verification,
        "contextView": context_view,
        "evidenceContext": evidence_context,
        "businessFlowContext": business_flow_context,
        "crossModuleImplementationContext": cross_module_context,
        "gitCheckpointContext": git_checkpoint_context,
        "executionPolicy": runtime_contract.get("executionPolicy", {}),
        "authoritativeSources": source_map.get("authoritativeSources", {}),
        "executionOrder": list(execution_order),
        "forbiddenShortcuts": list(forbidden_shortcuts),
    }
