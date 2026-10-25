#!/usr/bin/env python3
"""Report whether the independent test database environment is ready.

The command never prints credential values. A missing configuration is a normal
SKIPPED outcome rather than a command failure, because isolated/unit tests may
still run and the Skill must never fall back to a local, development, or
production business database.
"""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Mapping

import agent_context


CONTRACT = agent_context.RUNTIME_CONTRACT.get("testExecution", {}).get("independentDatabase", {})
GROUPS = CONTRACT.get("requiredEnvironmentGroups", {})
READY_STATUS = str(CONTRACT.get("readyStatus", "READY"))
MISSING_STATUS = str(CONTRACT.get("missingStatus", "SKIPPED"))
MISSING_REASON = str(CONTRACT.get("missingReason", "独立测试库环境未完整配置，跳过独立测试库测试。"))

COMMAND_HELP_METADATA = {
    "status": {
        "mutation": False,
        "previewSupported": False,
        "effect": "READ_ONLY",
    },
}

if not isinstance(GROUPS, dict) or set(GROUPS) != {"url", "username", "password"}:
    raise RuntimeError("runtime contract independent test database environment groups are invalid")


def _first_non_empty(names: list[str], env: Mapping[str, str]) -> str:
    for name in names:
        if str(env.get(name, "")).strip():
            return name
    return ""


def resolve_independent_database(env: Mapping[str, str] | None = None) -> dict[str, object]:
    values = os.environ if env is None else env
    resolved: dict[str, str] = {}
    missing: list[str] = []
    required: dict[str, list[str]] = {}

    for group_name in ("url", "username", "password"):
        raw_names = GROUPS.get(group_name, [])
        if not isinstance(raw_names, list) or not raw_names or any(not isinstance(item, str) for item in raw_names):
            raise RuntimeError(f"runtime contract independent test database group is invalid: {group_name}")
        names = [item for item in raw_names if item]
        required[group_name] = names
        selected = _first_non_empty(names, values)
        resolved[group_name] = selected
        if not selected:
            missing.append("|".join(names))

    ready = not missing
    return {
        "ready": ready,
        "status": READY_STATUS if ready else MISSING_STATUS,
        "requiredEnvironmentGroups": required,
        "resolvedVariableNames": resolved,
        "missingEnvironmentGroups": missing,
        "reason": "" if ready else MISSING_REASON,
        "fallbackAllowed": False,
        "secretValuesExposed": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    command = parser.add_subparsers(dest="command", required=True)
    status = command.add_parser("status", help="show independent test database readiness")
    agent_context.add_agent_argument(status)
    status.add_argument("--json", action="store_true", help="emit machine-readable readiness without secret values")
    return parser


def command_status(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "independent_test_environment", "status")
    record = resolve_independent_database()
    if args.json:
        print(json.dumps({"independentDatabase": record}, ensure_ascii=False, indent=2))
    else:
        print(f"INDEPENDENT_TEST_DB {record['status']}")
        if record["ready"]:
            resolved = record["resolvedVariableNames"]
            print(
                "variables="
                + ",".join(str(resolved[key]) for key in ("url", "username", "password"))
            )
        else:
            print("missing=" + ",".join(str(item) for item in record["missingEnvironmentGroups"]))
            print("reason=" + str(record["reason"]))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "status":
        return command_status(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
