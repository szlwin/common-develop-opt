"""common-develop test package bootstrap."""
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"
for _path in (str(_SCRIPTS), str(_ROOT / "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
