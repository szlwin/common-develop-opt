"""Atomic persistence and task-scoped locking for document-core writers."""
from __future__ import annotations

import inspect
import os
import tempfile
import threading
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from typing import Iterator
from collections.abc import Callable
from typing import TypeVar

from .digests import canonical_json, sha256_bytes, sha256_file
from .file_lock import nonblocking_file_lock

_LOCK_STATE = threading.local()
T = TypeVar("T")
R = TypeVar("R")


def load_merge_save(load: Callable[[], T], save: Callable[[T], None], merge: Callable[[T], R]) -> R:
    value = load()
    result = merge(value)
    save(value)
    return result


def atomic_write_bytes(path: Path, data: bytes) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    temporary = Path(name)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(Path(path), text.encode("utf-8"))


@contextmanager
def task_writer_lock(task_dir: Path) -> Iterator[Path]:
    task = Path(task_dir).resolve()
    if not task.is_dir():
        raise ValueError(f"task directory does not exist: {task}")
    lock_path = task / "cache" / "amend-phase.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    held = getattr(_LOCK_STATE, "held", None)
    if held is None:
        held = {}
        _LOCK_STATE.held = held
    key = str(lock_path)
    if key in held:
        handle, depth = held[key]
        held[key] = (handle, depth + 1)
        try:
            yield lock_path
        finally:
            handle, depth = held[key]
            held[key] = (handle, depth - 1)
            if depth <= 1:
                held.pop(key, None)
        return
    with lock_path.open("a+b") as handle:
        try:
            with nonblocking_file_lock(handle):
                held[key] = (handle, 1)
                try:
                    yield lock_path
                finally:
                    held.pop(key, None)
        except BlockingIOError as exc:
            raise ValueError(
                f"another amend-phase writer (task lifecycle writer) holds the task lock: {lock_path}"
            ) from exc


def task_write_locked(function):
    first_parameter = next(iter(inspect.signature(function).parameters))

    @wraps(function)
    def wrapped(*args, **kwargs):
        first = args[0] if args else kwargs.get(first_parameter)
        if first is None:
            return function(*args, **kwargs)
        task_dir = Path(first.task_dir) if hasattr(first, "task_dir") else Path(first)
        with task_writer_lock(task_dir):
            return function(*args, **kwargs)

    return wrapped


def now() -> str:
    from datetime import datetime
    return datetime.now().astimezone().isoformat(timespec="seconds")


__all__ = ["atomic_write_bytes", "atomic_write_text", "canonical_json", "load_merge_save", "now", "sha256_bytes", "sha256_file", "task_write_locked", "task_writer_lock"]
