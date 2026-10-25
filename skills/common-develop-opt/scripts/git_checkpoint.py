"""Public facade for document_core git_checkpoint."""
from document_core import git_checkpoint as _core
from document_core.git_checkpoint import *
main = _core.main
def build_parser(): return _core.build_parser()
def parser():
    return build_parser()
if __name__ == "__main__":
    raise SystemExit(main())
