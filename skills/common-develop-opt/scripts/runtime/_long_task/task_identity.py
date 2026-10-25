"""Portable task identity for persisted lifecycle records."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any


def portable_task_path(task_dir: Path) -> str:
    """Return a stable task path relative to the project document root."""
    resolved = Path(task_dir).resolve()
    parts = resolved.parts
    for index, part in enumerate(parts):
        if part == "version" and index + 3 < len(parts) and parts[index + 2] == "task":
            return PurePosixPath(*parts[index:]).as_posix()
    return PurePosixPath("task", resolved.name).as_posix()


def fingerprint_binding(payload: dict[str, Any]) -> dict[str, str]:
    """Preserve the exact binding shape used when an authorization was signed."""
    if "taskPath" in payload:
        return {"taskPath": str(payload.get("taskPath") or "")}
    return {"taskDir": str(payload.get("taskDir") or "")}


def binding_matches(payload: dict[str, Any], task_dir: Path) -> bool:
    """Match current portable bindings and relocated legacy absolute bindings."""
    expected = portable_task_path(task_dir)
    if "taskPath" in payload:
        candidate = str(payload.get("taskPath") or "").replace("\\", "/")
        path = PurePosixPath(candidate)
        return bool(candidate) and not path.is_absolute() and ".." not in path.parts and path.as_posix() == expected

    legacy = str(payload.get("taskDir") or "").replace("\\", "/").rstrip("/")
    marker = "/version/"
    if marker not in legacy:
        return False
    candidate = "version/" + legacy.split(marker, 1)[1]
    return PurePosixPath(candidate).as_posix() == expected
