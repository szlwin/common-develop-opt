"""Public facade for the document_core review_session implementation."""
import sys
from pathlib import Path
_scripts = Path(__file__).resolve().parent
if str(_scripts) not in sys.path:
    sys.path.append(str(_scripts))
from document_core import review_session as _core
from document_core.review_session import *
from document_core.review_session import main
for _name, _value in vars(_core).items():
    if _name.startswith('_') and not _name.startswith('__'):
        globals()[_name] = _value
def build_parser():
    return _core.build_parser()

def parser():
    return _core.build_parser()
def _cli():
    try:
        return main()
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(_cli())
