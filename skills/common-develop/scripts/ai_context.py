#!/usr/bin/env python3
"""Resolve one compact, read-only AI execution context from canonical contracts."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import agent_context
import context_resolver
import work_mode
import evidence
import long_task
import review_store
import simple_yaml
import task_verify
from common_utils import sha256_file
import doc_layout
import request_intake
import current_manifest
from _core import capability_packs
from _core.ai_context_sections import EXECUTION_ORDER, FORBIDDEN_SHORTCUTS
from _core.ai_context_payload import (
    build_phase_contract, build_result_payload, build_review_profile_ids,
)

ROOT = Path(__file__).resolve().parent.parent
SOURCE_MAP_PATH = ROOT / "ai" / "source-map.json"


class AIContextError(ValueError):
    """Raised when a compact execution context cannot be resolved."""


def _load_source_map() -> dict[str, Any]:
    try:
        value = json.loads(SOURCE_MAP_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AIContextError(f"cannot load AI source map: {exc}") from exc
    if not isinstance(value, dict) or value.get("kind") != "INDEX_ONLY":
        raise AIContextError("ai/source-map.json must be an INDEX_ONLY object")
    return value


def _phase_for_task(task_type: str, explicit_phase: str) -> dict[str, Any] | None:
    phase_id = explicit_phase or task_type
    for item in agent_context.RUNTIME_CONTRACT.get("canonicalPhases", []):
        if isinstance(item, dict) and item.get("id") == phase_id:
            return item
    if explicit_phase:
        raise AIContextError(f"unknown canonical phase: {explicit_phase}")
    return None


def _architecture_review_context(
    doc_root: str, version: str, target: str, phase: str, mode: dict[str, Any]
) -> dict[str, Any]:
    enabled = bool((mode or {}).get("architectureReview"))
    policy = (
        ((agent_context.RUNTIME_CONTRACT.get("commandInterface") or {}).get("workModes") or {})
        .get("architectureReview", {})
        .get("policy", {})
    )
    if not enabled:
        return {"enabled": False, "step": "DISABLED"}
    step = "SKELETON"
    state_ref = ""
    if doc_root and version and target:
        task_dir = Path(doc_root) / "version" / version / "task" / target
        state_ref = str(task_dir / "task_events.jsonl")
        if task_dir.exists():
            if not (task_dir / "task_events.jsonl").is_file():
                step = "INVALID"
            else:
                try:
                    state = long_task.runtime_state(task_dir)
                    ar_state = state.get("architecture_review") if isinstance(state, dict) else None
                    if isinstance(ar_state, dict) and ar_state.get("step"):
                        step = str(ar_state["step"])
                except (OSError, ValueError):
                    step = "INVALID"
    required_reviewers: list[str] = []
    if phase == "development":
        if step in {"SKELETON", "SKELETON_REVIEW_PASSED"}:
            required_reviewers = list((policy.get("firstStep") or {}).get("requiredReviewers") or [])
        elif step in {"IMPLEMENTATION", "IMPLEMENTATION_READY_FOR_REVIEW", "IMPLEMENTATION_REVIEW_PASSED"}:
            required_reviewers = list((policy.get("secondStep") or {}).get("developmentGateReviewers") or [])
            if bool((mode or {}).get("minimal")):
                suppressed = set(work_mode.MINIMAL_SUPPRESSED_REVIEWERS.get("development", []))
                required_reviewers = [value for value in required_reviewers if value not in suppressed]
    elif phase == "code_review":
        required_reviewers = list((policy.get("finalCodeReview") or {}).get("requiredReviewers") or [])
    return {
        "enabled": True,
        "step": step,
        "stateRef": state_ref or str(policy.get("stateSource") or ""),
        "policyRef": "assets/runtime-contract.json#/commandInterface/workModes/architectureReview/policy",
        "requiredReviewerAgents": required_reviewers,
        "transitionCommand": str(policy.get("transitionCommand") or ""),
        "canBeginConcreteImplementation": step == "SKELETON_REVIEW_PASSED",
        "canLeaveDevelopment": step in {"IMPLEMENTATION_READY_FOR_REVIEW", "IMPLEMENTATION_REVIEW_PASSED"},
        "finalReviewPassed": step == "IMPLEMENTATION_REVIEW_PASSED",
    }


def _allowed_commands(agent: str) -> tuple[list[str], list[str]]:
    direct: list[str] = []
    task_context: list[str] = []
    for key, definition in agent_context.RUNTIME_CONTRACT.get("scriptPermissions", {}).items():
        if not isinstance(key, str) or not isinstance(definition, dict):
            continue
        mode = definition.get("mode")
        if mode == "DECLARED_AGENT":
            direct.append(key)
        elif mode == "STATIC" and agent in definition.get("allowedAgents", []):
            direct.append(key)
        elif mode == "TASK_CONTEXT":
            task_context.append(key)
    return sorted(direct), sorted(task_context)


def _project_fact_paths(doc_root: str, version: str, target: str) -> dict[str, str]:
    if not doc_root or not version:
        return {}
    base = Path(doc_root) / "version" / version
    result = {
        "workMode": str(base / "work.md"),
        "businessFlowIndex": str(Path(doc_root) / "docs" / "_flows" / "flow_index.yaml"),
        "businessFlowRoot": str(Path(doc_root) / "docs" / "_flows"),
        "dependencyImpact": str(Path(doc_root) / "docs" / "_relations" / "dependency_impact.yaml"),
        "dependencyGraph": str(Path(doc_root) / "docs" / "_relations" / "dependency_graph.md"),
        "requirementList": str(base / "requirement_list.md"),
        "projectProcess": str(base / "project_process.md"),
        "workRecord": str(base / "work_record.md"),
    }
    if target:
        task = base / "task" / target
        result.update(
            {
                "requirement": str(base / "doc" / target / "requirement.md"),
                "taskPlan": str(task / "task_plan.json"),
                "gitCheckpoints": str(task / "git_checkpoints.yaml"),
                "requestIntake": str(task / "request_intake.json"),
                "currentManifest": str(task / "cache" / "current_manifest.json"),
                "currentImpact": str(task / "cache" / "current_impact.json"),
            }
        )
    return result


def _project_fact_read_policy(doc_root: str, version: str, target: str) -> dict[str, Any]:
    if not (doc_root and version and target):
        return {}
    task_dir = Path(doc_root) / "version" / version / "task" / target
    routing_policy = {}
    try:
        routing = context_resolver.load_routing()
        routing_policy = ((routing.get("routingPolicy") or {}).get("projectFactReadPolicy") or {})
    except (OSError, ValueError):
        routing_policy = {}
    runtime_file_policy = {
        "task.json": {
            "authority": "CANONICAL_IDENTITY",
            "readMode": "QUERY_ONLY",
            "query": "wk status --summary or wk context",
            "fullRead": "INITIALIZATION_VALIDATION_AUDIT_ONLY",
        },
        "cache/current_manifest.json": {
            "authority": "DERIVED",
            "readMode": "CONDITIONAL_POINTER",
            "selectors": ["/state", "/activeRevisions", "/blockers", "/traceabilitySummary"],
            "query": "wk status/next/context; task_layout.py view",
            "fullRead": "CACHE_DIAGNOSTICS_ONLY",
        },
        "traceability.json": {
            "authority": "CANONICAL_TRACEABILITY",
            "readMode": "QUERY_ONLY",
            "query": "wk context --trace-id or --acceptance-id",
            "fullRead": "EXPLICIT_AUDIT_OR_FAILURE_DIAGNOSIS_ONLY",
        },
        "acceptance.json": {
            "authority": "CANONICAL_ACCEPTANCE",
            "readMode": "QUERY_ONLY",
            "query": "acceptance.py verify --assertion-id; manual_review.py show --assertion-id",
            "fullRead": "COMPILE_VALIDATE_ARCHIVE_AUDIT_ONLY",
        },
        "evidence/registry.jsonl": {
            "authority": "CANONICAL_EVIDENCE_INDEX",
            "readMode": "QUERY_ONLY",
            "query": "evidence.py query/show",
            "selectors": ["phase", "revision", "type", "scope", "status", "limit", "evidence_id"],
            "fullRead": "FORBIDDEN_BY_DEFAULT",
        },
        "evidence/reviews.jsonl": {
            "authority": "CANONICAL_REVIEW_HISTORY",
            "readMode": "QUERY_ONLY",
            "query": "manual_review.py show --review-result-ref or --assertion-id",
            "selectors": ["review_id", "assertion_id", "review_revision"],
            "fullRead": "EXPLICIT_HISTORY_AUDIT_ONLY",
        },
        "development_task_plan.json": {
            "authority": "CANONICAL_DEVELOPMENT_PLAN",
            "readMode": "QUERY_ONLY",
            "query": "task_plan.py status/query --task-id/--field",
            "selectors": ["task_id", "field", "max_bytes"],
            "fullRead": "EXPLICIT_AUDIT_OR_VALIDATION_ONLY",
        },
    }
    return {
        "defaultReadSet": list(routing_policy.get("default") or ["wk begin"]),
        "conditionalReadSet": list(routing_policy.get("conditional") or ["cache/current_manifest.json"]),
        "indirectReadSet": list(routing_policy.get("indirect") or ["cache/current_impact.json"]),
        "historyRequiresExplicitRead": bool(routing_policy.get("historyRequiresExplicitRead", True)),
        "cacheCompatibilityRule": str(routing_policy.get("cacheCompatibilityRule") or "Reject incompatible cache and rebuild from canonical events."),
        "onDemandOnly": {
            "taskState": "runtime authority; use wk status/context reducer output",
            "traceability": "use traceabilitySummary, then query by Trace/Acceptance ID",
            "taskEvents": "runtime authority; read only through wk status/next/context unless auditing exact events",
            "evidenceRegistry": "QUERY_ONLY via evidence.py query/show",
            "reviewRegistry": "QUERY_ONLY via manual_review.py show",
            "developmentTaskPlan": "QUERY_ONLY via task_plan.py status/query",
        },
        "runtimeFiles": runtime_file_policy,
        "traceQueryCommand": (
            f"python3 scripts/wk.py context -g {{CURRENT_AGENT}} --task-dir {task_dir} "
            "--trace-id {TRACE_ID}"
        ),
        "acceptanceQueryCommand": (
            f"python3 scripts/wk.py context -g {{CURRENT_AGENT}} --task-dir {task_dir} "
            "--acceptance-id {ACCEPTANCE_ID}"
        ),
        "authorityReadRule": "Only wk status/next/context, evidence.py query/show, manual_review.py show, and task_plan.py status/query may read task runtime registries by default; current_manifest.json is conditional and current_impact.json is indirect through wk next.",
        "directReadExceptions": list(routing_policy.get("directReadExceptions") or ["cache/current_manifest.json"]),
    }


def _traceability_context(doc_root: str, version: str, target: str) -> dict[str, Any]:
    if not (doc_root and version and target):
        return {"status": "NOT_REQUESTED"}
    task_dir = Path(doc_root) / "version" / version / "task" / target
    return current_manifest.traceability_summary(task_dir)





def _request_intake_context(doc_root: str, version: str, target: str) -> dict[str, Any]:
    if not (doc_root and version and target):
        return {"status": "NOT_REQUESTED"}
    task_dir = Path(doc_root) / "version" / version / "task" / target
    path = task_dir / "request_intake.json"
    if not path.is_file():
        return {
            "status": "NOT_INITIALIZED",
            "path": str(path),
            "classifyCommand": "python3 scripts/request_intake.py classify -g ProjectManagerAgent -b|-f {INPUT}",
        }
    try:
        document = request_intake.load(path)
    except (OSError, request_intake.RequestIntakeError) as exc:
        return {"status": "INVALID", "path": str(path), "errors": [str(exc)]}
    profile = document.get("workflowProfile")
    recommended = ""
    reason = ""
    if profile == "BLOCKED":
        recommended = "python3 scripts/request_intake.py classify -g ProjectManagerAgent -f {DESCRIPTION} --classification {CLASSIFICATION}"
        reason = "REQUEST_CLASSIFICATION_REQUIRED"
    elif profile == "SIMPLE_OPTIMIZATION_FLOW":
        recommended = f"python3 scripts/long_task.py task-context -g DevelopAgent --task-dir {task_dir} --task-id {{TASK_ID}}"
        reason = "START_SIMPLE_OPTIMIZATION_DEVELOPMENT"
    else:
        recommended = f"python3 scripts/long_task.py task-context -g ProjectManagerAgent --task-dir {task_dir} --task-id {{TASK_ID}}"
        reason = "START_CLASSIFIED_WORKFLOW"
    return {
        "status": "PASSED",
        "path": str(path),
        "digest": sha256_file(path),
        "targetId": document.get("targetId"),
        "classification": document.get("classification"),
        "workflowProfile": profile,
        "entryPhase": document.get("entryPhase"),
        "requiredPhases": document.get("requiredPhases", []),
        "skippedPhases": document.get("skippedPhases", []),
        "relatedFeature": document.get("relatedFeature", ""),
        "relatedRequirement": document.get("relatedRequirement", ""),
        "impactAssessment": document.get("impactAssessment", {}),
        "classificationRevision": document.get("classificationRevision", 1),
        "recommendedCommand": recommended,
        "reasonCode": reason,
    }

def _evidence_context(
    doc_root: str, version: str, target: str, phase: str, *, current_revision: str = ""
) -> dict[str, Any]:
    if not doc_root or not version or not target:
        return {}
    task_dir = Path(doc_root) / "version" / version / "task" / target
    path = evidence.registry_path(task_dir)
    if not path.is_file():
        return {
            "status": "NOT_INITIALIZED",
            "queryCommand": f"python3 scripts/evidence.py query --task-dir {task_dir} --phase {phase or '{PHASE}'} --limit 20",
        }
    try:
        errors, entries = evidence.validate_registry(
            task_dir,
            current_revisions={current_revision} if current_revision else set(),
        )
    except (OSError, ValueError) as exc:
        return {"status": "INVALID", "errors": [str(exc)]}
    active = [item for item in entries.values() if item.get("status") == "ACTIVE"]
    current = [item for item in active if not phase or item.get("phase") == phase]
    review_errors, review_records = review_store.validate_registry(task_dir)
    return {
        "status": "PASSED" if not errors and not review_errors else "INVALID",
        "artifactCount": len(entries),
        "activeArtifactCount": len(active),
        "currentPhaseArtifactCount": len(current),
        "reviewResultCount": len(review_records),
        "errors": errors + review_errors,
        "queryCommand": f"python3 scripts/evidence.py query --task-dir {task_dir} --phase {phase or '{PHASE}'} --limit 20",
        "reviewQueryCommand": f"python3 scripts/manual_review.py show --task-dir {task_dir} --review-result-ref {{REV_ID}}",
        "reviewRegistry": "QUERY_ONLY",
    }



def _task_verification_context(
    doc_root: str, version: str, target: str, phase: str,
    *, level: str = "summary", use_cache: bool = True,
) -> dict[str, Any]:
    if not (doc_root and version and target):
        return {"status": "NOT_REQUESTED"}
    task_dir = Path(doc_root) / "version" / version / "task" / target
    if not (task_dir / "task_events.jsonl").is_file():
        return {
            "status": "NOT_INITIALIZED",
            "taskDir": str(task_dir),
            "verifyCommand": (
                f"python3 scripts/task_verify.py verify --task-dir {task_dir} "
                f"--profile task-health --level {level} --json"
            ),
        }
    profile = "task-health"
    try:
        result, cache_hit = task_verify.verify_task(
            task_dir, profile, use_cache=use_cache, evidence_scope="current"
        )
        payload = task_verify.format_result(result, level, cache_hit=cache_hit)
    except (OSError, ValueError) as exc:
        return {"status": "INVALID", "errors": [str(exc)], "taskDir": str(task_dir)}
    payload["status"] = payload.pop("overall", "INVALID")
    payload["verifyCommand"] = (
        f"python3 scripts/task_verify.py verify --task-dir {task_dir} "
        f"--profile {profile} --level {level} --json"
    )
    return payload


def _task_context_state(task_dir: Path) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Load the reduced task state without mixing it with presentation logic."""
    try:
        state = long_task.runtime_state(task_dir)
    except (OSError, ValueError) as exc:
        return None, {"status": "INVALID", "errors": [str(exc)], "taskDir": str(task_dir)}
    if not isinstance(state, dict) or not state:
        return None, {"status": "INVALID", "errors": ["task event reducer produced no task state"]}
    return state, {}


