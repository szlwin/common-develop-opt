"""Phase policy for the internal runtime."""
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
def phase_predecessors(phase: str, minimal: bool) -> list[str]:
    source = MINIMAL_PHASE_PREDECESSORS if minimal else PHASE_PREDECESSORS
    return list(source.get(phase, []))


def rebind_phase_task_inputs(
    tasks: list[dict[str, Any]], state: dict[str, Any], phase: str, *, minimal: bool
) -> list[str]:
    """将当前 iteration 的任务输入收敛到当前直接前置 revision，清除历史遗留的额外输入。"""
    artifacts = state.get('artifact_revisions') if isinstance(state.get('artifact_revisions'), dict) else {}
    artifact = artifacts.get(phase) if isinstance(artifacts, dict) else None
    iteration_id = str(artifact.get('iteration_id') or '') if isinstance(artifact, dict) else ''
    expected = phase_input_revisions(state, phase, minimal=minimal)
    updated: list[str] = []
    for task in tasks:
        if not isinstance(task, dict) or task.get('phase') != phase:
            continue
        if iteration_id and task.get('iteration_id') != iteration_id:
            continue
        if task.get('input_revisions') != expected:
            task['input_revisions'] = dict(expected)
            updated.append(str(task.get('id') or ''))
    return updated


def workflow_profile(state: dict[str, Any] | None) -> str:
    summary = state.get('request_intake') if isinstance(state, dict) else None
    return str(summary.get('workflow_profile') or '') if isinstance(summary, dict) else ''


STANDARD_SKIP_OVERRIDE_FIELDS = {
    'status', 'phase', 'reason', 'requested_by', 'recorded_by', 'recorded_at',
    'requires_explicit_reopen', 'iteration_id', 'iteration_no', 'revision',
    'source_revision', 'confirmation_ref', 'confirmation_event_id',
    'confirmation_event_digest',
}


def standard_skip_override(state: dict[str, Any] | None, phase: str) -> dict[str, Any] | None:
    if phase not in STANDARD_USER_SKIPPABLE_PHASES or not isinstance(state, dict):
        return None
    work_policy = state.get('work_policy') if isinstance(state.get('work_policy'), dict) else {}
    overrides = work_policy.get('phase_overrides') if isinstance(work_policy.get('phase_overrides'), dict) else {}
    override = overrides.get(phase) if isinstance(overrides, dict) else None
    artifacts = state.get('artifact_revisions') if isinstance(state.get('artifact_revisions'), dict) else {}
    artifact = artifacts.get(phase) if isinstance(artifacts.get(phase), dict) else None
    if not isinstance(override, dict) or not isinstance(artifact, dict):
        return None
    if set(override) != STANDARD_SKIP_OVERRIDE_FIELDS:
        return None
    if (
        override.get('status') != 'SKIPPED_BY_USER'
        or override.get('phase') != phase
        or not is_nonempty_string(override.get('reason'))
        or override.get('requested_by') != 'user'
        or str(override.get('recorded_by') or '') not in PROJECT_MANAGER_AGENTS
        or not _valid_timezone_timestamp(override.get('recorded_at'))
        or override.get('requires_explicit_reopen') is not True
        or artifact.get('status') != STANDARD_USER_SKIP_POLICY['artifactStatus']
        or override.get('iteration_id') != artifact.get('iteration_id')
        or override.get('iteration_no') != artifact.get('iteration_no')
        or override.get('revision') != artifact.get('revision')
        or not is_nonempty_string(override.get('confirmation_ref'))
        or not is_nonempty_string(override.get('confirmation_event_id'))
        or not is_nonempty_string(override.get('confirmation_event_digest'))
    ):
        return None
    return override


def phase_skip_allowed(phase: str, minimal: bool, state: dict[str, Any] | None = None) -> bool:
    mode_state = (state or {}).get('work_mode') if isinstance((state or {}).get('work_mode'), dict) else {}
    lightweight = bool(mode_state.get('lightweight'))
    historical = {
        str(value) for value in (mode_state.get('skipped_phases') or [])
        if isinstance(value, str) and value
    }
    standard_override = standard_skip_override(state, phase) is not None
    return (
        (lightweight and phase in set(work_mode.LIGHTWEIGHT_PHASES))
        or (minimal and phase in set(work_mode.MINIMAL_PHASES))
        or phase in historical
        or phase in workflow_skipped_phases(state)
        or standard_override
    )


