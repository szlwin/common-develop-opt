"""Structural boundaries for the public facades and internal domain modules."""
from __future__ import annotations

import re

import ast 
from pathlib import Path
from typing import Any

from checks._check_skill.context import ROOT
from checks._check_skill.model import CheckResult


FACADE_LINE_LIMITS = {
    "scripts/long_task.py": 300,
    "scripts/check_skill.py": 200,
}
INTERNAL_PACKAGES = ("_long_task", "_check_skill")
SUGGESTED_FUNCTION_LINE_LIMIT = 250


def _is_main_guard(node: ast.If) -> bool:
    try:
        return ast.unparse(node.test) == "__name__ == '__main__'"
    except (AttributeError, ValueError):
        return False


def _imported_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _facade_errors(path: Path, relative: str) -> list[str]:
    errors: list[str] = []
    source = path.read_text(encoding="utf-8")
    line_count = len(source.splitlines())
    limit = FACADE_LINE_LIMITS[relative]
    if line_count > limit:
        errors.append(f"{relative}: public facade has {line_count} lines; limit is {limit}")
    tree = ast.parse(source, filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Assign):
            continue
        if isinstance(node, ast.If) and _is_main_guard(node):
            continue
        errors.append(
            f"{relative}:{getattr(node, 'lineno', 1)}: facade may contain only imports and the __main__ delegate"
        )
    return errors


def check_internal_module_boundaries(result: CheckResult) -> None:
    """Enforce one-way public facades and import-only internal modules."""
    for relative in FACADE_LINE_LIMITS:
        path = ROOT / relative
        for home in ('runtime', 'checks'):
            candidate = ROOT / relative.replace('scripts/', f'scripts/{home}/', 1)
            if candidate.is_file() and not re.search(r'^\s*sys\.modules\[__name__\]\s*=', candidate.read_text(encoding='utf-8'), re.M):
                path = candidate
                break
        if not path.is_file():
            result.errors.append(f"missing public facade: {relative}")
            continue
        result.errors.extend(_facade_errors(path, relative))

    long_functions: list[dict[str, Any]] = []
    for package in INTERNAL_PACKAGES:
        package_root = next((ROOT / 'scripts' / home / package
                             for home in ('runtime', 'checks', '')
                             if (ROOT / 'scripts' / home / package if home else ROOT / 'scripts' / package).exists()),
                            ROOT / 'scripts' / package)
        for path in sorted(package_root.rglob("*.py")):
            relative = path.relative_to(ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported_roots = _imported_roots(tree)
            public_module = package.removeprefix("_")
            if public_module in imported_roots:
                result.errors.append(
                    f"{relative}: internal module must not import public facade {public_module}"
                )
            for node in tree.body:
                if isinstance(node, ast.If) and _is_main_guard(node):
                    result.errors.append(f"{relative}:{node.lineno}: internal modules must be import-only")
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                length = int((node.end_lineno or node.lineno) - node.lineno + 1)
                if length > SUGGESTED_FUNCTION_LINE_LIMIT:
                    long_functions.append({"path": relative, "function": node.name, "lines": length})

    if long_functions:
        result.warnings.append(
            "internal functions above the suggested 250-line limit remain composition candidates: "
            + ", ".join(
                f"{item['path']}:{item['function']}({item['lines']})" for item in long_functions
            )
        )
    result.details["internalModuleBoundaries"] = {
        "facadeLimits": FACADE_LINE_LIMITS,
        "suggestedFunctionLineLimit": SUGGESTED_FUNCTION_LINE_LIMIT,
        "longFunctions": long_functions,
    }
    if not any("facade" in error or "internal module" in error for error in result.errors):
        result.ok("Public facades are thin and internal runtime modules have no reverse CLI dependency")
