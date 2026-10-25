#!/usr/bin/env python3
"""Technology-neutral command execution and Evidence registration."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from document_core import agent_context
from runtime import work_mode

from runtime._long_task.command_evidence import (
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ExecutionEvidenceError,
    run_and_register as _run_and_register,
)


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
) -> dict[str, Any]:
    return _run_and_register(
        task_dir,
        agent=agent,
        command=command,
        revision=revision,
        phase=phase,
        cwd=cwd,
        mutation_scope=mutation_scope,
        timeout=timeout,
        scope_refs=scope_refs,
        attempt_id=attempt_id,
        declared_commands=declared_commands,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command_name", required=True)
    run = commands.add_parser("run", help="run a declared project command and register generic command Evidence")
    agent_context.add_agent_argument(run)
    run.add_argument("--task-dir", required=True)
    run.add_argument("--phase", required=True)
    run.add_argument("--revision", required=True)
    run.add_argument("--command", required=True)
    run.add_argument("--cwd", default=".")
    run.add_argument("--mutation-scope", required=True, choices=list(work_mode.MUTATION_SCOPES))
    run.add_argument("--timeout", type=int, default=DEFAULT_COMMAND_TIMEOUT_SECONDS)
    run.add_argument("--scope-ref", action="append", default=[])
    run.add_argument("--attempt-id", default="", help="bind command results to the active attempt")
    run.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = run_and_register(
            Path(args.task_dir), agent=agent_context.selected_agent(args), command=args.command,
            revision=args.revision, phase=args.phase, cwd=Path(args.cwd),
            mutation_scope=args.mutation_scope, timeout=args.timeout, scope_refs=args.scope_ref, attempt_id=args.attempt_id,
        )
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(
                f"{payload['status']} error={payload.get('errorCode') or '-'} "
                f"timeout={payload['timeoutSeconds']}s durationMs={payload['durationMs']} "
                f"evidence={payload['evidenceId']} commandResult={payload['commandResultRef']}"
            )
        return 0 if payload["status"] == "PASSED" else 2
    except (ExecutionEvidenceError, agent_context.AgentAuthorizationError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
