#!/usr/bin/env python3
"""Resolve the minimal common-develop Reference set for one task.

The resolver is the executable counterpart of context-routing.json. It prevents
Agents from manually loading all references or maintaining a second routing
matrix in prompts.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import agent_context
import work_mode
import contract_normalization

ROOT = Path(__file__).resolve().parent.parent
ROUTING_PATH = ROOT / "context-routing.json"


class ContextResolutionError(ValueError):
    """Raised when a task type or routing contract cannot be resolved."""


@dataclass(frozen=True)
class ResolvedMode:
    model_code: bool = False
    page_design: bool = False
    minimal: bool = False
    auto: bool = False
    architecture_review: bool = False
    git_checkpoint: bool = False
    git_push: bool = False
    lightweight: bool = False
    source: str = "default"

    def to_dict(self) -> dict[str, object]:
        return {
            "modelCode": self.model_code,
            "pageDesign": self.page_design,
            "minimal": self.minimal,
            "auto": self.auto,
            "architectureReview": self.architecture_review,
            "gitCheckpoint": self.git_checkpoint,
            "gitPush": self.git_push,
            "lightweight": self.lightweight,
            "source": self.source,
        }


def load_routing(path: Path = ROUTING_PATH) -> dict[str, Any]:
    try:
        return contract_normalization.load_context_routing(path)
    except contract_normalization.ContractNormalizationError as exc:
        raise ContextResolutionError(str(exc)) from exc


def resolve_mode(doc_root: str = "", version: str = "") -> ResolvedMode:
    if not doc_root and not version:
        return ResolvedMode()
    if not doc_root or not version:
        raise ContextResolutionError("--doc-root and --version must be supplied together")
    path = work_mode.work_path(Path(doc_root), version)
    if not path.is_file():
        raise ContextResolutionError(f"work mode source does not exist: {path}")
    parsed = work_mode.parse_mode(path.read_text(encoding="utf-8"))
    return ResolvedMode(
        model_code=parsed.model_code,
        page_design=parsed.page_design,
        minimal=parsed.minimal,
        auto=parsed.auto,
        architecture_review=parsed.architecture_review,
        git_checkpoint=parsed.git_checkpoint,
        git_push=parsed.git_push,
        lightweight=parsed.lightweight,
        source=str(path),
    )


def _task_route(routing: dict[str, Any], task_type: str) -> dict[str, Any]:
    matches = [
        route
        for route in routing.get("routes", [])
        if isinstance(route, dict) and task_type in (route.get("taskTypes") or [])
    ]
    if not matches:
        raise ContextResolutionError(f"unknown task type: {task_type}")
    if len(matches) != 1:
        raise ContextResolutionError(f"task type is routed more than once: {task_type}")
    return matches[0]


def _keyword_match(prompt: str, keyword: str, match_type: str = "") -> bool:
    """Match one routing keyword without treating every value as a substring."""
    normalized = prompt.casefold()
    value = keyword.casefold().strip()
    if not value:
        return False
    selected = match_type
    if not selected:
        if value.endswith("-") and re.fullmatch(r"[a-z][a-z0-9_]*-", value):
            selected = "stable_id"
        elif re.fullmatch(r"--?[a-z0-9][a-z0-9_-]*", value):
            selected = "exact_phrase"
        elif re.fullmatch(r"[a-z0-9_]+", value):
            selected = "token"
        else:
            selected = "exact_phrase"
    if selected == "stable_id":
        return bool(re.search(rf"(?<![a-z0-9_]){re.escape(value)}[a-z0-9][a-z0-9_.:-]*", normalized))
    if selected == "token":
        return bool(re.search(rf"(?<![a-z0-9_-]){re.escape(value)}(?![a-z0-9_-])", normalized))
    if selected == "substring":
        return value in normalized
    if selected == "exact_phrase":
        if re.fullmatch(r"[a-z0-9_-]+(?:\s+[a-z0-9_-]+)*", value):
            return bool(re.search(rf"(?<![a-z0-9_-]){re.escape(value)}(?![a-z0-9_-])", normalized))
        return value in normalized
    raise ContextResolutionError(f"unknown prompt keyword match type: {selected}")


def _matching_conditions(routing: dict[str, Any], prompt: str) -> list[dict[str, Any]]:
    matches: list[tuple[int, int, dict[str, Any]]] = []
    matched_ids: set[str] = set()
    conditions = routing.get("promptConditions", [])
    for index, condition in enumerate(conditions):
        if not isinstance(condition, dict):
            continue
        keywords = [value for value in condition.get("keywords", []) if isinstance(value, str)]
        keyword_match = str(condition.get("keywordMatch") or "")
        hits = [value for value in keywords if _keyword_match(prompt, value, keyword_match)]
        excludes = [value for value in condition.get("excludeKeywords", []) if isinstance(value, str)]
        if any(_keyword_match(prompt, value) for value in excludes):
            continue
        mode = str(condition.get("match") or "any")
        minimum = condition.get("minKeywordHits", 1)
        min_hits = minimum if isinstance(minimum, int) and not isinstance(minimum, bool) and minimum > 0 else 1
        if mode == "all":
            matched = bool(keywords) and len(hits) == len(keywords)
        elif mode == "threshold":
            matched = len(hits) >= min_hits
        elif mode == "any":
            matched = len(hits) >= min_hits
        else:
            raise ContextResolutionError(f"unknown prompt condition match mode: {mode}")
        required = {str(value) for value in condition.get("requires", []) if isinstance(value, str)}
        excluded_ids = {str(value) for value in condition.get("excludes", []) if isinstance(value, str)}
        cooccurs = [value for value in condition.get("cooccursWith", []) if isinstance(value, str)]
        if cooccurs and not any(_keyword_match(prompt, value) for value in cooccurs):
            matched = False
        if required and not required.issubset(matched_ids):
            matched = False
        if excluded_ids & matched_ids:
            matched = False
        if matched:
            priority = condition.get("priority", 0)
            priority_value = priority if isinstance(priority, int) and not isinstance(priority, bool) else 0
            enriched = dict(condition)
            enriched["matchedKeywords"] = hits
            matches.append((priority_value, -index, enriched))
            matched_ids.add(str(condition.get("id") or ""))
    matches.sort(reverse=True, key=lambda item: (item[0], item[1]))
    return [item[2] for item in matches]


def resolve_context(
    task_type: str,
    *,
    prompt: str = "",
    mode: ResolvedMode | None = None,
    routing: dict[str, Any] | None = None,
    budget_strategy: str = "",
) -> dict[str, Any]:
    contract = load_routing() if routing is None else contract_normalization.materialize_context_routing(routing)
    selected_mode = ResolvedMode() if mode is None else mode
    policy = contract.get("routingPolicy") or {}
    catalog = contract.get("resourceCatalog") or {}
    fixed = list(contract.get("fixedResources") or [])
    route = _task_route(contract, task_type)
    strategy = budget_strategy or str(policy.get("budgetStrategy") or "best_effort")
    if strategy not in {"best_effort", "strict"}:
        raise ContextResolutionError(f"unknown context budget strategy: {strategy}")

    minimal_override = (contract.get("modeOverrides") or {}).get("minimal") or {}
    lightweight_override = (contract.get("modeOverrides") or {}).get("lightweight") or {}
    if selected_mode.minimal and task_type in set(minimal_override.get("skipTaskTypes") or []):
        raise ContextResolutionError(f"task type {task_type} is skipped by the current minimal work mode")
    if selected_mode.lightweight and task_type in set(lightweight_override.get("skipTaskTypes") or []):
        raise ContextResolutionError(f"task type {task_type} is skipped by the current lightweight work mode")

    max_conditions = int(policy.get("maxMatchedPromptConditions", 0))
    max_dynamic = int(policy.get("maxDynamicResources", 0))
    max_total = int(policy.get("maxTotalResources", 0))
    hard_budget = int(policy.get("maxDefaultTokenBudget", 0))
    target_budget = int(policy.get("targetResolvedTokenBudget", hard_budget))
    reserved_task_fact_budget = int(policy.get("reservedTaskFactTokenBudget", 0))
    max_fixed_budget = int(policy.get("maxFixedTokenBudget", 0))
    max_base_route_budget = int(policy.get("maxBaseRouteTokenBudget", 0))
    base_route_targets = dict(policy.get("baseRouteTokenBudgetTargets") or {})
    conditions = _matching_conditions(contract, prompt)

    excluded: dict[str, str] = {}
    if selected_mode.minimal:
        excluded.update({rid: "MODE_EXCLUDED_MINIMAL" for rid in minimal_override.get("excludeResources") or []})
    if selected_mode.lightweight:
        excluded.update({rid: "MODE_EXCLUDED_LIGHTWEIGHT" for rid in lightweight_override.get("excludeResources") or []})

    candidates: list[dict[str, str]] = []
    candidate_keys: set[str] = set()
    omitted_resources: list[dict[str, Any]] = []
    deduplicated_resources: list[dict[str, str]] = []

    def definition(resource_id: str) -> dict[str, Any]:
        value = catalog.get(resource_id)
        if not isinstance(value, dict):
            raise ContextResolutionError(f"resource is missing from catalog: {resource_id}")
        return value

    def add_candidate(resource_id: str, source: str, *, required: bool = False) -> None:
        item = definition(resource_id)
        priority_class = "mandatory" if required else str(item.get("priorityClass") or "helpful")
        if resource_id in fixed:
            deduplicated_resources.append({"id": resource_id, "source": source, "reason": "FIXED_RESOURCE"})
            return
        if resource_id in excluded and priority_class != "mandatory":
            omitted_resources.append({
                "id": resource_id, "source": source,
                "tokenBudget": int(item.get("tokenBudget") or 0),
                "priorityClass": priority_class, "reason": excluded[resource_id],
            })
            return
        # Mode exclusions apply before promotion. An included conditional resource
        # is now required, even when it cannot fit within the context limits.
        if priority_class == "required-if-triggered":
            priority_class = "mandatory"
        if resource_id in candidate_keys:
            if priority_class == "mandatory":
                next(candidate for candidate in candidates if candidate["id"] == resource_id)["priorityClass"] = "mandatory"
            deduplicated_resources.append({"id": resource_id, "source": source, "reason": "RESOURCE_DEDUPLICATED"})
            return
        candidate_keys.add(resource_id)
        candidates.append({"id": resource_id, "source": source, "priorityClass": priority_class})

    # Canonical order: fixed -> exact route -> mode resources -> triggered conditions.
    for resource_id in route.get("resources") or []:
        add_candidate(resource_id, f"route:{task_type}", required=True)
    if selected_mode.minimal:
        for resource_id in minimal_override.get("requiredResources") or []:
            add_candidate(resource_id, "mode:minimal:required", required=True)
    if selected_mode.lightweight:
        for resource_id in lightweight_override.get("requiredResources") or []:
            add_candidate(resource_id, "mode:lightweight:required", required=True)
    mode_resources: list[tuple[bool, str, dict[str, Any], bool]] = [
        (selected_mode.page_design, "pageDesign", (contract.get("modeOverrides") or {}).get("pageDesign") or {}, True),
        (selected_mode.architecture_review, "architectureReview", (contract.get("modeOverrides") or {}).get("architectureReview") or {}, True),
        (selected_mode.git_checkpoint, "gitCheckpoint", (contract.get("modeOverrides") or {}).get("gitCheckpoint") or {}, False),
        (selected_mode.git_push, "gitPush", (contract.get("modeOverrides") or {}).get("gitPush") or {}, False),
    ]
    for enabled, mode_id, override, task_scoped in mode_resources:
        if not enabled or (task_scoped and task_type not in set(override.get("taskTypes") or [])):
            continue
        for resource_id in override.get("includeResources") or []:
            add_candidate(resource_id, f"mode:{mode_id}")
    for condition in conditions:
        condition_id = str(condition.get("id") or "")
        for resource_id in condition.get("resources") or []:
            add_candidate(resource_id, f"condition:{condition_id}")

    selected_sources: dict[str, str] = {resource_id: "fixed" for resource_id in fixed}
    resource_ids: list[str] = []
    current_budget = 0
    for resource_id in fixed:
        item = definition(resource_id)
        if resource_id in excluded:
            raise ContextResolutionError(f"mandatory fixed resource cannot be excluded: {resource_id}")
        resource_ids.append(resource_id)
        current_budget += int(item.get("tokenBudget") or 0)
    if current_budget > target_budget or current_budget > hard_budget:
        raise ContextResolutionError("fixed context resources exceed the configured token budget")
    if max_fixed_budget and current_budget > max_fixed_budget:
        raise ContextResolutionError("fixed context resources exceed maxFixedTokenBudget")

    base_route_resources = [rid for rid in route.get("resources") or [] if rid not in fixed]
    base_route_budget = current_budget + sum(int(definition(rid).get("tokenBudget") or 0) for rid in base_route_resources)
    selected_dynamic = 0
    insufficient: list[dict[str, Any]] = []
    for candidate in candidates:
        resource_id, source, priority_class = candidate["id"], candidate["source"], candidate["priorityClass"]
        item = definition(resource_id)
        token_budget = int(item.get("tokenBudget") or 0)
        mandatory = priority_class == "mandatory"
        reason = ""
        if selected_dynamic >= max_dynamic:
            reason = "MAX_DYNAMIC_RESOURCES"
        elif len(resource_ids) >= max_total:
            reason = "MAX_TOTAL_RESOURCES"
        elif current_budget + token_budget > hard_budget:
            reason = "MAX_DEFAULT_TOKEN_BUDGET"
        elif not mandatory and current_budget + token_budget > target_budget:
            reason = "TARGET_TOKEN_BUDGET"
        if reason:
            omitted = {"id": resource_id, "source": source, "tokenBudget": token_budget, "priorityClass": priority_class, "reason": reason}
            omitted_resources.append(omitted)
            if mandatory:
                insufficient.append(omitted)
            continue
        resource_ids.append(resource_id)
        selected_sources[resource_id] = source
        selected_dynamic += 1
        current_budget += token_budget

    status = "CONTEXT_INSUFFICIENT" if insufficient else "RESOLVED"
    if strategy == "strict" and insufficient:
        status = "CONTEXT_INSUFFICIENT"

    # A condition is omitted only when none of its resources is represented after deduplication.
    omitted_condition_reasons: list[dict[str, Any]] = []
    for condition in conditions:
        condition_id = str(condition.get("id") or "")
        condition_resources = [str(value) for value in condition.get("resources") or []]
        missing = [rid for rid in condition_resources if rid not in resource_ids]
        if condition_resources and len(missing) == len(condition_resources):
            reasons = sorted({str(item["reason"]) for item in omitted_resources if item["id"] in missing})
            omitted_condition_reasons.append({"id": condition_id, "reason": reasons[0] if len(reasons) == 1 else "ALL_RESOURCES_OMITTED", "resourceIds": missing, "resourceReasons": reasons})

    resources: list[dict[str, Any]] = []
    effective_priorities = {item["id"]: item["priorityClass"] for item in candidates}
    for resource_id in resource_ids:
        item = definition(resource_id)
        read_strategy = item.get("readStrategy")
        resource = {
            "id": resource_id, "path": item.get("path"), "tokenBudget": item.get("tokenBudget"),
            "purpose": item.get("purpose"), "readStrategy": read_strategy,
            "priorityClass": "mandatory" if resource_id in fixed else effective_priorities[resource_id], "fixed": resource_id in fixed,
            "selectedBy": selected_sources.get(resource_id, "unknown"),
            "readCommand": (f"python3 scripts/reference_reader.py read -g {{CURRENT_AGENT}} --resource {resource_id} --full" if read_strategy == "full" else f"python3 scripts/reference_reader.py read -g {{CURRENT_AGENT}} --resource {resource_id} --section {{SECTION_ID}}"),
        }
        if read_strategy == "relevant_sections":
            resource["sectionIndexCommand"] = f"python3 scripts/reference_reader.py index -g {{CURRENT_AGENT}} --resource {resource_id} --json"
            resource["queryReadCommand"] = f'python3 scripts/reference_reader.py read -g {{CURRENT_AGENT}} --resource {resource_id} --query "{{TOPIC}}" --max-sections 3'
        resources.append(resource)

    total_budget = sum(int(item.get("tokenBudget") or 0) for item in resources)
    return {
        "status": status, "taskType": task_type, "mode": selected_mode.to_dict(),
        "matchedConditionIds": [str(item.get("id") or "") for item in conditions],
        "omittedConditionIds": [item["id"] for item in omitted_condition_reasons],
        "omittedConditionReasons": omitted_condition_reasons,
        "resources": resources, "resourceIds": resource_ids,
        "omittedResources": omitted_resources, "deduplicatedResources": deduplicated_resources,
        "insufficientMandatoryResources": insufficient,
        "totalTokenBudget": total_budget, "budgetHeadroom": max(0, target_budget - total_budget),
        "baseRouteTokenBudget": base_route_budget,
        "executionCore": {
            "machineContextFirst": True,
            "authoritativeRefs": ["assets/runtime-contract.json", "assets/semantic-contract.json", "context-routing.json", "ai/source-map.json"],
            "detailResourceIds": ["runtime-contract", "platform-core", "scope-core", "semantic-core"],
            "hardGates": ["scripted_state_transitions_only", "current_revision_evidence_only", "independent_blocking_reviews", "preserve_task_git_and_audit_history"],
        },
        "limits": {
            "maxDynamicResources": max_dynamic, "maxMatchedPromptConditions": max_conditions,
            "maxMatchedPromptConditionsMode": policy.get("maxMatchedPromptConditionsMode"),
            "maxTotalResources": max_total, "maxDefaultTokenBudget": hard_budget,
            "targetResolvedTokenBudget": target_budget, "reservedTaskFactTokenBudget": reserved_task_fact_budget,
            "maxFixedTokenBudget": max_fixed_budget, "maxBaseRouteTokenBudget": max_base_route_budget,
            "baseRouteTokenBudgetTargets": base_route_targets, "budgetStrategy": strategy,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    resolve = commands.add_parser("resolve", help="resolve the minimal Reference set")
    agent_context.add_agent_argument(resolve)
    resolve.add_argument("--task-type", required=True)
    resolve.add_argument("--prompt", default="")
    resolve.add_argument("--doc-root", default="")
    resolve.add_argument("--version", default="")
    resolve.add_argument("--json", action="store_true")
    return parser


def command_resolve(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "context_resolver", "resolve", declared=True)
    result = resolve_context(
        args.task_type,
        prompt=args.prompt,
        mode=resolve_mode(args.doc_root, args.version),
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"TASK_TYPE {result['taskType']}")
        print("MODE " + json.dumps(result["mode"], ensure_ascii=False, separators=(",", ":")))
        if result["matchedConditionIds"]:
            print("CONDITIONS " + ",".join(result["matchedConditionIds"]))
        for item in result["resources"]:
            mode = "fixed" if item["fixed"] else "dynamic"
            print(
                f"RESOURCE {item['id']} {item['path']} budget={item['tokenBudget']} "
                f"read={item['readStrategy']} kind={mode}"
            )
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "resolve":
            return command_resolve(args)
        return 2
    except (agent_context.AgentAuthorizationError, ContextResolutionError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
