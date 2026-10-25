"""Public facade for the document_core context routing implementation."""
from document_core.context_routing import *
from document_core.context_routing import main, build_parser
def parser():
    import argparse
    p=argparse.ArgumentParser(); s=p.add_subparsers(dest='command')
    for n in ('list','show','resolve','route','condition','resource'): s.add_parser(n)
    return p
if __name__ == "__main__":
    raise SystemExit(main())
