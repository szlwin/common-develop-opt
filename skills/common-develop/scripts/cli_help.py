#!/usr/bin/env python3
"""Discover and validate machine-readable help for declared common-develop CLIs."""
from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable

import agent_context


ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = ROOT / "assets" / "capability-pack-contract.json"


class CliHelpError(ValueError):
    """Raised when a declared CLI or requested command cannot be discovered."""


def _load_contract() -> dict[str, Any]:
    try:
        value = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CliHelpError(f"cannot load CLI entrypoint contract: {exc}") from exc
    if not isinstance(value, dict):
        raise CliHelpError("capability-pack contract must be a JSON object")
    return value


def declared_cli_paths(contract: dict[str, Any] | None = None) -> list[str]:
    """Return canonical CLI paths without inspecting Python source files."""
    value = _load_contract() if contract is None else contract
    entry = value.get("entryLayer") if isinstance(value.get("entryLayer"), dict) else {}
    paths = list(entry.get("cliEntrypoints") or [])
    paths.extend(entry.get("supportCliEntrypoints") or [])
    for pack in value.get("packs") or []:
        if isinstance(pack, dict):
            paths.extend(pack.get("cliEntrypoints") or [])
    result: list[str] = []
    for path in paths:
        if not isinstance(path, str) or not path:
            raise CliHelpError(f"invalid declared CLI path: {path}")
        if path not in result:
            result.append(path)
    return result


def _script_id(path: str) -> str:
    return Path(path).stem


def declared_cli_entries(contract: dict[str, Any] | None = None) -> list[dict[str, str]]:
    value = _load_contract() if contract is None else contract
    entry = value.get("entryLayer") if isinstance(value.get("entryLayer"), dict) else {}
    public = set(entry.get("cliEntrypoints") or [])
    support = set(entry.get("supportCliEntrypoints") or [])
    pack_by_path = {
        path: str(pack.get("id") or "")
        for pack in value.get("packs") or [] if isinstance(pack, dict)
        for path in pack.get("cliEntrypoints") or []
    }
    result: list[dict[str, str]] = []
    for path in declared_cli_paths(value):
        item = {"id": _script_id(path), "path": path, "kind": "CLI"}
        if path in public:
            item["scope"] = "ENTRY"
        elif path in support:
            item["scope"] = "SUPPORT"
        else:
            item["scope"] = "CAPABILITY_PACK"
            item["capabilityPack"] = pack_by_path.get(path, "")
        result.append(item)
    return result


def resolve_cli_path(script: str, contract: dict[str, Any] | None = None) -> str:
    requested = script.strip()
    matches = [
        item["path"] for item in declared_cli_entries(contract)
        if requested in {item["id"], item["path"], Path(item["path"]).name}
    ]
    if len(matches) != 1:
        known = ", ".join(item["id"] for item in declared_cli_entries(contract))
        raise CliHelpError(f"unknown declared CLI: {script}; known={known}")
    return matches[0]


def _parser_factory(path: str):
    module = importlib.import_module(_script_id(path))
    factory = getattr(module, "build_parser", None)
    if not callable(factory):
        factory = getattr(module, "parser", None)
    if not callable(factory):
        raise CliHelpError(f"declared CLI has no build_parser()/parser() factory: {path}")
    return module, factory


def _subparser_action(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:
    return next(
        (action for action in parser._actions if isinstance(action, argparse._SubParsersAction)),
        None,
    )


def command_names(parser: argparse.ArgumentParser) -> list[str]:
    action = _subparser_action(parser)
    return list(action.choices) if action is not None else []


def _command_help_map(parser: argparse.ArgumentParser) -> dict[str, str]:
    action = _subparser_action(parser)
    if action is None:
        return {}
    return {
        str(choice.dest): str(choice.help or "")
        for choice in action._choices_actions
    }


def _json_value(value: Any) -> Any:
    if value is argparse.SUPPRESS:
        return None
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)


def _type_name(value: Any) -> str:
    if value is None:
        return "string"
    return str(getattr(value, "__name__", value))


def _argument(action: argparse.Action) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": action.dest,
        "kind": "option" if action.option_strings else "positional",
        "flags": list(action.option_strings),
        "required": bool(action.required),
        "type": _type_name(action.type),
        "action": type(action).__name__,
        "help": "" if action.help is argparse.SUPPRESS else str(action.help or ""),
    }
    if action.nargs is not None:
        result["nargs"] = action.nargs
    if action.choices is not None:
        result["choices"] = [_json_value(item) for item in action.choices]
    if action.default is not argparse.SUPPRESS:
        result["default"] = _json_value(action.default)
    if action.metavar is not None:
        result["metavar"] = _json_value(action.metavar)
    return result


