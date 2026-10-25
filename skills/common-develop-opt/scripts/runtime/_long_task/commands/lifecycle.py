"""Commands lifecycle for the internal runtime."""
from __future__ import annotations
import argparse
import copy
import json
from document_core import agent_context
from runtime import work_mode
from runtime.error_catalog import LifecycleError
from document_core import evidence
from document_core import acceptance
from runtime import risk_detect
from document_core import storage
from runtime._long_task import authorization, task_events
from pathlib import Path
from typing import Any

from document_core.common_utils import now
from runtime._long_task.contracts import *
from runtime._long_task.record_io import *
from runtime._long_task.review_validation import *

from runtime._long_task.architecture_gate import architecture_review_state, expected_required_reviewers, new_architecture_review_state, resolve_architecture_finalize_transition, sync_mode_review_requirements
from runtime._long_task.commands.queries import active_phase_reviewers, current_phase_tasks, emit_json, find_attempt, find_plan_task, load_task_runtime_documents
from runtime._long_task.state_access import read_static_task_plan
from runtime._long_task.evidence_gate import authorize_task_command, blocking_issue_ids_for_phase_revision, blocking_issue_ids_for_revision, revision_fingerprint, validate_attempt_completion_evidence, validate_start_gate
from runtime._long_task.phase_policy import actual_work_mode_state, phase_input_revisions, phase_predecessors, phase_skip_allowed, rebind_phase_task_inputs
from runtime._long_task.state_access import append_task_events_payloads, load_task_history, read_task_state_cache, task_state_path
from runtime._long_task.validation.task import validate_task
from runtime._long_task.validation.task_plan import attempt_event_type
from runtime._long_task.lifecycle_helpers import (
    add_cancelled_attempt_work_payload, new_iteration_id, reopen_phase_event_specs,
)
from runtime._long_task.commands.risk_reviewers import command_list_risk_triggers
from runtime._long_task.commands.attempts import command_start_attempt, command_finish_attempt
from runtime._long_task.commands.artifacts import command_publish_artifact
from runtime._long_task.commands.mode_sync import command_sync_work_mode
from runtime._long_task.commands.phases import command_advance_phase, command_reopen_phase, command_finalize_phase
from runtime._long_task.commands.amendment import command_amend_phase
def command_advance_development_step(args: argparse.Namespace) -> int:
    """Open the concrete implementation iteration after wk -ar skeleton Review passes."""
    task_dir = Path(args.task_dir)
    agent = agent_context.authorize_command(args, 'long_task', 'advance-development-step')
    errors, _, state, _ = validate_task(task_dir)
    if errors:
        raise LifecycleError('task is invalid before advance-development-step: ' + '; '.join(errors))
    if state.get('current_phase') != 'development':
        raise LifecycleError('advance-development-step is only valid in the development phase')
    if not bool((state.get('work_mode') or {}).get('architecture_review')):
        raise LifecycleError('advance-development-step requires wk -ar')
    ar_state = architecture_review_state(state)
    if ar_state.get('step') != 'SKELETON_REVIEW_PASSED':
        raise LifecycleError('wk -ar concrete implementation requires a passed skeleton Review')
    development = (state.get('artifact_revisions') or {}).get('development')
    if not isinstance(development, dict) or development.get('status') != 'PASSED':
        raise LifecycleError('wk -ar skeleton development artifact must be PASSED before step transition')
    skeleton_iteration_id = str(ar_state.get('skeleton_iteration_id') or '')
    skeleton_revision = str(ar_state.get('skeleton_revision') or '')
    skeleton_refs = list(ar_state.get('skeleton_review_result_refs') or [])
    if not skeleton_iteration_id or not skeleton_revision or not skeleton_refs:
        raise LifecycleError('wk -ar skeleton Review state is incomplete')

    tracked = [
        task_state_path(task_dir), storage.path(task_dir, 'plan'),
        task_events.event_path(task_dir),
        acceptance.assertions_path(task_dir),
    ]
    backups = {path: path.read_bytes() for path in tracked if path.exists()}
    try:
        command_reopen_phase(argparse.Namespace(
            task_dir=str(task_dir), from_phase='development',
            reason='wk -ar skeleton Review passed; begin concrete implementation',
            source_revision=skeleton_revision, round='', agent=agent,
            _suppress_output=True,
        ))
        state_path = task_state_path(task_dir)
        state_document, reopened = read_task_state_cache(task_dir, authoritative=True)
        development = (reopened.get('artifact_revisions') or {}).get('development')
        implementation_iteration_id = str(development.get('iteration_id') or '') if isinstance(development, dict) else ''
        timestamp = now()
        reopened['architecture_review'] = {
            'enabled': True,
            'step': 'IMPLEMENTATION',
            'skeleton_iteration_id': skeleton_iteration_id,
            'skeleton_revision': skeleton_revision,
            'skeleton_review_result_refs': skeleton_refs,
            'implementation_iteration_id': implementation_iteration_id,
            'implementation_revision': '',
            'final_review_revision': '',
            'final_review_result_refs': [],
            'updated_at': timestamp,
        }
        sync_mode_review_requirements(
            reopened, minimal=bool((reopened.get('work_mode') or {}).get('minimal'))
        )
        reopened['current_agent'] = agent
        reopened['next_agent'] = 'DevelopAgent'
        reopened['next_action'] = (
            '在已通过的架构代码骨架上实现具体方法；不得改变已审查的方法契约、调用主线和主要分支，'
            '需要改变时先回到 SKELETON iteration'
        )
        reopened['checkpoint_at'] = timestamp
        event_payloads, reopened, _history = append_task_events_payloads(
            task_dir, state_path, state_document, reopened, [], synced_at=timestamp
        )
        transactional_text_write(event_payloads)
        post_errors, _, _, _ = validate_task(task_dir)
        if post_errors:
            raise LifecycleError('wk -ar implementation iteration validation failed: ' + '; '.join(post_errors))
    except Exception:
        transactional_binary_write = {path: content for path, content in backups.items()}
        for path, content in transactional_binary_write.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        raise
    emit_json({
        'status': 'IMPLEMENTATION_READY',
        'skeletonIterationId': skeleton_iteration_id,
        'skeletonRevision': skeleton_revision,
        'implementationIterationId': implementation_iteration_id,
        'nextAgent': 'DevelopAgent',
    })
    return 0

