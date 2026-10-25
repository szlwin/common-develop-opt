"""Lifecycle step planning for the ``wk step`` preview/apply command.

The planner consumes the existing reducer-backed lifecycle decision. It never
executes lifecycle commands or authorizes a future action; apply selection is a
closed-world projection consumed by the CLI orchestrator. Keeping this
projection separate from :mod:`wk` avoids importing the CLI facade into the
core layer.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from runtime._long_task import task_events
from document_core import storage


PLAN_VERSION = 1

# These are the only read-only actions currently exposed by lifecycle
# decisions.  All other legal actions are deliberately presented as
# potentially mutating, even when their implementation may be idempotent.
_READ_ONLY_ACTIONS = {
    "long_task.validate",
    "task_verify.verify",
    "auto_remediation.classify",
}

# task-context is a query from a lifecycle point of view, but its current
# implementation writes the derived resume_context projection.  It must not be
# mislabeled as read-only in an apply-capable plan.
_NON_CONFIRMING_ACTIONS = set()

# Apply is intentionally a closed world.  The planner may expose other legal
# actions, but the orchestrator can only invoke these existing atomic
# commands with arguments derived from the current reducer decision.
APPLYABLE_ACTIONS = {
    "wk.reconcile",
    "acceptance.ensure-review",
    "long_task.publish-artifact",
    "long_task.finalize-phase",
    "long_task.advance-phase",
}


from runtime._core.canonical_json import canonical_json as _canonical_json


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return "MISSING"
    except OSError:
        return "UNREADABLE"


def _command_name(permission_command_id: str) -> str:
    return permission_command_id.rsplit(".", 1)[-1]


def _action_entry(
    permission_command_id: str,
    *,
    decision: dict[str, Any],
    action_index: int,
) -> dict[str, Any]:
    read_only = permission_command_id in _READ_ONLY_ACTIONS
    requires_confirmation = not read_only and permission_command_id not in _NON_CONFIRMING_ACTIONS
    reason_codes = [str(item) for item in decision.get("reasonCodes", []) if str(item)]
    reason = "；".join(reason_codes) or str(decision.get("preferredAction") or "当前阶段允许的生命周期动作")
    return {
        "index": action_index,
        "command": _command_name(permission_command_id),
        "permissionCommandId": permission_command_id,
        "requiresConfirmation": requires_confirmation,
        "readOnly": read_only,
        "autoApplyAllowed": permission_command_id in APPLYABLE_ACTIONS,
        "selectedForApply": False,
        "reason": reason,
        "requiredAgent": str(decision.get("requiredAgent") or "ProjectManagerAgent"),
    }


def _plan_inputs(task_dir: Path, state: dict[str, Any], decision: dict[str, Any], history: dict[str, Any]) -> dict[str, Any]:
    phase = str(state.get("current_phase") or decision.get("currentPhase") or "")
    artifact = ((state.get("artifact_revisions") or {}).get(phase) or {}) if phase else {}
    events_path = task_events.event_path(task_dir)
    plan_path = storage.path(task_dir, "plan")
    return {
        "schemaVersion": PLAN_VERSION,
        "command": "wk.step",
        "taskDir": str(task_dir.resolve()),
        "currentPhase": phase,
        "currentIterationId": str(artifact.get("iteration_id") or ""),
        "currentRevision": str(artifact.get("revision") or ""),
        "currentRound": str(state.get("current_round") or ""),
        "classification": str(decision.get("classification") or "UNKNOWN"),
        "currentState": str(decision.get("currentState") or "UNKNOWN"),
        "reasonCodes": [str(item) for item in decision.get("reasonCodes", []) if str(item)],
        "lastEventSequence": history.get("last_seq", 0),
        "lastEventDigest": str(history.get("last_digest") or ""),
        # File digests make stale-plan detection fail closed even if an edited
        # ledger cannot be reduced successfully and therefore has no lastDigest.
        "taskEventsDigest": _file_digest(events_path),
        "taskPlanDigest": _file_digest(plan_path),
        "acceptanceDigest": _file_digest(storage.path(task_dir, "acceptance")),
    }


def _apply_sequence(decision: dict[str, Any], legal_actions: list[str]) -> list[str]:
    """Return the explicitly selected, safe sequence for ``--apply``.

    ``legalActions`` is an alternatives list in lifecycle decisions, not an
    instruction to execute every item.  Derive at most one action plus the
    declared after-success transition, and never infer an action from a
    permission identifier supplied by a caller.
    """
    preferred = str(decision.get("preferredAction") or "")
    preferred_map = {
        "ensure-review-assertions": "acceptance.ensure-review",
        "publish-artifact": "long_task.publish-artifact",
        "advance-phase": "long_task.advance-phase",
        "reconcile": "wk.reconcile",
        "close-phase": "long_task.finalize-phase",
    }
    first = preferred_map.get(preferred)
    if not first or first not in legal_actions or first not in APPLYABLE_ACTIONS:
        first = next((item for item in legal_actions if item in APPLYABLE_ACTIONS), "")
    if not first:
        return []
    sequence = [first]
    # Closure decisions explicitly declare the next transition.  It is safe
    # to include it only when it is the exact known lifecycle transition.
    after = decision.get("afterSuccess")
    if first == "long_task.finalize-phase" and isinstance(after, dict):
        if str(after.get("action") or "") == "advance-phase":
            sequence.append("long_task.advance-phase")
    return sequence


def apply_sequence(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return selected plan entries in the exact declared apply order.

    ``steps`` is a presentation list (and may contain alternative legal
    actions), while ``applySteps`` is the executable sequence.  Iterating the
    latter prevents an alternative from changing the order of mutations.
    Missing entries are omitted here and rejected by the orchestrator's
    closed-world check.
    """
    by_action = {
        str(item.get("permissionCommandId") or ""): item
        for item in payload.get("steps") or []
        if isinstance(item, dict) and str(item.get("permissionCommandId") or "")
    }
    return [dict(by_action[action]) for action in (str(item) for item in payload.get("applySteps") or []) if action in by_action]



