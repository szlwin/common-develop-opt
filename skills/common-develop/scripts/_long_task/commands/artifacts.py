"""Domain-specific artifact lifecycle commands extracted from the lifecycle facade."""
from __future__ import annotations

import argparse
import copy
import json
import shlex
from pathlib import Path
from typing import Any

import agent_context
import acceptance
import evidence
import risk_detect
import storage
import work_mode
from error_catalog import LifecycleError
from common_utils import now
from _long_task import task_events
from _long_task.contracts import *
from _long_task.record_io import *
from _long_task.review_validation import *
from _long_task.architecture_gate import (
    architecture_review_state, new_architecture_review_state,
    resolve_architecture_finalize_transition, sync_mode_review_requirements,
)
from _long_task.commands.queries import (
    active_phase_reviewers, current_phase_tasks, emit_json, find_attempt, find_plan_task,
    load_task_runtime_documents,
)
from _long_task.evidence_gate import (
    authorize_task_command, blocking_issue_ids_for_phase_revision, blocking_issue_ids_for_revision,
)
from _long_task.phase_policy import (
    actual_work_mode_state, phase_input_revisions, phase_predecessors,
    phase_skip_allowed, rebind_phase_task_inputs,
)
from _long_task.state_access import (
    append_task_events_payloads, load_task_history, read_static_task_plan,
    read_task_state_cache, task_state_path,
)
from _long_task.validation.task import validate_task
from _long_task.lifecycle_helpers import (
    add_cancelled_attempt_work_payload, new_iteration_id, reopen_phase_event_specs,
)
from _long_task import stage_result

