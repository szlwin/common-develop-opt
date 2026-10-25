"""Existing document digest serialization (including legacy JSON policy)."""
from __future__ import annotations
import hashlib,json
from pathlib import Path
from typing import Any

def canonical_json(value: Any) -> str:
    """Serialize JSON deterministically without ASCII escaping or whitespace."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    """Return the SHA-256 digest for an in-memory byte sequence."""

    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    """Return the SHA-256 digest for UTF-8 encoded text."""

    return sha256_bytes(value.encode("utf-8"))


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a file using bounded-memory reads."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()

