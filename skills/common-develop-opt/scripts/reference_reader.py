"""Public facade for the document_core reference-reader implementation."""
import sys
from pathlib import Path
_SCRIPTS = str(Path(__file__).resolve().parent)
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)
from document_core.reference_reader import *
from document_core import reference_reader as _implementation
from document_core.reference_reader import main, build_parser

# Keep test/checker root overrides effective while delegating parsing to the
# canonical implementation.
ROOT = _implementation.ROOT
def resource_sections(path):
    return _implementation._sections.resource_sections(path, root=ROOT)

def parser():
    return _implementation.build_parser()
if __name__ == "__main__":
    raise SystemExit(main())
