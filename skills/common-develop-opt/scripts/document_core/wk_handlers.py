"""Canonical wk handler registry for B4-4 step 3.

Handlers are injected explicitly so the dispatcher has no implicit module-name
lookups. The current registry is an isolated transition seam for the existing
runtime; each handler can be replaced independently in the next cutover step.
"""
from __future__ import annotations

from argparse import Namespace
from typing import Callable

from .wk_dispatcher import COMMANDS


class CanonicalHandlerUnavailable(RuntimeError):
    """Raised when a command has not yet acquired a canonical implementation."""


def _read_status(args: Namespace) -> int:
    from . import runtime
    import json
    print(json.dumps(runtime.status(args.task_dir, full=bool(getattr(args, "full", False))), ensure_ascii=False, sort_keys=True))
    return 0


def _read_next(args: Namespace) -> int:
    from . import runtime
    import json
    print(json.dumps(runtime.next_action(args.task_dir, agent=args.agent, explain=bool(getattr(args, "explain", False))), ensure_ascii=False, sort_keys=True))
    return 0


def _read_context(args: Namespace) -> int:
    from . import runtime
    import json
    print(json.dumps(runtime.context(args.task_dir, task_id=getattr(args, "task_id", "")), ensure_ascii=False, sort_keys=True))
    return 0


for _handler in (_read_status, _read_next, _read_context):
    # The projection layer still imports the legacy reducer.  This metadata
    # prevents a thin wrapper from being counted as an independent migration.
    _handler.canonical_independent = False
    _handler.dependency_blocker = "document_core.state_projection imports compat lifecycle reducer"


def build_canonical_registry() -> dict[str, Callable[[Namespace], int]]:
    """Return only handlers implemented in document_core.

    Keeping the registry intentionally incomplete makes the cutover gate
    fail closed: a legacy callback cannot be mistaken for a migrated handler.
    """
    return {"status": _read_status, "next": _read_next, "context": _read_context}


def build_production_registry() -> dict[str, Callable[[Namespace], int]]:
    """Return a complete registry for the public ``wk`` entry.

    Every command in ``COMMANDS`` resolves to a callable: canonical handlers
    are used where they exist and are independent; the remaining commands are
    served by the established runtime facade so the dispatcher never routes a
    parsed command to a missing handler. Handlers are sourced through the
    facade (not the internal module) so patched callbacks keep working during
    the cutover window. A missing command here is a packaging bug, not a
    runtime state, so it raises instead of reporting.
    """
    import sys
    from pathlib import Path

    compat = Path(__file__).resolve().parents[1] / "compat"
    if str(compat) not in sys.path:
        sys.path.insert(0, str(compat))
    from runtime import wk

    registry = build_registry(wk)
    canonical = build_canonical_registry()
    for command, handler in canonical.items():
        if getattr(handler, "canonical_independent", False):
            registry[command] = handler
    missing_commands = missing(registry)
    if missing_commands:
        raise CanonicalHandlerUnavailable(
            f"production registry incomplete: {missing_commands}"
        )
    return registry


def build_registry(source: object) -> dict[str, Callable[[Namespace], int]]:
    registry: dict[str, Callable[[Namespace], int]] = {}
    for command in COMMANDS:
        name = f"command_{command.replace('-', '_')}"
        handler = getattr(source, name, None)
        if callable(handler):
            registry[command] = handler
    return registry


def missing(registry: dict[str, Callable[[Namespace], int]]) -> list[str]:
    return [command for command in COMMANDS if command not in registry]


__all__ = ["CanonicalHandlerUnavailable", "build_canonical_registry", "build_production_registry", "build_registry", "missing"]
