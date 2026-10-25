"""Append-only lifecycle ledger and deterministic task runtime reducer.

``task_events.jsonl`` is the authority for mutable task runtime. The current
manifest is a rebuildable projection of the reducer output.
"""
from __future__ import annotations

import copy
import gzip
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from document_core.common_utils import now, sha256_bytes
from document_core import storage
from runtime._long_task.contracts import OPEN_ISSUE_STATUSES

EVENT_FILE = "task_events.jsonl"
EVENT_SCHEMA_VERSION = 1
CACHE_STATE_REF = "cache/current_manifest.json"
SNAPSHOT_EVENT_TYPE = "TASK_REDUCER_SNAPSHOT"
EVENT_ARCHIVE_DIR = "history"
DEFAULT_COMPACTION_THRESHOLD_BYTES = 256 * 1024

# Fields projected from dedicated event families rather than state patches.
DERIVED_STATE_FIELDS = {
    "active_task_ids",
    "current_attempt_id",
    "failed_attempts",
    "stale_events",
    "auto_remediation",
    "active_amendment",
    "open_issue_ids",
    "event_projection",
}

# Runtime-only fields are owned by the event reducer, not the static plan.
TASK_PLAN_RUNTIME_FIELDS = {
    "status",
    "attempts",
    "output_revision",
    "validation_evidence_ids",
    # Diagnostic/effective policy fields are materialized views, never static
    # task-plan facts.  Treat them like the legacy runtime fields everywhere
    # the static plan is validated or persisted.
    "declared_max_attempts",
    "effective_max_attempts",
    "max_attempts_source",
    "policy_override",
}

EVENT_TYPES = {
    SNAPSHOT_EVENT_TYPE,
    "TASK_STATE_PATCHED",
    "TASK_RUNTIME_SET",
    "WORK_POLICY_SET",
    "TASK_POLICY_SET",
    "HISTORICAL_CLOSURE_RECORDED",
    "ATTEMPT_STARTED",
    "ATTEMPT_COMMAND_RECORDED",
    "ATTEMPT_FINISHED",
    "ATTEMPT_CANCELLED",
    "STAGE_OUTCOME_PUBLISHED",
    "STAGE_OUTCOME_FINALIZED",
    "STAGE_OUTCOME_SUPERSEDED",
    "STAGE_OUTCOME_RECORDED",
    "PHASE_STALE",
    "AUTO_REMEDIATION_DECISION",
    "AUTO_REMEDIATION_CONVERGENCE",
    "ISSUE_OPENED",
    "ISSUE_ASSIGNED",
    "ISSUE_REMEDIATED",
    "ISSUE_VERIFIED",
    "ISSUE_CLOSED",
    "ISSUE_DEFERRED",
    "ISSUE_REOPENED",
    "ISSUE_REJECTED",
    "AUTHORIZATION_BATCH_CONFIRMED",
    "AUTHORIZATION_BATCH_CONSUMED",
    "ARTIFACT_AMENDED",
    "TRACEABILITY_REVALIDATED",
}

ISSUE_EVENT_TYPES = {
    "ISSUE_OPENED",
    "ISSUE_ASSIGNED",
    "ISSUE_REMEDIATED",
    "ISSUE_VERIFIED",
    "ISSUE_CLOSED",
    "ISSUE_DEFERRED",
    "ISSUE_REOPENED",
    "ISSUE_REJECTED",
}


class TaskEventError(ValueError):
    """Raised when the event ledger is malformed or inconsistent."""


def event_path(task_dir: Path) -> Path:
    return Path(task_dir) / EVENT_FILE


def state_cache_path(task_dir: Path) -> Path:
    return Path(task_dir) / CACHE_STATE_REF






def _canonical_event_payload(event: dict[str, Any]) -> bytes:
    payload = {key: value for key, value in event.items() if key != "digest"}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _event_digest(event: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_event_payload(event)).hexdigest()


def _archive_path(task_dir: Path, ref: str) -> Path:
    clean = str(ref or "").strip()
    if not clean:
        raise TaskEventError("snapshot event archive ref is required")
    root = Path(task_dir).resolve()
    resolved = (root / clean).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise TaskEventError(f"snapshot event archive escapes task directory: {ref}") from exc
    return resolved


