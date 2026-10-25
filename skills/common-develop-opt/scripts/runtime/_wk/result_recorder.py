"""One writer transaction over canonical Evidence, Assertions and attempt events."""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

from document_core import acceptance
from runtime import current_manifest
from document_core import agent_context
from document_core import evidence
from runtime import work_mode
from runtime.error_catalog import SkillError
from document_core.common_utils import attach_rollback_details, now, restore_files
from runtime._long_task.amendment_validation import task_writer_lock
from runtime._long_task.record_io import transactional_text_write, version_root_from_task_dir
from runtime._long_task.state_access import (
    append_task_events_payloads, load_task_history, read_task_state_cache,
    runtime_tasks, task_state_path,
)


def _reject(code: str, message: str) -> None:
    raise SkillError(code, message)


def _binding(task_dir: Path, *, agent: str, attempt_id: str, command_id: str,
             result_file: Path, revision: str, phase: str) -> tuple[dict, dict, str, dict]:
    document, state = read_task_state_cache(task_dir)
    history = load_task_history(task_dir)
    attempt = next((a for a in history.get("attempts", []) if a.get("attempt_id") == attempt_id), None)
    if not attempt or attempt.get("status") != "RUNNING":
        _reject("RESULT_ATTEMPT_NOT_RUNNING", "结果只能登记到当前 RUNNING attempt")
    task = next((t for t in runtime_tasks(task_dir) if t.get("id") == attempt.get("task_id")), None)
    if not task or attempt.get("iteration_id") != task.get("iteration_id"):
        _reject("RESULT_ITERATION_STALE", "attempt 不属于当前任务 iteration")
    artifact = (state.get("artifact_revisions") or {}).get(phase) or {}
    if (phase != state.get("current_phase") or phase != attempt.get("phase")
            or phase != task.get("phase") or task.get("status") != "RUNNING"
            or task.get("id") not in state.get("active_task_ids", [])
            or attempt.get("iteration_id") != artifact.get("iteration_id")):
        _reject("RESULT_PHASE_MISMATCH", "attempt、任务和当前阶段绑定不一致")
    if agent != attempt.get("agent") or agent != task.get("owner_agent"):
        _reject("RESULT_OWNER_MISMATCH", "只有当前 attempt Owner 可以登记执行结果")
    agent_context.authorize_task_context(agent, "long_task", "finish-attempt", phase=phase,
        next_agent=str(state.get("next_agent") or ""),
        project_manager_agent=str(state.get("project_manager_agent") or ""))
    mode = work_mode.validate_work_file(version_root_from_task_dir(task_dir) / "work.md")
    if not work_mode.mutation_decision(mode, phase, "taskFacts")["allowed"]:
        _reject("RESULT_MUTATION_FORBIDDEN", "当前工作模式禁止登记任务执行结果")
    from runtime._long_task.evidence_gate import command_result_payload
    result = command_result_payload(task_dir, str(result_file))
    metadata = result.get("metadata") or {}
    if not isinstance(metadata, dict):
        _reject("RESULT_BINDING_MISMATCH", "结果 metadata 必须为对象")
    if not command_id or command_id != str(result.get("command_id") or result_file.stem):
        _reject("RESULT_COMMAND_ID_MISMATCH", "command ID 必须匹配结果文件的 ID")
    if result.get("command") not in (task.get("validation_commands") or []):
        _reject("RESULT_COMMAND_NOT_DECLARED", "命令必须精确匹配当前任务 validation_commands")
    if (not revision or result.get("revision") != revision
            or metadata.get("attempt_id") != attempt_id or metadata.get("phase") != phase):
        _reject("RESULT_BINDING_MISMATCH", "结果文件必须绑定本次 attempt、phase 和 revision")
    # A running attempt may introduce a candidate revision before publication.
    # Once it has Evidence, every command must prove that same candidate.
    known = str(attempt.get("output_revision") or "")
    if not known:
        known = str(artifact.get("revision") or "")
    if known and revision != known:
        _reject("CURRENT_REVISION_EVIDENCE_STALE", "执行结果不匹配当前 revision")
    for prior in history.get("attempts", []):
        if (prior.get("phase") == phase and prior.get("iteration_id") != attempt.get("iteration_id")
                and prior.get("output_revision") == revision):
            _reject("CURRENT_REVISION_EVIDENCE_STALE", "不得复用历史 iteration 的输出 revision")
    for evidence_id in attempt.get("command_evidence_ids") or []:
        evidence.require_evidence(task_dir, evidence_id, expected_revision=revision, expected_phase=phase)
    if result.get("kind") != "command_result" or type(result.get("exit_code")) is not int:
        _reject("RESULT_FORMAT_INVALID", "结果必须是带整数 exit_code 的 command_result")
    return copy.deepcopy(attempt), task, document, state


