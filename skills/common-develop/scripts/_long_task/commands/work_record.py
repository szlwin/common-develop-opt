"""Commands work record for the internal runtime."""
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

from _long_task.commands.queries import emit_json
from _long_task.state_access import load_task_history, runtime_state, runtime_tasks
def command_append_work_event(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, 'long_task', 'append-work-event', declared=True)
    version_root = Path(args.doc_root) / 'version' / args.version
    path = ensure_work_record(version_root, args.version)
    _, events = read_work_events(path)
    timestamp = now()
    event_id = args.event_id or ''
    event = {
        'event_id': event_id,
        'timestamp': timestamp,
        'event_type': args.event_type,
        'status': args.status,
        'agent': agent,
        'source': args.source or 'long_task.py append-work-event',
        'version': args.version,
        'target_id': args.target_id or '',
        'scope': args.scope or args.target_id or '未登记',
        'phase': args.phase or '',
        'task_type': args.task_type or args.event_type,
        'execution_mode': args.execution_mode or 'standard / sequential',
        'task_id': args.task_id or '',
        'attempt_id': args.attempt_id or '',
        'iteration_id': args.iteration_id or '',
        'iteration_no': args.iteration_no or 0,
        'input_revision': args.input_revision or '',
        'output_revision': args.output_revision or '',
        'stage_outcome_refs': list(dict.fromkeys(args.stage_outcome_ref or [])),
        'evidence_ids': list(dict.fromkeys(args.evidence_ref or [])),
        'review_result_refs': list(dict.fromkeys(args.review_result_ref or [])),
        'issue_ids': list(dict.fromkeys(args.issue_ref or [])),
        'git_checkpoint_refs': list(dict.fromkeys(args.git_checkpoint_ref or [])),
        'summary': args.summary,
        'state_change': args.state_change or '未登记',
        'sql_change_summary': args.sql_change_summary or '无 / 未登记',
        'validation_summary': args.validation_summary or '',
        'blockers': list(dict.fromkeys(args.blocker or [])),
        'modified_files_summary': list(dict.fromkeys(args.modified_file_summary or [])),
        'next_action': args.next_action or '无 / 未登记',
        'next_agent': args.next_agent or '未登记',
        'correction_of': args.correction_of or '',
    }
    if event['status'] not in WORK_EVENT_STATUSES:
        raise ValueError(f'invalid work event status: {event["status"]}')
    updated, result_events = append_work_event(path, event, version=args.version)
    transactional_text_write({path: updated})
    emitted = result_events[-1]
    emit_json(emitted)
    return 0


