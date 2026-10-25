#!/usr/bin/env python3
"""Run the canonical common-develop delivery checks without polluting the package."""

from __future__ import annotations

import argparse 
import ast 
import json 
import os 
import re 
import shutil 
import subprocess 
import sys 
import tempfile 
import time 
import traceback 
import unittest 
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from document_core import agent_context

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = ROOT / "scripts"
TESTS_ROOT = ROOT / "tests"
TRANSIENT_DIR_NAMES = {"__pycache__", ".pytest_cache", "htmlcov"}
TRANSIENT_SUFFIXES = {".pyc", ".pyo", ".tmp", ".bak"}
MERGE_DOCS_BATCH_SIZE = 5
UNIT_BATCH_TIMEOUT_SECONDS = 240
INTERNAL_TEST_EXCLUSION_REASON = (
    "tests/internal is reserved for the unittest worker and test helpers; "
    "only tests/test_*.py is the canonical unit-test source"
)
UNIT_MODULE_GROUPS = (
    ("test_agent_context", "test_ai_context", "test_artifact_footprint", "test_business_flow",
    "test_check_skill", "test_context_resolver", "test_doc_layout", "test_document_facts", "test_evidence"),
    ("test_git_checkpoint", "test_long_task", "test_machine_verification", "test_manual_review",
     "test_process_gates", "test_quality_gate", "test_reference_reader"),
    ("test_render_relationships", "test_request_intake", "test_requirement_doc", "test_review_contract",
     "test_review_contract_cli", "test_review_store", "test_semantic_audit", "test_simple_yaml"),
    ("test_task_layout", "test_task_plan", "test_task_verify",
     "test_independent_test_environment", "test_wk", "test_work_mode", "test_work_record"),
)


def transient_paths(root: Path = ROOT) -> list[Path]:
    """Return package-local generated files and directories safe to remove."""

    matches: list[Path] = []
    for path in root.rglob("*"):
        if ".git" in path.parts:
            continue
        if path.is_dir() and path.name in TRANSIENT_DIR_NAMES:
            matches.append(path)
            continue
        if path.is_file() and (path.suffix in TRANSIENT_SUFFIXES or path.name in {".coverage", ".DS_Store", "Thumbs.db"} or path.name.startswith("._")):
            matches.append(path)
    # Remove children before parents and keep deterministic output.
    return sorted(set(matches), key=lambda item: (-len(item.parts), item.as_posix()))


def clean_transient_report(root: Path = ROOT) -> dict[str, Any]:
    """Remove known generated artifacts and retain per-path cleanup errors."""

    removed: list[str] = []
    errors: list[dict[str, str]] = []
    for path in transient_paths(root):
        if not path.exists():
            continue
        rel = path.relative_to(root).as_posix()
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed.append(rel)
        except FileNotFoundError:
            # A concurrent cleanup is harmless and should not hide the
            # remaining cleanup result.
            continue
        except OSError as exc:
            errors.append({
                "path": rel,
                "errorType": type(exc).__name__,
                "message": str(exc),
            })
    return {"removed": removed, "errors": errors}


def clean_transient(root: Path = ROOT) -> list[str]:
    """Backward-compatible list API for callers that do not need diagnostics."""

    return clean_transient_report(root)["removed"]


