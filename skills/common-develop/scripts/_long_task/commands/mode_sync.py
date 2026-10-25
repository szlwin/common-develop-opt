"""Domain-specific work-mode synchronization commands extracted from the lifecycle facade."""
from __future__ import annotations

import argparse
import copy
import json
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
    active_phase_reviewers, current_phase_tasks, emit_json, find_plan_task,
    load_task_runtime_documents,
)
from _long_task.evidence_gate import (
    blocking_issue_ids_for_phase_revision, blocking_issue_ids_for_revision,
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

def command_sync_work_mode(args: argparse.Namespace) -> int:
    task_dir = Path(args.task_dir)
    agent = agent_context.authorize_command(args, 'long_task', 'sync-work-mode')
    state_path = task_state_path(task_dir)
    state_document, state = read_task_state_cache(task_dir, authoritative=True)
    plan_path = storage.path(task_dir, 'plan')
    plan_document, tasks = read_static_task_plan(task_dir)
    history = load_task_history(task_dir)
    outcomes = [item for item in history.get('outcomes', []) if isinstance(item, dict)]
    tasks = task_events.materialize_tasks(tasks, history) if isinstance(tasks, list) else tasks
    if not isinstance(state, dict) or not isinstance(tasks, list):
        raise LifecycleError('invalid long-task runtime documents')
    actual = actual_work_mode_state(task_dir)
    timestamp = now()
    previous_mode = state.get('work_mode') if isinstance(state.get('work_mode'), dict) else {}
    previous_minimal = bool(previous_mode.get('minimal'))
    previous_lightweight = bool(previous_mode.get('lightweight'))
    previous_lightweight_waived = bool(previous_mode.get('lightweight_waived'))
    previous_skipped_phases = {
        str(value) for value in (previous_mode.get('skipped_phases') or [])
        if isinstance(value, str) and value in PHASES
    }
    previous_minimal_skipped_phases = {
        str(value) for value in (previous_mode.get('minimal_skipped_phases') or [])
        if isinstance(value, str) and value in work_mode.MINIMAL_PHASES
    }
    # The source set is authoritative for new state.  For a state produced
    # before source tracking existed, conservatively attribute current minimal
    # skips to -min rather than allowing the protection to be bypassed.
    if previous_minimal and 'minimal_skipped_phases' not in previous_mode:
        previous_minimal_skipped_phases = {
            phase for phase in work_mode.MINIMAL_PHASES
            if isinstance((state.get('artifact_revisions') or {}).get(phase), dict)
            and (state.get('artifact_revisions') or {})[phase].get('status') == 'NOT_APPLICABLE'
        }
    previous_architecture_review = bool(previous_mode.get('architecture_review'))
    previous_work_policy = state.get('work_policy') if isinstance(state.get('work_policy'), dict) else {}
    previous_phase_overrides = previous_work_policy.get('phase_overrides') if isinstance(previous_work_policy.get('phase_overrides'), dict) else {}
    minimal = bool(actual['minimal'])
    lightweight = bool(actual.get('lightweight'))
    intake_summary_state = state.get('request_intake') if isinstance(state.get('request_intake'), dict) else {}
    try:
        work_mode.validate_workflow_mode_compatibility(
            str(intake_summary_state.get('workflow_profile') or ''),
            work_mode.WorkMode(
                model_code=bool(actual.get('model_code')),
                page_design=bool(actual.get('page_design')),
                minimal=minimal,
                auto=bool(actual.get('auto')),
                architecture_review=bool(actual.get('architecture_review')),
                git_checkpoint=bool(actual.get('git_checkpoint')),
                git_push=bool(actual.get('git_push')),
                review_only=bool(actual.get('review_only')),
                lightweight=lightweight,
            ),
        )
    except work_mode.WorkModeError as exc:
        raise LifecycleError(str(exc), code=getattr(exc, "code", None)) from exc
    architecture_review_enabled = bool(actual.get('architecture_review'))
    if previous_minimal and not minimal:
        skipped = [
            phase for phase in sorted(previous_minimal_skipped_phases)
            if isinstance((state.get('artifact_revisions') or {}).get(phase), dict)
            and (state.get('artifact_revisions') or {})[phase].get('status') == 'NOT_APPLICABLE'
        ]
        if skipped:
            raise LifecycleError(
                'cannot disable wk -min by deleting skipped history; run reopen-phase '
                '--from-phase test_design after changing work.md, then run sync-work-mode'
            )
    artifacts = state.get('artifact_revisions') or {}
    if previous_architecture_review != architecture_review_enabled:
        development = artifacts.get('development')
        if not isinstance(development, dict):
            raise LifecycleError('development artifact is missing while synchronizing wk -ar')
        development_review = (state.get('collaboration_reviews') or {}).get('development')
        development_started = (
            development.get('revision') not in {'', None}
            or development.get('status') not in {'PENDING', 'STALE'}
            or (isinstance(development_review, dict) and (development_review.get('artifact_revision') or development_review.get('independent_conclusions')))
        )
        if development_started:
            raise LifecycleError('cannot change wk -ar after development has started; reopen from development under the selected mode')
        state['architecture_review'] = new_architecture_review_state(
            architecture_review_enabled, str(development.get('iteration_id') or ''), timestamp
        )
    elif not isinstance(state.get('architecture_review'), dict):
        state['architecture_review'] = new_architecture_review_state(
            architecture_review_enabled, str((artifacts.get('development') or {}).get('iteration_id') or ''), timestamp
        )
    closing_lightweight = previous_lightweight and not lightweight
    historical_lightweight = previous_lightweight_waived or closing_lightweight
    preserved_skips = previous_skipped_phases | (set(work_mode.LIGHTWEIGHT_PHASES) if historical_lightweight else set())
    minimal_skipped_phases = set(previous_minimal_skipped_phases)
    if minimal:
        # A phase already waived by lightweight mode is not evidence that -min
        # created the waiver.  Keep the provenance sets disjoint so closing
        # -min does not misattribute the earlier lightweight skip.
        lightweight_owned = set(work_mode.LIGHTWEIGHT_PHASES) if (lightweight or historical_lightweight) else set()
        minimal_skipped_phases.update(set(work_mode.MINIMAL_PHASES) - lightweight_owned)
    state['work_mode'] = {
        **actual,
        'lightweight_waived': historical_lightweight,
        'skipped_phases': sorted(preserved_skips),
        'minimal_skipped_phases': sorted(minimal_skipped_phases),
        'synced_at': timestamp,
    }
    policy_mode = 'lightweight' if lightweight else ('lightweight_closed' if historical_lightweight else ('minimal' if minimal else 'standard'))
    state['work_policy'] = {
        'mode': policy_mode,
        'tdd': 'not_requested' if historical_lightweight or lightweight else 'requested',
        'test_design': 'not_requested' if historical_lightweight or lightweight else 'requested',
        'review': 'not_requested' if historical_lightweight or lightweight else 'requested',
        'testing': 'not_requested' if historical_lightweight or lightweight else 'requested',
        'explicit_reopen_required': bool(
            (historical_lightweight and not lightweight) or previous_phase_overrides
        ),
        'updated_at': timestamp,
    }
    if previous_phase_overrides:
        state['work_policy']['phase_overrides'] = copy.deepcopy(previous_phase_overrides)
    effective_minimal = minimal or lightweight or bool(preserved_skips)
    sync_mode_review_requirements(state, minimal=effective_minimal)
    skip_reason = str(work_mode.LIGHTWEIGHT_SKIP_REASON if lightweight else work_mode.MINIMAL_SKIP_REASON)
    new_outcomes: list[dict[str, Any]] = []
    task_runtime_specs: list[dict[str, Any]] = []
    active_skipped_phases = set(work_mode.LIGHTWEIGHT_PHASES if lightweight else ())
    if minimal:
        active_skipped_phases.update(work_mode.MINIMAL_PHASES)
    skipped_phases = tuple(dict.fromkeys(sorted(active_skipped_phases)))
    for phase in skipped_phases:
        artifact = artifacts.get(phase)
        if not isinstance(artifact, dict):
            continue
        current_outcome = next((item for item in outcomes if isinstance(item, dict) and item.get('phase') == phase and item.get('is_current') is True), None)
        if minimal or lightweight:
            if artifact.get('status') not in {'PENDING', 'STALE', 'NOT_APPLICABLE'}:
                mode_name = 'wk -ws' if lightweight else 'wk -min'
                raise LifecycleError(f'cannot enable {mode_name} after phase {phase} has started; reopen or reset the version first')
            artifact['status'] = 'NOT_APPLICABLE'
            artifact['revision'] = f'SKIP-{phase.upper()}-{artifact.get("iteration_no")}'
            if current_outcome is None:
                skipped_outcome = {
                    'id': f'SO-{task_dir.name}-{phase.upper()}-SKIP-I{artifact.get("iteration_no")}',
                    'phase': phase,
                    'round': f'{phase.upper()}-SKIP',
                    'iteration_id': artifact.get('iteration_id'),
                    'iteration_no': artifact.get('iteration_no'),
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
            for task in tasks:
                if isinstance(task, dict) and task.get('phase') == phase and task.get('status') in {'PENDING', 'READY', 'REWORK'}:
                    task['status'] = 'NOT_APPLICABLE'
                    task_runtime_specs.append({
                        'type': 'TASK_RUNTIME_SET', 'entity_id': str(task.get('id') or ''),
                        'timestamp': timestamp, 'source': 'long_task.py sync-work-mode',
                        'payload': {'runtime': {'status': 'NOT_APPLICABLE'}},
                    })
        # Turning off lightweight mode is intentionally non-retroactive.  Its
        # skipped Review/Test phases remain NOT_APPLICABLE until the user
        # explicitly reopens one; closing the mode must not manufacture missing
        # Review or Test evidence.
    for phase in phases_for(int(state.get('schema_version') or 0)):
        updated_ids = set(rebind_phase_task_inputs(tasks, state, phase, minimal=effective_minimal))
        for task in tasks:
            if not isinstance(task, dict) or str(task.get('id') or '') not in updated_ids:
                continue
            task_runtime_specs.append({
                'type': 'TASK_RUNTIME_SET',
                'entity_id': str(task.get('id') or ''),
                'timestamp': timestamp,
                'source': 'long_task.py sync-work-mode',
                'payload': {'runtime': {
                    'input_revisions': copy.deepcopy(task.get('input_revisions') or {}),
                }},
            })
    state['current_agent'] = agent
    state['checkpoint_at'] = timestamp
    event_specs = list(task_runtime_specs) + [{
        'type': 'WORK_POLICY_SET',
        'entity_id': str(state.get('target_id') or task_dir.name),
        'timestamp': timestamp,
        'source': 'long_task.py sync-work-mode',
        'payload': {'policy': copy.deepcopy(state['work_policy'])},
    }] + [
        {
            'type': 'STAGE_OUTCOME_RECORDED',
            'entity_id': str(outcome.get('id') or ''),
            'timestamp': timestamp,
            'source': 'long_task.py sync-work-mode',
            'payload': {'outcome': outcome},
        }
        for outcome in new_outcomes
    ]
    payloads, state, _history = append_task_events_payloads(
        task_dir, state_path, state_document, state, event_specs, synced_at=timestamp
    )
    transactional_text_write(payloads)
    emit_json({'status': 'SYNCED', 'workMode': state['work_mode']})
    return 0