def command_publish_artifact(args: argparse.Namespace) -> int:
    """Freeze one PASSED attempt revision and open Review for the current phase."""
    task_dir = Path(args.task_dir)
    errors, _, state, issues = validate_task(task_dir)
    if errors:
        raise LifecycleError('task is invalid before publish-artifact: ' + '; '.join(errors))
    agent = authorize_task_command(args, 'publish-artifact', state, task_dir)
    phase = str(state.get('current_phase') or '')
    if phase == 'development' and bool((state.get('work_mode') or {}).get('architecture_review')):
        step = architecture_review_state(state).get('step')
        if step not in {'SKELETON', 'IMPLEMENTATION'}:
            raise LifecycleError(f'wk -ar development artifact cannot be published from step {step}')
        if step == 'IMPLEMENTATION' and not architecture_review_state(state).get('skeleton_review_result_refs'):
            raise LifecycleError('wk -ar concrete implementation requires passed skeleton Review refs')
    owner_agent = PHASE_DEFINITIONS[phase].get('ownerAgent')
    if agent != owner_agent:
        raise agent_context.AgentAuthorizationError(
            f'Agent {agent} cannot publish {phase} artifact; ownerAgent={owner_agent}'
        )
    (state_document, state), (_, tasks), (_, attempts) = load_task_runtime_documents(task_dir)
    if state.get('current_attempt_id') or state.get('active_task_ids'):
        raise LifecycleError('cannot publish artifact while a task attempt is active')
    artifact = (state.get('artifact_revisions') or {}).get(phase)
    if not isinstance(artifact, dict):
        raise LifecycleError(f'task-state lacks artifact revision for {phase}')
    iteration_id = str(artifact.get('iteration_id') or '')
    phase_tasks = current_phase_tasks(tasks, phase, iteration_id)
    if not phase_tasks:
        raise LifecycleError(f'publish-artifact requires at least one current-iteration task for {phase}')
    incomplete = [
        str(task.get('id')) for task in phase_tasks
        if task.get('status') not in {'PASSED', 'NOT_APPLICABLE'}
    ]
    if incomplete:
        raise LifecycleError(f'cannot publish artifact because current phase tasks are incomplete: {incomplete}')
    attempt = find_attempt(attempts, args.attempt_id)
    if attempt.get('status') != 'PASSED':
        raise LifecycleError(f'publish-artifact requires a PASSED attempt: {args.attempt_id}')
    if attempt.get('phase') != phase or attempt.get('iteration_id') != iteration_id:
        raise LifecycleError('publish-artifact attempt is not from the current phase iteration')
    revision = str(attempt.get('output_revision') or '')
    if not revision:
        raise LifecycleError('publish-artifact attempt has no output_revision')
    blocking_issues = blocking_issue_ids_for_revision(issues, revision)
    if blocking_issues:
        raise LifecycleError(
            f'cannot publish artifact with P0/P1 issues against {revision}: {blocking_issues}'
        )
    attempt_task = find_plan_task(tasks, str(attempt.get('task_id') or ''))
    if attempt_task.get('status') != 'PASSED' or attempt_task.get('output_revision') != revision:
        raise LifecycleError('publish-artifact attempt does not match its current PASSED task')
    evidence_ids = list(dict.fromkeys([
        *(attempt.get('evidence_ids') or []),
        *(attempt.get('command_evidence_ids') or []),
        *(args.evidence_id or []),
    ]))
    if not evidence_ids:
        raise LifecycleError('publish-artifact requires current revision evidence')
    current_evidence = []
    for evidence_id in evidence_ids:
        current_evidence.append(evidence.require_evidence(
            task_dir,
            str(evidence_id),
            expected_revision=revision,
            expected_phase=phase,
        ))

    from _core.review_inputs import collect_review_inputs
    collect_review_inputs(task_dir, phase, revision, current_evidence)

    history = load_task_history(task_dir)
    outcomes = [item for item in history.get('outcomes', []) if isinstance(item, dict)]
    current_outcome = next((
        item for item in outcomes
        if isinstance(item, dict) and item.get('phase') == phase and item.get('is_current') is True
    ), None)
    if isinstance(current_outcome, dict):
        if (
            artifact.get('revision') == revision
            and current_outcome.get('iteration_id') == iteration_id
            and current_outcome.get('output_revision') == revision
            and current_outcome.get('status') == 'RUNNING'
        ):
            emit_json({
                'status': 'ALREADY_PUBLISHED',
                'phase': phase,
                'revision': revision,
                'stageOutcomeId': current_outcome.get('id'),
            })
            return 0
        raise LifecycleError(f'{phase} already has a current StageOutcome; run reopen-phase for a new revision')
    if artifact.get('revision') not in {'', revision}:
        raise LifecycleError(f'{phase} artifact already binds a different revision; run reopen-phase')

    review = (state.get('collaboration_reviews') or {}).get(phase)
    reviewers: list[str] = []
    if isinstance(review, dict):
        if review.get('current_iteration_id') != iteration_id:
            raise LifecycleError(f'{phase} collaboration Review iteration is stale')
        if review.get('artifact_revision') not in {'', revision}:
            raise LifecycleError(f'{phase} collaboration Review binds a different revision; run reopen-phase')
        if review.get('independent_conclusions'):
            raise LifecycleError(f'{phase} collaboration Review already has conclusions; run reopen-phase for a new revision')
        reviewers = active_phase_reviewers(task_dir, state, phase, review)
    missing_reviewers = acceptance.missing_manual_reviewers(
        task_dir,
        phase=phase,
        revision=revision,
        reviewers=reviewers,
    )
    if missing_reviewers:
        repair = (
            f'python3 {shlex.quote(str(acceptance.__file__))} ensure-review -g ProjectManagerAgent '
            f'--task-dir {shlex.quote(str(task_dir))} --phase {shlex.quote(phase)} '
            f'--revision {shlex.quote(revision)}'
        )
        raise LifecycleError(
            f'publish-artifact requires current MANUAL_REVIEW assertions for '
            f'{phase}@{revision}: {missing_reviewers}; run: {repair}'
        )

    timestamp = now()
    artifact['revision'] = revision
    artifact['status'] = 'IN_PROGRESS'
    if isinstance(review, dict):
        review['artifact_revision'] = revision
        review['status'] = 'IN_REVIEW'
    next_agent = reviewers[0] if reviewers else (state.get('project_manager_agent') or PROJECT_MANAGER_AGENT)
    next_action = (
        f'由 {next_agent} 审查 {phase}@{revision}'
        if reviewers else f'由 ProjectManagerAgent 核验并结束 {phase}@{revision}'
    )
    current_task_traces = list(dict.fromkeys(
        str(trace_id) for task in phase_tasks
        for trace_id in (task.get('acceptance_trace_ids') or []) if str(trace_id)
    ))
    current_open_issue_ids = list(dict.fromkeys(
        str(issue.get('id')) for issue in issues
        if isinstance(issue, dict)
        and issue.get('status') in OPEN_ISSUE_STATUSES
        and str(issue.get('artifact_revision') or '') == revision
        and issue.get('id')
    ))
    expected_input_revisions = {
        predecessor: state['artifact_revisions'][predecessor]['revision']
        for predecessor in phase_predecessors(
            phase, bool(actual_work_mode_state(task_dir).get('minimal'))
        )
    }
    expected_changed_files = list(dict.fromkeys(
        str(item) for item in (attempt.get('modified_files') or []) if str(item)
    ))
    generated_stage_result = {
        'phase': phase,
        'produced_by_agent': agent,
        'input_revisions': expected_input_revisions,
        'output_revision': revision,
        'evidence_ids': evidence_ids,
        'trace_refs': current_task_traces,
        'changed_files': expected_changed_files,
        'open_issue_ids': current_open_issue_ids,
        'risks': list(dict.fromkeys(args.open_risk or [])),
        'next_action': next_action,
        'next_agent': next_agent,
    }
    stage_result_source = 'generated-from-passed-attempt'
    stage_result_json = str(getattr(args, 'stage_result', '') or '').strip()
    if stage_result_json:
        try:
            loaded = json.loads(stage_result_json)
        except json.JSONDecodeError as exc:
            raise LifecycleError(f'--stage-result must be valid JSON: {exc}') from exc
        if not isinstance(loaded, dict):
            raise LifecycleError('--stage-result must contain a JSON object')
        generated_stage_result = loaded
        stage_result_source = 'explicit-json'
    stage_errors = stage_result.validate_stage_result(
        generated_stage_result,
        expected_phase=phase,
        expected_agent=agent,
        expected_revision=revision,
        expected_input_revisions=expected_input_revisions,
        expected_evidence_ids=evidence_ids,
        expected_changed_files=expected_changed_files,
        expected_trace_refs=current_task_traces,
        expected_open_issue_ids=current_open_issue_ids,
        expected_risks=list(dict.fromkeys(args.open_risk or [])),
        expected_next_action=next_action,
        expected_next_agent=next_agent,
    )
    if stage_errors:
        raise LifecycleError('invalid StageResult: ' + '; '.join(stage_errors))
    outcome = stage_result.map_to_stage_outcome(generated_stage_result, {
        'id': f'SO-{state.get("target_id")}-{phase.upper()}-I{int(artifact.get("iteration_no")):03d}',
        'round': str(state.get('current_round') or f'{phase.upper()}-I{int(artifact.get("iteration_no")):03d}'),
        'iteration_id': iteration_id,
        'iteration_no': int(artifact.get('iteration_no')),
        'is_current': True,
        'superseded_by': '',
        'status': 'RUNNING',
        'started_at': timestamp,
        'completed_at': '',
    })
    outcomes.append(outcome)
    state['task_status'] = 'PARTIAL'
    state['current_agent'] = agent
    state['next_agent'] = next_agent
    state['next_action'] = next_action
    state['checkpoint_at'] = timestamp
    event_payloads, state, _history = append_task_events_payloads(
        task_dir, task_state_path(task_dir), state_document, state,
        [{
            'type': 'STAGE_OUTCOME_PUBLISHED',
            'entity_id': str(outcome.get('id') or ''),
            'timestamp': timestamp,
            'source': 'long_task.py publish-artifact',
            'payload': {
                'outcome': outcome,
                'stage_result': generated_stage_result,
                'stage_result_source': stage_result_source,
            },
        }],
        synced_at=timestamp,
    )
    transactional_text_write(event_payloads)
    emit_json({
        'status': 'PUBLISHED',
        'phase': phase,
        'revision': revision,
        'stageOutcomeId': outcome['id'],
        'reviewerAgents': reviewers,
        'nextAgent': state['next_agent'],
        'stageResult': generated_stage_result,
        'stageResultSource': stage_result_source,
    })
    return 0
