"""Attempt lifecycle commands extracted from the long-task lifecycle facade."""
from __future__ import annotations

import argparse
import re
import agent_context
import evidence
import storage
from pathlib import Path
from typing import Any

from common_utils import now
from error_catalog import LifecycleError
from _long_task import task_events
from _long_task.command_evidence import (
    COMMAND_EXIT_NONZERO,
    COMMAND_TIMEOUT,
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ExecutionEvidenceError,
    run_and_register,
)
from _long_task.contracts import *
from _long_task.record_io import *
from _long_task.review_validation import *
from _long_task.commands.queries import emit_json, find_attempt, find_plan_task, load_task_runtime_documents
from _long_task.evidence_gate import (
    authorize_task_command, blocking_issue_ids_for_revision, revision_fingerprint,
    validate_attempt_completion_evidence, validate_start_gate,
)
from _long_task.state_access import (
    append_task_events_payloads, load_task_history, read_task_state_cache, task_state_path,
)
from _long_task.validation.task import validate_task
from _long_task.validation.task_plan import attempt_event_type

def command_start_attempt(args: argparse.Namespace) -> int:
    task_dir = Path(args.task_dir)
    errors, _, state, issues = validate_task(task_dir)
    if errors:
        raise LifecycleError('task is invalid before start-attempt: ' + '; '.join(errors))
    agent = authorize_task_command(args, 'start-attempt', state, task_dir)
    (state_document, state), (plan_document, tasks), (attempts_document, attempts) = load_task_runtime_documents(task_dir)
    task = find_plan_task(tasks, args.task_id)
    if task.get('phase') != state.get('current_phase'):
        raise LifecycleError(
            f'task {args.task_id} phase {task.get("phase")} is not current_phase {state.get("current_phase")}; '
            'use advance-phase or reopen-phase'
        )
    validate_start_gate(task_dir, state, tasks, task, issues)
    if task.get('owner_agent') != agent:
        raise agent_context.AgentAuthorizationError(
            f'Agent {agent} cannot execute task {args.task_id}; owner_agent={task.get("owner_agent")}'
        )
    if task.get('status') not in {'PENDING', 'READY', 'REWORK', 'FAILED'}:
        raise LifecycleError(f'task {args.task_id} cannot start from status {task.get("status")}')
    if any(isinstance(item, dict) and item.get('task_id') == args.task_id and item.get('status') == 'RUNNING' for item in attempts):
        raise LifecycleError(f'task {args.task_id} already has a RUNNING attempt')
    expected_input_fingerprint = revision_fingerprint(task.get('input_revisions') or {})
    if args.input_revision and args.input_revision not in {
        expected_input_fingerprint, *[str(value) for value in (task.get('input_revisions') or {}).values()]
    }:
        raise LifecycleError('start-attempt --input-revision does not match task input_revisions')
    attempt_no = int(task.get('attempts', 0)) + 1
    max_attempts = int(task.get('max_attempts', 0))
    if attempt_no > max_attempts:
        raise LifecycleError(f'task {args.task_id} exceeds max_attempts={max_attempts}')
    iteration_id = str(task.get('iteration_id'))
    iteration_no = int(task.get('iteration_no'))
    attempt_id = f'ATTEMPT-{args.task_id}-I{iteration_no:03d}-A{attempt_no:03d}'
    timestamp = now()
    attempt = {
        'attempt_id': attempt_id,
        'task_id': args.task_id,
        'iteration_id': iteration_id,
        'iteration_no': iteration_no,
        'attempt_no': attempt_no,
        'agent': agent,
        'phase': task.get('phase'),
        'status': 'RUNNING',
        'input_revision': args.input_revision or revision_fingerprint(task.get('input_revisions') or {}),
        'output_revision': '',
        'started_at': timestamp,
        'completed_at': '',
        'failure_type': '',
        'failure_reason': '',
        'modified_files': [],
        'command_evidence_ids': [],
        'evidence_ids': [],
        'summary': args.summary or '',
        'next_action': args.next_action or f'完成 {attempt_id}',
    }
    attempts.append(attempt)
    task['attempts'] = attempt_no
    task['status'] = 'RUNNING'
    artifacts = state.get('artifact_revisions') or {}
    phase_artifact = artifacts.get(task.get('phase'))
    if isinstance(phase_artifact, dict) and phase_artifact.get('status') in {'PENDING', 'STALE', 'FAILED', 'BLOCKED'}:
        phase_artifact['status'] = 'IN_PROGRESS'
    state['task_status'] = 'RUNNING'
    state['current_phase'] = task.get('phase')
    state['current_agent'] = agent
    state['current_attempt_id'] = attempt_id
    active = state.setdefault('active_task_ids', [])
    if args.task_id not in active:
        active.append(args.task_id)
    state['next_action'] = attempt['next_action']
    state['next_agent'] = agent
    state['checkpoint_at'] = timestamp
    event_payloads, state, _history = append_task_events_payloads(
        task_dir, task_state_path(task_dir), state_document, state,
        [{
            'type': 'ATTEMPT_STARTED',
            'entity_id': attempt_id,
            'timestamp': timestamp,
            'source': 'long_task.py start-attempt',
            'payload': {'attempt': attempt},
        }],
        synced_at=timestamp,
    )
    transactional_text_write(event_payloads)
    emit_json({'attemptId': attempt_id, 'attemptNo': attempt_no, 'taskId': args.task_id, 'status': 'RUNNING'})
    return 0

