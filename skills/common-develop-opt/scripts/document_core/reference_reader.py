#!/usr/bin/env python3
"""Index and selectively read routed common-develop Markdown References."""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

# Transitional support modules remain in compat while this read-only capability
# is migrated; keep the dependency explicit and local to this implementation.
_COMPAT_ROOT = Path(__file__).resolve().parent.parent / "compat"
if str(_COMPAT_ROOT) not in sys.path:
    sys.path.insert(0, str(_COMPAT_ROOT))

from document_core import agent_context
from runtime import contract_normalization
from resource_paths import formalize, formal_relative
from reference_policy import definitions, load_catalog

_SCRIPT_ROOT = Path(__file__).resolve().parent
if str(_SCRIPT_ROOT.parent) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT.parent))
if str(_SCRIPT_ROOT.parent / "compat") not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT.parent / "compat"))
ROOT = Path(__file__).resolve().parents[2]
ROUTING_PATH = ROOT / "context-routing.json"
from document_core.sections import (ReferenceReadError, Section, slugify, parse_sections,
    select_sections, _query_terms, HEADING_RE, STABLE_ID_RE, FENCE_RE)
from document_core import sections as _sections


def resource_sections(path: Path):
    return _sections.resource_sections(path, root=ROOT)


def load_routing(path: Path = ROUTING_PATH) -> dict[str, Any]:
    try:
        return contract_normalization.load_context_routing(path)
    except contract_normalization.ContractNormalizationError as exc:
        raise ReferenceReadError(str(exc)) from exc


def resource_definition(resource_id: str, routing: dict[str, Any] | None = None) -> dict[str, Any]:
    contract = load_routing() if routing is None else routing
    catalog = contract.get("resourceCatalog")
    if not isinstance(catalog, dict):
        raise ReferenceReadError("context routing resourceCatalog must be an object")
    definition = catalog.get(resource_id)
    if not isinstance(definition, dict):
        raise ReferenceReadError(f"unknown routed resource: {resource_id}")
    relative = definition.get("path")
    if not isinstance(relative, str) or not relative:
        raise ReferenceReadError(f"resource {resource_id} has no path")
    path = formalize(ROOT / relative).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ReferenceReadError(f"resource {resource_id} escapes the Skill root") from exc
    if not path.is_file():
        raise ReferenceReadError(f"resource {resource_id} does not exist: {relative}")
    result = dict(definition)
    result["id"] = resource_id
    result["resolvedPath"] = path
    result["formalPath"] = formal_relative(path)
    return result








def index_resource(resource_id: str) -> dict[str, Any]:
    definition = resource_definition(resource_id)
    title, sections, _ = resource_sections(definition["resolvedPath"])
    return {
        "schemaVersion": 1,
        "resourceId": resource_id,
        "path": definition["path"],
        "title": title,
        "readStrategy": definition.get("readStrategy"),
        "sections": [section.summary() for section in sections],
    }






def read_resource(
    resource_id: str,
    *,
    full: bool = False,
    section_ids: Iterable[str] = (),
    query: str = "",
    max_sections: int = 3,
    entry: bool = False,
    max_bytes: int | None = None,
) -> dict[str, Any]:
    definition = resource_definition(resource_id)
    path: Path = definition["resolvedPath"]
    strategy = str(definition.get("readStrategy") or "")
    if entry and (full or section_ids or query):
        raise ReferenceReadError('--entry cannot be combined with --full, --section or --query')
    if entry:
        title, sections = parse_sections(path)
        paths = [path]
    else:
        title, sections, paths = resource_sections(path)

    if full or entry:
        content = '\n'.join(source.read_text(encoding='utf-8') for source in paths)
        selected = [section.section_id for section in sections]
    else:
        if not section_ids and not query:
            alias_sections = definition.get("sectionIds")
            if isinstance(alias_sections, list):
                section_ids = [item for item in alias_sections if isinstance(item, str)]
        chosen = select_sections(
            sections, section_ids=section_ids, query=query, max_sections=max_sections
        )
        if not chosen:
            if strategy == "full":
                content = path.read_text(encoding="utf-8")
                selected = [section.section_id for section in sections]
            else:
                raise ReferenceReadError(
                    f"resource {resource_id} uses relevant_sections; provide --section, --query, or explicit --full"
                )
        else:
            prefix = f"# {title}\n\n<!-- selected-resource: {resource_id}; source: {definition['path']} -->\n\n"
            content = prefix + "\n".join(section.content.rstrip() for section in chosen) + "\n"
            selected = [section.section_id for section in chosen]

    policies = definitions(load_catalog(ROOT))
    ceiling = max(policies[source.relative_to(ROOT).as_posix()]['maxReadBytes'] for source in paths)
    if max_bytes is not None and (type(max_bytes) is not int or max_bytes <= 0):
        raise ReferenceReadError('--max-bytes must be positive')
    limit = min(ceiling, max_bytes) if max_bytes is not None else ceiling
    content_bytes = len(content.encode('utf-8'))
    if content_bytes > limit:
        raise ReferenceReadError(
            f'REFERENCE_READ_BUDGET_EXCEEDED: {resource_id}: {content_bytes} > {limit}; select fewer sections'
        )
    return {
        "schemaVersion": 1,
        "resourceId": resource_id,
        "path": definition["path"],
        "readStrategy": strategy,
        "selectedSectionIds": selected,
        "content": content,
        "contentBytes": content_bytes,
        "maxReadBytes": limit,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    index = commands.add_parser("index", help="list stable level-2/3 section IDs for one routed Reference")
    agent_context.add_agent_argument(index)
    index.add_argument("--resource", required=True)
    index.add_argument("--json", action="store_true")

    read = commands.add_parser("read", help="read selected sections from one routed Reference")
    agent_context.add_agent_argument(read)
    read.add_argument("--resource", required=True)
    read.add_argument("--section", action="append", default=[])
    read.add_argument("--query", default="")
    read.add_argument("--max-sections", type=int, default=3)
    read.add_argument("--full", action="store_true")
    read.add_argument("--entry", action="store_true", help="read only the physical quick/core entry")
    read.add_argument("--max-bytes", type=int, help="lower the catalog UTF-8 content byte limit")
    read.add_argument("--json", action="store_true")
    return parser


def command_index(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "reference_reader", "index", declared=True)
    result = index_resource(args.resource)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"RESOURCE {result['resourceId']} {result['path']} strategy={result['readStrategy']}")
        for section in result["sections"]:
            print(f"SECTION {section['id']} lines={section['startLine']}-{section['endLine']} {section['title']}")
    return 0


def command_read(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "reference_reader", "read", declared=True)
    result = read_resource(
        args.resource,
        full=args.full,
        section_ids=args.section,
        query=args.query,
        max_sections=args.max_sections,
        entry=args.entry,
        max_bytes=args.max_bytes,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["content"], end="")
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "index":
            return command_index(args)
        if args.command == "read":
            return command_read(args)
        return 2
    except (agent_context.AgentAuthorizationError, ReferenceReadError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
