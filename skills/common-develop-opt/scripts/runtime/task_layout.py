#!/usr/bin/env python3
"""Manage the current-manifest projection for a task."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable

from document_core import agent_context
from runtime import current_manifest
from document_core import storage


def _task_dir(args: argparse.Namespace) -> Path:
    if args.task_dir:
        return Path(args.task_dir).resolve()
    if not args.doc_root or not args.version or not args.target:
        raise ValueError("provide --task-dir or --doc-root/--version/--target")
    return (Path(args.doc_root) / "version" / args.version / "task" / args.target).resolve()


def sync(task_dir: Path) -> dict:
    storage.assert_new_project(task_dir)
    result = current_manifest.sync_manifest(task_dir)
    cache = Path(task_dir) / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / ".gitignore").write_text("*\n!.gitignore\n", encoding="utf-8")
    return result


def validate(task_dir: Path) -> list[str]:
    errors: list[str] = []
    try:
        storage.assert_new_project(task_dir)
    except storage.StorageError as exc:
        errors.append(str(exc))
        return errors
    manifest = current_manifest.read_manifest(task_dir)
    if not isinstance(manifest, dict):
        errors.append(f"missing current manifest: {storage.path(task_dir, 'manifest')}")
    elif not current_manifest.manifest_fresh(task_dir, manifest):
        errors.append("current_manifest.json is stale; run task_layout.py sync")
    return errors


def build_projections(task_dir: Path, *, use_cache: bool = True) -> dict:
    """Build the canonical manifest projection."""
    return sync(Path(task_dir))


def validate_projections(task_dir: Path) -> list[str]:
    return validate(Path(task_dir))


def read_agent_view(task_dir: Path, agent: str) -> dict:
    manifest = current_manifest.read_manifest(Path(task_dir)) or {}
    return {"agent": agent, "manifest": manifest, "authority": "DERIVED"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    agent_context.add_agent_argument(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("sync", "validate", "view"):
        command = commands.add_parser(name)
        command.add_argument("--task-dir", default="")
        command.add_argument("--doc-root", default="")
        command.add_argument("--version", default="")
        command.add_argument("--target", default="")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    agent = agent_context.authorize_command(args, "task_layout", args.command, declared=True)
    task_dir = _task_dir(args)
    if args.command == "sync":
        print(json.dumps(sync(task_dir), ensure_ascii=False, indent=2))
        return 0
    if args.command == "validate":
        errors = validate(task_dir)
        print(json.dumps({"status": "PASSED" if not errors else "FAILED", "errors": errors}, ensure_ascii=False, indent=2))
        return 0 if not errors else 1
    value = current_manifest.read_manifest(task_dir)
    print(json.dumps(value or {}, ensure_ascii=False, indent=2))
    return 0 if value else 1


if __name__ == "__main__":
    raise SystemExit(main())