def _arguments(parser: argparse.ArgumentParser) -> list[dict[str, Any]]:
    return [
        _argument(action)
        for action in parser._actions
        if not isinstance(action, argparse._SubParsersAction) and action.dest != "help"
    ]


def _metadata(module: Any, command: str) -> dict[str, Any]:
    declared = getattr(module, "COMMAND_HELP_METADATA", {})
    if not isinstance(declared, dict):
        raise CliHelpError(f"{module.__name__}.COMMAND_HELP_METADATA must be an object")
    value = declared.get(command, {})
    if not isinstance(value, dict):
        raise CliHelpError(f"invalid command help metadata: {module.__name__}.{command}")
    return dict(value)


def parser_catalog(
    parser: argparse.ArgumentParser,
    *,
    script_path: str,
    command: str = "",
    module: Any = None,
) -> dict[str, Any]:
    """Serialize one argparse parser without re-parsing its Python source."""
    subcommands = command_names(parser)
    selected = parser
    if command:
        action = _subparser_action(parser)
        if action is None or command not in action.choices:
            raise CliHelpError(f"unknown command for {_script_id(script_path)}: {command}")
        selected = action.choices[command]

    arguments = _arguments(selected)
    metadata = _metadata(module, command) if module is not None else {}
    agent_argument = next((item for item in arguments if item["name"] == "agent"), None)
    permission_command_id = str(
        metadata.get("permissionCommandId")
        or (f"{_script_id(script_path)}.{command}" if command else "")
    )
    payload: dict[str, Any] = {
        "schemaVersion": 1,
        "script": script_path,
        "scriptId": _script_id(script_path),
        "command": command or None,
        "description": str(selected.description or _command_help_map(parser).get(command, "")),
        "usage": selected.format_usage().strip(),
        "arguments": arguments,
        "agentDefault": agent_argument.get("default") if agent_argument else None,
        "mutation": metadata.get("mutation"),
        "previewSupported": metadata.get(
            "previewSupported",
            any("--apply" in item["flags"] for item in arguments),
        ),
        "permissionCommandId": permission_command_id or None,
        "permissionQuery": (
            "python3 scripts/runtime_contract.py permissions -g {CURRENT_AGENT} "
            f"--for-agent {{CURRENT_AGENT}} --command-id {permission_command_id} --json"
            if permission_command_id else None
        ),
        "machineSource": "argparse parser object",
    }
    if not command:
        help_map = _command_help_map(parser)
        action = _subparser_action(parser)
        command_entries: list[dict[str, Any]] = []
        for name in subcommands:
            command_metadata = _metadata(module, name)
            command_parser = action.choices[name] if action is not None else parser
            command_arguments = _arguments(command_parser)
            command_entries.append({
                "id": name,
                "description": help_map.get(name, ""),
                "mutation": command_metadata.get("mutation"),
                "previewSupported": command_metadata.get(
                    "previewSupported",
                    any("--apply" in item["flags"] for item in command_arguments),
                ),
                "effect": command_metadata.get("effect"),
                "permissionCommandId": command_metadata.get(
                    "permissionCommandId", f"{_script_id(script_path)}.{name}"
                ),
            })
        payload["commands"] = command_entries
        payload["commandCount"] = len(subcommands)
    if metadata.get("effect"):
        payload["effect"] = metadata["effect"]
    return payload


# Root-style scripts expose a single argparse parser instead of subcommands.
# Keep their permission IDs explicit because the parser cannot express the
# semantic operation selected by flags such as ``--check``.
ROOT_COMMAND_PERMISSION_IDS = {
    "baseline_guard": ["baseline_guard.status"],
    "check_skill": ["check_skill.check"],
    "quality_gate": ["quality_gate.run"],
    "semantic_audit": ["semantic_audit.check"],
    "render_relationships": ["render_relationships.check", "render_relationships.generate"],
}


