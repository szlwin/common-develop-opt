"""Evidence gate for the internal runtime."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import re
import sys
import agent_context
import work_mode
import doc_layout
import evidence
import work_record
import acceptance
import risk_detect
import review_store
import request_intake
import task_plan
import cli_help
import storage
from _long_task import task_events
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from common_utils import now, sha256_file
from _long_task.contracts import *
from _long_task.record_io import *
from _long_task.review_validation import *

from _long_task.phase_policy import actual_work_mode_state, phase_predecessors, phase_skip_allowed
from _long_task.state_access import resolve_task_ref
def open_critical_issue_ids(issues: list[dict[str, Any]]) -> list[str]:
    return [
        str(item.get('id')) for item in issues
        if isinstance(item, dict)
        and item.get('status') in OPEN_ISSUE_STATUSES
        and item.get('severity') in {'P0', 'P1'}
    ]


def blocking_issue_ids_for_phase_revision(
    issues: list[dict[str, Any]], phase: str, revision: str
) -> list[str]:
    """Return unresolved findings that can invalidate the current phase.

    A reopened phase may legitimately coexist with findings against a
    superseded revision of the same phase or an invalidated downstream phase.
    Global findings, findings against the current revision, and unresolved
    upstream findings still block finalization and advancement.
    """
    current_index = PHASES.index(phase)
    result = []
    for item in issues:
        if (
            not isinstance(item, dict)
            or item.get('status') not in OPEN_ISSUE_STATUSES
            or item.get('severity') not in {'P0', 'P1'}
        ):
            continue
        issue_revision = str(item.get('artifact_revision') or '')
        issue_phase = str(item.get('phase') or '')
        if (
            not issue_revision
            or issue_revision == revision
            or issue_phase not in PHASES
            or PHASES.index(issue_phase) < current_index
        ):
            result.append(str(item.get('id')))
    return result


def validate_attempt_completion_evidence(
    task_dir: Path,
    task: dict[str, Any],
    status: str,
    output_revision: str,
    modified_files: list[str],
    command_evidence_ids: list[str],
    evidence_ids: list[str],
) -> None:
    if status != 'PASSED':
        return
    for evidence_id in evidence_ids:
        evidence.require_evidence(task_dir, evidence_id, expected_revision=output_revision)
    allowed = set(task.get('allowed_files') or [])
    unexpected = sorted(set(modified_files) - allowed)
    if unexpected:
        raise ValueError(f'modified files are outside allowed_files: {unexpected}')
    commands = list(task.get('validation_commands') or [])
    if commands and not command_evidence_ids:
        raise ValueError('PASSED attempt requires command evidence IDs for validation_commands')
    results: list[dict[str, Any]] = []
    for evidence_id in command_evidence_ids:
        entry = evidence.require_evidence(
            task_dir, evidence_id, expected_revision=output_revision, allowed_types={'command_ref'}
        )
        command_result_ref = str(entry.get('command_result_ref') or '')
        if not command_result_ref:
            raise ValueError(f'command evidence missing command_result_ref: {evidence_id}')
        results.append(command_result_payload(task_dir, command_result_ref))
    for result in results:
        if result.get('exit_code') != 0:
            raise ValueError(f'validation command failed: {result.get("command")} exit={result.get("exit_code")}')
        if result.get('revision') != output_revision:
            raise ValueError('command result revision does not match attempt output_revision')
    if commands:
        executed = {str(result.get('command')) for result in results}
        missing = [command for command in commands if command not in executed]
        if missing:
            raise ValueError(f'validation_commands missing command results: {missing}')


def authorize_task_command(args: argparse.Namespace, command: str, state: dict[str, Any], task_dir: Path) -> str:
    """Authorize a task-scoped command from the current task state."""
    agent = agent_context.selected_agent(args)
    schema_version = int(state.get('schema_version', 0))
    phases_for(schema_version)
    phase = state.get('current_phase')
    if not isinstance(phase, str):
        raise agent_context.AgentAuthorizationError('task state has no current canonical phase')
    additional: list[str] = []
    conditional: list[str] = []
    reviews = state.get('collaboration_reviews', {})
    if isinstance(reviews, dict):
        review = reviews.get(phase, {})
        if isinstance(review, dict):
            values = review.get('additional_reviewers', {})
            if isinstance(values, dict):
                additional = [value for value in values if isinstance(value, str)]
            conditional_map = conditional_reviewers_for(schema_version).get(phase, {})
            if isinstance(conditional_map, dict):
                trace_path = storage.path(task_dir, 'traceability')
                try:
                    trace_document = storage.read_json(trace_path, default={})
                    traces = trace_document.get('items', []) if isinstance(trace_document, dict) else []
                except (OSError, ValueError, storage.StorageError):
                    traces = []
                active_flags = {flag for flag in ('impact_required', 'cross_module_implementation_required') if any((isinstance(item, dict) and item.get(flag) is True for item in traces if isinstance(traces, list)))}
                conditional = [reviewer for reviewer, flag in conditional_map.items() if isinstance(reviewer, str) and flag in active_flags]
    project_manager_agent = state.get('project_manager_agent', '')
    agent_context.authorize_task_context(
        agent,
        'long_task',
        command,
        phase=phase,
        next_agent=state.get('next_agent', '') if isinstance(state.get('next_agent'), str) else '',
        conditional_reviewers=conditional,
        additional_reviewers=additional,
        project_manager_agent=project_manager_agent if isinstance(project_manager_agent, str) else '',
    )
    return agent


def validate_start_gate(
    task_dir: Path,
    state: dict[str, Any],
    tasks: list[dict[str, Any]],
    task: dict[str, Any],
    issues: list[dict[str, Any]],
) -> None:
    actual_mode = actual_work_mode_state(task_dir)
    mode_state = state.get('work_mode') if isinstance(state.get('work_mode'), dict) else {}
    minimal = bool(
        actual_mode.get('minimal')
        or actual_mode.get('lightweight')
        or mode_state.get('skipped_phases')
    )
    phase = str(task.get('phase') or '')
    artifacts = state.get('artifact_revisions') or {}
    for predecessor in phase_predecessors(phase, minimal):
        item = artifacts.get(predecessor)
        status = item.get('status') if isinstance(item, dict) else None
        if status != 'PASSED' and not (phase_skip_allowed(predecessor, minimal, state) and status == 'NOT_APPLICABLE'):
            raise ValueError(f'{phase} cannot start because predecessor {predecessor} is {status}')
    if phase_skip_allowed(phase, minimal, state):
        if minimal and phase in set(work_mode.MINIMAL_PHASES):
            raise ValueError(f'{phase} is skipped by wk -min and cannot start')
        raise ValueError(f'{phase} is skipped by the active workflow and cannot start')
    current_artifact = artifacts.get(phase)
    current_revision = (
        str(current_artifact.get('revision') or '')
        if isinstance(current_artifact, dict) else ''
    )
    current_iteration_no = (
        int(current_artifact.get('iteration_no') or 0)
        if isinstance(current_artifact, dict) else 0
    )
    current_phase_index = PHASES.index(phase)
    critical = [
        str(item.get('id')) for item in issues
        if isinstance(item, dict)
        and item.get('status') in OPEN_ISSUE_STATUSES
        and item.get('severity') in {'P0', 'P1'}
        and not (
            not current_revision
            and current_iteration_no > 1
            and bool(str(item.get('artifact_revision') or ''))
            and item.get('phase') in PHASES
            and PHASES.index(str(item.get('phase'))) >= current_phase_index
        )
    ]
    if critical:
        raise ValueError(
            f'task cannot start with open P0/P1 issues against '
            f'{current_revision or "the pending revision"}: {critical}'
        )
    task_map = {item.get('id'): item for item in tasks if isinstance(item, dict)}
    incomplete = []
    for dep in task.get('depends_on') or []:
        dep_task = task_map.get(dep)
        if not isinstance(dep_task, dict) or dep_task.get('status') not in {'PASSED', 'NOT_APPLICABLE'}:
            incomplete.append(f'{dep}={dep_task.get("status") if isinstance(dep_task, dict) else "MISSING"}')
    if incomplete:
        raise ValueError(f'task dependencies are not complete: {incomplete}')
    iteration_id, iteration_no = current_iteration(artifacts, phase)
    if task.get('iteration_id') != iteration_id or task.get('iteration_no') != iteration_no:
        raise ValueError(
            f'task iteration is stale for phase {phase}: task={task.get("iteration_id")}/{task.get("iteration_no")} '
            f'current={iteration_id}/{iteration_no}'
        )
    input_revisions = task.get('input_revisions') or {}
    if not isinstance(input_revisions, dict):
        raise ValueError('task input_revisions must be an object')
    required_inputs = phase_predecessors(phase, minimal)
    missing_inputs = [input_phase for input_phase in required_inputs if input_phase not in input_revisions]
    if missing_inputs:
        raise ValueError(f'task input_revisions missing required predecessors: {missing_inputs}')
    unexpected_inputs = sorted(
        str(input_phase) for input_phase in input_revisions
        if input_phase not in set(required_inputs)
    )
    if unexpected_inputs:
        raise ValueError(
            f'task input_revisions contains unexpected predecessors: {unexpected_inputs}; '
            f'expected: {required_inputs}'
        )
    for input_phase in required_inputs:
        expected_revision = input_revisions[input_phase]
        artifact = artifacts.get(input_phase)
        actual_revision = artifact.get('revision') if isinstance(artifact, dict) else None
        if actual_revision != expected_revision:
            raise ValueError(
                f'task input revision is stale: {input_phase} expected={expected_revision} current={actual_revision}'
            )
    if phase == 'development' and not minimal:
        # TDD is an embedded Development gate.  The final Development TDD
        # Review verifies that a target-specific RED existed before the
        # corresponding production mutation, so a separate top-level tdd
        # StageOutcome is no longer a start-task prerequisite.
        if 'test_design' not in input_revisions:
            raise ValueError('development requires current test_design input for embedded TDD')
    risk_triggers = set(task.get('risk_triggers') or [])
    if phase == 'code_review':
        report_errors, report = risk_detect.validate_report(task_dir)
        if report_errors:
            raise ValueError('risk detection report is invalid: ' + '; '.join(report_errors))
        development = artifacts.get('development') if isinstance(artifacts, dict) else None
        development_revision = development.get('revision') if isinstance(development, dict) else None
        if report.get('status') != 'SCANNED':
            raise ValueError('code review requires a SCANNED risk detection report')
        if report.get('input_revision') != development_revision:
            raise ValueError(
                f'risk detection revision is stale: report={report.get("input_revision")} current={development_revision}'
            )
        detected_risks = risk_detect.active_risk_types(report)
        missing_triggers = detected_risks - risk_triggers
        if missing_triggers:
            raise ValueError(f'code review task omits detected risk_triggers: {sorted(missing_triggers)}')
        expected_reviewers = {
            definition['reviewerAgent']
            for trigger, definition in RISK_REVIEWER_CATALOG.items()
            if trigger in risk_triggers and phase in set(definition.get('allowedPhases', []))
        }
        missing_reviewers = expected_reviewers - set(task.get('reviewer_agents') or [])
        if missing_reviewers:
            raise ValueError(f'code review task missing triggered risk reviewers: {sorted(missing_reviewers)}')


def blocking_issue_ids_for_revision(
    issues: list[dict[str, Any]], revision: str
) -> list[str]:
    """Block only findings raised against the candidate being completed.

    Findings from a superseded revision remain open until the original
    Reviewer verifies the replacement, but they must not make producing that
    replacement impossible.
    """
    return [
        str(item.get('id')) for item in issues
        if isinstance(item, dict)
        and item.get('status') in OPEN_ISSUE_STATUSES
        and item.get('severity') in {'P0', 'P1'}
        and str(item.get('artifact_revision') or '') in {'', revision}
    ]


def command_result_payload(task_dir: Path, ref: str) -> dict[str, Any]:
    path = resolve_task_ref(task_dir, ref)
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'invalid command result file {ref}: {exc}') from exc
    if (
        not isinstance(value, dict)
        or value.get('kind') != 'command_result'
        or value.get('schema_version') not in {1, 2}
    ):
        raise ValueError(f'command result file has invalid schema: {ref}')
    for key in ('command', 'executed_at', 'revision', 'output_digest'):
        if not is_nonempty_string(value.get(key)):
            raise ValueError(f'command result file missing {key}: {ref}')
    exit_code = value.get('exit_code')
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        raise ValueError(f'command result exit_code must be an integer: {ref}')
    output_ref = str(value.get('output_ref') or '')
    inline_output = value.get('output')
    if output_ref and inline_output not in (None, ''):
        raise ValueError(f'command result must use output or output_ref, not both: {ref}')
    if output_ref:
        output_path = resolve_task_ref(task_dir, output_ref)
        digest = sha256_file(output_path)
    elif isinstance(inline_output, str):
        digest = hashlib.sha256(inline_output.encode('utf-8')).hexdigest()
    else:
        raise ValueError(f'command result requires output or output_ref: {ref}')
    if digest != value['output_digest']:
        raise ValueError(f'command result output_digest mismatch: {ref}')

    # Schema v2 auto-evidence may preserve stderr separately.  Validate the
    # same inline-vs-reference and digest invariants as stdout so a tampered
    # stderr stream cannot silently pass a later evidence gate.  Schema v1
    # command results legitimately have no stderr fields.
    stderr_ref = str(value.get('stderr_ref') or '')
    inline_stderr = value.get('stderr')
    if stderr_ref and inline_stderr not in (None, ''):
        raise ValueError(f'command result must use stderr or stderr_ref, not both: {ref}')
    if stderr_ref:
        stderr_path = resolve_task_ref(task_dir, stderr_ref)
        stderr_digest = sha256_file(stderr_path)
    elif isinstance(inline_stderr, str):
        stderr_digest = hashlib.sha256(inline_stderr.encode('utf-8')).hexdigest()
    else:
        stderr_digest = None
    declared_stderr_digest = value.get('stderr_digest')
    if stderr_digest is None:
        if declared_stderr_digest not in (None, ''):
            raise ValueError(f'command result stderr_digest has no stderr payload: {ref}')
    elif declared_stderr_digest != stderr_digest:
        raise ValueError(f'command result stderr_digest mismatch: {ref}')
    # Newly generated schema-v2 results carry bounded-execution audit fields.
    # Keep reading older v2 evidence for historical compatibility, but validate
    # the fields whenever present so malformed timeout metadata cannot pass.
    timeout_seconds = value.get('timeout_seconds')
    if timeout_seconds is not None and (
        not isinstance(timeout_seconds, int) or isinstance(timeout_seconds, bool) or timeout_seconds <= 0
    ):
        raise ValueError(f'command result timeout_seconds must be a positive integer: {ref}')
    duration_ms = value.get('duration_ms')
    if duration_ms is not None and (
        not isinstance(duration_ms, int) or isinstance(duration_ms, bool) or duration_ms < 0
    ):
        raise ValueError(f'command result duration_ms must be a non-negative integer: {ref}')
    status = value.get('status')
    if status is not None and status not in {'PASSED', 'FAILED'}:
        raise ValueError(f'command result status is invalid: {ref}')
    error_code = value.get('error_code')
    if error_code is not None and not isinstance(error_code, str):
        raise ValueError(f'command result error_code must be a string: {ref}')
    timed_out = (value.get('metadata') or {}).get('timed_out') if isinstance(value.get('metadata'), dict) else None
    if timed_out is True and (value.get('exit_code') != 124 or error_code != 'COMMAND_TIMEOUT'):
        raise ValueError(f'command result timeout metadata is inconsistent: {ref}')
    return value


def current_iteration(artifacts: dict[str, Any], phase: str) -> tuple[str, int]:
    item = artifacts.get(phase)
    if not isinstance(item, dict):
        return ('', 0)
    return (str(item.get('iteration_id') or ''), int(item.get('iteration_no') or 0))


def revision_fingerprint(revisions: dict[str, Any]) -> str:
    payload = json.dumps(revisions, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()
