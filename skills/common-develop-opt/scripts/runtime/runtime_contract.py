#!/usr/bin/env python3
"""Query focused Runtime Contract slices without returning the whole contract."""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path
from typing import Any, Iterable

from document_core import agent_context
from runtime import contract_normalization
from resource_paths import asset_path
from runtime.contract_query_utils import (
    ContractQueryError,
    bounded_selection,
    load_json_object,
    print_payload,
    section_summary,
    select_pointer,
)

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = asset_path('assets/runtime/runtime-contract.json')


def load_contract() -> dict[str, Any]:
    try:
        contract = contract_normalization.load_runtime_contract(CONTRACT_PATH)
    except contract_normalization.ContractNormalizationError as exc:
        raise ContractQueryError(str(exc)) from exc
    if contract.get("schemaVersion") != 2:
        raise ContractQueryError("runtime contract schemaVersion must be 2")
    return contract


def _entry(
    *, command_id: str, category: str, flags: list[str], description: str,
    policy_ref: str, reference: str = "", default: Any = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": command_id,
        "category": category,
        "flags": flags,
        "description": description,
        "policyRef": policy_ref,
    }
    if reference:
        result["reference"] = reference
    if default is not None:
        result["default"] = default
    return result


def command_catalog(contract: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    contract = load_contract() if contract is None else contract
    interface = contract.get("commandInterface") or {}
    result: list[dict[str, Any]] = []

    current = interface.get("currentAgent") or {}
    result.append(_entry(
        command_id="current-agent", category="selector",
        flags=list(current.get("flags") or []),
        description="Select the current common-develop Agent for authorization and records.",
        default=current.get("default"),
        policy_ref="assets/runtime/runtime-contract.json#/commandInterface/currentAgent",
        reference=str(current.get("reference") or "references/recovery-archive/wk-cli.md"),
    ))

    intake = interface.get("requestIntake") or {}
    intake_help = intake.get("help") or {}
    for request_id, flags in (intake.get("flags") or {}).items():
        help_item = intake_help.get(request_id) or {}
        result.append(_entry(
            command_id=request_id, category="request", flags=list(flags or []),
            description=str(help_item.get("description") or intake.get("descriptionInputPolicy") or ""),
            policy_ref=f"assets/runtime/runtime-contract.json#/commandInterface/requestIntake/flags/{request_id}",
            reference=str(help_item.get("reference") or "references/core/request-intake.md"),
        ))

    for mode_id, definition in (interface.get("workModes") or {}).items():
        if not isinstance(definition, dict) or not isinstance(definition.get("flags"), list):
            continue
        flags = list(definition["flags"])
        flags.extend(str(flag) for flag in (definition.get("closeFlags") or []))
        result.append(_entry(
            command_id=mode_id, category="mode", flags=flags,
            description=str(definition.get("description") or ""),
            default=definition.get("default"),
            policy_ref=f"assets/runtime/runtime-contract.json#/commandInterface/workModes/{mode_id}",
            reference=str(definition.get("reference") or "references/core/work-mode-core.md"),
        ))

    task_plan = interface.get("taskPlanning") or {}
    if isinstance(task_plan, dict) and isinstance(task_plan.get("flags"), list):
        result.append(_entry(
            command_id="task-plan", category="action", flags=list(task_plan["flags"]),
            description=str(task_plan.get("description") or ""),
            default=task_plan.get("default"),
            policy_ref="assets/runtime/runtime-contract.json#/commandInterface/taskPlanning",
            reference=str(task_plan.get("reference") or "references/requirement-design/implementation-plan.md"),
        ))

    known_flags = {flag for item in result for flag in item["flags"]}
    for action_index, action in enumerate(contract.get("auxiliaryActions") or []):
        if not isinstance(action, dict):
            continue
        tokens = shlex.split(str(action.get("command") or ""))
        flags = [token for token in tokens if token.startswith("-")]
        if not flags or all(flag in known_flags for flag in flags):
            continue
        result.append(_entry(
            command_id=str(action.get("id") or ""), category="action", flags=flags,
            description=str(action.get("description") or ""),
            policy_ref=f"assets/runtime/runtime-contract.json#/auxiliaryActions/{action_index}",
            reference=str(action.get("reference") or "references/recovery-archive/wk-cli.md"),
        ))
        known_flags.update(flags)

    for command_id, definition in (interface.get("orchestration") or {}).items():
        if not isinstance(definition, dict):
            continue
        command = str(definition.get("command") or command_id)
        result.append(_entry(
            command_id=command_id, category="orchestration", flags=[command],
            description=str(definition.get("description") or ""),
            policy_ref=f"assets/runtime/runtime-contract.json#/commandInterface/orchestration/{command_id}",
            reference=str(definition.get("reference") or "references/recovery-archive/wk-cli.md"),
        ))
    return result


def command_by_flag(flag: str, contract: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized = flag.strip()
    for item in command_catalog(contract):
        if normalized == item["id"] or normalized in item["flags"]:
            return item
    raise ContractQueryError(f"unknown wk flag or command: {flag}")


def _authorized_permissions(contract: dict[str, Any], subject_agent: str, phase: str = "") -> dict[str, Any]:
    if subject_agent not in set(contract.get("agents") or []):
        raise ContractQueryError(f"unknown Agent: {subject_agent}")
    static: list[str] = []
    declared: list[str] = []
    task_context: list[str] = []
    for command_id, permission in sorted((contract.get("scriptPermissions") or {}).items()):
        mode = permission.get("mode") if isinstance(permission, dict) else ""
        if mode == "STATIC" and subject_agent in set(permission.get("allowedAgents") or []):
            static.append(command_id)
        elif mode == "DECLARED_AGENT":
            declared.append(command_id)
        elif mode == "TASK_CONTEXT":
            task_context.append(command_id)
    return {
        "agent": subject_agent,
        "phase": phase or None,
        "allowedStaticCommands": static,
        "allowedReadOnlyCommands": declared,
        "taskContextCommands": task_context,
        "taskContextNote": "Task-context authorization also depends on current phase, next Agent, conditional reviewers, and project manager state.",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    list_parser = commands.add_parser("list", help="list top-level Runtime Contract sections")
    agent_context.add_agent_argument(list_parser)
    list_parser.add_argument("--json", action="store_true")

    show = commands.add_parser("show", help="show one bounded JSON Pointer selection")
    agent_context.add_agent_argument(show)
    show.add_argument("--pointer", required=True)
    show.add_argument("--max-bytes", type=int, default=24_576)
    show.add_argument("--json", action="store_true")

    command = commands.add_parser("command", help="show one wk flag or orchestration command")
    agent_context.add_agent_argument(command)
    command.add_argument("--flag", required=True)
    command.add_argument("--json", action="store_true")

    phase = commands.add_parser("phase", help="show one canonical lifecycle phase")
    agent_context.add_agent_argument(phase)
    phase.add_argument("--id", required=True)
    phase.add_argument("--json", action="store_true")

    permissions = commands.add_parser("permissions", help="show compact permissions for one Agent")
    agent_context.add_agent_argument(permissions)
    permissions.add_argument("--for-agent", required=True, dest="subject_agent")
    permissions.add_argument("--phase", default="")
    permissions.add_argument("--command-id", default="")
    permissions.add_argument("--json", action="store_true")
    return parser


def _authorize(args: argparse.Namespace) -> None:
    agent = agent_context.authorize_command(args, "runtime_contract", args.command, declared=True)
def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        _authorize(args)
        contract = load_contract()
        if args.command == "list":
            payload: Any = {"sections": section_summary(contract)}
        elif args.command == "show":
            payload = {
                "pointer": args.pointer,
                "value": bounded_selection(select_pointer(contract, args.pointer), max_bytes=args.max_bytes),
            }
        elif args.command == "command":
            payload = command_by_flag(args.flag, contract)
        elif args.command == "phase":
            payload = next(
                (item for item in contract.get("canonicalPhases") or [] if isinstance(item, dict) and item.get("id") == args.id),
                None,
            )
            if payload is None:
                raise ContractQueryError(f"unknown canonical phase: {args.id}")
        elif args.command == "permissions":
            payload = _authorized_permissions(contract, args.subject_agent, args.phase)
            if args.command_id:
                definition = (contract.get("scriptPermissions") or {}).get(args.command_id)
                if not isinstance(definition, dict):
                    raise ContractQueryError(f"unknown script permission: {args.command_id}")
                payload = {"agent": args.subject_agent, "commandId": args.command_id, "definition": definition}
        else:
            raise ContractQueryError(f"unsupported command: {args.command}")
        print_payload(payload, as_json=args.json)
        return 0
    except (ContractQueryError, agent_context.AgentAuthorizationError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
