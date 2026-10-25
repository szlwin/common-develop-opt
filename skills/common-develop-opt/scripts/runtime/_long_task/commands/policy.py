"""Runtime task policy commands."""
from __future__ import annotations

import argparse
from pathlib import Path

from document_core import agent_context
from document_core.common_utils import now
from runtime.error_catalog import LifecycleError
from runtime._long_task import task_events
from runtime._long_task.commands.queries import emit_json, find_plan_task, load_task_runtime_documents
from runtime._long_task.state_access import append_task_events_payloads, task_state_path
from runtime._long_task.record_io import transactional_text_write


def command_set_task_policy(args: argparse.Namespace) -> int:
    """Set or clear the iteration-scoped runtime max_attempts override."""
    task_dir = Path(args.task_dir)
    agent = agent_context.authorize_command(args, "long_task", "set-task-policy")
    try:
        requested = int(args.max_attempts)
    except (TypeError, ValueError) as exc:
        raise LifecycleError(
            "--max-attempts 必须是大于等于 0 的整数",
            code="LIFECYCLE_POLICY_INVALID",
            repair_command="重新执行 set-task-policy 并传入非负整数 --max-attempts",
        ) from exc
    if requested < 0:
        raise LifecycleError(
            "--max-attempts 必须是大于等于 0 的整数",
            code="LIFECYCLE_POLICY_INVALID",
            repair_command="重新执行 set-task-policy 并传入非负整数 --max-attempts",
        )
    reason = str(getattr(args, "reason", "") or "").strip()
    if not reason:
        raise LifecycleError(
            "set-task-policy 必须提供非空 --reason",
            code="LIFECYCLE_POLICY_INVALID",
            repair_command="重新执行 set-task-policy 并提供 --reason",
        )

    (state_document, state), (_plan_document, tasks), (_attempts_document, attempts) = load_task_runtime_documents(task_dir)
    task_id = str(args.task_id or "").strip()
    task = find_plan_task(tasks, task_id)
    iteration_id = str(task.get("iteration_id") or "").strip()
    if not iteration_id:
        raise LifecycleError(
            f"任务 {task_id} 缺少当前 iteration_id，无法设置运行时策略",
            code="LIFECYCLE_POLICY_INVALID",
            repair_command="先修复 task_plan.json 的当前 iteration_id，再重试 set-task-policy",
        )

    current_attempts = [
        item for item in attempts
        if isinstance(item, dict)
        and str(item.get("task_id") or "") == task_id
        and str(item.get("iteration_id") or "") == iteration_id
    ]
    attempt_count = len(current_attempts)
    reduced = task_events.reduce_events(task_events.read_events(task_dir))
    policy_map = reduced.get("task_policies") if isinstance(reduced.get("task_policies"), dict) else {}
    current_policy = (policy_map.get(task_id) or {}).get(iteration_id) if task_id else None
    current_policy = current_policy if isinstance(current_policy, dict) else None
    # ``after=0`` is an auditable tombstone, not an active override.  Compare
    # future changes with the effective static value so clear -> set-to-static
    # remains correct and a tombstone never becomes ``before=0``.
    active_policy = (
        current_policy
        if current_policy and int(current_policy.get("after") or 0) > 0
        else None
    )
    declared = int(task.get("declared_max_attempts", task.get("max_attempts", 0)) or 0)
    before = int(active_policy.get("after") or 0) if active_policy else declared
    effective_after = requested if requested > 0 else declared

    # A finite effective limit must remain above the number of attempts already
    # consumed in this iteration.  This also applies when clearing an override:
    # clearing can restore a smaller static limit and must not strand a task in
    # an unreviewed BLOCKED state.
    if effective_after > 0 and effective_after <= attempt_count:
        target = requested if requested > 0 else declared
        action = "设置" if requested > 0 else "清除覆盖并恢复"
        raise LifecycleError(
            f"任务 {task_id} 当前 iteration 已有 {attempt_count} 次 attempt，不能{action} max_attempts={target}",
            code="LIFECYCLE_POLICY_INVALID",
            repair_command=f"传入大于当前 attempt 数 {attempt_count} 的 --max-attempts，或先 reopen-phase 创建新 iteration",
        )
    if effective_after == before:
        emit_json({
            "taskId": task_id,
            "iterationId": iteration_id,
            "before": before,
            "after": requested,
            "effectiveMaxAttempts": effective_after,
            "source": "runtime_policy" if active_policy else "static_plan",
            "changed": False,
        })
        return 0

    timestamp = now()
    spec = task_events.task_policy_event_spec(
        task_id,
        iteration_id,
        before,
        requested,
        reason,
        agent,
        timestamp=timestamp,
        source="long_task.py set-task-policy",
    )
    payloads, _projected, _history = append_task_events_payloads(
        task_dir,
        task_state_path(task_dir),
        state_document,
        state,
        [spec],
        synced_at=timestamp,
    )
    transactional_text_write(payloads)
    emit_json({
        "taskId": task_id,
        "iterationId": iteration_id,
        "before": before,
        "after": requested,
        "effectiveMaxAttempts": requested if requested > 0 else declared,
        "source": "runtime_policy" if requested > 0 else "static_plan",
        "changed": True,
        "eventType": "TASK_POLICY_SET",
        "reason": reason,
        "agent": agent,
    })
    return 0

__all__ = ["command_set_task_policy"]
