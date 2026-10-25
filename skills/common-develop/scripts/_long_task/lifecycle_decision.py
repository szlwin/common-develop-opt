"""Reducer-driven lifecycle convergence decisions for wk lifecycle commands.

This module deliberately receives the long_task facade as a runtime argument to
avoid adding another authority reader or creating an import cycle.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import reason_codes as rc
import storage


def _current_outcome(history: dict[str, Any], phase: str) -> dict[str, Any] | None:
    for item in reversed(history.get("outcomes") or []):
        if isinstance(item, dict) and item.get("phase") == phase and item.get("is_current") is True:
            return item
    return None


def _trace_gaps(runtime: Any, task_dir: Path) -> list[str]:
    try:
        trace_document = storage.read_json(storage.path(task_dir, "traceability"), default={})
        traces = trace_document.get("items", []) if isinstance(trace_document, dict) else []
    except (OSError, ValueError):
        return ["traceability.json"]
    result: list[str] = []
    for index, item in enumerate(traces if isinstance(traces, list) else []):
        if isinstance(item, dict) and item.get("status") in {"GAP", "STALE"}:
            result.append(str(item.get("id") or f"index-{index}"))
    return result


def _missing_review_assertions(
    runtime: Any,
    task_dir: Path,
    state: dict[str, Any],
    phase: str,
    revision: str,
) -> list[str]:
    review = ((state.get("collaboration_reviews") or {}).get(phase) or {})
    if not isinstance(review, dict):
        return []
    reviewers = runtime.active_phase_reviewers(task_dir, state, phase, review)
    return runtime.acceptance.missing_manual_reviewers(
        task_dir,
        phase=phase,
        revision=revision,
        reviewers=reviewers,
    )


def _review_assertion_registration_required(
    base: dict[str, Any],
    *,
    phase: str,
    revision: str,
    missing_reviewers: list[str],
    candidate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base.update({
        "classification": "ACCEPTANCE_REGISTRATION_REQUIRED",
        "currentState": rc.REVIEW_ASSERTION_MISSING,
        "reasonCodes": [rc.REVIEW_ASSERTION_MISSING],
        "missing": [
            f"MANUAL_REVIEW assertion for {phase}@{revision} reviewer {reviewer}"
            for reviewer in missing_reviewers
        ],
        "preferredAction": "ensure-review-assertions",
        "legalActions": ["acceptance.ensure-review"],
        "requiredAgent": "ProjectManagerAgent",
        "candidate": dict(candidate or {"revision": revision}),
        "mutationRequirement": "TASK_FACTS_ONLY",
    })
    return base


def evaluate(task_dir: Path, runtime: Any, *, validation: tuple | None = None) -> dict[str, Any]:
    """Return one deterministic lifecycle decision without mutating the task."""
    task_dir = Path(task_dir)
    errors, warnings, state, issues = validation or runtime.validate_task(task_dir)
    phase = str(state.get("current_phase") or "") if isinstance(state, dict) else ""
    base: dict[str, Any] = {
        "taskDir": str(task_dir),
        "currentPhase": phase,
        "classification": "UNKNOWN",
        "currentState": "UNKNOWN",
        "reasonCodes": [],
        "satisfied": [],
        "missing": [],
        "blockers": [],
        "legalActions": [],
        "preferredAction": "",
        "requiredAgent": str(state.get("next_agent") or "ProjectManagerAgent") if isinstance(state, dict) else "ProjectManagerAgent",
        "mutationRequirement": "NONE",
        "warnings": list(warnings or []),
    }
    if errors:
        base.update({
            "classification": "BLOCKED",
            "currentState": "INVALID",
            "reasonCodes": [rc.TASK_INVALID],
            "blockers": list(errors),
            "legalActions": ["long_task.validate"],
            "preferredAction": "validate",
        })
        return base

    critical = runtime.blocking_issue_ids_for_phase_revision(
        issues, phase, str(((state.get("artifact_revisions") or {}).get(phase) or {}).get("revision") or "")
    ) if phase else []
    if critical:
        base.update({
            "classification": "BLOCKED", "currentState": "CRITICAL_ISSUE_OPEN",
            "reasonCodes": [rc.OPEN_CRITICAL_ISSUE], "blockers": critical,
            "legalActions": ["long_task.issue-event", "auto_remediation.classify"],
            "preferredAction": "remediate",
        })
        return base

    if state.get("current_attempt_id") or state.get("active_task_ids"):
        base.update({
            "classification": "BLOCKED", "currentState": "ATTEMPT_ACTIVE",
            "reasonCodes": [rc.ACTIVE_ATTEMPT],
            "blockers": list(state.get("active_task_ids") or []) or [str(state.get("current_attempt_id") or "")],
            "legalActions": ["long_task.task-context", "long_task.finish-attempt"],
            "preferredAction": "continue-attempt",
        })
        return base

    artifact = ((state.get("artifact_revisions") or {}).get(phase) or {}) if phase else {}
    revision = str(artifact.get("revision") or "")
    iteration_id = str(artifact.get("iteration_id") or "")
    history = runtime.load_task_history(task_dir)
    tasks = runtime.runtime_tasks(task_dir)
    phase_tasks = [
        item for item in tasks if isinstance(item, dict)
        and item.get("phase") == phase and item.get("iteration_id") == iteration_id
    ]
    incomplete = [
        str(item.get("id") or "") for item in phase_tasks
        if item.get("status") not in {"PASSED", "NOT_APPLICABLE"}
    ]
    if incomplete:
        base.update({
            "classification": "EXECUTION_REQUIRED", "currentState": "PHASE_TASKS_INCOMPLETE",
            "reasonCodes": [rc.PHASE_TASKS_INCOMPLETE], "missing": incomplete,
            "legalActions": ["long_task.task-context", "long_task.start-attempt"],
            "preferredAction": "execute-current-phase",
        })
        return base
    if phase_tasks:
        base["satisfied"].append("all current-iteration phase tasks are complete")

    artifact_status = str(artifact.get("status") or "")
    if artifact_status in {"PASSED", "NOT_APPLICABLE"}:
        outcome = _current_outcome(history, phase)
        expected = "PASSED" if artifact_status == "PASSED" else "SKIPPED"
        if not isinstance(outcome, dict) or outcome.get("status") != expected:
            base.update({
                "classification": "BLOCKED", "currentState": "STAGE_OUTCOME_STALE",
                "reasonCodes": [rc.STAGE_OUTCOME_STALE],
                "blockers": [f"expected current {expected} StageOutcome for {phase}"],
                "preferredAction": "reconcile",
                "legalActions": ["wk.reconcile"],
            })
            return base
        is_final = phase == runtime.PHASES[-1]
        base.update({
            "classification": "COMPLETED" if is_final else "ADVANCE_READY",
            "currentState": "TASK_COMPLETED" if is_final else "PHASE_PASSED",
            "reasonCodes": [rc.TASK_COMPLETED if is_final else rc.ADVANCE_READY],
            "preferredAction": "none" if is_final else "advance-phase",
            "legalActions": [] if is_final else ["long_task.advance-phase"],
            "requiredAgent": "ProjectManagerAgent",
        })
        base["satisfied"].extend(["current artifact passed", "current StageOutcome passed"])
        if not is_final:
            next_index = runtime.PHASES.index(phase) + 1
            base["afterSuccess"] = {"phase": runtime.PHASES[next_index] if next_index < len(runtime.PHASES) else ""}
        return base

    if not revision or artifact_status == "PENDING":
        passed_attempts = [
            item for item in history.get("attempts") or [] if isinstance(item, dict)
            and item.get("phase") == phase and item.get("iteration_id") == iteration_id
            and item.get("status") == "PASSED" and item.get("output_revision")
        ]
        revisions = {str(item.get("output_revision")) for item in passed_attempts}
        latest = passed_attempts[-1] if passed_attempts else None
        if phase_tasks and len(revisions) == 1 and isinstance(latest, dict):
            candidate = str(latest.get("output_revision") or "")
            candidate_context = {"attemptId": latest.get("attempt_id"), "revision": candidate}
            missing_reviewers = _missing_review_assertions(
                runtime, task_dir, state, phase, candidate
            )
            if missing_reviewers:
                return _review_assertion_registration_required(
                    base,
                    phase=phase,
                    revision=candidate,
                    missing_reviewers=missing_reviewers,
                    candidate=candidate_context,
                )
            base.update({
                "classification": "PUBLISH_READY", "currentState": "ARTIFACT_NOT_PUBLISHED",
                "reasonCodes": [rc.ARTIFACT_NOT_PUBLISHED, rc.PUBLISH_READY],
                "missing": ["current phase artifact publication / StageOutcome publication"],
                "preferredAction": "publish-artifact",
                "legalActions": ["long_task.publish-artifact"],
                "requiredAgent": str(runtime.PHASE_DEFINITIONS.get(phase, {}).get("ownerAgent") or base["requiredAgent"]),
                "candidate": candidate_context,
                "mutationRequirement": "TASK_FACTS_ONLY",
            })
            return base
        base.update({
            "classification": "EXECUTION_REQUIRED", "currentState": "ARTIFACT_NOT_READY",
            "reasonCodes": [rc.ARTIFACT_NOT_PUBLISHED],
            "missing": ["unique PASSED attempt / current artifact revision"],
            "preferredAction": "execute-current-phase",
            "legalActions": ["long_task.task-context", "long_task.start-attempt"],
        })
        return base

    if artifact_status != "IN_PROGRESS":
        base.update({
            "classification": "BLOCKED", "currentState": "UNEXPECTED_ARTIFACT_STATE",
            "reasonCodes": [rc.TASK_INVALID], "blockers": [f"artifact status={artifact_status}"],
            "preferredAction": "validate",
            "legalActions": ["long_task.validate"],
        })
        return base

    base["satisfied"].append(f"current artifact published at {revision}")
    review = ((state.get("collaboration_reviews") or {}).get(phase) or {})
    if review:
        missing_reviewers = _missing_review_assertions(runtime, task_dir, state, phase, revision)
        if missing_reviewers:
            return _review_assertion_registration_required(
                base,
                phase=phase,
                revision=revision,
                missing_reviewers=missing_reviewers,
            )
        if review.get("artifact_revision") != revision:
            base.update({
                "classification": "REVIEW_REQUIRED", "currentState": "REVIEW_STALE",
                "reasonCodes": [rc.REVIEW_STALE],
                "missing": [f"Review bound to {revision}"],
                "preferredAction": "review-current-revision",
                "legalActions": ["manual_review.prepare", "manual_review.submit"],
            })
            return base
        if review.get("status") != "PASSED":
            base.update({
                "classification": "REVIEW_REQUIRED", "currentState": "REVIEW_PENDING",
                "reasonCodes": [rc.REVIEW_REQUIRED],
                "missing": [f"required collaboration Review PASSED for {revision}"],
                "preferredAction": "review-current-revision",
                "legalActions": ["manual_review.prepare", "manual_review.submit"],
            })
            return base
        base["satisfied"].append("required collaboration Review passed for current exact revision")

    gaps = _trace_gaps(runtime, task_dir)
    if gaps:
        base.update({
            "classification": "VALIDATION_REQUIRED", "currentState": "TRACEABILITY_GAP",
            "reasonCodes": [rc.TRACE_GAP], "missing": gaps,
            "preferredAction": "repair-traceability",
            "legalActions": ["task_verify.verify"],
        })
        return base

    outcome = _current_outcome(history, phase)
    if not isinstance(outcome, dict):
        base.update({
            "classification": "RECONCILIATION_REQUIRED", "currentState": "STAGE_OUTCOME_MISSING",
            "reasonCodes": [rc.STAGE_OUTCOME_MISSING],
            "missing": [f"current RUNNING StageOutcome for {phase}@{revision}"],
            "preferredAction": "reconcile",
            "legalActions": ["wk.reconcile"],
            "mutationRequirement": "TASK_FACTS_ONLY",
        })
        return base
    if outcome.get("status") != "RUNNING" or outcome.get("iteration_id") != iteration_id or outcome.get("output_revision") != revision:
        base.update({
            "classification": "RECONCILIATION_REQUIRED", "currentState": "STAGE_OUTCOME_STALE",
            "reasonCodes": [rc.STAGE_OUTCOME_STALE],
            "missing": [f"current RUNNING StageOutcome aligned to {revision}"],
            "preferredAction": "reconcile",
            "legalActions": ["wk.reconcile"],
            "mutationRequirement": "TASK_FACTS_ONLY",
        })
        return base
    if not outcome.get("evidence_ids"):
        base.update({
            "classification": "VALIDATION_REQUIRED", "currentState": "STAGE_OUTCOME_EVIDENCE_MISSING",
            "reasonCodes": [rc.STAGE_OUTCOME_EVIDENCE_MISSING],
            "missing": ["StageOutcome evidence IDs"],
            "preferredAction": "register-evidence",
            "legalActions": ["evidence.register"],
        })
        return base

    base["satisfied"].extend(["current RUNNING StageOutcome aligned", "StageOutcome evidence registered", "no open P0/P1"])
    base.update({
        "classification": "CLOSURE_ONLY", "currentState": "CLOSURE_READY",
        "reasonCodes": [rc.CLOSURE_READY],
        "preferredAction": "close-phase",
        "legalActions": ["wk.close", "long_task.finalize-phase"],
        "requiredAgent": "ProjectManagerAgent",
        "mutationRequirement": "TASK_FACTS_ONLY",
        "afterSuccess": {"action": "advance-phase"},
    })
    return base