def command_validate(args: argparse.Namespace) -> int:
    errors, warnings, state, issues = validate_task(Path(args.task_dir))
    try:
        current_agent = authorize_task_command(args, 'validate', state, Path(args.task_dir))
    except agent_context.AgentAuthorizationError as exc:
        errors.append(str(exc))
        current_agent = agent_context.selected_agent(args)
    status = 'FAILED' if errors else 'PASSED'
    if getattr(args, 'json', False):
        payload = {'status': status, 'taskDir': str(args.task_dir), 'schemaVersion': state.get('schema_version') if isinstance(state, dict) else None, 'currentPhase': state.get('current_phase') if isinstance(state, dict) else None, 'taskStatus': state.get('task_status') if isinstance(state, dict) else None, 'currentAgent': current_agent, 'openIssueCount': sum((1 for issue in issues if isinstance(issue, dict) and issue.get('status') in OPEN_ISSUE_STATUSES)), 'warnings': warnings, 'errors': errors}
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for warning in warnings:
            print(f'WARN: {warning}')
        for error in errors:
            print(f'ERROR: {error}')
        if not errors:
            print(f'OK: {args.task_dir}')
    return 1 if errors else 0


def command_sync_risk_reviewers(args: argparse.Namespace) -> int:
    """Synchronize current detected risks into code-review tasks and Reviewer registrations."""
    task_dir = Path(args.task_dir)
    agent = agent_context.authorize_command(args, 'long_task', 'sync-risk-reviewers')
    report_errors, report = risk_detect.validate_report(task_dir)
    if report_errors:
        raise LifecycleError('risk detection report is invalid: ' + '; '.join(report_errors))
    if report.get('status') != 'SCANNED':
        raise LifecycleError('sync-risk-reviewers requires a SCANNED risk detection report')
    state_path = task_state_path(task_dir)
    state_document, state = read_task_state_cache(task_dir, authoritative=True)
    plan_path = storage.path(task_dir, 'plan')
    plan_document, tasks = read_static_task_plan(task_dir)
    if not isinstance(state, dict) or not isinstance(tasks, list):
        raise LifecycleError('invalid long-task runtime documents')
    artifacts = state.get('artifact_revisions') if isinstance(state.get('artifact_revisions'), dict) else {}
    development = artifacts.get('development') if isinstance(artifacts, dict) else None
    development_revision = development.get('revision') if isinstance(development, dict) else None
    if report.get('input_revision') != development_revision:
        raise LifecycleError(
            f'risk detection revision is stale: report={report.get("input_revision")} '
            f'current={development_revision}'
        )
    active_risks = risk_detect.active_risk_types(report)
    detections = [
        item for item in report.get('detections', [])
        if isinstance(item, dict) and item.get('status') == 'DETECTED'
        and item.get('risk_type') in active_risks
    ]
    reviews = state.get('collaboration_reviews') if isinstance(state.get('collaboration_reviews'), dict) else {}
    code_review = reviews.get('code_review') if isinstance(reviews, dict) else None
    if not isinstance(code_review, dict):
        raise LifecycleError('task state lacks code_review collaboration review')
    schema_version = int(state.get('schema_version', 0))
    minimal_mode = bool((state.get('work_mode') or {}).get('minimal'))
    protected_reviewers = expected_required_reviewers('code_review', state, minimal=minimal_mode)
    conditional_policy = conditional_reviewers_for(schema_version).get('code_review', {})
    if isinstance(conditional_policy, dict):
        protected_reviewers |= {
            reviewer for reviewer in conditional_policy
            if isinstance(reviewer, str) and reviewer
        }

    additional_reviewers: dict[str, dict[str, Any]] = {}
    triggered_reviewers: set[str] = set()
    for risk_type in sorted(active_risks):
        definition = RISK_REVIEWER_CATALOG.get(risk_type)
        if not isinstance(definition, dict):
            raise LifecycleError(f'risk type has no Reviewer mapping: {risk_type}')
        reviewer = str(definition.get('reviewerAgent') or '')
        triggered_reviewers.add(reviewer)
        evidence_ids = sorted({
            evidence_id
            for item in detections
            if item.get('risk_type') == risk_type
            for evidence_id in (item.get('evidence_ids') or [])
            if isinstance(evidence_id, str) and evidence_id
        })
        if not evidence_ids:
            raise LifecycleError(f'detected risk lacks evidence IDs: {risk_type}')
        rule_ids = sorted({
            str(item.get('rule_id') or '')
            for item in detections if item.get('risk_type') == risk_type
        })
        additional_reviewers[reviewer] = {
            'trigger': risk_type,
            'reason': f'automatically synchronized from risk.json rules={rule_ids}',
            'evidence_ids': evidence_ids,
        }
    code_review['additional_reviewers'] = additional_reviewers
    all_risk_reviewers = {
        str(item.get('reviewerAgent') or '')
        for item in RISK_REVIEWER_CATALOG.values() if isinstance(item, dict)
    }
    removable_risk_reviewers = all_risk_reviewers - protected_reviewers
    code_review_artifact = artifacts.get('code_review') if isinstance(artifacts, dict) else None
    current_iteration_id = code_review_artifact.get('iteration_id') if isinstance(code_review_artifact, dict) else None
    updated_task_ids: list[str] = []
    for task in tasks:
        if not isinstance(task, dict) or task.get('phase') != 'code_review':
            continue
        if current_iteration_id and task.get('iteration_id') != current_iteration_id:
            continue
        task['risk_triggers'] = sorted(active_risks)
        current_reviewers = {
            value for value in (task.get('reviewer_agents') or [])
            if isinstance(value, str) and value not in removable_risk_reviewers
        }
        task['reviewer_agents'] = sorted(current_reviewers | triggered_reviewers)
        updated_task_ids.append(str(task.get('id') or ''))
    event_payloads, state, _history = append_task_events_payloads(
        task_dir, state_path, state_document, state, [], synced_at=now()
    )
    static_tasks = task_events.strip_plan_runtime_fields(tasks)
    plan_value = json.loads(plan_document) if plan_document.strip() else {}
    plan_value['tasks'] = static_tasks
    transactional_text_write({
        **event_payloads,
        plan_path: json.dumps(plan_value, ensure_ascii=False, indent=2) + '\n',
    })
    emit_json({
        'status': 'SYNCED',
        'riskTypes': sorted(active_risks),
        'reviewerAgents': sorted(additional_reviewers),
        'triggeredReviewerAgents': sorted(triggered_reviewers),
        'updatedTaskIds': updated_task_ids,
        'inputRevision': report.get('input_revision'),
    })
    return 0







