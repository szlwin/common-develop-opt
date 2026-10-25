"""Canonical event sourced lifecycle state machine prototype for B4-2."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .persistence import atomic_write_text, canonical_json, sha256_bytes, task_writer_lock

STATES = frozenset({"PLANNED", "IN_PROGRESS", "BLOCKED", "COMPLETED", "REOPENED"})
TRANSITIONS = {
    ("PLANNED", "START"): "IN_PROGRESS",
    ("IN_PROGRESS", "STEP"): "IN_PROGRESS",
    ("IN_PROGRESS", "BLOCK"): "BLOCKED",
    ("IN_PROGRESS", "FINISH"): "COMPLETED",
    ("COMPLETED", "REOPEN"): "REOPENED",
    ("BLOCKED", "RESUME"): "IN_PROGRESS",
}


class StateMachineError(ValueError):
    pass


def _paths(root: Path) -> tuple[Path, Path]:
    root = Path(root)
    return root / "state.json", root / "events.jsonl"


def task_paths(task_dir: Path) -> tuple[Path, Path]:
    """Paths for the production task journal and rebuildable snapshot."""
    root = Path(task_dir)
    return root / "cache" / "canonical_state.json", root / "task_events.jsonl"


def replay_task_events(task_dir: Path) -> dict[str, Any]:
    """Replay real task_events records without importing the legacy reducer."""
    state_path, journal = task_paths(task_dir)
    rows = _read_events(journal)
    state: dict[str, Any] = {}
    previous = ""
    for index, row in enumerate(rows, 1):
        if int(row.get("seq") or index) != index:
            raise StateMachineError("task event sequence is not contiguous")
        digest = str(row.get("digest") or "")
        if digest and str(row.get("prev_digest") or "") != previous:
            raise StateMachineError("task event digest chain is broken")
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
        patch = payload.get("state_patch") if isinstance(payload.get("state_patch"), dict) else payload.get("state")
        if isinstance(patch, dict):
            state.update(patch)
        for key in ("task_id", "phase", "revision", "status"):
            if row.get(key) is not None:
                state[key] = row[key]
        previous = digest
    if rows:
        state["event_count"] = len(rows)
        state["last_digest"] = previous
    atomic_write_text(state_path, canonical_json(state) + "\n")
    return state


def append_task_event(task_dir: Path, event: dict[str, Any]) -> dict[str, Any]:
    """Append one canonical task event using the production journal filename."""
    required = ("task_id", "phase", "revision", "event_type")
    if any(not str(event.get(key) or "") for key in required):
        raise StateMachineError("event requires task_id, phase, revision, and event_type")
    state_path, journal = task_paths(task_dir)
    with task_writer_lock(Path(task_dir)):
        rows = _read_events(journal)
        event_id = str(event.get("event_id") or sha256_bytes(canonical_json(event).encode()))
        if any(str(row.get("event_id") or "") == event_id for row in rows):
            return {"status": "IDEMPOTENT", "eventId": event_id}
        previous = str(rows[-1].get("digest") or "") if rows else ""
        record = dict(event)
        record.update({"seq": len(rows) + 1, "event_id": event_id, "prev_digest": previous})
        record["digest"] = sha256_bytes(canonical_json(record).encode())
        atomic_write_text(journal, "".join(canonical_json(row) + "\n" for row in rows + [record]))
        state = replay_task_events(Path(task_dir))
        return {"status": "COMMITTED", "eventId": event_id, "state": state}


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise StateMachineError(f"invalid event journal line {number}") from exc
        if not isinstance(value, dict):
            raise StateMachineError(f"event journal line {number} must be object")
        rows.append(value)
    return rows


def reduce_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    state: dict[str, Any] = {"status": "PLANNED"}
    for row in events:
        event = row.get("event") or {}
        target = TRANSITIONS.get((str(state.get("status")), str(event.get("type"))))
        if target is None:
            raise StateMachineError("invalid lifecycle transition")
        state.update(event.get("state") or {})
        state["status"] = target
        state["taskId"] = event.get("taskId")
        state["phase"] = event.get("phase")
        state["revision"] = event.get("revision")
    return state


def apply_event(root: Path, event: dict[str, Any], *, crash_after_prepare: bool = False) -> dict[str, Any]:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    required = ("taskId", "phase", "revision", "type")
    if any(not str(event.get(key) or "") for key in required):
        raise StateMachineError("event requires taskId, phase, revision, and type")
    state_path, journal_path = _paths(root)
    with task_writer_lock(root):
        rows = _read_events(journal_path)
        event_id = str(event.get("eventId") or sha256_bytes(canonical_json(event).encode()))
        if any(row.get("eventId") == event_id for row in rows):
            return {"status": "IDEMPOTENT", "eventId": event_id}
        current = reduce_events(rows)
        target = TRANSITIONS.get((str(current.get("status")), str(event["type"])))
        if target is None:
            raise StateMachineError(f"invalid lifecycle transition from {current['status']} via {event['type']}")
        record = {"eventId": event_id, "event": dict(event), "digest": sha256_bytes(canonical_json(event).encode())}
        prepared = rows + [record]
        if crash_after_prepare:
            raise RuntimeError("CRASH_INJECTED_AFTER_PREPARE")
        next_state = dict(current)
        next_state.update(event.get("state") or {})
        next_state.update({"status": target, "taskId": event["taskId"], "phase": event["phase"], "revision": event["revision"]})
        atomic_write_text(journal_path, "".join(canonical_json(row) + "\n" for row in prepared))
        atomic_write_text(state_path, canonical_json(next_state) + "\n")
        return {"status": "COMMITTED", "eventId": event_id, "stateDigest": sha256_bytes(canonical_json(next_state).encode())}


def recover(root: Path) -> dict[str, Any]:
    root = Path(root)
    state_path, journal_path = _paths(root)
    rows = _read_events(journal_path)
    if not rows:
        return {"status": "EMPTY", "replayed": 0}
    state = reduce_events(rows)
    atomic_write_text(state_path, canonical_json(state) + "\n")
    return {"status": "RECOVERED", "replayed": len(rows), "state": state}


__all__ = ["STATES", "TRANSITIONS", "StateMachineError", "apply_event", "recover", "reduce_events", "task_paths", "replay_task_events", "append_task_event"]