def _run(
    name: str,
    command: list[str],
    env: dict[str, str],
    *,
    timeout_seconds: int = 300,
    cwd: Path = ROOT,
) -> dict[str, Any]:
    started = time.perf_counter()
    # PYTHONDONTWRITEBYTECODE already prevents package-local cache pollution.
    # PYTHONPYCACHEPREFIX is deliberately removed: selected Review/semantic
    # unittest groups can stall in this execution environment when imports are
    # redirected to a temporary prefix, even though no bytecode should be written.
    run_env = env.copy()
    run_env.pop("PYTHONPYCACHEPREFIX", None)
    # A PIPE can remain open when a tested command launches an environment
    # helper process that outlives the unittest parent.  Capturing into a
    # temporary file makes completion depend only on the requested process.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as output_file:
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                env=run_env,
                text=True,
                stdout=output_file,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=timeout_seconds,
            )
            output_file.flush()
            output_file.seek(0)
            output = output_file.read()
            mocked_stdout = getattr(completed, "stdout", None)
            if not output and isinstance(mocked_stdout, str):
                output = mocked_stdout
            result = {
                "name": name,
                "command": command,
                "cwd": str(cwd),
                "exitCode": completed.returncode,
                "status": "PASSED" if completed.returncode == 0 else "FAILED",
                "output": output,
                "durationMs": round((time.perf_counter() - started) * 1000, 3),
            }
            if completed.returncode != 0:
                result["errorCode"] = "QUALITY_GATE_STEP_FAILED"
                result["phase"] = "EXECUTION"
            return result
        except subprocess.TimeoutExpired:
            output_file.flush()
            output_file.seek(0)
            return {
                "name": name,
                "command": command,
                "cwd": str(cwd),
                "exitCode": 124,
                "status": "TIMEOUT",
                "errorCode": "QUALITY_GATE_SUBPROCESS_TIMEOUT",
                "phase": "EXECUTION",
                "output": output_file.read(),
                "durationMs": round((time.perf_counter() - started) * 1000, 3),
            }
        except OSError as exc:
            return {
                "name": name,
                "command": command,
                "cwd": str(cwd),
                "exitCode": 1,
                "status": "FAILED",
                "errorCode": "QUALITY_GATE_SUBPROCESS_START_FAILED",
                "errorType": type(exc).__name__,
                "phase": "EXECUTION",
                "output": str(exc),
                "durationMs": round((time.perf_counter() - started) * 1000, 3),
            }


def test_modules(root: Path = ROOT) -> list[str]:
    """Return real unittest modules; exclude the similarly named CLI utility."""

    return [
        path.relative_to(root / 'tests').with_suffix('').as_posix().replace('/', '.')
        for path in sorted((root / "tests").glob('test_*.py'))
    ]


def test_discovery_report(root: Path = ROOT) -> dict[str, Any]:
    """Describe canonical tests and intentional internal-resource exclusions."""

    tests_root = root / "tests"
    discovered = [
        {
            "path": path.relative_to(root).as_posix(),
            "module": path.relative_to(tests_root).with_suffix('').as_posix().replace('/', '.'),
        }
        for path in sorted(tests_root.glob('test_*.py'))
    ]
    internal_root = tests_root / "internal"
    internal_files = [
        path.relative_to(root).as_posix()
        for path in sorted(internal_root.rglob("*"))
        if path.is_file() and path.name.startswith("test_") and path.suffix == ".py"
    ] if internal_root.is_dir() else []
    return {
        "policy": "CURRENT_MODULES_ONLY",
        "canonicalPattern": "tests/test_*.py",
        "discoveredTests": discovered,
        "excludedTests": [{
            "path": "tests/internal",
            "files": internal_files,
            "reason": INTERNAL_TEST_EXCLUSION_REASON,
        }],
    }


UNITTEST_WORKER = TESTS_ROOT / "internal" / "unittest_worker.py"


def unit_command(targets: list[str]) -> list[str]:
    return [sys.executable, str(UNITTEST_WORKER), "--quiet", *targets]


@dataclass(frozen=True)
class DiscoveredTest:
    test_id: str
    slow: bool = False


def _has_slow_decorator(node: ast.AST) -> bool:
    decorators = getattr(node, "decorator_list", [])
    for decorator in decorators:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        if isinstance(target, ast.Name) and target.id == "slow":
            return True
        if isinstance(target, ast.Attribute) and target.attr == "slow":
            return True
    return False


def discover_tests(module: str) -> list[DiscoveredTest]:
    """Return deterministic test metadata without importing the test module."""

    path = TESTS_ROOT / (module.replace('.', '/') + '.py')
    if not path.is_file():
        raise ValueError(f"missing test module: {module}")
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    result: list[DiscoveredTest] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_"):
            result.append(DiscoveredTest(f"{module}.{node.name}", _has_slow_decorator(node)))
        elif isinstance(node, ast.ClassDef):
            class_slow = _has_slow_decorator(node)
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test_"):
                    result.append(DiscoveredTest(
                        f"{module}.{node.name}.{child.name}",
                        class_slow or _has_slow_decorator(child),
                    ))
    if not result:
        raise ValueError(f"no tests discovered for module {module}")
    return result


def test_ids(module: str, *, include_slow: bool = True) -> list[str]:
    """Return deterministic unittest IDs, optionally excluding explicit slow tests."""

    return [item.test_id for item in discover_tests(module) if include_slow or not item.slow]


def chunked_test_ids(ids: list[str], size: int = MERGE_DOCS_BATCH_SIZE) -> list[list[str]]:
    if size <= 0:
        raise ValueError("test batch size must be positive")
    return [ids[index:index + size] for index in range(0, len(ids), size)]


