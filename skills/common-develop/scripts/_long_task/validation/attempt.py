"""Validation attempt for the internal runtime."""
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
from _long_task import task_events
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from common_utils import now, sha256_file
from _long_task.contracts import *
from _long_task.record_io import *
from _long_task.review_validation import *

from _long_task.evidence_gate import validate_attempt_completion_evidence
def validate_attempt_history(
    task_dir: Path,
    attempts: list[dict[str, Any]],
    work_events: list[dict[str, Any]],
    tasks: list[dict[str, Any]],
    state: dict[str, Any],
    phases: list[str],
    errors: list[str],
    warnings: list[str],
) -> None:
    task_map = {item.get('id'): item for item in tasks if isinstance(item, dict) and is_nonempty_string(item.get('id'))}
    seen_ids: set[str] = set()
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    running_ids: set[str] = set()
    failed_counts: dict[str, int] = {}
    for index, attempt in enumerate(attempts):
        prefix = f'task-attempts[{index}]'
        if not isinstance(attempt, dict):
            errors.append(f'{prefix} must be an object')
            continue
        validate_allowed_fields(attempt, TASK_ATTEMPT_FIELDS, prefix, errors)
        attempt_id = str(attempt.get('attempt_id') or '').strip()
        task_id = str(attempt.get('task_id') or '').strip()
        iteration_id = str(attempt.get('iteration_id') or '').strip()
        iteration_no = attempt.get('iteration_no')
        if not attempt_id:
            errors.append(f'{prefix} missing attempt_id')
        elif attempt_id in seen_ids:
            errors.append(f'duplicate attempt_id: {attempt_id}')
        seen_ids.add(attempt_id)
        task = task_map.get(task_id)
        if not task_id or not isinstance(task, dict):
            errors.append(f'{prefix} references unknown task: {task_id}')
        if not iteration_id:
            errors.append(f'{prefix} missing iteration_id')
        if not isinstance(iteration_no, int) or isinstance(iteration_no, bool) or iteration_no < 1:
            errors.append(f'{prefix} iteration_no must be a positive integer')
        grouped.setdefault((task_id, iteration_id), []).append(attempt)
        no = attempt.get('attempt_no')
        if not isinstance(no, int) or isinstance(no, bool) or no < 1:
            errors.append(f'{prefix} attempt_no must be a positive integer')
        agent = attempt.get('agent')
        if not is_nonempty_string(agent) or agent not in KNOWN_AGENTS:
            errors.append(f'{prefix} unknown agent: {agent}')
        phase = attempt.get('phase')
        if phase not in phases:
            errors.append(f'{prefix} invalid phase: {phase}')
        if isinstance(task, dict) and phase != task.get('phase'):
            errors.append(f'{prefix} phase does not match task plan: {phase} != {task.get("phase")}')
        status = attempt.get('status')
        if status not in ATTEMPT_STATUSES:
            errors.append(f'{prefix} invalid status: {status}')
        is_current_iteration = (
            isinstance(task, dict)
            and iteration_id == str(task.get('iteration_id') or '').strip()
        )
        for key in ('modified_files', 'command_evidence_ids', 'evidence_ids'):
            if not validate_string_list(attempt.get(key, [])):
                errors.append(f'{prefix} {key} must be a string array')
        command_audit = attempt.get('last_command_audit')
        if command_audit is not None:
            if not isinstance(command_audit, dict):
                errors.append(f'{prefix} last_command_audit must be an object')
            else:
                for key in (
                    'command', 'status', 'error_code', 'command_result_ref', 'evidence_id',
                ):
                    if not is_nonempty_string(command_audit.get(key)):
                        errors.append(f'{prefix} last_command_audit missing {key}')
                for key in ('timeout_seconds', 'duration_ms'):
                    value = command_audit.get(key)
                    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                        errors.append(f'{prefix} last_command_audit {key} must be a non-negative integer')
                if isinstance(command_audit.get('timeout_seconds'), int) and command_audit.get('timeout_seconds') <= 0:
                    errors.append(f'{prefix} last_command_audit timeout_seconds must be positive')
                exit_code = command_audit.get('exit_code')
                if not isinstance(exit_code, int) or isinstance(exit_code, bool):
                    errors.append(f'{prefix} last_command_audit exit_code must be an integer')
                if command_audit.get('status') not in {'PASSED', 'FAILED'}:
                    errors.append(f'{prefix} last_command_audit status is invalid')
                if not isinstance(command_audit.get('timed_out'), bool):
                    errors.append(f'{prefix} last_command_audit timed_out must be boolean')
        if not is_nonempty_string(attempt.get('started_at')):
            errors.append(f'{prefix} missing started_at')
        if status == 'RUNNING':
            running_ids.add(attempt_id)
            if is_nonempty_string(attempt.get('completed_at')):
                errors.append(f'{prefix} RUNNING must not have completed_at')
        elif not is_nonempty_string(attempt.get('completed_at')):
            errors.append(f'{prefix} completed attempt missing completed_at')
        if status == 'PASSED':
            if not is_nonempty_string(attempt.get('output_revision')):
                errors.append(f'{prefix} PASSED without output_revision')
            if not attempt.get('evidence_ids'):
                errors.append(f'{prefix} PASSED without evidence_ids')
            # A reopened phase intentionally preserves prior attempts while their
            # evidence may be superseded by the new iteration. ACTIVE/revision
            # completion gates therefore apply only to the task's current
            # iteration; historical attempts remain immutable audit records.
            if is_current_iteration:
                try:
                    validate_attempt_completion_evidence(
                        task_dir,
                        task,
                        status,
                        str(attempt.get('output_revision') or ''),
                        list(attempt.get('modified_files') or []),
                        list(attempt.get('command_evidence_ids') or []),
                        list(attempt.get('evidence_ids') or []),
                    )
                except ValueError as exc:
                    errors.append(f'{prefix} {exc}')
        if status in {'FAILED', 'BLOCKED'}:
            if not is_nonempty_string(attempt.get('failure_type')) and not is_nonempty_string(attempt.get('failure_reason')):
                errors.append(f'{prefix} {status} without failure_type/failure_reason')
            failed_counts[task_id] = failed_counts.get(task_id, 0) + 1
        if status in {'FAILED', 'BLOCKED', 'CANCELLED'} and is_nonempty_string(attempt.get('output_revision')):
            errors.append(f'{prefix} {status} must not have output_revision')
    running_by_task: dict[str, int] = {}
    for (task_id, iteration_id), task_attempts in grouped.items():
        numbers = sorted(item.get('attempt_no') for item in task_attempts if isinstance(item.get('attempt_no'), int))
        if numbers != list(range(1, len(task_attempts) + 1)):
            errors.append(f'task {task_id} iteration {iteration_id} attempt_no must be continuous from 1: {numbers}')
        running = [item for item in task_attempts if item.get('status') == 'RUNNING']
        running_by_task[task_id] = running_by_task.get(task_id, 0) + len(running)
        if len(running) > 1:
            errors.append(f'task {task_id} iteration {iteration_id} has multiple RUNNING attempts')
    for task_id, count in running_by_task.items():
        if count > 1:
            errors.append(f'task {task_id} has RUNNING attempts in multiple iterations')
    for task_id, task in task_map.items():
        current_key = (task_id, str(task.get('iteration_id') or ''))
        current_attempts = grouped.get(current_key, [])
        if task.get('attempts') != len(current_attempts):
            errors.append(f'task {task_id} current iteration attempts mismatch: plan={task.get("attempts")}, history={len(current_attempts)}')
        running = [item for item in current_attempts if item.get('status') == 'RUNNING']
        if running and task.get('status') != 'RUNNING':
            errors.append(f'task {task_id} has RUNNING attempt but plan status is {task.get("status")}')
        if not running and task.get('status') == 'RUNNING':
            errors.append(f'task {task_id} plan is RUNNING without a current-iteration RUNNING attempt')
        latest = current_attempts[-1] if current_attempts else None
        if task.get('status') == 'PASSED':
            if not isinstance(latest, dict) or latest.get('status') != 'PASSED':
                errors.append(f'task {task_id} PASSED without a current-iteration PASSED attempt')
            else:
                if task.get('output_revision') != latest.get('output_revision'):
                    errors.append(f'task {task_id} output_revision differs from latest PASSED attempt')
                if set(task.get('validation_evidence_ids') or []) != set(latest.get('evidence_ids') or []):
                    errors.append(f'task {task_id} validation_evidence_ids differs from latest PASSED attempt')
        if not current_attempts and task.get('status') in {'RUNNING', 'PASSED', 'FAILED', 'BLOCKED', 'CANCELLED'}:
            errors.append(f'task {task_id} status {task.get("status")} requires current-iteration attempt history')
    current_attempt_id = str(state.get('current_attempt_id') or '').strip()
    if current_attempt_id and current_attempt_id not in running_ids:
        errors.append(f'current_attempt_id must reference a RUNNING attempt: {current_attempt_id}')
    active_task_ids = set(state.get('active_task_ids') or []) if isinstance(state.get('active_task_ids'), list) else set()
    for (task_id, _), task_attempts in grouped.items():
        if any(item.get('status') == 'RUNNING' for item in task_attempts) and task_id not in active_task_ids:
            errors.append(f'RUNNING attempt task missing from active_task_ids: {task_id}')
    recorded_failed = state.get('failed_attempts') if isinstance(state.get('failed_attempts'), dict) else {}
    for task_id in task_map:
        if recorded_failed.get(task_id, 0) != failed_counts.get(task_id, 0):
            errors.append(
                f'failed_attempts mismatch for {task_id}: state={recorded_failed.get(task_id, 0)}, '
                f'history={failed_counts.get(task_id, 0)}'
            )

    event_ids: set[str] = set()
    attempt_event_counts: dict[str, int] = {}
    for index, event in enumerate(work_events):
        prefix = f'work-record[{index}]'
        if not isinstance(event, dict):
            errors.append(f'{prefix} must be an object')
            continue
        validate_allowed_fields(event, WORK_EVENT_FIELDS, prefix, errors)
        event_id = str(event.get('event_id') or '').strip()
        if not event_id:
            errors.append(f'{prefix} missing event_id')
        elif event_id in event_ids:
            errors.append(f'duplicate work event id: {event_id}')
        event_ids.add(event_id)
        if event.get('status') not in WORK_EVENT_STATUSES:
            errors.append(f'{prefix} invalid status: {event.get("status")}')
        if not is_nonempty_string(event.get('timestamp')) or not is_nonempty_string(event.get('event_type')):
            errors.append(f'{prefix} missing timestamp/event_type')
        if not is_nonempty_string(event.get('agent')) or event.get('agent') not in KNOWN_AGENTS:
            errors.append(f'{prefix} unknown agent: {event.get("agent")}')
        if not validate_string_list(event.get('evidence_ids', [])):
            errors.append(f'{prefix} evidence_ids must be a string array')
        if event.get('attempt_id'):
            if not is_nonempty_string(event.get('iteration_id')):
                errors.append(f'{prefix} attempt event missing iteration_id')
            if not isinstance(event.get('iteration_no'), int) or isinstance(event.get('iteration_no'), bool):
                errors.append(f'{prefix} attempt event iteration_no must be an integer')
        attempt_id = str(event.get('attempt_id') or '').strip()
        if attempt_id and event.get('target_id') == task_dir.name:
            if attempt_id not in seen_ids:
                errors.append(f'{prefix} references unknown attempt: {attempt_id}')
            if event.get('event_type') in {
                'TASK_ATTEMPT_COMPLETED', 'TASK_ATTEMPT_FAILED',
                'TASK_ATTEMPT_BLOCKED', 'TASK_ATTEMPT_PARTIAL',
                'TASK_ATTEMPT_CANCELLED',
            }:
                attempt_event_counts[attempt_id] = attempt_event_counts.get(attempt_id, 0) + 1
    for attempt in attempts:
        if isinstance(attempt, dict) and attempt.get('status') != 'RUNNING':
            attempt_id = attempt.get('attempt_id')
            if attempt_event_counts.get(attempt_id, 0) != 1:
                errors.append(f'completed attempt must have exactly one work_record event: {attempt_id}')
