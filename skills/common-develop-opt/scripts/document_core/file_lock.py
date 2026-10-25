"""Portable non-blocking file locks used by package writers.

The runtime deliberately keeps the lock contract small: callers provide an
open, writable file and receive a non-blocking exclusive lock for the
duration of the context.  POSIX hosts use ``fcntl.flock``; Windows hosts use
the CRT ``msvcrt.locking`` API.  Contention is normalised to
``BlockingIOError`` so the lifecycle callers keep their existing fail-closed
error handling.
"""
from __future__ import annotations

import errno
import os
from contextlib import contextmanager
from typing import IO, Iterator

if os.name == "nt":  # pragma: no cover - exercised on Windows CI
    import msvcrt
else:  # pragma: no cover - the alternate branch is exercised on POSIX CI
    import fcntl


def _ensure_windows_lock_byte(handle: IO[bytes]) -> None:
    """Make the one-byte range used by ``msvcrt.locking`` addressable."""

    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"\0")
        handle.flush()
    handle.seek(0)


def _contention_error(exc: OSError) -> BlockingIOError:
    """Translate platform-specific lock contention into one stable error."""

    return BlockingIOError(
        errno.EACCES,
        "file lock is already held",
    )


@contextmanager
def nonblocking_file_lock(handle: IO[bytes]) -> Iterator[None]:
    """Hold an exclusive, non-blocking lock on ``handle``.

    The caller owns opening and closing the handle.  The lock file is kept on
    disk so independent processes contend on the same path; releasing the
    context always unlocks it, including when the protected operation fails.
    """

    if os.name == "nt":  # pragma: no cover - exercised on Windows CI
        _ensure_windows_lock_byte(handle)
        acquired = False
        try:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if (
                    isinstance(exc, BlockingIOError)
                    or getattr(exc, "winerror", None) in {32, 33}
                    or exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
                ):
                    raise _contention_error(exc) from exc
                raise
            acquired = True
            yield
        finally:
            if acquired:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise
    try:
        yield
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


__all__ = ["nonblocking_file_lock"]
