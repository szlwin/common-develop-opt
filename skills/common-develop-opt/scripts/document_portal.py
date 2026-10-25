"""Public facade for document_core document portal."""
from document_core import document_portal as _core
from document_core.document_portal import *
main = _core.main
for _name, _value in vars(_core).items():
    if _name.startswith('_') and not _name.startswith('__'):
        globals()[_name] = _value

def build_parser():
    return _core.build_parser()

def parser():
    import argparse
    result = argparse.ArgumentParser()
    sub = result.add_subparsers(dest="command")
    for name in ("render", "validate"):
        command = sub.add_parser(name)
        from document_core import agent_context
        agent_context.add_agent_argument(command)
        command.add_argument("--doc-root", default="project_doc")
        command.add_argument("--version", action="append")
    return result
if __name__ == "__main__":
    raise SystemExit(main())
