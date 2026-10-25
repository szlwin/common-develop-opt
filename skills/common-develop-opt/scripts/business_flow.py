"""Public facade for document_core business_flow."""
import argparse
from document_core import business_flow as _core
from document_core.business_flow import *
from document_core import agent_context
main = _core.main

def build_parser():
    return parser()

def parser():
    """Expose the complete static command contract for discovery and callers."""
    p = argparse.ArgumentParser(description=__doc__)
    s = p.add_subparsers(dest="command", required=True)

    init = s.add_parser("init")
    agent_context.add_agent_argument(init)
    init.add_argument("--doc-root", default="project_doc")
    init.add_argument("--version", required=True)
    init.add_argument("--module", required=True)
    init.add_argument("--group", required=True)
    init.add_argument("--name")
    init.set_defaults(handler=_core.command_init, func=_core.command_init)

    validate = s.add_parser("validate")
    agent_context.add_agent_argument(validate)
    validate.add_argument("--doc-root", default="project_doc")
    validate.add_argument("--file", required=True)
    validate.add_argument("--require-links", action="store_true")
    validate.set_defaults(handler=_core.command_validate, func=_core.command_validate)

    render = s.add_parser("render")
    agent_context.add_agent_argument(render)
    render.add_argument("--doc-root", default="project_doc")
    render.add_argument("--file", required=True)
    render.add_argument("--output")
    render.set_defaults(handler=_core.command_render, func=_core.command_render)

    impact = s.add_parser("impact")
    agent_context.add_agent_argument(impact)
    impact.add_argument("--doc-root", default="project_doc")
    impact.add_argument("--ref", action="append")
    impact.add_argument("--json", action="store_true")
    impact.set_defaults(handler=_core.command_impact, func=_core.command_impact)
    return p
if __name__ == "__main__":
    raise SystemExit(main())
