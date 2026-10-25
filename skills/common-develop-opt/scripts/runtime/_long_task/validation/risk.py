"""Validation risk for the internal runtime."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
import re
import sys
from document_core import agent_context
from runtime import work_mode
from runtime import doc_layout
from document_core import evidence
from runtime import work_record
from document_core import acceptance
from runtime import risk_detect
from document_core import review_store
from runtime import request_intake
from document_core import task_plan
from checks import cli_help
from runtime._long_task import task_events
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from document_core.common_utils import now, sha256_file
from runtime._long_task.contracts import *
from runtime._long_task.record_io import *
from runtime._long_task.review_validation import *
def is_current_risk_reviewer_registration(
    report: dict[str, Any],
    artifacts: dict[str, Any],
    phase: str,
    reviewer: str,
    trigger_info: Any,
) -> bool:
    """Return whether an overlapping Reviewer entry is exact current risk metadata.

    ``additional_reviewers`` is both a Reviewer source and the audit registration
    for detected risks. A Reviewer can already be required by the phase while
    still needing that registration. Only the current code-review detection,
    its catalog Reviewer, and its complete Evidence set qualify for this narrow
    overlap; arbitrary duplicate Reviewer declarations remain invalid.
    """
    if phase != 'code_review' or not isinstance(trigger_info, dict):
        return False
    risk_type = str(trigger_info.get('trigger') or '')
    definition = RISK_REVIEWER_CATALOG.get(risk_type)
    if not isinstance(definition, dict) or definition.get('reviewerAgent') != reviewer:
        return False
    if report.get('status') != 'SCANNED' or risk_type not in risk_detect.active_risk_types(report):
        return False
    development = artifacts.get('development') if isinstance(artifacts, dict) else None
    development_revision = development.get('revision') if isinstance(development, dict) else None
    if not development_revision or report.get('input_revision') != development_revision:
        return False
    detections = [
        item for item in report.get('detections', [])
        if isinstance(item, dict)
        and item.get('status') == 'DETECTED'
        and item.get('risk_type') == risk_type
        and item.get('required_reviewer') == reviewer
    ]
    expected_evidence = {
        evidence_id
        for item in detections
        for evidence_id in (item.get('evidence_ids') or [])
        if isinstance(evidence_id, str) and evidence_id
    }
    registered_evidence = trigger_info.get('evidence_ids')
    return (
        bool(expected_evidence)
        and validate_nonempty_string_list(registered_evidence)
        and len(registered_evidence) == len(expected_evidence)
        and set(registered_evidence) == expected_evidence
    )


def validate_risk_gate(
    report: dict[str, Any],
    artifacts: dict[str, Any],
    collaboration_reviews: dict[str, Any],
    tasks: list[dict[str, Any]],
    errors: list[str],
) -> None:
    code_review_artifact = artifacts.get('code_review') if isinstance(artifacts, dict) else None
    if not isinstance(code_review_artifact, dict) or code_review_artifact.get('status') != 'PASSED':
        return
    development_artifact = artifacts.get('development') if isinstance(artifacts, dict) else None
    development_revision = development_artifact.get('revision') if isinstance(development_artifact, dict) else None
    if report.get('status') != 'SCANNED':
        errors.append('code_review PASSED without SCANNED risk detection report')
        return
    if report.get('input_revision') != development_revision:
        errors.append(
            f'code_review risk detection revision differs from development: '
            f'{report.get("input_revision")} != {development_revision}'
        )
    active_risks = risk_detect.active_risk_types(report)
    review = collaboration_reviews.get('code_review') if isinstance(collaboration_reviews, dict) else None
    additional = review.get('additional_reviewers') if isinstance(review, dict) else None
    conclusions = review.get('independent_conclusions') if isinstance(review, dict) else None
    additional = additional if isinstance(additional, dict) else {}
    conclusions = conclusions if isinstance(conclusions, dict) else {}
    for risk_type in sorted(active_risks):
        definition = RISK_REVIEWER_CATALOG.get(risk_type, {})
        reviewer = definition.get('reviewerAgent')
        trigger = additional.get(reviewer)
        if not isinstance(trigger, dict) or trigger.get('trigger') != risk_type:
            errors.append(f'code_review missing detected risk reviewer registration: {risk_type}->{reviewer}')
            continue
        conclusion = conclusions.get(reviewer)
        if not isinstance(conclusion, dict) or conclusion.get('conclusion') != 'PASSED':
            errors.append(f'code_review missing PASSED detected risk conclusion: {risk_type}->{reviewer}')
    current_code_review_tasks = [
        task for task in tasks
        if isinstance(task, dict)
        and task.get('phase') == 'code_review'
        and task.get('iteration_id') == code_review_artifact.get('iteration_id')
    ]
    if current_code_review_tasks:
        declared = set().union(*(set(task.get('risk_triggers') or []) for task in current_code_review_tasks))
        missing = active_risks - declared
        if missing:
            errors.append(f'code_review tasks omit detected risk_triggers: {sorted(missing)}')
