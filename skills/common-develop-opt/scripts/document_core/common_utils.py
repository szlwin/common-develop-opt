"""Small deterministic helpers shared by common-develop runtime scripts."""

from __future__ import annotations

from .digests import canonical_json, sha256_bytes, sha256_text, sha256_file
import inspect
from functools import wraps
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any


def now() -> str:
    """Return the current local timestamp in the canonical runtime format."""

    return datetime.now().astimezone().isoformat(timespec="seconds")










def restore_files(originals: dict[Path, bytes | None]) -> dict[str, Any]:
    """Best-effort restore for a failed multi-file transaction.

    The returned report is intentionally data-only so callers can attach it to
    the original business exception without masking that exception.
    """
    recovered: list[str] = []
    errors: list[str] = []
    for raw_path, content in originals.items():
        path = Path(raw_path)
        try:
            if content is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write_bytes(path, content)
            recovered.append(str(path))
        except OSError as exc:
            errors.append(f"{path}: {exc}")
    return {
        "rollbackStatus": "COMPLETE" if not errors else ("PARTIAL" if recovered else "FAILED"),
        "recoveredPaths": recovered,
        "rollbackErrors": errors,
    }


def attach_rollback_details(exc: BaseException, details: dict[str, Any]) -> None:
    """Attach rollback diagnostics while preserving the original exception."""
    setattr(exc, "rollback_details", details)
    if details.get("rollbackErrors"):
        setattr(exc, "rollback_errors", list(details["rollbackErrors"]))


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically replace a file with fsync and safe temporary-file cleanup."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        # Persist the directory entry when the platform supports directory fsync.
        try:
            dir_fd = os.open(target.parent, os.O_RDONLY)
        except OSError:
            dir_fd = -1
        if dir_fd >= 0:
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    finally:
        temporary.unlink(missing_ok=True)



def atomic_write_text(path: Path, text: str) -> None:
    """Atomically replace a UTF-8 text file after creating its parent directory."""

    atomic_write_bytes(path, text.encode("utf-8"))


def task_write_locked(function):
    """Serialize a task writer, including its read/merge phase, with lifecycle writers."""
    first_parameter = next(iter(inspect.signature(function).parameters))
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            from runtime._long_task.amendment_validation import task_writer_lock
        except ImportError:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compat"))
            from runtime._long_task.amendment_validation import task_writer_lock
        first = args[0] if args else kwargs.get(first_parameter)
        if first is None:
            return function(*args, **kwargs)
        task_dir = Path(first.task_dir) if hasattr(first, "task_dir") else Path(first)
        with task_writer_lock(task_dir):
            return function(*args, **kwargs)
    return wrapped
