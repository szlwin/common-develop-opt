"""Domain-specific phase lifecycle commands extracted from the lifecycle facade."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from document_core import agent_context
from document_core import acceptance
from document_core import evidence
from runtime import risk_detect
from document_core import storage
from runtime import work_mode
from runtime.error_catalog import LifecycleError
from document_core.common_utils import now
from runtime._long_task import authorization, task_events
from runtime._long_task.contracts import *
from runtime._long_task.record_io import *
from runtime._long_task.review_validation import *
from runtime._long_task.architecture_gate import (
    architecture_review_state, new_architecture_review_state,
    resolve_architecture_finalize_transition, sync_mode_review_requirements,
)
from runtime._long_task.commands.queries import (
    active_phase_reviewers, current_phase_tasks, emit_json, find_plan_task,
    load_task_runtime_documents,
)
from runtime._long_task.evidence_gate import (
    blocking_issue_ids_for_phase_revision, blocking_issue_ids_for_revision,
)
from runtime._long_task.phase_policy import (
    actual_work_mode_state, phase_input_revisions, phase_predecessors,
    phase_skip_allowed, rebind_phase_task_inputs,
)
from runtime._long_task.state_access import (
    append_task_events_payloads, load_task_history, read_static_task_plan,
    read_task_state_cache, task_state_path,
)
from runtime._long_task.validation.task import validate_task
from runtime._long_task.lifecycle_helpers import (
    add_cancelled_attempt_work_payload, new_iteration_id, reopen_phase_event_specs,
)

def command_advance_phase(args: argparse.Namespace) -> int:
    task_dir = Path(args.task_dir)
    agent = agent_context.authorize_command(args, 'long_task', 'advance-phase')
    errors, _, state, issues = validate_task(task_dir)
    if errors:
        raise LifecycleError('task is invalid before advance-phase: ' + '; '.join(errors))
    phases = phases_for(int(state.get('schema_version', 0)))
    current = str(state.get('current_phase'))
    index = phases.index(current)
    artifact = state['artifact_revisions'][current]
    critical = blocking_issue_ids_for_phase_revision(
        issues, current, str(artifact.get('revision') or '')
    )
    if critical:
        raise LifecycleError(
            f'cannot advance {current} with P0/P1 issues against the current '
            f'or upstream revision: {critical}'
        )
    minimal = bool(actual_work_mode_state(task_dir).get('minimal'))
    if artifact.get('status') != 'PASSED' and not (phase_skip_allowed(current, minimal, state) and artifact.get('status') == 'NOT_APPLICABLE'):
        raise LifecycleError(f'cannot advance because current phase {current} is {artifact.get("status")}')
    if current == 'development' and bool((state.get('work_mode') or {}).get('architecture_review')):
        step = architecture_review_state(state).get('step')
        if step not in {'IMPLEMENTATION_READY_FOR_REVIEW', 'IMPLEMENTATION_REVIEW_PASSED'}:
            raise LifecycleError(
                'wk -ar cannot leave development before the skeleton Review and concrete implementation gates pass; '
                'run advance-development-step after the first Review'
            )
    plan_path = storage.path(task_dir, 'plan')
    plan_document, static_tasks = read_static_task_plan(task_dir)
    history = load_task_history(task_dir)
    tasks = task_events.materialize_tasks(static_tasks, history) if isinstance(static_tasks, list) else []
    phase_tasks = [
        task for task in tasks if isinstance(task, dict)
        and task.get('phase') == current
        and task.get('iteration_id') == artifact.get('iteration_id')
    ]
    incomplete_phase_tasks = [
        task.get('id') for task in phase_tasks
        if task.get('status') not in {'PASSED', 'NOT_APPLICABLE'}
    ]
    if incomplete_phase_tasks:
        raise LifecycleError(f'cannot advance because current phase tasks are incomplete: {incomplete_phase_tasks}')
    outcomes = [item for item in history.get('outcomes', []) if isinstance(item, dict)]
    expected_outcome_status = 'SKIPPED' if artifact.get('status') == 'NOT_APPLICABLE' else 'PASSED'
    outcome = next((
        item for item in outcomes
        if isinstance(item, dict) and item.get('phase') == current and item.get('is_current') is True
    ), None) if isinstance(outcomes, list) else None
    if not isinstance(outcome, dict) or outcome.get('status') != expected_outcome_status:
        raise LifecycleError(f'cannot advance because current phase {current} has no current {expected_outcome_status} StageOutcome')
    if outcome.get('iteration_id') != artifact.get('iteration_id'):
        raise LifecycleError(f'cannot advance because {current} StageOutcome iteration is stale')
    if artifact.get('status') == 'PASSED' and outcome.get('output_revision') != artifact.get('revision'):
        raise LifecycleError(f'cannot advance because {current} StageOutcome revision is stale')
    if index == len(phases) - 1:
        raise LifecycleError('already at final phase; create the final Git checkpoint with git_checkpoint.py commit')
    actual_mode = actual_work_mode_state(task_dir)
    checkpoint_result = None
    push_result = None
    git_checkpoint_enabled = bool(actual_mode.get('git_checkpoint'))
    git_push_enabled = bool(actual_mode.get('git_push'))
    if git_push_enabled and not git_checkpoint_enabled:
        raise LifecycleError('wk -gp/--git-push requires wk -gc/--git-checkpoint')
    if git_checkpoint_enabled and state.get('execution_mode') != 'SEQUENTIAL':
        raise LifecycleError('wk -gc only supports execution_mode=SEQUENTIAL')
    if git_push_enabled:
        try:
            from document_core import git_checkpoint
            checkpoint_result = git_checkpoint.commit_checkpoint(task_dir, agent=agent, phase=current)
            push_result = git_checkpoint.push_checkpoint(
                task_dir, agent=agent, checkpoint_id=str(checkpoint_result.get('checkpointId') or '')
            )
            if not isinstance(push_result, dict) or push_result.get('remoteVerified') is not True:
                if isinstance(push_result, dict) and push_result.get('status') == 'CONNECTOR_REQUIRED':
                    raise LifecycleError(
                        'remote_connector_required: execute the GitHub Connector request at '
                        f"{push_result.get('connectorRequestPath')} and validate its receipt with "
                        'git_checkpoint.py connector-complete before retrying advance-phase'
                    )
                raise LifecycleError('remote checkpoint synchronization did not produce verified remote state')
        except (OSError, ValueError) as exc:
            raise LifecycleError(
                f'cannot advance because remote Git checkpoint synchronization failed: {exc}'
            ) from exc
    next_index = index + 1
    while next_index < len(phases) and phase_skip_allowed(phases[next_index], minimal, state):
        next_index += 1
    if next_index >= len(phases):
        raise LifecycleError('no next executable phase')
    next_phase = phases[next_index]
    for predecessor in phase_predecessors(next_phase, minimal):
        item = state['artifact_revisions'][predecessor]
        if item.get('status') != 'PASSED' and not (phase_skip_allowed(predecessor, minimal, state) and item.get('status') == 'NOT_APPLICABLE'):
            raise LifecycleError(f'cannot advance to {next_phase}; predecessor {predecessor} is {item.get("status")}')
    state_path = task_state_path(task_dir)
    state_document, cached_state = read_task_state_cache(task_dir, authoritative=True)
    state = task_events.project_state(cached_state, history, synced_at=str((cached_state.get('event_projection') or {}).get('synced_at') or ''))
    authorization_batch = authorization.find_valid_batch(task_dir, state, current, phases)
    timestamp = now()
    state['current_phase'] = next_phase
    state['current_round'] = f'{next_phase.upper()}-I{state["artifact_revisions"][next_phase]["iteration_no"]:03d}'
    state['current_agent'] = agent
    state['next_agent'] = PHASE_DEFINITIONS[next_phase].get('ownerAgent', PROJECT_MANAGER_AGENT)
    state['next_action'] = f'开始 {next_phase} 阶段'
    state['last_gate'] = current
    state['checkpoint_at'] = timestamp
    expected_inputs = phase_input_revisions(state, next_phase, minimal=minimal)
    rebound_task_ids: list[str] = []
    runtime_specs: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict) or task.get('phase') != next_phase:
            continue
        if task.get('input_revisions') != expected_inputs:
            rebound_task_ids.append(str(task.get('id') or ''))
        runtime_specs.append({
            'type': 'TASK_RUNTIME_SET',
            'entity_id': str(task.get('id') or ''),
            'timestamp': timestamp,
            'source': 'long_task.py advance-phase',
            'payload': {'runtime': {'input_revisions': dict(expected_inputs)}},
        })
    if authorization_batch is not None:
        runtime_specs.append(authorization.consumption_spec(
            authorization_batch,
            from_phase=current,
            to_phase=next_phase,
            state=state,
            timestamp=timestamp,
        ))
    tracked = [state_path, task_events.event_path(task_dir)]
    backups = {path: path.read_bytes() if path.exists() else None for path in tracked}
    event_payloads, state, _history = append_task_events_payloads(
        task_dir, state_path, state_document, state, runtime_specs, synced_at=timestamp
    )
    transactional_text_write(event_payloads)
    if git_checkpoint_enabled and not git_push_enabled:
        try:
            from document_core import git_checkpoint
            checkpoint_result = git_checkpoint.commit_checkpoint(task_dir, agent=agent, phase=current)
        except (OSError, ValueError) as exc:
            for path, content in backups.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
            raise LifecycleError(f'cannot advance because Git checkpoint failed: {exc}') from exc
    emit_json({
        'status': 'ADVANCED', 'fromPhase': current, 'currentPhase': next_phase,
        'reboundTaskIds': rebound_task_ids, 'gitCheckpoint': checkpoint_result, 'gitPush': push_result,
        'authorization': ({
            'status': 'CONSUMED',
            'requestFingerprint': authorization_batch['fingerprint'],
            'untilPhase': authorization_batch['untilPhase'],
        } if authorization_batch is not None else {'status': 'NOT_USED'}),
    })
    return 0


def _reopen_boundary_confirmation(
    args: argparse.Namespace, task_dir: Path, state: dict[str, Any], phases: list[str],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Resolve the exact upstream authorization before any invalidation."""
    if args.from_phase not in phases:
        raise LifecycleError(f'invalid phase {args.from_phase}')
    changed_phase = str(getattr(args, 'changed_phase', '') or state.get('current_phase') or '')
    if changed_phase not in phases:
        raise LifecycleError(f'invalid changed phase {changed_phase}')
    boundary_confirmation: dict[str, Any] = {}
    boundary_consumption: dict[str, Any] | None = None
    if phases.index(args.from_phase) < phases.index(changed_phase):
        confirmation_ref = str(getattr(args, 'confirmation_ref', '') or '').strip()
        if not confirmation_ref:
            raise LifecycleError(
                'reopen-phase upstream expansion requires user-confirmed boundary authorization via --confirmation-ref; '
                'the reference must resolve to a prior authorization event'
            )
        source_revision = str(getattr(args, 'source_revision', '') or '').strip()
        if not source_revision:
            raise LifecycleError('upstream reopen boundary confirmation requires --source-revision')
        authorization_record = authorization.find_valid_reopen_boundary(
            task_dir, state, changed_phase=changed_phase, from_phase=args.from_phase,
            source_revision=source_revision, reason=str(args.reason),
            confirmation_ref=confirmation_ref, phases=phases,
        )
        if authorization_record is None:
            raise LifecycleError('confirmation-ref does not resolve to a valid prior reopen authorization for this task boundary')
        confirmation_event = authorization_record['event']
        confirmation_payload = authorization_record['payload']
        boundary_confirmation = {
            'changed_phase': changed_phase,
            'confirmation_ref': confirmation_payload.get('requestFingerprint'),
            'confirmation_fingerprint': confirmation_payload.get('requestFingerprint'),
            'confirmation_event_id': confirmation_event.get('event_id'),
            'confirmation_event_digest': confirmation_event.get('digest'),
            'user_confirmed': True,
            'confirmed_at': confirmation_event.get('timestamp'),
        }
        boundary_consumption = authorization.reopen_boundary_consumption_spec(authorization_record, timestamp=now())
    return boundary_confirmation, boundary_consumption


