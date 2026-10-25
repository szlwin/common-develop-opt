#!/usr/bin/env python3
"""Build the compact, rebuildable cross-session task recovery manifest."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import long_task
import work_mode
import storage
from common_utils import atomic_write_text


SCHEMA_VERSION = 1
MANIFEST_NAME = "current_manifest.json"
MANIFEST_PATH = Path("cache") / MANIFEST_NAME
IMPACT_PATH = Path("cache") / "current_impact.json"


def _digest(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _read_traceability_items(
    task_dir: Path,
    *,
    document_override: str | None = None,
) -> tuple[str, list[dict[str, Any]], str]:
    """Read the canonical traceability block for summaries and ID queries only."""
    path = storage.path(Path(task_dir), "traceability")
    if document_override is None and not path.is_file():
        return "NOT_PRESENT", [], ""
    raw = document_override.encode("utf-8") if document_override is not None else None
    digest = hashlib.sha256(raw).hexdigest() if raw is not None else _file_digest(path)
    try:
        value = json.loads(document_override) if document_override is not None else json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "INVALID", [], digest
    if isinstance(value, dict):
        value = value.get("items", [])
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        return "INVALID", [], digest
    return "PASSED", [item for item in value if isinstance(item, dict)], digest


def traceability_summary(task_dir: Path, *, document_override: str | None = None) -> dict[str, Any]:
    """Return a compact projection; traceability.json remains canonical."""
    path = storage.path(Path(task_dir), "traceability")
    status, items, digest = _read_traceability_items(Path(task_dir), document_override=document_override)
    by_status: dict[str, int] = {}
    covered: list[str] = []
    gaps: list[str] = []
    stale: list[str] = []
    blocking: list[str] = []
    acceptance_ids: set[str] = set()
    evidence_ids: set[str] = set()
    for item in items:
        trace_id = str(item.get("id") or "")
        item_status = str(item.get("status") or "UNKNOWN")
        by_status[item_status] = by_status.get(item_status, 0) + 1
        if trace_id and item_status == "COVERED":
            covered.append(trace_id)
        if trace_id and item_status == "GAP":
            gaps.append(trace_id)
        if trace_id and item_status == "STALE":
            stale.append(trace_id)
        if trace_id and (item.get("blocking") is True or item.get("blocking_required") is True):
            blocking.append(trace_id)
        for key in ("acceptance_criteria", "acceptance_ids"):
            values = item.get(key) or []
            if isinstance(values, list):
                acceptance_ids.update(str(value) for value in values if value)
        values = item.get("verification_evidence_ids") or []
        if isinstance(values, list):
            evidence_ids.update(str(value) for value in values if value)
    return {
        "path": str(path),
        "authority": "CANONICAL",
        "readMode": "SUMMARY_DEFAULT_FULL_ON_DEMAND",
        "status": status,
        "sourceDigest": digest,
        "total": len(items),
        "byStatus": dict(sorted(by_status.items())),
        "coveredIds": sorted(covered),
        "gapIds": sorted(gaps),
        "staleIds": sorted(stale),
        "blockingIds": sorted(blocking),
        "acceptanceIds": sorted(acceptance_ids),
        "verificationEvidenceIds": sorted(evidence_ids),
        "queryCommands": {
            "trace": "python3 scripts/wk.py context -g {CURRENT_AGENT} --task-dir {TASK_DIR} --trace-id {TRACE_ID}",
            "acceptance": "python3 scripts/wk.py context -g {CURRENT_AGENT} --task-dir {TASK_DIR} --acceptance-id {ACCEPTANCE_ID}",
        },
    }


def query_traceability(task_dir: Path, *, trace_id: str = "", acceptance_id: str = "") -> dict[str, Any]:
    """Select exact trace rows without exposing the whole traceability document."""
    status, items, digest = _read_traceability_items(Path(task_dir))
    if not trace_id and not acceptance_id:
        raise ValueError("traceability query requires --trace-id or --acceptance-id")
    matches = []
    for item in items:
        if trace_id and str(item.get("id") or "") == trace_id:
            matches.append(item)
            continue
        values = item.get("acceptance_criteria") or item.get("acceptance_ids") or []
        if acceptance_id and isinstance(values, list) and acceptance_id in {str(value) for value in values}:
            matches.append(item)
    assertions: list[dict[str, Any]] = []
    assertions_path = storage.path(Path(task_dir), "acceptance")
    if acceptance_id and assertions_path.is_file():
        try:
            document = json.loads(assertions_path.read_text(encoding="utf-8"))
            raw = document.get("assertions") if isinstance(document, dict) else []
            assertions = [
                item for item in raw
                if isinstance(item, dict) and str(item.get("acceptance_id") or "") == acceptance_id
            ]
        except (OSError, UnicodeError, json.JSONDecodeError):
            assertions = []
    return {
        "status": status if status != "PASSED" or matches else "PASSED",
        "query": {"traceId": trace_id, "acceptanceId": acceptance_id},
        "sourcePath": str(storage.path(Path(task_dir), "traceability")),
        "sourceDigest": digest,
        "count": len(matches),
        "items": matches,
        "assertions": assertions,
        "notFound": bool(status == "PASSED" and not matches),
    }


def _revision_projection(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    artifacts = state.get("artifact_revisions")
    if not isinstance(artifacts, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for phase, value in sorted(artifacts.items()):
        if not isinstance(value, dict):
            continue
        result[str(phase)] = {
            "revision": str(value.get("revision") or ""),
            "status": str(value.get("status") or ""),
            "iterationId": str(value.get("iteration_id") or ""),
            "iterationNo": value.get("iteration_no"),
        }
    return result


def _work_policy(state: dict[str, Any]) -> dict[str, Any]:
    mode = state.get("work_mode") if isinstance(state.get("work_mode"), dict) else {}
    recorded = state.get("work_policy") if isinstance(state.get("work_policy"), dict) else {}
    lightweight = bool(mode.get("lightweight"))
    waived = bool(mode.get("lightweight_waived"))
    minimal = bool(mode.get("minimal"))
    if lightweight:
        effective = "lightweight"
    elif waived:
        effective = "lightweight_closed"
    elif minimal:
        effective = "minimal"
    else:
        effective = "standard"
    mode_skipped = {str(item) for item in (mode.get("skipped_phases") or []) if isinstance(item, str)}
    overrides = recorded.get("phase_overrides") if isinstance(recorded.get("phase_overrides"), dict) else {}
    standard_skipped = {
        str(phase)
        for phase, override in overrides.items()
        if isinstance(override, dict) and override.get("status") == "SKIPPED_BY_USER"
    }
    skipped = sorted(mode_skipped | standard_skipped)
    return {
        "effectiveMode": str(recorded.get("mode") or effective),
        "lightweightActive": lightweight,
        "lightweightClosed": waived,
        "minimal": minimal,
        "skippedPhases": skipped,
        "retroactiveReviewTestRequired": False if (lightweight or waived) else None,
        "explicitReopenRequired": bool((waived and not lightweight) or standard_skipped),
        "requested": {
            key: str(recorded.get(key) or ("not_requested" if lightweight or waived else "requested"))
            for key in ("tdd", "test_design", "review", "testing")
        },
        "workRef": str(mode.get("ref") or ""),
        "workDigest": str(mode.get("digest") or ""),
    }


def build_manifest(
    task_dir: Path,
    *,
    state: dict[str, Any] | None = None,
    issues: list[dict[str, Any]] | None = None,
    validation_errors: list[str] | None = None,
    validation_warnings: list[str] | None = None,
    traceability_document: str | None = None,
) -> dict[str, Any]:
    task_dir = Path(task_dir).resolve()
    storage.assert_new_project(task_dir, require_events=False)
    state = state if isinstance(state, dict) else long_task.runtime_state(task_dir)
    projection = state.get("event_projection") if isinstance(state.get("event_projection"), dict) else {}
    event_seq = int(projection.get("source_event_seq") or 0)
    event_digest = str(projection.get("source_digest") or "")
    issues = issues if isinstance(issues, list) else long_task.runtime_issues(task_dir)
    open_issues = [
        str(item.get("id")) for item in issues
        if isinstance(item, dict) and item.get("status") in long_task.OPEN_ISSUE_STATUSES and item.get("id")
    ]
    critical = [
        str(item.get("id")) for item in issues
        if isinstance(item, dict)
        and item.get("status") in long_task.OPEN_ISSUE_STATUSES
        and item.get("severity") in {"P0", "P1"}
        and item.get("id")
    ]
    trace_summary = traceability_summary(task_dir, document_override=traceability_document)
    state_projection = {
        "taskStatus": str(state.get("task_status") or "UNKNOWN"),
        "currentPhase": str(state.get("current_phase") or ""),
        "currentRound": str(state.get("current_round") or ""),
        "currentAgent": str(state.get("current_agent") or ""),
        "nextAgent": str(state.get("next_agent") or ""),
        "nextAction": str(state.get("next_action") or ""),
        "eventSeq": event_seq,
        "eventDigest": event_digest,
        "storageMode": str(projection.get("storage_mode") or "EVENT_LEDGER_V3"),
    }
    source_inputs = {
        "taskDir": str(task_dir),
        "eventSeq": event_seq,
        "eventDigest": event_digest,
        "workDigest": str((state.get("work_mode") or {}).get("digest") or ""),
        "activeRevisions": _revision_projection(state),
        "impactDigest": _file_digest(task_dir / IMPACT_PATH),
        "traceabilityDigest": str(trace_summary.get("sourceDigest") or ""),
        "taskPlanDigest": _file_digest(storage.path(task_dir, "plan")),
    }
    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "CURRENT_MANIFEST",
        "authority": "DERIVED",
        "sourceInputDigest": _digest(source_inputs),
        "taskDir": str(task_dir),
        "state": state_projection,
        "activeRevisions": _revision_projection(state),
        "workPolicy": _work_policy(state),
        "blockers": {
            "openIssueIds": sorted(open_issues),
            "openCriticalIssueIds": sorted(critical),
            "validationErrors": sorted(str(item) for item in (validation_errors or [])),
            "validationWarnings": sorted(str(item) for item in (validation_warnings or [])),
        },
        "traceabilitySummary": trace_summary,
        "recoveryPolicy": {
            "sourceOfTruth": [
                "task_events.jsonl reducer",
                "canonical Evidence Registry",
                "canonical Review Registry",
                "exact artifact revision",
            ],
            "defaultReadSet": [
                "wk status",
                "wk next --explain",
                "wk context",
                "current revision documents only",
            ],
            "conditionalReadSet": ["cache/current_manifest.json"],
            "indirectReadSet": ["cache/current_impact.json via wk next"],
            "cacheCompatibilityRule": "Reject incompatible derived cache and rebuild from canonical events.",
            "historyRequiresExplicitRead": True,
            "ignoredForState": ["README.md", "chat memory"],
            "onDemandOnly": ["traceability.json", "evidence/registry.jsonl", "historical files"],
        },
        "derivedImpact": _derived_impact(task_dir, state),
        "freshness": {
            "sourceEventSeq": event_seq,
            "sourceEventDigest": event_digest,
            "generatedBy": "scripts/current_manifest.py",
        },
    }
    return manifest


def manifest_path(task_dir: Path) -> Path:
    return Path(task_dir) / MANIFEST_PATH


def _derived_impact(task_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    path = Path(task_dir) / IMPACT_PATH
    if not path.is_file():
        return {"path": str(path), "present": False, "fresh": False, "authority": "DERIVED"}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"path": str(path), "present": True, "fresh": False, "authority": "DERIVED", "status": "INVALID"}
    if not isinstance(value, dict):
        return {"path": str(path), "present": True, "fresh": False, "authority": "DERIVED", "status": "INVALID"}
    phase = str(value.get("phase") or "")
    current_phase = str(state.get("current_phase") or "")
    artifacts = state.get("artifact_revisions") if isinstance(state.get("artifact_revisions"), dict) else {}
    artifact = artifacts.get(phase) if isinstance(artifacts.get(phase), dict) else {}
    plan_revision = str((value.get("provenance") or {}).get("revision") or "")
    current_revision = str(artifact.get("revision") or "")
    return {
        "path": str(path),
        "present": True,
        "fresh": bool(
            Path(str(value.get("taskDir") or "")).resolve() == Path(task_dir).resolve()
            and phase == current_phase
            and str((value.get("provenance") or {}).get("baseRevision") or "") == current_revision
        ),
        "authority": "DERIVED",
        "status": str(value.get("status") or "INVALID"),
        "phase": phase,
        "revision": plan_revision,
        "revalidationRequired": bool(value.get("revalidationRequired")),
        "reasonCodes": list(value.get("reasonCodes") or []),
    }


def read_manifest(task_dir: Path) -> dict[str, Any] | None:
    path = manifest_path(task_dir)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def manifest_fresh(task_dir: Path, manifest: dict[str, Any] | None = None) -> bool:
    manifest = manifest if isinstance(manifest, dict) else read_manifest(task_dir)
    if not manifest:
        return False
    try:
        current = build_manifest(Path(task_dir))
    except (OSError, ValueError):
        return False
    return (
        manifest.get("schemaVersion") == SCHEMA_VERSION
        and manifest.get("sourceInputDigest") == current.get("sourceInputDigest")
    )


def sync_manifest(task_dir: Path) -> dict[str, Any]:
    path = manifest_path(task_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = build_manifest(Path(task_dir))
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    return {"status": "SYNCED", "path": str(path), "manifest": value}