def unit_module_groups(modules: list[str]) -> list[list[str]]:
    available = set(modules)
    groups: list[list[str]] = []
    assigned: set[str] = set()
    for declared in UNIT_MODULE_GROUPS:
        group = [module for module in declared if module in available]
        if group:
            groups.append(group)
            assigned.update(group)
    for module in modules:
        if module not in assigned and module != "test_merge_docs":
            groups.append([module])
    return groups


def _progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def unit_batch_definitions(
    modules: list[str],
    discovered: dict[str, list[str]],
    *,
    individual_targets: bool = False,
) -> list[dict[str, Any]]:
    batches: list[dict[str, Any]] = []
    for group_index, module_group in enumerate(unit_module_groups(modules), 1):
        targets = (
            [test_id for module in module_group for test_id in discovered.get(module, [])]
            if individual_targets else module_group
        )
        if not targets:
            continue
        batches.append({
            "name": f"unit:group-{group_index:02d}",
            "targets": targets,
            "testModules": module_group,
            "testCaseCount": sum(len(discovered.get(module, [])) for module in module_group),
        })
    for batch_index, batch_ids in enumerate(chunked_test_ids(discovered.get("test_merge_docs", [])), 1):
        batches.append({
            "name": f"unit:test_merge_docs:batch-{batch_index:02d}",
            "targets": batch_ids,
            "testModules": ["test_merge_docs"],
            "testCaseCount": len(batch_ids),
        })
    return batches


