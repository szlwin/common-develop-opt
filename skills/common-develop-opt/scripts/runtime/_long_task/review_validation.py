"""Review evidence and independent-conclusion validation."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from document_core import evidence
from document_core import review_store
from runtime._long_task.contracts import (
    CRITERION_RESULT_FIELDS, CRITERION_STATUSES, FULL_INDEPENDENT_CONCLUSION_FIELDS,
    INDEPENDENT_CONCLUSION_FIELDS, OPEN_ISSUE_STATUSES, REVIEW_CONCLUSIONS,
    REVIEW_CRITERIA, REVIEW_EVIDENCE_FIELDS, REVIEW_PROFILES,
    review_profile, review_profile_key,
)
from runtime._long_task.record_io import (
    is_nonempty_string, read_block, validate_nonempty_string_list,
    validate_string_list,
)

def validate_review_evidence(
    task_dir: Path,
    value: Any,
    prefix: str,
    errors: list[str],
    *,
    expected_revision: str,
    expected_phase: str = '',
    validated_entries: Optional[dict[str, dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        errors.append(f'{prefix} must be an array')
        return []
    validated: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        item_prefix = f'{prefix}[{index}]'
        if not isinstance(item, dict):
            errors.append(f'{item_prefix} must be an object')
            continue
        validate_allowed_fields(item, REVIEW_EVIDENCE_FIELDS, item_prefix, errors)
        evidence_id = item.get('evidence_id')
        if not is_nonempty_string(evidence_id):
            errors.append(f'{item_prefix} missing evidence_id')
        if not is_nonempty_string(item.get('claim')):
            errors.append(f'{item_prefix} missing claim')
        if is_nonempty_string(evidence_id) and is_nonempty_string(item.get('claim')):
            try:
                entry = evidence.require_evidence(
                    task_dir, str(evidence_id), expected_revision=expected_revision,
                    expected_phase=expected_phase,
                    validated_entries=validated_entries,
                )
            except ValueError as exc:
                errors.append(f'{item_prefix} {exc}')
                continue
            roles = sorted(evidence.evidence_types(entry))
            validated.append({
                'evidence_id': str(evidence_id),
                'claim': str(item.get('claim')),
                'type': str(entry.get('type') or ''),
                'types': roles,
                'scope_refs': list(entry.get('scope_refs') or []),
            })
    return validated


def severity_rank(value: str) -> int:
    return {'P0': 3, 'P1': 2, 'P2': 1}.get(value, 0)


def evidence_satisfies_policy(evidence: list[dict[str, Any]], criterion: dict[str, Any]) -> tuple[bool, str]:
    policy = criterion.get('evidencePolicy', {})
    minimum = policy.get('minimumEvidenceCount', 1)
    any_types = set(policy.get('anyOfTypes', []))
    all_types = set(policy.get('allOfTypes', []))
    present: set[str] = set()
    for item in evidence:
        roles = item.get('types')
        if not isinstance(roles, list):
            roles = item.get('used_as_types')
        if isinstance(roles, list):
            present.update(str(role) for role in roles if role)
        elif item.get('type'):
            present.add(str(item.get('type')))
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 0:
        return False, 'criterion evidence policy minimum is invalid'
    if len(evidence) < minimum:
        return False, f'requires at least {minimum} evidence item(s)'
    if any_types and not (present & any_types):
        return False, f'requires one evidence type from {sorted(any_types)}'
    missing_all = sorted(all_types - present)
    if missing_all:
        return False, f'missing required evidence types {missing_all}'
    return True, ''


def validate_independent_conclusion(
    task_dir: Path,
    phase: str,
    reviewer: str,
    conclusion: dict[str, Any],
    artifact_revision: str,
    issues: list[dict[str, Any]],
    errors: list[str],
    *,
    validated_entries: Optional[dict[str, dict[str, Any]]] = None,
) -> None:
    prefix = f'collaboration_reviews.{phase}.{reviewer}'
    compact = conclusion
    if isinstance(compact, dict) and compact.get('review_result_ref'):
        validate_allowed_fields(compact, INDEPENDENT_CONCLUSION_FIELDS, prefix, errors)
        try:
            materialized = review_store.materialize_conclusion(task_dir, compact)
        except ValueError as exc:
            errors.append(f'{prefix} {exc}')
            return
        for key in ('profile_id', 'revision', 'conclusion', 'finding_ids', 'reviewed_at'):
            if compact.get(key) != materialized.get(key):
                errors.append(f'{prefix}.{key} differs from review result {compact.get("review_result_ref")}')
        conclusion = materialized
    else:
        # Transitional support while a newly generated conclusion is validated
        # before it is appended to the compact Review event registry.
        validate_allowed_fields(conclusion, FULL_INDEPENDENT_CONCLUSION_FIELDS, prefix, errors)
    expected_profile_id = review_profile_key(phase, reviewer)
    if conclusion.get('profile_id') != expected_profile_id:
        errors.append(f'{prefix}.profile_id must be {expected_profile_id}')
    if conclusion.get('revision') != artifact_revision:
        errors.append(f'{prefix} revision does not match review artifact')
    review_conclusion = conclusion.get('conclusion')
    if review_conclusion not in REVIEW_CONCLUSIONS:
        errors.append(f'{prefix}.conclusion is invalid: {review_conclusion}')
    if not validate_nonempty_string_list(conclusion.get('checked_scope')):
        errors.append(f'{prefix}.checked_scope must be a non-empty string array')
    if not validate_string_list(conclusion.get('finding_ids')):
        errors.append(f'{prefix}.finding_ids must be a string array')
    if not validate_string_list(conclusion.get('limitations')):
        errors.append(f'{prefix}.limitations must be a string array')
    if not is_nonempty_string(conclusion.get('reviewed_at')):
        errors.append(f'{prefix} missing reviewed_at')
    try:
        profile = review_profile(phase, reviewer)
    except RuntimeError:
        profile = {}
    expected_ids = profile.get('criterionIds', []) if isinstance(profile, dict) else []
    results = conclusion.get('criteria_results')
    if not isinstance(results, list):
        errors.append(f'{prefix}.criteria_results must be an array')
        results = []
    seen: dict[str, dict[str, Any]] = {}
    issue_map = {issue.get('id'): issue for issue in issues if isinstance(issue, dict) and isinstance(issue.get('id'), str)}
    union_findings: set[str] = set()
    has_failed = False
    has_blocked = False
    for index, result in enumerate(results):
        result_prefix = f'{prefix}.criteria_results[{index}]'
        if not isinstance(result, dict):
            errors.append(f'{result_prefix} must be an object')
            continue
        validate_allowed_fields(result, CRITERION_RESULT_FIELDS, result_prefix, errors)
        criterion_id = result.get('criterion_id')
        if not isinstance(criterion_id, str) or criterion_id not in REVIEW_CRITERIA:
            errors.append(f'{result_prefix}.criterion_id is invalid: {criterion_id}')
            continue
        if criterion_id in seen:
            errors.append(f'{prefix}.criteria_results duplicates criterion {criterion_id}')
            continue
        seen[criterion_id] = result
        if criterion_id not in expected_ids:
            errors.append(f'{result_prefix} criterion is not in profile {expected_profile_id}: {criterion_id}')
        status_value = result.get('status')
        if status_value not in CRITERION_STATUSES:
            errors.append(f'{result_prefix}.status is invalid: {status_value}')
            continue
        evidence = validate_review_evidence(
            task_dir, result.get('evidence'), f'{result_prefix}.evidence', errors,
            expected_revision=artifact_revision, expected_phase=phase,
            validated_entries=validated_entries,
        )
        for evidence_item in evidence:
            if criterion_id not in set(evidence_item.get('scope_refs') or []):
                errors.append(
                    f'{result_prefix}.evidence {evidence_item.get("evidence_id")} '
                    f'does not cover criterion {criterion_id}'
                )
        finding_ids = result.get('finding_ids')
        if not validate_string_list(finding_ids):
            errors.append(f'{result_prefix}.finding_ids must be a string array')
            finding_ids = []
        union_findings.update(finding_ids)
        reason = result.get('reason')
        if reason is not None and not isinstance(reason, str):
            errors.append(f'{result_prefix}.reason must be a string')
        criterion = REVIEW_CRITERIA.get(criterion_id, {})
        if status_value in {'PASSED', 'FAILED'}:
            valid_policy, message = evidence_satisfies_policy(evidence, criterion)
            if not valid_policy:
                errors.append(f'{result_prefix}.evidence {message}')
        if status_value == 'PASSED':
            if finding_ids:
                errors.append(f'{result_prefix} PASSED must not reference findings')
        elif status_value == 'FAILED':
            has_failed = True
            if not finding_ids:
                errors.append(f'{result_prefix} FAILED requires finding_ids')
            floor = str(criterion.get('failureSeverityFloor') or 'P1')
            for finding_id in finding_ids:
                issue = issue_map.get(finding_id)
                if not isinstance(issue, dict):
                    errors.append(f'{result_prefix} references unknown finding: {finding_id}')
                    continue
                if issue.get('phase') != phase:
                    errors.append(f'{result_prefix} finding phase differs: {finding_id}')
                if issue.get('artifact_revision') != artifact_revision:
                    errors.append(f'{result_prefix} finding revision differs: {finding_id}')
                if issue.get('raised_by_agent') != reviewer:
                    errors.append(f'{result_prefix} finding was not raised by {reviewer}: {finding_id}')
                if issue.get('status') not in OPEN_ISSUE_STATUSES:
                    errors.append(f'{result_prefix} FAILED finding must remain open until re-review: {finding_id}')
                if severity_rank(str(issue.get('severity'))) < severity_rank(floor):
                    errors.append(f'{result_prefix} finding severity is below {floor}: {finding_id}')
        elif status_value == 'BLOCKED':
            has_blocked = True
            if not is_nonempty_string(reason):
                errors.append(f'{result_prefix} BLOCKED requires reason')
        elif status_value == 'NOT_APPLICABLE':
            if criterion.get('allowNotApplicable') is not True:
                errors.append(f'{result_prefix} criterion does not allow NOT_APPLICABLE')
            if not is_nonempty_string(reason):
                errors.append(f'{result_prefix} NOT_APPLICABLE requires reason')
            if finding_ids:
                errors.append(f'{result_prefix} NOT_APPLICABLE must not reference findings')
    missing = sorted(set(expected_ids) - set(seen))
    extra = sorted(set(seen) - set(expected_ids))
    if missing:
        errors.append(f'{prefix} missing criterion results: {missing}')
    if extra:
        errors.append(f'{prefix} has unexpected criterion results: {extra}')
    declared_findings = set(conclusion.get('finding_ids') or []) if isinstance(conclusion.get('finding_ids'), list) else set()
    if declared_findings != union_findings:
        errors.append(f'{prefix}.finding_ids must equal the union of criteria finding_ids')
    derived = 'BLOCKED' if has_blocked else ('NEEDS_CHANGES' if has_failed else 'PASSED')
    if review_conclusion != derived:
        errors.append(f'{prefix}.conclusion must be {derived} from criteria results')
    if conclusion.get('limitations') and review_conclusion == 'PASSED':
        errors.append(f'{prefix} PASSED must not contain unresolved limitations')


def validate_allowed_fields(value: dict[str, Any], allowed: set[str], prefix: str, errors: list[str]) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        errors.append(f'{prefix} contains unknown fields: {unknown}')

def load_optional_block(path: Path, name: str, expected_type: type, errors: list[str]) -> Any:
    try:
        _, value = read_block(path, name)
    except ValueError as exc:
        errors.append(str(exc))
        return expected_type()
    if not isinstance(value, expected_type):
        errors.append(f"{path}: '{name}' must be {expected_type.__name__}")
        return expected_type()
    return value

__all__ = [
    "validate_review_evidence", "severity_rank", "evidence_satisfies_policy",
    "validate_independent_conclusion", "validate_allowed_fields",
    "load_optional_block",
]
