"""Bridge a public script to the preserved implementation without hiding compatibility limits."""
from pathlib import Path
import hashlib
import importlib.util
import sys
from contextlib import contextmanager

_LOADED = {}

@contextmanager
def _implementation_import_path(target: Path):
    """Expose the implementation directory only while importing it.

    Retained implementations use historical bare imports.  Limiting the path
    mutation to module initialization prevents one public facade from
    changing import resolution for later facades in the same process.
    """
    parent = str(target.parent)
    original = list(sys.path)
    sys.path[:] = [item for item in sys.path if item != parent]
    sys.path.insert(0, parent)
    try:
        yield
    finally:
        sys.path[:] = original


def implementation_path(target):
    """Return a canonical implementation path without changing ``sys.path``."""
    return Path(target).resolve()


def load_module(target: Path):
    """Load and cache the implementation module as an explicit API object."""
    target = implementation_path(target)
    if target in _LOADED:
        return _LOADED[target]
    location = hashlib.sha256(str(target).encode()).hexdigest()[:16]
    name = f'_common_develop_compat_{target.stem}_{location}'
    module = sys.modules.get(name)
    if module is None or Path(module.__file__).resolve() != target:
        spec = importlib.util.spec_from_file_location(name, target)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            with _implementation_import_path(target):
                spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(name, None)
            raise
    _LOADED[target] = module
    return module

def load(target: Path) -> dict:
    return vars(load_module(target))


def invoke(target: Path, entry: str = 'main', *args, **kwargs):
    """Invoke one named implementation callable without copying globals."""
    callable_obj = getattr(load_module(target), entry)
    return callable_obj(*args, **kwargs)

def execute(target: Path, entry: str = 'main') -> None:
    raise SystemExit(invoke(target, entry))
