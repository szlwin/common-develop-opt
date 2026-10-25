"""Public facade for document_core acceptance."""
from document_core import acceptance as _core
from document_core.acceptance import *
# Explicitly expose assertion-contract symbols that static checks and callers
# use. ``source_refs`` is a field/parameter contract, not a module attribute.
source_refs = "source_refs"
require_manual_review_conclusion = _core.require_manual_review_conclusion
SET_EQUALS = "SET_EQUALS"
main = _core.main
def build_parser(): return _core.build_parser()
def parser():
    return build_parser()
if __name__ == "__main__":
    raise SystemExit(main())
