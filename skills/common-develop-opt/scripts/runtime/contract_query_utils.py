#!/usr/bin/env python3
"""Small, bounded helpers for querying large machine contracts safely."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DEFAULT_MAX_SELECTION_BYTES = 24_576
HARD_MAX_SELECTION_BYTES = 65_536


class ContractQueryError(ValueError):
    """Raised when a contract query is invalid or too broad."""


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractQueryError(f"cannot load JSON contract {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractQueryError(f"JSON contract must be an object: {path}")
    return value


def decode_pointer_token(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def select_pointer(document: Any, pointer: str) -> Any:
    """Resolve a non-root RFC 6901 JSON Pointer."""
    if pointer in {"", "/"}:
        raise ContractQueryError("root or whole-document queries are forbidden; use a deeper JSON Pointer")
    if not pointer.startswith("/"):
        raise ContractQueryError("JSON Pointer must start with '/'")
    current = document
    for raw in pointer.split("/")[1:]:
        token = decode_pointer_token(raw)
        if isinstance(current, dict):
            if token not in current:
                raise ContractQueryError(f"JSON Pointer does not exist: {pointer}")
            current = current[token]
        elif isinstance(current, list):
            if not token.isdigit():
                raise ContractQueryError(f"array JSON Pointer token must be an index: {token}")
            index = int(token)
            if index >= len(current):
                raise ContractQueryError(f"array JSON Pointer is out of range: {pointer}")
            current = current[index]
        else:
            raise ContractQueryError(f"JSON Pointer traverses a scalar value: {pointer}")
    return current


def bounded_selection(value: Any, *, max_bytes: int = DEFAULT_MAX_SELECTION_BYTES) -> Any:
    if max_bytes <= 0 or max_bytes > HARD_MAX_SELECTION_BYTES:
        raise ContractQueryError(
            f"max output bytes must be between 1 and {HARD_MAX_SELECTION_BYTES}"
        )
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > max_bytes:
        raise ContractQueryError(
            f"selected contract value is {len(encoded)} bytes, above limit {max_bytes}; use a deeper pointer or narrower query"
        )
    return value


def section_summary(document: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for key, value in document.items():
        item: dict[str, Any] = {"id": key, "type": type(value).__name__}
        if isinstance(value, (dict, list)):
            item["itemCount"] = len(value)
        result.append(item)
    return result


def print_payload(payload: Any, *, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif isinstance(payload, dict):
        for key, value in payload.items():
            if isinstance(value, (dict, list)):
                print(f"{key}: {json.dumps(value, ensure_ascii=False, separators=(',', ':'))}")
            else:
                print(f"{key}: {value}")
    elif isinstance(payload, list):
        for item in payload:
            print(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
    else:
        print(payload)
