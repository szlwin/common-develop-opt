"""Public facade for document_core task_plan."""
from document_core import task_plan as _core
from document_core.task_plan import *
main = _core.main
def build_parser(): return _core.build_parser()
def parser():
    import argparse
    p=argparse.ArgumentParser(); s=p.add_subparsers(dest='command')
    for n in ('finalize','init','query','revise','seal','status','submit-review','validate'): s.add_parser(n)
    return p
if __name__ == "__main__":
    raise SystemExit(main())
