"""Api for the internal runtime."""
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
import impact_analysis
from error_catalog import SkillError, emit_cli_error
from _long_task import task_events
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from common_utils import now, sha256_file
from _long_task.contracts import *
from _long_task.record_io import *
from _long_task.review_validation import *
from _long_task.state_access import *
from _long_task.phase_policy import *
from _long_task.architecture_gate import *
from _long_task.evidence_gate import *
from _long_task.commands.initialization import *
from _long_task.validation.task_plan import *
from _long_task.validation.attempt import *
from _long_task.validation.risk import *
from _long_task.validation.acceptance import *
from _long_task.validation.task import *
from _long_task.commands.lifecycle import *
from _long_task.commands.queries import *
from _long_task.commands.policy import *
from _long_task.commands.work_record import *
from _long_task.commands.maintenance import *
from _long_task.commands.traceability import *
from _long_task.commands import lifecycle as _lifecycle_commands
from _long_task.command_evidence import DEFAULT_COMMAND_TIMEOUT_SECONDS
from _long_task.amendment_validation import task_writer_lock


_MUTATING_COMMANDS = {
    'init', 'reopen-phase', 'amend-phase', 'sync-work-mode', 'skip-phase', 'sync-risk-reviewers',
    'advance-development-step', 'advance-phase', 'resume', 'start-attempt',
    'finish-attempt', 'publish-artifact', 'finalize-phase', 'issue-event', 'revalidate-traceability',
    'append-work-event', 'set-task-policy', 'compact-events',
}
_COMMAND_EFFECTS = {
    'resume': 'CACHE_WRITE',
    'init': 'PROJECT_DOCUMENT_WRITE',
    'reopen-phase': 'LIFECYCLE_EVENT_WRITE',
    'amend-phase': 'LIFECYCLE_EVENT_WRITE',
    'sync-work-mode': 'LIFECYCLE_EVENT_WRITE',
    'skip-phase': 'LIFECYCLE_EVENT_WRITE',
    'sync-risk-reviewers': 'LIFECYCLE_EVENT_WRITE',
    'advance-development-step': 'LIFECYCLE_EVENT_WRITE',
    'advance-phase': 'LIFECYCLE_EVENT_WRITE',
    'start-attempt': 'LIFECYCLE_EVENT_WRITE',
    'finish-attempt': 'LIFECYCLE_EVENT_WRITE',
    'publish-artifact': 'LIFECYCLE_EVENT_WRITE',
    'finalize-phase': 'LIFECYCLE_EVENT_WRITE',
    'issue-event': 'LIFECYCLE_EVENT_WRITE',
    'append-work-event': 'VERSION_WORK_RECORD_WRITE',
    'set-task-policy': 'LIFECYCLE_EVENT_WRITE',
    'compact-events': 'LIFECYCLE_EVENT_WRITE',
    'revalidate-traceability': 'LIFECYCLE_EVENT_WRITE',
}
COMMAND_HELP_METADATA = {
    command: {
        'mutation': command in _MUTATING_COMMANDS,
        'previewSupported': False,
        'effect': _COMMAND_EFFECTS.get(command, 'READ_ONLY'),
    }
    for command in (
        'init', 'validate', 'reopen-phase', 'amend-phase', 'sync-work-mode', 'skip-phase', 'sync-risk-reviewers',
        'list-risk-triggers', 'advance-development-step', 'advance-phase', 'resume',
        'start-attempt', 'finish-attempt', 'publish-artifact', 'finalize-phase',
        'task-events', 'issue-event', 'revalidate-traceability', 'attempts', 'read-attempt', 'latest-attempt',
        'task-context', 'append-work-event', 'validate-work-record', 'work-events',
        'render-work-record', 'set-task-policy', 'compact-events', 'help',
    )
}
COMMAND_HELP_METADATA['list-risk-triggers']['permissionCommandId'] = 'long_task.list-risk-triggers'
COMMAND_HELP_METADATA['render-work-record']['permissionCommandId'] = 'long_task.work-events'

# Keep command dependencies late-bound at the facade boundary. This preserves
# runtime substitution (for example, focused test doubles) without allowing
# internal command modules to import the public ``long_task`` facade.
_LIFECYCLE_COMMAND_NAMES = (
    'command_validate', 'command_sync_work_mode', 'command_skip_phase', 'command_sync_risk_reviewers',
    'command_list_risk_triggers', 'command_reopen_phase', 'command_amend_phase',
    'command_advance_development_step', 'command_advance_phase',
    'command_publish_artifact', 'command_finalize_phase',
    'command_start_attempt', 'command_finish_attempt',
    'command_issue_event', 'command_set_task_policy', 'command_compact_events',
    'command_revalidate_traceability',
)
_LIFECYCLE_IMPLEMENTATIONS = {
    name: getattr(_lifecycle_commands, name, globals().get(name))
    for name in _LIFECYCLE_COMMAND_NAMES
}

_TASK_WRITER_COMMANDS = set(_LIFECYCLE_COMMAND_NAMES) - {
    'command_validate', 'command_list_risk_triggers',
}


def _dispatch_lifecycle_command(name: str, args: argparse.Namespace) -> int:
    # Keep the facade's late-bound dependencies available after lifecycle
    # commands are split into domain modules.  Tests and integrations patch
    # dependencies on ``long_task``; copy those bindings into the concrete
    # implementation module instead of assuming every command still lives in
    # ``commands.lifecycle``.
    implementation = _LIFECYCLE_IMPLEMENTATIONS[name]
    implementation_module = sys.modules[implementation.__module__]
    for dependency in (
        'validate_task', 'runtime_state', 'runtime_issues', 'load_task_history',
        'append_task_events_payloads', 'read_task_state_cache', 'task_state_path',
    ):
        if dependency in globals() and dependency in implementation_module.__dict__:
            implementation_module.__dict__[dependency] = globals()[dependency]
    if name in _TASK_WRITER_COMMANDS:
        writer = task_writer_lock(Path(args.task_dir))
        try:
            writer.__enter__()
        except ValueError as exc:
            raise LifecycleError(str(exc)) from exc
        try:
            return int(implementation(args))
        finally:
            writer.__exit__(None, None, None)
    return int(implementation(args))


def _lifecycle_delegate(name: str):
    def delegated(args: argparse.Namespace) -> int:
        return _dispatch_lifecycle_command(name, args)
    delegated.__name__ = name
    delegated.__qualname__ = name
    return delegated


for _command_name in _LIFECYCLE_COMMAND_NAMES:
    globals()[_command_name] = _lifecycle_delegate(_command_name)
def command_help(args: argparse.Namespace) -> int:
    """Return parser-derived help without reading this source file."""
    agent = agent_context.authorize_command(args, 'long_task', 'help', declared=True)
    payload = cli_help.parser_catalog(
        build_parser(),
        script_path='scripts/long_task.py',
        command=str(args.help_command or ''),
        module=sys.modules[__name__],
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main(argv: Optional[Iterable[str]]=None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        doc_layout.validate_cli_namespace(args)
        return int(args.func(args))
    except (
        agent_context.AgentAuthorizationError,
        impact_analysis.ImpactAnalysisError,
        OSError,
        ValueError,
    ) as exc:
        emit_cli_error(exc, json_output=bool(getattr(args, 'json', False)), stream=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    from types import SimpleNamespace
    from _long_task.cli import build_parser as _build_parser
    runtime = sys.modules.get(__name__) or SimpleNamespace(**globals())
    return _build_parser(runtime)
