"""Small persistence composition helpers used by document registries."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


def load_merge_save(
    load: Callable[[], T],
    save: Callable[[T], None],
    merge: Callable[[T], R],
) -> R:
    """Load one value, apply a mutation, then persist it exactly once.

    The merge callback runs before ``save``.  If it raises, persistence is not
    attempted, which preserves the usual validation/transaction behavior of
    callers while removing repetitive load/mutate/save scaffolding.
    """

    value = load()
    result = merge(value)
    save(value)
    return result