def command_finish_attempt(args: argparse.Namespace) -> int:
    task_dir = Path(args.task_dir)
    errors, _, _, issues = validate_task(task_dir)
    if errors:
        raise LifecycleError('task is invalid before finish-attempt: ' + '; '.join(errors))
    blocking_issues = blocking_issue_ids_for_revision(issues, args.output_revision or '')
    if blocking_issues and args.status == 'PASSED':
        raise LifecycleError(
            f'PASSED attempt is blocked by P0/P1 issues against '
            f'{args.output_revision}: {blocking_issues}'
        )
    (state_document, state), (plan_document, tasks), (attempts_document, attempts) = load_task_runtime_documents(task_dir)
    agent = authorize_task_command(args, 'finish-attempt', state, task_dir)
    attempt = find_attempt(attempts, args.attempt_id)
    if attempt.get('status') != 'RUNNING':
        raise LifecycleError(f'attempt {args.attempt_id} is not RUNNING')
    if attempt.get('agent') != agent:
        raise agent_context.AgentAuthorizationError(
            f'Agent {agent} cannot finish attempt owned by {attempt.get("agent")}'
        )
    status = args.status
    if status not in ATTEMPT_STATUSES - {'RUNNING'}:
        raise LifecycleError(f'invalid finish status: {status}')
    auto_evidence = bool(getattr(args, 'auto_evidence', False))
    check_command = str(getattr(args, 'check', '') or '').strip()
    if auto_evidence and status != 'PASSED':
        raise LifecycleError(
            '--auto-evidence is only supported when finishing an attempt as PASSED',
            code='LIFECYCLE_AUTO_EVIDENCE_STATUS_INVALID',
        )
    if auto_evidence and not check_command:
        raise LifecycleError(
            '--auto-evidence requires exactly one --check command',
            code='LIFECYCLE_AUTO_EVIDENCE_CHECK_REQUIRED',
        )
    if check_command and not auto_evidence:
        raise LifecycleError(
            '--check requires --auto-evidence',
            code='LIFECYCLE_AUTO_EVIDENCE_CHECK_WITHOUT_FLAG',
        )
    evidence_ids = list(dict.fromkeys(args.evidence_ref or []))
    modified_files = list(dict.fromkeys(args.modified_file or []))
    command_evidence_ids = list(dict.fromkeys(args.command_ref or []))
    task = find_plan_task(tasks, str(attempt.get('task_id')))
    previous_task_status = str(task.get('status') or '')
    if status == 'PASSED' and not args.output_revision:
        raise LifecycleError('PASSED attempt requires --output-revision')
    if status == 'PASSED' and str(task.get('phase') or '') == 'requirement_confirmation':
        # Confirmation revisions are append-only within the same feature.  A
        # new iteration may not silently reuse a retired R-number (for
        # example, R06 -> R07 after a business-model change).  Non-canonical
        # project revision labels remain supported for compatibility.
        match = re.search(r'^(?P<stem>REQCONF(?:-[A-Z0-9]+)*)-R(?P<number>[0-9]+)(?:@.*)?$', str(args.output_revision or ''))
        if match:
            candidate = int(match.group('number'))
            stem = match.group('stem')
            retired = []
            for prior in attempts:
                if not isinstance(prior, dict) or prior.get('phase') != 'requirement_confirmation':
                    continue
                prior_revision = str(prior.get('output_revision') or '')
                prior_match = re.search(r'^(?P<prior_stem>REQCONF(?:-[A-Z0-9]+)*)-R(?P<number>[0-9]+)(?:@.*)?$', prior_revision)
                if prior_match and prior_match.group('prior_stem') == stem and prior.get('status') == 'PASSED':
                    retired.append(int(prior_match.group('number')))
            if retired and candidate <= max(retired):
                raise LifecycleError(
                    f'requirement_confirmation revision {args.output_revision} must advance beyond '
                    f'{stem}-R{max(retired):02d}; retired revision numbers cannot be reused'
                )
    if status == 'PASSED' and not evidence_ids and not auto_evidence:
        raise LifecycleError('PASSED attempt requires at least one --evidence-ref')
    if status in {'FAILED', 'BLOCKED'} and not (args.failure_type or args.failure_reason):
        raise LifecycleError(f'{status} attempt requires --failure-type or --failure-reason')
    if status in {'FAILED', 'BLOCKED', 'CANCELLED'} and args.output_revision:
        raise LifecycleError(f'{status} attempt must not have --output-revision')
    if attempt.get('iteration_id') != task.get('iteration_id'):
        raise LifecycleError('cannot finish an attempt from a superseded iteration')
    if auto_evidence:
        declared_commands = [str(value) for value in (task.get('validation_commands') or []) if str(value)]
        if not declared_commands:
            raise LifecycleError(
                '当前任务未声明 validation_commands，不能使用 --auto-evidence',
                code='LIFECYCLE_AUTO_EVIDENCE_COMMAND_NOT_DECLARED',
            )
        if check_command not in declared_commands:
            raise LifecycleError(
                f'--check 必须精确匹配当前任务声明的 validation_commands：{check_command}',
                code='LIFECYCLE_AUTO_EVIDENCE_COMMAND_NOT_DECLARED',
            )
        try:
            result = run_and_register(
                task_dir,
                agent=agent,
                command=check_command,
                revision=str(args.output_revision),
                phase=str(task.get('phase') or state.get('current_phase') or ''),
                cwd=Path.cwd(),
                mutation_scope='taskFacts',
                timeout=int(getattr(args, 'timeout', DEFAULT_COMMAND_TIMEOUT_SECONDS)),
                scope_refs=[str(task.get('id') or ''), str(args.attempt_id), check_command],
                attempt_id=str(args.attempt_id),
                declared_commands=declared_commands,
                capture_stderr=True,
                collector='finish-attempt-auto-evidence-v1',
            )
        except ExecutionEvidenceError as exc:
            raise LifecycleError(str(exc), code='LIFECYCLE_AUTO_EVIDENCE_EXECUTION_INVALID') from exc
        generated_evidence_id = str(result.get('evidenceId') or '')
        if result.get('status') != 'PASSED':
            timed_out = bool(result.get('timedOut')) or result.get('errorCode') == COMMAND_TIMEOUT
            failure_code = str(result.get('errorCode') or COMMAND_EXIT_NONZERO)
            attempt['last_command_audit'] = {
                'command': check_command,
                'status': 'FAILED',
                'error_code': failure_code,
                'exit_code': int(result.get('exitCode') or 0),
                'timeout_seconds': int(result.get('timeoutSeconds') or getattr(args, 'timeout', DEFAULT_COMMAND_TIMEOUT_SECONDS)),
                'duration_ms': int(result.get('durationMs') or 0),
                'timed_out': timed_out,
                'command_result_ref': str(result.get('commandResultRef') or ''),
                'evidence_id': generated_evidence_id,
            }
            # The failed command does not finish the attempt, but its immutable
            # result must still be visible in the attempt's event history.
            audit_timestamp = now()
            audit_payloads, _, _ = append_task_events_payloads(
                task_dir, task_state_path(task_dir), state_document, state,
                [{
                    'type': 'ATTEMPT_COMMAND_RECORDED',
                    'entity_id': str(args.attempt_id),
                    'timestamp': audit_timestamp,
                    'source': 'long_task.py finish-attempt --auto-evidence',
                    'payload': {'attempt': attempt},
                }],
                synced_at=audit_timestamp,
            )
            transactional_text_write(audit_payloads)
            error_code = (
                'LIFECYCLE_AUTO_EVIDENCE_COMMAND_TIMEOUT'
                if timed_out else 'LIFECYCLE_AUTO_EVIDENCE_COMMAND_FAILED'
            )
            raise LifecycleError(
                f'自动验证命令失败：{check_command} exit={result.get("exitCode")}; '
                f'error={failure_code}; evidence={generated_evidence_id}; commandResult={result.get("commandResultRef")}',
                code=error_code,
            )
        evidence_ids = list(dict.fromkeys([*evidence_ids, generated_evidence_id]))
        command_evidence_ids = list(dict.fromkeys([*command_evidence_ids, generated_evidence_id]))
    validate_attempt_completion_evidence(
        task_dir, task, status, args.output_revision or '', modified_files, command_evidence_ids, evidence_ids
    )
    timestamp = now()
    attempt.update({
        'status': status,
        'output_revision': args.output_revision or '',
        'completed_at': timestamp,
        'failure_type': args.failure_type or '',
        'failure_reason': args.failure_reason or '',
        'modified_files': modified_files,
        'command_evidence_ids': command_evidence_ids,
        'evidence_ids': evidence_ids,
        'summary': args.summary or attempt.get('summary') or '',
        'next_action': args.next_action or '',
    })
    if status == 'PASSED':
        task['status'] = 'PASSED'
        task['output_revision'] = args.output_revision
        task['validation_evidence_ids'] = evidence_ids
    elif status == 'CANCELLED':
        task['status'] = 'CANCELLED'
        task['output_revision'] = ''
        task['validation_evidence_ids'] = []
    elif status == 'BLOCKED':
        task['status'] = 'BLOCKED'
        task['output_revision'] = ''
        task['validation_evidence_ids'] = evidence_ids
    elif status in {'FAILED', 'PARTIAL'}:
        task['status'] = 'REWORK' if int(task.get('attempts', 0)) < int(task.get('max_attempts', 0)) else 'BLOCKED'
        task['output_revision'] = args.output_revision if status == 'PARTIAL' else ''
        task['validation_evidence_ids'] = evidence_ids
    failed = state.setdefault('failed_attempts', {})
    if status in {'FAILED', 'BLOCKED'}:
        failed[task['id']] = int(failed.get(task['id'], 0)) + 1
    active = state.setdefault('active_task_ids', [])
    state['active_task_ids'] = [value for value in active if value != task['id']]
    if state.get('current_attempt_id') == args.attempt_id:
        state['current_attempt_id'] = ''
    state['current_agent'] = agent
    state['task_status'] = 'BLOCKED' if task['status'] == 'BLOCKED' else ('REWORK' if task['status'] == 'REWORK' else 'RUNNING')
    if not state['active_task_ids'] and task['status'] == 'PASSED':
        state['task_status'] = 'PARTIAL'
    state['next_action'] = args.next_action or (
        f'复核 {task["id"]} 输出并推进下一任务' if status == 'PASSED' else f'处理 {task["id"]} 的 {status} 结果'
    )
    state['next_agent'] = args.next_agent or state.get('project_manager_agent') or PROJECT_MANAGER_AGENT
    state['checkpoint_at'] = timestamp

    work_path = ensure_work_record(version_root_from_task_dir(task_dir), str(state.get('version')))
    work_mode_state = state.get('work_mode') if isinstance(state.get('work_mode'), dict) else {}
    enabled_modes = [
        key for key in ('model_code', 'page_design', 'minimal', 'auto', 'architecture_review', 'git_checkpoint', 'git_push', 'review_only', 'lightweight')
        if work_mode_state.get(key) is True
    ]
    event = {
        'event_id': f'EVENT-{args.attempt_id}-{status}',
        'timestamp': timestamp,
        'event_type': attempt_event_type(status),
        'status': status,
        'agent': agent,
        'source': 'long_task.py finish-attempt',
        'version': str(state.get('version') or ''),
        'target_id': task_dir.name,
        'scope': task.get('title') or task.get('objective') or task_dir.name,
        'phase': task.get('phase') or '',
        'task_type': task.get('phase') or 'task-attempt',
        'execution_mode': ' / '.join(enabled_modes) if enabled_modes else 'standard / sequential',
        'task_id': task['id'],
        'attempt_id': args.attempt_id,
        'iteration_id': attempt.get('iteration_id') or '',
        'iteration_no': attempt.get('iteration_no') or 0,
        'input_revision': attempt.get('input_revision') or '',
        'output_revision': attempt.get('output_revision') or '',
        'stage_outcome_refs': [],
        'evidence_ids': evidence_ids,
        'review_result_refs': [],
        'issue_ids': list(state.get('open_issue_ids') or []),
        'git_checkpoint_refs': [],
        'summary': attempt.get('summary') or f'{task["id"]} iteration {attempt.get("iteration_no")} attempt {attempt.get("attempt_no")} {status}',
        'state_change': f'{task["id"]}: {previous_task_status} → {task.get("status")}',
        'sql_change_summary': '无 / 未登记',
        'validation_summary': f'登记 Evidence {len(evidence_ids)} 项；命令 Evidence {len(command_evidence_ids)} 项',
        'blockers': [value for value in (attempt.get('failure_type'), attempt.get('failure_reason')) if value],
        'modified_files_summary': modified_files,
        'next_action': state['next_action'],
        'next_agent': state['next_agent'],
    }
    work_text, _ = append_work_event(work_path, event, version=str(state.get('version') or ''))
    event_payloads, state, _history = append_task_events_payloads(
        task_dir, task_state_path(task_dir), state_document, state,
        [{
            'type': 'ATTEMPT_FINISHED',
            'entity_id': args.attempt_id,
            'timestamp': timestamp,
            'source': 'long_task.py finish-attempt',
            'payload': {'attempt': attempt},
        }],
        synced_at=timestamp,
    )
    payloads = {**event_payloads, work_path: work_text}
    transactional_text_write(payloads)
    emit_json({
        'attemptId': args.attempt_id,
        'taskId': task['id'],
        'attemptNo': attempt.get('attempt_no'),
        'status': status,
        'taskStatus': task['status'],
        'workEventId': event['event_id'],
    })
    return 0