def enrich_work_event(doc_root: Path, version: str, event: dict[str, Any]) -> dict[str, Any]:
    enriched = copy.deepcopy(event)
    enriched['version'] = version

    def missing(key: str) -> bool:
        value = enriched.get(key)
        return value in (None, '', '未登记', '无 / 未登记') or value == []

    def fill(key: str, value: Any) -> None:
        if missing(key) and value not in (None, '', []):
            enriched[key] = copy.deepcopy(value)

    target_id = str(enriched.get('target_id') or '').strip()
    if not target_id:
        return enriched
    task_dir = doc_root / 'version' / version / 'task' / target_id
    if not task_dir.is_dir():
        return enriched
    state: dict[str, Any] = {}
    tasks: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    try:
        state = runtime_state(task_dir)
        history = load_task_history(task_dir)
        tasks = runtime_tasks(task_dir)
        attempts = [item for item in history.get('attempts', []) if isinstance(item, dict)]
        outcomes = [item for item in history.get('outcomes', []) if isinstance(item, dict)]
    except (OSError, ValueError):
        return enriched
    task = next((item for item in tasks if item.get('id') == enriched.get('task_id')), None)
    attempt = next((item for item in attempts if item.get('attempt_id') == enriched.get('attempt_id')), None)
    if isinstance(task, dict):
        fill('phase', task.get('phase') or '')
        if enriched.get('scope') in (None, '', '未登记', target_id):
            enriched['scope'] = task.get('title') or task.get('objective') or target_id
        fill('task_type', task.get('phase') or enriched.get('event_type') or '')
    if isinstance(attempt, dict):
        fill('phase', attempt.get('phase') or '')
        fill('input_revision', attempt.get('input_revision') or '')
        fill('output_revision', attempt.get('output_revision') or '')
        fill('summary', attempt.get('summary') or '')
        fill('next_action', attempt.get('next_action') or '')
        # The current attempt record is the canonical detailed source; align
        # the work summary with its active Evidence.
        enriched['evidence_ids'] = list(dict.fromkeys(attempt.get('evidence_ids') or []))
        enriched['modified_files_summary'] = list(dict.fromkeys(attempt.get('modified_files') or []))
        blockers = [value for value in (attempt.get('failure_type'), attempt.get('failure_reason')) if value]
        enriched['blockers'] = blockers
        command_count = len(attempt.get('command_evidence_ids') or [])
        evidence_count = len(enriched['evidence_ids'])
        enriched['validation_summary'] = f'登记 Evidence {evidence_count} 项；命令 Evidence {command_count} 项'
    phase = str(enriched.get('phase') or '')
    output_revision = str(enriched.get('output_revision') or '')
    matching_outcomes = [
        str(item.get('id')) for item in outcomes
        if item.get('phase') == phase and (not output_revision or item.get('output_revision') == output_revision)
    ]
    if matching_outcomes:
        enriched['stage_outcome_refs'] = list(dict.fromkeys(matching_outcomes))
    review_refs: list[str] = []
    review = (state.get('collaboration_reviews') or {}).get(phase) if isinstance(state.get('collaboration_reviews'), dict) else None
    if isinstance(review, dict):
        conclusions = review.get('independent_conclusions') or {}
        values = conclusions.values() if isinstance(conclusions, dict) else conclusions
        for conclusion in values:
            if not isinstance(conclusion, dict):
                continue
            if output_revision and conclusion.get('revision') != output_revision:
                continue
            ref = str(conclusion.get('review_result_ref') or '').strip()
            if ref:
                review_refs.append(ref)
    if review_refs:
        enriched['review_result_refs'] = list(dict.fromkeys(review_refs))
    checkpoint_refs: list[str] = []
    try:
        import git_checkpoint
        _, _, checkpoints = git_checkpoint.validate_document(task_dir, require_resolved=False)
        for item in checkpoints:
            if not isinstance(item, dict):
                continue
            if phase and item.get('phase') != phase:
                continue
            if output_revision and item.get('stage_revision') != output_revision:
                continue
            checkpoint_id = str(item.get('checkpoint_id') or '').strip()
            if checkpoint_id:
                checkpoint_refs.append(checkpoint_id)
    except (ImportError, OSError, ValueError):
        pass
    if checkpoint_refs:
        enriched['git_checkpoint_refs'] = list(dict.fromkeys(checkpoint_refs))
    work_mode_state = state.get('work_mode') if isinstance(state.get('work_mode'), dict) else {}
    enabled = [key for key in ('model_code', 'page_design', 'minimal', 'auto', 'architecture_review', 'git_checkpoint', 'git_push', 'review_only', 'lightweight') if work_mode_state.get(key) is True]
    fill('execution_mode', ' / '.join(enabled) if enabled else 'standard / sequential')
    fill('source', 'long_task.py finish-attempt')
    fill('state_change', f"{enriched.get('task_id') or target_id}: RUNNING → {enriched.get('status') or 'RECORDED'}")
    fill('sql_change_summary', '无 / 未登记')
    # Historical next_agent cannot always be reconstructed from the final task
    # snapshot; leave it explicitly unregistered instead of fabricating it.
    fill('next_agent', '未登记')
    if missing('issue_ids'):
        enriched['issue_ids'] = []
    return enriched


def command_work_events(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, 'long_task', 'work-events', declared=True)
    path = Path(args.doc_root) / 'version' / args.version / 'work_record.md'
    _, events = read_work_events(path)
    result = [item for item in events if isinstance(item, dict)]
    for key, value in (('target_id', args.target_id), ('task_id', args.task_id), ('attempt_id', args.attempt_id), ('event_type', args.event_type), ('status', args.status)):
        if value:
            result = [item for item in result if item.get(key) == value]
    if args.limit:
        result = result[-args.limit:]
    emit_json({'count': len(result), 'events': result})
    return 0


def command_validate_work_record(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, 'long_task', 'validate-work-record')
    path = Path(args.doc_root) / 'version' / args.version / 'work_record.md'
    if not path.is_file():
        raise ValueError(f'missing work_record.md: {path}')
    errors, events = work_record.validate_document(
        path, allowed_fields=WORK_EVENT_FIELDS, known_agents=KNOWN_AGENTS, statuses=WORK_EVENT_STATUSES
    )
    emit_json({'status': 'PASSED' if not errors else 'FAILED', 'path': str(path), 'eventCount': len(events), 'errors': errors})
    return 0 if not errors else 1


def command_render_work_record(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, 'long_task', 'work-events', declared=True)
    path = Path(args.doc_root) / 'version' / args.version / 'work_record.md'
    _, events = read_work_events(path)
    print(work_record.render_markdown(args.version, events), end='')
    return 0
