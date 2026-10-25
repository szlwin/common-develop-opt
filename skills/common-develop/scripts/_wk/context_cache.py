"""Cache stable context only; lifecycle, Evidence and Review are always read fresh."""
from __future__ import annotations
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from common_utils import atomic_write_text

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_FIELDS = {"contractQueries", "contextRouting", "allowedCommands", "stableIdIndex", "revisionSummary"}


def _component(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]+", value):
        return value
    return "key-" + hashlib.sha256(value.encode()).hexdigest()


def cache_path(task_dir: Path, revision: str, profile: str) -> Path:
    return task_dir / "cache" / "context" / _component(revision) / f"{_component(profile)}.json"


def source_digests(task_dir: Path) -> dict[str, str]:
    sources = {"events": task_dir / "task_events.jsonl", "work": task_dir.parent.parent / "work.md",
               "traceability": task_dir / "traceability.json",
               "plan": task_dir / "task_plan.json", "intake": task_dir / "request_intake.json",
               "routing": ROOT / "context-routing.json"}
    sources["sourceMap"] = ROOT / "ai" / "source-map.json"
    sources["contextImplementation"] = Path(__file__).with_name("orchestration.py")
    # Contract normalization may materialize shared profile files. Include them
    # too so a dependency edit cannot leave an apparently valid cached query.
    sources.update({f"assets/{p.name}": p for p in (ROOT / "assets").glob("*.json")})
    return {key: hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"
            for key, path in sorted(sources.items())}


def source_digest(task_dir: Path) -> str:
    return _digest(source_digests(task_dir))


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def load(task_dir: Path, revision: str, profile: str) -> dict[str, Any] | None:
    if not revision:
        return None
    try:
        value = json.loads(cache_path(task_dir, revision, profile).read_text(encoding="utf-8"))
        payload = value.get("payload")
        if (value.get("revision") != revision or value.get("profile") != profile
                or value.get("sourceDigests") != source_digests(task_dir)
                or not isinstance(payload, dict) or set(payload) - ALLOWED_FIELDS
                or value.get("payloadDigest") != _digest(payload)):
            return None
        return value
    except (OSError, ValueError, AttributeError):
        return None


def save(task_dir: Path, revision: str, profile: str, payload: dict[str, Any]) -> Path:
    if not revision or set(payload) - ALLOWED_FIELDS:
        raise ValueError("context cache requires a concrete revision and stable context fields only")
    path = cache_path(task_dir, revision, profile)
    value = {"revision": revision, "profile": profile, "sourceDigests": source_digests(task_dir),
             "generatedAt": datetime.now(timezone.utc).isoformat(), "expiresOnChange": True,
             "payload": payload, "payloadDigest": _digest(payload)}
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    return path
