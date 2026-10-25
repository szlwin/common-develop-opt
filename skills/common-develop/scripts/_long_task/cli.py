"""Command-line parser construction for the long_task runtime."""
from __future__ import annotations

import argparse
from types import ModuleType

import agent_context


def _add_lifecycle_commands(subparsers, runtime: ModuleType) -> None:
    init_parser = subparsers.add_parser('init', help=f'initialize a long-task schema v{runtime.LONG_TASK_SCHEMA_VERSION} task')
    agent_context.add_agent_argument(init_parser)
    init_parser.add_argument('--doc-root', required=True)
    init_parser.add_argument('--version', required=True)
    init_parser.add_argument('--target', required=True)
    init_parser.add_argument('--reuse', action='store_true')
    init_parser.add_argument('--request-intake', default='', help='validated request_intake.json to bind during initialization')
    init_parser.set_defaults(func=runtime.command_init)
    validate_parser = subparsers.add_parser('validate', help='validate a task')
    agent_context.add_agent_argument(validate_parser)
    validate_parser.add_argument('--task-dir', required=True)
    validate_parser.add_argument('--json', action='store_true', help='emit machine-readable validation result')
    validate_parser.set_defaults(func=runtime.command_validate)
    reopen_parser = subparsers.add_parser(
        'reopen-phase',
        help='after read-only upstream impact confirmation, create one phase iteration and invalidate downstream work',
    )
    agent_context.add_agent_argument(reopen_parser)
    reopen_parser.add_argument('--task-dir', required=True)
    reopen_parser.add_argument('--from-phase', required=True)
    reopen_parser.add_argument('--round', default='')
    reopen_parser.add_argument('--source-revision', default='')
    reopen_parser.add_argument('--changed-phase', default='', help='phase where the change was first observed; defaults to current phase')
    reopen_parser.add_argument('--user-confirmed', action='store_true', help='deprecated compatibility flag; authorization is resolved from --confirmation-ref')
    reopen_parser.add_argument('--confirmation-ref', default='', help='stable reference to the user boundary-confirmation record')
    reopen_parser.add_argument('--reason', required=True)
    reopen_parser.set_defaults(func=runtime.command_reopen_phase)
    amend_parser = subparsers.add_parser(
        'amend-phase', help='open a bounded current-phase amendment and preserve unrelated facts'
    )
    agent_context.add_agent_argument(amend_parser)
    amend_parser.add_argument('--task-dir', required=True)
    amend_parser.add_argument('--phase', required=True, choices=runtime.PHASES)
    amend_parser.add_argument('--base-revision', required=True)
    amend_parser.add_argument('--revision', required=True)
    amend_parser.add_argument('--change-class', required=True)
    amend_parser.add_argument('--changed-section', action='append', default=[], required=True)
    amend_parser.add_argument('--affected-phase', action='append', default=[], required=True)
    amend_parser.add_argument('--affected-task-id', action='append', default=[], required=True)
    amend_parser.add_argument('--affected-trace-id', action='append', default=[])
    amend_parser.add_argument('--affected-acceptance-id', action='append', default=[])
    amend_parser.add_argument('--preserve-evidence-id', action='append', default=[])
    amend_parser.add_argument('--invalidate-evidence-id', action='append', default=[])
    amend_parser.add_argument('--canonical-file', required=True, help='canonical Markdown file under the current version doc tree')
    amend_parser.add_argument('--impact-plan', required=True)
    amend_parser.add_argument('--reason', required=True)
    amend_parser.set_defaults(func=runtime.command_amend_phase)
    sync_mode_parser = subparsers.add_parser('sync-work-mode', help='bind task state to version work.md mode and digest')
    agent_context.add_agent_argument(sync_mode_parser)
    sync_mode_parser.add_argument('--task-dir', required=True)
    sync_mode_parser.set_defaults(func=runtime.command_sync_work_mode)
    policy_parser = subparsers.add_parser('set-task-policy', help='set or clear an iteration-scoped runtime max_attempts override')
    agent_context.add_agent_argument(policy_parser)
    policy_parser.add_argument('--task-dir', required=True)
    policy_parser.add_argument('--task-id', required=True)
    policy_parser.add_argument('--max-attempts', required=True, type=int)
    policy_parser.add_argument('--reason', required=True)
    policy_parser.set_defaults(func=runtime.command_set_task_policy)
    skip_parser = subparsers.add_parser('skip-phase', help='record an explicit standard-mode user skip')
    agent_context.add_agent_argument(skip_parser)
    skip_parser.add_argument('--task-dir', required=True)
    skip_parser.add_argument('--phase', required=True, choices=runtime.STANDARD_USER_SKIPPABLE_PHASES)
    skip_parser.add_argument('--reason', required=True)
    skip_parser.add_argument('--confirmation-ref', required=True)
    skip_parser.set_defaults(func=runtime.command_skip_phase)
    sync_risk_parser = subparsers.add_parser(
        'sync-risk-reviewers',
        help='synchronize risk detection into code-review tasks and Reviewer registrations',
    )
    agent_context.add_agent_argument(sync_risk_parser)
    sync_risk_parser.add_argument('--task-dir', required=True)
    sync_risk_parser.set_defaults(func=runtime.command_sync_risk_reviewers)
    list_risk_parser = subparsers.add_parser('list-risk-triggers', help='list supported risk trigger keys, optionally filtered by phase')
    agent_context.add_agent_argument(list_risk_parser)
    list_risk_parser.add_argument('--phase', default='', choices=['', *runtime.PHASES])
    list_risk_parser.add_argument('--json', action='store_true')
    list_risk_parser.set_defaults(func=runtime.command_list_risk_triggers)
    development_step_parser = subparsers.add_parser(
        'advance-development-step',
        help='after wk -ar skeleton Review passes, reopen development for concrete implementation',
    )
    agent_context.add_agent_argument(development_step_parser)
    development_step_parser.add_argument('--task-dir', required=True)
    development_step_parser.set_defaults(func=runtime.command_advance_development_step)
    advance_parser = subparsers.add_parser('advance-phase', help='advance only after the current phase gate passes')
    agent_context.add_agent_argument(advance_parser)
    advance_parser.add_argument('--task-dir', required=True)
    advance_parser.set_defaults(func=runtime.command_advance_phase)
    resume_parser = subparsers.add_parser('resume', help='write compact resume context')
    agent_context.add_agent_argument(resume_parser)
    resume_parser.add_argument('--task-dir', required=True)
    resume_parser.add_argument('--allow-invalid', action='store_true')
    resume_parser.set_defaults(func=runtime.command_resume)