def build_preview(
    task_dir: Path,
    *,
    runtime: Any,
    validation: tuple | None = None,
    decision: dict[str, Any] | None = None,
    validate: Callable[[Path], tuple] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, non-mutating preview payload.

    ``runtime`` is the late-bound long-task facade.  The optional ``validate``
    hook exists for tests and keeps validation exactly once per command when
    the caller already has a validated tuple.
    """
    task_dir = Path(task_dir)
    if validation is None:
        validation = (validate or runtime.validate_task)(task_dir)
    if decision is None:
        from runtime._long_task import lifecycle_decision

        decision = lifecycle_decision.evaluate(task_dir, runtime, validation=validation)
    errors, warnings, state, _issues = validation
    state = state if isinstance(state, dict) else {}
    try:
        history = runtime.load_task_history(task_dir)
    except (OSError, ValueError):
        history = {"last_seq": 0, "last_digest": ""}

    legal_actions = decision.get("legalActions") if isinstance(decision.get("legalActions"), list) else []
    steps = [
        _action_entry(str(action), decision=decision, action_index=index)
        for index, action in enumerate(legal_actions, start=1)
        if str(action).strip()
    ]
    apply_steps = _apply_sequence(decision, [str(item) for item in legal_actions])
    known_step_ids = {str(item.get("permissionCommandId") or "") for item in steps}
    for action in apply_steps:
        if action not in known_step_ids:
            steps.append(_action_entry(action, decision=decision, action_index=len(steps) + 1))
    selected_ids = set(apply_steps)
    for item in steps:
        item["selectedForApply"] = str(item.get("permissionCommandId") or "") in selected_ids
    inputs = _plan_inputs(task_dir, state, decision, history)
    digest = "sha256:" + hashlib.sha256(
        _canonical_json({**inputs, "steps": steps, "applySteps": apply_steps}).encode("utf-8")
    ).hexdigest()
    phase = str(inputs["currentPhase"])
    payload = {
        "status": "BLOCKED" if errors or decision.get("classification") == "BLOCKED" else "READY",
        "taskDir": str(task_dir.resolve()),
        "currentPhase": phase,
        "currentIterationId": inputs["currentIterationId"],
        "currentRevision": inputs["currentRevision"],
        "classification": decision.get("classification", "UNKNOWN"),
        "currentState": decision.get("currentState", "UNKNOWN"),
        "reasonCodes": list(decision.get("reasonCodes") or []),
        "missing": list(decision.get("missing") or []),
        "blockers": list(decision.get("blockers") or []),
        "requiredAgent": decision.get("requiredAgent", "ProjectManagerAgent"),
        "planVersion": PLAN_VERSION,
        "planDigest": digest,
        "steps": steps,
        "applySteps": apply_steps,
        "readOnly": True,
        "mutationsApplied": False,
        "authorizationRecorded": False,
        "expiresWhen": [
            "task_events.jsonl changes",
            "current phase iteration changes",
            "current artifact revision changes",
            "task_plan.json changes",
        ],
        "warnings": list(warnings or []) + list(decision.get("warnings") or []),
    }
    return payload
