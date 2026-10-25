"""Public compatibility adapter for the preserved check_skill.py command."""
from pathlib import Path
import json
import sys
from compat_adapter import execute, load
_IMPL = Path(__file__).resolve().parent / "checks" / "check_skill.py"
try:
    globals().update({k:v for k,v in load(_IMPL).items() if k not in {"__name__", "__file__", "__package__"}})
except Exception as exc:
    if __name__ != '__main__':
        raise
    print(json.dumps({'status': 'FAILED', 'checks': [], 'warnings': [], 'details': {},
                      'errors': [f'CHECKER_BOOTSTRAP_FAILED: {type(exc).__name__}: {exc}']}, ensure_ascii=False))
    raise SystemExit(1)
if __name__ == "__main__":
    execute(_IMPL)
