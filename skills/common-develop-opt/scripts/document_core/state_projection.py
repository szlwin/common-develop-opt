"""Read-only projections over the canonical lifecycle runtime boundary."""
from __future__ import annotations

from pathlib import Path
import sys
import importlib.util
from typing import Any

_COMPAT = Path(__file__).resolve().parent.parent / "compat"
_RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
if str(_RUNTIME.parent) not in sys.path:
    sys.path.insert(0, str(_RUNTIME.parent))
_WK_PATH = _RUNTIME / "wk.py"
_WK_SPEC = importlib.util.spec_from_file_location("_document_core_legacy_wk", _WK_PATH)
if _WK_SPEC is None or _WK_SPEC.loader is None:
    raise ImportError(f"cannot load lifecycle projection source: {_WK_PATH}")
wk = importlib.util.module_from_spec(_WK_SPEC)
sys.modules[_WK_SPEC.name] = wk
_WK_SPEC.loader.exec_module(wk)
from . import evidence, review_store


def status(task_dir: Path, *, full: bool = False) -> dict[str, Any]:
    """Return the legacy status projection without invoking a writer."""
    return wk._status_payload(Path(task_dir), full=full, include_next=False)


def next_action(task_dir: Path, *, agent: str = "ProjectManagerAgent", explain: bool = False) -> dict[str, Any]:
    """Return the next legal action projection without applying it."""
    task_dir = Path(task_dir)
    payload = status(task_dir)
    return wk._safe_next_payload(task_dir, payload, agent, explain=explain)


def context(task_dir: Path, *, task_id: str = "") -> dict[str, Any]:
    """Reduce task events into context; this function performs no writes."""
    task_dir = Path(task_dir)
    result = wk.long_task.build_task_context(task_dir, task_id=task_id)
    result["source"] = "task_events.jsonl reducer"
    result.update(wk._current_manifest_ref(task_dir, state=wk.long_task.runtime_state(task_dir)))
    return result


def evidence_query(task_dir: Path, **filters: Any) -> list[dict[str, Any]]:
    """Return a bounded evidence query using the canonical read path."""
    registry = Path(task_dir) / "evidence" / "registry.jsonl"
    if not registry.exists():
        return []
    return evidence.query_entries(Path(task_dir), **filters)


def review_query(task_dir: Path) -> dict[str, Any]:
    """Return validated review records without changing the registry."""
    errors, records = review_store.validate_registry(Path(task_dir))
    return {"status": "PASSED" if not errors else "FAILED", "errors": errors, "records": list(records.values())}