def _restore_reopen_mode_policy(
    state: dict[str, Any], actual_mode: dict[str, Any], from_phase: str, phases: list[str],
) -> int:
    """Restore applicable phase waivers and return the invalidation boundary."""
    if actual_mode.get('lightweight') and from_phase in set(work_mode.LIGHTWEIGHT_PHASES) & set(phases):
        raise LifecycleError(
            f'cannot reopen {from_phase} while wk -ws is active; '
            'lightweight mode does not permit test design, Review, or testing; '
            'run wk -wc before explicitly reopening the required phase'
        )
    recorded_mode = state.get('work_mode') if isinstance(state.get('work_mode'), dict) else {}
    waived = {
        str(value) for value in (recorded_mode.get('skipped_phases') or [])
        if isinstance(value, str) and value in phases
    }
    if not actual_mode.get('lightweight') and from_phase in waived:
        waived.difference_update(phases[phases.index(from_phase):])
        recorded_mode['skipped_phases'] = sorted(waived)
        recorded_mode['lightweight_waived'] = bool(waived)
    minimal_skipped = {
        str(value) for value in (recorded_mode.get('minimal_skipped_phases') or [])
        if isinstance(value, str) and value in work_mode.MINIMAL_PHASES
    }
    if not actual_mode.get('minimal'):
        minimal_skipped.difference_update(
            phase for phase in work_mode.MINIMAL_PHASES
            if phases.index(phase) >= phases.index(from_phase)
        )
    recorded_mode['minimal_skipped_phases'] = sorted(minimal_skipped)
    state['work_mode'] = recorded_mode
    source_index = phases.index(from_phase)
    work_policy = state.get('work_policy') if isinstance(state.get('work_policy'), dict) else {}
    phase_overrides = work_policy.get('phase_overrides') if isinstance(work_policy.get('phase_overrides'), dict) else {}
    removed_override = False
    for phase in phases[source_index:]:
        removed_override = phase_overrides.pop(phase, None) is not None or removed_override
    if removed_override:
        work_policy['phase_overrides'] = phase_overrides
    work_policy['explicit_reopen_required'] = bool(
        phase_overrides or (recorded_mode.get('lightweight_waived') and not actual_mode.get('lightweight'))
    )
    state['work_policy'] = work_policy
    return source_index


