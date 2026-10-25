"""Orchestrator for the internal runtime."""
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

from _check_skill.asset_checks import check_asset_boundary, check_incremental_validity_contract, check_request_intake_contract, check_semantic_contract, check_structured_schemas, check_task_template
from _check_skill.cli import parser
from _check_skill.convergence_checks import check_ai_rule_convergence
from _check_skill.document_checks import check_active_structure, check_current_document_contract
from _check_skill.model import CheckResult
from _check_skill.python_checks import check_python
from _check_skill.reference_checks import check_ai_reference_reading
from _check_skill.release_checks import check_capability_pack_contract, check_release_baseline_contract
from _check_skill.review_checks import check_development_task_plan_contract, check_machine_evidence_contracts
from _check_skill.runtime_checks import check_agents, check_runtime_contract
from _check_skill.mode_combination_checks import check_work_mode_combination_satisfiability
from _check_skill.invariant_checks import check_doc_invariant_drift
from _check_skill.command_permission_checks import check_command_permission_consistency
from _check_skill.structure_checks import check_internal_module_boundaries
from _check_skill.apg4_checks import check_apg4_contract_and_communication
def main_for_args(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        agent = agent_context.authorize_command(args, "check_skill", "check")
    except agent_context.AgentAuthorizationError as exc:
        print(f"ERROR {exc}")
        return 1
    if args.write_manifest:
        write_manifest()
    result = run_checks()
    payload = {
        "status": "PASSED" if not result.errors else "FAILED",
        "currentAgent": agent,
        "checks": result.checks,
        "warnings": result.warnings,
        "errors": result.errors,
        "details": result.details,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for check in result.checks:
            print(f"PASS {check}")
        for warning in result.warnings:
            print(f"WARN {warning}")
        for error in result.errors:
            print(f"ERROR {error}")
        print(f"RESULT {payload['status']} checks={len(result.checks)} warnings={len(result.warnings)} errors={len(result.errors)}")
    return 0 if not result.errors else 1


def run_checks() -> CheckResult:
    result = CheckResult()
    check_python(result)
    check_internal_module_boundaries(result)
    contract, agents = check_runtime_contract(result)
    if contract:
        check_work_mode_combination_satisfiability(contract, result)
        check_command_permission_consistency(contract, result)
        check_incremental_validity_contract(contract, result)
        check_task_template(contract, result)
    check_asset_boundary(result)
    check_routing(result)
    check_agents(agents, result)
    check_structured_schemas(contract, result)
    check_active_structure(contract, result)
    check_current_document_contract(result)
    check_doc_invariant_drift(result)
    check_apg4_contract_and_communication(result)
    check_machine_evidence_contracts(contract, result)
    check_development_task_plan_contract(contract, result)
    check_request_intake_contract(contract, result)
    check_semantic_contract(contract, result)
    check_ai_reference_reading(contract, result)
    check_ai_rule_convergence(contract, result)
    check_capability_pack_contract(contract, result)
    check_release_baseline_contract(contract, result)
    check_manifest(result)
    # Parse the OpenAI interface with the same deterministic YAML subset.
    load_yaml(ROOT / "agents" / "openai.yaml", result)
    return result


def main() -> int:
    return main_for_args()
