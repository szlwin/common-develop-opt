#!/usr/bin/env python3
"""Unified, transactional entry point for common-develop request work."""
from __future__ import annotations

import argparse
import json
import sys
from types import ModuleType
from typing import Any, Iterable

from runtime._core import wk_cli
from runtime._core import wk_commands as _commands

# Compatibility imports used by the stable error boundary.
from document_core import agent_context
from runtime import request_intake
from runtime import work_mode
from runtime.error_catalog import SkillError, emit_cli_error

# Preserve the historical module-level API while keeping command implementation
# out of the public facade. The canonical document_core.wk_dispatcher owns
# --apply, --preview and --json; _core/wk_cli only binds callbacks. A few
# integrations patch these names, so callbacks
# and helper aliases remain available and are synchronized before dispatch.
_COMMAND_NAMES = (
    "command_start", "command_status", "command_doctor", "command_impact",
    "command_reconcile", "command_close", "command_document_archive",
    "command_begin", "command_progress", "command_finish",
    "command_record_result",
    "command_delivery", "command_env", "command_next", "command_step",
    "command_confirm", "command_context", "command_help", "command_packs",
)
_COMPAT_EXPORTS = _COMMAND_NAMES + ("emit_result", "WKError", "COMMAND_HELP_METADATA", "CATEGORY_ORDER")
for _name in _COMPAT_EXPORTS:
    if hasattr(_commands, _name):
        globals()[_name] = getattr(_commands, _name)
_ORIGINAL_ALIASES = {
    name: getattr(_commands, name)
    for name in _COMPAT_EXPORTS
    if hasattr(_commands, name)
}


def _sync_patched_facade_globals() -> None:
    """Propagate compatibility patches made against ``scripts.wk``."""
    for name, original in _ORIGINAL_ALIASES.items():
        if name in _COMMAND_NAMES or name in {"build_parser", "main", "_normalize_help_argv"}:
            continue
        current = globals().get(name, original)
        # Always refresh, including restoration after a unittest/mock patch;
        # otherwise a temporary facade patch would leak into the implementation.
        setattr(_commands, name, current)


def _dispatch(command_name: str, args: argparse.Namespace) -> int:
    _sync_patched_facade_globals()
    return int(getattr(_commands, command_name)(args))


# Explicit wrappers keep argparse callback identity and existing callers stable.
def command_start(args: argparse.Namespace) -> int: return _dispatch("command_start", args)
def command_status(args: argparse.Namespace) -> int: return _dispatch("command_status", args)
def command_begin(args: argparse.Namespace) -> int: return _dispatch("command_begin", args)
def command_progress(args: argparse.Namespace) -> int: return _dispatch("command_progress", args)
def command_finish(args: argparse.Namespace) -> int: return _dispatch("command_finish", args)
def command_record_result(args: argparse.Namespace) -> int: return _dispatch("command_record_result", args)
def command_doctor(args: argparse.Namespace) -> int: return _dispatch("command_doctor", args)
def command_impact(args: argparse.Namespace) -> int: return _dispatch("command_impact", args)
def command_reconcile(args: argparse.Namespace) -> int: return _dispatch("command_reconcile", args)
def command_close(args: argparse.Namespace) -> int: return _dispatch("command_close", args)
def command_document_archive(args: argparse.Namespace) -> int: return _dispatch("command_document_archive", args)
def command_delivery(args: argparse.Namespace) -> int: return _dispatch("command_delivery", args)
def command_env(args: argparse.Namespace) -> int: return _dispatch("command_env", args)
def command_next(args: argparse.Namespace) -> int: return _dispatch("command_next", args)
def command_step(args: argparse.Namespace) -> int: return _dispatch("command_step", args)
def command_confirm(args: argparse.Namespace) -> int: return _dispatch("command_confirm", args)
def command_context(args: argparse.Namespace) -> int: return _dispatch("command_context", args)
def command_help(args: argparse.Namespace) -> int: return _dispatch("command_help", args)
def command_packs(args: argparse.Namespace) -> int: return _dispatch("command_packs", args)


def _normalize_help_argv(argv: list[str]) -> list[str]:
    """Normalize symbolic wk flags before argparse parses subcommands."""
    if argv and argv[0] == "-wd":
        return ["document-archive", "-wd", *argv[1:]]
    if not argv or argv[0] != "help":
        return argv
    result = list(argv)
    for index, token in enumerate(result[:-1]):
        if token == "--flag" and result[index + 1].startswith("-"):
            result[index] = f"--flag={result[index + 1]}"
            del result[index + 1]
            break
    return result


def build_parser() -> argparse.ArgumentParser:
    """Build the public wk parser without owning parser registration details."""
    return wk_cli.build_parser(sys.modules[__name__])


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    args = parser.parse_args(_normalize_help_argv(raw_argv))
    # ``wk.main([...])`` is a long-standing programmatic API that returned JSON;
    # preserve it while the real CLI (argv=None) uses concise human output.
    if argv is not None and "--json" not in raw_argv:
        args._legacy_output = True
    try:
        return int(args.func(args))
    except (SkillError, request_intake.RequestIntakeError, work_mode.WorkModeError, ValueError, agent_context.AgentAuthorizationError, OSError, json.JSONDecodeError) as exc:
        emit_cli_error(exc, json_output=bool(getattr(args, "json", False)), stream=sys.stderr,
            blocked=getattr(args.func, "__name__", "") in {"command_begin", "command_progress", "command_finish", "command_record_result"})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
