"""Compact append-only Review Result registry for the canonical v1 format."""
from __future__ import annotations

import sys
from pathlib import Path
_COMPAT_ROOT = Path(__file__).resolve().parent.parent / "compat"
_SCRIPT_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_SCRIPT_ROOT), str(_COMPAT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable, Optional

from .persistence import atomic_write_text, canonical_json as _canonical_json
from resource_paths import asset_path

# Full and compact records deliberately share the same semantic version. Load
# the value from the Runtime Contract so the storage implementation does not
# create a second version authority.
try:
    _RUNTIME_CONTRACT = json.loads(
        asset_path('assets/runtime/runtime-contract.json')
        .read_text(encoding="utf-8")
    )
    REVIEW_REGISTRY_SCHEMA_VERSION = int(_RUNTIME_CONTRACT["reviewRegistrySchemaVersion"])
except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
    raise RuntimeError(f"cannot load review registry schema version: {exc}") from exc

FULL_RECORD_SCHEMA_VERSION = REVIEW_REGISTRY_SCHEMA_VERSION
COMPACT_RECORD_SCHEMA_VERSION = REVIEW_REGISTRY_SCHEMA_VERSION
LEGACY_COMPACT_RECORD_SCHEMA_VERSIONS = frozenset({3, 4})
REVIEW_ID_RE = re.compile(r"^REV-[0-9]{6}$")
_REGISTRY_VALIDATION_CACHE: dict[tuple[str, str], tuple[list[str], dict[str, dict[str, Any]]]] = {}


def registry_path(task_dir: Path) -> Path:
    return Path(task_dir) / "evidence" / "reviews.jsonl"










def _load_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid {label} line {line_no}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{label} line {line_no} must be an object")
        records.append(value)
    return records


def next_review_id(records: Iterable[dict[str, Any]]) -> str:
    highest = 0
    for record in records:
        value = str(record.get("review_id") or "")
        if REVIEW_ID_RE.fullmatch(value):
            highest = max(highest, int(value[4:]))
    return f"REV-{highest + 1:06d}"


def validate_record(record: dict[str, Any], *, prefix: str = "review-result") -> list[str]:
    errors: list[str] = []
    allowed = {
        "schema_version", "review_id", "assertion_id", "acceptance_id", "profile_id",
        "reviewer", "confirmed_by", "confirmation_mode", "drafted_by_agent",
        "review_phase", "review_revision", "assertion_phase", "assertion_revision",
        "result", "conclusion_mode", "conclusion", "selected_evidence_ids", "issue_ids",
        "default_answer", "answer_exceptions", "summary", "notes", "confirmed_at",
    }
    unknown = sorted(set(record) - allowed)
    if unknown:
        errors.append(f"{prefix} contains unknown fields: {unknown}")
    if record.get("schema_version") != FULL_RECORD_SCHEMA_VERSION:
        errors.append(f"{prefix}.schema_version must be {FULL_RECORD_SCHEMA_VERSION}")
    if not REVIEW_ID_RE.fullmatch(str(record.get("review_id") or "")):
        errors.append(f"{prefix}.review_id is invalid: {record.get('review_id')}")
    for key in (
        "assertion_id", "acceptance_id", "profile_id", "reviewer", "confirmed_by",
        "review_phase", "review_revision", "assertion_phase", "assertion_revision",
        "result", "conclusion_mode", "confirmed_at",
    ):
        if not isinstance(record.get(key), str) or not record.get(key):
            errors.append(f"{prefix}.{key} must be a non-empty string")
    for key in ("selected_evidence_ids", "issue_ids", "notes"):
        if not isinstance(record.get(key), list):
            errors.append(f"{prefix}.{key} must be an array")
    if not isinstance(record.get("answer_exceptions"), dict):
        errors.append(f"{prefix}.answer_exceptions must be an object")
    if not isinstance(record.get("conclusion"), dict):
        errors.append(f"{prefix}.conclusion must be an object")
    return errors


def _catalog_ref(values: list[Any], value: Any) -> int:
    if value not in values:
        values.append(value)
    return values.index(value)


def _compact_criterion(
    item: dict[str, Any],
    review_id: str,
    text_catalog: list[str],
    evidence_sets: list[list[str]],
) -> dict[str, Any]:
    evidence_ids: list[str] = []
    claim_overrides: dict[str, str] = {}
    for evidence_item in item.get("evidence") or []:
        if not isinstance(evidence_item, dict) or not evidence_item.get("evidence_id"):
            continue
        evidence_id = str(evidence_item["evidence_id"])
        evidence_ids.append(evidence_id)
        claim = str(evidence_item.get("claim") or "")
        default_claim = f"Review Result {review_id} supports {item.get('criterion_id') or ''}"
        if claim and claim != default_claim and not claim.startswith("guided manual Review supports "):
            claim_overrides[evidence_id] = claim
    compact: dict[str, Any] = {
        "id": str(item.get("criterion_id") or ""),
        "s": str(item.get("status") or ""),
        "e": _catalog_ref(evidence_sets, list(dict.fromkeys(evidence_ids))),
    }
    finding_ids = list(item.get("finding_ids") or [])
    if finding_ids:
        compact["f"] = finding_ids
    reason = str(item.get("reason") or "")
    if reason:
        compact["r"] = _catalog_ref(text_catalog, reason)
    if claim_overrides:
        compact["c"] = claim_overrides
    return compact


def _expand_criterion(
    item: dict[str, Any],
    review_id: str,
    text_catalog: list[str],
    evidence_sets: list[list[str]],
) -> dict[str, Any]:
    criterion_id = str(item.get("id") or "")
    set_index = item.get("e")
    evidence_ids = evidence_sets[set_index] if isinstance(set_index, int) and 0 <= set_index < len(evidence_sets) else []
    overrides = item.get("c") if isinstance(item.get("c"), dict) else {}
    result: dict[str, Any] = {
        "criterion_id": criterion_id,
        "status": str(item.get("s") or ""),
        "evidence": [
            {
                "evidence_id": str(evidence_id),
                "claim": str(overrides.get(str(evidence_id)) or f"Review Result {review_id} supports {criterion_id}"),
            }
            for evidence_id in evidence_ids
        ],
        "finding_ids": list(item.get("f") or []),
    }
    reason_index = item.get("r")
    if isinstance(reason_index, int) and 0 <= reason_index < len(text_catalog):
        result["reason"] = text_catalog[reason_index]
    return result




def _compact_record(record: dict[str, Any]) -> dict[str, Any]:
    conclusion = record.get("conclusion") if isinstance(record.get("conclusion"), dict) else {}
    expected = {
        "profile_id": record.get("profile_id"),
        "revision": record.get("review_revision"),
        "conclusion": record.get("result"),
        "reviewed_at": record.get("confirmed_at"),
    }
    text_catalog: list[str] = []
    evidence_sets: list[list[str]] = []
    conclusion_payload: dict[str, Any] = {}
    for key, value in conclusion.items():
        if key == "criteria_results":
            conclusion_payload[key] = [
                _compact_criterion(item, str(record["review_id"]), text_catalog, evidence_sets)
                for item in value or [] if isinstance(item, dict)
            ]
        elif key in expected and value == expected[key]:
            continue
        elif value not in (None, "", [], {}):
            conclusion_payload[key] = value
    answer_exceptions: dict[str, Any] = {}
    for key, raw in (record.get("answer_exceptions") or {}).items():
        if not isinstance(raw, dict):
            answer_exceptions[str(key)] = raw
            continue
        item = dict(raw)
        detail = str(item.pop("detail", "") or "")
        if detail:
            item["detail_ref"] = _catalog_ref(text_catalog, detail)
        answer_exceptions[str(key)] = item
    if text_catalog:
        conclusion_payload["text_catalog"] = text_catalog
    if evidence_sets:
        conclusion_payload["evidence_sets"] = evidence_sets
    compact: dict[str, Any] = {
        "schema_version": COMPACT_RECORD_SCHEMA_VERSION,
        "review_id": record["review_id"],
        "assertion_id": record["assertion_id"],
        "acceptance_id": record["acceptance_id"],
        "profile_id": record["profile_id"],
        "reviewer": record["reviewer"],
        "review_phase": record["review_phase"],
        "review_revision": record["review_revision"],
        "assertion_phase": record["assertion_phase"],
        "assertion_revision": record["assertion_revision"],
        "result": record["result"],
        "conclusion_mode": record["conclusion_mode"],
        "confirmed_at": record["confirmed_at"],
        "confirmed_by": record["confirmed_by"],
        "confirmation_mode": record["confirmation_mode"],
        "conclusion": conclusion_payload,
        "evidence_ids": list(record.get("selected_evidence_ids") or []),
        "default_answer": str(record.get("default_answer") or ""),
    }
    optional = {
        "drafted_by_agent": str(record.get("drafted_by_agent") or ""),
        "issue_ids": list(record.get("issue_ids") or []),
        "answer_exceptions": answer_exceptions,
        "summary": str(record.get("summary") or ""),
        "notes": list(record.get("notes") or []),
    }
    for key, value in optional.items():
        if value not in ("", [], {}):
            compact[key] = value
    return compact


def _expand_record(compact: dict[str, Any]) -> dict[str, Any]:
    payload = compact.get("conclusion") if isinstance(compact.get("conclusion"), dict) else {}
    text_catalog = list(payload.get("text_catalog") or [])
    evidence_sets = list(payload.get("evidence_sets") or [])
    conclusion: dict[str, Any] = {
        "profile_id": compact["profile_id"],
        "revision": compact["review_revision"],
        "conclusion": compact["result"],
        "finding_ids": [],
        "limitations": [],
        "reviewed_at": compact["confirmed_at"],
    }
    for key, value in payload.items():
        if key in {"text_catalog", "evidence_sets"}:
            continue
        if key == "criteria_results":
            conclusion[key] = [
                _expand_criterion(item, str(compact["review_id"]), text_catalog, evidence_sets)
                for item in value or [] if isinstance(item, dict)
            ]
        else:
            conclusion[key] = value
    answer_exceptions: dict[str, Any] = {}
    for key, raw in (compact.get("answer_exceptions") or {}).items():
        if not isinstance(raw, dict):
            answer_exceptions[str(key)] = raw
            continue
        item = dict(raw)
        detail_ref = item.pop("detail_ref", None)
        if isinstance(detail_ref, int) and 0 <= detail_ref < len(text_catalog):
            item["detail"] = text_catalog[detail_ref]
        answer_exceptions[str(key)] = item
    return {
        "schema_version": FULL_RECORD_SCHEMA_VERSION,
        "review_id": compact["review_id"],
        "assertion_id": compact["assertion_id"],
        "acceptance_id": compact["acceptance_id"],
        "profile_id": compact["profile_id"],
        "reviewer": compact["reviewer"],
        "confirmed_by": str(compact.get("confirmed_by") or ""),
        "confirmation_mode": str(compact.get("confirmation_mode") or ""),
        "drafted_by_agent": str(compact.get("drafted_by_agent") or ""),
        "review_phase": compact["review_phase"],
        "review_revision": compact["review_revision"],
        "assertion_phase": compact["assertion_phase"],
        "assertion_revision": compact["assertion_revision"],
        "result": compact["result"],
        "conclusion_mode": compact["conclusion_mode"],
        "conclusion": conclusion,
        "selected_evidence_ids": list(compact.get("evidence_ids") or []),
        "issue_ids": list(compact.get("issue_ids") or []),
        "default_answer": str(compact.get("default_answer") or ""),
        "answer_exceptions": answer_exceptions,
        "summary": str(compact.get("summary") or ""),
        "notes": list(compact.get("notes") or []),
        "confirmed_at": compact["confirmed_at"],
    }

def validate_compact_record(record: dict[str, Any], *, prefix: str = "review") -> list[str]:
    errors: list[str] = []
    required = {
        "schema_version", "review_id", "assertion_id", "acceptance_id", "profile_id",
        "reviewer", "review_phase", "review_revision", "assertion_phase",
        "assertion_revision", "result", "conclusion_mode", "confirmed_at",
        "confirmed_by", "confirmation_mode", "conclusion", "evidence_ids", "default_answer",
    }
    allowed = required | {"drafted_by_agent", "issue_ids", "answer_exceptions", "summary", "notes"}
    unknown = sorted(set(record) - allowed)
    if unknown:
        errors.append(f"{prefix} contains unknown fields: {unknown}")
    if record.get("schema_version") != COMPACT_RECORD_SCHEMA_VERSION:
        errors.append(f"{prefix}.schema_version must be {COMPACT_RECORD_SCHEMA_VERSION}")
    missing = sorted(key for key in required if key not in record)
    if missing:
        errors.append(f"{prefix} missing fields: {missing}")
    if not REVIEW_ID_RE.fullmatch(str(record.get("review_id") or "")):
        errors.append(f"{prefix}.review_id is invalid")
    if not isinstance(record.get("conclusion"), dict):
        errors.append(f"{prefix}.conclusion must be an object")
    if not isinstance(record.get("evidence_ids"), list):
        errors.append(f"{prefix}.evidence_ids must be an array")
    for key, expected in (("issue_ids", list), ("answer_exceptions", dict), ("notes", list)):
        if key in record and not isinstance(record.get(key), expected):
            errors.append(f"{prefix}.{key} has invalid type")
    return errors








def load_records(task_dir: Path) -> list[dict[str, Any]]:
    """Load current compact Review records."""
    raw = _load_jsonl(registry_path(task_dir), "reviews.jsonl")
    if not raw:
        return []
    versions = {item.get("schema_version") for item in raw}
    if not all(version == COMPACT_RECORD_SCHEMA_VERSION for version in versions):
        legacy = sorted(version for version in versions if version in LEGACY_COMPACT_RECORD_SCHEMA_VERSIONS)
        detail = f"; legacy versions present={legacy}" if legacy else ""
        raise ValueError(
            "reviews.jsonl must use the current registry format (v1); "
            "legacy records require an explicit audited migration" + detail
        )
    return [_expand_record(item) for item in raw if item.get("kind") != "PLAN_REVIEW"]




def records_by_id(task_dir: Path) -> dict[str, dict[str, Any]]:
    return {str(item["review_id"]): item for item in load_records(task_dir)}


def validate_registry(task_dir: Path) -> tuple[list[str], dict[str, dict[str, Any]]]:
    path = registry_path(task_dir)
    cache_key: tuple[str, str] | None = None
    try:
        cache_key = (str(path.resolve()), hashlib.sha256(path.read_bytes()).hexdigest())
        cached = _REGISTRY_VALIDATION_CACHE.get(cache_key)
        if cached is not None:
            return cached
    except OSError:
        pass
    errors: list[str] = []
    result: dict[str, dict[str, Any]] = {}
    try:
        raw = _load_jsonl(registry_path(task_dir), "reviews.jsonl")
        for index, compact in enumerate(raw):
            if compact.get("kind") == "PLAN_REVIEW":
                continue
            prefix = f"reviews[{index}]"
            item_errors = validate_compact_record(compact, prefix=prefix)
            errors.extend(item_errors)
            if item_errors:
                continue
            if _canonical_json(compact) != _canonical_json(_compact_record(_expand_record(compact))):
                errors.append(f"{prefix} is not canonical compact JSON")
                continue
            record = _expand_record(compact)
            errors.extend(validate_record(record, prefix=f"{prefix}.materialized"))
            review_id = str(record.get("review_id") or "")
            if review_id in result:
                errors.append(f"duplicate review_id: {review_id}")
            result[review_id] = record
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(str(exc))
    outcome = (errors, result)
    if cache_key is not None:
        _REGISTRY_VALIDATION_CACHE.clear()
        _REGISTRY_VALIDATION_CACHE[cache_key] = outcome
    return outcome


def save_records(task_dir: Path, records: list[dict[str, Any]]) -> None:
    _REGISTRY_VALIDATION_CACHE.clear()
    task = Path(task_dir)
    target = registry_path(task)
    target.parent.mkdir(parents=True, exist_ok=True)
    compact_records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, source in enumerate(records):
        record = dict(source)
        errors = validate_record(record, prefix=f"review-result[{index}]")
        if errors:
            raise ValueError("; ".join(errors))
        review_id = str(record["review_id"])
        if review_id in seen:
            raise ValueError(f"duplicate review_id: {review_id}")
        seen.add(review_id)
        compact_records.append(_compact_record(record))
    payload = "".join(_canonical_json(item) + "\n" for item in compact_records)
    atomic_write_text(target, payload)


def append_record(task_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    _REGISTRY_VALIDATION_CACHE.clear()
    existing = load_records(task_dir)
    record = dict(payload)
    record.setdefault("schema_version", FULL_RECORD_SCHEMA_VERSION)
    record.setdefault("review_id", next_review_id(existing))
    errors = validate_record(record)
    if errors:
        raise ValueError("; ".join(errors))
    if any(item.get("review_id") == record["review_id"] for item in existing):
        raise ValueError(f"duplicate review_id: {record['review_id']}")
    compact = _compact_record(record)
    path = registry_path(Path(task_dir))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(_canonical_json(compact) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return record








def require_record(task_dir: Path, review_id: str) -> dict[str, Any]:
    errors, records = validate_registry(task_dir)
    if errors:
        raise ValueError("; ".join(errors))
    record = records.get(review_id)
    if not isinstance(record, dict):
        raise ValueError(f"unknown review_result_ref: {review_id}")
    return record


def compact_conclusion(record: dict[str, Any]) -> dict[str, Any]:
    conclusion = record.get("conclusion") if isinstance(record.get("conclusion"), dict) else {}
    return {
        "profile_id": conclusion.get("profile_id") or record.get("profile_id"),
        "revision": conclusion.get("revision") or record.get("review_revision"),
        "conclusion": conclusion.get("conclusion") or record.get("result"),
        "review_result_ref": record.get("review_id"),
        "finding_ids": list(conclusion.get("finding_ids") or []),
        "reviewed_at": conclusion.get("reviewed_at") or record.get("confirmed_at"),
    }


def materialize_conclusion(task_dir: Path, compact: dict[str, Any]) -> dict[str, Any]:
    review_id = str(compact.get("review_result_ref") or "")
    if not review_id:
        return compact
    conclusion = require_record(task_dir, review_id).get("conclusion")
    if not isinstance(conclusion, dict):
        raise ValueError(f"review result lacks conclusion: {review_id}")
    return conclusion


def find_latest(
    task_dir: Path,
    *,
    reviewer: str = "",
    phase: str = "",
    revision: str = "",
    assertion_id: str = "",
) -> Optional[dict[str, Any]]:
    for record in reversed(load_records(task_dir)):
        if reviewer and record.get("reviewer") != reviewer:
            continue
        if phase and record.get("review_phase") != phase:
            continue
        if revision and record.get("review_revision") != revision:
            continue
        if assertion_id and record.get("assertion_id") != assertion_id:
            continue
        return record
    return None
