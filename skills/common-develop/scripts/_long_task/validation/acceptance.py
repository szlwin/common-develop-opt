"""Validation acceptance for the internal runtime."""
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
def validate_acceptance_contract(
    task_dir: Path,
    assertions: dict[str, dict[str, Any]],
    traceability: list[dict[str, Any]],
    artifacts: dict[str, Any],
    collaboration_reviews: dict[str, Any],
    tasks: list[dict[str, Any]],
    *,
    completion_required: bool,
    errors: list[str],
    validated_entries: Optional[dict[str, dict[str, Any]]] = None,
) -> None:
    acceptance_ids = {
        acceptance_id
        for trace in traceability
        if isinstance(trace, dict) and trace.get('status') != 'NOT_APPLICABLE'
        for acceptance_id in (trace.get('acceptance_criteria') or [])
        if isinstance(acceptance_id, str) and acceptance_id
    }
    assertion_acceptance_ids: set[str] = set()
    assertions_by_acceptance_id: dict[str, list[dict[str, Any]]] = {}
    assertion_source_refs: set[str] = set()
    assertions_by_source_ref: dict[str, list[dict[str, Any]]] = {}
    for assertion_id, item in assertions.items():
        prefix = f'acceptance_assertions.{assertion_id}'
        acceptance_id = str(item.get('acceptance_id') or '')
        assertion_acceptance_ids.add(acceptance_id)
        assertions_by_acceptance_id.setdefault(acceptance_id, []).append(item)
        for value in item.get('source_refs') or []:
            if isinstance(value, str) and value:
                assertion_source_refs.add(value)
                assertions_by_source_ref.setdefault(value, []).append(item)
        phase = str(item.get('phase') or '')
        if phase not in PHASES:
            errors.append(f'{prefix} invalid phase: {phase}')
            continue
        revision = str(item.get('revision') or '')
        status = item.get('status')
        artifact = artifacts.get(phase)
        current_revision = artifact.get('revision') if isinstance(artifact, dict) else None
        if status in {'VERIFIED', 'FAILED', 'BLOCKED', 'NOT_APPLICABLE'} and revision != current_revision:
            errors.append(
                f'{prefix} revision is stale: assertion={revision} current={current_revision}'
            )
        for evidence_id in item.get('evidence_ids') or []:
            try:
                evidence.require_evidence(
                    task_dir,
                    evidence_id,
                    expected_revision=revision,
                    validated_entries=validated_entries,
                )
            except ValueError as exc:
                errors.append(f'{prefix} {exc}')
        verifier = str(item.get('verified_by_agent') or '')
        if verifier and verifier not in KNOWN_AGENTS:
            errors.append(f'{prefix} unknown verified_by_agent: {verifier}')
        if item.get('type') == 'MANUAL_REVIEW' and status == 'VERIFIED':
            parameters = item.get('parameters') if isinstance(item.get('parameters'), dict) else {}
            reviewer = str(parameters.get('reviewer_agent') or verifier)
            review_phase = str(parameters.get('review_phase') or phase)
            review = collaboration_reviews.get(review_phase) if isinstance(collaboration_reviews, dict) else None
            conclusions = review.get('independent_conclusions') if isinstance(review, dict) else None
            conclusion = conclusions.get(reviewer) if isinstance(conclusions, dict) else None
            review_artifact = artifacts.get(review_phase) if isinstance(artifacts, dict) else None
            review_revision = review_artifact.get('revision') if isinstance(review_artifact, dict) else None
            if (
                not isinstance(conclusion, dict)
                or conclusion.get('conclusion') != 'PASSED'
                or conclusion.get('revision') != review_revision
            ):
                errors.append(
                    f'{prefix} MANUAL_REVIEW lacks current PASSED conclusion from {reviewer} for {review_phase}@{review_revision}'
                )
    if completion_required:
        missing = sorted(acceptance_ids - assertion_acceptance_ids)
        if missing:
            errors.append(f'completion has acceptance criteria without structured assertions: {missing}')
        for acceptance_id in sorted(acceptance_ids & assertion_acceptance_ids):
            mapped = assertions_by_acceptance_id.get(acceptance_id, [])
            blocking_mapped = [item for item in mapped if item.get('blocking') is True]
            if not blocking_mapped:
                errors.append(
                    f'completion acceptance criterion has no blocking assertion: {acceptance_id}'
                )
                continue
            unverified = sorted(
                str(item.get('assertion_id') or '')
                for item in blocking_mapped if item.get('status') != 'VERIFIED'
            )
            if unverified:
                errors.append(
                    f'completion acceptance criterion has unverified blocking assertions: '
                    f'{acceptance_id} -> {unverified}'
                )
        expected_result_refs: set[str] = set()
        for task in tasks:
            if not isinstance(task, dict) or task.get('status') == 'NOT_APPLICABLE':
                continue
            phase = str(task.get('phase') or '')
            artifact = artifacts.get(phase)
            if isinstance(artifact, dict) and task.get('iteration_id') != artifact.get('iteration_id'):
                continue
            task_id = str(task.get('id') or '')
            for index, statement in enumerate(task.get('expected_results') or []):
                if isinstance(statement, str) and statement.strip():
                    expected_result_refs.add(f'{task_id}#expected_results/{index}')
        missing_expected_results = sorted(expected_result_refs - assertion_source_refs)
        if missing_expected_results:
            errors.append(
                'completion has task expected_results without structured assertions: '
                f'{missing_expected_results}'
            )
        for source_ref in sorted(expected_result_refs & assertion_source_refs):
            mapped = assertions_by_source_ref.get(source_ref, [])
            blocking_mapped = [item for item in mapped if item.get('blocking') is True]
            if not blocking_mapped:
                errors.append(
                    f'completion expected_result source has no blocking assertion: {source_ref}'
                )
                continue
            unverified = sorted(
                str(item.get('assertion_id') or '')
                for item in blocking_mapped if item.get('status') != 'VERIFIED'
            )
            if unverified:
                errors.append(
                    f'completion expected_result source has unverified blocking assertions: '
                    f'{source_ref} -> {unverified}'
                )
        blocking = [
            assertion_id
            for assertion_id, item in assertions.items()
            if item.get('blocking') is True and item.get('status') != 'VERIFIED'
        ]
        if blocking:
            errors.append(f'completion has unverified blocking assertions: {blocking}')
        unresolved = [
            assertion_id
            for assertion_id, item in assertions.items()
            if item.get('status') in {'UNRESOLVED', 'FAILED', 'BLOCKED'}
        ]
        if unresolved:
            errors.append(f'completion has unresolved acceptance assertions: {unresolved}')
