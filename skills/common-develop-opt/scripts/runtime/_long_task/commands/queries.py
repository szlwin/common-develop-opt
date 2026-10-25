"""Commands queries for the internal runtime."""
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

from document_core.common_utils import now, sha256_file
from runtime._long_task.contracts import *
from runtime._long_task.record_io import *
from runtime._long_task.review_validation import *

from runtime._long_task.architecture_gate import expected_required_reviewers
from runtime._long_task.evidence_gate import authorize_task_command
from runtime._long_task.state_access import append_task_events_payloads, load_task_history, read_static_task_plan, read_task_state_cache, runtime_issues, runtime_state, task_state_path
from runtime._long_task.validation.task import validate_task
def build_task_context(task_dir: Path, task_id: str = '') -> dict[str, Any]:
    """Build one V3 context entirely from reducer output plus immutable plan facts."""
    state = runtime_state(task_dir)
    history = load_task_history(task_dir)
    _, static_tasks = read_static_task_plan(task_dir)
    tasks = task_events.materialize_tasks(static_tasks, history)
    attempts = [item for item in history.get('attempts', []) if isinstance(item, dict)]
    issues = runtime_issues(task_dir)
    selected = task_id or (str(state.get('active_task_ids', [''])[0]) if state.get('active_task_ids') else '')
    task = find_plan_task(tasks, selected) if selected else None
    task_attempts = filter_attempts(attempts, selected, '') if selected else []
    running = [item for item in task_attempts if item.get('status') == 'RUNNING']
    failures = [item for item in task_attempts if item.get('status') in {'FAILED', 'BLOCKED'}]
    task_payload = None
    if isinstance(task, dict):
        task_payload = {
            'id': task.get('id'), 'title': task.get('title'), 'phase': task.get('phase'),
            'status': task.get('status'), 'ownerAgent': task.get('owner_agent'),
            'attempts': task.get('attempts'), 'maxAttempts': task.get('max_attempts'),
            'outputRevision': task.get('output_revision'),
            'validationEvidenceIds': list(task.get('validation_evidence_ids') or []),
            'inputRevisions': task.get('input_revisions'), 'acceptanceTraceIds': task.get('acceptance_trace_ids'),
            'flowRefs': task.get('flow_refs'), 'flowStepRefs': task.get('flow_step_refs'),
            'validationCommands': task.get('validation_commands'), 'stopConditions': task.get('stop_conditions'),
        }
    return {
        'task': task_payload,
        'currentAttempt': running[-1] if running else None,
        'latestAttempt': task_attempts[-1] if task_attempts else None,
        'lastFailure': failures[-1] if failures else None,
        'openIssueIds': [item.get('id') for item in issues if isinstance(item, dict) and item.get('status') in OPEN_ISSUE_STATUSES],
        'nextAction': state.get('next_action'),
        'nextAgent': state.get('next_agent'),
        'currentPhase': state.get('current_phase'),
        'taskStatus': state.get('task_status'),
        'eventProjection': state.get('event_projection') or {},
    }


def command_task_context(args: argparse.Namespace) -> int:
    task_dir = Path(args.task_dir)
    state = runtime_state(task_dir)
    authorize_task_command(args, 'task-context', state, task_dir)
    emit_json(build_task_context(task_dir, args.task_id))
    return 0


def command_latest_attempt(args: argparse.Namespace) -> int:
    _, attempts = load_attempts_for_command(Path(args.task_dir), args, 'latest-attempt')
    result = filter_attempts(attempts, args.task_id or '', args.status or '')
    emit_json({'attempt': result[-1] if result else None})
    return 0


