"""Bounded authorization batches for lifecycle phase transitions.

Authorization events are advisory permissions, not a second state machine.  A
batch can only be consumed by the normal lifecycle command after that command's
ordinary gates have passed.  The event ledger remains the only authority.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from _long_task import task_events
from _long_task.phase_policy import phase_skip_allowed


class AuthorizationError(ValueError):
    """Raised when a batch authorization is malformed or stale."""


def reopen_boundary_fingerprint(payload: dict[str, Any]) -> str:
    """Digest the immutable fields of one reopen-boundary authorization."""
    fields = {
        "taskDir": str(payload.get("taskDir") or ""),
        "taskId": str(payload.get("taskId") or ""),
        "changedPhase": str(payload.get("changedPhase") or ""),
        "fromPhase": str(payload.get("fromPhase") or ""),
        "sourceRevision": str(payload.get("sourceRevision") or ""),
        "changedIterationId": str(payload.get("changedIterationId") or ""),
        "changedRevision": str(payload.get("changedRevision") or ""),
        "reason": str(payload.get("reason") or ""),
        "agent": str(payload.get("agent") or ""),
        "mode": "reopen",
    }
    encoded = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def standard_skip_fingerprint(payload: dict[str, Any]) -> str:
    fields = {
        "taskDir": str(payload.get("taskDir") or ""),
        "taskId": str(payload.get("taskId") or ""),
        "phase": str(payload.get("phase") or ""),
        "iterationId": str(payload.get("iterationId") or ""),
        "iterationNo": payload.get("iterationNo"),
        "revision": str(payload.get("revision") or ""),
        "reason": str(payload.get("reason") or ""),
        "agent": str(payload.get("agent") or ""),
        "mode": "standard-skip",
    }
    encoded = json.dumps(fields, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def find_valid_standard_skip(
    task_dir: Path,
    state: dict[str, Any],
    *,
    phase: str,
    reason: str,
    confirmation_ref: str,
) -> dict[str, Any] | None:
    """Resolve an unconsumed, exact user confirmation for a standard skip."""
    events = task_events.read_event_history(task_dir)
    artifact = ((state.get("artifact_revisions") or {}).get(phase) or {})
    if not isinstance(artifact, dict):
        return None
    expected = {
        "taskDir": str(Path(task_dir).resolve()),
        "taskId": str(state.get("target_id") or ""),
        "phase": phase,
        "iterationId": str(artifact.get("iteration_id") or ""),
        "iterationNo": artifact.get("iteration_no"),
        "revision": str(artifact.get("revision") or ""),
        "reason": reason,
        "agent": "ProjectManagerAgent",
        "mode": "standard-skip",
    }
    consumed = {
        str((event.get("payload") or {}).get("requestFingerprint") or "")
        for event in events
        if event.get("type") == "AUTHORIZATION_BATCH_CONSUMED"
        and isinstance(event.get("payload"), dict)
        and (event.get("payload") or {}).get("purpose") == "STANDARD_PHASE_SKIP"
    }
    candidates = []
    for event in events:
        if event.get("type") != "AUTHORIZATION_BATCH_CONFIRMED":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        fingerprint = str(payload.get("requestFingerprint") or "")
        if payload.get("mode") != "standard-skip" or fingerprint != confirmation_ref or fingerprint in consumed:
            continue
        if event.get("source") != "wk.confirm" or payload.get("agent") != "ProjectManagerAgent":
            continue
        if payload.get("atomicActions") != [{"phase": phase, "action": "SKIP"}]:
            continue
        if any(payload.get(key) != value for key, value in expected.items() if key == "iterationNo"):
            continue
        if any(str(payload.get(key) or "") != value for key, value in expected.items() if key != "iterationNo"):
            continue
        if fingerprint != standard_skip_fingerprint(payload):
            continue
        candidates.append({"event": event, "payload": payload})
    return max(candidates, key=lambda item: int(item["event"].get("seq") or 0)) if candidates else None


def standard_skip_consumption_spec(record: dict[str, Any], *, timestamp: str) -> dict[str, Any]:
    event = record["event"]
    payload = record["payload"]
    return {
        "type": "AUTHORIZATION_BATCH_CONSUMED",
        "entity_id": str(payload.get("requestFingerprint") or ""),
        "timestamp": timestamp,
        "source": "long_task.py skip-phase",
        "payload": {
            "purpose": "STANDARD_PHASE_SKIP",
            "requestFingerprint": payload.get("requestFingerprint"),
            "confirmationEventId": event.get("event_id"),
            "confirmationEventDigest": event.get("digest"),
            "taskId": payload.get("taskId"),
            "phase": payload.get("phase"),
            "iterationId": payload.get("iterationId"),
            "iterationNo": payload.get("iterationNo"),
            "sourceRevision": payload.get("revision"),
        },
    }


def find_valid_reopen_boundary(
    task_dir: Path,
    state: dict[str, Any],
    *,
    changed_phase: str,
    from_phase: str,
    source_revision: str,
    reason: str,
    confirmation_ref: str,
    phases: list[str],
) -> dict[str, Any] | None:
    """Resolve an unconsumed, exact authorization event for an upstream reopen."""
    if _phase_index(from_phase, phases) >= _phase_index(changed_phase, phases):
        return None
    events = task_events.read_event_history(task_dir)
    artifacts = state.get("artifact_revisions") if isinstance(state.get("artifact_revisions"), dict) else {}
    changed = artifacts.get(changed_phase) if isinstance(artifacts, dict) else {}
    changed = changed if isinstance(changed, dict) else {}
    expected = {
        "taskDir": str(Path(task_dir).resolve()),
        "taskId": str(state.get("target_id") or ""),
        "changedPhase": changed_phase,
        "fromPhase": from_phase,
        "sourceRevision": source_revision,
        "changedIterationId": str(changed.get("iteration_id") or ""),
        "changedRevision": str(changed.get("revision") or ""),
        "reason": reason,
        "agent": str(state.get("project_manager_agent") or "ProjectManagerAgent"),
        "mode": "reopen",
    }
    consumed = {
        str((event.get("payload") or {}).get("requestFingerprint") or "")
        for event in events
        if event.get("type") == "AUTHORIZATION_BATCH_CONSUMED"
        and isinstance(event.get("payload"), dict)
        and (event.get("payload") or {}).get("purpose") == "REOPEN_BOUNDARY"
    }
    candidates: list[dict[str, Any]] = []
    for event in events:
        if event.get("type") != "AUTHORIZATION_BATCH_CONFIRMED":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        fingerprint = str(payload.get("requestFingerprint") or "")
        if payload.get("mode") != "reopen" or fingerprint != confirmation_ref or fingerprint in consumed:
            continue
        if event.get("source") != "wk.confirm" or payload.get("agent") != "ProjectManagerAgent":
            continue
        if payload.get("atomicActions") != [{"fromPhase": from_phase, "changedPhase": changed_phase}]:
            continue
        if any(str(payload.get(key) or "") != value for key, value in expected.items()):
            continue
        if fingerprint != reopen_boundary_fingerprint(payload):
            continue
        # Any intervening stale event invalidates the authorization's bound runtime context.
        if any(
            later.get("type") == "PHASE_STALE" and int(later.get("seq") or 0) > int(event.get("seq") or 0)
            for later in events
        ):
            continue
        candidates.append({"event": event, "payload": payload})
    return max(candidates, key=lambda item: int(item["event"].get("seq") or 0)) if candidates else None


def reopen_boundary_consumption_spec(
    authorization_record: dict[str, Any], *, timestamp: str
) -> dict[str, Any]:
    event = authorization_record["event"]
    payload = authorization_record["payload"]
    return {
        "type": "AUTHORIZATION_BATCH_CONSUMED",
        "entity_id": str(payload.get("requestFingerprint") or ""),
        "timestamp": timestamp,
        "source": "long_task.py reopen-phase",
        "payload": {
            "purpose": "REOPEN_BOUNDARY",
            "requestFingerprint": payload.get("requestFingerprint"),
            "confirmationEventId": event.get("event_id"),
            "confirmationEventDigest": event.get("digest"),
            "taskId": payload.get("taskId"),
            "changedPhase": payload.get("changedPhase"),
            "fromPhase": payload.get("fromPhase"),
            "sourceRevision": payload.get("sourceRevision"),
        },
    }


def validate_reopen_boundary_audit(
    task_dir: Path,
    stale: dict[str, Any],
    phases: list[str],
    *,
    prefix: str,
    errors: list[str],
) -> None:
    """Verify a stale record against its prior confirmation and consumption events."""
    from_phase = str(stale.get("from_phase") or "")
    changed_phase = str(stale.get("changed_phase") or "")
    if from_phase not in phases or changed_phase not in phases:
        return
    if _phase_index(from_phase, phases) >= _phase_index(changed_phase, phases):
        errors.append(f"{prefix} confirmed reopen boundary must be upstream of changed_phase")
        return
    try:
        events = task_events.read_event_history(task_dir)
    except (OSError, ValueError) as exc:
        errors.append(f"{prefix} cannot read boundary authorization history: {exc}")
        return
    confirmation_ref = str(stale.get("confirmation_ref") or "")
    event_id = str(stale.get("confirmation_event_id") or "")
    event_digest = str(stale.get("confirmation_event_digest") or "")
    confirmation = next((
        event for event in events
        if event.get("type") == "AUTHORIZATION_BATCH_CONFIRMED"
        and event.get("event_id") == event_id
        and event.get("digest") == event_digest
        and isinstance(event.get("payload"), dict)
        and event.get("payload", {}).get("requestFingerprint") == confirmation_ref
    ), None)
    if not isinstance(confirmation, dict):
        errors.append(f"{prefix} boundary confirmation_ref does not resolve to an authorization event")
        return
    payload = confirmation["payload"]
    if confirmation.get("source") != "wk.confirm":
        errors.append(f"{prefix} boundary confirmation source must be wk.confirm")
    if payload.get("agent") != "ProjectManagerAgent":
        errors.append(f"{prefix} boundary confirmation agent must be ProjectManagerAgent")
    if payload.get("atomicActions") != [{"fromPhase": from_phase, "changedPhase": changed_phase}]:
        errors.append(f"{prefix} boundary confirmation atomicActions do not match the reopen boundary")
    expected = {
        "taskDir": str(Path(task_dir).resolve()),
        "taskId": str(task_dir.name),
        "changedPhase": changed_phase,
        "fromPhase": from_phase,
        "sourceRevision": str(stale.get("source_revision") or ""),
        "reason": str(stale.get("reason") or ""),
        "mode": "reopen",
    }
    if any(str(payload.get(key) or "") != value for key, value in expected.items()):
        errors.append(f"{prefix} boundary authorization fields do not match the stale event")
    if confirmation_ref != reopen_boundary_fingerprint(payload):
        errors.append(f"{prefix} boundary authorization fingerprint is invalid")
    if str(stale.get("confirmation_fingerprint") or "") != confirmation_ref:
        errors.append(f"{prefix} stale confirmation_fingerprint does not match confirmation_ref")
    if str(stale.get("confirmed_at") or "") != str(confirmation.get("timestamp") or ""):
        errors.append(f"{prefix} stale confirmed_at does not match the confirmation event")
    stale_event = next((
        event for event in events
        if event.get("type") == "PHASE_STALE"
        and isinstance(event.get("payload"), dict)
        and (event.get("payload", {}).get("stale_event") or {}).get("confirmation_ref") == confirmation_ref
        and (event.get("payload", {}).get("stale_event") or {}).get("created_at") == stale.get("created_at")
    ), None)
    if not isinstance(stale_event, dict) or int(confirmation.get("seq") or 0) >= int(stale_event.get("seq") or 0):
        errors.append(f"{prefix} boundary confirmation must exist before reopen")
        return
    consumptions = [
        event for event in events
        if event.get("type") == "AUTHORIZATION_BATCH_CONSUMED"
        and isinstance(event.get("payload"), dict)
        and event.get("payload", {}).get("purpose") == "REOPEN_BOUNDARY"
        and event.get("payload", {}).get("requestFingerprint") == confirmation_ref
        and int(confirmation.get("seq") or 0) < int(event.get("seq") or 0) < int(stale_event.get("seq") or 0)
    ]
    if len(consumptions) != 1:
        errors.append(f"{prefix} boundary authorization was not atomically consumed by reopen")
        return
    consumption = consumptions[0]
    consumption_payload = consumption["payload"]
    if consumption.get("source") != "long_task.py reopen-phase":
        errors.append(f"{prefix} boundary authorization consumption source must be long_task.py reopen-phase")
    if int(consumption.get("seq") or 0) + 1 != int(stale_event.get("seq") or 0):
        errors.append(f"{prefix} boundary authorization consumption must immediately precede PHASE_STALE")
    expected_consumption = {
        "purpose": "REOPEN_BOUNDARY",
        "requestFingerprint": confirmation_ref,
        "confirmationEventId": str(confirmation.get("event_id") or ""),
        "confirmationEventDigest": str(confirmation.get("digest") or ""),
        "taskId": str(payload.get("taskId") or ""),
        "changedPhase": changed_phase,
        "fromPhase": from_phase,
        "sourceRevision": str(stale.get("source_revision") or ""),
    }
    mismatched = [
        key for key, value in expected_consumption.items()
        if str(consumption_payload.get(key) or "") != value
    ]
    if mismatched:
        errors.append(
            f"{prefix} boundary authorization consumption fields do not match confirmation/stale event: {mismatched}"
        )


def phase_context(state: dict[str, Any], phase: str) -> dict[str, str]:
    artifacts = state.get("artifact_revisions") if isinstance(state.get("artifact_revisions"), dict) else {}
    artifact = artifacts.get(phase) if isinstance(artifacts, dict) else {}
    artifact = artifact if isinstance(artifact, dict) else {}
    return {
        "taskId": str(state.get("target_id") or ""),
        "iterationId": str(artifact.get("iteration_id") or ""),
        "revision": str(artifact.get("revision") or ""),
    }


def _phase_index(phase: str, phases: list[str]) -> int:
    try:
        return phases.index(phase)
    except ValueError as exc:
        raise AuthorizationError(f"unknown lifecycle phase: {phase}") from exc


def _invalidated_after(events: list[dict[str, Any]], seq: int, phase_range: set[str]) -> bool:
    for event in events:
        if int(event.get("seq") or 0) <= seq or event.get("type") != "PHASE_STALE":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        stale = payload.get("stale_event") if isinstance(payload.get("stale_event"), dict) else payload
        invalidated = stale.get("invalidated_phases") if isinstance(stale, dict) else []
        if phase_range.intersection(str(item) for item in invalidated if isinstance(item, str)):
            return True
    return False


def find_valid_batch(
    task_dir: Path,
    state: dict[str, Any],
    current_phase: str,
    phases: list[str],
) -> dict[str, Any] | None:
    """Return the newest still-valid batch that covers ``current_phase``.

    The first transition is pinned to the phase iteration and artifact revision
    captured at confirmation time.  Later transitions form a chain through
    ``AUTHORIZATION_BATCH_CONSUMED`` events, so a reopen or stale invalidation
    cannot silently reuse an old authorization.
    """
    events = task_events.read_events(task_dir)
    reduced = task_events.reduce_events(events)
    confirmations = [item for item in reduced.get("authorization_events", []) if isinstance(item, dict)]
    consumptions = [item for item in reduced.get("authorization_consumptions", []) if isinstance(item, dict)]
    current_index = _phase_index(current_phase, phases)
    context = phase_context(state, current_phase)
    task_id = context["taskId"]
    candidates: list[dict[str, Any]] = []
    for wrapper in confirmations:
        payload = wrapper.get("payload") if isinstance(wrapper.get("payload"), dict) else {}
        if payload.get("mode") != "apply":
            continue
        # Authorization batches are pinned to a concrete runtime context.  Do
        # not reuse pre-binding events: keeping them readable preserves audit
        # history, while requiring all keys here makes legacy events stale.
        if not {"taskId", "iterationId", "revision"}.issubset(payload):
            continue
        if str(payload.get("taskId") or "") != task_id:
            continue
        if str(payload.get("taskDir") or "") != str(Path(task_dir).resolve()):
            continue
        start = str(payload.get("fromPhase") or "")
        target = str(payload.get("untilPhase") or "")
        try:
            start_index = _phase_index(start, phases)
            target_index = _phase_index(target, phases)
        except AuthorizationError:
            continue
        if not start_index <= current_index <= target_index:
            continue
        seq = int(wrapper.get("seq") or 0)
        phase_range = set(phases[start_index:target_index + 1])
        if _invalidated_after(events, seq, phase_range):
            continue
        fingerprint = str(payload.get("requestFingerprint") or "")
        if not fingerprint:
            continue
        prior = [
            item for item in consumptions
            if isinstance(item.get("payload"), dict)
            and item.get("payload", {}).get("requestFingerprint") == fingerprint
        ]
        consumed_from = {str(item.get("payload", {}).get("fromPhase") or "") for item in prior}
        if current_index == start_index:
            if str(payload.get("iterationId") or "") != context["iterationId"]:
                continue
            if str(payload.get("revision") or "") != context["revision"]:
                continue
            if start in consumed_from:
                continue
        else:
            previous_phase = next(
                (phases[index] for index in range(current_index - 1, -1, -1)
                 if not phase_skip_allowed(
                     phases[index],
                     bool((state.get("work_mode") or {}).get("minimal")),
                     state,
                 )),
                None,
            )
            if previous_phase is None or not any(
                str(item.get("payload", {}).get("fromPhase") or "") == previous_phase
                for item in prior
            ):
                continue
            if current_phase in consumed_from:
                continue
        candidates.append({
            "fingerprint": fingerprint,
            "fromPhase": start,
            "untilPhase": target,
            "confirmedSeq": seq,
            "reason": str(payload.get("reason") or ""),
            "taskId": task_id,
            "iterationId": str(payload.get("iterationId") or ""),
            "revision": str(payload.get("revision") or ""),
        })
    return max(candidates, key=lambda item: item["confirmedSeq"]) if candidates else None


def consumption_spec(batch: dict[str, Any], *, from_phase: str, to_phase: str, state: dict[str, Any], timestamp: str) -> dict[str, Any]:
    context = phase_context(state, from_phase)
    return {
        "type": "AUTHORIZATION_BATCH_CONSUMED",
        "entity_id": batch["fingerprint"],
        "timestamp": timestamp,
        "source": "long_task.advance-phase",
        "payload": {
            "requestFingerprint": batch["fingerprint"],
            "taskId": batch["taskId"],
            "fromPhase": from_phase,
            "toPhase": to_phase,
            "iterationId": context["iterationId"],
            "revision": context["revision"],
            "confirmedSeq": batch["confirmedSeq"],
            "reason": batch["reason"],
        },
    }
