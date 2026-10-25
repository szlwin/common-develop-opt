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

import agent_context
import contract_normalization

ROOT = Path(__file__).resolve().parent.parent
ROUTING_PATH = ROOT / "context-routing.json"
HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$")
STABLE_ID_RE = re.compile(r"\s+\{#([A-Za-z][A-Za-z0-9._:-]*)\}\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


class ReferenceReadError(ValueError):
    """Raised when a routed Reference cannot be indexed or read safely."""


@dataclass(frozen=True)
class Section:
    section_id: str
    title: str
    level: int
    start_line: int
    end_line: int
    content: str
    stable: bool = False

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.section_id,
            "title": self.title,
            "level": self.level,
            "startLine": self.start_line,
            "endLine": self.end_line,
            "stable": self.stable,
        }


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
    path = (ROOT / relative).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ReferenceReadError(f"resource {resource_id} escapes the Skill root") from exc
    if not path.is_file():
        raise ReferenceReadError(f"resource {resource_id} does not exist: {relative}")
    result = dict(definition)
    result["id"] = resource_id
    result["resolvedPath"] = path
    return result


def slugify(title: str) -> str:
    value = unicodedata.normalize("NFKC", re.sub(r"[`*_]", "", title)).casefold()
    chars: list[str] = []
    pending_dash = False
    for char in value:
        if char.isalnum():
            if pending_dash and chars:
                chars.append("-")
            chars.append(char)
            pending_dash = False
        else:
            pending_dash = True
    slug = "".join(chars).strip("-")
    return slug or "section"


def parse_sections(path: Path, *, levels: tuple[int, ...] = (2, 3)) -> tuple[str, list[Section]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    document_title = path.stem
    in_fence = False
    headings: list[tuple[int, int, str, str | None]] = []
    for index, line in enumerate(lines, 1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("# ") and document_title == path.stem:
            document_title = line[2:].strip()
        match = HEADING_RE.match(line)
        if not match:
            continue
        level = len(match.group(1))
        if level in levels:
            raw_title = match.group(2).strip()
            stable_match = STABLE_ID_RE.search(raw_title)
            stable_id = stable_match.group(1) if stable_match else None
            title = STABLE_ID_RE.sub("", raw_title).strip() if stable_match else raw_title
            headings.append((index, level, title, stable_id))

    used: dict[str, int] = {}
    sections: list[Section] = []
    for offset, (start_line, level, title, stable_id) in enumerate(headings):
        end_line = len(lines)
        for next_start, next_level, _, _ in headings[offset + 1:]:
            if next_level <= level:
                end_line = next_start - 1
                break
        base = stable_id or slugify(title)
        if stable_id:
            if stable_id in used:
                raise ReferenceReadError(f"duplicate stable section id: {stable_id}")
            used[stable_id] = 1
            section_id = stable_id
        else:
            count = used.get(base, 0) + 1
            used[base] = count
            section_id = base if count == 1 else f"{base}-{count}"
        content = "\n".join(lines[start_line - 1:end_line]).rstrip() + "\n"
        sections.append(Section(section_id, title, level, start_line, end_line, content, stable=bool(stable_id)))
    return document_title, sections


def index_resource(resource_id: str) -> dict[str, Any]:
    definition = resource_definition(resource_id)
    title, sections = parse_sections(definition["resolvedPath"])
    return {
        "schemaVersion": 1,
        "resourceId": resource_id,
        "path": definition["path"],
        "title": title,
        "readStrategy": definition.get("readStrategy"),
        "sections": [section.summary() for section in sections],
    }


def _query_terms(query: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", query).casefold().strip()
    if not normalized:
        return []
    terms = [normalized]
    terms.extend(
        item for item in re.split(r"[\s,，、;；:/]+", normalized)
        if item and item not in terms
    )
    return terms


def select_sections(
    sections: list[Section], *, section_ids: Iterable[str] = (), query: str = "", max_sections: int = 3
) -> list[Section]:
    by_id = {section.section_id: section for section in sections}
    selected: list[Section] = []
    for section_id in section_ids:
        if section_id not in by_id:
            raise ReferenceReadError(f"unknown section id: {section_id}")
        if by_id[section_id] not in selected:
            selected.append(by_id[section_id])
    if selected:
        return selected

    terms = _query_terms(query)
    if not terms:
        return []
    scored: list[tuple[int, int, int, Section]] = []
    for index, section in enumerate(sections):
        title = unicodedata.normalize("NFKC", section.title).casefold()
        body = unicodedata.normalize("NFKC", section.content).casefold()
        score = 0
        for term in terms:
            if term in title:
                score += 10
            elif term in body:
                score += 2
        if score:
            scored.append((score, section.level, -index, section))
    scored.sort(reverse=True, key=lambda item: (item[0], item[1], item[2]))
    chosen: list[Section] = []
    for _, _, _, section in scored:
        overlaps = any(
            not (section.end_line < existing.start_line or section.start_line > existing.end_line)
            for existing in chosen
        )
        if overlaps:
            continue
        chosen.append(section)
        if len(chosen) >= max(1, max_sections):
            break
    return chosen


def read_resource(
    resource_id: str,
    *,
    full: bool = False,
    section_ids: Iterable[str] = (),
    query: str = "",
    max_sections: int = 3,
) -> dict[str, Any]:
    definition = resource_definition(resource_id)
    path: Path = definition["resolvedPath"]
    strategy = str(definition.get("readStrategy") or "")
    title, sections = parse_sections(path)

    if full:
        content = path.read_text(encoding="utf-8")
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

    return {
        "schemaVersion": 1,
        "resourceId": resource_id,
        "path": definition["path"],
        "readStrategy": strategy,
        "selectedSectionIds": selected,
        "content": content,
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