def _valid_timezone_timestamp(value: Any) -> bool:
    if not is_nonempty_string(value):
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def historical_lightweight_mode(state: dict[str, Any] | None) -> bool:
    mode_state = (state or {}).get('work_mode') if isinstance((state or {}).get('work_mode'), dict) else {}
    return bool(mode_state.get('lightweight') or mode_state.get('lightweight_waived'))


def validate_work_mode_state(task_dir: Path, state: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    recorded = state.get('work_mode')
    if not isinstance(recorded, dict):
        errors.append('work_mode must be an object')
        return {}
    validate_allowed_fields(recorded, WORK_MODE_STATE_FIELDS, 'work_mode', errors)
    try:
        actual = actual_work_mode_state(task_dir)
    except (OSError, ValueError, work_mode.WorkModeError) as exc:
        errors.append(str(exc))
        return {}
    for key in ('ref', 'digest', 'model_code', 'page_design', 'minimal', 'auto', 'architecture_review', 'git_checkpoint', 'git_push', 'review_only', 'lightweight'):
        # Events without an explicit policy use the standard mode; an enabled
        # marker in work.md still requires an explicit sync event.
        recorded_value = bool(recorded.get(key, False)) if key in {'git_push', 'review_only'} else recorded.get(key)
        if recorded_value != actual.get(key):
            errors.append(f'work_mode.{key} differs from work.md; run sync-work-mode')
    skipped_phases = recorded.get('skipped_phases', [])
    known_skip_phases = set(PHASES) | set(work_mode.LIGHTWEIGHT_PHASES) | set(work_mode.MINIMAL_PHASES)
    if not isinstance(skipped_phases, list) or any(
        not isinstance(value, str) or value not in known_skip_phases for value in skipped_phases
    ):
        errors.append('work_mode.skipped_phases must be a list of known phases')
    if not isinstance(recorded.get('lightweight_waived', False), bool):
        errors.append('work_mode.lightweight_waived must be boolean')
    minimal_skipped_phases = recorded.get('minimal_skipped_phases', [])
    if not isinstance(minimal_skipped_phases, list) or any(
        not isinstance(value, str) or value not in set(work_mode.MINIMAL_PHASES)
        for value in minimal_skipped_phases
    ):
        errors.append('work_mode.minimal_skipped_phases must be a list of minimal phases')
    if not is_nonempty_string(recorded.get('synced_at')):
        errors.append('work_mode.synced_at must not be empty')
    work_policy = state.get('work_policy')
    if not isinstance(work_policy, dict):
        errors.append('work_policy must be an object')
    else:
        overrides = work_policy.get('phase_overrides', {})
        if not isinstance(overrides, dict):
            errors.append('work_policy.phase_overrides must be an object')
        else:
            for phase, override in overrides.items():
                prefix = f'work_policy.phase_overrides.{phase}'
                if phase not in PHASES:
                    errors.append(f'{prefix} references unknown phase')
                    continue
                if not isinstance(override, dict):
                    errors.append(f'{prefix} must be an object')
                    continue
                missing = sorted(STANDARD_SKIP_OVERRIDE_FIELDS - set(override))
                unknown = sorted(set(override) - STANDARD_SKIP_OVERRIDE_FIELDS)
                if missing:
                    errors.append(f'{prefix} missing fields: {missing}')
                if unknown:
                    errors.append(f'{prefix} contains unknown fields: {unknown}')
                if phase not in STANDARD_USER_SKIPPABLE_PHASES:
                    errors.append(f'{prefix} is not allowed by standardUserSkipPolicy')
                if override.get('status') != 'SKIPPED_BY_USER':
                    errors.append(f'{prefix}.status must be SKIPPED_BY_USER')
                if override.get('phase') != phase:
                    errors.append(f'{prefix}.phase must match its map key')
                if not is_nonempty_string(override.get('reason')):
                    errors.append(f'{prefix}.reason must not be empty')
                if override.get('requested_by') != 'user':
                    errors.append(f'{prefix}.requested_by must be user')
                if str(override.get('recorded_by') or '') not in PROJECT_MANAGER_AGENTS:
                    errors.append(f'{prefix}.recorded_by must be an authorized ProjectManagerAgent')
                if not _valid_timezone_timestamp(override.get('recorded_at')):
                    errors.append(f'{prefix}.recorded_at must be a timezone-aware ISO-8601 timestamp')
                if override.get('requires_explicit_reopen') is not True:
                    errors.append(f'{prefix}.requires_explicit_reopen must be true')
                for field in ('confirmation_ref', 'confirmation_event_id', 'confirmation_event_digest'):
                    if not is_nonempty_string(override.get(field)):
                        errors.append(f'{prefix}.{field} must not be empty')
                artifact = (state.get('artifact_revisions') or {}).get(phase)
                if isinstance(artifact, dict):
                    if override.get('iteration_id') != artifact.get('iteration_id'):
                        errors.append(f'{prefix}.iteration_id must match the current artifact iteration')
                    if override.get('iteration_no') != artifact.get('iteration_no'):
                        errors.append(f'{prefix}.iteration_no must match the current artifact iteration')
                    if override.get('revision') != artifact.get('revision'):
                        errors.append(f'{prefix}.revision must match the current artifact revision')
                    if artifact.get('status') != STANDARD_USER_SKIP_POLICY['artifactStatus']:
                        errors.append(f'{prefix} requires artifact status {STANDARD_USER_SKIP_POLICY["artifactStatus"]}')
                _validate_standard_skip_audit(task_dir, phase, override, prefix=prefix, errors=errors)
            expected_explicit_reopen = bool(
                overrides or (recorded.get('lightweight_waived') and not recorded.get('lightweight'))
            )
            if work_policy.get('explicit_reopen_required') is not expected_explicit_reopen:
                errors.append(
                    'work_policy.explicit_reopen_required must match active standard or closed-lightweight waivers'
                )
    return actual


def _validate_standard_skip_audit(
    task_dir: Path,
    phase: str,
    override: dict[str, Any],
    *,
    prefix: str,
    errors: list[str],
) -> None:
    try:
        events = task_events.read_event_history(task_dir)
    except (OSError, ValueError) as exc:
        errors.append(f'{prefix} cannot read confirmation history: {exc}')
        return
    confirmation = next((
        event for event in events
        if event.get('type') == 'AUTHORIZATION_BATCH_CONFIRMED'
        and event.get('event_id') == override.get('confirmation_event_id')
        and event.get('digest') == override.get('confirmation_event_digest')
        and isinstance(event.get('payload'), dict)
        and event['payload'].get('requestFingerprint') == override.get('confirmation_ref')
    ), None)
    if not isinstance(confirmation, dict):
        errors.append(f'{prefix} confirmation does not resolve to its bound event')
        return
    payload = confirmation['payload']
    expected_confirmation = {
        'taskDir': str(Path(task_dir).resolve()), 'taskId': str(task_dir.name),
        'phase': phase, 'iterationId': override.get('iteration_id'),
        'iterationNo': override.get('iteration_no'), 'revision': override.get('source_revision'),
        'reason': override.get('reason'), 'agent': override.get('recorded_by'),
        'mode': 'standard-skip', 'atomicActions': [{'phase': phase, 'action': 'SKIP'}],
    }
    if confirmation.get('source') != 'wk.confirm' or any(
        payload.get(key) != value for key, value in expected_confirmation.items()
    ):
        errors.append(f'{prefix} confirmation fields do not match the standard skip')
    fingerprint_fields = {
        key: value for key, value in expected_confirmation.items() if key != 'atomicActions'
    }
    expected_fingerprint = hashlib.sha256(
        json.dumps(fingerprint_fields, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()
    if payload.get('requestFingerprint') != expected_fingerprint:
        errors.append(f'{prefix} confirmation fingerprint is invalid')
    consumptions = [
        event for event in events
        if event.get('type') == 'AUTHORIZATION_BATCH_CONSUMED'
        and isinstance(event.get('payload'), dict)
        and event['payload'].get('purpose') == 'STANDARD_PHASE_SKIP'
        and event['payload'].get('requestFingerprint') == override.get('confirmation_ref')
    ]
    if len(consumptions) != 1:
        errors.append(f'{prefix} confirmation must be consumed exactly once')
        return
    consumption = consumptions[0]
    expected_consumption = {
        'purpose': 'STANDARD_PHASE_SKIP',
        'requestFingerprint': override.get('confirmation_ref'),
        'confirmationEventId': override.get('confirmation_event_id'),
        'confirmationEventDigest': override.get('confirmation_event_digest'),
        'taskId': str(task_dir.name), 'phase': phase,
        'iterationId': override.get('iteration_id'), 'iterationNo': override.get('iteration_no'),
        'sourceRevision': override.get('source_revision'),
    }
    if consumption.get('source') != 'long_task.py skip-phase' or any(
        consumption['payload'].get(key) != value for key, value in expected_consumption.items()
    ):
        errors.append(f'{prefix} consumption fields do not match confirmation and waiver')
    if int(confirmation.get('seq') or 0) >= int(consumption.get('seq') or 0):
        errors.append(f'{prefix} confirmation must precede consumption')
    outcomes = [
        event for event in events
        if event.get('type') == 'STAGE_OUTCOME_RECORDED'
        and isinstance(event.get('payload'), dict)
        and isinstance(event['payload'].get('outcome'), dict)
        and event['payload']['outcome'].get('phase') == phase
        and event['payload']['outcome'].get('iteration_id') == override.get('iteration_id')
        and event['payload']['outcome'].get('output_revision') == override.get('revision')
    ]
    if len(outcomes) != 1:
        errors.append(f'{prefix} must bind exactly one standard skip StageOutcome')
        return
    outcome_event = outcomes[0]
    outcome = outcome_event['payload']['outcome']
    expected_outcome = {
        'phase': phase, 'iteration_id': override.get('iteration_id'),
        'iteration_no': override.get('iteration_no'), 'is_current': True,
        'status': STANDARD_USER_SKIP_POLICY['stageOutcomeStatus'],
        'produced_by_agent': override.get('recorded_by'),
        'output_revision': override.get('revision'), 'evidence_ids': [],
        'open_risks': [f'SKIPPED_BY_USER: {override.get("reason")}'],
        'started_at': override.get('recorded_at'), 'completed_at': override.get('recorded_at'),
    }
    if outcome_event.get('source') != 'long_task.py skip-phase' or any(
        outcome.get(key) != value for key, value in expected_outcome.items()
    ):
        errors.append(f'{prefix} StageOutcome fields do not match the standard skip')
    if int(consumption.get('seq') or 0) + 1 != int(outcome_event.get('seq') or 0):
        errors.append(f'{prefix} confirmation consumption must immediately precede StageOutcome')
    if consumption.get('timestamp') != override.get('recorded_at'):
        errors.append(f'{prefix} recorded_at must match the consumption timestamp')


def actual_work_mode_state(task_dir: Path) -> dict[str, Any]:
    version_root = version_root_from_task_dir(task_dir)
    path = version_root / 'work.md'
    if not path.is_file():
        raise ValueError(f'missing work mode source: {path}')
    mode = work_mode.validate_work_file(path)
    return {
        'ref': f'version/{version_root.name}/work.md',
        'digest': sha256_file(path),
        'model_code': mode.model_code,
        'page_design': mode.page_design,
        'minimal': mode.minimal,
        'auto': mode.auto,
        'architecture_review': mode.architecture_review,
        'git_checkpoint': mode.git_checkpoint,
        'git_push': mode.git_push,
        'review_only': mode.review_only,
        'lightweight': mode.lightweight,
    }


def phase_input_revisions(state: dict[str, Any], phase: str, *, minimal: bool) -> dict[str, str]:
    """返回当前阶段直接前置产物的 canonical revision 快照。"""
    artifacts = state.get('artifact_revisions') if isinstance(state.get('artifact_revisions'), dict) else {}
    return {
        predecessor: str((artifacts.get(predecessor) or {}).get('revision') or '')
        for predecessor in phase_predecessors(phase, minimal)
    }


def workflow_skipped_phases(state: dict[str, Any] | None) -> set[str]:
    profile = workflow_profile(state)
    definition = _RUNTIME_CONTRACT.get('workflowProfiles', {}).get(profile, {})
    values = definition.get('skippedPhases') if isinstance(definition, dict) else []
    return {str(value) for value in values} if isinstance(values, list) else set()