def command_skip_phase(args: argparse.Namespace) -> int:
    """Record an explicit standard-mode user skip without fabricating PASSED evidence."""
    task_dir = Path(args.task_dir)
    agent = agent_context.authorize_command(args, 'long_task', 'skip-phase')
    state_path = task_state_path(task_dir)
    state_document, state = read_task_state_cache(task_dir, authoritative=True)
    phase = str(args.phase or '')
    if phase not in STANDARD_USER_SKIPPABLE_PHASES:
        raise LifecycleError(
            f'standard-mode user skip only permits phases {list(STANDARD_USER_SKIPPABLE_PHASES)}'
        )
    reason = str(args.reason or '').strip()
    if not reason:
        raise LifecycleError('standard-mode user skip requires a non-empty reason')
    mode = state.get('work_mode') if isinstance(state.get('work_mode'), dict) else {}
    if mode.get('lightweight') or mode.get('minimal'):
        raise LifecycleError('skip-phase is only for standard mode; use -ws/-min policy for mode skips')
    artifacts = state.get('artifact_revisions') if isinstance(state.get('artifact_revisions'), dict) else {}
    artifact = artifacts.get(phase) if isinstance(artifacts.get(phase), dict) else None
    if artifact is None:
        raise LifecycleError(f'missing artifact revision for {phase}')
    if artifact.get('status') == 'NOT_APPLICABLE' and phase_skip_allowed(phase, False, state):
        raise LifecycleError(f'{phase} is already skipped; use reopen-phase before changing the waiver')
    # An exhausted retry budget materializes the task as BLOCKED. A user
    # waiver may still close that current iteration when no work or outcome
    # has been recorded (those conditions are checked below).
    if artifact.get('status') not in {'PENDING', 'STALE', 'IN_PROGRESS', 'BLOCKED'}:
        raise LifecycleError(
            f'cannot skip {phase} from artifact status {artifact.get("status")}; reopen the phase first'
        )
    confirmation_ref = str(getattr(args, 'confirmation_ref', '') or '').strip()
    confirmation = authorization.find_valid_standard_skip(
        task_dir, state, phase=phase, reason=reason, confirmation_ref=confirmation_ref,
    )
    if confirmation is None:
        raise LifecycleError(
            'confirmation-ref does not resolve to an unconsumed wk confirm authorization '
            'bound to this task, phase, iteration, revision, and reason'
        )
    plan_document, static_tasks = read_static_task_plan(task_dir)
    history = load_task_history(task_dir)
    tasks = task_events.materialize_tasks(static_tasks, history) if isinstance(static_tasks, list) else []
    iteration_id = str(artifact.get('iteration_id') or '')
    active_attempts = [
        item for item in history.get('attempts', [])
        if isinstance(item, dict) and item.get('status') == 'RUNNING'
        and item.get('iteration_id') == iteration_id
    ]
    current_outcomes = [
        item for item in history.get('outcomes', [])
        if isinstance(item, dict) and item.get('phase') == phase and item.get('is_current') is True
    ]
    if active_attempts or current_outcomes:
        raise LifecycleError(
            f'cannot skip {phase} after its current iteration has started; finish/cancel work or reopen first'
        )
    timestamp = now()
    iteration_no = int(artifact.get('iteration_no') or 1)
    source_revision = str(artifact.get('revision') or '')
    revision = f'SKIP-{phase.upper()}-{iteration_no}'
    artifact['status'] = 'NOT_APPLICABLE'
    artifact['revision'] = revision
    policy = state.get('work_policy') if isinstance(state.get('work_policy'), dict) else {}
    overrides = policy.setdefault('phase_overrides', {})
    overrides[phase] = {
        'status': 'SKIPPED_BY_USER', 'phase': phase, 'reason': reason,
        'requested_by': 'user', 'recorded_by': agent, 'recorded_at': timestamp,
        'requires_explicit_reopen': True, 'iteration_id': iteration_id,
        'iteration_no': iteration_no, 'revision': revision,
        'source_revision': source_revision,
        'confirmation_ref': confirmation_ref,
        'confirmation_event_id': str(confirmation['event'].get('event_id') or ''),
        'confirmation_event_digest': str(confirmation['event'].get('digest') or ''),
    }
    policy['explicit_reopen_required'] = True
    outcome = {'id': f'SO-{task_dir.name}-{phase.upper()}-SKIP-I{iteration_no:03d}', 'phase': phase, 'round': f'{phase.upper()}-SKIP', 'iteration_id': iteration_id, 'iteration_no': iteration_no, 'is_current': True, 'superseded_by': '', 'status': 'SKIPPED', 'produced_by_agent': agent, 'input_revisions': {}, 'output_revision': revision, 'evidence_ids': [], 'open_risks': [f'SKIPPED_BY_USER: {reason}'], 'started_at': timestamp, 'completed_at': timestamp}
    state['work_policy'] = policy
    state['current_agent'] = agent
    if state.get('current_phase') == phase:
        state['next_agent'] = agent
        state['next_action'] = f'推进已由用户显式跳过的 {phase} 阶段；保留未验证风险'
    state['checkpoint_at'] = timestamp
    runtime_specs: list[dict[str, Any]] = []
    for task in tasks:
        if (
            isinstance(task, dict) and task.get('phase') == phase
            and task.get('iteration_id') == iteration_id
            and task.get('status') in {'PENDING', 'READY', 'REWORK', 'BLOCKED'}
        ):
            runtime_specs.append({
                'type': 'TASK_RUNTIME_SET', 'entity_id': str(task.get('id') or ''),
                'timestamp': timestamp, 'source': 'long_task.py skip-phase',
                'payload': {'runtime': {'status': 'NOT_APPLICABLE'}},
            })
    consumption = authorization.standard_skip_consumption_spec(confirmation, timestamp=timestamp)
    payloads, _, _ = append_task_events_payloads(task_dir, state_path, state_document, state, [consumption,
        {'type': 'STAGE_OUTCOME_RECORDED', 'entity_id': outcome['id'], 'timestamp': timestamp, 'source': 'long_task.py skip-phase', 'payload': {'outcome': outcome}},
    ] + runtime_specs + [
        {'type': 'WORK_POLICY_SET', 'entity_id': str(state.get('target_id') or task_dir.name), 'timestamp': timestamp, 'source': 'long_task.py skip-phase', 'payload': {'policy': copy.deepcopy(policy)}},
    ], synced_at=timestamp)
    transactional_text_write(payloads)
    emit_json({'status': 'SKIPPED_BY_USER', 'phase': phase, 'revision': revision, 'reason': reason})
    return 0
