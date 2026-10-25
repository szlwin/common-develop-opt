"""Shared typed-value validation helpers for structured document modules."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def make_require_helpers(
    error_type: type[Exception],
    *,
    mapping_requirement: str = "a mapping",
    list_requirement: str = "a list",
    text_requirement: str = "a non-empty string",
) -> tuple[
    Callable[[Any, str], dict[str, Any]],
    Callable[[Any, str], list[Any]],
    Callable[[Any, str], str],
]:
    """Create validators that retain each caller's domain exception and wording."""

    def require_mapping(value: Any, label: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise error_type(f"{label} must be {mapping_requirement}")
        return value

    def require_list(value: Any, label: str) -> list[Any]:
        if not isinstance(value, list):
            raise error_type(f"{label} must be {list_requirement}")
        return value

    def require_text(value: Any, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise error_type(f"{label} must be {text_requirement}")
        return value.strip()

    return require_mapping, require_list, require_text
