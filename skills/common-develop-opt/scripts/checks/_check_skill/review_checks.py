"""Review checks for the internal runtime."""
from __future__ import annotations
from checks._check_skill.layout import checked_path, read_check_text
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

from checks._check_skill.model import CheckResult
def check_machine_evidence_contracts(contract: dict[str, Any] | None, result: CheckResult) -> None:
    evidence_schema = load_json(ROOT / 'assets' / 'review/evidence.schema.json', result)
    review_schema = load_json(ROOT / 'assets' / 'review/review-result.schema.json', result)
    acceptance_schema = load_json(ROOT / 'assets' / 'review/acceptance-assertion.schema.json', result)
    risk_contract = load_json(ROOT / 'assets' / 'runtime/risk-detection-contract.json', result)
    manual_guides = load_json(ROOT / 'assets' / 'review/manual-review-guides.json', result)
    if isinstance(evidence_schema, dict):
        if evidence_schema.get('properties', {}).get('schema_version', {}).get('const') != 4:
            result.errors.append('assets/review/evidence.schema.json: schema_version const must be 4')
        capture_modes = (
            evidence_schema.get('properties', {})
            .get('evidences', {})
            .get('items', {})
            .get('properties', {})
            .get('capture_mode', {})
            .get('enum')
        )
        if not isinstance(capture_modes, list) or set(capture_modes) != {
            'DIRECT', 'SNAPSHOT', 'BUNDLE', 'GIT_REF', 'REVIEW_REF',
        }:
            result.errors.append('assets/review/evidence.schema.json: capture_mode enum is incomplete')
    if isinstance(review_schema, dict):
        expected_review_version = (contract or {}).get('reviewRegistrySchemaVersion')
        if review_schema.get('properties', {}).get('schema_version', {}).get('const') != expected_review_version:
            result.errors.append(
                'assets/review/review-result.schema.json: schema_version const must match '
                f'runtime reviewRegistrySchemaVersion ({expected_review_version})'
            )
    projection_schema = load_json(ROOT / 'assets' / 'development/current-manifest.schema.json', result)
    if isinstance(projection_schema, dict):
        if projection_schema.get('properties', {}).get('schemaVersion', {}).get('const') != 1:
            result.errors.append('assets/development/current-manifest.schema.json: schemaVersion const must be 1')
        if projection_schema.get('properties', {}).get('kind', {}).get('const') != 'CURRENT_MANIFEST':
            result.errors.append('assets/development/current-manifest.schema.json: kind const must be CURRENT_MANIFEST')
    if isinstance(acceptance_schema, dict):
        assertion_item = acceptance_schema.get('properties', {}).get('assertions', {}).get('items', {})
        assertion_type = assertion_item.get('properties', {}).get('type', {}).get('enum')
        required_types = {
            'TEST_CASE', 'COMMAND_EXIT', 'JSON_PATH', 'SQL_VALUE',
            'METRIC_THRESHOLD', 'FILE_PATTERN', 'FILE_DIGEST', 'SET_EQUALS', 'MANUAL_REVIEW',
        }
        if not isinstance(assertion_type, list) or set(assertion_type) != required_types:
            result.errors.append('assets/review/acceptance-assertion.schema.json: assertion type enum is incomplete')
        required_fields = set(assertion_item.get('required', [])) if isinstance(assertion_item, dict) else set()
        for field in ('source_refs', 'evidence_ids', 'revision', 'phase', 'status'):
            if field not in required_fields:
                result.errors.append(
                    f'assets/review/acceptance-assertion.schema.json: assertion required fields missing {field}'
                )
    if isinstance(manual_guides, dict):
        if manual_guides.get('schemaVersion') != 1:
            result.errors.append('assets/review/manual-review-guides.json: schemaVersion must be 1')
        default_guide = manual_guides.get('defaultGuide')
        reviewer_guides = manual_guides.get('reviewerGuides')
        if not isinstance(default_guide, dict) or not isinstance(default_guide.get('questions'), list) or not default_guide.get('questions'):
            result.errors.append('assets/review/manual-review-guides.json: defaultGuide questions are required')
        if not isinstance(reviewer_guides, dict):
            result.errors.append('assets/review/manual-review-guides.json: reviewerGuides must be an object')
            reviewer_guides = {}
        declared_agents = set((contract or {}).get('agents', []))
        unknown_guides = sorted(set(reviewer_guides) - declared_agents)
        if unknown_guides:
            result.errors.append(f'assets/review/manual-review-guides.json: unknown Reviewer guides {unknown_guides}')
        for reviewer, guide in reviewer_guides.items():
            questions = guide.get('questions') if isinstance(guide, dict) else None
            if not isinstance(questions, list) or not questions or len(questions) > 5:
                result.errors.append(f'assets/review/manual-review-guides.json: {reviewer} must define 1..5 questions')
                continue
            ids = []
            for index, question in enumerate(questions):
                if not isinstance(question, dict):
                    result.errors.append(f'assets/review/manual-review-guides.json: {reviewer}.questions[{index}] must be an object')
                    continue
                question_id = question.get('id')
                if not isinstance(question_id, str) or not question_id:
                    result.errors.append(f'assets/review/manual-review-guides.json: {reviewer}.questions[{index}] missing id')
                ids.append(question_id)
                if not isinstance(question.get('text'), str) or not question.get('text'):
                    result.errors.append(f'assets/review/manual-review-guides.json: {reviewer}.questions[{index}] missing text')
                selectors = question.get('criterionPrefixes')
                if not isinstance(selectors, list) or not selectors or any(not isinstance(value, str) or not value for value in selectors):
                    result.errors.append(f'assets/review/manual-review-guides.json: {reviewer}.questions[{index}] criterionPrefixes are invalid')
            if len(ids) != len(set(ids)):
                result.errors.append(f'assets/review/manual-review-guides.json: {reviewer} contains duplicate question ids')
    if isinstance(risk_contract, dict):
        if risk_contract.get('schemaVersion') != 1:
            result.errors.append('assets/runtime/risk-detection-contract.json: schemaVersion must be 1')
        catalog = set((contract or {}).get('riskReviewerCatalog', {}))
        seen_rules: set[str] = set()
        for index, rule in enumerate(risk_contract.get('rules', [])):
            if not isinstance(rule, dict):
                result.errors.append(f'assets/runtime/risk-detection-contract.json: rules[{index}] must be an object')
                continue
            rule_id = rule.get('id')
            if not isinstance(rule_id, str) or not rule_id:
                result.errors.append(f'assets/runtime/risk-detection-contract.json: rules[{index}] missing id')
            elif rule_id in seen_rules:
                result.errors.append(f'assets/runtime/risk-detection-contract.json: duplicate rule id {rule_id}')
            seen_rules.add(str(rule_id))
            if rule.get('riskType') not in catalog:
                result.errors.append(
                    f'assets/runtime/risk-detection-contract.json: rules[{index}] unknown riskType {rule.get("riskType")}'
                )
            confidence = rule.get('confidence')
            if not isinstance(confidence, int) or isinstance(confidence, bool) or not (1 <= confidence <= 10):
                result.errors.append(f'assets/runtime/risk-detection-contract.json: rules[{index}] confidence must be 1..10')
        for index, rule in enumerate(risk_contract.get('traceabilityRules', [])):
            if not isinstance(rule, dict):
                result.errors.append(
                    f'assets/runtime/risk-detection-contract.json: traceabilityRules[{index}] must be an object'
                )
                continue
            rule_id = rule.get('id')
            if not isinstance(rule_id, str) or not rule_id:
                result.errors.append(
                    f'assets/runtime/risk-detection-contract.json: traceabilityRules[{index}] missing id'
                )
            elif rule_id in seen_rules:
                result.errors.append(f'assets/runtime/risk-detection-contract.json: duplicate rule id {rule_id}')
            seen_rules.add(str(rule_id))
            if rule.get('flag') not in {'impact_required', 'cross_module_implementation_required'}:
                result.errors.append(
                    f'assets/runtime/risk-detection-contract.json: traceabilityRules[{index}] invalid flag'
                )
            if rule.get('riskType') not in catalog:
                result.errors.append(
                    f'assets/runtime/risk-detection-contract.json: traceabilityRules[{index}] '
                    f'unknown riskType {rule.get("riskType")}'
                )
            confidence = rule.get('confidence')
            if not isinstance(confidence, int) or isinstance(confidence, bool) or not (1 <= confidence <= 10):
                result.errors.append(
                    f'assets/runtime/risk-detection-contract.json: traceabilityRules[{index}] confidence must be 1..10'
                )
    for relative in (
        'scripts/evidence.py', 'scripts/acceptance.py', 'scripts/risk_detect.py', 'scripts/manual_review.py',
        'assets/review/evidence.schema.json', 'assets/review/review-result.schema.json', 'assets/review/acceptance-assertion.schema.json',
        'assets/runtime/risk-detection-contract.json', 'assets/review/manual-review-guides.json',
    ):
        if not checked_path(ROOT / relative).is_file():
            result.errors.append(f'missing machine verification resource: {relative}')
    for handler in ('command_sync_risk_reviewers', 'validate_acceptance_contract', 'validate_risk_gate'):
        if not callable(getattr(long_task_runtime, handler, None)):
            result.errors.append(f'scripts/long_task.py missing machine gate: {handler}')
    acceptance_text = read_check_text(ROOT / 'scripts' / 'document_core' / 'acceptance.py', encoding='utf-8')
    for token in ('source_refs', 'require_manual_review_conclusion', 'SET_EQUALS'):
        if token not in acceptance_text:
            result.errors.append(f'scripts/acceptance.py missing assertion contract: {token}')
    manual_review_text = read_check_text(ROOT / 'scripts' / 'runtime' / 'manual_review.py', encoding='utf-8')
    for token in ('command_prepare', 'command_draft', 'command_wizard', 'command_submit', 'process_response', 'context_digest'):
        if token not in manual_review_text:
            result.errors.append(f'scripts/manual_review.py missing guided Review contract: {token}')
    risk_text = read_check_text(ROOT / 'scripts' / 'runtime' / 'risk_detect.py', encoding='utf-8')
    for token in ('stable_detection_id', 'add_manual_detection', 'waive_detection', 'traceability_detections'):
        if token not in risk_text:
            result.errors.append(f'scripts/risk_detect.py missing risk contract: {token}')
    if not result.errors:
        result.ok('Evidence, acceptance, risk, and guided manual Review contracts are current')


