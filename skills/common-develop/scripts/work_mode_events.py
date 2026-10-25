"""Version-level audit ledger for work-mode changes."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from common_utils import atomic_write_text, canonical_json, now

EVENT_TYPE = "WORK_MODE_CHANGED"
EVENT_SCHEMA_VERSION = 1
EVENT_FILENAME = "work_mode_events.jsonl"
EVENT_SOURCES = {"work_mode.apply", "work_mode.init"}


def event_path(doc_root: Path, version: str) -> Path:
    return Path(doc_root) / "version" / str(version) / EVENT_FILENAME


def _digest(event: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in event.items() if key != "digest"}
    return hashlib.sha256(canonical_json(unsigned).encode("utf-8")).hexdigest()


def read_events(doc_root: Path, version: str) -> list[dict[str, Any]]:
    path = event_path(doc_root, version)
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    previous = ""
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid work mode event at {path}:{line_no}: {exc}") from exc
        if not isinstance(value, dict) or value.get("type") != EVENT_TYPE:
            raise ValueError(f"invalid work mode event at {path}:{line_no}")
        required = ("schemaVersion", "eventId", "version", "timestamp", "agent", "source", "before", "after", "previousDigest", "digest")
        missing = [key for key in required if key not in value]
        if missing:
            raise ValueError(f"work mode event missing fields at {path}:{line_no}: {', '.join(missing)}")
        if value.get("schemaVersion") != EVENT_SCHEMA_VERSION:
            raise ValueError(f"unsupported work mode event schema at {path}:{line_no}")
        if not isinstance(value.get("eventId"), str) or not value["eventId"].strip():
            raise ValueError(f"work mode event id is invalid at {path}:{line_no}")
        if not isinstance(value.get("version"), str) or not value["version"].strip() or value["version"] != str(version):
            raise ValueError(f"work mode event version is invalid at {path}:{line_no}")
        for field in ("timestamp", "agent"):
            if not isinstance(value.get(field), str) or not value[field].strip():
                raise ValueError(f"work mode event {field} is invalid at {path}:{line_no}")
        if value.get("source") not in EVENT_SOURCES:
            raise ValueError(f"work mode event source is invalid at {path}:{line_no}")
        if not isinstance(value.get("before"), dict) or not isinstance(value.get("after"), dict):
            raise ValueError(f"work mode event mode snapshots are invalid at {path}:{line_no}")
        if not isinstance(value.get("previousDigest"), str):
            raise ValueError(f"work mode event previousDigest is invalid at {path}:{line_no}")
        if not isinstance(value.get("digest"), str) or not value["digest"].strip():
            raise ValueError(f"work mode event digest is invalid at {path}:{line_no}")
        if value.get("previousDigest", "") != previous or value.get("digest") != _digest(value):
            raise ValueError(f"work mode event chain is invalid at {path}:{line_no}")
        events.append(value)
        previous = str(value["digest"])
    return events


def append_change(doc_root: Path, version: str, before: dict[str, Any], after: dict[str, Any], *, agent: str, source: str = "work_mode.apply") -> dict[str, Any] | None:
    """Append one change unless the effective mode is unchanged."""
    if before == after:
        return None
    if source not in EVENT_SOURCES:
        raise ValueError(f"unsupported work mode event source: {source}")
    if not isinstance(agent, str) or not agent.strip():
        raise ValueError("work mode event agent must be a non-empty string")
    path = event_path(doc_root, version)
    events = read_events(doc_root, version)
    event = {
        "schemaVersion": EVENT_SCHEMA_VERSION,
        "type": EVENT_TYPE,
        "eventId": f"WME-{len(events) + 1:06d}",
        "timestamp": now(),
        "version": str(version),
        "agent": agent,
        "source": source,
        "before": before,
        "after": after,
        "previousDigest": str(events[-1].get("digest") or "") if events else "",
    }
    event["digest"] = _digest(event)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    atomic_write_text(path, existing + json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return event


def last_mode(doc_root: Path, version: str) -> dict[str, Any] | None:
    events = read_events(doc_root, version)
    return dict(events[-1]["after"]) if events else None


def historical_lightweight_state(doc_root: Path, version: str) -> dict[str, Any]:
    skipped: list[str] = []
    waived = False
    for event in read_events(doc_root, version):
        after = event.get("after") if isinstance(event.get("after"), dict) else {}
        if after.get("lightweight") is True:
            waived = True
            for phase in after.get("skippedPhases") or []:
                if isinstance(phase, str) and phase not in skipped:
                    skipped.append(phase)
    return {"waived": waived, "skippedPhases": skipped}


def render_events(doc_root: Path, version: str) -> str:
    return "\n".join(json.dumps(event, ensure_ascii=False, indent=2) for event in read_events(doc_root, version))


__all__ = ["EVENT_TYPE", "EVENT_SCHEMA_VERSION", "EVENT_FILENAME", "EVENT_SOURCES", "event_path", "read_events", "append_change", "render_events", "last_mode", "historical_lightweight_state"]
