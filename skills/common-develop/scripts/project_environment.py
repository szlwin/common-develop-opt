#!/usr/bin/env python3
"""Project technology-neutral capability projection from existing environment documents."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import agent_context
import simple_yaml

BLOCK_RE = re.compile(
    r"```(?P<format>json|yaml)\s+common-develop-capabilities\s*\n(?P<body>.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)


class ProjectEnvironmentError(ValueError):
    pass


def _candidate_paths(project_root: Path, version: str = "") -> list[Path]:
    roots = [project_root, project_root / "project_doc"]
    if project_root.name == "project_doc":
        roots = [project_root]
    result: list[Path] = []
    for root in roots:
        result.extend([root / "project_env.md", root / "harness_env.md"])
        if version:
            result.extend([
                root / "version" / version / "project_env.md",
                root / "version" / version / "harness_env.md",
            ])
    seen: set[Path] = set()
    return [p for p in result if not (p in seen or seen.add(p))]


def _load_block(path: Path) -> dict[str, Any] | None:
    text = path.read_text(encoding="utf-8")
    matches = list(BLOCK_RE.finditer(text))
    if not matches:
        return None
    if len(matches) != 1:
        raise ProjectEnvironmentError(f"{path}: expected at most one common-develop-capabilities block")
    match = matches[0]
    body = match.group("body")
    try:
        value = json.loads(body) if match.group("format").lower() == "json" else simple_yaml.loads(body)
    except (json.JSONDecodeError, simple_yaml.YamlError) as exc:
        raise ProjectEnvironmentError(f"{path}: invalid capability block: {exc}") from exc
    if not isinstance(value, dict):
        raise ProjectEnvironmentError(f"{path}: capability block must be a mapping")
    capabilities = value.get("capabilities", value)
    if not isinstance(capabilities, dict):
        raise ProjectEnvironmentError(f"{path}: capabilities must be a mapping")
    return capabilities


def project_capabilities(project_root: Path, *, version: str = "") -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    sources: list[dict[str, Any]] = []
    merged: dict[str, dict[str, Any]] = {}
    conflicts: list[str] = []
    for path in _candidate_paths(project_root, version):
        if not path.is_file():
            continue
        capabilities = _load_block(path)
        sources.append({"path": str(path), "structured": capabilities is not None})
        if capabilities is None:
            continue
        for name, definition in capabilities.items():
            if not isinstance(name, str) or not name.strip() or not isinstance(definition, dict):
                raise ProjectEnvironmentError(f"{path}: each capability must be a named mapping")
            normalized = dict(definition)
            normalized.setdefault("available", True)
            normalized["source"] = str(path)
            prior = merged.get(name)
            if prior is not None:
                comparable_prior = {k: v for k, v in prior.items() if k != "source"}
                comparable_new = {k: v for k, v in normalized.items() if k != "source"}
                if comparable_prior != comparable_new:
                    conflicts.append(name)
                    continue
            merged[name] = normalized
    available = sorted(name for name, item in merged.items() if item.get("available") is True)
    unavailable = sorted(name for name, item in merged.items() if item.get("available") is not True)
    return {
        "status": "BLOCKED" if conflicts else "PASSED",
        "projectRoot": str(project_root),
        "version": version,
        "sources": sources,
        "capabilities": merged,
        "availableCapabilities": available,
        "unavailableCapabilities": unavailable,
        "conflicts": sorted(set(conflicts)),
        "structuredCapabilitySourcePresent": any(item["structured"] for item in sources),
        "machineSource": "project_env.md / harness_env.md common-develop-capabilities block",
    }


def environment_health(project_root: Path, *, version: str = "") -> dict[str, Any]:
    projection = project_capabilities(project_root, version=version)
    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    checks.append({
        "id": "capability-source",
        "status": "PASSED" if projection.get("structuredCapabilitySourcePresent") else "NOT_DECLARED",
        "detail": "structured common-develop-capabilities block present" if projection.get("structuredCapabilitySourcePresent") else "no structured capability block declared",
    })
    conflicts = list(projection.get("conflicts") or [])
    checks.append({"id": "capability-conflicts", "status": "FAILED" if conflicts else "PASSED", "items": conflicts})
    errors.extend(f"conflicting capability declaration: {name}" for name in conflicts)
    for name, definition in (projection.get("capabilities") or {}).items():
        if not isinstance(definition, dict):
            errors.append(f"capability {name} definition must be an object")
            continue
        available = definition.get("available")
        if not isinstance(available, bool):
            errors.append(f"capability {name}.available must be boolean")
        for command_key in ("command", "adapterCommand"):
            if command_key in definition and (not isinstance(definition.get(command_key), str) or not str(definition.get(command_key)).strip()):
                errors.append(f"capability {name}.{command_key} must be a non-empty string when declared")
    checks.append({
        "id": "capability-shape",
        "status": "FAILED" if any(item.startswith("capability ") for item in errors) else "PASSED",
    })
    if not projection.get("structuredCapabilitySourcePresent"):
        warnings.append("no structured capability source is declared; commands may still be read manually from project environment docs")
    return {
        "status": "FAILED" if errors else "PASSED",
        "projection": projection,
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    status = commands.add_parser("status", help="project environment capabilities without technology assumptions")
    agent_context.add_agent_argument(status)
    status.add_argument("--project-root", required=True)
    status.add_argument("--version", default="")
    status.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        agent = agent_context.authorize_command(args, "project_environment", "status", declared=True)
        payload = project_capabilities(Path(args.project_root), version=args.version)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"ENV {payload['status']} root={payload['projectRoot']}")
            for name in payload["availableCapabilities"]:
                print(f"AVAILABLE {name}")
            for name in payload["unavailableCapabilities"]:
                print(f"UNAVAILABLE {name}")
            for name in payload["conflicts"]:
                print(f"CONFLICT {name}")
        return 2 if payload["status"] == "BLOCKED" else 0
    except (ProjectEnvironmentError, agent_context.AgentAuthorizationError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
