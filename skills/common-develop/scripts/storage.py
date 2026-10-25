"""Canonical storage contract for common-develop projects."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from common_utils import atomic_write_text


ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = ROOT / "assets" / "storage-contract.json"
CONTRACT = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


class StorageError(ValueError):
    """Raised when a project does not use the canonical storage model."""


def task_paths(task_dir: Path) -> dict[str, Path]:
    task_dir = Path(task_dir)
    files = CONTRACT["taskFiles"] | CONTRACT["derivedFiles"]
    # ``taskFiles`` may also carry non-path metadata (for example
    # ``eventsPolicy``); only string values are canonical filesystem paths.
    result = {key: task_dir / value for key, value in files.items() if isinstance(value, str)}
    result.update({key: task_dir / value for key, value in CONTRACT["evidence"].items()})
    return result


def assert_new_project(task_dir: Path, *, require_events: bool = True) -> None:
    task_dir = Path(task_dir)
    if not task_dir.is_dir():
        raise StorageError(f"task directory does not exist: {task_dir}")
    if len(task_dir.parents) >= 4:
        marker = task_dir.parents[3] / CONTRACT["projectMarker"]
        if marker.is_file():
            value = read_json(marker, default={})
            if not isinstance(value, dict) or value.get("storageModel") != CONTRACT["storageModel"]:
                raise StorageError(f"project marker is not {CONTRACT['storageModel']}: {marker}")
            marker_version = value.get("storageModelVersion")
            if marker_version is not None and marker_version != CONTRACT.get("modelVersion", CONTRACT.get("storageModelVersion")):
                raise StorageError(f"project marker storage model version is incompatible: {marker}")
    paths = task_paths(task_dir)
    if require_events and not paths["events"].is_file():
        raise StorageError("new task requires task_events.jsonl")
    if paths["plan"].exists() and not paths["plan"].is_file():
        raise StorageError("task_plan.json must be a file")


def ensure_directories(task_dir: Path) -> None:
    task_dir = Path(task_dir)
    assert_new_project(task_dir, require_events=False)
    paths = task_paths(task_dir)
    for key in ("manifest", "impact"):
        paths[key].parent.mkdir(parents=True, exist_ok=True)
    for key in ("objects", "runs"):
        paths[key].mkdir(parents=True, exist_ok=True)
    ignore = task_dir / "cache" / ".gitignore"
    if not ignore.exists():
        ignore.write_text("*\n!.gitignore\n", encoding="utf-8")


def ensure_project_marker(doc_root: Path) -> Path:
    path = Path(doc_root) / CONTRACT["projectMarker"]
    if not path.exists():
        write_json(path, {
            "schemaVersion": 1,
            "storageModel": CONTRACT["storageModel"],
            "storageModelVersion": CONTRACT.get("modelVersion", 6),
            "createdBy": "common-develop",
        })
    value = read_json(path, default={})
    if not isinstance(value, dict) or value.get("storageModel") != CONTRACT["storageModel"]:
        raise StorageError(f"project marker is not {CONTRACT['storageModel']}: {path}")
    marker_version = value.get("storageModelVersion")
    if marker_version is not None and marker_version != CONTRACT.get("modelVersion", CONTRACT.get("storageModelVersion")):
        raise StorageError(f"project marker storage model version is incompatible: {path}")
    return path


def read_json(path: Path, *, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StorageError(f"invalid JSON: {path}: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    path = Path(path)
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def path(task_dir: Path, name: str) -> Path:
    paths = task_paths(Path(task_dir))
    if name not in paths:
        raise StorageError(f"unknown canonical storage path: {name}")
    return paths[name]


__all__ = ["CONTRACT", "StorageError", "assert_new_project", "ensure_directories", "ensure_project_marker", "path", "read_json", "task_paths", "write_json"]
