"""Public compatibility adapter for the preserved baseline_guard.py command."""
from pathlib import Path
from compat_adapter import execute, load
_IMPL = Path(__file__).resolve().parent / "checks" / "baseline_guard.py"
globals().update({k:v for k,v in load(_IMPL).items() if k not in {"__name__", "__file__", "__package__"}})
if __name__ == "__main__":
    execute(_IMPL)
