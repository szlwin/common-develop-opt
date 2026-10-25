"""Make the canonical core importable for historical direct-script entrypoints."""
import sys
from pathlib import Path

_original = list(sys.path)
try:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import document_core
finally:
    sys.path[:] = _original
