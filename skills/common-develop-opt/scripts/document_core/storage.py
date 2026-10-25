"""Canonical storage paths for document-core registries."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from resource_paths import asset_path
from .digests import canonical_json

CONTRACT_PATH = asset_path("assets/storage/storage-contract.json")
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


class StorageError(ValueError):
    """Raised when a task does not satisfy the canonical storage contract."""


def task_paths(task_dir: Path) -> dict[str, Path]:
    task_dir = Path(task_dir)
    files = {**CONTRACT["taskFiles"], **CONTRACT["derivedFiles"]}
    result = {key: task_dir / value for key, value in files.items() if isinstance(value, str)}
    result.update({key: task_dir / value for key, value in CONTRACT["evidence"].items()})
    return result


def assert_new_project(task_dir: Path, *, require_events: bool = True) -> None:
    task_dir = Path(task_dir)
    if not task_dir.is_dir():
        raise StorageError(f"task directory does not exist: {task_dir}")
    paths = task_paths(task_dir)
    if require_events and not paths["events"].is_file():
        raise StorageError("new task requires task_events.jsonl")
    if paths["plan"].exists() and not paths["plan"].is_file():
        raise StorageError("task_plan.json must be a file")


def read_json(path: Path, *, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StorageError(f"invalid JSON: {path}: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    from .persistence import atomic_write_text
    atomic_write_text(Path(path), json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def path(task_dir: Path, name: str) -> Path:
    paths = task_paths(Path(task_dir))
    if name not in paths:
        raise StorageError(f"unknown canonical storage path: {name}")
    return paths[name]


def ensure_registry_parent(task_dir: Path) -> None:
    assert_new_project(Path(task_dir), require_events=False)
    path(task_dir, "objects").mkdir(parents=True, exist_ok=True)
    path(task_dir, "runs").mkdir(parents=True, exist_ok=True)


__all__ = ["CONTRACT", "StorageError", "assert_new_project", "ensure_registry_parent", "path", "read_json", "task_paths", "write_json"]


def ensure_directories(task_dir):
    ensure_registry_parent(task_dir)
    paths = task_paths(task_dir)
    for key in ("manifest", "impact"):
        paths[key].parent.mkdir(parents=True, exist_ok=True)


def ensure_project_marker(doc_root):
    marker = Path(doc_root) / CONTRACT["projectMarker"]
    marker.parent.mkdir(parents=True, exist_ok=True)
    if not marker.exists():
        write_json(marker, {
            "schemaVersion": 1,
            "storageModel": CONTRACT["storageModel"],
            "storageModelVersion": CONTRACT.get("modelVersion", 6),
            "createdBy": "common-develop",
        })
    return marker