def _add_attempt_commands(subparsers, runtime: ModuleType) -> None:
    start_parser = subparsers.add_parser('start-attempt', help='start one structured task attempt')
    agent_context.add_agent_argument(start_parser)
    start_parser.add_argument('--task-dir', required=True)
    start_parser.add_argument('--task-id', required=True)
    start_parser.add_argument('--input-revision', default='')
    start_parser.add_argument('--summary', default='')
    start_parser.add_argument('--next-action', default='')
    start_parser.set_defaults(func=runtime.command_start_attempt)

    finish_parser = subparsers.add_parser('finish-attempt', help='finish one structured task attempt')
    agent_context.add_agent_argument(finish_parser)
    finish_parser.add_argument('--task-dir', required=True)
    finish_parser.add_argument('--attempt-id', required=True)
    finish_parser.add_argument('--status', required=True, choices=sorted(runtime.ATTEMPT_STATUSES - {'RUNNING'}))
    finish_parser.add_argument('--output-revision', default='')
    finish_parser.add_argument('--failure-type', default='')
    finish_parser.add_argument('--failure-reason', default='')
    finish_parser.add_argument('--modified-file', action='append', default=[])
    finish_parser.add_argument('--command-ref', action='append', default=[])
    finish_parser.add_argument('--evidence-ref', action='append', default=[])
    finish_parser.add_argument(
        '--timeout', type=int, default=runtime.DEFAULT_COMMAND_TIMEOUT_SECONDS,
        help='auto-evidence command timeout in seconds; must be positive',
    )
    finish_parser.add_argument(
        '--auto-evidence', action='store_true',
        help='run one exact task.validation_commands entry and register its real command Evidence',
    )
    finish_parser.add_argument(
        '--check', default='',
        help='validation command to run with --auto-evidence; must exactly match task.validation_commands',
    )
    finish_parser.add_argument('--summary', default='')
    finish_parser.add_argument('--next-action', default='')
    finish_parser.add_argument('--next-agent', default='')
    finish_parser.set_defaults(func=runtime.command_finish_attempt)

    publish_parser = subparsers.add_parser(
        'publish-artifact',
        help='freeze one PASSED attempt revision and open current-phase Review',
    )
    agent_context.add_agent_argument(publish_parser)
    publish_parser.add_argument('--task-dir', required=True)
    publish_parser.add_argument('--attempt-id', required=True)
    publish_parser.add_argument('--evidence-id', action='append', default=[])
    publish_parser.add_argument('--open-risk', action='append', default=[])
    publish_parser.add_argument(
        '--stage-result', default='', metavar='JSON',
        help='optional JSON StageResult; when omitted it is generated from the PASSED attempt',
    )
    publish_parser.set_defaults(func=runtime.command_publish_artifact)

    finalize_parser = subparsers.add_parser(
        'finalize-phase',
        help='pass the current artifact and StageOutcome after Review and phase gates succeed',
    )
    agent_context.add_agent_argument(finalize_parser)
    finalize_parser.add_argument('--task-dir', required=True)
    finalize_parser.set_defaults(func=runtime.command_finalize_phase)

    revalidate_parser = subparsers.add_parser(
        'revalidate-traceability',
        help='revalidate one STALE/GAP trace with current Evidence and an independent Review',
    )
    agent_context.add_agent_argument(revalidate_parser)
    revalidate_parser.add_argument('--task-dir', required=True)
    revalidate_parser.add_argument('--trace-id', required=True)
    revalidate_parser.add_argument('--phase', required=True, choices=runtime.PHASES)
    revalidate_parser.add_argument('--revision', required=True)
    revalidate_parser.add_argument('--evidence-id', action='append', required=True)
    revalidate_parser.add_argument('--review-result-ref', required=True)
    revalidate_parser.add_argument('--reason', required=True)
    revalidate_parser.set_defaults(func=runtime.command_revalidate_traceability)


