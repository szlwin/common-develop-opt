"""Architecture gate for the internal runtime."""
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

ARCHITECTURE_REVIEW_POLICY = work_mode.ARCHITECTURE_REVIEW_POLICY
_AR_FIRST = ARCHITECTURE_REVIEW_POLICY.get('firstStep', {})
_AR_SECOND = ARCHITECTURE_REVIEW_POLICY.get('secondStep', {})
AR_SKELETON_REVIEWERS = tuple(_AR_FIRST.get('requiredReviewers', ()))
AR_IMPLEMENTATION_REVIEWERS = tuple(_AR_SECOND.get('developmentGateReviewers', ()))
_AR_FINAL = ARCHITECTURE_REVIEW_POLICY.get('finalCodeReview', {})
AR_FINAL_CODE_REVIEWERS = tuple(_AR_FINAL.get('requiredReviewers', ()))
AR_STEPS = {
    'DISABLED', 'SKELETON', 'SKELETON_REVIEW_PASSED',
    'IMPLEMENTATION', 'IMPLEMENTATION_READY_FOR_REVIEW', 'IMPLEMENTATION_REVIEW_PASSED',
}
if (
    not AR_SKELETON_REVIEWERS
    or not AR_IMPLEMENTATION_REVIEWERS
    or not AR_FINAL_CODE_REVIEWERS
    or any(
        reviewer not in KNOWN_AGENTS
        for reviewer in (
            *AR_SKELETON_REVIEWERS,
            *AR_IMPLEMENTATION_REVIEWERS,
            *AR_FINAL_CODE_REVIEWERS,
        )
    )
):
    raise RuntimeError('runtime contract wk -ar reviewer policy is invalid')
def resolve_architecture_finalize_transition(
    phase: str, state: dict[str, Any], review: dict[str, Any] | None, *, minimal: bool
) -> tuple[str, list[str]]:
    """Resolve the wk -ar state transition for a finalized phase."""
    if not bool((state.get('work_mode') or {}).get('architecture_review')):
        return ('', [])
    ar_state = architecture_review_state(state)
    step = str(ar_state.get('step') or '')
    if phase == 'development':
        if step == 'SKELETON':
            if not isinstance(review, dict) or expected_required_reviewers('development', state, minimal=minimal) != set(AR_SKELETON_REVIEWERS):
                raise ValueError('wk -ar skeleton must use the declared Architecture and Spec Reviewers')
            refs = _review_result_refs(review, AR_SKELETON_REVIEWERS)
            if len(refs) != len(set(AR_SKELETON_REVIEWERS)):
                raise ValueError('wk -ar skeleton requires passed Review Result refs from every first-step Reviewer')
            return ('SKELETON_REVIEW_PASSED', refs)
        if step == 'IMPLEMENTATION':
            expected = expected_required_reviewers('development', state, minimal=minimal)
            if not isinstance(review, dict) or expected != set(AR_IMPLEMENTATION_REVIEWERS):
                raise ValueError('wk -ar implementation Reviewers differ from the resolved mode policy')
            return ('IMPLEMENTATION_READY_FOR_REVIEW', [])
        raise ValueError(f'wk -ar development cannot finalize from step {step}')
    if phase == 'code_review':
        if step != 'IMPLEMENTATION_READY_FOR_REVIEW':
            raise ValueError('wk -ar final code_review requires a passed concrete implementation revision')
        if not isinstance(review, dict) or expected_required_reviewers('code_review', state, minimal=minimal) != set(AR_FINAL_CODE_REVIEWERS):
            raise ValueError('wk -ar final code_review must use Spec, Engineering Standards, and Architecture Reviewers')
        refs = _review_result_refs(review, AR_FINAL_CODE_REVIEWERS)
        if len(refs) != len(set(AR_FINAL_CODE_REVIEWERS)):
            raise ValueError('wk -ar final code_review requires passed Review Result refs from every final Reviewer')
        return ('IMPLEMENTATION_REVIEW_PASSED', refs)
    return ('', [])


def new_architecture_review_state(enabled: bool, development_iteration_id: str, timestamp: str) -> dict[str, Any]:
    return {
        'enabled': bool(enabled),
        'step': 'SKELETON' if enabled else 'DISABLED',
        'skeleton_iteration_id': development_iteration_id if enabled else '',
        'skeleton_revision': '',
        'skeleton_review_result_refs': [],
        'implementation_iteration_id': '',
        'implementation_revision': '',
        'final_review_revision': '',
        'final_review_result_refs': [],
        'updated_at': timestamp,
    }


