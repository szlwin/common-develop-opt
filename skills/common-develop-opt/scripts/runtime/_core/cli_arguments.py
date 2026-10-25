"""Shared, explicit argparse building blocks for domain CLIs.

The helpers intentionally only register arguments.  Domain commands and their
subparsers remain visible in each entry point so static command discovery and
permission checks can inspect the CLI without executing it.
"""
from __future__ import annotations

import argparse
from collections.abc import Iterable

from document_core import agent_context


def build_parser(description: str | None = None) -> argparse.ArgumentParser:
    """Build the common root parser and required subcommand collection."""
    return argparse.ArgumentParser(description=description)


def add_agent_argument(parser: argparse.ArgumentParser) -> None:
    """Register the canonical Agent selector."""
    agent_context.add_agent_argument(parser)


def add_json_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true")


def add_doc_root_argument(
    parser: argparse.ArgumentParser,
    *,
    required: bool = False,
    default: str = "project_doc",
) -> None:
    kwargs: dict[str, object] = {"required": required}
    if not required:
        kwargs["default"] = default
    parser.add_argument("--doc-root", **kwargs)


def add_file_argument(
    parser: argparse.ArgumentParser,
    *,
    required: bool = True,
    help: str | None = None,
) -> None:
    kwargs: dict[str, object] = {"required": required}
    if help is not None:
        kwargs["help"] = help
    parser.add_argument("--file", **kwargs)


def add_task_dir_argument(
    parser: argparse.ArgumentParser,
    *,
    required: bool = True,
    help: str | None = None,
) -> None:
    kwargs: dict[str, object] = {"required": required}
    if help is not None:
        kwargs["help"] = help
    parser.add_argument("--task-dir", **kwargs)


def add_repeatable_argument(
    parser: argparse.ArgumentParser,
    name: str,
    *,
    required: bool = False,
    default: Iterable[str] | None = None,
    help: str | None = None,
) -> None:
    kwargs: dict[str, object] = {"action": "append", "required": required}
    if default is not None:
        kwargs["default"] = list(default)
    if help is not None:
        kwargs["help"] = help
    parser.add_argument(name, **kwargs)


def set_handler(parser: argparse.ArgumentParser, handler: object) -> None:
    """Set the canonical callback and a temporary ``func`` alias.

    ``handler`` is the public spelling for the three domain CLIs.  ``func`` is
    retained on the Namespace because long_task and a few integrations use it
    as their callback spelling; both point to the same callable.
    """
    parser.set_defaults(handler=handler, func=handler)
