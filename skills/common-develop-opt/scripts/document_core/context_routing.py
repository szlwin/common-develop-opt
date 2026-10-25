#!/usr/bin/env python3
"""Query focused context-routing slices and resolved task routes."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Iterable

_SCRIPT_ROOT = Path(__file__).resolve().parents[1]
if str(_SCRIPT_ROOT / "compat") not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT / "compat"))

from document_core import agent_context
from runtime import context_resolver
from runtime.contract_query_utils import (
    ContractQueryError,
    bounded_selection,
    print_payload,
    section_summary,
    select_pointer,
)


def load_contract() -> dict[str, Any]:
    value = context_resolver.load_routing()
    if value.get("schemaVersion") != 2:
        raise ContractQueryError("context routing schemaVersion must be 2")
    return value


def _route_payload(contract: dict[str, Any], task_type: str) -> dict[str, Any]:
    route = next(
        (item for item in contract.get("routes") or [] if isinstance(item, dict) and task_type in set(item.get("taskTypes") or [])),
        None,
    )
    if route is None:
        raise ContractQueryError(f"unknown task type: {task_type}")
    catalog = contract.get("resourceCatalog") or {}
    fixed = list(contract.get("fixedResources") or [])
    resources = fixed + [item for item in route.get("resources") or [] if item not in fixed]
    definitions = []
    for resource_id in resources:
        definition = catalog.get(resource_id)
        if not isinstance(definition, dict):
            raise ContractQueryError(f"route references unknown resource: {resource_id}")
        definitions.append({
            "id": resource_id,
            "path": definition.get("path"),
            "tokenBudget": definition.get("tokenBudget"),
            "readStrategy": definition.get("readStrategy"),
            "fixed": resource_id in fixed,
        })
    return {
        "taskType": task_type,
        "routeId": route.get("id"),
        "taskTypes": route.get("taskTypes"),
        "resources": definitions,
        "baseRouteTokenBudget": sum(int(item.get("tokenBudget") or 0) for item in definitions),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    list_parser = commands.add_parser("list", help="list compact routes, resources, or conditions")
    agent_context.add_agent_argument(list_parser)
    list_parser.add_argument("--kind", choices=["sections", "routes", "resources", "conditions"], default="sections")
    list_parser.add_argument("--json", action="store_true")

    show = commands.add_parser("show", help="show one bounded JSON Pointer selection")
    agent_context.add_agent_argument(show)
    show.add_argument("--pointer", required=True)
    show.add_argument("--max-bytes", type=int, default=24_576)
    show.add_argument("--json", action="store_true")

    route = commands.add_parser("route", help="show one exact task route and base resources")
    agent_context.add_agent_argument(route)
    route.add_argument("--task-type", required=True)
    route.add_argument("--json", action="store_true")

    condition = commands.add_parser("condition", help="show one prompt condition")
    agent_context.add_agent_argument(condition)
    condition.add_argument("--id", required=True)
    condition.add_argument("--json", action="store_true")

    resource = commands.add_parser("resource", help="show one resource catalog entry")
    agent_context.add_agent_argument(resource)
    resource.add_argument("--id", required=True)
    resource.add_argument("--json", action="store_true")

    resolve = commands.add_parser("resolve", help="resolve the current minimal resource set")
    agent_context.add_agent_argument(resolve)
    resolve.add_argument("--task-type", required=True)
    resolve.add_argument("--prompt", default="")
    resolve.add_argument("--doc-root", default="")
    resolve.add_argument("--version", default="")
    resolve.add_argument("--json", action="store_true")
    return parser


def _authorize(args: argparse.Namespace) -> None:
    agent_context.authorize_command(args, "context_routing", args.command, declared=True)


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        _authorize(args)
        contract = load_contract()
        if args.command == "list":
            if args.kind == "sections":
                payload: Any = {"sections": section_summary(contract)}
            elif args.kind == "routes":
                payload = {"routes": [
                    {"id": item.get("id"), "taskTypes": item.get("taskTypes"), "resourceIds": item.get("resources")}
                    for item in contract.get("routes") or [] if isinstance(item, dict)
                ]}
            elif args.kind == "resources":
                payload = {"resources": [
                    {"id": key, "path": value.get("path"), "tokenBudget": value.get("tokenBudget"), "readStrategy": value.get("readStrategy")}
                    for key, value in sorted((contract.get("resourceCatalog") or {}).items()) if isinstance(value, dict)
                ]}
            else:
                payload = {"conditions": [
                    {"id": item.get("id"), "priority": item.get("priority"), "resourceIds": item.get("resources")}
                    for item in contract.get("promptConditions") or [] if isinstance(item, dict)
                ]}
        elif args.command == "show":
            payload = {"pointer": args.pointer, "value": bounded_selection(select_pointer(contract, args.pointer), max_bytes=args.max_bytes)}
        elif args.command == "route":
            payload = _route_payload(contract, args.task_type)
        elif args.command == "condition":
            payload = next((item for item in contract.get("promptConditions") or [] if isinstance(item, dict) and item.get("id") == args.id), None)
            if payload is None:
                raise ContractQueryError(f"unknown prompt condition: {args.id}")
        elif args.command == "resource":
            payload = (contract.get("resourceCatalog") or {}).get(args.id)
            if not isinstance(payload, dict):
                raise ContractQueryError(f"unknown resource: {args.id}")
            payload = {"id": args.id, **payload}
        elif args.command == "resolve":
            payload = context_resolver.resolve_context(
                args.task_type,
                prompt=args.prompt,
                mode=context_resolver.resolve_mode(args.doc_root, args.version),
                routing=contract,
            )
        else:
            raise ContractQueryError(f"unsupported command: {args.command}")
        print_payload(payload, as_json=args.json)
        return 0
    except (ContractQueryError, context_resolver.ContextResolutionError, agent_context.AgentAuthorizationError, RuntimeError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
