"""Public facade for document_core evidence."""
from document_core import evidence as _core
from document_core.evidence import *
from document_core import evidence as _core
for _name in ("_cache_put", "_FILE_DIGEST_CACHE", "_GIT_VALIDATION_CACHE", "_INDEX_VALIDATION_CACHE"):
    globals()[_name] = getattr(_core, _name)
main = _core.main
def build_parser(): return _core.build_parser()
def parser():
    return build_parser()
if __name__ == "__main__":
    raise SystemExit(main())
