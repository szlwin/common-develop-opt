"""Canonical lifecycle runtime boundary used by B4-3 consumers.

The adapter preserves the established task_events journal format while giving
canonical modules one narrow state/read and event append surface.  The old
command modules remain behind this boundary until B4-4 dual-run verification.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import state_projection
from .wk_dispatcher import COMMANDS, DispatcherError, build_parser, dispatch


def status(task_dir: Path, *, full: bool = False) -> dict[str, Any]:
    return state_projection.status(Path(task_dir), full=full)


def next_action(task_dir: Path, *, agent: str = "ProjectManagerAgent", explain: bool = False) -> dict[str, Any]:
    return state_projection.next_action(Path(task_dir), agent=agent, explain=explain)


def context(task_dir: Path, *, task_id: str = "") -> dict[str, Any]:
    return state_projection.context(Path(task_dir), task_id=task_id)


def evidence_query(task_dir: Path, **filters: Any) -> list[dict[str, Any]]:
    return state_projection.evidence_query(Path(task_dir), **filters)


def review_query(task_dir: Path) -> dict[str, Any]:
    return state_projection.review_query(Path(task_dir))


__all__ = ["status", "next_action", "context", "evidence_query", "review_query", "COMMANDS", "DispatcherError", "build_parser", "dispatch"]
