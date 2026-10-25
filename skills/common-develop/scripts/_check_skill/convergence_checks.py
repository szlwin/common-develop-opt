"""Convergence checks for the internal runtime."""
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

import simple_yaml
import agent_context
import semantic_audit
import reference_reader
import context_resolver
import contract_normalization
import design_doc
import case_doc
import cli_help
import merge_docs
import long_task as long_task_runtime
from _core import capability_packs, implementation_strategy
from _check_skill.context import *
from _check_skill.model import CheckResult
from _check_skill.io import load_json, load_yaml
from _check_skill.module_loader import *
from _check_skill.routing import *
from _check_skill.manifest import *

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None

from _check_skill.model import CheckResult
from _check_skill.python_checks import parser_command_names
from _check_skill.routing import load_materialized_routing
def check_ai_rule_convergence(contract: dict[str, Any], result: CheckResult) -> None:
    """Prevent duplicate, retired, or undiscoverable AI-facing rules from returning."""
    active_paths = [ROOT / "SKILL.md"] + sorted((ROOT / "references").glob("*.md"))
    routing = load_materialized_routing(result)
    if not isinstance(routing, dict):
        return
    runtime = contract if isinstance(contract, dict) else {}
    orchestration = ((runtime.get("commandInterface") or {}).get("orchestration") or {})

    try:
        wk_module = importlib.import_module("wk")
        wk_parser = wk_module.build_parser()
        parser_commands = parser_command_names(wk_parser)
    except Exception as exc:
        result.errors.append(f"cannot inspect wk parser command surface: {exc}")
        parser_commands = set()
    contract_commands = {str(item) for item in orchestration}
    if parser_commands and parser_commands != contract_commands:
        result.errors.append(
            f"wk parser/runtime orchestration mismatch: parser={sorted(parser_commands)} contract={sorted(contract_commands)}"
        )

    permissions = runtime.get("scriptPermissions") if isinstance(runtime.get("scriptPermissions"), dict) else {}
    missing_permissions = sorted(
        command for command in contract_commands if f"wk.{command}" not in permissions
    )
    if missing_permissions:
        result.errors.append(f"wk orchestration commands missing runtime permission: {missing_permissions}")

    expected_routes = {
        "wk_context": ["wk-cli", "lifecycle-core"],
        "wk_reconcile": ["wk-cli", "lifecycle-core"],
        "wk_close": ["wk-cli", "lifecycle-core"],
        "wk_impact": ["wk-cli", "review-evidence"],
        "wk_env": ["wk-cli", "test-environment"],
    }
    routes = routing.get("routes") if isinstance(routing.get("routes"), list) else []
    for task_type, expected_resources in expected_routes.items():
        matches = [route for route in routes if task_type in (route.get("taskTypes") or [])]
        if len(matches) != 1 or matches[0].get("resources") != expected_resources:
            result.errors.append(f"context routing for {task_type} must be exactly {expected_resources}")

    projection = ((runtime.get("taskContextOptimization") or {}).get("projection") or {})
    if projection.get("physicalFileBudget") != 1 or projection.get("kind") != "CURRENT_MANIFEST":
        result.errors.append("task context projection must be the single current manifest")

    reason_module = ROOT / "scripts" / "_long_task" / "reason_codes.py"
    impact_module = ROOT / "scripts" / "impact_analysis.py"
    if reason_module.is_file() and impact_module.is_file():
        reason_text = reason_module.read_text(encoding="utf-8")
        impact_text = impact_module.read_text(encoding="utf-8")
        names = re.findall(r'^([A-Z][A-Z0-9_]+)\s*=\s*"([A-Z][A-Z0-9_]+)"', reason_text, re.MULTILINE)
        duplicates = [value for name, value in names if f'"{value}"' in impact_text]
        if duplicates:
            result.errors.append(f"impact planner duplicates shared reason-code literals: {sorted(set(duplicates))}")

    if not result.errors:
        result.ok("AI rule convergence, command discovery, focused routing, projection, and reason-code sources agree")
