"""Commands initialization for the internal runtime."""
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
from document_core import storage
from runtime._long_task import task_events
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from document_core.common_utils import atomic_write_text, now, sha256_file
from runtime._long_task.contracts import *
from runtime._long_task.record_io import *
from runtime._long_task.review_validation import *

from runtime._long_task.architecture_gate import new_architecture_review_state, sync_mode_review_requirements
from runtime._long_task.phase_policy import actual_work_mode_state
def command_init(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, 'long_task', 'init')
    task_dir = Path(args.doc_root) / 'version' / args.version / 'task' / args.target
    if task_dir.exists() and any(task_dir.iterdir()) and (not args.reuse):
        print(f'ERROR: {task_dir} already exists and is not empty; use --reuse', file=sys.stderr)
        return 1
    task_dir.mkdir(parents=True, exist_ok=True)
    storage.ensure_project_marker(Path(args.doc_root))
    work_path = work_mode.ensure_work_file(Path(args.doc_root), args.version)
    created_at = now()
    replacements = {
        'TARGET_ID': args.target,
        'VERSION': args.version,
        'CREATED_AT': created_at,
        'CURRENT_AGENT': agent,
        'WORK_MODE_DIGEST': sha256_file(work_path),
    }
    # New projects have one machine-readable storage model.
    storage.ensure_directories(task_dir)
    canonical = storage.task_paths(task_dir)
    if not canonical['traceability'].exists() or not args.reuse:
        storage.write_json(canonical['traceability'], {'schema_version': 1, 'items': []})
    identity_path = task_dir / 'task.json'
    if not identity_path.exists() or not args.reuse:
        atomic_write_text(identity_path, json.dumps({
            'schema_version': 3,
            'target_id': args.target,
            'version': args.version,
            'project_manager_agent': PROJECT_MANAGER_AGENT,
            'execution_mode': 'SEQUENTIAL',
            'created_at': created_at,
        }, ensure_ascii=False, separators=(',', ':')) + '\n')
    actual_mode = actual_work_mode_state(task_dir)
    state = build_initial_task_state(args.target, args.version, agent, actual_mode, created_at)
    sync_mode_review_requirements(state, minimal=bool(actual_mode.get('minimal')))
    evidence.ensure_registry(task_dir, args.target, args.version)
    acceptance.ensure_document(task_dir, args.target, args.version)
    risk_detect.ensure_report(task_dir)
    ensure_work_record(task_dir.parent.parent, args.version)

    event_file = task_events.event_path(task_dir)
    if args.reuse and not event_file.exists():
        raise ValueError('task_events.jsonl is required when reusing an existing task')
    if not args.reuse:
        empty = task_events.reduce_events([])
        seed = task_events.state_patch_spec(empty, state, timestamp=created_at, source='long_task.py init')
        ledger_text, _created, events = task_events.append_many_text(task_dir, [seed] if seed else [])
        event_file.write_text(ledger_text, encoding='utf-8')
        reduced = task_events.reduce_events(events)
        projected = task_events.project_state(state, reduced, synced_at=created_at)

    intake_arg = str(getattr(args, 'request_intake', '') or '').strip()
    if intake_arg:
        intake_document = request_intake.load(Path(intake_arg))
        request_intake.bind_to_task(task_dir, intake_document, agent=agent)
    from runtime import current_manifest
    current_manifest.sync_manifest(task_dir)
    storage.assert_new_project(task_dir)
    print(task_dir)
    return 0


def build_initial_task_state(
    target_id: str,
    version: str,
    current_agent: str,
    actual_mode: dict[str, Any],
    created_at: str,
) -> dict[str, Any]:
    """Build the initial task state from machine contracts."""
    artifacts: dict[str, dict[str, Any]] = {}
    reviews: dict[str, dict[str, Any]] = {}
    for phase in PHASES:
        token = phase.upper().replace('_', '-')
        iteration_id = f'ITER-{target_id}-{token}-001'
        artifacts[phase] = {
            'revision': '',
            'status': 'PENDING',
            'iteration_id': iteration_id,
            'iteration_no': 1,
        }
        if phase in COLLABORATION_REVIEWERS:
            reviews[phase] = {
                'artifact_revision': '',
                'status': 'PENDING',
                'current_iteration_id': iteration_id,
            }

    mode = {
        'ref': str(actual_mode.get('ref') or ''),
        'digest': str(actual_mode.get('digest') or ''),
        'model_code': bool(actual_mode.get('model_code')),
        'page_design': bool(actual_mode.get('page_design')),
        'minimal': bool(actual_mode.get('minimal')),
        'auto': bool(actual_mode.get('auto')),
        'architecture_review': bool(actual_mode.get('architecture_review')),
        'git_checkpoint': bool(actual_mode.get('git_checkpoint')),
        'git_push': bool(actual_mode.get('git_push')),
        'review_only': bool(actual_mode.get('review_only')),
        'lightweight': bool(actual_mode.get('lightweight')),
        'lightweight_waived': False,
        'skipped_phases': [],
        'minimal_skipped_phases': [],
        'synced_at': created_at,
    }
    development_iteration = str(artifacts.get('development', {}).get('iteration_id') or '')
    first_phase = PHASES[0]
    first_owner = str((PHASE_DEFINITIONS.get(first_phase) or {}).get('ownerAgent') or '')
    if not first_owner:
        raise RuntimeError(f'runtime contract phase has no ownerAgent: {first_phase}')
    return {
        'schema_version': LONG_TASK_SCHEMA_VERSION,
        'target_id': target_id,
        'version': version,
        'task_status': 'READY',
        'current_phase': first_phase,
        'current_round': 'REQCONF-R01',
        'current_agent': current_agent,
        'project_manager_agent': PROJECT_MANAGER_AGENT,
        'execution_mode': 'SEQUENTIAL',
        'active_task_ids': [],
        'current_attempt_id': '',
        'work_mode': mode,
        'work_policy': {
            'mode': 'lightweight' if mode.get('lightweight') else ('minimal' if mode.get('minimal') else 'standard'),
            'tdd': 'not_requested' if mode.get('lightweight') else 'requested',
            'test_design': 'not_requested' if mode.get('lightweight') else 'requested',
            'review': 'not_requested' if mode.get('lightweight') else 'requested',
            'testing': 'not_requested' if mode.get('lightweight') else 'requested',
            'explicit_reopen_required': False,
        },
        'request_intake': {
            'ref': '', 'digest': '', 'classification': '', 'workflow_profile': '',
            'entry_phase': '', 'classification_revision': 0, 'synced_at': '',
            'governance_profile': '', 'governance_recommendation_digest': '',
            'governance_override_active': False,
        },
        'architecture_review': new_architecture_review_state(
            bool(mode.get('architecture_review')), development_iteration, created_at
        ),
        'artifact_revisions': artifacts,
        'collaboration_reviews': reviews,
        'open_issue_ids': [],
        'last_gate': '',
        'next_action': '由 ProjectManagerAgent 分派需求确认',
        'next_agent': first_owner,
        'checkpoint_at': created_at,
        'event_projection': {
            'source_event_seq': 0, 'source_digest': '', 'synced_at': created_at,
            'storage_mode': 'EVENT_LEDGER_V3',
        },
    }
