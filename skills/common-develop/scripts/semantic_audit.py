#!/usr/bin/env python3
"""Audit common-develop vocabulary, ID namespaces, and duplicate headings."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import agent_context
import contract_normalization

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = ROOT / "assets" / "semantic-contract.json"
TEXT_SUFFIXES = {".md", ".json", ".yaml", ".yml", ".py"}


class SemanticAuditError(ValueError):
    """Raised when the semantic contract or package is invalid."""


def load_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("kind") != "SEMANTIC_AND_NAMING_CONTRACT":
        raise SemanticAuditError("semantic contract kind is invalid")
    return data


def iter_scan_files(contract: dict[str, Any], root: Path = ROOT) -> list[Path]:
    exclusions = {str(item) for item in contract.get("scanExclusions", [])}
    result: list[Path] = []
    for raw in contract.get("scanRoots", []):
        candidate = root / str(raw)
        if candidate.is_file():
            paths = [candidate]
        elif candidate.is_dir():
            paths = [item for item in candidate.rglob("*") if item.is_file()]
        else:
            raise SemanticAuditError(f"scan root does not exist: {raw}")
        for path in paths:
            rel = path.relative_to(root).as_posix()
            if rel in exclusions or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            result.append(path)
    return sorted(set(result))


def duplicate_headings(path: Path, text: str) -> list[str]:
    headings: dict[tuple[int, str], list[int]] = defaultdict(list)
    in_fence = False
    for line_no, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if match:
            key = (len(match.group(1)), match.group(2).strip())
            headings[key].append(line_no)
    return [
        f"{path.relative_to(ROOT)}:{','.join(map(str, lines))}: duplicate heading H{level} {title!r}"
        for (level, title), lines in headings.items()
        if len(lines) > 1
    ]


def audit(root: Path = ROOT) -> dict[str, Any]:
    contract = load_contract(root / "assets" / "semantic-contract.json")
    errors: list[str] = []
    warnings: list[str] = []
    scanned: list[str] = []
    for path in iter_scan_files(contract, root):
        rel = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8")
        scanned.append(rel)
        if path.suffix.lower() == ".md":
            errors.extend(duplicate_headings(path, text))

    dependency_schema = json.loads(
        (root / "assets" / "structured-docs" / "dependency-impact.schema.json").read_text(encoding="utf-8")
    )
    top_properties = dependency_schema.get("properties", {})
    if "crossModuleImplementations" not in top_properties:
        errors.append("dependency-impact.schema.json missing crossModuleImplementations")
    if "crossModuleFlows" in top_properties:
        errors.append("dependency-impact.schema.json retains crossModuleFlows")

    business_schema = json.loads(
        (root / "assets" / "structured-docs" / "business-flow.schema.json").read_text(encoding="utf-8")
    )
    flow_pattern = business_schema.get("$defs", {}).get("flow", {}).get("properties", {}).get("id", {}).get("pattern")
    step_pattern = business_schema.get("$defs", {}).get("step", {}).get("properties", {}).get("id", {}).get("pattern")
    if flow_pattern != "^FLOW-[A-Z0-9_-]+$" or step_pattern != "^STEP-[A-Z0-9_-]+$":
        errors.append("business-flow schema must reserve FLOW/STEP for business semantics")

    runtime_contract = contract_normalization.load_runtime_contract(
        root / "assets" / "runtime-contract.json"
    )
    expected_execution_policy = {
        "mode": "SEQUENTIAL",
        "allowParallelTasks": False,
        "oneActiveTargetAtATime": True,
        "multiTargetOrder": "DECLARED_ORDER",
        "multiTargetOrderSource": "version/{VERSION}/project_process.md",
        "description": (
            "All targets and plan tasks execute sequentially. A, B, and C may be grouped by lifecycle stage, "
            "but only one target or task may be active at a time in the declared order."
        ),
    }
    if runtime_contract.get("executionPolicy") != expected_execution_policy:
        errors.append("runtime-contract executionPolicy is not canonical sequential-only")

    record_contract = json.loads(
        (root / "assets" / "long-task" / "record-contract.json").read_text(encoding="utf-8")
    )
    record_text = json.dumps(record_contract, ensure_ascii=False)
    for required in (
        "business_flow_required",
        "business_flow_refs",
        "cross_module_implementation_required",
        "cross_module_implementation_refs",
    ):
        if required not in record_text:
            errors.append(f"record-contract missing {required}")
    checkpoint_schema = json.loads(
        (root / "assets" / "git-checkpoint.schema.json").read_text(encoding="utf-8")
    )
    checkpoint_item = checkpoint_schema.get("properties", {}).get("checkpoints", {}).get("items", {})
    checkpoint_fields = checkpoint_item.get("properties", {})
    if checkpoint_schema.get("properties", {}).get("schema_version") != {"const": 2}:
        errors.append("git-checkpoint schema_version must be 2")
    for redundant in ("decision", "commit_locator"):
        if redundant in checkpoint_fields:
            errors.append(f"git-checkpoint schema retains redundant field {redundant}")

    return {
        "status": "PASSED" if not errors else "FAILED",
        "packageVersion": contract.get("packageVersion"),
        "scannedFiles": len(scanned),
        "errors": errors,
        "warnings": warnings,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    agent_context.add_agent_argument(result)
    result.add_argument("--json", action="store_true", help="print machine-readable result")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        agent = agent_context.authorize_command(args, "semantic_audit", "check")
        result = audit()
        result["currentAgent"] = agent
    except (agent_context.AgentAuthorizationError, OSError, json.JSONDecodeError, SemanticAuditError) as exc:
        result = {"status": "FAILED", "errors": [str(exc)], "warnings": []}
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"SEMANTIC_AUDIT {result['status']}")
        for item in result.get("errors", []):
            print(f"ERROR {item}")
        for item in result.get("warnings", []):
            print(f"WARNING {item}")
    return 0 if result.get("status") == "PASSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