def _reopen_artifact_iterations(
    artifacts: dict[str, Any], state: dict[str, Any], actual_mode: dict[str, Any],
    affected_phases: list[str], from_phase: str, target_id: str,
) -> tuple[dict[str, tuple[str, int, str]], list[str]]:
    iteration_map: dict[str, tuple[str, int, str]] = {}
    changed: list[str] = []
    for phase in affected_phases:
        item = artifacts.get(phase)
        if not isinstance(item, dict):
            continue
        old_id = str(item.get('iteration_id') or '')
        new_no = int(item.get('iteration_no') or 0) + 1
        new_id = new_iteration_id(target_id, phase, new_no)
        iteration_map[phase] = (new_id, new_no, old_id)
        item['iteration_id'] = new_id
        item['iteration_no'] = new_no
        item['revision'] = ''
        item['status'] = 'IN_PROGRESS' if phase == from_phase else (
            'NOT_APPLICABLE' if phase_skip_allowed(phase, bool(actual_mode.get('minimal')), state) else 'STALE'
        )
        changed.append(phase)
    return iteration_map, changed


def _reset_reopen_reviews(
    state: dict[str, Any], iteration_map: dict[str, tuple[str, int, str]], timestamp: str,
) -> None:
    reviews = state.get('collaboration_reviews') or {}
    for phase, (new_id, _, _) in iteration_map.items():
        review = reviews.get(phase)
        if not isinstance(review, dict):
            continue
        if review.get('artifact_revision') or review.get('independent_conclusions'):
            history = review.setdefault('review_history', [])
            history.append({
                'iteration_id': review.get('current_iteration_id'),
                'artifact_revision': review.get('artifact_revision'),
                'status': review.get('status'),
                'independent_conclusions': copy.deepcopy(review.get('independent_conclusions') or {}),
                'archived_at': timestamp,
            })
        review['current_iteration_id'] = new_id
        review['artifact_revision'] = ''
        review['additional_reviewers'] = {}
        review['independent_conclusions'] = {}
        review['status'] = 'PENDING'


