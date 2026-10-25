"""Resolve physical implementations and logical References for static checks."""
import ast 
from pathlib import Path
from resource_paths import formalize

ROOT = Path(__file__).resolve().parents[3]


def checked_path(path):
    path = Path(path)
    try:
        relative = path.absolute().relative_to(ROOT.absolute())
    except ValueError as exc:
        raise ValueError(f'CHECK_PATH_OUTSIDE_PACKAGE: {path}') from exc
    path = formalize(path)
    if not path.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError(f'CHECK_PATH_OUTSIDE_PACKAGE: {relative}')
    if relative.parts[:1] == ('scripts',):
        if path.is_file():
            tree = ast.parse(path.read_text(encoding='utf-8')) if path.suffix == '.py' else None
            adapter = tree and any(isinstance(node, ast.ImportFrom) and node.module == 'compat_adapter' for node in tree.body)
            if adapter:
                impl = next((ROOT / 'scripts' / home / Path(*relative.parts[1:])
                             for home in ('runtime', 'checks', 'document_core')
                             if (ROOT / 'scripts' / home / Path(*relative.parts[1:])).is_file()), None)
                if impl is None:
                    raise ValueError(f'ADAPTER_IMPLEMENTATION_MISSING: {relative}')
                path = impl
        else:
            impl = next((ROOT / 'scripts' / home / Path(*relative.parts[1:])
                         for home in ('runtime', 'checks', 'document_core')
                         if (ROOT / 'scripts' / home / Path(*relative.parts[1:])).exists()), None)
            if impl is not None:
                path = impl
    if not path.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError(f'CHECK_PATH_OUTSIDE_PACKAGE: {relative}')
    return path


def read_check_text(path, *args, **kwargs):
    path = checked_path(path)
    if path.suffix == '.md' and path.resolve().is_relative_to((ROOT/'references').resolve()):
        import reference_reader  # top-level facade; probes override its ROOT
        resolved = path.resolve()
        try:
            _, _, details = reference_reader.resource_sections(resolved)
        except Exception:
            # Probe and legacy fixtures may reference a missing local detail;
            # retain the historical checker behavior and read the summary.
            return path.read_text(*args, **kwargs)
        sources = dict.fromkeys([resolved, *(source.resolve() for source in details)])
        return '\n'.join(source.read_text(*args, **kwargs) for source in sources)
    return path.read_text(*args, **kwargs)
