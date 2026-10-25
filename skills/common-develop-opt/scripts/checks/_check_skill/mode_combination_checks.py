"""Static satisfiability checks for runtime work-mode combinations."""
from __future__ import annotations

from itertools import product
from typing import Any

from checks._check_skill.model import CheckResult

_MODE_FIELDS = {
    "modelCode": "model_code",
    "pageDesign": "page_design",
    "minimal": "minimal",
    "lightweight": "lightweight",
    "auto": "auto",
    "gitCheckpoint": "git_checkpoint",
    "gitPush": "git_push",
    "architectureReview": "architecture_review",
    "reviewOnly": "review_only",
}


def _graph_has_cycle(graph: dict[str, list[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(parent) for parent in graph.get(node, [])):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


def _phase_graph(contract: dict[str, Any], skipped: set[str]) -> dict[str, list[str]]:
    graph: dict[str, list[str]] = {}
    for phase in contract.get("canonicalPhases", []):
        if not isinstance(phase, dict):
            continue
        phase_id = phase.get("id")
        if not isinstance(phase_id, str) or phase_id in skipped:
            continue
        predecessors = phase.get("requiredPredecessors", [])
        graph[phase_id] = [
            predecessor for predecessor in predecessors
            if isinstance(predecessor, str) and predecessor not in skipped
        ]
    return graph


def _mode_policy_conflicts(modes: dict[str, dict[str, Any]], active: set[str]) -> list[str]:
    """Return column constraints that cannot be satisfied by one work row.

    The runtime validator has two kinds of policies: ordinary opt-in modes
    require ``enabledValue`` when active and ``disabledValue`` otherwise;
    ``minimal`` and ``lightweight`` are suppressive modes and require their
    columns to remain ``disabledValue`` when active.  Modeling the same rules
    here catches combinations such as ``lightweight + architectureReview``
    even when the two contract entries use the same enabled/disabled values.
    """
    constraints: dict[str, tuple[str, str]] = {}
    conflicts: list[str] = []
    suppressive = {"minimal", "lightweight"}
    for mode_name, definition in modes.items():
        if not isinstance(definition, dict):
            continue
        columns = definition.get("workColumns") or []
        if not isinstance(columns, list):
            continue
        enabled = str(definition.get("enabledValue", "AIW"))
        disabled = str(definition.get("disabledValue", "无"))
        if mode_name in suppressive:
            if mode_name not in active:
                continue
            required = disabled
        else:
            required = enabled if mode_name in active else disabled
        for column in columns:
            if not isinstance(column, str):
                continue
            previous = constraints.get(column)
            if previous and previous[0] != required:
                conflicts.append(
                    f"column={column!r}: {previous[1]} requires {previous[0]!r}, "
                    f"{mode_name} requires {required!r}"
                )
            else:
                constraints[column] = (required, mode_name)
    return conflicts


def _validate_shape(contract: dict[str, Any], result: CheckResult) -> tuple[dict[str, dict[str, Any]], set[str]]:
    command_interface = contract.get("commandInterface")
    modes = command_interface.get("workModes") if isinstance(command_interface, dict) else None
    if not isinstance(modes, dict):
        result.errors.append("assets/runtime/runtime-contract.json: commandInterface.workModes must be an object")
        return {}, set()
    # commandInterface.workModes also carries metadata keys such as
    # combinationAllowed/stateSource; only the nine boolean mode entries are
    # part of the enumeration.
    metadata_keys = {"applicationPolicy"}
    unknown = sorted(
        name for name in modes
        if name not in _MODE_FIELDS and name not in metadata_keys and isinstance(modes[name], dict)
    )
    if unknown:
        result.errors.append(f"assets/runtime/runtime-contract.json: unknown work modes: {unknown}")
    known_modes = {name: definition for name, definition in modes.items() if name in _MODE_FIELDS and isinstance(definition, dict)}
    phase_ids = {
        phase.get("id") for phase in contract.get("canonicalPhases", [])
        if isinstance(phase, dict) and isinstance(phase.get("id"), str)
    }
    for mode_name, definition in known_modes.items():
        for field in ("workColumns", "skippedPhases"):
            values = definition.get(field, [])
            if not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values):
                result.errors.append(f"work mode {mode_name}.{field} must be a string array")
        invalid_phases = sorted(set(definition.get("skippedPhases", [])) - phase_ids)
        if invalid_phases:
            result.errors.append(f"work mode {mode_name}.skippedPhases references invalid phases: {invalid_phases}")
        suppressions = definition.get("suppressedReviewerAssignments", {})
        if suppressions and not isinstance(suppressions, dict):
            result.errors.append(f"work mode {mode_name}.suppressedReviewerAssignments must be an object")
        if isinstance(suppressions, dict):
            for phase, reviewers in suppressions.items():
                if phase not in phase_ids:
                    result.errors.append(f"work mode {mode_name} suppression references invalid phase: {phase}")
                if not isinstance(reviewers, list) or any(not isinstance(agent, str) for agent in reviewers):
                    result.errors.append(f"work mode {mode_name} suppression for {phase} must be a string array")
    return known_modes, phase_ids


def check_work_mode_combination_satisfiability(contract: dict[str, Any], result: CheckResult) -> None:
    """Enumerate mode states and reject contradictions in the contract itself."""
    modes, phase_ids = _validate_shape(contract, result)
    if not modes:
        return
    mode_names = list(modes)
    profiles = contract.get("workflowProfiles") or {}
    all_agents = set(contract.get("agents") or [])
    global_conflicts: list[str] = []
    dependency_conflicts: list[str] = []

    for mode_name, definition in modes.items():
        dependencies = definition.get("dependsOn", []) if isinstance(definition, dict) else []
        if not isinstance(dependencies, list) or any(not isinstance(dep, str) or dep not in modes for dep in dependencies):
            result.errors.append(f"work mode {mode_name}.dependsOn references unknown mode")

    # Validate every declared profile against the policy it claims to enforce.
    for profile_name, profile in profiles.items():
        if not isinstance(profile, dict):
            continue
        incompatible = profile.get("incompatibleWorkModes", [])
        if not isinstance(incompatible, list) or any(mode not in modes for mode in incompatible):
            result.errors.append(f"workflow profile {profile_name}.incompatibleWorkModes references unknown mode")
        required_sequence = set(profile.get("requiredSequence") or [])
        for mode_name, definition in modes.items():
            skipped = set(definition.get("skippedPhases") or [])
            if required_sequence & skipped and mode_name not in incompatible:
                result.errors.append(
                    f"workflow profile {profile_name} must declare {mode_name} incompatible because it skips requiredSequence phases"
                )

    valid_combinations = 0
    for values in product((False, True), repeat=len(mode_names)):
        active = {name for name, enabled in zip(mode_names, values) if enabled}
        suffix = ",".join(sorted(active)) or "none"
        for mode_name in sorted(active):
            dependencies = set(modes[mode_name].get("dependsOn") or [])
            missing = sorted(dependencies - active)
            if missing:
                dependency_conflicts.append(f"[{suffix}] {mode_name} requires {missing}")
        if not any(set(modes[name].get("dependsOn") or []) - active for name in active):
            valid_combinations += 1
        conflicts = _mode_policy_conflicts(modes, active)
        if conflicts:
            global_conflicts.extend(f"[{suffix}] {item}" for item in conflicts)
        skipped = set().union(*(set(modes[name].get("skippedPhases") or []) for name in active))
        if skipped - phase_ids:
            global_conflicts.append(f"[{suffix}] invalid skipped phase")
        if _graph_has_cycle(_phase_graph(contract, skipped)):
            global_conflicts.append(f"[{suffix}] skipped phase predecessor graph contains a cycle")
        for profile_name, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            incompatible = set(profile.get("incompatibleWorkModes") or [])
            if active & incompatible:
                continue
            sequence = set(profile.get("requiredSequence") or [])
            if sequence & skipped:
                global_conflicts.append(f"[{suffix}] profile {profile_name} skips requiredSequence phase")

        # A suppression can remove all mandatory reviewers only when those
        # reviewers are declared for the phase.  Unknown suppressed names are
        # shape errors, not a reason to infer canonical membership.
        for mode_name in active:
            suppression = modes[mode_name].get("suppressedReviewerAssignments") or {}
            for phase, reviewers in suppression.items():
                if any(reviewer not in all_agents for reviewer in reviewers):
                    global_conflicts.append(f"[{suffix}] {mode_name}.{phase} suppresses undeclared reviewer")

    result.details["workModeCombinationCheck"] = {
        "booleanModes": mode_names,
        "enumeratedCombinations": 2 ** len(mode_names),
        "dependencyValidCombinations": valid_combinations,
        "dependencyConflicts": len(dependency_conflicts),
        # Dependency-invalid combinations are expected and counted separately;
        # only direct policy contradictions make the contract fail.
        "conflicts": len(global_conflicts),
        "examples": (dependency_conflicts + global_conflicts)[:20],
    }
    if global_conflicts:
        result.errors.extend(f"work-mode combination unsatisfiable: {item}" for item in global_conflicts[:20])
    else:
        result.ok(f"Work-mode combination satisfiability checked ({2 ** len(mode_names)} combinations)")