def _task_semantic_inputs(
    doc_root: str, version: str, target: str, phase_id: str, fact_paths: dict[str, str]
) -> list[dict[str, Any]]:
    semantic_keys_by_phase = {
        "requirement_confirmation": ["requirement", "requirementList"],
        "requirement_analysis": ["requirement", "traceabilitySummary", "dependencyImpact", "businessFlowIndex"],
        "business_model": ["requirement", "traceabilitySummary", "dependencyImpact", "dependencyGraph"],
        "design": ["requirement", "traceabilitySummary", "dependencyImpact", "dependencyGraph"],
        "test_design": ["requirement", "traceabilitySummary", "stageOutcomes"],
        "implementation_plan": ["requirement", "traceabilitySummary", "stageOutcomes", "developmentTaskPlan", "dependencyGraph"],
        "tdd": ["requirement", "traceabilitySummary", "stageOutcomes"],
        "development": ["requirement", "traceabilitySummary", "stageOutcomes", "dependencyGraph"],
        "code_review": ["traceabilitySummary", "stageOutcomes", "evidenceRegistry", "reviewRegistry"],
        "testing": ["requirement", "traceabilitySummary", "stageOutcomes"],
        "completion_verification": ["traceabilitySummary", "stageOutcomes", "evidenceRegistry", "reviewRegistry"],
    }
    task_dir = Path(doc_root) / "version" / version / "task" / target
    query_refs = {
        "stageOutcomes": {"ref": f"python3 scripts/wk.py context -g {{CURRENT_AGENT}} --task-dir {task_dir}"},
        "evidenceRegistry": {"ref": f"python3 scripts/evidence.py query --task-dir {task_dir} --phase {{PHASE}} --limit 20"},
        "reviewRegistry": {"ref": f"python3 scripts/manual_review.py show --task-dir {task_dir} --review-result-ref {{REV_ID}}"},
        "developmentTaskPlan": {"ref": f"python3 scripts/task_plan.py query -g {{CURRENT_AGENT}} --task-dir {task_dir} --max-bytes 24576"},
    }
    semantic_inputs: list[dict[str, Any]] = []
    for key in semantic_keys_by_phase.get(phase_id, ["currentManifest", "taskPlan"]):
        if key == "traceabilitySummary":
            semantic_inputs.append({
                "id": key, "ref": "derived:ai_context#/traceabilitySummary",
                "selector": "/traceabilitySummary", "readMode": "QUERY_RESULT",
            })
        elif key in query_refs:
            semantic_inputs.append({"id": key, **query_refs[key], "readMode": "QUERY_ONLY"})
        elif key in fact_paths:
            read_mode = "QUERY_ONLY" if key in {"stageOutcomes", "evidenceRegistry", "reviewRegistry", "developmentTaskPlan", "taskState", "taskEvents"} else "DIRECT_DOCUMENT"
            if key in {"currentManifest", "currentImpact"}:
                read_mode = "DIRECT_DERIVED_PROJECTION"
            semantic_inputs.append({"id": key, "ref": fact_paths[key], "readMode": read_mode})
    return semantic_inputs