def load_attempts_for_command(task_dir: Path, args: argparse.Namespace, command: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    state = runtime_state(task_dir)
    if not state:
        raise ValueError('task runtime reducer produced no state')
    authorize_task_command(args, command, state, task_dir)
    history = load_task_history(task_dir)
    attempts = [item for item in history.get('attempts', []) if isinstance(item, dict)]
    return state, attempts


def filter_attempts(attempts: list[dict[str, Any]], task_id: str = '', status: str = '') -> list[dict[str, Any]]:
    result = [item for item in attempts if isinstance(item, dict)]
    if task_id:
        result = [item for item in result if item.get('task_id') == task_id]
    if status:
        result = [item for item in result if item.get('status') == status]
    return result


def command_read_attempt(args: argparse.Namespace) -> int:
    _, attempts = load_attempts_for_command(Path(args.task_dir), args, 'read-attempt')
    emit_json(find_attempt(attempts, args.attempt_id))
    return 0


def current_phase_tasks(
    tasks: list[dict[str, Any]],
    phase: str,
    iteration_id: str,
) -> list[dict[str, Any]]:
    return [
        task for task in tasks
        if isinstance(task, dict)
        and task.get('phase') == phase
        and task.get('iteration_id') == iteration_id
    ]


def _handoff_iteration(value: Any) -> int:
    numbers = re.findall(r'(?:I|R)(\d+)', str(value or '').upper())
    return max((int(item) for item in numbers), default=0)


def latest_handoff(task_dir: Path, state: Optional[dict[str, Any]] = None) -> Optional[Path]:
    handoff_dir = task_dir / 'handoff'
    handoffs = sorted(handoff_dir.glob('*.md')) if handoff_dir.is_dir() else []
    if not handoffs:
        return None
    if state is None:
        try:
            state = runtime_state(task_dir)
        except (OSError, ValueError):
            state = {}
    ranked: list[tuple[tuple[Any, ...], Path]] = []
    for path in handoffs:
        try:
            _, handoff = read_block(path, 'handoff')
        except (OSError, ValueError):
            handoff = {}
        if not isinstance(handoff, dict):
            handoff = {}
        try:
            created = datetime.fromisoformat(str(handoff.get('created_at') or '').replace('Z', '+00:00'))
            created_key = created.isoformat()
        except ValueError:
            created_key = ''
        revision_score = _handoff_revision_score(handoff, state)
        phase_order = PHASE_INDEX.get(str(handoff.get('phase') or ''), -1)
        key = (*revision_score, _handoff_iteration(handoff.get('round')), phase_order, created_key, path.name)
        ranked.append((key, path))
    return max(ranked, key=lambda item: item[0])[1]


def command_task_events(args: argparse.Namespace) -> int:
    task_dir = Path(args.task_dir)
    state = runtime_state(task_dir)
    authorize_task_command(args, 'task-events', state, task_dir)
    if not task_events.event_path(task_dir).is_file():
        raise ValueError('task_events.jsonl is required')
    include_archive = bool(getattr(args, 'include_archive', False))
    events = task_events.read_event_history(task_dir) if include_archive else task_events.read_events(task_dir)
    if args.event_type:
        events = [item for item in events if item.get('type') == args.event_type]
    if args.entity_id:
        events = [item for item in events if item.get('entity_id') == args.entity_id]
    if args.limit:
        events = events[-args.limit:]
    emit_json({'count': len(events), 'includeArchive': include_archive, 'events': events})
    return 0


def command_attempts(args: argparse.Namespace) -> int:
    _, attempts = load_attempts_for_command(Path(args.task_dir), args, 'attempts')
    result = filter_attempts(attempts, args.task_id or '', args.status or '')
    if args.limit:
        result = result[-args.limit:]
    emit_json({'count': len(result), 'attempts': result})
    return 0


def load_task_runtime_documents(task_dir: Path) -> tuple[tuple[str, dict[str, Any]], tuple[str, list[dict[str, Any]]], tuple[str, list[dict[str, Any]]]]:
    state_document, projected_state = read_task_state_cache(task_dir, authoritative=True)
    plan_document, static_tasks = read_static_task_plan(task_dir)
    history = load_task_history(task_dir)
    state = task_events.project_state(projected_state, history, synced_at=str((projected_state.get('event_projection') or {}).get('synced_at') or ''))
    tasks = task_events.materialize_tasks(static_tasks, history)
    attempts = [item for item in history.get('attempts', []) if isinstance(item, dict)]
    return (state_document, state), (plan_document, tasks), ('', attempts)


def active_phase_reviewers(
    task_dir: Path,
    state: dict[str, Any],
    phase: str,
    review: dict[str, Any],
) -> list[str]:
    reviewers = sorted(expected_required_reviewers(
        phase, state, minimal=bool((state.get('work_mode') or {}).get('minimal'))
    ))
    trace_document = storage.read_json(storage.path(task_dir, 'traceability'), default={})
    traces = trace_document.get('items', []) if isinstance(trace_document, dict) else []
    trace_items = traces if isinstance(traces, list) else []
    active_flags = {
        flag for flag in ('impact_required', 'cross_module_implementation_required')
        if any(
            isinstance(item, dict) and item.get(flag) is True
            for item in trace_items
        )
    }
    conditional = conditional_reviewers_for(int(state.get('schema_version', LONG_TASK_SCHEMA_VERSION))).get(phase, {})
    if isinstance(conditional, dict):
        reviewers.extend(
            reviewer for reviewer, flag in conditional.items()
            if isinstance(reviewer, str) and flag in active_flags
        )
    additional = review.get('additional_reviewers')
    if isinstance(additional, dict):
        reviewers.extend(sorted(
            reviewer for reviewer in additional
            if isinstance(reviewer, str)
        ))
    return list(dict.fromkeys(reviewers))


def command_resume(args: argparse.Namespace) -> int:
    task_dir = Path(args.task_dir)
    errors, warnings, state, issues = validate_task(task_dir)
    try:
        current_agent = authorize_task_command(args, 'resume', state, task_dir)
    except agent_context.AgentAuthorizationError as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    if errors and (not args.allow_invalid):
        for error in errors:
            print(f'ERROR: {error}', file=sys.stderr)
        return 1
    schema_version = int(state.get('schema_version', 0)) if state else LONG_TASK_SCHEMA_VERSION
    tasks: list[dict[str, Any]] = []
    plan_value = storage.read_json(storage.path(task_dir, 'plan'), default={})
    tasks = plan_value.get('tasks', []) if isinstance(plan_value, dict) else []
    history = load_task_history(task_dir)
    tasks = task_events.materialize_tasks(tasks, history)
    attempts = [item for item in history.get('attempts', []) if isinstance(item, dict)]
    outcomes = [item for item in history.get('outcomes', []) if isinstance(item, dict)]
    latest = latest_handoff(task_dir, state)
    open_issues = [issue for issue in issues if issue.get('status') in OPEN_ISSUE_STATUSES]
    ready_tasks = [task for task in tasks if isinstance(task, dict) and task.get('status') == 'READY']
    active_tasks = [task for task in tasks if isinstance(task, dict) and task.get('status') == 'RUNNING']
    latest_outcome = outcomes[-1] if outcomes else None
    lines = [f"# {state.get('target_id', task_dir.name)} 恢复上下文", '', f"- 版本：{state.get('version', '')}", f'- Schema：{schema_version}', f"- 当前阶段：{state.get('current_phase', '')}", f"- 当前轮次：{state.get('current_round', '')}", f"- 任务状态：{state.get('task_status', '')}", f"- 执行模式：{state.get('execution_mode', 'SEQUENTIAL')}", f'- 当前执行 Agent：{current_agent}', f"- 项目管理 Agent：{state.get('project_manager_agent', '')}", f"- 下一 Agent：{state.get('next_agent', '')}", f"- 下一动作：{state.get('next_action', '')}", f"- 最近门禁：{state.get('last_gate', '') or '无'}", f"- 最近显式 handoff：{(latest if latest else '无')}", '', '## 运行中的任务', '']
    lines.extend([f"- {task.get('id')}: {task.get('title')}（{task.get('owner_agent')}）" for task in active_tasks] or ['- 无'])
    current_attempt_id = str(state.get('current_attempt_id') or '')
    current_attempt = next((item for item in attempts if isinstance(item, dict) and item.get('attempt_id') == current_attempt_id), None)
    latest_attempt = attempts[-1] if attempts else None
    lines.extend(['', '## 当前与最近执行', ''])
    if isinstance(current_attempt, dict):
        lines.append(f"- 当前：{current_attempt.get('attempt_id')} [{current_attempt.get('status')}] task={current_attempt.get('task_id')}")
    else:
        lines.append('- 当前：无')
    if isinstance(latest_attempt, dict):
        lines.append(f"- 最近：{latest_attempt.get('attempt_id')} [{latest_attempt.get('status')}] {latest_attempt.get('summary', '')}")
    else:
        lines.append('- 最近：无')
    lines.extend(['', '## 可执行任务', ''])
    lines.extend([f"- {task.get('id')}: {task.get('title')}" for task in ready_tasks] or ['- 无'])
    lines.extend(['', '## 开放问题', ''])
    lines.extend([f"- {issue.get('id')} [{issue.get('severity')}/{issue.get('status')}] {issue.get('description')}；责任人：{issue.get('owner_agent')}" for issue in open_issues] or ['- 无'])
    lines.extend(['', '## 最近阶段结果', ''])
    if isinstance(latest_outcome, dict):
        lines.append(f"- {latest_outcome.get('id')} [{latest_outcome.get('status')}] {latest_outcome.get('phase')} → {latest_outcome.get('output_revision', '')}")
    else:
        lines.append('- 无')
    stale = [phase for phase, item in (state.get('artifact_revisions') or {}).items() if isinstance(item, dict) and item.get('status') == 'STALE']
    lines.extend(['', '## 失效产物', ''])
    lines.extend([f'- {phase}' for phase in stale] or ['- 无'])
    lines.extend(['', '## 恢复检查', '', '1. 优先调用 task-context 核对当前任务、最新 attempt、最近失败和开放问题。', '2. 核对输入 revision、实际 Git diff 和工作区；只有存在显式 handoff 时才按需读取。', '3. 核对验证命令是否对应当前 revision，旧证据不得沿用。', '4. 检查任务依赖、共享文件和可变状态是否符合声明顺序。', '5. 状态一致后从 next_action 继续，不重复已完成步骤。'])
    if warnings:
        lines.extend(['', '## 校验警告', ''] + [f'- {warning}' for warning in warnings])
    if errors:
        lines.extend(['', '## 校验错误（allow-invalid）', ''] + [f'- {error}' for error in errors])
    output = task_dir / 'cache' / 'resume_context.md'
    output.parent.mkdir(parents=True, exist_ok=True)
    cache_ignore = output.parent / '.gitignore'
    if not cache_ignore.exists():
        cache_ignore.write_text('*\n!.gitignore\n', encoding='utf-8')
    output.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(output)
    return 0


def emit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _handoff_revision_score(handoff: dict[str, Any], state: dict[str, Any]) -> tuple[int, int, int]:
    """Rank handoffs by agreement with canonical state, never filesystem mtime."""
    artifacts = state.get('artifact_revisions') if isinstance(state.get('artifact_revisions'), dict) else {}
    current_revisions = {
        str(phase): str(item.get('revision') or '')
        for phase, item in artifacts.items() if isinstance(item, dict) and item.get('revision')
    }
    phase = str(handoff.get('phase') or '')
    output_revision = str(handoff.get('output_revision') or '')
    exact_output = int(bool(output_revision) and output_revision == current_revisions.get(phase, ''))
    inputs = handoff.get('input_revisions') if isinstance(handoff.get('input_revisions'), dict) else {}
    known_inputs = [(str(key), str(value or '')) for key, value in inputs.items() if str(key) in current_revisions]
    matching_inputs = sum(1 for key, value in known_inputs if value and value == current_revisions.get(key))
    stale_inputs = sum(1 for key, value in known_inputs if value and value != current_revisions.get(key))
    return exact_output, matching_inputs, -stale_inputs


def find_plan_task(tasks: list[dict[str, Any]], task_id: str) -> dict[str, Any]:
    matches = [item for item in tasks if isinstance(item, dict) and item.get('id') == task_id]
    if len(matches) != 1:
        raise ValueError(f'task plan must contain exactly one task: {task_id}')
    return matches[0]


def find_attempt(attempts: list[dict[str, Any]], attempt_id: str) -> dict[str, Any]:
    matches = [item for item in attempts if isinstance(item, dict) and item.get('attempt_id') == attempt_id]
    if len(matches) != 1:
        raise ValueError(f'task attempts must contain exactly one attempt: {attempt_id}')
    return matches[0]


def command_issue_event(args: argparse.Namespace) -> int:
    """Append one validated issue lifecycle transition to the V3 event ledger."""
    task_dir = Path(args.task_dir)
    state = runtime_state(task_dir)
    agent = authorize_task_command(args, 'issue-event', state, task_dir)
    issues = {str(item.get('id') or ''): copy.deepcopy(item) for item in runtime_issues(task_dir) if isinstance(item, dict)}
    issue_id = str(args.issue_id or '')
    issue = issues.get(issue_id)
    if not isinstance(issue, dict):
        raise ValueError(f'unknown issue: {issue_id}')
    action = str(args.event_type or '').upper().replace('-', '_')
    event_type = f'ISSUE_{action}'
    if event_type not in task_events.ISSUE_EVENT_TYPES - {'ISSUE_OPENED'}:
        raise ValueError(f'unsupported issue lifecycle event: {event_type}')
    timestamp = now()
    pm = str(state.get('project_manager_agent') or PROJECT_MANAGER_AGENT)
    owner = str(issue.get('owner_agent') or '')
    raised_by = str(issue.get('raised_by_agent') or '')
    verified_by = str(issue.get('verified_by_agent') or '')
    evidence_ids = list(dict.fromkeys(str(value) for value in (args.evidence_id or []) if str(value)))
    reason = str(args.reason or '').strip()

    if event_type == 'ISSUE_ASSIGNED':
        if agent != pm:
            raise agent_context.AgentAuthorizationError('only ProjectManagerAgent may assign an issue owner')
        new_owner = str(args.owner_agent or owner).strip()
        if not new_owner or new_owner not in KNOWN_AGENTS:
            raise ValueError('ISSUE_ASSIGNED requires a known --owner-agent')
        issue['owner_agent'] = new_owner
        issue['status'] = 'ASSIGNED'
    elif event_type == 'ISSUE_REMEDIATED':
        if not owner or agent != owner:
            raise agent_context.AgentAuthorizationError('only issue owner may record remediation')
        revision = str(args.resolution_revision or '').strip()
        if not revision:
            raise ValueError('ISSUE_REMEDIATED requires --resolution-revision')
        issue['status'] = 'FIXED_PENDING_VERIFY'
        issue['resolution_revision'] = revision
        issue['resolution_evidence'] = evidence_ids
        if reason:
            issue['decision'] = reason
    elif event_type == 'ISSUE_VERIFIED':
        if agent == owner:
            raise agent_context.AgentAuthorizationError('issue owner cannot self-verify remediation')
        if agent not in {raised_by, pm} and agent != verified_by:
            raise agent_context.AgentAuthorizationError('only issue raiser, designated verifier, or ProjectManagerAgent may verify')
        if str(issue.get('status') or '') != 'FIXED_PENDING_VERIFY':
            raise ValueError('ISSUE_VERIFIED requires FIXED_PENDING_VERIFY status')
        issue['verified_by_agent'] = agent
        issue['verified_at'] = timestamp
        if evidence_ids:
            issue['resolution_evidence'] = list(dict.fromkeys([*(issue.get('resolution_evidence') or []), *evidence_ids]))
        if reason:
            issue['decision'] = reason
    elif event_type == 'ISSUE_CLOSED':
        verifier = str(issue.get('verified_by_agent') or '')
        if not verifier:
            raise ValueError('ISSUE_CLOSED requires prior independent verification')
        if agent not in {verifier, pm}:
            raise agent_context.AgentAuthorizationError('only verifier or ProjectManagerAgent may close a verified issue')
        issue['status'] = 'CLOSED'
        if reason:
            issue['decision'] = reason
    elif event_type == 'ISSUE_DEFERRED':
        if agent != pm:
            raise agent_context.AgentAuthorizationError('only ProjectManagerAgent may defer an issue')
        if not reason:
            raise ValueError('ISSUE_DEFERRED requires --reason')
        issue['status'] = 'DEFERRED'
        issue['defer_reason'] = reason
    elif event_type == 'ISSUE_REOPENED':
        if agent not in {raised_by, pm, verified_by}:
            raise agent_context.AgentAuthorizationError('only issue raiser, verifier, or ProjectManagerAgent may reopen')
        issue['status'] = 'OPEN'
        for key in ('resolution_revision', 'resolution_evidence', 'verified_by_agent', 'verified_at', 'defer_reason'):
            if key in issue:
                issue[key] = [] if key == 'resolution_evidence' else ''
        if reason:
            issue['decision'] = reason
    elif event_type == 'ISSUE_REJECTED':
        if agent not in {raised_by, pm}:
            raise agent_context.AgentAuthorizationError('only issue raiser or ProjectManagerAgent may reject an issue')
        if not reason:
            raise ValueError('ISSUE_REJECTED requires --reason')
        issue['status'] = 'REJECTED'
        issue['decision'] = reason
        if evidence_ids:
            issue['resolution_evidence'] = evidence_ids

    state_document, current = read_task_state_cache(task_dir, authoritative=True)
    payloads, projected, reduced = append_task_events_payloads(
        task_dir, task_state_path(task_dir), state_document, current,
        [task_events.issue_event_spec(event_type, issue, timestamp=timestamp, source='long_task.py issue-event')],
        synced_at=timestamp,
    )
    transactional_text_write(payloads)
    emit_json({
        'status': 'RECORDED', 'eventType': event_type, 'issue': issue,
        'openIssueIds': list(projected.get('open_issue_ids') or []),
        'sourceEventSeq': int(reduced.get('last_seq') or 0),
    })
    return 0