def parse_unit_worker_output(output: str, batches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_name = {str(batch["name"]): batch for batch in batches}
    steps: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    output_lines: list[str] = []

    def finish_current() -> None:
        nonlocal current, output_lines
        if current is not None:
            current["output"] = "\n".join(output_lines).rstrip()
            steps.append(current)
        current = None
        output_lines = []

    for line in output.splitlines():
        if line.startswith("__QG_STEP__|"):
            finish_current()
            parts = line.split("|", 5)
            if len(parts) != 6:
                continue
            _, name, rc_text, duration_text, cases_text, modules_text = parts
            try:
                rc = int(rc_text)
                duration = float(duration_text)
                cases = int(cases_text)
            except ValueError:
                continue
            batch = by_name.get(name, {})
            current = {
                "name": name,
                "category": "unit",
                "command": unit_command(list(batch.get("targets", []))),
                "cwd": str(SCRIPTS_ROOT),
                "exitCode": rc,
                "status": "PASSED" if rc == 0 else "TIMEOUT" if rc == 124 else "FAILED",
                "phase": "EXECUTION",
                "durationMs": duration,
                "testCaseCount": cases,
                "testModules": modules_text.split(",") if modules_text else list(batch.get("testModules", [])),
            }
            if rc == 124:
                current["errorCode"] = "QUALITY_GATE_SUBPROCESS_TIMEOUT"
            elif rc != 0:
                current["errorCode"] = "TEST_EXECUTION_FAILED"
        elif current is not None:
            output_lines.append(line)
    finish_current()
    return steps


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    agent_context.add_agent_argument(parser)
    parser.add_argument("--profile", choices=("static", "fast", "full"), default="static")
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--clean-transient", action="store_true")
    parser.add_argument("--command-surface-inventory", type=Path,
                        help="also check an explicit repository v1 inventory")
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="stop after the first failed step; the default aggregates all available results",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def failed_step(
    name: str,
    message: str,
    *,
    category: str,
    command: list[str] | None = None,
    cwd: Path = ROOT,
    error_code: str = "QUALITY_GATE_STEP_FAILED",
    error_type: str = "",
    phase: str = "EXECUTION",
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "name": name,
        "category": category,
        "command": command or [],
        "cwd": str(cwd),
        "exitCode": 1,
        "status": "FAILED",
        "errorCode": error_code,
        "phase": phase,
        "output": message,
        "durationMs": 0.0,
    }
    if error_type:
        result["errorType"] = error_type
    return result


def summarize_steps(steps: list[dict[str, Any]]) -> dict[str, Any]:
    unit_steps = [step for step in steps if step.get("category") == "unit"]
    slowest = sorted(
        (
            {
                "name": str(step.get("name") or ""),
                "durationMs": float(step.get("durationMs") or 0.0),
                "status": str(step.get("status") or ""),
                "testCaseCount": int(step.get("testCaseCount") or 0),
            }
            for step in steps
        ),
        key=lambda item: (-item["durationMs"], item["name"]),
    )[:10]
    unit_modules: set[str] = set()
    for step in unit_steps:
        declared = step.get("testModules")
        if isinstance(declared, list):
            unit_modules.update(str(item) for item in declared)
        else:
            name = str(step.get("name") or "")
            if name.startswith("unit:"):
                unit_modules.add(name.split(":", 2)[1])
    return {
        "stepCount": len(steps),
        "passedSteps": sum(step.get("status") == "PASSED" for step in steps),
        "failedSteps": sum(step.get("status") == "FAILED" for step in steps),
        "timedOutSteps": sum(step.get("status") == "TIMEOUT" for step in steps),
        "unitTestModules": len(unit_modules),
        "unitTestCases": sum(int(step.get("testCaseCount") or 0) for step in unit_steps),
        "totalDurationMs": round(sum(float(step.get("durationMs") or 0.0) for step in steps), 3),
        "slowestSteps": slowest,
    }


def _main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        agent = agent_context.authorize_command(args, "quality_gate", "run")
    except (agent_context.AgentAuthorizationError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    steps: list[dict[str, Any]] = []
    discovery: dict[str, Any] | None = None
    cleanup_before = clean_transient_report() if args.clean_transient else {"removed": [], "errors": []}
    removed_before = cleanup_before["removed"]
    if cleanup_before["errors"]:
        steps.append(failed_step(
            "cleanup-before",
            "transient cleanup failed",
            category="maintenance",
            error_code="QUALITY_GATE_CLEANUP_FAILED",
            phase="CLEANUP",
        ))
        steps[-1]["errors"] = cleanup_before["errors"]
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Test subprocesses must not inherit the notebook kernel marker. In this
    # environment that marker triggers unrelated runtime preheating on every
    # Python startup and can make otherwise fast unittest modules stall.
    env["OAI_IS_JUPYTER_KERNEL"] = "0"
    existing_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        [str(TESTS_ROOT), str(SCRIPTS_ROOT)]
        + ([existing_pythonpath] if existing_pythonpath else [])
    )
    with tempfile.TemporaryDirectory(prefix="common-develop-pycache-") as pycache:
        env["PYTHONPYCACHEPREFIX"] = pycache
        check_command = [sys.executable, "scripts/check_skill.py", "-g", agent, "--json"]
        if args.write_manifest:
            check_command.append("--write-manifest")
        if args.command_surface_inventory is not None:
            check_command.extend(["--command-surface-inventory", str(args.command_surface_inventory.resolve())])
        static_commands = [
            ("check_skill", check_command),
            (
                "semantic_audit",
                [sys.executable, "scripts/semantic_audit.py", "-g", agent, "--json"],
            ),
            (
                "cli_help",
                [sys.executable, "scripts/cli_help.py", "validate", "-g", agent, "--json"],
            ),
        ]
        for name, command in static_commands:
            _progress(f"QUALITY_GATE START {name}")
            step = _run(name, command, env)
            step["category"] = "static"
            steps.append(step)
            _progress(f"QUALITY_GATE END {name} status={step['status']} durationMs={step['durationMs']}")
            if args.fail_fast and step["exitCode"] != 0:
                break

        static_failed = any(step["exitCode"] != 0 for step in steps)
        if args.profile in {"fast", "full"} and not (args.fail_fast and static_failed):
            discovery = test_discovery_report()
            # Keep the existing injection point for callers/tests that
            # customize the canonical module list, while the report remains
            # an independent explanation of the repository policy.
            modules = test_modules()
            discovered: dict[str, list[str]] = {}
            discovery_failed = False
            for module in modules:
                try:
                    discovered[module] = test_ids(module, include_slow=args.profile == "full")
                except (SyntaxError, UnicodeError, OSError, ImportError, ValueError) as exc:
                    if isinstance(exc, SyntaxError):
                        error_code = "TEST_DISCOVERY_SYNTAX_ERROR"
                    elif isinstance(exc, (ImportError, ModuleNotFoundError)):
                        error_code = "TEST_DISCOVERY_IMPORT_ERROR"
                    elif isinstance(exc, OSError):
                        error_code = "TEST_DISCOVERY_IO_ERROR"
                    else:
                        error_code = "TEST_DISCOVERY_CONFIGURATION_ERROR"
                    steps.append(
                        failed_step(
                            f"unit:{module}",
                            f"test discovery failed: {type(exc).__name__}: {exc}",
                            category="unit",
                            command=unit_command([module]),
                            cwd=ROOT,
                            error_code=error_code,
                            error_type=type(exc).__name__,
                            phase="DISCOVERY",
                        )
                    )
                    discovery_failed = True
                    if args.fail_fast:
                        break
                except Exception as exc:
                    steps.append(failed_step(
                        f"unit:{module}",
                        f"quality gate discovery failed internally: {type(exc).__name__}: {exc}",
                        category="quality_gate",
                        error_code="QUALITY_GATE_INTERNAL_ERROR",
                        error_type=type(exc).__name__,
                        phase="DISCOVERY",
                    ))
                    steps[-1]["traceback"] = traceback.format_exc(limit=3)
                    discovery_failed = True
                    if args.fail_fast:
                        break
            if not (args.fail_fast and discovery_failed):
                batches = unit_batch_definitions(
                    modules, discovered, individual_targets=args.profile == "fast"
                )
                _progress(
                    f"QUALITY_GATE START unit-batches batches={len(batches)} "
                    f"modules={len(modules)} cases={sum(len(ids) for ids in discovered.values())}"
                )
                for batch in batches:
                    name = str(batch["name"])
                    command = unit_command(list(batch["targets"]))
                    _progress(
                        f"QUALITY_GATE START {name} cases={batch['testCaseCount']} "
                        f"modules={','.join(batch['testModules'])}"
                    )
                    step = _run(
                        name,
                        command,
                        env,
                        timeout_seconds=UNIT_BATCH_TIMEOUT_SECONDS,
                        cwd=ROOT,
                    )
                    step["category"] = "unit"
                    step["testCaseCount"] = int(batch["testCaseCount"])
                    step["testModules"] = list(batch["testModules"])
                    steps.append(step)
                    _progress(
                        f"QUALITY_GATE END {name} status={step['status']} "
                        f"durationMs={step['durationMs']}"
                    )
                    if args.fail_fast and step["exitCode"] != 0:
                        break
                unit_batch_steps = [step for step in steps if step.get("category") == "unit"]
                unit_status = "PASSED" if unit_batch_steps and all(
                    step.get("exitCode") == 0 for step in unit_batch_steps
                ) else "FAILED"
                _progress(
                    f"QUALITY_GATE END unit-batches status={unit_status} "
                    f"completed={len(unit_batch_steps)}/{len(batches)}"
                )

    cleanup_after = clean_transient_report() if args.clean_transient else {"removed": [], "errors": []}
    removed_after = cleanup_after["removed"]
    if cleanup_after["errors"]:
        steps.append(failed_step(
            "cleanup-after",
            "transient cleanup failed",
            category="maintenance",
            error_code="QUALITY_GATE_CLEANUP_FAILED",
            phase="CLEANUP",
        ))
        steps[-1]["errors"] = cleanup_after["errors"]
    status = "PASSED" if all(step["exitCode"] == 0 for step in steps) else "FAILED"
    payload = {
        "status": status,
        "packageVersion": agent_context.RUNTIME_CONTRACT.get("packageVersion"),
        "currentAgent": agent,
        "profile": args.profile,
        "failFast": args.fail_fast,
        "removedTransient": sorted(set(removed_before + removed_after)),
        "discovery": discovery if args.profile in {"fast", "full"} else None,
        "summary": summarize_steps(steps),
        "steps": steps,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for step in steps:
            case_text = f" cases={step.get('testCaseCount')}" if step.get("testCaseCount") is not None else ""
            print(
                f"{step['name']} {step['status']} exit={step['exitCode']} "
                f"durationMs={step.get('durationMs', 0)}{case_text}"
            )
            if step["exitCode"] != 0 and step["output"]:
                print(step["output"].rstrip())
        print(f"QUALITY_GATE {status} summary={json.dumps(payload['summary'], ensure_ascii=False)}")
    return 0 if status == "PASSED" else 1


def main(argv: list[str] | None = None) -> int:
    """Run the gate while preserving a structured result for unexpected faults."""

    try:
        return _main(argv)
    except Exception as exc:
        requested = sys.argv[1:] if argv is None else argv
        payload = {
            "status": "FAILED",
            "errorCode": "QUALITY_GATE_INTERNAL_ERROR",
            "errorType": type(exc).__name__,
            "phase": "QUALITY_GATE",
            "message": str(exc),
            "traceback": traceback.format_exc(limit=3),
        }
        if "--json" in requested:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"QUALITY_GATE FAILED {payload['errorCode']}: {payload['message']}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
