#!/usr/bin/env python3
"""Validate the canonical common-develop documentation layout."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import agent_context

FORBIDDEN_VERSION_DOC_NAMES = ("docs", "document", "documents")
EXPECTED_VERSION_PATHS = ("version/{VERSION}/doc", "docs")


class DocumentLayoutError(ValueError):
    """Raised when DOC_ROOT or a document path violates the canonical layout."""


def _resolved(path: str | os.PathLike[str] | Path) -> Path:
    return Path(path).expanduser().resolve()


def _looks_like_version_directory(path: Path) -> bool:
    if path.parent.name == "version":
        return True
    if (path / "task").exists() and (path / "doc").exists() and path.parent.name == "version":
        return True
    return False


def version_root(doc_root: Path, version: str) -> Path:
    return doc_root / "version" / version


def layout_findings(doc_root: str | os.PathLike[str] | Path, version: str = "") -> list[dict[str, Any]]:
    root = _resolved(doc_root)
    findings: list[dict[str, Any]] = []
    if _looks_like_version_directory(root):
        findings.append({
            "code": "DOC_ROOT_POINTS_TO_VERSION_DIRECTORY",
            "path": str(root),
            "expected": [str(root.parent.parent), f"--version {root.name}"],
            "message": "DOC_ROOT points to a version directory.",
        })
        return findings
    versions_root = root / "version"
    version_roots: list[Path] = []
    if version:
        version_roots.append(versions_root / version)
    elif versions_root.is_dir():
        version_roots.extend(path for path in sorted(versions_root.iterdir()) if path.is_dir())
    for current in version_roots:
        for name in FORBIDDEN_VERSION_DOC_NAMES:
            candidate = current / name
            if candidate.exists():
                findings.append({
                    "code": "INVALID_VERSION_DOCS_DIRECTORY" if name == "docs" else "INVALID_VERSION_DOCUMENT_DIRECTORY",
                    "path": candidate.relative_to(root).as_posix(),
                    "expected": [
                        (current / "doc").relative_to(root).as_posix(),
                        "docs",
                    ],
                    "message": f"version-level directory '{name}' is forbidden",
                })
    return findings


def validate_doc_root(
    doc_root: str | os.PathLike[str] | Path,
    version: str = "",
) -> Path:
    root = _resolved(doc_root)
    if _looks_like_version_directory(root):
        raise DocumentLayoutError(
            "DOC_ROOT points to a version directory.\n"
            f"Use --doc-root {root.parent.parent} --version {version or root.name}."
        )
    if root.exists() and not root.is_dir():
        raise DocumentLayoutError(f"DOC_ROOT must be a directory: {root}")
    for child_name in ("docs", "version"):
        child = root / child_name
        if child.exists() and not child.is_dir():
            raise DocumentLayoutError(f"DOC_ROOT/{child_name} must be a directory: {child}")
    findings = layout_findings(root, version)
    if findings:
        first = findings[0]
        raise DocumentLayoutError(
            f"{first['code']}: {first['path']}; expected={first['expected']}"
        )
    return root


def doc_root_from_task_dir(task_dir: str | os.PathLike[str] | Path) -> tuple[Path, str]:
    task = _resolved(task_dir)
    try:
        version_root_path = task.parent.parent
        if task.parent.name != "task" or version_root_path.parent.name != "version":
            raise ValueError
        return version_root_path.parent.parent, version_root_path.name
    except (IndexError, ValueError) as exc:
        raise DocumentLayoutError(
            "task directory must match DOC_ROOT/version/{VERSION}/task/{TARGET_ID}: " + str(task)
        ) from exc


def validate_task_dir(task_dir: str | os.PathLike[str] | Path) -> tuple[Path, str]:
    root, version = doc_root_from_task_dir(task_dir)
    validate_doc_root(root, version)
    return root, version


def infer_doc_root(path: str | os.PathLike[str] | Path) -> tuple[Path, str] | None:
    current = _resolved(path)
    if current.is_file() or current.suffix:
        current = current.parent
    lineage = [current, *current.parents]
    for candidate in lineage:
        if candidate.name == "version" and candidate.parent:
            try:
                relative = _resolved(path).relative_to(candidate)
            except ValueError:
                continue
            version = relative.parts[0] if relative.parts else ""
            return candidate.parent, version
    return None


def validate_cli_namespace(args: argparse.Namespace) -> None:
    doc_root = str(getattr(args, "doc_root", "") or "")
    version = str(getattr(args, "version", "") or "")
    if doc_root:
        validate_doc_root(doc_root, version)
        return
    task_dir = str(getattr(args, "task_dir", "") or "")
    if task_dir:
        validate_task_dir(task_dir)
        return
    file_path = str(getattr(args, "file", "") or "")
    if file_path:
        inferred = infer_doc_root(file_path)
        if inferred:
            validate_doc_root(inferred[0], inferred[1])


def forbidden_task_plan_path(value: str, version: str = "") -> dict[str, Any] | None:
    normalized = str(value or "").replace("\\", "/").strip()
    normalized = re.sub(r"^\./", "", normalized)
    pattern = re.compile(r"(?:^|/)version/([^/]+)/(docs|document|documents)(?:/|$)")
    match = pattern.search(normalized)
    if not match:
        return None
    found_version, directory = match.groups()
    if version and found_version != version:
        return None
    return {
        "code": "INVALID_TASK_PLAN_DOCUMENT_PATH",
        "path": normalized,
        "version": found_version,
        "directory": directory,
        "expected": [f"version/{found_version}/doc/**", "docs/**", f"version/{found_version}/task/**"],
    }


def validate_task_plan_paths(task: dict[str, Any], prefix: str, errors: list[str], version: str = "") -> None:
    for value in task.get("allowed_files") or []:
        finding = forbidden_task_plan_path(str(value), version)
        if finding:
            errors.append(
                f"{prefix} allowed_files contains forbidden version document path: {finding['path']}; "
                f"expected={finding['expected']}"
            )


def task_layout_errors(task_dir: str | os.PathLike[str] | Path) -> tuple[list[str], list[dict[str, Any]]]:
    try:
        root, version = doc_root_from_task_dir(task_dir)
    except DocumentLayoutError as exc:
        return [str(exc)], [{"code": "INVALID_TASK_DIRECTORY", "path": str(task_dir), "expected": []}]
    findings = layout_findings(root, version)
    errors = [
        f"{item['code']}: {item['path']}; expected={item['expected']}"
        for item in findings
    ]
    return errors, findings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="validate DOC_ROOT and report structured layout findings")
    agent_context.add_agent_argument(check)
    check.add_argument("--doc-root", required=True)
    check.add_argument("--version", default="")
    check.add_argument("--json", action="store_true")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        agent = agent_context.selected_agent(args)
        if args.command == "check":
            agent_context.authorize_declared(agent, "doc_layout", "check")
            findings = layout_findings(args.doc_root, args.version)
            payload = {"status": "FAILED" if findings else "PASSED", "findings": findings}
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print(payload["status"])
                for item in findings:
                    print(f"{item['code']} {item['path']}")
            return 1 if findings else 0
    except (OSError, DocumentLayoutError, agent_context.AgentAuthorizationError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
