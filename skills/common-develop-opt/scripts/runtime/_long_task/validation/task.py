"""Validation task for the internal runtime."""
from __future__ import annotations
import re
from runtime import doc_layout
from document_core import evidence
from runtime import work_record
from document_core import acceptance
from runtime import risk_detect
from document_core import review_store
from runtime import request_intake
from document_core import task_plan
from document_core import storage
from runtime._long_task import task_events
from pathlib import Path
from typing import Any, Optional
from runtime._long_task.contracts import *
from runtime._long_task.record_io import *
from runtime._long_task.review_validation import *
from runtime._long_task.architecture_gate import expected_required_reviewers, validate_architecture_review_state
from runtime._long_task.phase_policy import phase_predecessors, phase_skip_allowed, validate_work_mode_state
from runtime._long_task.state_access import (
    load_task_history,
    runtime_issues,
    runtime_state,
    task_state_path,
)
from runtime._long_task.validation.acceptance import validate_acceptance_contract
from runtime._long_task.validation.attempt import validate_attempt_history
from runtime._long_task.validation.risk import is_current_risk_reviewer_registration, validate_risk_gate
from runtime._long_task.validation.task_plan import validate_task_plan
from runtime._long_task.validation.task_inputs import _validate_task_section_1
from runtime._long_task.validation.task_consistency import _validate_task_section_2, _validate_task_section_3
from runtime._long_task.validation.task_completion import _validate_task_section_4, _validate_task_section_5
from runtime._long_task.validation.task_context import TaskValidationContext
from runtime._long_task.amendment_validation import validate_amendment_history

def validate_task(task_dir: Path, *, prevalidated: Optional[dict[str, Any]]=None) -> tuple[list[str], list[str], dict[str, Any], list[dict[str, Any]]]:
    prevalidated = prevalidated or {}
    errors: list[str] = []
    (layout_errors, _) = prevalidated.get('layout', doc_layout.task_layout_errors(task_dir))
    errors.extend(layout_errors)
    warnings: list[str] = []
    trace_path = storage.path(task_dir, 'traceability')
    evidence_path = evidence.registry_path(task_dir)
    acceptance_path = acceptance.assertions_path(task_dir)
    risk_path = risk_detect.report_path(task_dir)
    for required_path in (evidence_path, acceptance_path, risk_path):
        if not required_path.is_file():
            errors.append(f'missing required file: {required_path}')
    history = load_task_history(task_dir, errors)
    state = runtime_state(task_dir, errors=errors)
    issues = runtime_issues(task_dir, errors=errors)
    if not state:
        errors.append('task runtime reducer produced no state; task_events.jsonl is required')
        return (errors, warnings, state, issues)
    state_path = task_state_path(task_dir)
    if not state_path.is_file():
        warnings.append(f'current manifest is absent and rebuildable from task_events.jsonl: {state_path}')
    schema_version = state.get('schema_version')
    if schema_version != LONG_TASK_SCHEMA_VERSION:
        errors.append(f'unsupported schema_version: {schema_version}; expected {LONG_TASK_SCHEMA_VERSION}')
        return (errors, warnings, state, issues)

    ctx = TaskValidationContext(
        task_dir=task_dir,
        errors=errors,
        warnings=warnings,
        history=history,
        state=state,
        issues=issues,
        prevalidated=prevalidated,
    )
    ctx.bind(path=risk_path, schema_version=schema_version, trace_path=trace_path)

    section_1 = _validate_task_section_1(ctx)
    section_1_names = (
        'acceptance_assertions', 'actual_mode', 'artifacts', 'attempts',
        'collaboration_reviewers', 'current_artifact_revisions', 'current_phase',
        'evidence_entries', 'item', 'key', 'minimal_mode', 'outcomes', 'phase',
        'phases', 'risk_report', 'tasks', 'trace_ids', 'traceability', 'value',
        'work_events',
    )
    ctx.bind(**dict(zip(section_1_names, section_1)))

    _validate_task_section_2(ctx)
    _validate_task_section_3(ctx)

    # Validate the entire amendment chain on every ordinary task validation;
    # waiting until finalize would allow a malformed event to sit in the
    # authoritative ledger and confuse the next session.
    validate_amendment_history(
        task_dir, state, history, ctx.value('tasks'), ctx.value('traceability'),
        ctx.value('acceptance_assertions'), ctx.value('evidence_entries'), errors,
    )

    section_4 = _validate_task_section_4(ctx)
    ctx.bind(**dict(zip((
        'current_outcomes', 'handoffs', 'key', 'outcome',
        'passed_completion_outcome', 'phase', 'prefix',
    ), section_4)))
    _validate_task_section_5(ctx)
    return (errors, warnings, state, issues)