def record(task_dir: Path, *, agent: str, attempt_id: str, command_id: str,
           result_file: Path, revision: str, phase: str, scope_refs: list[str],
           acceptance_id: str = "") -> dict[str, Any]:
    task_dir, result_file = task_dir.resolve(), result_file.resolve()
    if not result_file.is_file():
        _reject("RESULT_FILE_MISSING", f"result file does not exist: {result_file}")
    # Lock precedes reads, validation, backups and every write. New files are
    # represented by None so rollback removes them, using shared rollback IO.
    with task_writer_lock(task_dir):
        paths = (task_dir / "task_events.jsonl", evidence.registry_path(task_dir),
                 task_state_path(task_dir), task_dir / "acceptance.json")
        originals = {p: p.read_bytes() if p.is_file() else None for p in paths}
        try:
            attempt, task, document, state = _binding(task_dir, agent=agent,
                attempt_id=attempt_id, command_id=command_id, result_file=result_file,
                revision=revision, phase=phase)
            command_ref = evidence.normalize_task_ref(task_dir, result_file)
            if acceptance_id:
                query = current_manifest.query_traceability(task_dir, acceptance_id=acceptance_id)
                owned_traces = set(task.get("acceptance_trace_ids") or [])
                if query["status"] != "PASSED" or not any(t.get("id") in owned_traces for t in query["items"]):
                    _reject("RESULT_ACCEPTANCE_NOT_DECLARED", "Acceptance ID 不属于当前任务 Trace 的验收范围")
            entry = evidence.register_evidence(task_dir, agent=agent, evidence_type="command_ref",
                ref=command_ref, revision=revision, phase=phase,
                scope_refs=list(dict.fromkeys([*scope_refs, task["id"], attempt_id,
                    json.loads(result_file.read_text(encoding="utf-8"))["command"]])),
                command_result_ref=command_ref,
                metadata={"attemptId": attempt_id, "commandId": command_id}, capture_mode="DIRECT")
            evidence_id = entry["evidence_id"]
            assertion = None
            if acceptance_id:
                key = json.dumps([attempt_id, command_id, revision, acceptance_id], separators=(",", ":"))
                assertion_id = "ASSERT-" + hashlib.sha256(key.encode()).hexdigest()[:24].upper()
                assertions = acceptance.load_document(task_dir)
                assertion = next((a for a in assertions.get("assertions", []) if a.get("assertion_id") == assertion_id), None)
                if assertion is None:
                    acceptance.add_assertion(task_dir, assertion_id=assertion_id,
                        acceptance_id=acceptance_id, statement=f"Declared command {command_id} exits successfully",
                        assertion_type="COMMAND_EXIT", phase=phase, revision=revision, blocking=True,
                        parameters={"expected": 0, "operator": "EQUALS"}, source_refs=[command_ref])
                    assertions = acceptance.load_document(task_dir)
                    assertion = next(a for a in assertions["assertions"] if a["assertion_id"] == assertion_id)
                assertion["evidence_ids"] = [evidence_id]
                status, reason = acceptance.verify_assertion(task_dir, assertion, agent)
                assertion.update(status=status, reason=reason, verified_by_agent=agent, verified_at=now())
                acceptance.save_document(task_dir, assertions)
            already_recorded = evidence_id in (attempt.get("command_evidence_ids") or [])
            for field in ("command_evidence_ids", "evidence_ids"):
                attempt[field] = list(dict.fromkeys([*(attempt.get(field) or []), evidence_id]))
            payloads, _, _ = append_task_events_payloads(task_dir, task_state_path(task_dir), document, state,
                [] if already_recorded else [{"type": "ATTEMPT_COMMAND_RECORDED", "entity_id": attempt_id,
                    "timestamp": now(), "source": "wk record-result", "payload": {"attempt": attempt}}], synced_at=now())
            transactional_text_write(payloads)
            # Compute the real Gate; recording a successful command is not a
            # lifecycle completion or independent Review conclusion.
            from runtime import long_task
            from runtime._long_task.lifecycle_decision import evaluate
            gate = evaluate(task_dir, long_task)
            return {"kind": "COMMAND_RESULT_REGISTRATION", "status": "RECORDED", "attemptId": attempt_id, "commandId": command_id,
                    "evidence": entry, "acceptance": assertion, "gate": gate,
                    "idempotent": already_recorded}
        except Exception as exc:
            attach_rollback_details(exc, restore_files(originals))
            raise
