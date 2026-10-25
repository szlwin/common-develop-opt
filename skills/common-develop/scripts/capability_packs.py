#!/usr/bin/env python3
"""Query the three internal capability packs of the single common-develop Skill."""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Iterable

import agent_context
from _core import capability_packs as core


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    list_parser = commands.add_parser("list", help="list the three internal capability packs")
    agent_context.add_agent_argument(list_parser)
    list_parser.add_argument("--json", action="store_true")
    show = commands.add_parser("show", help="show one internal capability pack")
    agent_context.add_agent_argument(show)
    show.add_argument("--pack", choices=core.PACK_IDS, required=True)
    show.add_argument("--json", action="store_true")
    resolve = commands.add_parser("resolve", help="resolve capability packs for one task type and phase")
    agent_context.add_agent_argument(resolve)
    resolve.add_argument("--task-type", required=True)
    resolve.add_argument("--phase", default="")
    resolve.add_argument("--json", action="store_true")
    validate = commands.add_parser("validate", help="validate the single-Skill capability-pack contract")
    agent_context.add_agent_argument(validate)
    validate.add_argument("--json", action="store_true")
    return parser


def _print(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        agent = agent_context.authorize_command(args, "capability_packs", args.command, declared=True)
        contract = core.load_contract()
        if args.command == "list":
            payload: Any = {
                "singleAuthoritySkill": contract["singleAuthoritySkill"],
                "releaseUnit": contract["releaseUnit"],
                "packs": [
                    {"id": item["id"], "name": item["name"], "purpose": item["purpose"]}
                    for item in contract["packs"]
                ],
            }
        elif args.command == "show":
            payload = core.pack_by_id(args.pack, contract)
        elif args.command == "resolve":
            payload = core.resolve(args.task_type, args.phase, contract)
        elif args.command == "validate":
            errors = core.validate_contract(contract)
            payload = {"status": "PASSED" if not errors else "FAILED", "errors": errors}
        else:  # pragma: no cover
            raise core.CapabilityPackError(f"unsupported command: {args.command}")
        _print(payload, args.json)
        return 0 if not isinstance(payload, dict) or payload.get("status") != "FAILED" else 1
    except (core.CapabilityPackError, agent_context.AgentAuthorizationError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
