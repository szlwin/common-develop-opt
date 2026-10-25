"""Public facade for the document_core merge_docs command."""
from document_core import merge_docs as _core
from document_core.merge_docs import *
for _name, _value in vars(_core).items():
    if _name.startswith('_') and not _name.startswith('__'):
        globals()[_name] = _value
main = _core.main

def build_parser():
    return _core.parser()

def parser():
    import argparse
    result = argparse.ArgumentParser()
    sub = result.add_subparsers(dest="command")
    for name in ("init", "validate", "preview", "apply", "archive", "status", "query", "write"):
        command = sub.add_parser(name)
        from document_core import agent_context
        agent_context.add_agent_argument(command)
        command.add_argument("--doc-root", default="project_doc")
        command.add_argument("--module", required=True)
        command.add_argument("--version", required=name != "status")
    return result
if __name__ == "__main__":
    raise SystemExit(main())
