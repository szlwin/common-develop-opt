"""Outcome, handoff, and completion checks for task validation."""
from __future__ import annotations
from pathlib import Path
from _long_task.validation.task_support import *
from _long_task import authorization

def _validate_task_section_4(ctx):
    acceptance_assertions = ctx.value('acceptance_assertions')
    actual_mode = ctx.value('actual_mode')
    artifacts = ctx.value('artifacts')
    collaboration_reviews = ctx.value('collaboration_reviews')
    count = ctx.value('count')
    errors = ctx.value('errors')
    evidence_entries = ctx.value('evidence_entries')
    evidence_id = ctx.value('evidence_id')
    index = ctx.value('index')
    issue = ctx.value('issue')
    issues = ctx.value('issues')
    key = ctx.value('key')
    minimal_mode = ctx.value('minimal_mode')
    outcomes = ctx.value('outcomes')
    phase = ctx.value('phase')
    phases = ctx.value('phases')
    predecessor = ctx.value('predecessor')
    predecessor_artifact = ctx.value('predecessor_artifact')
    prefix = ctx.value('prefix')
    risk_report = ctx.value('risk_report')
    state = ctx.value('state')
    task_dir = ctx.value('task_dir')
    tasks = ctx.value('tasks')
    traceability = ctx.value('traceability')
    warnings = ctx.value('warnings')
    current_outcomes = None
    handoffs = None
    outcome = None
    passed_completion_outcome = None
    review_rounds = state.get('review_rounds', {})
    amendment = state.get('active_amendment')
    if amendment is not None:
        if not isinstance(amendment, dict):
            errors.append('active_amendment must be an object when present')
        else:
            validate_allowed_fields(amendment, AMENDMENT_FIELDS, 'active_amendment', errors)
            for key in (
                'amendment_id', 'phase', 'base_revision', 'revision', 'change_class',
                'impact_plan_digest', 'reason', 'created_at', 'executed_by_agent',
            ):
                if not is_nonempty_string(amendment.get(key)):
                    errors.append(f'active_amendment missing {key}')
            for key in (
                'changed_sections', 'affected_phases', 'affected_task_ids',
                'affected_trace_ids', 'affected_acceptance_ids',
                'preserved_evidence_ids', 'invalidated_evidence_ids',
            ):
                if not validate_string_list(amendment.get(key, [])):
                    errors.append(f'active_amendment {key} must be a string array')
            if amendment.get('phase') not in phases:
                errors.append(f"active_amendment invalid phase: {amendment.get('phase')}")
            if amendment.get('executed_by_agent') != PROJECT_MANAGER_AGENT:
                errors.append('active_amendment must be executed by ProjectManagerAgent')
    if not isinstance(review_rounds, dict):
        errors.append('review_rounds must be an object when present')
        review_rounds = {}
    for (phase, count) in review_rounds.items():
        if phase not in phases:
            errors.append(f'review_rounds has invalid phase: {phase}')
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            errors.append(f'review_rounds.{phase} must be a non-negative integer')
    stale_events = state.get('stale_events', [])
    if not isinstance(stale_events, list):
        errors.append('stale_events must be an array when present')
    else:
        for (index, event) in enumerate(stale_events):
            prefix = f'stale_events[{index}]'
            if not isinstance(event, dict):
                errors.append(f'{prefix} must be an object')
                continue
            validate_allowed_fields(event, STALE_EVENT_FIELDS, prefix, errors)
            if event.get('from_phase') not in phases:
                errors.append(f"{prefix} invalid from_phase: {event.get('from_phase')}")
            if not validate_string_list(event.get('invalidated_phases', [])):
                errors.append(f'{prefix} invalidated_phases must be a string array')
            for key in ('reason', 'created_at', 'executed_by_agent'):
                if not is_nonempty_string(event.get(key)):
                    errors.append(f'{prefix} missing {key}')
            if is_nonempty_string(event.get('executed_by_agent')) and event.get('executed_by_agent') not in KNOWN_AGENTS:
                errors.append(f"{prefix} unknown executed_by_agent: {event.get('executed_by_agent')}")
            if event.get('changed_phase', ''):
                if event.get('changed_phase') not in phases:
                    errors.append(f"{prefix} invalid changed_phase: {event.get('changed_phase')}")
                if event.get('user_confirmed') is not True:
                    errors.append(f'{prefix} upstream boundary confirmation must set user_confirmed=true')
                if not is_nonempty_string(event.get('confirmation_ref')):
                    errors.append(f'{prefix} upstream boundary confirmation missing confirmation_ref')
                if not is_nonempty_string(event.get('source_revision')):
                    errors.append(f'{prefix} upstream boundary confirmation missing source_revision')
                if not re.fullmatch(r'[a-f0-9]{64}', str(event.get('confirmation_fingerprint') or '')):
                    errors.append(f'{prefix} upstream boundary confirmation fingerprint must be SHA-256')
                if not is_nonempty_string(event.get('confirmed_at')):
                    errors.append(f'{prefix} upstream boundary confirmation missing confirmed_at')
                if not is_nonempty_string(event.get('confirmation_event_id')):
                    errors.append(f'{prefix} upstream boundary confirmation missing confirmation_event_id')
                if not is_nonempty_string(event.get('confirmation_event_digest')):
                    errors.append(f'{prefix} upstream boundary confirmation missing confirmation_event_digest')
                authorization.validate_reopen_boundary_audit(
                    task_dir, event, phases, prefix=prefix, errors=errors,
                )
    max_rounds = state.get('max_auto_review_rounds', 3)
    if not isinstance(max_rounds, int) or isinstance(max_rounds, bool) or max_rounds < 1:
        errors.append('max_auto_review_rounds must be a positive integer')
        max_rounds = 3
    if any((issue.get('status') in OPEN_ISSUE_STATUSES and issue.get('severity') in {'P0', 'P1'} for issue in issues)):
        exceeded = [phase for (phase, count) in review_rounds.items() if isinstance(count, int) and count >= max_rounds]
        auto_mode = bool(actual_mode.get('auto'))
        if exceeded and (not auto_mode) and (state.get('task_status') != 'BLOCKED'):
            errors.append(f'review round limit reached with open P0/P1; task must be BLOCKED: {exceeded}')
        elif exceeded and auto_mode:
            warnings.append('wk -auto keeps remediation active after the configured review-round threshold; use auto_remediation.py convergence and stop only on non-converging evidence')
    outcome_ids: set[str] = set()
    passed_completion_outcome = False
    current_outcomes: dict[str, dict[str, Any]] = {}
    for (index, outcome) in enumerate(outcomes):
        prefix = f'stage-outcomes[{index}]'
        if not isinstance(outcome, dict):
            errors.append(f'{prefix} must be an object')
            continue
        validate_allowed_fields(outcome, STAGE_OUTCOME_FIELDS, prefix, errors)
        outcome_id = str(outcome.get('id') or '').strip()
        if not outcome_id:
            errors.append(f'{prefix} missing id')
        elif outcome_id in outcome_ids:
            errors.append(f'duplicate stage outcome id: {outcome_id}')
        outcome_ids.add(outcome_id)
        phase = outcome.get('phase')
        if phase not in phases:
            errors.append(f'{prefix} invalid phase: {phase}')
        if outcome.get('status') not in OUTCOME_STATUSES:
            errors.append(f"{prefix} invalid status: {outcome.get('status')}")
        for key in ('round', 'iteration_id', 'produced_by_agent', 'started_at'):
            if not is_nonempty_string(outcome.get(key)):
                errors.append(f'{prefix} missing {key}')
        if not isinstance(outcome.get('iteration_no'), int) or isinstance(outcome.get('iteration_no'), bool) or outcome.get('iteration_no') < 1:
            errors.append(f'{prefix} iteration_no must be a positive integer')
        if not isinstance(outcome.get('is_current'), bool):
            errors.append(f'{prefix} is_current must be a boolean')
        if not isinstance(outcome.get('superseded_by'), str):
            errors.append(f'{prefix} superseded_by must be a string')
        if outcome.get('is_current') is True:
            if phase in current_outcomes:
                errors.append(f'phase {phase} has multiple current stage outcomes')
            current_outcomes[str(phase)] = outcome
            if outcome.get('superseded_by'):
                errors.append(f'{prefix} current outcome must not have superseded_by')
        elif not outcome.get('superseded_by'):
            errors.append(f'{prefix} historical outcome missing superseded_by')
        if KNOWN_AGENTS and is_nonempty_string(outcome.get('produced_by_agent')):
            if outcome.get('produced_by_agent') not in KNOWN_AGENTS:
                errors.append(f"{prefix} unknown produced_by_agent: {outcome.get('produced_by_agent')}")
        if not isinstance(outcome.get('input_revisions'), dict):
            errors.append(f'{prefix} input_revisions must be an object')
        # StageOutcome evidence is canonicalized as evidence_ids. The old
        # ``evidence`` name belongs to review criteria and is not accepted here.
        for key in ('evidence_ids', 'open_risks'):
            if not validate_string_list(outcome.get(key, [])):
                errors.append(f'{prefix} {key} must be a string array')
        evidence_ids = outcome.get('evidence_ids') if isinstance(outcome.get('evidence_ids'), list) else []
        for evidence_id in evidence_ids:
            if not re.fullmatch(r'EVD-[0-9]{6}', str(evidence_id)):
                errors.append(f'{prefix} invalid evidence_id: {evidence_id}')
        if outcome.get('status') in {'RUNNING', 'PASSED'}:
            if not evidence_ids:
                errors.append(f"{prefix} {outcome.get('status')} without evidence_ids")
            for evidence_id in evidence_ids:
                try:
                    # Historical outcomes may retain Evidence superseded by a
                    # later iteration. Current lifecycle gates require ACTIVE
                    # Evidence for the exact phase and output revision.
                    evidence.require_evidence(
                        task_dir,
                        evidence_id,
                        expected_revision=str(outcome.get('output_revision') or ''),
                        expected_phase=str(phase or ''),
                        allow_historical=outcome.get('is_current') is not True,
                        validated_entries=evidence_entries,
                    )
                except ValueError as exc:
                    errors.append(f'{prefix} {exc}')
        if outcome.get('status') == 'PASSED':
            owner_agent = PHASE_DEFINITIONS.get(str(phase), {}).get('ownerAgent')
            if owner_agent and outcome.get('produced_by_agent') != owner_agent:
                errors.append(f'{prefix} PASSED must be produced by phase owner {owner_agent}')
            if outcome.get('is_current') is True:
                input_revisions = outcome.get('input_revisions') if isinstance(outcome.get('input_revisions'), dict) else {}
                for predecessor in phase_predecessors(str(phase), minimal_mode):
                    predecessor_artifact = artifacts.get(predecessor)
                    expected_revision = predecessor_artifact.get('revision') if isinstance(predecessor_artifact, dict) else None
                    if input_revisions.get(predecessor) != expected_revision:
                        errors.append(f'{prefix} input_revisions.{predecessor} must match current predecessor revision {expected_revision}')
            for key in ('output_revision', 'completed_at'):
                if not is_nonempty_string(outcome.get(key)):
                    errors.append(f'{prefix} PASSED missing {key}')
            phase_artifact = artifacts.get(phase)
            if outcome.get('is_current') is True and isinstance(phase_artifact, dict):
                if phase_artifact.get('revision') != outcome.get('output_revision'):
                    errors.append(f'{prefix} current output_revision does not match current phase artifact revision')
                if phase_artifact.get('iteration_id') != outcome.get('iteration_id'):
                    errors.append(f'{prefix} current iteration_id does not match current phase artifact iteration')
                if phase_artifact.get('iteration_no') != outcome.get('iteration_no'):
                    errors.append(f'{prefix} current iteration_no does not match current phase artifact iteration')
            if phase == 'completion_verification' and outcome.get('is_current') is True and isinstance(phase_artifact, dict):
                passed_completion_outcome = phase_artifact.get('revision') == outcome.get('output_revision')
        if outcome.get('status') == 'SKIPPED':
            if not outcome.get('open_risks'):
                errors.append(f'{prefix} SKIPPED without reason/risk')
            if outcome.get('is_current') is True and (not phase_skip_allowed(str(phase), minimal_mode, state)):
                errors.append(f'{prefix} current SKIPPED outside active workflow skipped phases')
    validate_risk_gate(risk_report, artifacts if isinstance(artifacts, dict) else {}, collaboration_reviews if isinstance(collaboration_reviews, dict) else {}, tasks, errors)
    completion_artifact_for_assertions = artifacts.get('completion_verification') if isinstance(artifacts, dict) else None
    historical_closure = state.get('historical_closure') if isinstance(state.get('historical_closure'), dict) else None
    completion_required = (
        not historical_closure
        and (
            state.get('task_status') == 'COMPLETED'
            or (isinstance(completion_artifact_for_assertions, dict) and completion_artifact_for_assertions.get('status') == 'PASSED')
        )
    )
    validate_acceptance_contract(task_dir, acceptance_assertions, traceability, artifacts if isinstance(artifacts, dict) else {}, collaboration_reviews if isinstance(collaboration_reviews, dict) else {}, tasks, completion_required=completion_required, errors=errors, validated_entries=evidence_entries)
    handoff_dir = task_dir / 'handoff'
    handoffs = sorted(handoff_dir.glob('*.md')) if handoff_dir.is_dir() else []
    return (current_outcomes, handoffs, key, outcome, passed_completion_outcome, phase, prefix)