def _task_context_view(
    doc_root: str, version: str, target: str, phase: str, agent: str,
    verification: dict[str, Any], review_profile_ids: list[str],
) -> dict[str, Any]:
    if not (doc_root and version and target):
        return {}
    task_dir = Path(doc_root) / "version" / version / "task" / target
    if not (task_dir / "task_events.jsonl").is_file():
        return {"status": "NOT_INITIALIZED", "taskDir": str(task_dir)}
    state, error_view = _task_context_state(task_dir)
    if state is None:
        return error_view
    artifacts = state.get("artifact_revisions") if isinstance(state.get("artifact_revisions"), dict) else {}
    phase_id = phase or str(state.get("current_phase") or "")
    definition = next(
        (item for item in agent_context.RUNTIME_CONTRACT.get("canonicalPhases", [])
         if isinstance(item, dict) and item.get("id") == phase_id),
        None,
    )
    predecessor_ids = list((definition or {}).get("requiredPredecessors") or [])
    predecessors = [
        {
            "phase": item,
            "revision": str((artifacts.get(item) or {}).get("revision") or ""),
            "status": str((artifacts.get(item) or {}).get("status") or ""),
        }
        for item in predecessor_ids if isinstance(artifacts.get(item), dict)
    ]
    current_artifact = artifacts.get(phase_id) if isinstance(artifacts.get(phase_id), dict) else {}
    fact_paths = _project_fact_paths(doc_root, version, target)
    return {
        "status": "READY" if verification.get("status") == "PASSED" else "BLOCKED",
        "targetId": str(state.get("target_id") or target),
        "version": str(state.get("version") or version),
        "phase": phase_id,
        "selectedAgent": agent,
        "currentRevision": str(current_artifact.get("revision") or ""),
        "currentArtifactStatus": str(current_artifact.get("status") or ""),
        "predecessorRevisions": predecessors,
        "openIssueIds": list(state.get("open_issue_ids") or []),
        "activeTaskIds": list(state.get("active_task_ids") or []),
        "currentAttemptId": str(state.get("current_attempt_id") or ""),
        "nextAction": str(state.get("next_action") or ""),
        "nextAgent": str(state.get("next_agent") or ""),
        "architectureReview": dict(state.get("architecture_review") or {}) if isinstance(state.get("architecture_review"), dict) else {},
        "reviewProfileIds": review_profile_ids,
        "semanticInputs": _task_semantic_inputs(doc_root, version, target, phase_id, fact_paths),
        "lazyObjectPolicy": {
            "loadEvidenceBodiesByDefault": False,
            "loadReviewBodiesByDefault": False,
            "loadSnapshotBodiesByDefault": False,
            "readByIdCommands": {
                "evidence": f"python3 scripts/evidence.py show --task-dir {task_dir} --evidence-id {{EVD_ID}}",
                "review": f"python3 scripts/manual_review.py show --task-dir {task_dir} --review-result-ref {{REV_ID}}",
            },
        },
    }


