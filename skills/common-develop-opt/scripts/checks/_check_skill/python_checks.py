"""Python checks for the internal runtime."""
from __future__ import annotations
import argparse 
import ast 
import hashlib 
import importlib.util 
import json 
import py_compile 
import re 
import sys 
import tempfile 
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from document_core import simple_yaml
from document_core import agent_context
from checks import semantic_audit
import reference_reader
from runtime import context_resolver
from runtime import contract_normalization
from runtime import design_doc
from runtime import case_doc
from checks import cli_help
import merge_docs
from runtime import long_task as long_task_runtime
from runtime._core import capability_packs, implementation_strategy
from checks._check_skill.context import *
from checks._check_skill.model import CheckResult
from checks._check_skill.io import load_json, load_yaml
from checks._check_skill.module_loader import *
from checks._check_skill.routing import *
from checks._check_skill.manifest import *

try:
    import jsonschema 
except ImportError:  # pragma: no cover
    jsonschema = None

from checks._check_skill.cli import parser
from checks._check_skill.model import CheckResult
def parser_command_names(parser: argparse.ArgumentParser) -> set[str]:
    """Return registered argparse subcommands without inspecting source text."""
    result: set[str] = set()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            result.update(str(item) for item in action.choices)
    return result


def check_python(result: CheckResult) -> None:
    leaked_tests = sorted((ROOT / "scripts").glob("test_*.py"))
    if leaked_tests:
        result.errors.append(
            "scripts/ must contain runtime code only; move unittest modules to tests/: "
            + ", ".join(path.name for path in leaked_tests)
        )

    duplicate_bodies: dict[str, list[tuple[str, str, int]]] = {}
    for path in sorted((ROOT / "scripts").rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            result.errors.append(f"{path.relative_to(ROOT)}: parse failed: {exc}")
            continue

        if not path.name.startswith("test_"):
            for function in (node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
                if function.name == "main":
                    continue
                body = ast.dump(ast.Module(body=function.body, type_ignores=[]), include_attributes=False)
                if len(body) < 350:
                    continue
                digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
                duplicate_bodies.setdefault(digest, []).append(
                    (path.relative_to(ROOT).as_posix(), function.name, function.lineno)
                )

        # argparse silently becomes an AI-facing conflict when the same parser target
        # declares the same literal flag twice. Detect it before importing the script.
        for function in (node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
            declarations: dict[tuple[str, str], int] = {}
            for node in ast.walk(function):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr != "add_argument" or not node.args:
                    continue
                target = ast.unparse(node.func.value)
                for argument in node.args:
                    if not isinstance(argument, ast.Constant) or not isinstance(argument.value, str):
                        continue
                    option = argument.value
                    if not option.startswith("-"):
                        continue
                    key = (target, option)
                    if key in declarations:
                        result.errors.append(
                            f"{path.relative_to(ROOT)}:{node.lineno}: duplicate argparse option {option} "
                            f"for {target} in {function.name}; first declared at line {declarations[key]}"
                        )
                    else:
                        declarations[key] = node.lineno

    for locations in duplicate_bodies.values():
        if len(locations) > 1:
            result.errors.append(f"duplicate runtime helper implementation must be centralized: {locations}")

    with tempfile.TemporaryDirectory(prefix="common-develop-pycompile-") as temp_dir:
        temp_root = Path(temp_dir)
        for path in sorted([*(ROOT / "scripts").rglob("*.py"), *(ROOT / "tests").rglob("*.py")]):
            try:
                target = temp_root / path.relative_to(ROOT).with_suffix(".pyc")
                target.parent.mkdir(parents=True, exist_ok=True)
                py_compile.compile(str(path), cfile=str(target), doraise=True)
            except py_compile.PyCompileError as exc:
                result.errors.append(f"{path.relative_to(ROOT)}: compile failed: {exc.msg}")
    if not result.errors:
        result.ok("Python scripts compile without duplicate CLI options or duplicate helper implementations")
