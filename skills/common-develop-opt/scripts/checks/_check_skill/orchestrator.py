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

from checks._check_skill.asset_checks import check_asset_boundary, check_incremental_validity_contract, check_request_intake_contract, check_semantic_contract, check_structured_schemas, check_task_template
from checks._check_skill.cli import parser
from checks._check_skill.convergence_checks import check_ai_rule_convergence
from checks._check_skill.document_checks import check_active_structure, check_current_document_contract
from checks._check_skill.model import CheckResult
from checks._check_skill.python_checks import check_python
from checks._check_skill.reference_checks import check_agent_json_read_boundary, check_ai_reference_reading
from checks._check_skill.release_checks import check_capability_pack_contract, check_release_baseline_contract, check_command_surface_inventory
from checks._check_skill.review_checks import check_development_task_plan_contract, check_machine_evidence_contracts
from checks._check_skill.runtime_checks import check_agents, check_runtime_contract
from checks._check_skill.mode_combination_checks import check_work_mode_combination_satisfiability
from checks._check_skill.invariant_checks import check_doc_invariant_drift
from checks._check_skill.command_permission_checks import check_command_permission_consistency
from checks._check_skill.structure_checks import check_internal_module_boundaries
from checks._check_skill.apg4_checks import check_apg4_contract_and_communication
from checks._check_skill.classified_resources import check_classified_resources
def main_for_args(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        agent = agent_context.authorize_command(args, "check_skill", "check")
    except agent_context.AgentAuthorizationError as exc:
        print(f"ERROR {exc}")
        return 1
    try:
        if args.write_manifest:
            write_manifest()
        result = run_checks(profile=args.profile, command_surface_inventory=args.command_surface_inventory)
    except Exception as exc:
        result = CheckResult()
        result.errors.append(f'CHECKER_EXECUTION_FAILED: {type(exc).__name__}: {exc}')
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


def run_checks(*, profile='development', command_surface_inventory=None) -> CheckResult:
    result = CheckResult()
    def check(function, *args, fallback=None):
        try:
            return function(*args, result)
        except Exception as exc:
            result.errors.append(f'{function.__name__}: CHECKER_EXECUTION_FAILED: {type(exc).__name__}: {exc}')
            return fallback
    check(check_python)
    check(check_internal_module_boundaries)
    contract, agents = check(check_runtime_contract, fallback=({}, []))
    if contract:
        check(check_work_mode_combination_satisfiability, contract)
        check(check_command_permission_consistency, contract)
        check(check_incremental_validity_contract, contract)
        check(check_task_template, contract)
    check(check_asset_boundary)
    check(check_classified_resources)
    check(check_routing)
    check(check_agents, agents)
    check(check_structured_schemas, contract)
    check(check_active_structure, contract)
    check(check_current_document_contract)
    check(check_doc_invariant_drift)
    check(check_apg4_contract_and_communication)
    check(check_machine_evidence_contracts, contract)
    check(check_development_task_plan_contract, contract)
    check(check_request_intake_contract, contract)
    check(check_semantic_contract, contract)
    check(check_ai_reference_reading, contract)
    check(check_agent_json_read_boundary, contract)
    check(check_ai_rule_convergence, contract)
    check(check_capability_pack_contract, contract)
    if command_surface_inventory is not None:
        check(check_command_surface_inventory, command_surface_inventory)
    check(check_release_baseline_contract, contract)
    from checks._check_skill.package_identity import validate_identity
    result.errors.extend(validate_identity(ROOT, profile=profile))
    result.details['validationProfile'] = profile
    if profile == 'release':
        try:
            from checks import baseline_guard
            baseline = baseline_guard.inspect_baseline(profile='release')
            result.details['releaseBaseline'] = baseline
            if baseline['status'] not in baseline_guard.CLEAN_STATUSES:
                result.errors.append('RELEASE_BASELINE_NOT_CLEAN')
        except (ValueError, OSError) as exc:
            result.errors.append(f'RELEASE_BASELINE_INVALID: {exc}')
    check(check_manifest)
    # Parse the OpenAI interface with the same deterministic YAML subset.
    load_yaml(ROOT / "agents" / "openai.yaml", result)
    return result


def main() -> int:
    return main_for_args()
