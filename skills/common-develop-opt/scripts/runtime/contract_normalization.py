#!/usr/bin/env python3
"""Materialize compact V2 machine contracts into runtime views."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any
from resource_paths import asset_path, formalize

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_PATH = asset_path('assets/runtime/runtime-contract.json')
REVIEW_PATH = asset_path('assets/review/review-contract.json')
ROUTING_PATH = ROOT / "context-routing.json"


class ContractNormalizationError(ValueError):
    """Raised when a normalized contract cannot be deterministically materialized."""


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(formalize(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractNormalizationError(f"cannot load contract {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractNormalizationError(f"contract must be an object: {path}")
    return value


def materialize_runtime_contract(raw: dict[str, Any]) -> dict[str, Any]:
    """Expand V2 permission profiles into the runtime command map."""
    if raw.get("schemaVersion") != 2:
        raise ContractNormalizationError("runtime contract schemaVersion must be 2")
    contract = copy.deepcopy(raw)
    profiles = contract.get("scriptPermissionProfiles")
    assignments = contract.get("scriptPermissionAssignments")
    if not isinstance(profiles, dict) or not isinstance(assignments, dict):
        raise ContractNormalizationError("runtime contract normalized script permissions are invalid")
    permissions: dict[str, dict[str, Any]] = {}
    for profile_id, commands in assignments.items():
        profile = profiles.get(profile_id)
        if not isinstance(profile, dict):
            raise ContractNormalizationError(f"unknown script permission profile: {profile_id}")
        if not isinstance(commands, list) or not commands or any(not isinstance(command, str) or not command for command in commands):
            raise ContractNormalizationError(f"invalid script permission assignments: {profile_id}")
        for command in commands:
            if command in permissions:
                raise ContractNormalizationError(f"script permission assigned more than once: {command}")
            # ``mutation`` is a static command-surface classification used by
            # check_skill.  Keep it on the compact permission profile, but do
            # not expose it in the runtime authorization view: callers rely on
            # the historical scriptPermissions shape and mutation is not an
            # authorization capability.
            runtime_profile = {
                key: value for key, value in copy.deepcopy(profile).items()
                if key != "mutation"
            }
            permissions[command] = runtime_profile
    contract["scriptPermissions"] = permissions
    return contract


def materialize_review_contract(raw: dict[str, Any]) -> dict[str, Any]:
    """Apply review defaults and derive profile identity fields from profile keys."""
    if raw.get("schemaVersion") != 2:
        raise ContractNormalizationError("review contract schemaVersion must be 2")
    contract = copy.deepcopy(raw)
    evidence_defaults = contract.get("evidencePolicyDefaults")
    criterion_defaults = contract.get("criterionDefaults")
    if not isinstance(evidence_defaults, dict) or not isinstance(criterion_defaults, dict):
        raise ContractNormalizationError("review contract defaults are invalid")

    policies = contract.get("evidencePolicyCatalog")
    if not isinstance(policies, dict):
        raise ContractNormalizationError("review contract evidencePolicyCatalog must be an object")
    contract["evidencePolicyCatalog"] = {
        policy_id: {**copy.deepcopy(evidence_defaults), **copy.deepcopy(policy)}
        for policy_id, policy in policies.items()
        if isinstance(policy, dict)
    }
    if len(contract["evidencePolicyCatalog"]) != len(policies):
        raise ContractNormalizationError("review contract contains a non-object evidence policy")

    criteria = contract.get("criteriaCatalog")
    if not isinstance(criteria, dict):
        raise ContractNormalizationError("review contract criteriaCatalog must be an object")
    contract["criteriaCatalog"] = {
        criterion_id: {**copy.deepcopy(criterion_defaults), **copy.deepcopy(criterion)}
        for criterion_id, criterion in criteria.items()
        if isinstance(criterion, dict)
    }
    if len(contract["criteriaCatalog"]) != len(criteria):
        raise ContractNormalizationError("review contract contains a non-object criterion")

    profiles = contract.get("profiles")
    if not isinstance(profiles, dict):
        raise ContractNormalizationError("review contract profiles must be an object")
    def _expand_profiles(values: dict[str, Any], label: str) -> dict[str, dict[str, Any]]:
        expanded: dict[str, dict[str, Any]] = {}
        for profile_key, profile in values.items():
            if not isinstance(profile, dict) or ":" not in profile_key:
                raise ContractNormalizationError(f"invalid {label} review profile: {profile_key}")
            phase, reviewer_agent = profile_key.split(":", 1)
            expanded[profile_key] = {
                "profileId": profile_key,
                "phase": phase,
                "reviewerAgent": reviewer_agent,
                **copy.deepcopy(profile),
            }
        return expanded

    expanded_profiles = _expand_profiles(profiles, "active")
    risk_templates = contract.get("riskReviewTemplates", {})
    if not isinstance(risk_templates, dict):
        raise ContractNormalizationError("review contract riskReviewTemplates must be an object")
    for risk_id, definition in risk_templates.items():
        if not isinstance(risk_id, str) or not isinstance(definition, dict):
            raise ContractNormalizationError(f"invalid risk Review template: {risk_id}")
        reviewer = definition.get("reviewerAgent")
        phases = definition.get("allowedPhases")
        template_ref = definition.get("templateRef")
        if not isinstance(reviewer, str) or not reviewer:
            raise ContractNormalizationError(f"risk Review template missing reviewerAgent: {risk_id}")
        if not isinstance(phases, list) or not phases or any(not isinstance(phase, str) or not phase for phase in phases):
            raise ContractNormalizationError(f"risk Review template has invalid allowedPhases: {risk_id}")
        if template_ref is not None and template_ref not in contract.get("profileTemplates", {}):
            raise ContractNormalizationError(f"risk Review template references unknown profile template: {risk_id}")
        if template_ref is None and not isinstance(definition.get("criterionIds"), list):
            raise ContractNormalizationError(f"risk Review template needs templateRef or criterionIds: {risk_id}")
    contract["profiles"] = expanded_profiles
    return contract


def materialize_context_routing(raw: dict[str, Any]) -> dict[str, Any]:
    """Expand routing resource defaults and grouped base-route budgets."""
    if raw.get("schemaVersion") != 2:
        raise ContractNormalizationError("context routing schemaVersion must be 2")
    contract = copy.deepcopy(raw)
    defaults = contract.get("resourceDefaults")
    catalog = contract.get("resourceCatalog")
    if not isinstance(defaults, dict) or not isinstance(catalog, dict):
        raise ContractNormalizationError("context routing normalized resource catalog is invalid")
    path_template = defaults.get("pathTemplate")
    default_strategy = defaults.get("readStrategy")
    if not isinstance(path_template, str) or "{id}" not in path_template or not isinstance(default_strategy, str):
        raise ContractNormalizationError("context routing resourceDefaults are invalid")
    governance = contract.get("governancePolicy")
    levels = governance.get("hardConstraintLevels") if isinstance(governance, dict) else None
    if not isinstance(levels, dict) or set(levels) != {"HARD_GATE", "REQUIRED", "RECOMMENDED", "EXAMPLE"}:
        raise ContractNormalizationError("context routing hardConstraintLevels are incomplete")
    expanded_catalog: dict[str, dict[str, Any]] = {}
    for resource_id, definition in catalog.items():
        if not isinstance(definition, dict):
            raise ContractNormalizationError(f"invalid resource definition: {resource_id}")
        expanded = copy.deepcopy(definition)
        expanded.setdefault("path", path_template.format(id=resource_id))
        expanded.setdefault("readStrategy", default_strategy)
        status = expanded.get("status")
        if status not in {"active", "deprecated", "internal", "on_demand"}:
            raise ContractNormalizationError(f"invalid resource status: {resource_id}")
        if not isinstance(expanded.get("owner"), str) or not expanded["owner"].strip():
            raise ContractNormalizationError(f"resource owner is required: {resource_id}")
        if "replacement" not in expanded:
            raise ContractNormalizationError(f"resource replacement is required: {resource_id}")
        if status == "deprecated" and not isinstance(expanded.get("replacement"), str):
            raise ContractNormalizationError(f"deprecated resource replacement is invalid: {resource_id}")
        if status != "deprecated" and expanded.get("replacement") is not None:
            raise ContractNormalizationError(f"non-deprecated resource replacement must be null: {resource_id}")
        if not isinstance(expanded.get("lastReviewed"), str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expanded["lastReviewed"]):
            raise ContractNormalizationError(f"resource lastReviewed must be YYYY-MM-DD: {resource_id}")
        priority_class = expanded.get("priorityClass", defaults.get("priorityClass", "helpful"))
        if priority_class not in {"mandatory", "required-if-triggered", "helpful", "example-only"}:
            raise ContractNormalizationError(f"invalid resource priorityClass: {resource_id}")
        expanded["priorityClass"] = priority_class
        expanded_catalog[resource_id] = expanded
    contract["resourceCatalog"] = expanded_catalog
    fixed_ids = {str(value) for value in contract.get("fixedResources") or [] if isinstance(value, str)}
    route_ids = {
        str(resource_id)
        for route in contract.get("routes") or []
        if isinstance(route, dict)
        for resource_id in route.get("resources") or []
        if isinstance(resource_id, str)
    }
    required_mode_ids: set[str] = set()
    triggered_mode_ids: set[str] = set()
    for override in (contract.get("modeOverrides") or {}).values():
        if not isinstance(override, dict):
            continue
        required_mode_ids.update(str(value) for value in override.get("requiredResources") or [] if isinstance(value, str))
        triggered_mode_ids.update(str(value) for value in override.get("includeResources") or [] if isinstance(value, str))
    triggered_condition_ids = {
        str(resource_id)
        for condition in contract.get("promptConditions") or []
        if isinstance(condition, dict)
        for resource_id in condition.get("resources") or []
        if isinstance(resource_id, str)
    }
    for resource_id, definition in expanded_catalog.items():
        if resource_id in fixed_ids or resource_id in route_ids or resource_id in required_mode_ids:
            definition["priorityClass"] = "mandatory"
        elif resource_id in triggered_mode_ids or resource_id in triggered_condition_ids:
            definition["priorityClass"] = "required-if-triggered"
        else:
            definition["priorityClass"] = definition.get("priorityClass", "helpful")

    policy = contract.get("routingPolicy")
    if not isinstance(policy, dict):
        raise ContractNormalizationError("context routing routingPolicy must be an object")
    groups = policy.get("baseRouteBudgetGroups")
    if not isinstance(groups, list):
        raise ContractNormalizationError("context routing baseRouteBudgetGroups must be an array")
    targets: dict[str, int] = {}
    for group in groups:
        if not isinstance(group, dict):
            raise ContractNormalizationError("context routing base-route budget group must be an object")
        budget = group.get("tokenBudget")
        task_types = group.get("taskTypes")
        if not isinstance(budget, int) or isinstance(budget, bool) or budget <= 0:
            raise ContractNormalizationError("context routing base-route budget must be a positive integer")
        if not isinstance(task_types, list) or not task_types:
            raise ContractNormalizationError("context routing base-route budget group taskTypes are invalid")
        for task_type in task_types:
            if not isinstance(task_type, str) or not task_type:
                raise ContractNormalizationError("context routing base-route task type is invalid")
            if task_type in targets:
                raise ContractNormalizationError(f"base-route task type assigned more than once: {task_type}")
            targets[task_type] = budget
    policy["baseRouteTokenBudgetTargets"] = targets
    order = policy.get("order")
    if order != ["fixedResources", "exactTaskRoute", "modeOverrides", "matchingPromptConditions"]:
        raise ContractNormalizationError("context routing order must be fixed, exact route, mode overrides, prompt conditions")
    condition_match = policy.get("conditionMatch", "typed")
    if condition_match not in {"typed", "case_insensitive_substring"}:
        raise ContractNormalizationError("context routing conditionMatch is invalid")
    condition_modes = policy.get("conditionMatchModes", ["stable_id", "exact_phrase", "token", "substring"])
    if not isinstance(condition_modes, list) or not condition_modes or any(mode not in {"stable_id", "exact_phrase", "token", "substring"} for mode in condition_modes):
        raise ContractNormalizationError("context routing conditionMatchModes are invalid")
    policy["conditionMatchModes"] = list(condition_modes)
    conditions = contract.get("promptConditions")
    if not isinstance(conditions, list):
        raise ContractNormalizationError("context routing promptConditions must be an array")
    for condition in conditions:
        if not isinstance(condition, dict):
            raise ContractNormalizationError("context routing prompt condition must be an object")
        condition.setdefault("match", "any")
        condition.setdefault("minKeywordHits", 1)
        if condition["match"] not in {"any", "all", "threshold"}:
            raise ContractNormalizationError("context routing prompt condition match is invalid")
        if not isinstance(condition["minKeywordHits"], int) or isinstance(condition["minKeywordHits"], bool) or condition["minKeywordHits"] <= 0:
            raise ContractNormalizationError("context routing prompt condition minKeywordHits is invalid")
    policy.setdefault("maxMatchedPromptConditionsMode", "advisory")
    policy.setdefault("budgetStrategy", "best_effort")
    if policy["maxMatchedPromptConditionsMode"] not in {"advisory", "hard"}:
        raise ContractNormalizationError("context routing maxMatchedPromptConditionsMode is invalid")
    if policy["budgetStrategy"] not in {"best_effort", "strict"}:
        raise ContractNormalizationError("context routing budgetStrategy is invalid")
    return contract


def load_runtime_contract(path: Path = RUNTIME_PATH) -> dict[str, Any]:
    return materialize_runtime_contract(_read_object(path))


def load_review_contract(path: Path = REVIEW_PATH) -> dict[str, Any]:
    return materialize_review_contract(_read_object(path))


def load_context_routing(path: Path = ROUTING_PATH) -> dict[str, Any]:
    return materialize_context_routing(_read_object(path))
