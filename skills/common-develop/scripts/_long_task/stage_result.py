"""Validation and mapping for the Agent StageResult contract.

StageResult is the Agent-facing handoff format. StageOutcome is the runtime
state/event format and is deliberately built from a validated StageResult
instead of being treated as the same object.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Iterable

try:
    import jsonschema
except ImportError:  # pragma: no cover - canonical environments include it
    jsonschema = None


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "assets" / "stage-result.schema.json"


class StageResultError(ValueError):
    """A StageResult is structurally or semantically invalid."""


def _schema() -> dict[str, Any]:
    try:
        value = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StageResultError(f"cannot load StageResult schema: {exc}") from exc
    if not isinstance(value, dict):
        raise StageResultError("StageResult schema must be an object")
    return value


def validate_stage_result(
    value: dict[str, Any],
    *,
    expected_phase: str = "",
    expected_agent: str = "",
    expected_revision: str = "",
    expected_input_revisions: dict[str, str] | None = None,
    expected_evidence_ids: Iterable[str] | None = None,
    expected_changed_files: Iterable[str] | None = None,
    expected_trace_refs: Iterable[str] | None = None,
    expected_open_issue_ids: Iterable[str] | None = None,
    expected_risks: Iterable[str] | None = None,
    expected_next_action: str = "",
    expected_next_agent: str = "",
) -> list[str]:
    """Return schema and current-lifecycle binding errors for one result."""
    if not isinstance(value, dict):
        return ["StageResult must be an object"]
    if jsonschema is None:
        return ["jsonschema is required to validate StageResult"]
    errors: list[str] = []
    validator = jsonschema.Draft202012Validator(_schema())
    for error in sorted(validator.iter_errors(value), key=lambda item: tuple(str(part) for part in item.path)):
        location = ".".join(str(item) for item in error.path) or "$"
        errors.append(f"StageResult schema {location}: {error.message}")

    def compare_string(field: str, expected: str) -> None:
        if expected and value.get(field) != expected:
            errors.append(
                f"StageResult.{field} must match current value: "
                f"expected={expected!r} actual={value.get(field)!r}"
            )

    def compare_list(field: str, expected: Iterable[str] | None) -> None:
        if expected is None:
            return
        actual = list(value.get(field) or [])
        wanted = list(dict.fromkeys(str(item) for item in expected))
        if actual != wanted:
            errors.append(
                f"StageResult.{field} must match current value: "
                f"expected={wanted!r} actual={actual!r}"
            )

    compare_string("phase", expected_phase)
    compare_string("produced_by_agent", expected_agent)
    compare_string("output_revision", expected_revision)
    if expected_input_revisions is not None and value.get("input_revisions") != expected_input_revisions:
        errors.append(
            "StageResult.input_revisions must match current predecessors: "
            f"expected={expected_input_revisions!r} actual={value.get('input_revisions')!r}"
        )
    compare_list("evidence_ids", expected_evidence_ids)
    compare_list("changed_files", expected_changed_files)
    compare_list("trace_refs", expected_trace_refs)
    compare_list("open_issue_ids", expected_open_issue_ids)
    compare_list("risks", expected_risks)
    compare_string("next_action", expected_next_action)
    compare_string("next_agent", expected_next_agent)
    return errors


def map_to_stage_outcome(stage_result: dict[str, Any], lifecycle_fields: dict[str, Any]) -> dict[str, Any]:
    """Map shared fields into a runtime StageOutcome.

    ``trace_refs``, ``changed_files`` and ``open_issue_ids`` remain on the
    Agent-facing result/event payload. Runtime StageOutcome owns only the
    lifecycle fields declared by record-contract.json; ``risks`` maps to its
    canonical ``open_risks`` field.
    """
    outcome = copy.deepcopy(lifecycle_fields)
    for field in ("phase", "produced_by_agent", "input_revisions", "output_revision", "evidence_ids"):
        outcome[field] = copy.deepcopy(stage_result[field])
    outcome["open_risks"] = list(stage_result["risks"])
    return outcome