def _add_event_query_commands(subparsers, runtime: ModuleType) -> None:
    compact_parser = subparsers.add_parser(
        'compact-events',
        help='preview or apply an auditable rollover of an oversized task event ledger',
    )
    agent_context.add_agent_argument(compact_parser)
    compact_parser.add_argument('--task-dir', required=True)
    compact_parser.add_argument(
        '--threshold-bytes', type=int,
        default=runtime.task_events.DEFAULT_COMPACTION_THRESHOLD_BYTES,
    )
    compact_parser.add_argument('--force', action='store_true')
    compact_parser.add_argument('--plan-digest', default='')
    compact_parser.add_argument('--apply', action='store_true')
    compact_parser.add_argument('--json', action='store_true')
    compact_parser.set_defaults(func=runtime.command_compact_events)

    task_events_parser = subparsers.add_parser('task-events', help='read task-local append-only lifecycle events')
    agent_context.add_agent_argument(task_events_parser)
    task_events_parser.add_argument('--task-dir', required=True)
    task_events_parser.add_argument('--event-type', default='')
    task_events_parser.add_argument('--entity-id', default='')
    task_events_parser.add_argument('--limit', type=int, default=0)
    task_events_parser.add_argument('--include-archive', action='store_true')
    task_events_parser.set_defaults(func=runtime.command_task_events)

    issue_parser = subparsers.add_parser('issue-event', help='append one V3 issue lifecycle transition')
    agent_context.add_agent_argument(issue_parser)
    issue_parser.add_argument('--task-dir', required=True)
    issue_parser.add_argument('--issue-id', required=True)
    issue_parser.add_argument('--event-type', required=True, choices=['assigned', 'remediated', 'verified', 'closed', 'deferred', 'reopened', 'rejected'])
    issue_parser.add_argument('--owner-agent', default='')
    issue_parser.add_argument('--resolution-revision', default='')
    issue_parser.add_argument('--evidence-id', action='append', default=[])
    issue_parser.add_argument('--reason', default='')
    issue_parser.set_defaults(func=runtime.command_issue_event)

    attempts_parser = subparsers.add_parser('attempts', help='read filtered attempt history as compact JSON')
    agent_context.add_agent_argument(attempts_parser)
    attempts_parser.add_argument('--task-dir', required=True)
    attempts_parser.add_argument('--task-id', default='')
    attempts_parser.add_argument('--status', default='', choices=['', *sorted(runtime.ATTEMPT_STATUSES)])
    attempts_parser.add_argument('--limit', type=int, default=0)
    attempts_parser.set_defaults(func=runtime.command_attempts)

    read_attempt_parser = subparsers.add_parser('read-attempt', help='read one attempt as JSON')
    agent_context.add_agent_argument(read_attempt_parser)
    read_attempt_parser.add_argument('--task-dir', required=True)
    read_attempt_parser.add_argument('--attempt-id', required=True)
    read_attempt_parser.set_defaults(func=runtime.command_read_attempt)

    latest_attempt_parser = subparsers.add_parser('latest-attempt', help='read latest matching attempt as JSON')
    agent_context.add_agent_argument(latest_attempt_parser)
    latest_attempt_parser.add_argument('--task-dir', required=True)
    latest_attempt_parser.add_argument('--task-id', default='')
    latest_attempt_parser.add_argument('--status', default='', choices=['', *sorted(runtime.ATTEMPT_STATUSES)])
    latest_attempt_parser.set_defaults(func=runtime.command_latest_attempt)

    context_parser = subparsers.add_parser('task-context', help='read minimum execution context for one task')
    agent_context.add_agent_argument(context_parser)
    context_parser.add_argument('--task-dir', required=True)
    context_parser.add_argument('--task-id', required=True)
    context_parser.set_defaults(func=runtime.command_task_context)


