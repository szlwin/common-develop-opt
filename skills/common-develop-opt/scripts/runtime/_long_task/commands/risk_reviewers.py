"""Risk-reviewer catalog commands for the long-task CLI."""
from __future__ import annotations

import argparse

from document_core import agent_context
from runtime.error_catalog import LifecycleError
from runtime._long_task.contracts import PHASES, RISK_REVIEWER_CATALOG
from runtime._long_task.commands.queries import emit_json


def command_list_risk_triggers(args: argparse.Namespace) -> int:
    """List the contract-defined risk triggers, optionally scoped to a phase."""
    # This is a public, read-only catalog command.  It is not a task-context
    # validation alias: callers may use it before a task exists.
    agent_context.authorize_command(args, 'long_task', 'list-risk-triggers')
    phase = str(getattr(args, 'phase', '') or '')
    if phase and phase not in PHASES:
        raise LifecycleError(f'unknown phase: {phase}')
    items = []
    for trigger, definition in sorted(RISK_REVIEWER_CATALOG.items()):
        allowed_phases = list(definition.get('allowedPhases', []))
        if phase and phase not in allowed_phases:
            continue
        items.append({
            'trigger': trigger,
            'reviewerAgent': definition.get('reviewerAgent'),
            'allowedPhases': allowed_phases,
            'signals': list(definition.get('signals', [])),
        })
    if getattr(args, 'json', False):
        emit_json({'phase': phase, 'count': len(items), 'riskTriggers': items})
    else:
        for item in items:
            print(f"{item['trigger']}\t{item['reviewerAgent']}\t{','.join(item['allowedPhases'])}")
    return 0
