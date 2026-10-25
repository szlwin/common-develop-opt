"""Audited, single-trace recovery for stale traceability facts."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import acceptance
import agent_context
import evidence
import review_store
import storage
from common_utils import now, sha256_text
from error_catalog import LifecycleError
from _long_task import task_events
from _long_task.contracts import COLLABORATION_REVIEWERS, PHASE_INDEX, PHASES, PROJECT_MANAGER_AGENT
from _long_task.record_io import transactional_text_write
from _long_task.state_access import append_task_events_payloads, runtime_state, task_state_path


def _scope_contains(scopes: Any, value: str) -> bool:
    """Accept canonical IDs and legacy comma-packed scope strings."""
    if not isinstance(scopes, list):
        return False
    for item in scopes:
        if not isinstance(item, str):
            continue
        tokens = {token.strip() for token in re.split(r"[,;]", item) if token.strip()}
        if value == item or value in tokens:
            return True
    return False


def _load_traceability(task_dir: Path) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    path = storage.path(task_dir, "traceability")
    if not path.is_file():
        raise LifecycleError(f"traceability document is missing: {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LifecycleError(f"traceability document is invalid: {path}: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("items"), list):
        raise LifecycleError("traceability document must contain an items array")
    items = [item for item in document["items"] if isinstance(item, dict)]
    if len(items) != len(document["items"]):
        raise LifecycleError("traceability items must be objects")
    return path, document, items


def _require_review(
    task_dir: Path,
    review_id: str,
    *,
    phase: str,
    revision: str,
    evidence_ids: set[str],
) -> tuple[dict[str, Any], str]:
    try:
        record = review_store.require_record(task_dir, review_id)
    except ValueError as exc:
        raise LifecycleError(str(exc)) from exc
    if record.get("result") != "PASSED":
        raise LifecycleError(f"review {review_id} must be PASSED")
    if record.get("review_phase") != phase or record.get("review_revision") != revision:
        raise LifecycleError(f"review {review_id} is not bound to {phase}@{revision}")
    profile_id = str(record.get("profile_id") or "")
    expected_prefix = f"{phase}:"
    if not profile_id.startswith(expected_prefix):
        raise LifecycleError(f"review {review_id} has an invalid profile for {phase}")
    reviewer = profile_id[len(expected_prefix):]
    if reviewer not in COLLABORATION_REVIEWERS.get(phase, set()):
        raise LifecycleError(f"review {review_id} is not an independent required review for {phase}")
    if reviewer == PROJECT_MANAGER_AGENT or record.get("confirmed_by") == PROJECT_MANAGER_AGENT:
        raise LifecycleError(f"review {review_id} is not independent of ProjectManagerAgent")
    selected = set(record.get("selected_evidence_ids") or record.get("evidence_ids") or [])
    if not evidence_ids.issubset(selected):
        raise LifecycleError(f"review {review_id} does not cite every supplied Evidence ID")
    return record, reviewer


def _validate_acceptance_bindings(
    task_dir: Path,
    acceptance_ids: list[str],
    *,
    phase: str,
    revision: str,
    evidence_ids: set[str],
) -> None:
    try:
        document = acceptance.load_document(task_dir)
    except ValueError as exc:
        raise LifecycleError(str(exc)) from exc
    assertions = document.get("assertions") if isinstance(document, dict) else []
    for acceptance_id in acceptance_ids:
        matches = [
            item for item in assertions
            if isinstance(item, dict) and item.get("acceptance_id") == acceptance_id
        ]
        if not matches:
            raise LifecycleError(f"trace references unknown acceptance assertion: {acceptance_id}")
        valid = [
            item for item in matches
            if item.get("status") == "VERIFIED"
            and item.get("phase") == phase
            and item.get("revision") == revision
            and evidence_ids.intersection(set(item.get("evidence_ids") or []))
        ]
        if not valid:
            raise LifecycleError(
                f"acceptance {acceptance_id} lacks VERIFIED assertion bound to {phase}@{revision} "
                "and the supplied Evidence"
            )


def command_revalidate_traceability(args: argparse.Namespace) -> int:
    """Revalidate exactly one STALE/GAP trace using current Evidence and Review."""
    agent = agent_context.authorize_command(args, "long_task", "revalidate-traceability")
    task_dir = Path(args.task_dir).resolve()
    storage.assert_new_project(task_dir)
    state = runtime_state(task_dir)
    phase = str(args.phase or "")
    revision = str(args.revision or "")
    trace_id = str(args.trace_id or "")
    evidence_ids = list(dict.fromkeys(str(item) for item in (args.evidence_id or []) if str(item)))
    if phase not in PHASES:
        raise LifecycleError(f"unknown lifecycle phase: {phase}")
    current_phase = str(state.get("current_phase") or "")
    if current_phase not in PHASE_INDEX or PHASE_INDEX[phase] > PHASE_INDEX[current_phase]:
        raise LifecycleError(f"traceability revalidation phase is not current or upstream: {phase}")
    artifact = (state.get("artifact_revisions") or {}).get(phase)
    if not isinstance(artifact, dict) or artifact.get("revision") != revision:
        raise LifecycleError(f"revision is not the current {phase} artifact revision")
    if not evidence_ids:
        raise LifecycleError("at least one Evidence ID is required")
    trace_path, document, items = _load_traceability(task_dir)
    matches = [item for item in items if item.get("id") == trace_id]
    if len(matches) != 1:
        raise LifecycleError(f"traceability must contain exactly one {trace_id}")
    trace = matches[0]
    from_status = str(trace.get("status") or "")
    if from_status not in {"STALE", "GAP"}:
        raise LifecycleError(f"trace {trace_id} is {from_status}; only STALE/GAP can be revalidated")

    try:
        registry_errors, entries = evidence.validate_registry(
            task_dir,
            current_revisions={revision} if revision else None,
        )
    except ValueError as exc:
        raise LifecycleError(str(exc)) from exc
    if registry_errors:
        raise LifecycleError("Evidence registry is invalid: " + "; ".join(registry_errors))
    for evidence_id in evidence_ids:
        try:
            entry = evidence.require_evidence(
                task_dir, evidence_id, expected_revision=revision, expected_phase=phase,
                validated_entries=entries,
            )
        except ValueError as exc:
            raise LifecycleError(str(exc)) from exc
        if not _scope_contains(entry.get("scope_refs"), trace_id):
            raise LifecycleError(f"Evidence {evidence_id} does not cover trace {trace_id}")

    review, reviewer = _require_review(
        task_dir, str(args.review_result_ref), phase=phase, revision=revision,
        evidence_ids=set(evidence_ids),
    )
    acceptance_ids = [
        str(item) for item in (trace.get("acceptance_criteria") or [])
        if isinstance(item, str) and item
    ]
    if not acceptance_ids:
        raise LifecycleError(f"trace {trace_id} has no acceptance criteria")
    _validate_acceptance_bindings(
        task_dir, acceptance_ids, phase=phase, revision=revision,
        evidence_ids=set(evidence_ids),
    )
    if not trace.get("requirement_refs"):
        raise LifecycleError(f"trace {trace_id} has no requirement references")

    updated = dict(trace)
    updated["status"] = "COVERED"
    updated["verification_evidence_ids"] = list(dict.fromkeys(
        [*(item for item in (trace.get("verification_evidence_ids") or []) if isinstance(item, str)), *evidence_ids]
    ))
    updated["verified_by_agents"] = list(dict.fromkeys(
        [*(item for item in (trace.get("verified_by_agents") or []) if isinstance(item, str)), reviewer]
    ))
    note = str(updated.get("notes") or "").strip()
    recovery_note = (
        f"Trace revalidated by {reviewer} using {', '.join(evidence_ids)} and "
        f"{args.review_result_ref} at {revision}; {args.reason.strip()}"
    )
    updated["notes"] = recovery_note if not note else note + "\n" + recovery_note
    document["items"] = [updated if item is trace else item for item in items]
    updated_document = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    traceability_digest = sha256_text(updated_document)
    timestamp = now()
    record = {
        "trace_id": trace_id,
        "from_status": from_status,
        "to_status": "COVERED",
        "phase": phase,
        "revision": revision,
        "evidence_ids": evidence_ids,
        "review_result_ref": str(args.review_result_ref),
        "acceptance_ids": acceptance_ids,
        "traceability_digest": traceability_digest,
        "reason": str(args.reason).strip(),
        "agent": agent,
        "created_at": timestamp,
    }
    specs = [{
        "type": "TRACEABILITY_REVALIDATED",
        "entity_id": trace_id,
        "timestamp": timestamp,
        "source": "long_task.py revalidate-traceability",
        "payload": {"revalidation": record},
    }]
    payloads, projected, reduced = append_task_events_payloads(
        task_dir, task_state_path(task_dir), json.dumps(state, ensure_ascii=False), state, specs, synced_at=timestamp,
    )
    import current_manifest
    manifest = current_manifest.build_manifest(
        task_dir,
        state=projected,
        issues=[item for item in reduced.get("issues", []) if isinstance(item, dict)],
        traceability_document=updated_document,
    )
    payloads[trace_path] = updated_document
    payloads[current_manifest.manifest_path(task_dir)] = json.dumps(
        manifest, ensure_ascii=False, indent=2
    ) + "\n"
    transactional_text_write(payloads)
    print(json.dumps({
        "status": "REVALIDATED",
        "traceId": trace_id,
        "fromStatus": from_status,
        "toStatus": "COVERED",
        "phase": phase,
        "revision": revision,
        "evidenceIds": evidence_ids,
        "reviewResultRef": review.get("review_id"),
        "traceabilityDigest": traceability_digest,
        "eventType": "TRACEABILITY_REVALIDATED",
    }, ensure_ascii=False, indent=2))
    return 0


__all__ = ["command_revalidate_traceability"]
