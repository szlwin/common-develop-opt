"""Public compatibility adapter for the preserved design_doc.py command."""
from pathlib import Path
from compat_adapter import execute, load
_IMPL = Path(__file__).resolve().parent / "runtime" / "design_doc.py"
globals().update({k:v for k,v in load(_IMPL).items() if k not in {"__name__", "__file__", "__package__"}})
if __name__ == "__main__":
    execute(_IMPL)
