"""Import-path bootstrap for direct ``unittest discover -s tests`` runs."""

from __future__ import annotations

import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent
for _path in (str(_ROOT / "scripts"), str(_ROOT / "tests")):
    if _path not in sys.path:
        sys.path.insert(0, _path)