def _reopen_outcomes(
    outcomes: list[dict[str, Any]], iteration_map: dict[str, tuple[str, int, str]],
    state: dict[str, Any], actual_mode: dict[str, Any], target_id: str, agent: str, timestamp: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    artifacts = state['artifact_revisions']
    superseded_outcomes: list[dict[str, Any]] = []
    for outcome in outcomes:
        if not isinstance(outcome, dict) or outcome.get('phase') not in iteration_map:
            continue
        if outcome.get('is_current') is True:
            outcome['is_current'] = False
            outcome['superseded_by'] = iteration_map[outcome['phase']][0]
            superseded_outcomes.append(copy.deepcopy(outcome))
    new_outcomes: list[dict[str, Any]] = []
    if actual_mode.get('minimal') is True or actual_mode.get('lightweight') is True:
        skip_reason = str(work_mode.LIGHTWEIGHT_SKIP_REASON if actual_mode.get('lightweight') else work_mode.MINIMAL_SKIP_REASON)
        for phase, (new_id, new_no, _) in iteration_map.items():
            if not phase_skip_allowed(phase, True, state):
                continue
            artifact = artifacts[phase]
            artifact['status'] = 'NOT_APPLICABLE'
            artifact['revision'] = f'SKIP-{phase.upper()}-{new_no}'
            skipped_outcome = {
                'id': f'SO-{target_id}-{phase.upper()}-SKIP-I{new_no}',
                'phase': phase,
                'round': f'{phase.upper()}-SKIP-I{new_no:03d}',
                'iteration_id': new_id,
                'iteration_no': new_no,
                'is_current': True,
                'superseded_by': '',
                'status': 'SKIPPED',
                'produced_by_agent': agent,
                'input_revisions': {},
                'output_revision': artifact['revision'],
                'evidence_ids': [],
                'open_risks': [skip_reason],
                'started_at': timestamp,
                'completed_at': timestamp,
            }
            outcomes.append(skipped_outcome)
            new_outcomes.append(skipped_outcome)
    return superseded_outcomes, new_outcomes


def _reopen_task_runtimes(
    tasks: list[dict[str, Any]], iteration_map: dict[str, tuple[str, int, str]],
    state: dict[str, Any], minimal_mode: bool, reason: str, timestamp: str,
) -> tuple[set[str], list[dict[str, Any]]]:
    invalidated_task_ids: set[str] = set()
    task_runtime_specs: list[dict[str, Any]] = []
    for task in tasks:
        if not isinstance(task, dict) or task.get('phase') not in iteration_map:
            continue
        phase = str(task['phase'])
        new_id, new_no, old_id = iteration_map[phase]
        task['supersedes_iteration_id'] = old_id
        task['iteration_id'] = new_id
        task['iteration_no'] = new_no
        task['revision_reason'] = reason
        task['attempts'] = 0
        task['output_revision'] = ''
        task['validation_evidence_ids'] = []
        task['status'] = 'NOT_APPLICABLE' if phase_skip_allowed(phase, minimal_mode, state) else 'REWORK'
        # 新 iteration 只继承直接前置 revision；后续 advance-phase 会再次绑定刚通过的 canonical revision。
        task['input_revisions'] = phase_input_revisions(state, phase, minimal=minimal_mode)
        invalidated_task_ids.add(str(task.get('id')))
        task_runtime_specs.append({
            'type': 'TASK_RUNTIME_SET',
            'entity_id': str(task.get('id') or ''),
            'timestamp': timestamp,
            'source': 'long_task.py reopen-phase',
            'payload': {'runtime': {
                'status': task.get('status'),
                'attempts': 0,
                'output_revision': '',
                'validation_evidence_ids': [],
                'iteration_id': new_id,
                'iteration_no': new_no,
                'supersedes_iteration_id': old_id,
                'revision_reason': reason,
                'input_revisions': copy.deepcopy(task.get('input_revisions') or {}),
            }},
        })
    return invalidated_task_ids, task_runtime_specs


def _cancel_reopened_attempts(
    attempts: list[dict[str, Any]], invalidated_task_ids: set[str], reason: str, timestamp: str,
) -> list[dict[str, Any]]:
    cancelled: list[dict[str, Any]] = []
    for attempt in attempts:
        if not isinstance(attempt, dict):
            continue
        if attempt.get('status') == 'RUNNING' and attempt.get('task_id') in invalidated_task_ids:
            attempt['status'] = 'CANCELLED'
            attempt['completed_at'] = timestamp
            attempt['failure_type'] = 'STALE_INVALIDATION'
            attempt['failure_reason'] = reason
            attempt['summary'] = attempt.get('summary') or '上游 revision 变化，当前执行失效'
            attempt['next_action'] = f'在新 iteration 中重新执行 {attempt.get("task_id")}'
            cancelled.append(attempt)
    return cancelled


def _update_reopen_state(
    args: argparse.Namespace, state: dict[str, Any], actual_mode: dict[str, Any],
    phases: list[str], iteration_map: dict[str, tuple[str, int, str]],
    cancelled: list[dict[str, Any]], agent: str, timestamp: str,
) -> None:
    artifacts = state['artifact_revisions']
    source_index = phases.index(args.from_phase)
    minimal_mode = bool(actual_mode.get('minimal') or actual_mode.get('lightweight'))
    state['task_status'] = 'REWORK'
    state['current_phase'] = args.from_phase
    state['current_round'] = args.round or f'{args.from_phase.upper()}-I{iteration_map[args.from_phase][1]:03d}'
    state['current_agent'] = agent
    state['next_action'] = f'执行 {args.from_phase} 新 iteration，并重新评估全部下游产物、任务和证据'
    state['next_agent'] = PHASE_DEFINITIONS[args.from_phase].get('ownerAgent', PROJECT_MANAGER_AGENT)
    cancelled_task_ids = {str(item.get('task_id')) for item in cancelled}
    state['active_task_ids'] = [value for value in state.get('active_task_ids', []) if value not in cancelled_task_ids]
    if state.get('current_attempt_id') in {item.get('attempt_id') for item in cancelled}:
        state['current_attempt_id'] = ''
    state['checkpoint_at'] = timestamp
    if bool(actual_mode.get('architecture_review')):
        if source_index <= phases.index('development'):
            development = artifacts.get('development')
            state['architecture_review'] = new_architecture_review_state(True, str(development.get('iteration_id') or '') if isinstance(development, dict) else '', timestamp)
            sync_mode_review_requirements(state, minimal=minimal_mode)
        elif args.from_phase == 'code_review':
            # 仅重做最终 Code Review 时，架构骨架和具体实现仍然有效；只撤销上一轮 final Review 结论。
            ar_state = architecture_review_state(state)
            step = str(ar_state.get('step') or '')
            if step not in {'IMPLEMENTATION_READY_FOR_REVIEW', 'IMPLEMENTATION_REVIEW_PASSED'}:
                raise LifecycleError('wk -ar code_review can only reopen after the concrete implementation is ready for final Review')
            ar_state['step'] = 'IMPLEMENTATION_READY_FOR_REVIEW'
            ar_state['final_review_revision'] = ''
            ar_state['final_review_result_refs'] = []
            ar_state['updated_at'] = timestamp
            sync_mode_review_requirements(state, minimal=minimal_mode)


def command_reopen_phase(args: argparse.Namespace) -> int:
    task_dir = Path(args.task_dir)
    agent = agent_context.authorize_command(args, 'long_task', 'reopen-phase')
    state_path = task_state_path(task_dir)
    state_document, state = read_task_state_cache(task_dir, authoritative=True)
    actual_mode = actual_work_mode_state(task_dir)
    schema_version = int(state.get('schema_version', 0))
    phases = phases_for(schema_version)
    boundary_confirmation, boundary_consumption = _reopen_boundary_confirmation(args, task_dir, state, phases)
    source_index = _restore_reopen_mode_policy(state, actual_mode, args.from_phase, phases)
    timestamp = now()
    artifacts = state.get('artifact_revisions')
    if not isinstance(artifacts, dict):
        raise LifecycleError('artifact_revisions must be an object')
    plan_path = storage.path(task_dir, 'plan')
    plan_document, tasks = read_static_task_plan(task_dir)
    history = load_task_history(task_dir)
    attempts = [item for item in history.get('attempts', []) if isinstance(item, dict)]
    outcomes = [item for item in history.get('outcomes', []) if isinstance(item, dict)]
    tasks = task_events.materialize_tasks(tasks, history)
    assertions_path = acceptance.assertions_path(task_dir)
    assertions_document = acceptance.load_document(task_dir)
    if not isinstance(tasks, list) or not isinstance(attempts, list) or not isinstance(outcomes, list):
        raise LifecycleError('long-task plan/attempt/outcome documents are invalid')
    target_id = str(state.get('target_id') or task_dir.name)
    iteration_map, changed = _reopen_artifact_iterations(
        artifacts, state, actual_mode, phases[source_index:], args.from_phase, target_id,
    )
    _reset_reopen_reviews(state, iteration_map, timestamp)
    superseded_outcomes, new_outcomes = _reopen_outcomes(
        outcomes, iteration_map, state, actual_mode, target_id, agent, timestamp,
    )
    minimal_mode = bool(actual_mode.get('minimal') or actual_mode.get('lightweight'))
    invalidated_task_ids, task_runtime_specs = _reopen_task_runtimes(
        tasks, iteration_map, state, minimal_mode, args.reason, timestamp,
    )
    assertions_document['assertions'] = [
        item for item in assertions_document.get('assertions', [])
        if not isinstance(item, dict) or item.get('phase') not in iteration_map
    ]
    cancelled = _cancel_reopened_attempts(attempts, invalidated_task_ids, args.reason, timestamp)
    _update_reopen_state(args, state, actual_mode, phases, iteration_map, cancelled, agent, timestamp)
    event_specs = reopen_phase_event_specs(
        task_runtime_specs,
        superseded_outcomes,
        new_outcomes,
        cancelled,
        from_phase=args.from_phase,
        source_revision=args.source_revision or '',
        invalidated_phases=changed,
        reason=args.reason,
        agent=agent,
        iteration_id=iteration_map[args.from_phase][0],
        timestamp=timestamp,
        boundary_confirmation=boundary_confirmation,
    )
    if boundary_consumption is not None:
        event_specs.insert(max(0, len(event_specs) - 1), boundary_consumption)
    event_payloads, state, _history = append_task_events_payloads(
        task_dir, state_path, state_document, state, event_specs, synced_at=timestamp
    )
    payloads = {
        **event_payloads,
        assertions_path: json.dumps(assertions_document, ensure_ascii=False, indent=2) + '\n',
    }
    add_cancelled_attempt_work_payload(
        payloads, task_dir, state, cancelled, agent=agent, timestamp=timestamp
    )
    transactional_text_write(payloads)
    if not bool(getattr(args, '_suppress_output', False)):
        emit_json({'reopenedPhase': args.from_phase, 'newIterationId': iteration_map[args.from_phase][0],
                   'invalidatedPhases': changed, 'cancelledAttemptIds': [item.get('attempt_id') for item in cancelled],
                   'artifactHandling': 'STALE_ONLY_CANONICAL_FILES_PRESERVED'})
    return 0


def command_finalize_phase(args: argparse.Namespace) -> int:
    """Pass the current artifact and StageOutcome after all phase gates succeed."""
    task_dir = Path(args.task_dir)
    agent = agent_context.authorize_command(args, 'long_task', 'finalize-phase')
    errors, _, state, issues = validate_task(task_dir)
    if errors:
        raise LifecycleError('task is invalid before finalize-phase: ' + '; '.join(errors))
    phase = str(state.get('current_phase') or '')
    state_path = task_state_path(task_dir)
    state_document, state = read_task_state_cache(task_dir, authoritative=True)
    plan_document, static_tasks = read_static_task_plan(task_dir)
    history = load_task_history(task_dir)
    tasks = task_events.materialize_tasks(static_tasks, history) if isinstance(static_tasks, list) else []
    outcomes = [item for item in history.get('outcomes', []) if isinstance(item, dict)]
    if not isinstance(tasks, list):
        raise LifecycleError('task plan is invalid')
    if state.get('current_attempt_id') or state.get('active_task_ids'):
        raise LifecycleError('cannot finalize phase while a task attempt is active')
    artifact = (state.get('artifact_revisions') or {}).get(phase)
    if not isinstance(artifact, dict) or artifact.get('status') != 'IN_PROGRESS' or not artifact.get('revision'):
        raise LifecycleError(f'finalize-phase requires a published IN_PROGRESS artifact for {phase}')
    revision = str(artifact['revision'])
    critical = blocking_issue_ids_for_phase_revision(issues, phase, revision)
    if critical:
        raise LifecycleError(
            f'cannot finalize {phase} with P0/P1 issues against the current '
            f'or upstream revision: {critical}'
        )
    iteration_id = str(artifact.get('iteration_id') or '')
    incomplete = [
        str(task.get('id')) for task in current_phase_tasks(tasks, phase, iteration_id)
        if task.get('status') not in {'PASSED', 'NOT_APPLICABLE'}
    ]
    if incomplete:
        raise LifecycleError(f'cannot finalize phase because current phase tasks are incomplete: {incomplete}')
    review = (state.get('collaboration_reviews') or {}).get(phase)
    if isinstance(review, dict):
        if review.get('artifact_revision') != revision or review.get('status') != 'PASSED':
            raise LifecycleError(f'cannot finalize {phase} before collaboration Review passes for {revision}')
    ar_state = architecture_review_state(state)
    ar_transition, ar_transition_refs = resolve_architecture_finalize_transition(
        phase, state, review if isinstance(review, dict) else None,
        minimal=bool((state.get('work_mode') or {}).get('minimal')),
    )
    ar_review_refs = ar_transition_refs if ar_transition == 'SKELETON_REVIEW_PASSED' else []
    ar_final_review_refs = ar_transition_refs if ar_transition == 'IMPLEMENTATION_REVIEW_PASSED' else []
    trace_document = storage.read_json(storage.path(task_dir, 'traceability'), default={})
    traces = trace_document.get('items', []) if isinstance(trace_document, dict) else []
    invalid_traces = [
        str(item.get('id') or f'index-{index}')
        for index, item in enumerate(traces if isinstance(traces, list) else [])
        if isinstance(item, dict) and item.get('status') in {'GAP', 'STALE'}
    ]
    if invalid_traces:
        raise LifecycleError(f'cannot finalize {phase} with GAP/STALE traces: {invalid_traces}')
    outcome = next((
        item for item in outcomes
        if isinstance(item, dict) and item.get('phase') == phase and item.get('is_current') is True
    ), None)
    if (
        not isinstance(outcome, dict)
        or outcome.get('status') != 'RUNNING'
        or outcome.get('iteration_id') != iteration_id
        or outcome.get('output_revision') != revision
    ):
        raise LifecycleError(f'finalize-phase requires the current RUNNING StageOutcome for {phase}@{revision}')
    for evidence_id in outcome.get('evidence_ids') or []:
        evidence.require_evidence(
            task_dir,
            str(evidence_id),
            expected_revision=revision,
            expected_phase=phase,
        )
    if not outcome.get('evidence_ids'):
        raise LifecycleError('finalize-phase requires StageOutcome evidence IDs')

    timestamp = now()
    artifact['status'] = 'PASSED'
    outcome['status'] = 'PASSED'
    outcome['completed_at'] = timestamp
    state['current_agent'] = agent
    state['checkpoint_at'] = timestamp
    is_final = phase == PHASES[-1]
    state['task_status'] = 'COMPLETED' if is_final else 'PARTIAL'
    state['next_agent'] = state.get('project_manager_agent') or PROJECT_MANAGER_AGENT
    state['next_action'] = '' if is_final else f'运行 advance-phase 推进 {phase} 的下一阶段'
    if ar_transition == 'SKELETON_REVIEW_PASSED':
        ar_state.update({
            'enabled': True,
            'step': ar_transition,
            'skeleton_iteration_id': iteration_id,
            'skeleton_revision': revision,
            'skeleton_review_result_refs': ar_review_refs,
            'implementation_iteration_id': '',
            'implementation_revision': '',
            'final_review_revision': '',
            'final_review_result_refs': [],
            'updated_at': timestamp,
        })
        state['next_action'] = '运行 advance-development-step 开启 wk -ar 具体实现 iteration'
    elif ar_transition == 'IMPLEMENTATION_READY_FOR_REVIEW':
        ar_state.update({
            'enabled': True,
            'step': ar_transition,
            'implementation_iteration_id': iteration_id,
            'implementation_revision': revision,
            'final_review_revision': '',
            'final_review_result_refs': [],
            'updated_at': timestamp,
        })
        state['next_action'] = '运行 advance-phase 进入最终 code_review（第二轮 Review）'
    elif ar_transition == 'IMPLEMENTATION_REVIEW_PASSED':
        ar_state.update({
            'enabled': True,
            'step': ar_transition,
            'final_review_revision': revision,
            'final_review_result_refs': ar_final_review_refs,
            'updated_at': timestamp,
        })
        state['next_action'] = '运行 advance-phase 进入 testing' if not is_final else ''

    rollback_paths = [
        state_path, task_events.event_path(task_dir),
    ]
    originals = {path: path.read_bytes() if path.exists() else None for path in rollback_paths}
    event_payloads, state, _history = append_task_events_payloads(
        task_dir, state_path, state_document, state,
        [{
            'type': 'STAGE_OUTCOME_FINALIZED',
            'entity_id': str(outcome.get('id') or ''),
            'timestamp': timestamp,
            'source': 'long_task.py finalize-phase',
            'payload': {'outcome': outcome},
        }],
        synced_at=timestamp,
    )
    transactional_text_write(event_payloads)
    post_errors, _, _, _ = validate_task(task_dir)
    if post_errors:
        for path, content in originals.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        raise LifecycleError('finalize-phase gate validation failed: ' + '; '.join(post_errors))
    emit_json({
        'status': 'PASSED',
        'phase': phase,
        'revision': revision,
        'stageOutcomeId': outcome.get('id'),
        'taskStatus': state['task_status'],
    })
    return 0
