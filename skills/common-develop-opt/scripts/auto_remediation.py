#!/usr/bin/env python3
"""Public facade for the document_core auto-remediation implementation."""
from document_core.auto_remediation import *
from document_core.auto_remediation import main, build_parser
from document_core.auto_remediation import _record_task_event
def parser():
    import argparse
    p=argparse.ArgumentParser(); s=p.add_subparsers(dest='command')
    for n in ('architecture','classify','convergence','diagnose-log','policy','preflight'): s.add_parser(n)
    return p
if __name__ == "__main__":
    raise SystemExit(main())