def _validate_snapshot_archive(
    task_dir: Path,
    event: dict[str, Any],
    _seen: set[Path] | None = None,
) -> None:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    reduced = payload.get("reduced")
    archive = payload.get("archive")
    if not isinstance(reduced, dict) or not isinstance(archive, dict):
        raise TaskEventError("snapshot event requires reduced and archive objects")
    path = _archive_path(task_dir, str(archive.get("ref") or ""))
    seen = _seen if _seen is not None else set()
    if path in seen:
        raise TaskEventError(f"snapshot event archive cycle detected: {path}")
    seen.add(path)
    if not path.is_file():
        raise TaskEventError(f"snapshot event archive is missing: {path}")
    compressed = path.read_bytes()
    if sha256_bytes(compressed) != str(archive.get("sha256") or ""):
        raise TaskEventError(f"snapshot event archive digest mismatch: {path}")
    try:
        raw = gzip.decompress(compressed)
        text = raw.decode("utf-8")
    except (OSError, EOFError, UnicodeError) as exc:
        raise TaskEventError(f"snapshot event archive is invalid: {path}") from exc
    if sha256_bytes(raw) != str(archive.get("uncompressed_sha256") or ""):
        raise TaskEventError(f"snapshot event archive content digest mismatch: {path}")
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) != int(archive.get("event_count") or 0):
        raise TaskEventError(f"snapshot event archive count mismatch: {path}")
    try:
        first = json.loads(lines[0]) if lines else {}
        last = json.loads(lines[-1]) if lines else {}
    except json.JSONDecodeError as exc:
        raise TaskEventError(f"snapshot event archive boundary record is invalid: {path}") from exc
    if first.get("type") == SNAPSHOT_EVENT_TYPE:
        _validate_snapshot_archive(task_dir, first, seen)
    if int(last.get("seq") or 0) != int(archive.get("last_seq") or 0):
        raise TaskEventError(f"snapshot event archive last seq mismatch: {path}")
    if str(last.get("digest") or "") != str(archive.get("last_digest") or ""):
        raise TaskEventError(f"snapshot event archive last digest mismatch: {path}")
    if str(event.get("prev_digest") or "") != str(archive.get("last_digest") or ""):
        raise TaskEventError("snapshot event does not continue the archived digest chain")
    if int(event.get("seq") or 0) != int(archive.get("last_seq") or 0) + 1:
        raise TaskEventError("snapshot event seq does not continue the archived sequence")