def check_development_task_plan_contract(contract: dict[str, Any], result: CheckResult) -> None:
    required = [
        "scripts/task_plan.py",
        "assets/development/development-task-plan.schema.json",
        "assets/development/development-task-review.schema.json",
    ]
    for relative in required:
        if not checked_path(ROOT / relative).is_file():
            result.errors.append(f"missing wk -tp resource: {relative}")
    if jsonschema is not None:
        for relative in ("assets/development/development-task-plan.schema.json", "assets/development/development-task-review.schema.json"):
            schema = load_json(ROOT / relative, result)
            if isinstance(schema, dict):
                try:
                    jsonschema.Draft202012Validator.check_schema(schema)
                except jsonschema.SchemaError as exc:
                    result.errors.append(f"{relative} is invalid: {exc.message}")
    plan_schema = load_json(ROOT / "assets" / 'development/development-task-plan.schema.json', result)
    review_schema = load_json(ROOT / "assets" / 'development/development-task-review.schema.json', result)
    if isinstance(plan_schema, dict):
        task_def = ((plan_schema.get("$defs") or {}).get("task") or {})
        task_required = task_def.get("required") or []
        task_properties = task_def.get("properties") or {}
        if "depends_on" not in task_required or "depends_on" not in task_properties:
            result.errors.append("development task schema must require depends_on")
        if "dependencies" in task_required or "dependencies" in task_properties:
            result.errors.append("development task schema retains conflicting dependencies field")
        expected_statuses = ["PENDING", "IN_REVIEW", "REWORK", "BLOCKED", "PASSED"]
        if ((plan_schema.get("properties") or {}).get("status") or {}).get("enum") != expected_statuses:
            result.errors.append("development task plan statuses must reuse the review status domain subset")
        plan_properties = plan_schema.get("properties") or {}
        if (plan_properties.get("implementation_strategy_contract_version") or {}).get("const") != implementation_strategy.CONTRACT_VERSION:
            result.errors.append("development task schema strategy contract version differs from semantic contract")
        implementation_properties = (((task_properties.get("implementation") or {}).get("properties")) or {})
        required_strategy_fields = {
            "implementation_decision_ref", "strategy", "existing_code_refs", "shared_logic_disposition",
            "shared_logic_owner", "compatibility_verifications", "source_doc_refs",
        }
        if not required_strategy_fields.issubset(implementation_properties):
            result.errors.append("development task schema missing implementation strategy fields")
    if isinstance(review_schema, dict):
        expected_results = ["PASSED", "NEEDS_CHANGES", "BLOCKED"]
        if ((review_schema.get("properties") or {}).get("result") or {}).get("enum") != expected_results:
            result.errors.append("development task Review results must reuse reviewConclusion")
        severities = (((((review_schema.get("properties") or {}).get("findings") or {}).get("items") or {}).get("properties") or {}).get("severity") or {}).get("enum")
        if severities != ["P0", "P1", "P2"]:
            result.errors.append("development task findings must use P0/P1/P2 only")
    active_task_plan_paths = [
        ROOT / "SKILL.md", ROOT / "ai" / "README.md", REFERENCES / 'requirement-design/implementation-plan.md',
        ROOT / "scripts" / "task_plan.py",
        ROOT / "assets" / 'development/development-task-plan.schema.json', ROOT / "assets" / 'development/development-task-review.schema.json',
    ]
    active_task_plan_text = "\n".join(read_check_text(path, encoding="utf-8") for path in active_task_plan_paths)
    if "CHANGES_REQUESTED" in active_task_plan_text:
        result.errors.append("wk -tp active contract retains CHANGES_REQUESTED instead of NEEDS_CHANGES/REWORK")
    if '"dependencies"' in active_task_plan_text or "    dependencies:" in active_task_plan_text:
        result.errors.append("wk -tp active contract retains dependencies instead of depends_on")
    try:
        path = ROOT / "scripts" / "task_plan.py"
        spec = importlib.util.spec_from_file_location("common_develop_task_plan_check", path)
        if spec is None or spec.loader is None:
            raise RuntimeError("cannot load task_plan.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        if not agent_context.parser_has_agent_selector(module.build_parser()):
            result.errors.append("scripts/task_plan.py every command must expose -g/--agent")
        expected_reviewers = contract.get("commandInterface", {}).get("taskPlanning", {}).get("reviewPolicy", {})
        if module.STANDARD_REVIEWERS != expected_reviewers.get("standardReviewers"):
            result.errors.append("task_plan.py standard Reviewers differ from runtime contract")
        if module.MINIMAL_REVIEWERS != expected_reviewers.get("minimalReviewers"):
            result.errors.append("task_plan.py minimal Reviewers differ from runtime contract")
    except (OSError, RuntimeError, ValueError) as exc:
        result.errors.append(f"scripts/task_plan.py contract check failed: {exc}")
    source_map = load_json(ROOT / "ai" / "source-map.json", result)
    if isinstance(source_map, dict):
        authoritative = source_map.get("authoritativeSources", {})
        if authoritative.get("developmentTaskPlanScript") != "scripts/task_plan.py":
            result.errors.append("ai/source-map.json must point developmentTaskPlanScript to scripts/task_plan.py")
    required_tokens = {
        REFERENCES / 'requirement-design/implementation-plan.md': ["task_plan.py seal", "task_plan.py revise", "NEEDS_CHANGES", "depends_on", "implementation_decision_ref", "implementation_strategy_contract_version"],
        REFERENCES / 'review-agents/roles.md': ["wk -tp", "TP-*"],
    }
    for path, tokens in required_tokens.items():
        text = read_check_text(path, encoding="utf-8")
        for token in tokens:
            if token not in text:
                result.errors.append(f"{path.relative_to(ROOT)} missing wk -tp contract token: {token}")
    if not result.errors:
        result.ok("wk -tp task generation, serial Agent Review, revision, and freeze contracts are current")