def _estimate_tokens(value: Any) -> int:
    return max(1, (len(json.dumps(value, ensure_ascii=False, separators=(",", ":"))) + 3) // 4)


def _apply_token_budget(
    result: dict[str, Any], requested_budget: int, *, strategy: str = "best_effort"
) -> dict[str, Any]:
    if strategy not in {"best_effort", "strict"}:
        raise AIContextError(f"unknown context budget strategy: {strategy}")
    if requested_budget <= 0:
        result["contextBudget"] = {
            "requested": 0, "effective": 0, "minimumApplied": False,
            "estimatedTokens": _estimate_tokens(result), "withinBudget": True,
            "strategy": strategy, "status": result.get("contextStatus", "RESOLVED"), "omitted": [],
        }
        return result
    minimum = 500
    budget = max(minimum, requested_budget)
    omitted: list[dict[str, Any]] = []
    minimum_applied = requested_budget < minimum

    def drop(key: str, replacement: Any = None, *, reason: str = "token_budget") -> None:
        if key not in result:
            return
        value = result.get(key)
        if value in (None, {}, []):
            return
        omitted.append({"field": key, "reason": reason})
        if replacement is None:
            result.pop(key, None)
        else:
            result[key] = replacement

    if _estimate_tokens(result) > budget:
        drop("authoritativeSources", {"ref": "ai/source-map.json#/authoritativeSources"})
        drop("executionPolicy", {"ref": "assets/runtime-contract.json#/executionPolicy"})
    if _estimate_tokens(result) > budget:
        flow = result.get("businessFlowContext")
        if isinstance(flow, dict) and len(flow.get("matchedFlows") or []) > 3:
            flow["matchedFlows"] = list(flow["matchedFlows"])[:3]
            omitted.append({"field": "businessFlowContext.matchedFlows", "reason": "token_budget"})
        cross = result.get("crossModuleImplementationContext")
        if isinstance(cross, dict) and len(cross.get("matchedMappings") or []) > 3:
            cross["matchedMappings"] = list(cross["matchedMappings"])[:3]
            omitted.append({"field": "crossModuleImplementationContext.matchedMappings", "reason": "token_budget"})
    if _estimate_tokens(result) > budget:
        agent_contract = result.get("agentContract")
        if isinstance(agent_contract, dict):
            direct_count = len(agent_contract.get("directCommands") or [])
            context_count = len(agent_contract.get("taskContextCommands") or [])
            agent_contract["directCommands"] = {"count": direct_count, "ref": "assets/runtime-contract.json#/scriptPermissions"}
            agent_contract["taskContextCommands"] = {"count": context_count, "ref": "assets/runtime-contract.json#/scriptPermissions"}
            omitted.append({"field": "agentContract.commandLists", "reason": "token_budget"})
        result.pop("allowedCommands", None)
        result.pop("taskContextCommands", None)
    if _estimate_tokens(result) > budget:
        drop("businessFlowContext", {"ref": result.get("projectFactPaths", {}).get("businessFlowIndex", "")})
        drop("crossModuleImplementationContext", {"ref": result.get("projectFactPaths", {}).get("dependencyImpact", "")})
        drop("forbiddenShortcuts", {"ref": "SKILL.md#禁止捷径"})
    if _estimate_tokens(result) > budget:
        verification = result.get("taskVerification")
        if isinstance(verification, dict):
            verification.pop("checks", None)
            verification.pop("counts", None)
            omitted.append({"field": "taskVerification.checks", "reason": "token_budget"})
        evidence_context = result.get("evidenceContext")
        if isinstance(evidence_context, dict):
            errors = evidence_context.get("errors")
            if isinstance(errors, list) and len(errors) > 3:
                evidence_context["errors"] = errors[:3]
                evidence_context["omittedErrorCount"] = len(errors) - 3
    if _estimate_tokens(result) > budget:
        drop("projectFactReadPolicy", {"ref": "context-routing.json#/routingPolicy/projectFactReadPolicy"})
        drop("traceabilitySummary", {"ref": "derived:ai_context#/traceabilitySummary"})
    if _estimate_tokens(result) > budget:
        # In strict mode these fields are the completion/routing contract and must
        # remain visible; an over-budget result is reported as insufficient.
        keep = {
            "schemaVersion", "packageVersion", "selectedAgent", "taskType", "phaseContract",
            "workMode", "capabilityPacks", "requestIntakeContext", "taskVerification", "contextView", "projectFactPaths",
            "executionCore", "completionGateSummary", "resources", "routingLimits", "omittedResources",
            "matchedConditionIds", "omittedConditionIds", "omittedConditionReasons", "contextStatus",
        } if strategy == "strict" else {
            "schemaVersion", "packageVersion", "selectedAgent", "taskType", "phaseContract",
            "workMode", "capabilityPacks", "requestIntakeContext", "taskVerification", "contextView", "projectFactPaths",
        }
        for key in list(result):
            if key not in keep:
                omitted.append({"field": key, "reason": "hard_token_budget"})
                result.pop(key, None)
    if _estimate_tokens(result) > budget and strategy != "strict":
        paths = result.get("projectFactPaths")
        if isinstance(paths, dict):
            result["projectFactPaths"] = {key: paths[key] for key in ("currentManifest", "currentImpact", "taskPlan") if key in paths}
            omitted.append({"field": "projectFactPaths.detail", "reason": "hard_token_budget"})
        result["capabilityPacks"] = {"ref": "assets/capability-pack-contract.json#/packs"}
        omitted.append({"field": "capabilityPacks.detail", "reason": "hard_token_budget"})
    estimated = _estimate_tokens(result)
    insufficient = result.get("contextStatus") == "CONTEXT_INSUFFICIENT" or (strategy == "strict" and estimated > budget)
    if insufficient:
        result["contextStatus"] = "CONTEXT_INSUFFICIENT"
    result["contextBudget"] = {
        "requested": requested_budget,
        "effective": budget,
        "minimumApplied": minimum_applied,
        "estimatedTokens": estimated,
        "withinBudget": estimated <= budget,
        "strategy": strategy,
        "status": "CONTEXT_INSUFFICIENT" if insufficient else "RESOLVED",
        "omitted": omitted,
    }
    return result


def _apply_context_checkpoint(
    result: dict[str, Any], doc_root: str, version: str, target: str,
    phase: str, agent: str, enabled: bool,
) -> dict[str, Any]:
    if not enabled or not (doc_root and version and target):
        return result
    task_dir = Path(doc_root) / "version" / version / "task" / target
    checkpoint = task_dir / "cache" / "context" / (phase or "unscoped") / f"{agent}.json"
    verification_for_digest = dict(result.get("taskVerification") or {})
    verification_for_digest.pop("cacheHit", None)
    stable = {
        "selectedAgent": result.get("selectedAgent"),
        "phaseContract": result.get("phaseContract"),
        "taskVerification": verification_for_digest,
        "contextView": result.get("contextView"),
        "workMode": result.get("workMode"),
        "requestIntakeContext": result.get("requestIntakeContext"),
    }
    digest = hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    previous: dict[str, Any] = {}
    if checkpoint.is_file():
        try:
            loaded = json.loads(checkpoint.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                previous = loaded
        except (OSError, json.JSONDecodeError):
            previous = {}
    result["contextDelta"] = {
        "baseContextDigest": str(previous.get("contextDigest") or ""),
        "contextDigest": digest,
        "unchanged": previous.get("contextDigest") == digest,
        "previousInputDigest": str(previous.get("inputDigest") or ""),
        "currentInputDigest": str((result.get("taskVerification") or {}).get("inputDigest") or ""),
    }
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    checkpoint.write_text(json.dumps({
        "schemaVersion": 1,
        "contextDigest": digest,
        "inputDigest": result["contextDelta"]["currentInputDigest"],
        "phase": phase,
        "agent": agent,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if result["contextDelta"]["unchanged"]:
        result["contextView"] = {
            "status": "UNCHANGED",
            "contextDigest": digest,
            "message": "Reuse the previous context; no authoritative task input changed.",
        }
    return result


def _prompt_identifiers(prompt: str, prefix: str) -> list[str]:
    pattern = rf"\b{re.escape(prefix)}[A-Z0-9_-]+\b"
    return sorted(set(re.findall(pattern, (prompt or "").upper())))


def _business_flow_context(doc_root: str, prompt: str) -> dict[str, Any]:
    if not doc_root:
        return {"indexPath": "", "matchedGroups": [], "matchedFlows": []}
    root = Path(doc_root)
    registry_path = root / "docs" / "_flows" / "flow_index.yaml"
    result: dict[str, Any] = {
        "indexPath": str(registry_path),
        "matchedGroups": [],
        "matchedFlows": [],
    }
    if not registry_path.is_file():
        return result
    try:
        index = simple_yaml.load(registry_path)
    except (OSError, ValueError):
        return result
    if not isinstance(index, dict):
        return result

    prompt_text = (prompt or "").strip()
    prompt_folded = prompt_text.casefold()
    prompt_ids = set(re.findall(r"\b[A-Z][A-Z0-9_-]{2,}\b", prompt_text.upper()))
    groups: dict[str, dict[str, Any]] = {
        str(item.get("code")): item
        for item in index.get("groups", [])
        if isinstance(item, dict) and item.get("code")
    }
    matched: list[tuple[int, dict[str, Any]]] = []
    matched_groups: set[str] = set()
    for item in index.get("flows", []):
        if not isinstance(item, dict):
            continue
        flow_id = str(item.get("id", ""))
        name = str(item.get("name", ""))
        group = str(item.get("group", ""))
        owner = str(item.get("ownerModule", ""))
        participants = [str(value) for value in (item.get("participantModules") or [])]
        feature_refs = [str(value) for value in (item.get("featureRefs") or [])]
        group_name = str(groups.get(group, {}).get("name", ""))

        score = 0
        if flow_id and flow_id.upper() in prompt_ids:
            score += 100
        score += 80 * sum(1 for ref in feature_refs if ref.upper() in prompt_ids)
        if name and name.casefold() in prompt_folded:
            score += 70
        if group_name and group_name.casefold() in prompt_folded:
            score += 50
        if group and group.upper() in prompt_ids:
            score += 40
        if owner and owner.upper() in prompt_ids:
            score += 30
        score += 20 * sum(1 for module in participants if module.upper() in prompt_ids)
        if score:
            matched.append((score, item))
            if group:
                matched_groups.add(group)

    matched.sort(key=lambda pair: (-pair[0], str(pair[1].get("id", ""))))
    result["matchedFlows"] = [item for _, item in matched[:20]]
    result["matchedGroups"] = [
        {
            "code": code,
            "name": groups.get(code, {}).get("name", ""),
            "yamlPath": str(root / "docs" / "_flows" / f"{code}_flow.yaml"),
            "markdownPath": str(root / "docs" / "_flows" / f"{code}_flow.md"),
            "revision": groups.get(code, {}).get("revision", ""),
        }
        for code in sorted(matched_groups)
    ]
    return result


def _cross_module_implementation_context(doc_root: str, prompt: str) -> dict[str, Any]:
    root = Path(doc_root) if doc_root else Path()
    document_path = root / "docs" / "_relations" / "dependency_impact.yaml" if doc_root else Path()
    explicit_mapping_refs = set(_prompt_identifiers(prompt, "CMI-"))
    step_refs = set(_prompt_identifiers(prompt, "CMSTEP-"))
    prompt_text = prompt or ""
    prompt_folded = prompt_text.casefold()
    prompt_ids = set(re.findall(r"\b[A-Z][A-Z0-9_-]{2,}\b", prompt_text.upper()))
    keywords = ("跨模块", "外部系统", "最终一致性", "补偿", "部分成功", "integration")
    matched: list[tuple[int, dict[str, Any]]] = []
    if doc_root and document_path.is_file():
        try:
            document = simple_yaml.load(document_path)
        except (OSError, ValueError):
            document = {}
        if isinstance(document, dict):
            for item in document.get("crossModuleImplementations", []):
                if not isinstance(item, dict):
                    continue
                mapping_id = str(item.get("id", ""))
                name = str(item.get("name", ""))
                business_flow = str(item.get("businessFlowRef", ""))
                business_steps = [str(value) for value in item.get("businessStepRefs", [])]
                implementation_steps = [
                    str(value.get("id", "")) for value in item.get("steps", []) if isinstance(value, dict)
                ]
                modules = [
                    str(value.get("module", "")) for value in item.get("participants", []) if isinstance(value, dict)
                ]
                score = 0
                if mapping_id in explicit_mapping_refs:
                    score += 100
                if any(value in step_refs for value in implementation_steps):
                    score += 90
                if name and name.casefold() in prompt_folded:
                    score += 70
                if business_flow and business_flow.upper() in prompt_ids:
                    score += 60
                score += 40 * sum(1 for value in business_steps if value.upper() in prompt_ids)
                score += 15 * sum(1 for value in modules if value.upper() in prompt_ids)
                if score:
                    matched.append((score, item))
    matched.sort(key=lambda pair: (-pair[0], str(pair[1].get("id", ""))))
    matched_items = [
        {
            "id": str(item.get("id", "")),
            "name": str(item.get("name", "")),
            "businessFlowRef": str(item.get("businessFlowRef", "")),
            "businessStepRefs": [str(value) for value in item.get("businessStepRefs", [])],
            "participantModules": [
                str(value.get("module", "")) for value in item.get("participants", []) if isinstance(value, dict)
            ],
        }
        for _, item in matched[:20]
    ]
    mapping_refs = sorted(explicit_mapping_refs | {item["id"] for item in matched_items if item["id"]})
    required = bool(
        mapping_refs
        or step_refs
        or matched_items
        or any(keyword.casefold() in prompt_folded for keyword in keywords)
    )
    return {
        "required": required,
        "documentPath": str(document_path) if doc_root else "",
        "generatedViewPath": str(root / "docs" / "_relations" / "dependency_graph.md") if doc_root else "",
        "mappingRefs": mapping_refs,
        "stepRefs": sorted(step_refs),
        "matchedMappings": matched_items,
        "sourceField": "crossModuleImplementations",
    }


def _git_checkpoint_context(doc_root: str, version: str, target: str, mode: dict[str, Any] | None) -> dict[str, Any]:
    enabled = bool((mode or {}).get("gitCheckpoint"))
    record_path = ""
    latest: dict[str, Any] | None = None
    if doc_root and version and target:
        path = Path(doc_root) / "version" / version / "task" / target / "git_checkpoints.yaml"
        record_path = str(path)
        if path.is_file():
            try:
                document = simple_yaml.load(path)
            except (OSError, ValueError):
                document = {}
            if isinstance(document, dict):
                checkpoints = document.get("checkpoints")
                if isinstance(checkpoints, list) and checkpoints:
                    item = checkpoints[-1]
                    if isinstance(item, dict):
                        latest = {
                            key: item.get(key)
                            for key in (
                                "checkpoint_id", "phase", "iteration_no", "stage_revision",
                                "stage_outcome_ref", "branch", "commit_title", "created_at",
                                "supersedes_checkpoint_id",
                            )
                        }
    return {
        "enabled": enabled,
        "executionPolicyRef": "assets/runtime-contract.json#/executionPolicy",
        "policyRef": "assets/runtime-contract.json#/commandInterface/workModes/gitCheckpoint/policy",
        "recordPath": record_path,
        "latestCheckpoint": latest,
        "historyLoaded": False,
        "nextAction": (
            "validate_current_phase_then_commit_before_advance"
            if enabled
            else "do_not_require_git_checkpoint"
        ),
    }



def _completion_gate_summary(
    verification: dict[str, Any],
    context_view: dict[str, Any],
    request_context: dict[str, Any],
    architecture_context: dict[str, Any],
) -> dict[str, Any]:
    verification_status = str(verification.get("status") or "NOT_REQUESTED")
    intake_status = str(request_context.get("status") or "NOT_REQUESTED")
    open_issue_ids = list(context_view.get("openIssueIds") or [])
    blocked_reasons: list[str] = []
    if verification_status not in {"PASSED", "NOT_REQUESTED"}:
        blocked_reasons.append("TASK_VERIFICATION_NOT_PASSED")
    if intake_status in {"INVALID", "NOT_INITIALIZED"} or request_context.get("workflowProfile") == "BLOCKED":
        blocked_reasons.append("REQUEST_INTAKE_NOT_READY")
    if open_issue_ids:
        blocked_reasons.append("OPEN_ISSUES")
    if architecture_context.get("enabled") and not architecture_context.get("finalReviewPassed"):
        blocked_reasons.append("ARCHITECTURE_REVIEW_NOT_FINAL")
    return {
        "status": "READY" if not blocked_reasons else "BLOCKED",
        "taskVerificationStatus": verification_status,
        "requestIntakeStatus": intake_status,
        "openIssueIds": open_issue_ids,
        "architectureReviewPassed": (
            bool(architecture_context.get("finalReviewPassed"))
            if architecture_context.get("enabled")
            else True
        ),
        "blockedReasons": blocked_reasons,
        "detailResourceIds": [
            "acceptance-core",
            "verification-integration",
            "long-task-core",
            "review-evidence",
        ],
    }

def resolve_ai_context(
    *,
    agent: str,
    task_type: str,
    prompt: str = "",
    phase: str = "",
    doc_root: str = "",
    version: str = "",
    target: str = "",
    token_budget: int = 0,
    verification_level: str = "summary",
    use_verification_cache: bool = True,
    since_checkpoint: bool = False,
    budget_strategy: str = "best_effort",
) -> dict[str, Any]:
    source_map = _load_source_map()
    mode = context_resolver.resolve_mode(doc_root, version)
    routed = context_resolver.resolve_context(task_type, prompt=prompt, mode=mode, budget_strategy=budget_strategy)
    phase_definition = _phase_for_task(task_type, phase)
    phase_id = str(phase_definition.get("id") if phase_definition else phase or task_type)
    architecture_review_context = _architecture_review_context(
        doc_root, version, target, phase_id, routed.get("mode") or {}
    )
    capability_pack_context = capability_packs.resolve(task_type, phase_id, compact=True)
    direct, contextual = _allowed_commands(agent)

    phase_contract = build_phase_contract(
        phase_definition, architecture_review_context, agent, agent_context.project_manager_agents()
    )
    review_profile_ids = build_review_profile_ids(phase_contract)
    verification = _task_verification_context(
        doc_root, version, target, str(phase_contract.get("id") if phase_contract else phase),
        level=verification_level, use_cache=use_verification_cache,
    )
    context_view = _task_context_view(
        doc_root, version, target, str(phase_contract.get("id") if phase_contract else phase),
        agent, verification, review_profile_ids,
    )
    request_context = _request_intake_context(doc_root, version, target)
    completion_gate_summary = _completion_gate_summary(
        verification, context_view, request_context, architecture_review_context
    )
    result = build_result_payload(
        agent=agent,
        task_type=task_type,
        phase_contract=phase_contract,
        phase_id=phase_id,
        routed=routed,
        capability_pack_context=capability_pack_context,
        architecture_review_context=architecture_review_context,
        request_context=request_context,
        completion_gate_summary=completion_gate_summary,
        direct_commands=direct,
        contextual_commands=contextual,
        project_fact_paths=_project_fact_paths(doc_root, version, target),
        project_fact_read_policy=_project_fact_read_policy(doc_root, version, target),
        traceability_summary=_traceability_context(doc_root, version, target),
        verification=verification,
        context_view=context_view,
        evidence_context=_evidence_context(
            doc_root, version, target, str(phase_contract.get("id") if phase_contract else phase),
            current_revision=str(context_view.get("currentRevision") or "")
            if isinstance(context_view, dict) else "",
        ),
        business_flow_context=_business_flow_context(doc_root, prompt),
        cross_module_context=_cross_module_implementation_context(doc_root, prompt),
        git_checkpoint_context=_git_checkpoint_context(doc_root, version, target, routed.get("mode")),
        source_map=source_map,
        runtime_contract=agent_context.RUNTIME_CONTRACT,
        is_project_manager=agent in agent_context.project_manager_agents(),
        execution_order=EXECUTION_ORDER,
        forbidden_shortcuts=FORBIDDEN_SHORTCUTS,
    )
    result["contextStatus"] = routed.get("status", "RESOLVED")

    result = _apply_context_checkpoint(
        result, doc_root, version, target,
        str(phase_contract.get("id") if phase_contract else phase), agent, since_checkpoint,
    )
    return _apply_token_budget(result, token_budget, strategy=budget_strategy)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    resolve = commands.add_parser("resolve", help="resolve one compact AI execution context")
    agent_context.add_agent_argument(resolve)
    resolve.add_argument("--task-type", required=True)
    resolve.add_argument("--prompt", default="")
    resolve.add_argument("--phase", default="")
    resolve.add_argument("--doc-root", default="")
    resolve.add_argument("--version", default="")
    resolve.add_argument("--target", default="")
    resolve.add_argument("--token-budget", type=int, default=0)
    resolve.add_argument("--budget-strategy", choices=["best_effort", "strict"], default="best_effort")
    resolve.add_argument("--verification-level", choices=task_verify.LEVELS, default="summary")
    resolve.add_argument("--no-verification-cache", action="store_true")
    resolve.add_argument("--since-checkpoint", action="store_true")
    resolve.add_argument("--json", action="store_true")
    return parser


def command_resolve(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "ai_context", "resolve", declared=True)
    result = resolve_ai_context(
        agent=agent,
        task_type=args.task_type,
        prompt=args.prompt,
        phase=args.phase,
        doc_root=args.doc_root,
        version=args.version,
        target=args.target,
        token_budget=args.token_budget,
        verification_level=args.verification_level,
        use_verification_cache=not args.no_verification_cache,
        since_checkpoint=args.since_checkpoint,
        budget_strategy=args.budget_strategy,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"AGENT {result['selectedAgent']}")
        print(f"TASK_TYPE {result['taskType']}")
        if result["phaseContract"]:
            print("PHASE " + json.dumps(result["phaseContract"], ensure_ascii=False, separators=(",", ":")))
        print("MODE " + json.dumps(result["workMode"], ensure_ascii=False, separators=(",", ":")))
        for item in result["resources"] or []:
            print(f"RESOURCE {item['id']} {item['path']} read={item['readStrategy']} budget={item['tokenBudget']}")
            if item.get("sectionIndexCommand"):
                print(f"  INDEX {item['sectionIndexCommand']}")
            print(f"  READ {item['readCommand']}")
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        doc_layout.validate_cli_namespace(args)
        if args.command == "resolve":
            return command_resolve(args)
        return 2
    except (agent_context.AgentAuthorizationError, context_resolver.ContextResolutionError, AIContextError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