def _validate_task_section_5(ctx):
    artifact = ctx.value('artifact')
    artifacts = ctx.value('artifacts')
    current_outcomes = ctx.value('current_outcomes')
    derived_open = ctx.value('derived_open')
    errors = ctx.value('errors')
    handoffs = ctx.value('handoffs')
    item = ctx.value('item')
    key = ctx.value('key')
    minimal_mode = ctx.value('minimal_mode')
    outcome = ctx.value('outcome')
    passed_completion_outcome = ctx.value('passed_completion_outcome')
    phase = ctx.value('phase')
    phases = ctx.value('phases')
    prefix = ctx.value('prefix')
    schema_version = ctx.value('schema_version')
    state = ctx.value('state')
    status = ctx.value('status')
    task = ctx.value('task')
    tasks = ctx.value('tasks')
    value = ctx.value('value')
    for handoff_path in handoffs:
        prefix = f'handoff {handoff_path.name}'
        try:
            (_, handoff) = read_block(handoff_path, 'handoff')
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if not isinstance(handoff, dict):
            errors.append(f'{prefix} must be a JSON object')
            continue
        validate_allowed_fields(handoff, HANDOFF_FIELDS, prefix, errors)
        if handoff.get('schema_version') != schema_version:
            errors.append(f'{prefix} schema_version must match task-state: {schema_version}')
        required_handoff_fields = ['target_id', 'version', 'phase', 'round', 'from_agent', 'to_agent', 'next_action', 'created_at']
        for key in required_handoff_fields:
            if not is_nonempty_string(handoff.get(key)):
                errors.append(f'{prefix} missing {key}')
        if handoff.get('target_id') != state.get('target_id'):
            errors.append(f'{prefix} target_id does not match task-state')
        if handoff.get('version') != state.get('version'):
            errors.append(f'{prefix} version does not match task-state')
        if handoff.get('phase') not in phases:
            errors.append(f"{prefix} invalid phase: {handoff.get('phase')}")
        if KNOWN_AGENTS:
            agent_keys = ('from_agent', 'to_agent')
            for key in agent_keys:
                value = handoff.get(key)
                if is_nonempty_string(value) and value not in KNOWN_AGENTS:
                    errors.append(f'{prefix} unknown {key}: {value}')
        for key in ('decision_ids', 'validation', 'open_issues', 'blockers', 'stop_conditions'):
            if not validate_string_list(handoff.get(key, [])):
                errors.append(f'{prefix} {key} must be a string array')
    if state.get('task_status') == 'COMPLETED':
        if derived_open:
            errors.append('COMPLETED task still has open issues')
        stale_or_failed = [phase for (phase, item) in artifacts.items() if isinstance(item, dict) and item.get('status') in {'STALE', 'FAILED', 'BLOCKED'}]
        if stale_or_failed:
            errors.append(f'COMPLETED task has invalid artifacts: {stale_or_failed}')
        incomplete_artifacts = []
        for phase in phases:
            item = artifacts.get(phase)
            status = item.get('status') if isinstance(item, dict) else None
            if status == 'PASSED':
                continue
            if status == 'NOT_APPLICABLE' and phase_skip_allowed(phase, minimal_mode, state):
                continue
            incomplete_artifacts.append(phase)
        if incomplete_artifacts:
            errors.append(f'COMPLETED task has incomplete or illegally skipped artifacts: {incomplete_artifacts}')
        for phase in phases:
            artifact = artifacts.get(phase)
            outcome = current_outcomes.get(phase)
            if not isinstance(artifact, dict) or not isinstance(outcome, dict):
                errors.append(f'COMPLETED task requires current stage outcome for {phase}')
                continue
            expected = 'SKIPPED' if artifact.get('status') == 'NOT_APPLICABLE' else 'PASSED'
            if outcome.get('status') != expected:
                errors.append(f'COMPLETED task outcome for {phase} must be {expected}')
            if outcome.get('iteration_id') != artifact.get('iteration_id'):
                errors.append(f'COMPLETED task outcome iteration mismatch for {phase}')
            if artifact.get('status') == 'PASSED' and outcome.get('output_revision') != artifact.get('revision'):
                errors.append(f'COMPLETED task outcome revision mismatch for {phase}')
        incomplete_tasks = [task.get('id') for task in tasks if isinstance(task, dict) and task.get('status') not in {'PASSED', 'NOT_APPLICABLE'}]
        if incomplete_tasks:
            errors.append(f'COMPLETED task has incomplete plan tasks: {incomplete_tasks}')
        completion_artifact = artifacts.get('completion_verification')
        if not isinstance(completion_artifact, dict) or completion_artifact.get('status') != 'PASSED':
            errors.append('COMPLETED task requires completion_verification artifact PASSED')
        if not passed_completion_outcome:
            errors.append('COMPLETED task requires a PASSED completion_verification outcome')
