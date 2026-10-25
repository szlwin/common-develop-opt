"""Shared command execution and command Evidence registration primitives."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import agent_context
import evidence
import storage
import work_mode

from _long_task.record_io import version_root_from_task_dir
from common_utils import atomic_write_bytes, atomic_write_text
from _long_task.state_access import runtime_state

INLINE_LIMIT = 16 * 1024
DEFAULT_COMMAND_TIMEOUT_SECONDS = 300
COMMAND_TIMEOUT = "COMMAND_TIMEOUT"
COMMAND_EXIT_NONZERO = "COMMAND_EXIT_NONZERO"


class ExecutionEvidenceError(ValueError):
    """Raised when a command cannot be executed as Evidence."""


def _safe_id(command: str, revision: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    digest = hashlib.sha256(f"{command}\0{revision}\0{stamp}".encode("utf-8")).hexdigest()[:12]
    return f"EXEC-{stamp}-{digest}"


def _mode_for_task(task_dir: Path) -> work_mode.WorkMode:
    version_root = version_root_from_task_dir(task_dir)
    return work_mode.validate_work_file(version_root / "work.md")


def _write_stream(
    task_dir: Path,
    run_dir: Path,
    command_id: str,
    name: str,
    data: bytes,
    result: dict[str, Any],
) -> None:
    text = data.decode("utf-8", errors="replace")
    if len(data) <= INLINE_LIMIT:
        result[name] = text
        result[f"{name}_digest"] = hashlib.sha256(data).hexdigest()
        return
    output_path = run_dir / f"{command_id}.{name}.txt"
    atomic_write_bytes(output_path, data)
    result[f"{name}_ref"] = output_path.relative_to(task_dir).as_posix()
    result[f"{name}_digest"] = hashlib.sha256(data).hexdigest()


def run_and_register(
    task_dir: Path,
    *,
    agent: str,
    command: str,
    revision: str,
    phase: str,
    cwd: Path,
    mutation_scope: str,
    timeout: int,
    scope_refs: list[str],
    attempt_id: str = "",
    declared_commands: list[str] | None = None,
    capture_stderr: bool = False,
    collector: str = "generic-execution-evidence-v1",
) -> dict[str, Any]:
    """Run one command and register its immutable command-result Evidence.

    ``declared_commands`` is intentionally opt-in. The generic execution CLI
    retains its existing capability, while lifecycle auto-evidence must pass
    the current task's exact static validation command list.
    """
    task_dir = Path(task_dir).resolve()
    command = str(command or "")
    if not command or "\x00" in command:
        raise ExecutionEvidenceError("command must be a non-empty string")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or timeout <= 0:
        raise ExecutionEvidenceError(
            "timeout must be a positive number of seconds; unlimited execution is not supported"
        )
    if declared_commands is not None and command not in {str(item) for item in declared_commands}:
        raise ExecutionEvidenceError(
            f"command is not declared in task.validation_commands: {command}"
        )
    state = runtime_state(task_dir)
    current_phase = str(state.get("current_phase") or "")
    if phase != current_phase:
        raise ExecutionEvidenceError(f"phase must equal current task phase: expected={current_phase} actual={phase}")
    agent_context.authorize_task_context(
        agent, "execution_evidence", "run", phase=phase,
        next_agent=str(state.get("next_agent") or ""),
        project_manager_agent=str(state.get("project_manager_agent") or ""),
    )
    mode = _mode_for_task(task_dir)
    mutation = work_mode.mutation_decision(mode, phase, mutation_scope)
    if not mutation["allowed"]:
        raise ExecutionEvidenceError(
            f"mutation policy blocks {mutation_scope} in {phase} ({mutation['mode']})"
        )
    started = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    started_monotonic = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=str(Path(cwd).resolve()),
            shell=True,
            executable="/bin/bash",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if capture_stderr else subprocess.STDOUT,
            timeout=timeout,
            env=os.environ.copy(),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # A timeout is a real failed execution. Preserve its partial output so
        # the caller can audit why the attempt was not completed.
        stdout = exc.stdout or b""
        stderr = exc.stderr or b""
        if isinstance(stdout, str):
            stdout = stdout.encode("utf-8", errors="replace")
        if isinstance(stderr, str):
            stderr = stderr.encode("utf-8", errors="replace")
        return _persist_result(
            task_dir, agent=agent, command=command, revision=revision, phase=phase,
            cwd=Path(cwd), mutation_scope=mutation_scope, started=started,
            returncode=124, stdout=stdout, stderr=stderr,
            scope_refs=scope_refs, attempt_id=attempt_id, mutation=mutation, collector=collector,
            capture_stderr=capture_stderr, timed_out=True,
            timeout_seconds=timeout, duration_ms=_duration_ms(started_monotonic),
        )
    stdout = completed.stdout or b""
    stderr = (completed.stderr or b"") if capture_stderr else b""
    return _persist_result(
        task_dir, agent=agent, command=command, revision=revision, phase=phase,
        cwd=Path(cwd), mutation_scope=mutation_scope, started=started,
        returncode=int(completed.returncode), stdout=stdout, stderr=stderr,
        scope_refs=scope_refs, attempt_id=attempt_id, mutation=mutation, collector=collector,
        capture_stderr=capture_stderr, timeout_seconds=timeout,
        duration_ms=_duration_ms(started_monotonic),
    )


def _duration_ms(started_monotonic: float) -> int:
    """Return a non-negative wall-clock duration suitable for audit records."""
    return max(0, int(round((time.monotonic() - started_monotonic) * 1000)))


def _persist_result(
    task_dir: Path,
    *,
    agent: str,
    command: str,
    revision: str,
    phase: str,
    cwd: Path,
    mutation_scope: str,
    started: str,
    returncode: int,
    stdout: bytes,
    stderr: bytes,
    scope_refs: list[str],
    attempt_id: str,
    mutation: dict[str, Any],
    collector: str,
    capture_stderr: bool = False,
    timed_out: bool = False,
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    duration_ms: int = 0,
) -> dict[str, Any]:
    command_id = _safe_id(command, revision)
    run_dir = storage.path(task_dir, "runs") / revision
    run_dir.mkdir(parents=True, exist_ok=True)
    result_path = run_dir / f"{command_id}.json"
    status = "PASSED" if returncode == 0 else "FAILED"
    error_code = COMMAND_TIMEOUT if timed_out else (COMMAND_EXIT_NONZERO if returncode != 0 else "")
    result: dict[str, Any] = {
        "schema_version": 2,
        "kind": "command_result",
        "status": status,
        "error_code": error_code,
        "command": command,
        "exit_code": int(returncode),
        "executed_at": started,
        "completed_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "timeout_seconds": int(timeout_seconds),
        "duration_ms": int(duration_ms),
        "revision": revision,
        "output_digest": hashlib.sha256(stdout).hexdigest(),
        "metadata": {
            "cwd": str(cwd.resolve()),
            "phase": phase,
            "mutation_scope": mutation_scope,
            "timed_out": timed_out,
        },
    }
    if attempt_id:
        result["metadata"]["attempt_id"] = attempt_id
    _write_stream(task_dir, run_dir, command_id, "output", stdout, result)
    if capture_stderr:
        _write_stream(task_dir, run_dir, command_id, "stderr", stderr, result)
    atomic_write_text(result_path, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    relative = result_path.relative_to(task_dir).as_posix()
    entry = evidence.register_evidence(
        task_dir,
        agent=agent,
        evidence_type="command_ref",
        ref=relative,
        revision=revision,
        phase=phase,
        scope_refs=scope_refs,
        command_result_ref=relative,
        metadata={"collector": collector, "mutation_scope": mutation_scope},
        capture_mode="DIRECT",
    )
    return {
        "status": status,
        "exitCode": int(returncode),
        "errorCode": error_code,
        "timeoutSeconds": int(timeout_seconds),
        "durationMs": int(duration_ms),
        "timedOut": bool(timed_out),
        "commandResultRef": relative,
        "evidenceId": entry["evidence_id"],
        "revision": revision,
        "phase": phase,
        "mutationPolicy": mutation,
    }