def _add_record_commands(subparsers, runtime: ModuleType) -> None:
    append_event_parser = subparsers.add_parser('append-work-event', help='append one version-level summary event')
    agent_context.add_agent_argument(append_event_parser)
    append_event_parser.add_argument('--doc-root', required=True)
    append_event_parser.add_argument('--version', required=True)
    append_event_parser.add_argument('--event-id', default='')
    append_event_parser.add_argument('--event-type', required=True)
    append_event_parser.add_argument('--status', required=True, choices=sorted(runtime.WORK_EVENT_STATUSES))
    append_event_parser.add_argument('--source', default='')
    append_event_parser.add_argument('--target-id', default='')
    append_event_parser.add_argument('--scope', default='')
    append_event_parser.add_argument('--phase', default='', choices=['', *runtime.PHASES])
    append_event_parser.add_argument('--task-type', default='')
    append_event_parser.add_argument('--execution-mode', default='')
    append_event_parser.add_argument('--task-id', default='')
    append_event_parser.add_argument('--attempt-id', default='')
    append_event_parser.add_argument('--iteration-id', default='')
    append_event_parser.add_argument('--iteration-no', type=int, default=0)
    append_event_parser.add_argument('--input-revision', default='')
    append_event_parser.add_argument('--output-revision', default='')
    append_event_parser.add_argument('--stage-outcome-ref', action='append', default=[])
    append_event_parser.add_argument('--evidence-ref', action='append', default=[])
    append_event_parser.add_argument('--review-result-ref', action='append', default=[])
    append_event_parser.add_argument('--issue-ref', action='append', default=[])
    append_event_parser.add_argument('--git-checkpoint-ref', action='append', default=[])
    append_event_parser.add_argument('--summary', required=True)
    append_event_parser.add_argument('--state-change', default='')
    append_event_parser.add_argument('--sql-change-summary', default='')
    append_event_parser.add_argument('--validation-summary', default='')
    append_event_parser.add_argument('--blocker', action='append', default=[])
    append_event_parser.add_argument('--modified-file-summary', action='append', default=[])
    append_event_parser.add_argument('--next-action', default='')
    append_event_parser.add_argument('--next-agent', default='')
    append_event_parser.add_argument('--correction-of', default='')
    append_event_parser.set_defaults(func=runtime.command_append_work_event)

    validate_work_record_parser = subparsers.add_parser('validate-work-record', help='validate canonical human-readable work_record.md events')
    agent_context.add_agent_argument(validate_work_record_parser)
    validate_work_record_parser.add_argument('--doc-root', required=True)
    validate_work_record_parser.add_argument('--version', required=True)
    validate_work_record_parser.set_defaults(func=runtime.command_validate_work_record)

    work_events_parser = subparsers.add_parser('work-events', help='read filtered version-level work events')
    agent_context.add_agent_argument(work_events_parser)
    work_events_parser.add_argument('--doc-root', required=True)
    work_events_parser.add_argument('--version', required=True)
    work_events_parser.add_argument('--target-id', default='')
    work_events_parser.add_argument('--task-id', default='')
    work_events_parser.add_argument('--attempt-id', default='')
    work_events_parser.add_argument('--event-type', default='')
    work_events_parser.add_argument('--status', default='', choices=['', *sorted(runtime.WORK_EVENT_STATUSES)])
    work_events_parser.add_argument('--limit', type=int, default=0)
    work_events_parser.set_defaults(func=runtime.command_work_events)

    render_work_record_parser = subparsers.add_parser('render-work-record', help='render canonical work_record.md')
    agent_context.add_agent_argument(render_work_record_parser)
    render_work_record_parser.add_argument('--doc-root', required=True)
    render_work_record_parser.add_argument('--version', required=True)
    render_work_record_parser.set_defaults(func=runtime.command_render_work_record)
    help_parser = subparsers.add_parser('help', help='show parser-derived machine-readable command help')
    agent_context.add_agent_argument(help_parser)
    help_parser.add_argument('--command', dest='help_command', default='')
    help_parser.add_argument('--json', action='store_true', help='retained for a uniform query interface; output is always JSON')
    help_parser.set_defaults(func=runtime.command_help)


def _add_json_flags(subparsers) -> None:
    # Every long-task command may emit a structured error when explicitly
    # requested. Commands that already expose --json keep their success
    # output semantics; this closes the error-format gap for the remaining
    # lifecycle and mutation commands.
    for command_parser in subparsers.choices.values():
        if '--json' not in command_parser._option_string_actions:
            command_parser.add_argument('--json', action='store_true', help='emit machine-readable error')


def build_parser(runtime: ModuleType) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=runtime.__doc__)
    subparsers = parser.add_subparsers(dest='command', required=True)
    _add_lifecycle_commands(subparsers, runtime)
    _add_attempt_commands(subparsers, runtime)
    _add_event_query_commands(subparsers, runtime)
    _add_record_commands(subparsers, runtime)
    _add_json_flags(subparsers)
    return parser


__all__ = ["build_parser"]