def declared_permission_ids(contract: dict[str, Any] | None = None) -> set[str]:
    """Derive permission IDs from declared argparse parsers and metadata.

    ``scriptPermissions`` is a materialized view, so its key set must follow
    the executable parser surface.  A module may set ``PERMISSION_REQUIRED``
    to false for a utility that has no Agent authorization boundary.
    """
    permission_ids: set[str] = set()
    for entry in declared_cli_entries(contract):
        path = entry["path"]
        module, factory = _parser_factory(path)
        if getattr(module, "PERMISSION_REQUIRED", True) is False:
            continue
        parser = factory()
        commands = command_names(parser)
        if not commands:
            permission_ids.update(
                getattr(module, "ROOT_COMMAND_PERMISSION_IDS", None)
                or ROOT_COMMAND_PERMISSION_IDS.get(_script_id(path), [])
            )
            continue
        action = _subparser_action(parser)
        for name in commands:
            metadata = _metadata(module, name)
            permission_ids.add(str(metadata.get("permissionCommandId") or f"{_script_id(path)}.{name}"))
    return permission_ids


def catalog_for_script(script: str, command: str = "") -> dict[str, Any]:
    path = resolve_cli_path(script)
    module, factory = _parser_factory(path)
    original_program = sys.argv[0]
    try:
        sys.argv[0] = Path(path).name
        parser = factory()
    finally:
        sys.argv[0] = original_program
    return parser_catalog(parser, script_path=path, command=command, module=module)


def validate_declared_help() -> dict[str, Any]:
    """Execute top-level and subcommand help for every declared CLI."""
    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    for path in declared_cli_paths():
        absolute = ROOT / path
        try:
            module, factory = _parser_factory(path)
            parser = factory()
        except Exception as exc:
            errors.append(f"{path}: cannot build parser: {type(exc).__name__}: {exc}")
            continue
        commands = command_names(parser)
        metadata = getattr(module, "COMMAND_HELP_METADATA", {})
        if metadata and not set(metadata).issubset(set(commands)):
            errors.append(
                f"{path}: COMMAND_HELP_METADATA contains unknown parser commands: "
                f"metadata={sorted(set(metadata) - set(commands))} parser={sorted(commands)}"
            )
        invocations = [[sys.executable, str(absolute), "-h"]]
        invocations.extend([sys.executable, str(absolute), name, "-h"] for name in commands)
        for invocation in invocations:
            completed = subprocess.run(
                invocation,
                cwd=ROOT,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=30,
            )
            relative_command = ["python3", path, *invocation[2:]]
            passed = completed.returncode == 0 and "usage:" in completed.stdout
            checks.append({"command": relative_command, "status": "PASSED" if passed else "FAILED"})
            if not passed:
                errors.append(
                    f"help failed ({completed.returncode}): {' '.join(relative_command)}: "
                    f"{completed.stdout.strip()[:500]}"
                )
    return {
        "status": "PASSED" if not errors else "FAILED",
        "declaredCliCount": len(declared_cli_paths()),
        "helpInvocationCount": len(checks),
        "checks": checks,
        "errors": errors,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    list_parser = commands.add_parser("list", help="list declared CLI entrypoints without importing them")
    agent_context.add_agent_argument(list_parser)
    list_parser.add_argument("--json", action="store_true")
    show = commands.add_parser("show", help="show one script or subcommand as machine-readable argparse metadata")
    agent_context.add_agent_argument(show)
    show.add_argument("--script", required=True)
    show.add_argument("--command", dest="detail_command", default="")
    show.add_argument("--json", action="store_true")
    validate = commands.add_parser("validate", help="execute -h for every declared CLI and subcommand")
    agent_context.add_agent_argument(validate)
    validate.add_argument("--json", action="store_true")
    return parser


COMMAND_HELP_METADATA = {
    "list": {"mutation": False, "previewSupported": False, "effect": "READ_ONLY"},
    "show": {"mutation": False, "previewSupported": False, "effect": "READ_ONLY"},
    "validate": {"mutation": False, "previewSupported": False, "effect": "READ_ONLY"},
}


def _print(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        agent = agent_context.authorize_command(args, "cli_help", args.command, declared=True)
        if args.command == "list":
            payload: Any = {"schemaVersion": 1, "entrypoints": declared_cli_entries()}
        elif args.command == "show":
            payload = catalog_for_script(args.script, args.detail_command)
        elif args.command == "validate":
            payload = validate_declared_help()
        else:  # pragma: no cover
            raise CliHelpError(f"unsupported command: {args.command}")
        _print(payload, args.json)
        return 0 if not isinstance(payload, dict) or payload.get("status") != "FAILED" else 1
    except (CliHelpError, agent_context.AgentAuthorizationError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
