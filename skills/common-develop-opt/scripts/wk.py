"""Unified, transactional entry point for common-develop request work."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Iterable

_SCRIPTS = Path(__file__).resolve().parent
# Import-path setup belongs only at the CLI boundary.  The old compat path no
# longer exists and must not be injected into the process search path.
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

# The canonical parser and dispatcher own the public command surface; the
# handler registry resolves every command before dispatch (see wk_handlers).
from document_core import wk_dispatcher, wk_handlers

# Established runtime pieces still referenced by the entry contract: the
# facade provides the patched-callback command implementations, argv
# normalization, the error boundary types and the public module surface.
from runtime import wk as _runtime
from runtime.error_catalog import SkillError, emit_cli_error
from runtime.request_intake import RequestIntakeError
from runtime.work_mode import WorkModeError
from document_core.agent_context import AgentAuthorizationError


def build_parser():
    return wk_dispatcher.build_parser(wk_handlers.build_production_registry())


def main(argv: Iterable[str] | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    registry = wk_handlers.build_production_registry()
    args = wk_dispatcher.build_parser(registry).parse_args(
        _runtime._normalize_help_argv(raw_argv)
    )
    # ``wk.main([...])`` is a long-standing programmatic API that returned JSON;
    # preserve it while the real CLI (argv=None) uses concise human output.
    if argv is not None and "--json" not in raw_argv:
        args._legacy_output = True
    handler = getattr(args, "_handler", None)
    if handler is None:
        raise wk_dispatcher.DispatcherError(
            f"canonical handler not registered: {args.command}"
        )
    try:
        return int(handler(args))
    except (SkillError, RequestIntakeError, WorkModeError, ValueError,
            AgentAuthorizationError, OSError, json.JSONDecodeError) as exc:
        emit_cli_error(
            exc,
            json_output=bool(getattr(args, "json", False)),
            stream=sys.stderr,
            blocked=getattr(handler, "__name__", "") in {
                "command_begin", "command_progress", "command_finish",
                "command_record_result",
            },
        )
        return 1


# Preserve the historical module surface (command_* callbacks, emit_result,
# parser helpers) so existing programmatic callers keep working; build_parser
# and main above stay canonical because setdefault never overrides them.
# Export only the names retained for historical callers; runtime internals do
# not become public accidentally through a ``dir()`` sweep.
_COMPAT_EXPORTS = (
    "agent_context", "work_mode", "SkillError", "RequestIntakeError",
    "WorkModeError", "emit_cli_error",
)
for _name in _COMPAT_EXPORTS:
    # Some names are imported directly above because they form the public
    # error boundary; do not require the runtime facade to re-export them.
    if _name not in globals():
        globals()[_name] = getattr(_runtime, _name)

if __name__ == "__main__":
    raise SystemExit(main())
