"""Public facade for document_core requirement_doc."""
import argparse
from document_core import requirement_doc as _core
from document_core.requirement_doc import *
from document_core import agent_context
# Executable document contract: command_init, command_sync_list, command_validate,
# command_status, command_sync_traceability, command_finalize_analysis,
# parse_trace_sources, merge_traceability, FEATURE_RE, CONFIRMATION_HEADINGS,
# REQUIRED_HEADINGS.
main = _core.main

def build_parser():
    """Expose the complete static command contract for discovery and callers."""
    p = argparse.ArgumentParser(description=__doc__)
    s = p.add_subparsers(dest="command", required=True)

    init = s.add_parser("init")
    agent_context.add_agent_argument(init)
    init.add_argument("--doc-root", required=True)
    init.add_argument("--version", required=True)
    init.add_argument("--target-id", required=True)
    init.add_argument("--requirement-id", required=True)
    init.add_argument("--title", required=True)
    init.add_argument("--requirement-type", default="新增能力")
    init.add_argument("--primary-module", required=True)
    init.add_argument("--collaboration-modules", default="-")
    init.add_argument("--affected-roles", default="待确认")
    init.add_argument("--change-requirement-id", default="-")
    init.add_argument("--feature", action="append")
    init.add_argument("--overwrite", action="store_true")
    init.add_argument("--json", action="store_true")
    init.set_defaults(handler=_core.command_init, func=_core.command_init)

    sync_list = s.add_parser("sync-list")
    agent_context.add_agent_argument(sync_list)
    sync_list.add_argument("--file", required=True)
    sync_list.add_argument("--description", required=True)
    sync_list.add_argument("--status", default="")
    sync_list.add_argument("--json", action="store_true")
    sync_list.set_defaults(handler=_core.command_sync_list, func=_core.command_sync_list)

    validate = s.add_parser("validate")
    agent_context.add_agent_argument(validate)
    validate.add_argument("--file", required=True)
    validate.add_argument("--stage", choices=("confirmation", "analysis"), required=True)
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(handler=_core.command_validate, func=_core.command_validate)

    status = s.add_parser("status")
    agent_context.add_agent_argument(status)
    status.add_argument("--file", required=True)
    status.add_argument("--json", action="store_true")
    status.set_defaults(handler=_core.command_status, func=_core.command_status)

    sync = s.add_parser("sync-traceability")
    agent_context.add_agent_argument(sync)
    sync.add_argument("--file", required=True)
    sync.add_argument("--task-dir", required=True)
    sync.add_argument("--dry-run", action="store_true")
    sync.add_argument("--json", action="store_true")
    sync.set_defaults(handler=_core.command_sync_traceability, func=_core.command_sync_traceability)

    finalize = s.add_parser("finalize-requirement-analysis")
    agent_context.add_agent_argument(finalize)
    finalize.add_argument("--file", required=True)
    finalize.add_argument("--task-dir", required=True)
    finalize.add_argument("--json", action="store_true")
    finalize.set_defaults(handler=_core.command_finalize_analysis, func=_core.command_finalize_analysis)
    return p

def parser():
    return build_parser()
if __name__ == "__main__":
    raise SystemExit(main())
