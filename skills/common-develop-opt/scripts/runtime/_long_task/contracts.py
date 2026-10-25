"""Immutable runtime, review, and record contracts for long-task execution."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from resource_paths import asset_path
from typing import Any, Iterable, Optional

from runtime import contract_normalization
from runtime import review_contract
from document_core import task_plan
from runtime import work_record
from document_core.common_utils import now, sha256_file
_RUNTIME_CONTRACT_PATH = asset_path('assets/runtime/runtime-contract.json')
try:
    _RUNTIME_CONTRACT = contract_normalization.load_runtime_contract(_RUNTIME_CONTRACT_PATH)
except (OSError, json.JSONDecodeError, contract_normalization.ContractNormalizationError) as exc:
    raise RuntimeError(f'cannot load common-develop runtime contract: {exc}') from exc

def _runtime_statuses(name: str) -> set[str]:
    values = _RUNTIME_CONTRACT.get('statuses', {}).get(name, [])
    if not isinstance(values, list) or any((not isinstance(value, str) for value in values)):
        raise RuntimeError(f'runtime contract status domain is invalid: {name}')
    return set(values)
LONG_TASK_SCHEMA_VERSION = _RUNTIME_CONTRACT.get('longTaskSchemaVersion')
if LONG_TASK_SCHEMA_VERSION != 2:
    raise RuntimeError('runtime contract longTaskSchemaVersion must be 2')
PHASE_DEFINITIONS = {phase['id']: phase for phase in _RUNTIME_CONTRACT.get('canonicalPhases', [])}
PHASES = [
    phase['id'] for phase in _RUNTIME_CONTRACT.get('canonicalPhases', [])
    if phase.get('phaseKind', 'CANONICAL') == 'CANONICAL'
]
KNOWN_PHASES = set(PHASE_DEFINITIONS)
PHASE_INDEX = {phase: index for index, phase in enumerate(PHASES)}
if not PHASES:
    raise RuntimeError('runtime contract canonicalPhases must be non-empty')
STANDARD_USER_SKIP_POLICY = _RUNTIME_CONTRACT.get('standardUserSkipPolicy', {})
if not isinstance(STANDARD_USER_SKIP_POLICY, dict):
    raise RuntimeError('runtime contract standardUserSkipPolicy must be an object')
STANDARD_USER_SKIPPABLE_PHASES = tuple(STANDARD_USER_SKIP_POLICY.get('allowedPhases', ()))
if (
    not STANDARD_USER_SKIPPABLE_PHASES
    or any(phase not in PHASES for phase in STANDARD_USER_SKIPPABLE_PHASES)
    or STANDARD_USER_SKIP_POLICY.get('artifactStatus') != 'NOT_APPLICABLE'
    or STANDARD_USER_SKIP_POLICY.get('stageOutcomeStatus') != 'SKIPPED'
    or STANDARD_USER_SKIP_POLICY.get('requiresConfirmationEvent') is not True
    or STANDARD_USER_SKIP_POLICY.get('requiresExplicitReopen') is not True
    or STANDARD_USER_SKIP_POLICY.get('neverCountsAsPassed') is not True
):
    raise RuntimeError('runtime contract standardUserSkipPolicy is invalid')
TASK_STATUSES = _runtime_statuses('task')
ARTIFACT_STATUSES = _runtime_statuses('artifact')
PLAN_TASK_STATUSES = _runtime_statuses('planTask')
ISSUE_STATUSES = _runtime_statuses('issue')
OPEN_ISSUE_STATUSES = {'OPEN', 'ASSIGNED', 'FIXED_PENDING_VERIFY'}
ISSUE_TYPES = {'AMBIGUITY', 'CONFLICT', 'OMISSION', 'NON_TESTABLE', 'INCONSISTENCY', 'RISK', 'SCOPE_DRIFT', 'EVIDENCE_GAP', 'OTHER'}
TRACE_STATUSES = _runtime_statuses('trace')
COLLABORATION_STATUSES = _runtime_statuses('review')
REVIEW_CONCLUSIONS = _runtime_statuses('reviewConclusion')
OUTCOME_STATUSES = _runtime_statuses('stageOutcome')
ATTEMPT_STATUSES = _runtime_statuses('taskAttempt')
WORK_EVENT_STATUSES = _runtime_statuses('workEvent')
DECISION_STATUSES = {'ACTIVE', 'SUPERSEDED', 'REJECTED'}
EXECUTION_POLICY = _RUNTIME_CONTRACT.get('executionPolicy', {})
if not isinstance(EXECUTION_POLICY, dict) or EXECUTION_POLICY.get('mode') != 'SEQUENTIAL' or EXECUTION_POLICY.get('allowParallelTasks') is not False:
    raise RuntimeError('runtime contract executionPolicy must define sequential-only execution')
EXECUTION_MODES = {str(EXECUTION_POLICY['mode'])}
KNOWN_AGENTS = set(_RUNTIME_CONTRACT.get('agents', []))
if not KNOWN_AGENTS:
    raise RuntimeError('runtime contract agents must be non-empty')
PROJECT_MANAGEMENT = _RUNTIME_CONTRACT.get('projectManagement', {})
if not isinstance(PROJECT_MANAGEMENT, dict):
    raise RuntimeError('runtime contract projectManagement must be an object')
PROJECT_MANAGER_FIELD = PROJECT_MANAGEMENT.get('taskStateField')
PROJECT_MANAGER_AGENT = PROJECT_MANAGEMENT.get('defaultAgent')
PROJECT_MANAGER_AGENTS = set(PROJECT_MANAGEMENT.get('allowedAgents', []))
if PROJECT_MANAGER_FIELD != 'project_manager_agent':
    raise RuntimeError('runtime contract projectManagement.taskStateField must be project_manager_agent')
if not isinstance(PROJECT_MANAGER_AGENT, str) or PROJECT_MANAGER_AGENT not in KNOWN_AGENTS:
    raise RuntimeError('runtime contract projectManagement.defaultAgent must be a declared Agent')
if not PROJECT_MANAGER_AGENTS or PROJECT_MANAGER_AGENT not in PROJECT_MANAGER_AGENTS:
    raise RuntimeError('runtime contract projectManagement.allowedAgents must include defaultAgent')
COLLABORATION_REVIEWERS = {phase['id']: set(phase.get('requiredReviewerAgents', [])) for phase in _RUNTIME_CONTRACT.get('canonicalPhases', []) if phase.get('requiredReviewerAgents')}
PHASE_PREDECESSORS = {phase_id: list(definition.get('requiredPredecessors', [])) for phase_id, definition in PHASE_DEFINITIONS.items()}
MINIMAL_PHASE_PREDECESSORS = {phase_id: list(definition.get('minimalModePredecessors', definition.get('requiredPredecessors', []))) for phase_id, definition in PHASE_DEFINITIONS.items()}
CONDITIONAL_REVIEWERS = {phase['id']: dict(phase.get('conditionalReviewerAgents', {})) for phase in _RUNTIME_CONTRACT.get('canonicalPhases', []) if phase.get('conditionalReviewerAgents')}
RISK_REVIEWER_CATALOG = _RUNTIME_CONTRACT.get('riskReviewerCatalog', {})
if not isinstance(RISK_REVIEWER_CATALOG, dict):
    raise RuntimeError('runtime contract riskReviewerCatalog must be an object')
RISK_REVIEWERS_BY_AGENT: dict[str, tuple[str, set[str]]] = {}
for trigger_id, definition in RISK_REVIEWER_CATALOG.items():
    if not isinstance(trigger_id, str) or not isinstance(definition, dict):
        raise RuntimeError('runtime contract risk reviewer definition is invalid')
    agent = definition.get('reviewerAgent')
    phases = definition.get('allowedPhases')
    if not isinstance(agent, str) or not isinstance(phases, list) or any((not isinstance(phase, str) for phase in phases)):
        raise RuntimeError(f'runtime contract risk reviewer is invalid: {trigger_id}')
    if agent in RISK_REVIEWERS_BY_AGENT:
        raise RuntimeError(f'runtime contract maps multiple risk triggers to Agent: {agent}')
    RISK_REVIEWERS_BY_AGENT[agent] = (trigger_id, set(phases))

_REVIEW_CONTRACT_PATH = asset_path('assets/review/review-contract.json')
_REVIEW_CONTRACT = review_contract.load_contract()
REVIEW_PROFILES = _REVIEW_CONTRACT.get('profiles', {})
REVIEW_CRITERIA = _REVIEW_CONTRACT.get('criteriaCatalog', {})
REVIEW_EVIDENCE_TYPES = set((_REVIEW_CONTRACT.get('evidenceTypes') or {}).keys())
CRITERION_STATUSES = set(_REVIEW_CONTRACT.get('criterionStatuses', []))
if not isinstance(REVIEW_PROFILES, dict) or not REVIEW_PROFILES:
    raise RuntimeError('review acceptance contract profiles must be non-empty')
if not isinstance(REVIEW_CRITERIA, dict) or not REVIEW_CRITERIA:
    raise RuntimeError('review acceptance contract criteriaCatalog must be non-empty')
if not REVIEW_EVIDENCE_TYPES or not CRITERION_STATUSES:
    raise RuntimeError('review acceptance contract evidence/status domains must be non-empty')

def review_profile_key(phase: str, reviewer: str) -> str:
    return f'{phase}:{reviewer}'

def review_profile(phase: str, reviewer: str) -> dict[str, Any]:
    try:
        return review_contract.resolve_profile(_REVIEW_CONTRACT, phase, reviewer)
    except ValueError as exc:
        raise RuntimeError(f'missing review acceptance profile: {phase}:{reviewer}') from exc

_RECORD_CONTRACT_PATH = asset_path('assets/storage/long-task/record-contract.json')
try:
    _RECORD_CONTRACT = json.loads(_RECORD_CONTRACT_PATH.read_text(encoding='utf-8'))
except (OSError, json.JSONDecodeError) as exc:
    raise RuntimeError(f'cannot load long-task record contract: {exc}') from exc
if _RECORD_CONTRACT.get('longTaskSchemaVersion') != LONG_TASK_SCHEMA_VERSION:
    raise RuntimeError('long-task record contract version differs from runtime contract')
if _RECORD_CONTRACT.get('reviewRegistrySchemaVersion') != _RUNTIME_CONTRACT.get('reviewRegistrySchemaVersion'):
    raise RuntimeError('long-task record contract reviewRegistrySchemaVersion differs from runtime contract')
WORK_RECORD_SCHEMA_VERSION = _RUNTIME_CONTRACT.get('workRecordSchemaVersion')
if WORK_RECORD_SCHEMA_VERSION != work_record.WORK_RECORD_SCHEMA_VERSION:
    raise RuntimeError('runtime contract workRecordSchemaVersion differs from work_record implementation')
if _RECORD_CONTRACT.get('workRecordSchemaVersion') != WORK_RECORD_SCHEMA_VERSION:
    raise RuntimeError('record contract workRecordSchemaVersion differs from runtime contract')

def _record_fields(name: str) -> set[str]:
    definition = _RECORD_CONTRACT.get('records', {}).get(name)
    fields = definition.get('allowedFields') if isinstance(definition, dict) else None
    if not isinstance(fields, list) or not fields or any(not isinstance(field, str) or not field for field in fields):
        raise RuntimeError(f'long-task record contract is invalid: {name}')
    if len(fields) != len(set(fields)):
        raise RuntimeError(f'long-task record contract contains duplicate fields: {name}')
    return set(fields)

TASK_STATE_FIELDS = _record_fields('taskState')
ARTIFACT_REVISION_FIELDS = _record_fields('artifactRevision')
COLLABORATION_REVIEW_FIELDS = _record_fields('collaborationReview')
ADDITIONAL_REVIEWER_FIELDS = _record_fields('additionalReviewer')
INDEPENDENT_CONCLUSION_FIELDS = _record_fields('independentConclusion')
REVIEW_RESULT_FIELDS = _record_fields('reviewResult')
FULL_INDEPENDENT_CONCLUSION_FIELDS = {'profile_id', 'revision', 'conclusion', 'checked_scope', 'criteria_results', 'finding_ids', 'limitations', 'reviewed_at'}
CRITERION_RESULT_FIELDS = _record_fields('criterionResult')
REVIEW_EVIDENCE_FIELDS = _record_fields('reviewEvidence')
TASK_PLAN_FIELDS = _record_fields('taskPlanItem')
TRACEABILITY_FIELDS = _record_fields('traceabilityItem')
REVIEW_ISSUE_FIELDS = _record_fields('reviewIssue')
STAGE_OUTCOME_FIELDS = _record_fields('stageOutcome')
HANDOFF_FIELDS = _record_fields('handoff')
STALE_EVENT_FIELDS = _record_fields('staleEvent')
TASK_ATTEMPT_FIELDS = _record_fields('taskAttempt')
WORK_EVENT_FIELDS = _record_fields('workEvent')
WORK_MODE_STATE_FIELDS = _record_fields('workModeState')
ARCHITECTURE_REVIEW_STATE_FIELDS = _record_fields('architectureReviewState')
REQUEST_INTAKE_STATE_FIELDS = _record_fields('requestIntakeState')
TASK_EVENT_FIELDS = _record_fields('taskEvent')
EVENT_PROJECTION_FIELDS = _record_fields('eventProjection')
AUTO_REMEDIATION_PROJECTION_FIELDS = _record_fields('autoRemediationProjection')
AMENDMENT_FIELDS = _record_fields('amendmentEvent')
TRACEABILITY_REVALIDATION_FIELDS = _record_fields('traceabilityRevalidation')

__all__ = [name for name in globals() if name.isupper() or name in {
    "review_profile_key", "review_profile"
}]
