"""Explicit unittest markers consumed by the quality-gate AST discovery."""
from __future__ import annotations

from typing import TypeVar


T = TypeVar("T")


def slow(test: T) -> T:
    """Mark an expensive test without changing unittest execution semantics."""
    setattr(test, "__slow__", True)
    return test


__all__ = ["slow"]