def sync_mode_review_requirements(state: dict[str, Any], *, minimal: bool) -> None:
    reviews = state.get('collaboration_reviews')
    if not isinstance(reviews, dict):
        return
    for phase, review in reviews.items():
        if not isinstance(review, dict):
            continue
        expected = expected_required_reviewers(str(phase), state, minimal=minimal)
        if 'required_reviewers' in review:
            review['required_reviewers'] = sorted(expected)
        conclusions = review.get('independent_conclusions')
        if review.get('status') == 'PASSED' and (
            not isinstance(conclusions, dict) or not expected.issubset(set(conclusions))
        ):
            review['status'] = 'REWORK'


def architecture_review_state(state: dict[str, Any]) -> dict[str, Any]:
    value = state.get('architecture_review')
    return value if isinstance(value, dict) else {}


def validate_architecture_review_state(
    task_dir: Path, state: dict[str, Any], artifacts: dict[str, Any], actual_mode: dict[str, Any], errors: list[str]
) -> None:
    value = state.get('architecture_review')
    if not isinstance(value, dict):
        errors.append('architecture_review must be an object')
        return
    validate_allowed_fields(value, ARCHITECTURE_REVIEW_STATE_FIELDS, 'architecture_review', errors)
    enabled = actual_mode.get('architecture_review') is True
    if value.get('enabled') is not enabled:
        errors.append('architecture_review.enabled differs from work.md; run sync-work-mode')
    step = str(value.get('step') or '')
    if step not in AR_STEPS:
        errors.append(f'architecture_review.step is invalid: {step}')
        return
    if not is_nonempty_string(value.get('updated_at')):
        errors.append('architecture_review.updated_at must not be empty')
    if not isinstance(value.get('skeleton_review_result_refs'), list) or any(
        not is_nonempty_string(item) for item in value.get('skeleton_review_result_refs', [])
    ):
        errors.append('architecture_review.skeleton_review_result_refs must be a string array')
    if not isinstance(value.get('final_review_result_refs'), list) or any(
        not is_nonempty_string(item) for item in value.get('final_review_result_refs', [])
    ):
        errors.append('architecture_review.final_review_result_refs must be a string array')
    development = artifacts.get('development') if isinstance(artifacts, dict) else None
    development_iteration = str(development.get('iteration_id') or '') if isinstance(development, dict) else ''
    if not enabled:
        if step != 'DISABLED':
            errors.append('architecture_review.step must be DISABLED when wk -ar is off')
        for key in ('skeleton_iteration_id', 'skeleton_revision', 'implementation_iteration_id', 'implementation_revision', 'final_review_revision'):
            if value.get(key) not in {'', None}:
                errors.append(f'architecture_review.{key} must be empty when wk -ar is off')
        if value.get('skeleton_review_result_refs'):
            errors.append('architecture_review.skeleton_review_result_refs must be empty when wk -ar is off')
        if value.get('final_review_result_refs'):
            errors.append('architecture_review.final_review_result_refs must be empty when wk -ar is off')
        return
    if step == 'DISABLED':
        errors.append('architecture_review.step cannot be DISABLED when wk -ar is on')
        return
    if step == 'SKELETON':
        if value.get('skeleton_iteration_id') != development_iteration:
            errors.append('architecture_review skeleton iteration differs from current development iteration')
        if value.get('skeleton_revision') or value.get('skeleton_review_result_refs'):
            errors.append('architecture_review SKELETON step must not contain passed Review data')
    else:
        skeleton_revision = str(value.get('skeleton_revision') or '')
        refs = list(value.get('skeleton_review_result_refs') or [])
        if not skeleton_revision or not refs:
            errors.append('architecture_review requires the passed skeleton revision and Review refs')
        else:
            seen_reviewers: set[str] = set()
            for review_ref in refs:
                try:
                    record = review_store.require_record(task_dir, review_ref)
                except ValueError as exc:
                    errors.append(f'architecture_review invalid skeleton Review ref {review_ref}: {exc}')
                    continue
                if record.get('review_phase') != 'development' or record.get('review_revision') != skeleton_revision or record.get('result') != 'PASSED':
                    errors.append(f'architecture_review Review {review_ref} does not pass development@{skeleton_revision}')
                reviewer = str(record.get('reviewer') or '')
                if reviewer:
                    seen_reviewers.add(reviewer)
            missing = set(AR_SKELETON_REVIEWERS) - seen_reviewers
            if missing:
                errors.append(f'architecture_review missing passed skeleton Reviewers: {sorted(missing)}')
    if step == 'IMPLEMENTATION':
        if value.get('implementation_iteration_id') != development_iteration:
            errors.append('architecture_review implementation iteration differs from current development iteration')
        if value.get('implementation_revision') or value.get('final_review_revision') or value.get('final_review_result_refs'):
            errors.append('architecture_review IMPLEMENTATION step must not contain passed implementation or final Review data')
    if step in {'IMPLEMENTATION_READY_FOR_REVIEW', 'IMPLEMENTATION_REVIEW_PASSED'}:
        if value.get('implementation_iteration_id') != development_iteration:
            errors.append('architecture_review final implementation iteration differs from current development iteration')
        revision = str(development.get('revision') or '') if isinstance(development, dict) else ''
        if not revision or value.get('implementation_revision') != revision:
            errors.append('architecture_review implementation revision differs from passed development artifact')
    if step == 'IMPLEMENTATION_READY_FOR_REVIEW':
        if value.get('final_review_revision') or value.get('final_review_result_refs'):
            errors.append('architecture_review final Review data must be empty before code_review passes')
        code_review = artifacts.get('code_review') if isinstance(artifacts, dict) else None
        if isinstance(code_review, dict) and code_review.get('status') == 'PASSED':
            errors.append('architecture_review must record IMPLEMENTATION_REVIEW_PASSED when code_review has passed')
    if step == 'IMPLEMENTATION_REVIEW_PASSED':
        code_review = artifacts.get('code_review') if isinstance(artifacts, dict) else None
        final_revision = str(code_review.get('revision') or '') if isinstance(code_review, dict) else ''
        if not final_revision or code_review.get('status') != 'PASSED' or value.get('final_review_revision') != final_revision:
            errors.append('architecture_review final Review revision differs from passed code_review artifact')
        refs = list(value.get('final_review_result_refs') or [])
        seen_reviewers: set[str] = set()
        for review_ref in refs:
            try:
                record = review_store.require_record(task_dir, review_ref)
            except ValueError as exc:
                errors.append(f'architecture_review invalid final Review ref {review_ref}: {exc}')
                continue
            if record.get('review_phase') != 'code_review' or record.get('review_revision') != final_revision or record.get('result') != 'PASSED':
                errors.append(f'architecture_review Review {review_ref} does not pass code_review@{final_revision}')
            reviewer = str(record.get('reviewer') or '')
            if reviewer:
                seen_reviewers.add(reviewer)
        missing = set(AR_FINAL_CODE_REVIEWERS) - seen_reviewers
        if missing:
            errors.append(f'architecture_review missing passed final Reviewers: {sorted(missing)}')


