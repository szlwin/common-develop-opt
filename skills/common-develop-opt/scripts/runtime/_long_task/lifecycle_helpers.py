"""Small, domain-focused lifecycle helpers shared by lifecycle commands."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from document_core.common_utils import canonical_json, sha256_text
from runtime import work_record
from runtime._long_task.record_io import read_work_events, work_record_path_for_task


def new_iteration_id(target_id: str, phase: str, iteration_no: int) -> str:
    return f"ITER-{target_id}-{phase.upper().replace('_', '-')}-{iteration_no:03d}"


def reopen_phase_event_specs(
    task_runtime_specs: list[dict[str, Any]],
    superseded_outcomes: list[dict[str, Any]],
    new_outcomes: list[dict[str, Any]],
    cancelled: list[dict[str, Any]],
    *,
    from_phase: str,
    source_revision: str,
    invalidated_phases: list[str],
    reason: str,
    agent: str,
    iteration_id: str,
    timestamp: str,
    boundary_confirmation: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Build the append-only events for a phase reopen."""
    source = "long_task.py reopen-phase"
    event_specs: list[dict[str, Any]] = list(task_runtime_specs)
    for event_type, records, payload_key, id_key in (
        ("STAGE_OUTCOME_SUPERSEDED", superseded_outcomes, "outcome", "id"),
        ("STAGE_OUTCOME_RECORDED", new_outcomes, "outcome", "id"),
        ("ATTEMPT_CANCELLED", cancelled, "attempt", "attempt_id"),
    ):
        for record in records:
            event_specs.append({
                "type": event_type,
                "entity_id": str(record.get(id_key) or ""),
                "timestamp": timestamp,
                "source": source,
                "payload": {payload_key: record},
            })
    event_specs.append({
        "type": "PHASE_STALE",
        "entity_id": f"{from_phase}:{iteration_id}",
        "timestamp": timestamp,
        "source": source,
        "payload": {"stale_event": {
            "from_phase": from_phase,
            "source_revision": source_revision,
            "invalidated_phases": invalidated_phases,
            "reason": reason,
            "created_at": timestamp,
            "executed_by_agent": agent,
            **(boundary_confirmation or {}),
        }},
    })
    return event_specs


def add_cancelled_attempt_work_payload(
    payloads: dict[Path, str],
    task_dir: Path,
    state: dict[str, Any],
    cancelled: list[dict[str, Any]],
    *,
    agent: str,
    timestamp: str,
) -> None:
    """Add cancelled attempt entries to the version work-record payload."""
    if not cancelled:
        return
    work_path = work_record_path_for_task(task_dir)
    version = str(state.get("version") or work_path.parent.name)
    work_events = read_work_events(work_path)[1] if work_path.exists() else []
    for attempt in cancelled:
        identity = 'CANCELLED-' + sha256_text(canonical_json([task_dir.name, attempt.get('attempt_id')]))
        work_events.append({
            "event_id": f"EVENT-{identity}",
            "record_id": f"WR-{work_record.compact_timestamp(timestamp)}-{identity}",
            "timestamp": timestamp,
            "event_type": "TASK_ATTEMPT_CANCELLED",
            "status": "CANCELLED",
            "agent": agent,
            "target_id": task_dir.name,
            "task_id": attempt.get("task_id") or "",
            "attempt_id": attempt.get("attempt_id") or "",
            "phase": attempt.get("phase") or "",
            "iteration_id": attempt.get("iteration_id") or "",
            "iteration_no": attempt.get("iteration_no") or 0,
            "summary": attempt.get("summary") or "上游 revision 变化，执行已取消",
            "output_revision": "",
            "evidence_ids": [],
            "next_action": attempt.get("next_action") or "",
        })
    payloads[work_path] = work_record.render_document(version, work_events)
