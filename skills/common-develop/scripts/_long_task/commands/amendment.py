"""Bounded, append-only amendment of the current lifecycle phase."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import agent_context
import acceptance
import evidence
import impact_analysis
import storage
from common_utils import now
from error_catalog import LifecycleError
from _long_task import revision_provenance, task_events
from _long_task.contracts import PHASE_DEFINITIONS, PROJECT_MANAGER_AGENT
from _long_task.lifecycle_helpers import new_iteration_id
from _long_task.record_io import transactional_text_write
from _long_task.state_access import (
    append_task_events_payloads,
    load_task_history,
    read_static_task_plan,
    read_task_state_cache,
    task_state_path,
)
from _long_task.commands.queries import emit_json
from _long_task.validation.task import validate_task
from _long_task.amendment_validation import (
    amendment_lock,
    canonical_file_path,
    prepare_canonical_append,
    safe_resolve,
    validate_affected_scope_closure,
    validate_amendment_record,
    validate_amendment_revision,
    validate_bounded_impact_plan,
)


def _unique(values: list[str] | None) -> list[str]:
    return list(dict.fromkeys(str(value).strip() for value in (values or []) if str(value).strip()))


def _validate_impact_plan(
    path: Path,
    *,
    task_dir: Path,
    phase: str,
    base_revision: str,
    revision: str,
    change_class: str,
) -> tuple[dict[str, Any], str]:
    try:
        plan = impact_analysis.load_plan(path)
    except impact_analysis.ImpactAnalysisError as exc:
        raise LifecycleError(f"impact plan is invalid: {exc}") from exc
    provenance = plan.get("provenance") if isinstance(plan.get("provenance"), dict) else {}
    expected = {
        "phase": phase,
        "baseRevision": base_revision,
        "revision": revision,
        "changeClass": change_class,
    }
    actual = {
        "phase": str(plan.get("phase") or ""),
        "baseRevision": str(provenance.get("baseRevision") or ""),
        "revision": str(provenance.get("revision") or ""),
        "changeClass": str(provenance.get("changeClass") or ""),
    }
    mismatches = [key for key in expected if actual[key] != expected[key]]
    try:
        plan_task_dir = safe_resolve(
            Path(str(plan.get("taskDir") or "")), "impact plan taskDir"
        )
    except ValueError as exc:
        raise LifecycleError(f"impact plan taskDir is invalid: {exc}") from exc
    if plan_task_dir != safe_resolve(task_dir, "task directory"):
        mismatches.append("taskDir")
    if mismatches:
        raise LifecycleError(f"impact plan does not match amend-phase inputs: {sorted(set(mismatches))}")
    gate_errors = validate_bounded_impact_plan(plan, change_class=change_class)
    if gate_errors:
        raise LifecycleError("; ".join(gate_errors))
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise LifecycleError(f"cannot read impact plan: {path}: {exc}") from exc
    return plan, digest


def _validate_dependency_closure(tasks: list[dict[str, Any]], affected: set[str], phase: str) -> None:
    dependents: dict[str, set[str]] = {}
    for task in tasks:
        if not isinstance(task, dict) or task.get("phase") != phase:
            continue
        task_id = str(task.get("id") or "")
        for dependency in task.get("depends_on") or []:
            dependents.setdefault(str(dependency), set()).add(task_id)
    queue = list(affected)
    missing: set[str] = set()
    seen = set(affected)
    while queue:
        current = queue.pop(0)
        for dependent in dependents.get(current, set()):
            if dependent in seen:
                continue
            seen.add(dependent)
            missing.add(dependent)
            queue.append(dependent)
    if missing:
        raise LifecycleError(
            f"affected task set is not dependency-closed; also include: {sorted(missing)}"
        )


def command_amend_phase(args: argparse.Namespace) -> int:
    """Open a bounded current-phase iteration while preserving unrelated facts."""
    task_dir = Path(args.task_dir)
    agent = agent_context.authorize_command(args, "long_task", "amend-phase")
    if agent != PROJECT_MANAGER_AGENT:
        raise LifecycleError("amend-phase is restricted to ProjectManagerAgent")
    try:
        with amendment_lock(task_dir):
            return _command_amend_phase_locked(args, agent=agent)
    except LifecycleError:
        raise
    except ValueError as exc:
        raise LifecycleError(str(exc)) from exc


def _command_amend_phase_locked(args: argparse.Namespace, *, agent: str) -> int:
    """Execute amend-phase while the caller holds the task writer lock."""
    task_dir = Path(args.task_dir)

    phase = str(args.phase or "")
    base_revision = str(args.base_revision or "").strip()
    revision = str(args.revision or "").strip()
    change_class = str(args.change_class or "").strip().upper()
    changed_sections = _unique(args.changed_section)
    affected_phases = _unique(args.affected_phase)
    affected_task_ids = _unique(args.affected_task_id)
    affected_trace_ids = _unique(args.affected_trace_id)
    affected_acceptance_ids = _unique(args.affected_acceptance_id)
    preserved_evidence_ids = _unique(args.preserve_evidence_id)
    invalidated_evidence_ids = _unique(args.invalidate_evidence_id)
    reason = str(args.reason or "").strip()
    if not reason:
        raise LifecycleError("amend-phase requires a non-empty reason")
    if not changed_sections or not affected_phases or not affected_task_ids:
        raise LifecycleError(
            "amend-phase requires changed sections, affected phases, and affected task IDs"
        )
    if set(preserved_evidence_ids) & set(invalidated_evidence_ids):
        raise LifecycleError("preserved and invalidated Evidence IDs must be disjoint")
    if change_class not in revision_provenance.CHANGE_CLASSES:
        raise LifecycleError(f"invalid amend-phase change class: {change_class}")
    if change_class == "PROCESS_ONLY":
        raise LifecycleError("PROCESS_ONLY changes do not use amend-phase")
    if change_class == "UNKNOWN":
        raise LifecycleError("UNKNOWN impact requires reopen-phase")

    state_path = task_state_path(task_dir)
    validation_errors, _warnings, _validated_state, _issues = validate_task(task_dir)
    if validation_errors:
        raise LifecycleError(
            "task is invalid before amend-phase: " + "; ".join(validation_errors)
        )
    state_document, state = read_task_state_cache(task_dir, authoritative=True)
    current_phase = str(state.get("current_phase") or "")
    if phase != current_phase:
        raise LifecycleError(
            f"amend-phase only supports the current phase: requested={phase} current={current_phase}"
        )
    if affected_phases != [phase]:
        raise LifecycleError("bounded amendment requires the current phase as its only affected phase")
    artifacts = state.get("artifact_revisions") if isinstance(state.get("artifact_revisions"), dict) else {}
    artifact = artifacts.get(phase) if isinstance(artifacts, dict) else None
    if not isinstance(artifact, dict) or str(artifact.get("revision") or "") != base_revision:
        raise LifecycleError("base revision does not match the current phase artifact")
    if state.get("task_status") == "COMPLETED":
        raise LifecycleError("completed tasks require reopen-phase")

    plan_path = Path(args.impact_plan)
    history = load_task_history(task_dir)
    prior_amendments = [
        item for item in history.get("amendment_events", [])
        if isinstance(item, dict) and item.get("phase") == phase
    ]
    amendment_no = len(prior_amendments) + 1
    try:
        validate_amendment_revision(base_revision, revision, amendment_no)
    except ValueError as exc:
        raise LifecycleError(str(exc)) from exc
    if prior_amendments and prior_amendments[-1].get("revision") != base_revision:
        raise LifecycleError("base revision must equal the previous Amendment revision")
    _plan, impact_digest = _validate_impact_plan(
        plan_path,
        task_dir=task_dir,
        phase=phase,
        base_revision=base_revision,
        revision=revision,
        change_class=change_class,
    )
    _plan_document, static_tasks = read_static_task_plan(task_dir)
    tasks = task_events.materialize_tasks(static_tasks, history)
    attempts = [item for item in history.get("attempts", []) if isinstance(item, dict)]
    outcomes = [item for item in history.get("outcomes", []) if isinstance(item, dict)]
    if any(item.get("status") == "RUNNING" for item in attempts):
        raise LifecycleError("amend-phase is not allowed while any task attempt is RUNNING")

    task_map = {str(item.get("id") or ""): item for item in tasks if isinstance(item, dict)}
    unknown_tasks = sorted(set(affected_task_ids) - set(task_map))
    wrong_phase = sorted(
        task_id for task_id in affected_task_ids
        if task_id in task_map and task_map[task_id].get("phase") != phase
    )
    if unknown_tasks:
        raise LifecycleError(f"unknown affected task IDs: {unknown_tasks}")
    if wrong_phase:
        raise LifecycleError(f"affected tasks are outside the current phase: {wrong_phase}")
    _validate_dependency_closure(tasks, set(affected_task_ids), phase)
    try:
        canonical = canonical_file_path(
            task_dir,
            str(getattr(args, "canonical_file", "")),
            phase=phase,
            tasks=tasks,
            affected_task_ids=affected_task_ids,
        )
    except ValueError as exc:
        raise LifecycleError(str(exc)) from exc
    invalidated = set(invalidated_evidence_ids)
    for task in tasks:
        if not isinstance(task, dict) or task.get("status") not in {"PASSED", "NOT_APPLICABLE"}:
            continue
        task_id = str(task.get("id") or "")
        if task_id in set(affected_task_ids):
            continue
        leaked = sorted(set(task.get("validation_evidence_ids") or []) & invalidated)
        if leaked:
            raise LifecycleError(
                f"invalidated Evidence remains referenced by unaffected task {task_id}: {leaked}"
            )

    scope = _plan.get("affectedScope") if isinstance(_plan.get("affectedScope"), dict) else {}
    planned_tasks = _unique(scope.get("taskIds"))
    planned_traces = _unique(scope.get("traceIds"))
    planned_acceptance = _unique(scope.get("acceptanceIds"))
    if set(affected_task_ids) != set(planned_tasks):
        raise LifecycleError(
            f"affected task IDs must exactly match Impact Plan affectedScope.taskIds: "
            f"planned={sorted(planned_tasks)} supplied={sorted(affected_task_ids)}"
        )

    trace_document = storage.read_json(storage.path(task_dir, "traceability"), default={})
    trace_ids = {
        str(item.get("id") or "") for item in trace_document.get("items", [])
        if isinstance(item, dict) and item.get("id")
    } if isinstance(trace_document, dict) else set()
    unknown_traces = sorted(set(affected_trace_ids) - trace_ids)
    if unknown_traces:
        raise LifecycleError(f"unknown affected trace IDs: {unknown_traces}")
    acceptance_document = acceptance.load_document(task_dir)
    acceptance_ids = {
        str(item.get("acceptance_id") or "")
        for item in acceptance_document.get("assertions", [])
        if isinstance(item, dict) and item.get("acceptance_id")
    }
    for item in trace_document.get("items", []) if isinstance(trace_document, dict) else []:
        if isinstance(item, dict):
            acceptance_ids.update(str(value) for value in item.get("acceptance_criteria") or [] if str(value))
    unknown_acceptance = sorted(set(affected_acceptance_ids) - acceptance_ids)
    if unknown_acceptance:
        raise LifecycleError(f"unknown affected acceptance IDs: {unknown_acceptance}")
    trace_map = {
        str(item.get("id")): item for item in trace_document.get("items", [])
        if isinstance(item, dict) and item.get("id")
    } if isinstance(trace_document, dict) else {}
    closure_errors = validate_affected_scope_closure(
        tasks,
        trace_map.values(),
        affected_task_ids,
        affected_trace_ids,
        affected_acceptance_ids,
    )
    if closure_errors:
        raise LifecycleError("; ".join(closure_errors))
    if set(affected_trace_ids) != set(planned_traces):
        raise LifecycleError(
            f"affected trace IDs must exactly match Impact Plan affectedScope.traceIds: "
            f"planned={sorted(planned_traces)} supplied={sorted(affected_trace_ids)}"
        )
    if set(affected_acceptance_ids) != set(planned_acceptance):
        raise LifecycleError(
            f"affected Acceptance IDs must exactly match Impact Plan affectedScope.acceptanceIds: "
            f"planned={sorted(planned_acceptance)} supplied={sorted(affected_acceptance_ids)}"
        )
    for trace_id in affected_trace_ids:
        trace = trace_map.get(trace_id, {})
        if not set(trace.get("plan_task_ids") or []).intersection(affected_task_ids):
            raise LifecycleError(f"affected trace is not mapped to affected tasks: {trace_id}")
    for trace_id, trace in trace_map.items():
        if trace_id in affected_trace_ids:
            missing_acceptance = set(trace.get("acceptance_criteria") or []) - set(affected_acceptance_ids)
            if missing_acceptance:
                raise LifecycleError(f"affected trace Acceptance IDs are incomplete: {trace_id} -> {sorted(missing_acceptance)}")
    evidence_ids = {
        str(item.get("evidence_id") or "")
        for item in evidence.load_registry(task_dir).get("evidences", [])
        if isinstance(item, dict) and item.get("evidence_id")
    }
    unknown_evidence = sorted(
        (set(preserved_evidence_ids) | set(invalidated_evidence_ids)) - evidence_ids
    )
    if unknown_evidence:
        raise LifecycleError(f"unknown Evidence IDs: {unknown_evidence}")

    phase_order = list(artifacts)
    if phase in phase_order:
        downstream = set(phase_order[phase_order.index(phase) + 1:])
        passed_downstream = sorted({
            str(item.get("phase") or "") for item in outcomes
            if item.get("is_current") is True
            and item.get("status") == "PASSED"
            and item.get("phase") in downstream
        })
        if passed_downstream:
            raise LifecycleError(
                f"downstream PASSED StageOutcome requires reopen-phase: {passed_downstream}"
            )

    timestamp = now()
    target_id = str(state.get("target_id") or task_dir.name)
    old_iteration_id = str(artifact.get("iteration_id") or "")
    new_iteration_no = int(artifact.get("iteration_no") or 0) + 1
    new_id = new_iteration_id(target_id, phase, new_iteration_no)
    artifact.update({
        "iteration_id": new_id,
        "iteration_no": new_iteration_no,
        "revision": revision,
        "status": "IN_PROGRESS",
    })

    review = (state.get("collaboration_reviews") or {}).get(phase)
    if isinstance(review, dict):
        if review.get("artifact_revision") or review.get("independent_conclusions"):
            review.setdefault("review_history", []).append({
                "iteration_id": review.get("current_iteration_id"),
                "artifact_revision": review.get("artifact_revision"),
                "status": review.get("status"),
                "independent_conclusions": copy.deepcopy(review.get("independent_conclusions") or {}),
                "archived_at": timestamp,
            })
        review.update({
            "current_iteration_id": new_id,
            "artifact_revision": "",
            "additional_reviewers": {},
            "independent_conclusions": {},
            "status": "PENDING",
        })

    superseded_outcomes: list[dict[str, Any]] = []
    for outcome in outcomes:
        if outcome.get("phase") == phase and outcome.get("is_current") is True:
            outcome["is_current"] = False
            outcome["superseded_by"] = new_id
            superseded_outcomes.append(copy.deepcopy(outcome))

    runtime_specs: list[dict[str, Any]] = []
    for task_id in affected_task_ids:
        task = task_map[task_id]
        runtime_specs.append({
            "type": "TASK_RUNTIME_SET",
            "entity_id": task_id,
            "timestamp": timestamp,
            "source": "long_task.py amend-phase",
            "payload": {"runtime": {
                "status": "REWORK",
                "attempts": 0,
                "output_revision": "",
                "validation_evidence_ids": [],
                "iteration_id": new_id,
                "iteration_no": new_iteration_no,
                "supersedes_iteration_id": str(task.get("iteration_id") or old_iteration_id),
                "revision_reason": reason,
                "input_revisions": copy.deepcopy(task.get("input_revisions") or {}),
            }},
        })

    amendment_id = f"AM-{target_id}-{phase.upper().replace('_', '-')}-{amendment_no:02d}"
    impact_snapshot = task_dir / "history" / "amendments" / f"{amendment_id}.impact.json"
    if impact_snapshot.exists():
        raise LifecycleError(f"impact plan snapshot already exists: {impact_snapshot}")
    amendment = {
        "amendment_id": amendment_id,
        "phase": phase,
        "base_revision": base_revision,
        "revision": revision,
        "change_class": change_class,
        "changed_sections": changed_sections,
        "affected_phases": affected_phases,
        "affected_task_ids": affected_task_ids,
        "affected_trace_ids": affected_trace_ids,
        "affected_acceptance_ids": affected_acceptance_ids,
        "preserved_evidence_ids": preserved_evidence_ids,
        "invalidated_evidence_ids": invalidated_evidence_ids,
        "impact_plan_digest": impact_digest,
        "impact_plan_path": os.path.relpath(impact_snapshot, task_dir),
        "canonical_file": canonical.relative_to(task_dir.parent.parent.resolve()).as_posix(),
        "amendment_no": amendment_no,
        "canonical_document_digest": "",
        "reason": reason,
        "created_at": timestamp,
        "executed_by_agent": agent,
    }
    try:
        canonical_text, canonical_digest = prepare_canonical_append(canonical, amendment)
    except (OSError, ValueError) as exc:
        raise LifecycleError(f"cannot prepare canonical Amendment append: {exc}") from exc
    base_name = re.sub(r"-AM[0-9]+$", "", base_revision.split("@", 1)[0])
    expected_revision = f"{base_name}-AM{amendment_no:02d}@{canonical_digest}"
    if revision != expected_revision:
        raise LifecycleError(
            "revision digest does not match the canonical document append: "
            f"expected={expected_revision} actual={revision}"
        )
    amendment["canonical_document_digest"] = canonical_digest
    record_errors = validate_amendment_record(
        task_dir,
        amendment,
        tasks=tasks,
        traceability=trace_map.values(),
        acceptance_assertions={
            str(item.get("acceptance_id")): item
            for item in acceptance_document.get("assertions", [])
            if isinstance(item, dict) and item.get("acceptance_id")
        },
        evidence_entries={
            str(item.get("evidence_id")): item
            for item in evidence.load_registry(task_dir).get("evidences", [])
            if isinstance(item, dict) and item.get("evidence_id")
        },
        impact_plan=_plan,
        require_impact_file=False,
        prefix="amend-phase amendment",
    )
    if record_errors:
        raise LifecycleError("invalid Amendment record: " + "; ".join(record_errors))
    for outcome in superseded_outcomes:
        runtime_specs.append({
            "type": "STAGE_OUTCOME_SUPERSEDED",
            "entity_id": str(outcome.get("id") or ""),
            "timestamp": timestamp,
            "source": "long_task.py amend-phase",
            "payload": {"outcome": outcome},
        })
    runtime_specs.append({
        "type": "ARTIFACT_AMENDED",
        "entity_id": amendment_id,
        "timestamp": timestamp,
        "source": "long_task.py amend-phase",
        "payload": {"amendment": amendment},
    })

    state.update({
        "task_status": "REWORK",
        "current_round": f"{phase.upper()}-I{new_iteration_no:03d}",
        "current_agent": agent,
        "next_agent": PHASE_DEFINITIONS[phase].get("ownerAgent", PROJECT_MANAGER_AGENT),
        "next_action": f"execute affected tasks for {phase} amendment {amendment_id}",
        "checkpoint_at": timestamp,
        "active_amendment": amendment,
    })
    event_payloads, projected, _history = append_task_events_payloads(
        task_dir, state_path, state_document, state, runtime_specs, synced_at=timestamp
    )
    transactional_text_write({
        canonical: canonical_text,
        impact_snapshot: plan_path.read_text(encoding="utf-8"),
        **event_payloads,
    })
    emit_json({
        "status": "AMENDED",
        "phase": phase,
        "amendmentId": amendment_id,
        "baseRevision": base_revision,
        "revision": revision,
        "newIterationId": new_id,
        "affectedTaskIds": affected_task_ids,
        "preservedTaskIds": sorted(set(task_map) - set(affected_task_ids)),
        "impactPlanDigest": impact_digest,
        "nextAgent": projected.get("next_agent"),
        "artifactHandling": "CANONICAL_FILE_APPENDED_ATOMICALLY",
        "canonicalFile": str(canonical),
        "canonicalDocumentDigest": canonical_digest,
    })
    return 0