def expected_required_reviewers(
    phase: str, state: dict[str, Any], *, minimal: bool
) -> set[str]:
    mode_state = state.get('work_mode') if isinstance(state.get('work_mode'), dict) else {}
    if bool(mode_state.get('lightweight') or mode_state.get('lightweight_waived')):
        artifacts = state.get('artifact_revisions') if isinstance(state.get('artifact_revisions'), dict) else {}
        reviews = state.get('collaboration_reviews') if isinstance(state.get('collaboration_reviews'), dict) else {}
        artifact = artifacts.get(phase) if isinstance(artifacts, dict) else None
        review = reviews.get(phase) if isinstance(reviews, dict) else None
        if (
            isinstance(artifact, dict)
            and isinstance(review, dict)
            and artifact.get('status') == 'PASSED'
            and review.get('status') == 'PASSED'
            and artifact.get('revision') == review.get('artifact_revision')
        ):
            values = review.get('required_reviewers')
            recorded = {
                str(value) for value in (values if isinstance(values, list) else [])
                if isinstance(value, str) and value
            }
            if recorded:
                return recorded
            conclusions = review.get('independent_conclusions')
            conclusion_agents = set(conclusions) if isinstance(conclusions, dict) else set()
            return set(COLLABORATION_REVIEWERS.get(phase, set())) & conclusion_agents
        return set()
    expected = set(COLLABORATION_REVIEWERS.get(phase, set()))
    ar_state = architecture_review_state(state)
    if bool((state.get('work_mode') or {}).get('architecture_review')) and ar_state.get('enabled') is True:
        step = str(ar_state.get('step') or '')
        if phase == 'development':
            if step in {'SKELETON', 'SKELETON_REVIEW_PASSED'}:
                expected = set(AR_SKELETON_REVIEWERS)
            elif step in {'IMPLEMENTATION', 'IMPLEMENTATION_READY_FOR_REVIEW', 'IMPLEMENTATION_REVIEW_PASSED'}:
                expected = set(AR_IMPLEMENTATION_REVIEWERS)
        elif phase == 'code_review':
            expected = set(AR_FINAL_CODE_REVIEWERS)
    if minimal:
        expected -= set(work_mode.MINIMAL_SUPPRESSED_REVIEWERS.get(phase, []))
    return expected


def _review_result_refs(review: dict[str, Any], reviewers: Iterable[str]) -> list[str]:
    conclusions = review.get('independent_conclusions') if isinstance(review, dict) else {}
    if not isinstance(conclusions, dict):
        return []
    refs: list[str] = []
    for reviewer in reviewers:
        conclusion = conclusions.get(reviewer)
        if not isinstance(conclusion, dict) or conclusion.get('conclusion') != 'PASSED':
            return []
        review_ref = str(conclusion.get('review_result_ref') or '')
        if not review_ref:
            return []
        refs.append(review_ref)
    return list(dict.fromkeys(refs))