def _parse_event_text(text_value: str, *, source_path: Path, task_dir: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    previous = ""
    expected_seq = 1
    for line_no, raw in enumerate(text_value.splitlines(), start=1):
        text = raw.strip()
        if not text:
            continue
        try:
            event = json.loads(text)
        except json.JSONDecodeError as exc:
            raise TaskEventError(f"{source_path}:{line_no}: invalid JSON: {exc}") from exc
        if not isinstance(event, dict):
            raise TaskEventError(f"{source_path}:{line_no}: event must be an object")
        seq = event.get("seq")
        if not events and event.get("type") == SNAPSHOT_EVENT_TYPE:
            if not isinstance(seq, int) or seq <= 1:
                raise TaskEventError(f"{source_path}:{line_no}: snapshot seq must be greater than 1")
            expected_seq = seq
            previous = str(event.get("prev_digest") or "")
            _validate_snapshot_archive(task_dir, event)
        if seq != expected_seq:
            raise TaskEventError(f"{source_path}:{line_no}: seq must be {expected_seq}, got {seq}")
        if event.get("schema_version") != EVENT_SCHEMA_VERSION:
            raise TaskEventError(f"{source_path}:{line_no}: unsupported schema_version {event.get('schema_version')}")
        if event.get("type") not in EVENT_TYPES:
            raise TaskEventError(f"{source_path}:{line_no}: unsupported event type {event.get('type')}")
        if event.get("prev_digest", "") != previous:
            raise TaskEventError(f"{source_path}:{line_no}: prev_digest chain mismatch")
        digest = str(event.get("digest") or "")
        if not digest or digest != _event_digest(event):
            raise TaskEventError(f"{source_path}:{line_no}: digest mismatch")
        previous = digest
        expected_seq = int(seq) + 1
        events.append(event)
    return events


def _parse_lines(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return _parse_event_text(path.read_text(encoding="utf-8"), source_path=path, task_dir=path.parent)


def read_events(task_dir: Path) -> list[dict[str, Any]]:
    return _parse_lines(event_path(task_dir))


def _archived_history(task_dir: Path, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    archive = (snapshot.get("payload") or {}).get("archive") or {}
    path = _archive_path(task_dir, str(archive.get("ref") or ""))
    raw = gzip.decompress(path.read_bytes()).decode("utf-8")
    segment = _parse_event_text(raw, source_path=path, task_dir=Path(task_dir))
    if segment and segment[0].get("type") == SNAPSHOT_EVENT_TYPE:
        return [*_archived_history(Path(task_dir), segment[0]), *segment]
    return segment


def read_event_history(task_dir: Path) -> list[dict[str, Any]]:
    """Read active events plus immutable archive segments for explicit audit."""
    active = read_events(task_dir)
    if active and active[0].get("type") == SNAPSHOT_EVENT_TYPE:
        return [*_archived_history(Path(task_dir), active[0]), *active]
    return active


def render_events(events: Iterable[dict[str, Any]]) -> str:
    return "".join(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n" for event in events)


def _new_event(
    existing: list[dict[str, Any]],
    event_type: str,
    entity_id: str,
    payload: dict[str, Any],
    *,
    timestamp: str = "",
    source: str = "",
) -> dict[str, Any]:
    if event_type not in EVENT_TYPES:
        raise TaskEventError(f"unsupported event type: {event_type}")
    seq = int(existing[-1].get("seq") or 0) + 1 if existing else 1
    event = {
        "schema_version": EVENT_SCHEMA_VERSION,
        "seq": seq,
        "event_id": f"TE-{seq:06d}",
        "timestamp": timestamp or now(),
        "type": event_type,
        "entity_id": entity_id,
        "source": source or "common-develop",
        "prev_digest": str(existing[-1].get("digest") or "") if existing else "",
        "payload": copy.deepcopy(payload),
    }
    event["digest"] = _event_digest(event)
    return event


def _strip_derived_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(value)
        for key, value in state.items()
        if key not in DERIVED_STATE_FIELDS
    }








def strip_plan_runtime_fields(tasks: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        result.append({key: copy.deepcopy(value) for key, value in task.items() if key not in TASK_PLAN_RUNTIME_FIELDS})
    return result












def _seed_events_for_append(task_dir: Path) -> list[dict[str, Any]]:
    """Load the current ledger; an absent file represents a new task."""
    return read_events(task_dir)


def append_event_text(
    task_dir: Path,
    event_type: str,
    entity_id: str,
    payload: dict[str, Any],
    *,
    timestamp: str = "",
    source: str = "",
) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    events = _seed_events_for_append(task_dir)
    event = _new_event(events, event_type, entity_id, payload, timestamp=timestamp, source=source)
    events.append(event)
    return render_events(events), event, events


def append_many_text(task_dir: Path, specs: Iterable[dict[str, Any]]) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    events = _seed_events_for_append(task_dir)
    created: list[dict[str, Any]] = []
    for spec in specs:
        event = _new_event(
            events,
            str(spec["type"]),
            str(spec.get("entity_id") or ""),
            dict(spec.get("payload") or {}),
            timestamp=str(spec.get("timestamp") or ""),
            source=str(spec.get("source") or ""),
        )
        events.append(event)
        created.append(event)
    return render_events(events), created, events


def _apply_state_patch(state: dict[str, Any], payload: dict[str, Any]) -> None:
    # ``set``/``remove`` is the legacy top-level patch format and remains
    # readable forever.  New ledgers use JSON-Pointer path patches after the
    # initial seed so changing one nested Review or phase no longer repeats the
    # complete artifact/review state in every event.
    values = payload.get("set") if isinstance(payload.get("set"), dict) else {}
    for key, value in values.items():
        state[str(key)] = copy.deepcopy(value)
    removals = payload.get("remove") if isinstance(payload.get("remove"), list) else []
    for key in removals:
        if isinstance(key, str):
            state.pop(key, None)
    path_values = payload.get("set_paths") if isinstance(payload.get("set_paths"), dict) else {}
    for pointer, value in sorted(path_values.items(), key=lambda item: (item[0].count("/"), item[0])):
        parts = _pointer_parts(str(pointer))
        if not parts:
            if not isinstance(value, dict):
                raise TaskEventError("root state patch value must be an object")
            state.clear()
            state.update(copy.deepcopy(value))
            continue
        current: dict[str, Any] = state
        for part in parts[:-1]:
            child = current.get(part)
            if not isinstance(child, dict):
                child = {}
                current[part] = child
            current = child
        current[parts[-1]] = copy.deepcopy(value)
    path_removals = payload.get("remove_paths") if isinstance(payload.get("remove_paths"), list) else []
    for pointer in sorted((str(item) for item in path_removals), key=lambda item: (-item.count("/"), item)):
        parts = _pointer_parts(pointer)
        if not parts:
            state.clear()
            continue
        current: Any = state
        for part in parts[:-1]:
            if not isinstance(current, dict) or not isinstance(current.get(part), dict):
                current = None
                break
            current = current[part]
        if isinstance(current, dict):
            current.pop(parts[-1], None)


def reduce_events(events: Iterable[dict[str, Any]]) -> dict[str, Any]:
    attempts: dict[str, dict[str, Any]] = {}
    attempt_order: list[str] = []
    outcomes: dict[str, dict[str, Any]] = {}
    outcome_order: list[str] = []
    stale_events: list[dict[str, Any]] = []
    remediation_events: list[dict[str, Any]] = []
    authorization_events: list[dict[str, Any]] = []
    authorization_consumptions: list[dict[str, Any]] = []
    amendment_events: list[dict[str, Any]] = []
    traceability_revalidations: list[dict[str, Any]] = []
    issues: dict[str, dict[str, Any]] = {}
    issue_order: list[str] = []
    state_authority: dict[str, Any] = {}
    task_runtime: dict[str, dict[str, Any]] = {}
    # Runtime policy overrides are scoped to one logical task iteration.
    # Keeping the iteration key here prevents a reopen from inheriting an
    # override that was deliberately set for an older iteration.
    task_policies: dict[str, dict[str, dict[str, Any]]] = {}
    last_seq = 0
    last_digest = ""
    storage_mode = "EVENT_LEDGER_V3"
    for event in events:
        last_seq = int(event.get("seq") or 0)
        last_digest = str(event.get("digest") or "")
        event_type = str(event.get("type") or "")
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        entity_id = str(event.get("entity_id") or "")
        if event_type == SNAPSHOT_EVENT_TYPE:
            snapshot = payload.get("reduced") if isinstance(payload.get("reduced"), dict) else {}
            state_authority = copy.deepcopy(snapshot.get("state_authority") or {})
            attempt_values = [item for item in snapshot.get("attempts", []) if isinstance(item, dict)]
            attempts = {
                str(item.get("attempt_id")): copy.deepcopy(item)
                for item in attempt_values if item.get("attempt_id")
            }
            attempt_order = [str(item.get("attempt_id")) for item in attempt_values if item.get("attempt_id")]
            outcome_values = [item for item in snapshot.get("outcomes", []) if isinstance(item, dict)]
            outcomes = {
                str(item.get("id")): copy.deepcopy(item)
                for item in outcome_values if item.get("id")
            }
            outcome_order = [str(item.get("id")) for item in outcome_values if item.get("id")]
            stale_events = copy.deepcopy([
                item for item in snapshot.get("stale_events", []) if isinstance(item, dict)
            ])
            remediation_events = copy.deepcopy([
                item for item in snapshot.get("auto_remediation_events", []) if isinstance(item, dict)
            ])
            authorization_events = copy.deepcopy([
                item for item in snapshot.get("authorization_events", []) if isinstance(item, dict)
            ])
            authorization_consumptions = copy.deepcopy([
                item for item in snapshot.get("authorization_consumptions", []) if isinstance(item, dict)
            ])
            amendment_events = copy.deepcopy([
                item for item in snapshot.get("amendment_events", []) if isinstance(item, dict)
            ])
            traceability_revalidations = copy.deepcopy([
                item for item in snapshot.get("traceability_revalidations", []) if isinstance(item, dict)
            ])
            issue_values = [item for item in snapshot.get("issues", []) if isinstance(item, dict)]
            issues = {
                str(item.get("id")): copy.deepcopy(item)
                for item in issue_values if item.get("id")
            }
            issue_order = [str(item.get("id")) for item in issue_values if item.get("id")]
            task_runtime = copy.deepcopy(snapshot.get("task_runtime") or {})
            task_policies = copy.deepcopy(snapshot.get("task_policies") or {})
            storage_mode = "EVENT_LEDGER_V4_SEGMENTED"
        elif event_type == "TASK_STATE_PATCHED":
            _apply_state_patch(state_authority, payload)
        elif event_type == "TASK_RUNTIME_SET":
            runtime = payload.get("runtime") if isinstance(payload.get("runtime"), dict) else payload
            current = task_runtime.setdefault(entity_id, {})
            current.update(copy.deepcopy(runtime))
            current["_event_seq"] = last_seq
        elif event_type == "WORK_POLICY_SET":
            policy = payload.get("policy") if isinstance(payload.get("policy"), dict) else payload
            state_authority["work_policy"] = copy.deepcopy(policy)
        elif event_type == "TASK_POLICY_SET":
            policy = payload.get("policy") if isinstance(payload.get("policy"), dict) else payload
            task_id = str(policy.get("task_id") or entity_id)
            iteration_id = str(policy.get("iteration_id") or "")
            if task_id and iteration_id:
                task_policies.setdefault(task_id, {})[iteration_id] = copy.deepcopy(policy)
        elif event_type == "HISTORICAL_CLOSURE_RECORDED":
            closure = payload.get("closure") if isinstance(payload.get("closure"), dict) else payload
            state_authority["historical_closure"] = copy.deepcopy(closure)
        elif event_type.startswith("ATTEMPT_"):
            record = payload.get("attempt") if isinstance(payload.get("attempt"), dict) else payload
            attempt_id = str(record.get("attempt_id") or entity_id)
            if attempt_id:
                if attempt_id not in attempts:
                    attempt_order.append(attempt_id)
                attempts[attempt_id] = copy.deepcopy(record)
                task_id = str(record.get("task_id") or "")
                if task_id:
                    runtime = task_runtime.setdefault(task_id, {})
                    # Reopen sets the new iteration before cancelling old attempts.
                    if (event_type == "ATTEMPT_CANCELLED"
                            and runtime.get("iteration_id") and record.get("iteration_id")
                            and runtime["iteration_id"] != record["iteration_id"]):
                        continue
                    runtime.update({
                        "attempts": int(record.get("attempt_no") or runtime.get("attempts") or 0),
                        "iteration_id": str(record.get("iteration_id") or runtime.get("iteration_id") or ""),
                        "iteration_no": int(record.get("iteration_no") or runtime.get("iteration_no") or 0),
                    })
                    status = str(record.get("status") or "")
                    runtime["status"] = status
                    if status == "PASSED":
                        runtime["output_revision"] = str(record.get("output_revision") or "")
                        runtime["validation_evidence_ids"] = list(record.get("evidence_ids") or [])
                    elif status in {"FAILED", "BLOCKED", "CANCELLED"}:
                        runtime["output_revision"] = ""
                        runtime["validation_evidence_ids"] = list(record.get("evidence_ids") or [])
                    runtime["_event_seq"] = last_seq
        elif event_type.startswith("STAGE_OUTCOME_"):
            record = payload.get("outcome") if isinstance(payload.get("outcome"), dict) else payload
            outcome_id = str(record.get("id") or entity_id)
            if outcome_id:
                if outcome_id not in outcomes:
                    outcome_order.append(outcome_id)
                outcomes[outcome_id] = copy.deepcopy(record)
        elif event_type == "PHASE_STALE":
            record = payload.get("stale_event") if isinstance(payload.get("stale_event"), dict) else payload
            stale_events.append(copy.deepcopy(record))
        elif event_type.startswith("AUTO_REMEDIATION_"):
            remediation_events.append({
                "seq": event.get("seq"),
                "timestamp": event.get("timestamp"),
                "type": event_type,
                "entity_id": entity_id,
                "payload": copy.deepcopy(payload),
            })
        elif event_type == "AUTHORIZATION_BATCH_CONFIRMED":
            authorization_events.append({
                "seq": event.get("seq"),
                "timestamp": event.get("timestamp"),
                "type": event_type,
                "entity_id": entity_id,
                "payload": copy.deepcopy(payload),
            })
        elif event_type == "AUTHORIZATION_BATCH_CONSUMED":
            authorization_consumptions.append({
                "seq": event.get("seq"),
                "timestamp": event.get("timestamp"),
                "type": event_type,
                "entity_id": entity_id,
                "payload": copy.deepcopy(payload),
            })
        elif event_type == "ARTIFACT_AMENDED":
            record = payload.get("amendment") if isinstance(payload.get("amendment"), dict) else payload
            amendment_events.append(copy.deepcopy(record))
        elif event_type == "TRACEABILITY_REVALIDATED":
            record = payload.get("revalidation") if isinstance(payload.get("revalidation"), dict) else payload
            traceability_revalidations.append(copy.deepcopy(record))
        elif event_type in ISSUE_EVENT_TYPES:
            record = payload.get("issue") if isinstance(payload.get("issue"), dict) else payload
            issue_id = str(record.get("id") or entity_id)
            if issue_id:
                if issue_id not in issues:
                    issue_order.append(issue_id)
                issues[issue_id] = copy.deepcopy(record)
    return {
        "state_authority": state_authority,
        "attempts": [attempts[key] for key in attempt_order],
        "outcomes": [outcomes[key] for key in outcome_order],
        "stale_events": stale_events,
        "auto_remediation_events": remediation_events,
        "authorization_events": authorization_events,
        "authorization_consumptions": authorization_consumptions,
        "amendment_events": amendment_events,
        "traceability_revalidations": traceability_revalidations,
        "issues": [issues[key] for key in issue_order],
        "task_runtime": task_runtime,
        "task_policies": task_policies,
        "last_seq": last_seq,
        "last_digest": last_digest,
        "storage_mode": storage_mode,
    }


def prepare_compaction(
    task_dir: Path,
    *,
    threshold_bytes: int = DEFAULT_COMPACTION_THRESHOLD_BYTES,
    force: bool = False,
    timestamp: str = "",
    source: str = "long_task.py compact-events",
) -> dict[str, Any]:
    """Prepare an explicit, auditable ledger rollover without mutating files."""
    task_dir = Path(task_dir)
    path = event_path(task_dir)
    if not path.is_file():
        raise TaskEventError("task_events.jsonl is required")
    if threshold_bytes <= 0:
        raise TaskEventError("compaction threshold must be positive")
    raw = path.read_bytes()
    events = read_events(task_dir)
    if not events:
        raise TaskEventError("cannot compact an empty task event ledger")
    reduced = reduce_events(events)
    first_seq = int(events[0].get("seq") or 0)
    last_seq = int(events[-1].get("seq") or 0)
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    archive_rel = f"{EVENT_ARCHIVE_DIR}/task_events.{first_seq:06d}-{last_seq:06d}.jsonl.gz"
    archive = {
        "ref": archive_rel,
        "sha256": sha256_bytes(compressed),
        "uncompressed_sha256": sha256_bytes(raw),
        "event_count": len(events),
        "first_seq": first_seq,
        "last_seq": last_seq,
        "last_digest": str(events[-1].get("digest") or ""),
        "uncompressed_bytes": len(raw),
        "compressed_bytes": len(compressed),
    }
    snapshot_reduced = {
        key: copy.deepcopy(value)
        for key, value in reduced.items()
        if key not in {"last_seq", "last_digest", "storage_mode"}
    }
    snapshot = _new_event(
        events,
        SNAPSHOT_EVENT_TYPE,
        str((reduced.get("state_authority") or {}).get("target_id") or task_dir.name),
        {"archive": archive, "reduced": snapshot_reduced},
        timestamp=timestamp or now(),
        source=source,
    )
    compacted_text = render_events([snapshot])
    plan_source = {
        "active_sha256": sha256_bytes(raw),
        "last_seq": last_seq,
        "last_digest": str(events[-1].get("digest") or ""),
        "archive": archive,
    }
    plan_digest = sha256_bytes(
        json.dumps(plan_source, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    active_after = len(compacted_text.encode("utf-8"))
    needed = force or len(raw) >= threshold_bytes
    return {
        "status": "READY" if needed else "NOT_NEEDED",
        "needed": needed,
        "thresholdBytes": threshold_bytes,
        "activeBytesBefore": len(raw),
        "activeBytesAfter": active_after,
        "archiveBytes": len(compressed),
        "totalBytesAfter": active_after + len(compressed),
        "savedActiveBytes": max(0, len(raw) - active_after),
        "firstSeq": first_seq,
        "lastSeq": last_seq,
        "snapshotSeq": int(snapshot.get("seq") or 0),
        "archiveRef": archive_rel,
        "planDigest": plan_digest,
        "_archiveBytes": compressed,
        "_reducedBefore": reduced,
        "_snapshot": snapshot,
        "_ledgerText": compacted_text,
    }


def _pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _pointer_parts(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise TaskEventError(f"state patch path must be a JSON Pointer: {pointer}")
    if pointer == "/":
        return [""]
    return [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]


def _nested_state_patch(previous: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    set_paths: dict[str, Any] = {}
    remove_paths: list[str] = []

    def visit(old: Any, new: Any, path: str) -> None:
        if isinstance(old, dict) and isinstance(new, dict):
            for key in sorted(old.keys() - new.keys()):
                remove_paths.append(f"{path}/{_pointer_token(str(key))}")
            for key in sorted(new):
                child = f"{path}/{_pointer_token(str(key))}"
                if key not in old:
                    set_paths[child] = copy.deepcopy(new[key])
                elif old[key] != new[key]:
                    visit(old[key], new[key], child)
            return
        set_paths[path or "/"] = copy.deepcopy(new)

    visit(previous, target, "")
    return {"set_paths": set_paths, "remove_paths": sorted(remove_paths)}


def _state_patch(old: dict[str, Any], desired: dict[str, Any]) -> dict[str, Any]:
    previous = _strip_derived_state(old)
    target = _strip_derived_state(desired)
    if previous:
        return _nested_state_patch(previous, target)
    changed = {
        key: copy.deepcopy(value)
        for key, value in target.items()
        if key not in previous or previous.get(key) != value
    }
    removed = sorted(key for key in previous if key not in target)
    return {"set": changed, "remove": removed}


def state_patch_spec(reduced: dict[str, Any], desired_state: dict[str, Any], *, timestamp: str, source: str) -> dict[str, Any] | None:
    patch = _state_patch(reduced.get("state_authority") if isinstance(reduced.get("state_authority"), dict) else {}, desired_state)
    if not any(patch.values()):
        return None
    return {
        "type": "TASK_STATE_PATCHED",
        "entity_id": str(desired_state.get("target_id") or "task-state"),
        "timestamp": timestamp,
        "source": source,
        "payload": patch,
    }


def issue_event_spec(event_type: str, issue: dict[str, Any], *, timestamp: str, source: str) -> dict[str, Any]:
    if event_type not in ISSUE_EVENT_TYPES:
        raise TaskEventError(f"invalid issue lifecycle event: {event_type}")
    return {
        "type": event_type,
        "entity_id": str(issue.get("id") or ""),
        "timestamp": timestamp,
        "source": source,
        "payload": {"issue": copy.deepcopy(issue)},
    }


def task_policy_event_spec(
    task_id: str,
    iteration_id: str,
    before: int,
    after: int,
    reason: str,
    agent: str,
    *,
    timestamp: str,
    source: str,
) -> dict[str, Any]:
    """Build an auditable iteration-scoped max_attempts policy event."""
    if not task_id or not iteration_id:
        raise TaskEventError("task policy requires task_id and iteration_id")
    return {
        "type": "TASK_POLICY_SET",
        "entity_id": task_id,
        "timestamp": timestamp,
        "source": source,
        "payload": {"policy": {
            "task_id": task_id,
            "iteration_id": iteration_id,
            "before": int(before),
            "after": int(after),
            "reason": reason,
            "agent": agent,
        }},
    }






def current_issues(task_dir: Path) -> list[dict[str, Any]]:
    reduced = reduce_events(read_events(task_dir))
    return [copy.deepcopy(item) for item in reduced.get("issues", []) if isinstance(item, dict)]


def materialize_tasks(tasks: Iterable[dict[str, Any]], reduced: dict[str, Any]) -> list[dict[str, Any]]:
    """Overlay reducer-owned runtime fields on immutable canonical plan items."""
    runtime_map = reduced.get("task_runtime") if isinstance(reduced.get("task_runtime"), dict) else {}
    attempts = [item for item in reduced.get("attempts", []) if isinstance(item, dict)]
    result: list[dict[str, Any]] = []
    for original in tasks:
        if not isinstance(original, dict):
            continue
        task = copy.deepcopy(original)
        task_id = str(task.get("id") or "")
        runtime = copy.deepcopy(runtime_map.get(task_id) or {}) if task_id else {}
        current_iteration = str(runtime.get("iteration_id") or task.get("iteration_id") or "")
        declared_max_attempts = int(task.get("max_attempts") or 0)
        policy_map = reduced.get("task_policies") if isinstance(reduced.get("task_policies"), dict) else {}
        raw_policy = copy.deepcopy((policy_map.get(task_id) or {}).get(current_iteration) or {}) if task_id else {}
        # A clear event (after=0) remains in the ledger as a tombstone for
        # auditability, but it must not appear as an active materialized
        # override.
        policy_after = int(raw_policy.get("after") or 0) if raw_policy else 0
        task_policy = raw_policy if policy_after > 0 else {}
        effective_max_attempts = policy_after if policy_after > 0 else declared_max_attempts
        current_attempts = [
            item for item in attempts
            if str(item.get("task_id") or "") == task_id
            and (not current_iteration or str(item.get("iteration_id") or "") == current_iteration)
        ]
        attempt_count = len(current_attempts)
        status = str(runtime.get("status") or "")
        latest = current_attempts[-1] if current_attempts else None
        if latest and int(runtime.get("_event_seq") or 0) <= 0:
            status = str(latest.get("status") or status)
        if status == "FAILED":
            status = "REWORK" if effective_max_attempts <= 0 or attempt_count < effective_max_attempts else "BLOCKED"
        elif status == "PARTIAL":
            status = "REWORK"
        if not status:
            status = "PENDING"
        task["status"] = status
        task["attempts"] = int(runtime.get("attempts") or attempt_count)
        # Preserve the original plan value for diagnostics while keeping the
        # legacy max_attempts field effective for existing lifecycle code.
        task["declared_max_attempts"] = declared_max_attempts
        task["effective_max_attempts"] = effective_max_attempts
        task["max_attempts_source"] = "runtime_policy" if policy_after > 0 else "static_plan"
        task["policy_override"] = task_policy if task_policy else None
        task["max_attempts"] = effective_max_attempts
        task["output_revision"] = str(runtime.get("output_revision") or "")
        task["validation_evidence_ids"] = list(runtime.get("validation_evidence_ids") or [])
        for key in ("iteration_id", "iteration_no", "supersedes_iteration_id", "revision_reason", "input_revisions"):
            if key in runtime:
                task[key] = copy.deepcopy(runtime[key])
        result.append(task)
    return result


def project_state(state: dict[str, Any], reduced: dict[str, Any], *, synced_at: str = "") -> dict[str, Any]:
    """Build the rebuildable task-state cache from event authority and derived facts."""
    authority = reduced.get("state_authority") if isinstance(reduced.get("state_authority"), dict) else {}
    projected = copy.deepcopy(authority or _strip_derived_state(state))
    attempts = [item for item in reduced.get("attempts", []) if isinstance(item, dict)]
    running = [item for item in attempts if item.get("status") == "RUNNING"]
    failed: dict[str, int] = {}
    for attempt in attempts:
        if attempt.get("status") in {"FAILED", "BLOCKED"}:
            task_id = str(attempt.get("task_id") or "")
            if task_id:
                failed[task_id] = failed.get(task_id, 0) + 1
    if failed:
        projected["failed_attempts"] = failed
    stale = [copy.deepcopy(item) for item in reduced.get("stale_events", []) if isinstance(item, dict)]
    if stale:
        projected["stale_events"] = stale
    projected["active_task_ids"] = list(dict.fromkeys(str(item.get("task_id") or "") for item in running if item.get("task_id")))
    projected["current_attempt_id"] = str(running[-1].get("attempt_id") or "") if running else ""
    issues = [item for item in reduced.get("issues", []) if isinstance(item, dict)]
    projected["open_issue_ids"] = sorted({
        str(item.get("id")) for item in issues
        if str(item.get("status") or "") in OPEN_ISSUE_STATUSES and item.get("id")
    })
    remediation = reduced.get("auto_remediation_events") or []
    if remediation:
        latest = remediation[-1]
        projected["auto_remediation"] = {
            "event_seq": latest.get("seq"),
            "event_type": latest.get("type"),
            "timestamp": latest.get("timestamp"),
            "entity_id": latest.get("entity_id"),
            "decision": (latest.get("payload") or {}).get("decision", ""),
            "signal": (latest.get("payload") or {}).get("signal", ""),
        }
    amendments = [item for item in reduced.get("amendment_events", []) if isinstance(item, dict)]
    if amendments:
        latest_amendment = amendments[-1]
        phase = str(latest_amendment.get("phase") or "")
        artifact = (projected.get("artifact_revisions") or {}).get(phase)
        if (
            projected.get("current_phase") == phase
            and isinstance(artifact, dict)
            and artifact.get("revision") == latest_amendment.get("revision")
            and artifact.get("status") != "PASSED"
        ):
            projected["active_amendment"] = copy.deepcopy(latest_amendment)
    projected["event_projection"] = {
        "source_event_seq": int(reduced.get("last_seq") or 0),
        "source_digest": str(reduced.get("last_digest") or ""),
        "synced_at": synced_at or now(),
        "storage_mode": str(reduced.get("storage_mode") or "EVENT_LEDGER_V3"),
    }
    return projected
