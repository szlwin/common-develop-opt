"""State access for the internal runtime."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import re
import sys
import agent_context
import work_mode
import doc_layout
import evidence
import work_record
import acceptance
import risk_detect
import review_store
import request_intake
import task_plan
import cli_help
import storage
from _long_task import task_events
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from common_utils import now, sha256_file
from _long_task.contracts import *
from _long_task.record_io import *
from _long_task.review_validation import *
def read_task_state_cache(task_dir: Path, *, authoritative: bool = False) -> tuple[str, dict[str, Any]]:
    """Read the compact JSON manifest; the event ledger remains authoritative."""
    path = task_state_path(task_dir)
    if path.is_file():
        manifest = storage.read_json(path, default={})
        document = json.dumps(manifest, ensure_ascii=False, separators=(',', ':'))
        if task_events.event_path(task_dir).is_file():
            # Return an isolated projection so callers cannot mutate the
            # reducer-derived authority in place and mistake it for a write.
            return document, copy.deepcopy(runtime_state(task_dir))
    state = runtime_state(task_dir)
    if not state:
        raise ValueError('task event reducer produced no state for cache rebuild')
    target = str(state.get('target_id') or Path(task_dir).name)
    document = json.dumps({'target_id': target, 'authority': 'DERIVED'}, ensure_ascii=False)
    return document, copy.deepcopy(state)


def read_static_task_plan(task_dir: Path) -> tuple[str, list[dict[str, Any]]]:
    storage.assert_new_project(Path(task_dir), require_events=False)
    path = storage.path(Path(task_dir), 'plan')
    value = storage.read_json(path, default={})
    if not path.exists():
        return '{}', []
    tasks = value.get('tasks') if isinstance(value, dict) else None
    if not isinstance(tasks, list):
        raise ValueError('task-plan must be a JSON array')
    document = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    return document, [copy.deepcopy(item) for item in tasks if isinstance(item, dict)]


def append_task_events_payloads(
    task_dir: Path,
    state_path: Path,
    state_document: str,
    state: dict[str, Any],
    specs: list[dict[str, Any]],
    *,
    synced_at: str,
) -> tuple[dict[Path, str], dict[str, Any], dict[str, Any]]:
    """Append events, persist a state patch, and rebuild the manifest atomically."""
    if not task_events.event_path(task_dir).is_file():
        raise ValueError('task_events.jsonl is required')
    before = task_events.reduce_events(task_events.read_events(task_dir))
    patch = task_events.state_patch_spec(
        before, state, timestamp=synced_at, source='long_task.py state-projection'
    )
    combined = ([patch] if patch is not None else []) + list(specs)
    ledger_text, _created, events = task_events.append_many_text(task_dir, combined)
    reduced = task_events.reduce_events(events)
    projected = task_events.project_state(state, reduced, synced_at=synced_at)
    payloads: dict[Path, str] = {
        task_events.event_path(task_dir): ledger_text,
    }
    # Keep the compact cross-session recovery projection in the same write set.
    # It is derived cache only; task_events.jsonl remains the sole mutable authority.
    import current_manifest
    manifest_path = current_manifest.manifest_path(task_dir)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest = current_manifest.build_manifest(
        task_dir,
        state=projected,
        issues=[item for item in reduced.get('issues', []) if isinstance(item, dict)],
    )
    payloads[manifest_path] = (
        json.dumps(
            manifest,
            ensure_ascii=False,
            indent=2,
        ) + "\n"
    )
    return payloads, projected, reduced


def runtime_issues(task_dir: Path, *, errors: Optional[list[str]] = None) -> list[dict[str, Any]]:
    reduced = runtime_reduction(task_dir, errors)
    issues = reduced.get('issues') if isinstance(reduced.get('issues'), list) else []
    if issues:
        return [copy.deepcopy(item) for item in issues if isinstance(item, dict)]
    try:
        return task_events.current_issues(task_dir)
    except (OSError, ValueError) as exc:
        if errors is not None:
            errors.append(str(exc))
            return []
        raise


def resolve_task_ref(task_dir: Path, ref: str) -> Path:
    """Resolve a task reference without permitting root or symlink escape.

    References retain the historical task/version/document-root lookup order,
    but every candidate is resolved first and checked against the project root.
    The final file must remain inside that root even when an intermediate path is
    a symlink.
    """
    clean = str(ref or '').split('#', 1)[0].strip()
    if not clean:
        raise ValueError('empty file reference')
    task_root = Path(task_dir).resolve()
    version_root = version_root_from_task_dir(task_root).resolve()
    doc_root = version_root.parent.parent.resolve()
    project_root = doc_root.parent.resolve()
    candidate = Path(clean)
    candidates = [candidate] if candidate.is_absolute() else [
        task_root / candidate,
        version_root / candidate,
        doc_root / candidate,
        project_root / candidate,
    ]

    escaped = False
    for current in candidates:
        resolved = current.resolve(strict=False)
        try:
            resolved.relative_to(project_root)
        except ValueError:
            escaped = True
            continue
        if resolved.is_file():
            return resolved
    if escaped:
        raise ValueError(f'file reference escapes project root: {ref}')
    raise ValueError(f'file reference does not resolve: {ref}')


def load_task_history(task_dir: Path, errors: Optional[list[str]] = None) -> dict[str, Any]:
    """Load the current task event history."""
    try:
        if not task_events.event_path(task_dir).is_file():
            raise ValueError('task_events.jsonl is required')
        return task_events.reduce_events(task_events.read_events(task_dir))
    except (OSError, ValueError) as exc:
        if errors is not None:
            errors.append(str(exc))
            return {
                "state_authority": {}, "attempts": [], "outcomes": [], "stale_events": [],
                "auto_remediation_events": [], "authorization_events": [], "authorization_consumptions": [], "amendment_events": [], "traceability_revalidations": [], "issues": [], "task_runtime": {}, "task_policies": {},
                "last_seq": 0, "last_digest": "",
            }
        raise


def runtime_tasks(task_dir: Path, *, errors: Optional[list[str]] = None) -> list[dict[str, Any]]:
    try:
        _, tasks = read_static_task_plan(task_dir)
    except (OSError, ValueError) as exc:
        if errors is not None:
            errors.append(str(exc))
            return []
        raise
    return task_events.materialize_tasks(tasks, runtime_reduction(task_dir, errors))


def runtime_state(task_dir: Path, *, errors: Optional[list[str]] = None) -> dict[str, Any]:
    """Build current state from reducer authority; cache is rebuildable only."""
    try:
        storage.assert_new_project(Path(task_dir), require_events=False)
    except storage.StorageError as exc:
        if errors is not None:
            errors.append(str(exc))
            return {}
        raise
    reduced = runtime_reduction(task_dir, errors)
    return task_events.project_state({}, reduced, synced_at='')


def task_state_path(task_dir: Path) -> Path:
    """Return the only compact derived cache path."""
    return storage.path(Path(task_dir), 'manifest')


def runtime_reduction(task_dir: Path, errors: Optional[list[str]] = None) -> dict[str, Any]:
    """Load the current reducer output from the task event ledger."""
    return load_task_history(Path(task_dir), errors)
