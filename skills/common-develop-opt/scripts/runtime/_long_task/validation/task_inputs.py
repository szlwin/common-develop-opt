"""Input and registry validation for a task runtime."""
from __future__ import annotations
from runtime._long_task.validation.task_support import *

def _validate_task_section_1(ctx):
    errors = ctx.value('errors')
    history = ctx.value('history')
    path = ctx.value('path')
    prevalidated = ctx.value('prevalidated')
    schema_version = ctx.value('schema_version')
    state = ctx.value('state')
    task_dir = ctx.value('task_dir')
    trace_path = ctx.value('trace_path')
    warnings = ctx.value('warnings')
    acceptance_assertions = None
    actual_mode = None
    artifacts = None
    attempts = None
    collaboration_reviewers = None
    current_artifact_revisions = None
    current_phase = None
    evidence_entries = None
    item = None
    key = None
    minimal_mode = None
    outcomes = None
    phase = None
    phases = None
    risk_report = None
    tasks = None
    trace_ids = None
    traceability = None
    value = None
    work_events = None
    phases = phases_for(schema_version)
    collaboration_reviewers = reviewers_for(schema_version)
    # Avoid eager evaluation of the fallback: ``dict.get(key, expensive())``
    # evaluates the registry scan even when a caller already supplied a
    # prevalidated snapshot.  Reopen-heavy tasks otherwise pay for the same
    # historical Evidence validation on every gate.
    if 'evidence' in prevalidated:
        evidence_errors, evidence_entries = prevalidated['evidence']
    else:
        artifact_values = state.get('artifact_revisions') if isinstance(state.get('artifact_revisions'), dict) else {}
        current_revisions = {
            str(item.get('revision'))
            for item in artifact_values.values()
            if isinstance(item, dict)
            and item.get('status') in {'IN_PROGRESS', 'PASSED'}
            and str(item.get('revision') or '')
        }
        evidence_errors, evidence_entries = evidence.validate_registry(
            task_dir,
            current_revisions=current_revisions,
        )
    errors.extend(evidence_errors)
    if 'reviews' in prevalidated:
        review_registry_errors, review_records = prevalidated['reviews']
    else:
        review_registry_errors, review_records = review_store.validate_registry(task_dir)
    errors.extend(review_registry_errors)
    if 'acceptance' in prevalidated:
        acceptance_errors, acceptance_assertions = prevalidated['acceptance']
    else:
        acceptance_errors, acceptance_assertions = acceptance.validate_document(task_dir)
    errors.extend(acceptance_errors)
    if 'risk' in prevalidated:
        (risk_errors, risk_report) = prevalidated['risk']
    else:
        artifact_state = state.get('artifact_revisions') if isinstance(state.get('artifact_revisions'), dict) else {}
        development_state = artifact_state.get('development') if isinstance(artifact_state, dict) else None
        require_current_risk_inputs = (
            isinstance(development_state, dict)
            and development_state.get('status') == 'PASSED'
        )
        (risk_errors, risk_report) = risk_detect.validate_report(
            task_dir, verify_input_digest=require_current_risk_inputs
        )
    errors.extend(risk_errors)
    traceability: list[dict[str, Any]] = []
    if trace_path.is_file():
        trace_document = storage.read_json(trace_path, default={})
        traceability = trace_document.get('items', []) if isinstance(trace_document, dict) else []
    else:
        errors.append(f'missing required file: {trace_path}')
    tasks: list[dict[str, Any]] = []
    work_events: list[dict[str, Any]] = []
    plan_path = storage.path(task_dir, 'plan')
    plan_document = storage.read_json(plan_path, default={})
    tasks = plan_document.get('tasks', []) if isinstance(plan_document, dict) else []
    attempts = [item for item in history.get('attempts', []) if isinstance(item, dict)]
    outcomes = [item for item in history.get('outcomes', []) if isinstance(item, dict)]
    if not task_events.event_path(task_dir).is_file():
        errors.append('task_events.jsonl is required')
    development_plan_file = task_dir / task_plan.PLAN_FILENAME
    if development_plan_file.is_file():
        current_phase = str(state.get('current_phase') or '')
        if current_phase in phases and phases.index(current_phase) >= phases.index('implementation_plan'):
            (development_plan, _, development_plan_errors) = task_plan.validate_all(task_dir, require_revision=current_phase != 'implementation_plan')
            errors.extend((f'development-task-plan: {error}' for error in development_plan_errors))
            implementation_artifact = (state.get('artifact_revisions') or {}).get('implementation_plan')
            implementation_passed = isinstance(implementation_artifact, dict) and implementation_artifact.get('status') == 'PASSED'
            if (current_phase != 'implementation_plan' or implementation_passed) and development_plan.get('status') != 'PASSED':
                errors.append('development-task-plan must be PASSED before leaving implementation_plan or starting downstream work')
    git_checkpoint_path = task_dir / 'git_checkpoints.yaml'
    if git_checkpoint_path.is_file():
        try:
            from document_core import git_checkpoint
            (checkpoint_errors, _, _) = git_checkpoint.validate_document(task_dir, require_resolved=False)
            errors.extend(checkpoint_errors)
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
    work_record_path = work_record_path_for_task(task_dir)
    if not work_record_path.is_file():
        errors.append(f'missing required file: {work_record_path}')
    else:
        (work_record_errors, work_events) = work_record.validate_document(work_record_path, allowed_fields=WORK_EVENT_FIELDS, known_agents=KNOWN_AGENTS, statuses=WORK_EVENT_STATUSES)
        errors.extend(work_record_errors)
    required = ['schema_version', 'target_id', 'version', 'task_status', 'current_phase', 'current_round', 'artifact_revisions', 'open_issue_ids', 'next_action', 'next_agent', 'checkpoint_at']
    required.extend(['execution_mode', 'active_task_ids', 'current_attempt_id'])
    required.extend(['current_agent', 'project_manager_agent', 'work_mode', 'request_intake', 'architecture_review'])
    validate_allowed_fields(state, TASK_STATE_FIELDS, 'task-state', errors)
    for key in required:
        if key not in state:
            errors.append(f'task-state missing key: {key}')
    task_statuses = TASK_STATUSES
    if state.get('task_status') not in task_statuses:
        errors.append(f"invalid task_status: {state.get('task_status')}")
    if state.get('current_phase') not in phases:
        errors.append(f"invalid current_phase: {state.get('current_phase')}")
    if not is_nonempty_string(state.get('target_id')):
        errors.append('target_id must not be empty')
    elif state.get('target_id') != task_dir.name:
        errors.append(f"target_id must match task directory: {state.get('target_id')} != {task_dir.name}")
    if not is_nonempty_string(state.get('version')):
        errors.append('version must not be empty')
    if not is_nonempty_string(state.get('current_round')):
        errors.append('current_round must not be empty')
    agent_fields = ('project_manager_agent', 'next_agent', 'current_agent')
    for key in agent_fields:
        if not is_nonempty_string(state.get(key)):
            errors.append(f'{key} must not be empty')
        elif KNOWN_AGENTS and state.get(key) not in KNOWN_AGENTS:
            errors.append(f'unknown {key}: {state.get(key)}')
    project_manager_agent = state.get('project_manager_agent')
    if is_nonempty_string(project_manager_agent) and project_manager_agent not in PROJECT_MANAGER_AGENTS:
        errors.append(f'project_manager_agent is not authorized for project management: {project_manager_agent}; allowed={sorted(PROJECT_MANAGER_AGENTS)}')
    if not is_nonempty_string(state.get('next_action')) and state.get('task_status') != 'COMPLETED':
        errors.append('next_action must not be empty before completion')
    if state.get('execution_mode') not in EXECUTION_MODES:
        errors.append(f"invalid execution_mode: {state.get('execution_mode')}")
    actual_mode = validate_work_mode_state(task_dir, state, errors)
    intake_document = request_intake.validate_task_binding(task_dir, state, actual_mode, errors)
    mode_state = state.get('work_mode') if isinstance(state.get('work_mode'), dict) else {}
    minimal_mode = bool(
        actual_mode.get('minimal')
        or actual_mode.get('lightweight')
        or mode_state.get('skipped_phases')
    )
    artifacts = state.get('artifact_revisions')
    if not isinstance(artifacts, dict):
        errors.append('artifact_revisions must be an object')
        artifacts = {}
    extra_artifacts = sorted(set(artifacts) - set(phases))
    if extra_artifacts:
        errors.append(f'artifact_revisions contains unknown phases: {extra_artifacts}')
    for phase in phases:
        item = artifacts.get(phase)
        if not isinstance(item, dict):
            errors.append(f'artifact_revisions missing phase: {phase}')
            continue
        validate_allowed_fields(item, ARTIFACT_REVISION_FIELDS, f'artifact_revisions.{phase}', errors)
        if item.get('status') not in ARTIFACT_STATUSES:
            errors.append(f"invalid artifact status for {phase}: {item.get('status')}")
        if item.get('status') == 'PASSED' and (not is_nonempty_string(item.get('revision'))):
            errors.append(f'artifact_revisions.{phase} PASSED without revision')
        if not is_nonempty_string(item.get('iteration_id')):
            errors.append(f'artifact_revisions.{phase} missing iteration_id')
        if not isinstance(item.get('iteration_no'), int) or isinstance(item.get('iteration_no'), bool) or item.get('iteration_no') < 1:
            errors.append(f'artifact_revisions.{phase} iteration_no must be a positive integer')
        if item.get('status') == 'NOT_APPLICABLE' and (not phase_skip_allowed(phase, minimal_mode, state)):
            errors.append(f'artifact_revisions.{phase} cannot be NOT_APPLICABLE without an active mode or standard user skip policy')
    validate_architecture_review_state(task_dir, state, artifacts, actual_mode, errors)
    trace_ids: set[str] = set()
    current_artifact_revisions = {str(item.get('revision')) for item in artifacts.values() if isinstance(item, dict) and item.get('status') in {'IN_PROGRESS', 'PASSED'} and item.get('revision')}
    return (acceptance_assertions, actual_mode, artifacts, attempts, collaboration_reviewers, current_artifact_revisions, current_phase, evidence_entries, item, key, minimal_mode, outcomes, phase, phases, risk_report, tasks, trace_ids, traceability, value, work_events)
