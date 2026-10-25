"""Validation task plan for the internal runtime."""
from __future__ import annotations
import re
import doc_layout
from _long_task import task_events
from typing import Any, Optional
from _long_task.contracts import *
from _long_task.record_io import *
from _long_task.review_validation import *

from _long_task.phase_policy import phase_predecessors
def find_cycle(graph: dict[str, list[str]]) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> Optional[list[str]]:
        if node in visiting:
            try:
                index = stack.index(node)
            except ValueError:
                index = 0
            return stack[index:] + [node]
        if node in visited:
            return None
        visiting.add(node)
        stack.append(node)
        for dep in graph.get(node, []):
            cycle = visit(dep)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(node)
        visited.add(node)
        return None
    for node in graph:
        cycle = visit(node)
        if cycle:
            return cycle
    return []


def validate_task_plan(
    tasks: list[dict[str, Any]],
    phases: list[str],
    trace_map: dict[str, dict[str, Any]],
    errors: list[str],
    *,
    minimal: bool = False,
) -> set[str]:
    trace_ids = set(trace_map)
    task_ids: set[str] = set()
    logical_task_ids: set[str] = set()
    graph: dict[str, list[str]] = {}
    for index, task in enumerate(tasks):
        prefix = f'task-plan[{index}]'
        if not isinstance(task, dict):
            errors.append(f'{prefix} must be an object')
            continue
        static_task = {key: value for key, value in task.items() if key not in task_events.TASK_PLAN_RUNTIME_FIELDS}
        validate_allowed_fields(static_task, TASK_PLAN_FIELDS, prefix, errors)
        task_id = str(task.get('id') or '').strip()
        if not task_id:
            errors.append(f'{prefix} missing id')
            continue
        if task_id in task_ids:
            errors.append(f'duplicate task id: {task_id}')
        task_ids.add(task_id)
        for key in ('logical_task_id', 'iteration_id', 'revision_reason', 'title', 'objective', 'phase', 'owner_agent'):
            if not is_nonempty_string(task.get(key)):
                errors.append(f'{prefix} missing {key}')
        logical_task_id = str(task.get('logical_task_id') or '').strip()
        if logical_task_id:
            if logical_task_id in logical_task_ids:
                errors.append(f'duplicate logical_task_id: {logical_task_id}')
            logical_task_ids.add(logical_task_id)
        if not isinstance(task.get('iteration_no'), int) or isinstance(task.get('iteration_no'), bool) or task.get('iteration_no') < 1:
            errors.append(f'{prefix} iteration_no must be a positive integer')
        if not isinstance(task.get('feature_id'), str):
            errors.append(f'{prefix} feature_id must be a string')
        if not isinstance(task.get('supersedes_iteration_id'), str):
            errors.append(f'{prefix} supersedes_iteration_id must be a string')
        if KNOWN_AGENTS and is_nonempty_string(task.get('owner_agent')) and (task.get('owner_agent') not in KNOWN_AGENTS):
            errors.append(f"{prefix} unknown owner_agent: {task.get('owner_agent')}")
        if task.get('phase') not in phases:
            errors.append(f"{prefix} invalid phase: {task.get('phase')}")
        for key in ('depends_on', 'reviewer_agents', 'allowed_files', 'acceptance_trace_ids', 'flow_refs', 'flow_step_refs', 'validation_commands', 'expected_results', 'stop_conditions', 'risk_triggers'):
            if not validate_string_list(task.get(key, [])):
                errors.append(f'{prefix} {key} must be a string array')
        doc_layout.validate_task_plan_paths(task, prefix, errors)
        for reviewer in task.get('reviewer_agents') or []:
            if KNOWN_AGENTS and reviewer not in KNOWN_AGENTS:
                errors.append(f'{prefix} unknown reviewer: {reviewer}')
        declared_triggers = set(task.get('risk_triggers') or [])
        unknown_triggers = sorted(declared_triggers - set(RISK_REVIEWER_CATALOG))
        if unknown_triggers:
            errors.append(
                f"{prefix} unknown risk_triggers: {unknown_triggers}; "
                f"allowed values: {sorted(RISK_REVIEWER_CATALOG)}"
            )
        task_phase = str(task.get('phase') or '')
        for trigger in sorted(declared_triggers & set(RISK_REVIEWER_CATALOG)):
            allowed_phases = set(RISK_REVIEWER_CATALOG[trigger].get('allowedPhases', []))
            if task_phase and task_phase not in allowed_phases:
                errors.append(
                    f"{prefix} risk trigger {trigger} is not allowed in phase {task_phase}; "
                    f"allowed phases: {sorted(allowed_phases)}"
                )
        input_revisions = task.get('input_revisions')
        if not isinstance(input_revisions, dict):
            errors.append(f'{prefix} input_revisions must be an object')
        elif task_phase in phases:
            expected_input_phases = phase_predecessors(task_phase, minimal)
            missing_inputs = [
                input_phase for input_phase in expected_input_phases
                if input_phase not in input_revisions
            ]
            unexpected_inputs = sorted(
                str(input_phase) for input_phase in input_revisions
                if input_phase not in set(expected_input_phases)
            )
            if missing_inputs:
                errors.append(
                    f'{prefix} input_revisions missing required predecessors: {missing_inputs}'
                )
            if unexpected_inputs:
                errors.append(
                    f'{prefix} input_revisions contains unexpected predecessors: {unexpected_inputs}; '
                    f'expected: {expected_input_phases}'
                )
        max_attempts = task.get('max_attempts')
        if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
            errors.append(f'{prefix} max_attempts must be a positive integer')
        graph[task_id] = list(task.get('depends_on') or [])
        linked_traces: list[dict[str, Any]] = []
        for trace_id in task.get('acceptance_trace_ids') or []:
            if trace_id not in trace_ids:
                errors.append(f'{prefix} references unknown traceability id: {trace_id}')
            elif isinstance(trace_map.get(trace_id), dict):
                linked_traces.append(trace_map[trace_id])
        flow_refs = task.get('flow_refs') or []
        flow_step_refs = task.get('flow_step_refs') or []
        invalid_flow_refs = [item for item in flow_refs if not re.fullmatch(r'FLOW-[A-Z0-9_-]+', str(item))]
        invalid_step_refs = [item for item in flow_step_refs if not re.fullmatch(r'STEP-[A-Z0-9_-]+', str(item))]
        if invalid_flow_refs:
            errors.append(f'{prefix} invalid flow_refs: {invalid_flow_refs}')
        if invalid_step_refs:
            errors.append(f'{prefix} invalid flow_step_refs: {invalid_step_refs}')
        required_flow_refs = {
            flow_id for trace in linked_traces if trace.get('business_flow_required') is True
            for flow_id in (trace.get('business_flow_refs') or [])
        }
        if required_flow_refs and not set(flow_refs).intersection(required_flow_refs):
            errors.append(f'{prefix} flow-required traces need matching flow_refs: {sorted(required_flow_refs)}')
        if required_flow_refs and task.get('phase') in {'design', 'test_design', 'implementation_plan', 'development', 'code_review', 'testing', 'completion_verification'} and not flow_step_refs:
            errors.append(f'{prefix} flow-required task missing flow_step_refs')
    for task_id, dependencies in graph.items():
        for dependency in dependencies:
            if dependency not in task_ids:
                errors.append(f'task {task_id} depends on unknown task: {dependency}')
    cycle = find_cycle(graph)
    if cycle:
        errors.append(f"task dependency cycle: {' -> '.join(cycle)}")
    return task_ids


def attempt_event_type(status: str) -> str:
    return {
        'PASSED': 'TASK_ATTEMPT_COMPLETED',
        'FAILED': 'TASK_ATTEMPT_FAILED',
        'BLOCKED': 'TASK_ATTEMPT_BLOCKED',
        'PARTIAL': 'TASK_ATTEMPT_PARTIAL',
        'CANCELLED': 'TASK_ATTEMPT_CANCELLED',
        'RUNNING': 'TASK_ATTEMPT_STARTED',
    }[status]
