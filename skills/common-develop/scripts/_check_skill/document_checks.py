"""Document checks for the internal runtime."""
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
from _check_skill.check_context import CheckFlowContext
from _check_skill.io import load_json, load_yaml
from _check_skill.module_loader import *
from _check_skill.routing import *
from _check_skill.manifest import *
try:
    import jsonschema
except ImportError:
    jsonschema = None
from _check_skill.model import CheckResult
from _check_skill.python_checks import parser_command_names

def active_markdown_paths() -> list[Path]:
    return [ROOT / 'SKILL.md', *sorted((path for path in REFERENCES.rglob('*.md')))]

def normalized_paragraphs(path: Path) -> list[str]:
    paragraphs: list[str] = []
    buffer: list[str] = []
    in_code = False
    for line in path.read_text(encoding='utf-8').splitlines() + ['']:
        if line.startswith('```'):
            if buffer:
                paragraphs.append(' '.join(buffer))
                buffer = []
            in_code = not in_code
            continue
        if in_code:
            continue
        stripped = line.strip()
        boundary = not stripped or stripped.startswith(('#', '|', '>', '- ', '* ', '+ ')) or bool(re.match('^\\d+[.)、]\\s*', stripped))
        if boundary:
            if buffer:
                paragraphs.append(' '.join(buffer))
                buffer = []
        else:
            buffer.append(stripped)
    return [re.sub('\\s+', ' ', value).strip() for value in paragraphs if len(value) >= 160]

def normalized_fenced_blocks(path: Path) -> list[tuple[int, str]]:
    """Return stable long fenced blocks for exact duplicate detection."""
    values: list[tuple[int, str]] = []
    text = path.read_text(encoding='utf-8')
    for (index, match) in enumerate(re.finditer('```[^\\n]*\\n(.*?)\\n```', text, re.DOTALL), 1):
        value = '\n'.join((line.rstrip() for line in match.group(1).strip().splitlines()))
        if len(value) >= 180:
            values.append((index, value))
    return values

def _check_active_structure_section_1(ctx: CheckFlowContext) -> None:
    contract = ctx.contract
    result = ctx.result
    expected = None
    key = None
    overlap = None
    path = None
    present = None
    resource_id = None
    routing_contract = None
    semantic_contract = None
    skill = None
    source_map = None
    forbidden_runtime_files = {'INTEGRATION_REPORT.md', 'ARCHIVE_APPROVAL_UPDATE.md'}
    present = sorted((name for name in forbidden_runtime_files if (ROOT / name).exists()))
    if present:
        result.errors.append(f'historical delivery reports must stay outside runtime Skill: {present}')
    skill = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
    package_version = str(contract.get('packageVersion') or '')
    if 'assets/release-contract.json' not in skill:
        result.errors.append('SKILL.md must delegate package version and release stage to release-contract.json')
    if re.search('当前 Skill 包版本：`[^`]+`', skill) or re.search('（RC\\d+）', skill):
        result.errors.append('SKILL.md must not duplicate the current package version or RC stage')
    semantic_contract = load_json(ASSETS / 'semantic-contract.json', result)
    if isinstance(semantic_contract, dict) and semantic_contract.get('packageVersion') != package_version:
        result.errors.append('assets/semantic-contract.json packageVersion must match runtime contract')
    task_verify_text = (ROOT / 'scripts' / 'task_verify.py').read_text(encoding='utf-8')
    if 'VALIDATOR_VERSION = str(agent_context.RUNTIME_CONTRACT.get("packageVersion") or "")' not in task_verify_text:
        result.errors.append('task_verify.py validatorVersion must derive from runtime packageVersion')
    required_sections = ['AI 必须先执行', '唯一事实与冲突顺序', '生命周期与状态', '最小上下文路由', '全局硬门禁', '条件触发器', 'Review 与阶段结果', '长任务', 'wk 命令发现', 'Agent 输出契约', '交付前自检']
    for section in required_sections:
        if section not in skill:
            result.errors.append(f'SKILL.md missing AI entry section: {section}')
    skill_lines = len(skill.splitlines())
    if skill_lines > 90:
        result.errors.append(f'SKILL.md must remain a compact bootstrap of at most 90 lines; found {skill_lines}')
    if '├──' in skill or '└──' in skill:
        result.errors.append('SKILL.md must point to long-task contracts instead of copying directory trees')
    stage_result_schema = ASSETS / 'stage-result.schema.json'
    if not stage_result_schema.is_file():
        result.errors.append('missing assets/stage-result.schema.json')
    elif 'assets/stage-result.schema.json' not in skill:
        result.errors.append('SKILL.md must point Agent output to stage-result.schema.json')
    if 'python3 scripts/quality_gate.py' not in skill:
        result.errors.append('SKILL.md does not use the canonical quality_gate.py delivery entry')
    if 'python3 scripts/ai_context.py resolve' not in skill:
        result.errors.append('SKILL.md does not expose the on-demand AI context diagnostic entry')
    for token in ('新建任务', '恢复任务', '普通执行', '维护诊断', 'wk begin', '仅在修改本 Skill 源时'):
        if token not in skill:
            result.errors.append(f'SKILL.md missing scoped bootstrap rule: {token}')
    if 'python3 scripts/wk.py help' not in skill or 'assets/runtime-contract.json#commandInterface' not in skill:
        result.errors.append('SKILL.md must expose compact wk discovery and keep Runtime Contract as the command source')
    for required_flag in ('-b/--bug', '-f/--feature', '-mc', '-v', '-min', '-auto', '-ar', '-gc', '-ws', '-wd', '-wc', '-tp/--task-plan', '-l', '-d'):
        if required_flag not in skill:
            result.errors.append(f'SKILL.md wk discovery index missing: {required_flag}')
    ai_readme = ROOT / 'ai' / 'README.md'
    ai_source_map = ROOT / 'ai' / 'source-map.json'
    ai_script = ROOT / 'scripts' / 'ai_context.py'
    for path in (ai_readme, ai_source_map, ai_script):
        if not path.is_file():
            result.errors.append(f'missing AI entry artifact: {path.relative_to(ROOT)}')
    ai_readme_text = ai_readme.read_text(encoding='utf-8') if ai_readme.is_file() else ''
    if len(ai_readme_text.splitlines()) > 70:
        result.errors.append('ai/README.md must remain a compact bootstrap index of at most 70 lines')
    for forbidden_heading in ('## 业务流程', '## 大需求任务规划', '## Git 阶段检查点'):
        if forbidden_heading in ai_readme_text:
            result.errors.append(f'ai/README.md must point to routed rules instead of redefining them: {forbidden_heading}')
    if 'manifest/current/views' in ai_readme_text:
        result.errors.append('ai/README.md retains the retired manifest/current/views projection layout')
    if 'cache/current_manifest.json' not in ai_readme_text:
        result.errors.append('ai/README.md missing current manifest path')
    agent_interface = load_yaml(ROOT / 'agents' / 'openai.yaml', result)
    if isinstance(agent_interface, dict):
        prompt = str((agent_interface.get('interface') or {}).get('default_prompt') or '')
        if not prompt or len(prompt) > 600:
            result.errors.append('agents/openai.yaml default_prompt must be a compact bootstrap pointer of at most 600 characters')
        for duplicated_rule_token in ('publish-artifact', 'finalize-phase', 'reviews.jsonl', 'TEST_DB_URL', 'PlanReviewAgent', 'FLOW/STEP'):
            if duplicated_rule_token in prompt:
                result.errors.append(f'agents/openai.yaml default_prompt duplicates routed runtime rules: {duplicated_rule_token}')
        for required_pointer in ('wk start', 'wk begin', 'progress', '-gp', '-gc', 'scripts/ai_context.py resolve', 'ai/source-map.json'):
            if required_pointer not in prompt:
                result.errors.append(f'agents/openai.yaml default_prompt missing bootstrap pointer: {required_pointer}')
    source_map = load_json(ai_source_map, result) if ai_source_map.is_file() else None
    if isinstance(source_map, dict):
        if source_map.get('kind') != 'INDEX_ONLY':
            result.errors.append('ai/source-map.json must be INDEX_ONLY')
        expected_sources = {'runtime': 'assets/runtime-contract.json', 'review': 'assets/review-contract.json', 'routing': 'context-routing.json', 'semantic': 'assets/semantic-contract.json', 'longTaskRecords': 'assets/long-task/record-contract.json', 'referenceReader': 'scripts/reference_reader.py', 'runtimeQuery': 'scripts/runtime_contract.py', 'routingQuery': 'scripts/context_routing.py', 'reviewQuery': 'scripts/review_contract.py', 'contractMaterializer': 'scripts/contract_normalization.py', 'wkHelp': 'scripts/wk.py help'}
        actual_sources = source_map.get('authoritativeSources', {})
        for (key, expected) in expected_sources.items():
            if actual_sources.get(key) != expected:
                result.errors.append(f'ai/source-map.json authoritative source {key} must be {expected}')
        if actual_sources.get('stageResult') != 'assets/stage-result.schema.json':
            result.errors.append('ai/source-map.json must point stageResult to assets/stage-result.schema.json')
        if actual_sources.get('businessFlow') != 'docs/_flows/{FLOW_GROUP}_flow.yaml':
            result.errors.append('ai/source-map.json must point businessFlow to project flow YAML')
        if actual_sources.get('crossModuleImplementation') != 'docs/_relations/dependency_impact.yaml#crossModuleImplementations':
            result.errors.append('ai/source-map.json must point crossModuleImplementation to dependency-impact mapping')
        expected_storage_pointers = {'workRecord': 'version/{VERSION}/work_record.md', 'evidenceRegistry': 'version/{VERSION}/task/{TARGET_ID}/evidence/registry.jsonl', 'reviewRegistry': 'version/{VERSION}/task/{TARGET_ID}/evidence/reviews.jsonl', 'currentManifest': 'version/{VERSION}/task/{TARGET_ID}/cache/current_manifest.json'}
        if source_map.get('currentStoragePointers') != expected_storage_pointers:
            result.errors.append('ai/source-map.json currentStoragePointers are incomplete or stale')
        if source_map.get('storageModelRef') != 'assets/storage-contract.json#/modelVersion':
            result.errors.append('ai/source-map.json storageModelRef must point to storage contract modelVersion')
        review_policy_source = (source_map.get('sources') or {}).get('reviewPolicyCatalog')
        if review_policy_source != {'machine': 'assets/review-contract.json#/evidencePolicyCatalog', 'profileTemplates': 'assets/review-contract.json#/profileTemplates', 'riskReviewTemplates': 'assets/review-contract.json#/riskReviewTemplates', 'reviewSessionPolicy': 'assets/review-contract.json#/reviewSessionPolicy', 'query': 'scripts/review_contract.py'}:
            result.errors.append('ai/source-map.json reviewPolicyCatalog pointers are incomplete or stale')
        contract_queries = (source_map.get('sources') or {}).get('contractQueries')
        if not isinstance(contract_queries, dict) or contract_queries.get('runtime') != 'scripts/runtime_contract.py' or contract_queries.get('routing') != 'scripts/context_routing.py' or (contract_queries.get('review') != 'scripts/review_contract.py') or (contract_queries.get('wkHelp') != 'scripts/wk.py help'):
            result.errors.append('ai/source-map.json contract query pointers are incomplete')
        normalization_source = (source_map.get('sources') or {}).get('contractNormalization')
        if normalization_source != {'schemaVersion': 2, 'materializer': 'scripts/contract_normalization.py', 'runtime': 'scripts/runtime_contract.py', 'review': 'scripts/review_contract.py', 'routing': 'scripts/context_routing.py', 'policy': 'assets/runtime-contract.json#/contractQueryPolicy'}:
            result.errors.append('ai/source-map.json contractNormalization pointers are incomplete or stale')
        do_not_load = set(source_map.get('doNotLoadByDefault') or [])
        required_large_contract_blocks = {'entire assets/runtime-contract.json', 'entire assets/review-contract.json', 'entire context-routing.json'}
        if not required_large_contract_blocks.issubset(do_not_load):
            result.errors.append('ai/source-map.json must forbid loading all three large contracts by default')
        forbidden_map_keys = {'canonicalPhases', 'statuses', 'agents', 'scriptPermissions', 'workModes'}
        overlap = sorted(forbidden_map_keys & set(source_map))
        if overlap:
            result.errors.append(f'ai/source-map.json must not duplicate runtime matrices: {overlap}')
    if 'resourceCatalog' not in (ROOT / 'context-routing.json').read_text(encoding='utf-8'):
        result.errors.append('context-routing.json has no unified resourceCatalog')
    raw_routing_contract = load_json(ROOT / 'context-routing.json', result)
    routing_contract = None
    if isinstance(raw_routing_contract, dict):
        if raw_routing_contract.get('schemaVersion') != 2:
            result.errors.append('context-routing.json: schemaVersion must be 2')
        if raw_routing_contract.get('resourceDefaults') != {'pathTemplate': 'references/{id}.md', 'readStrategy': 'full'}:
            result.errors.append('context-routing.json: resourceDefaults are invalid')
        raw_catalog = raw_routing_contract.get('resourceCatalog') or {}
        for (resource_id, definition) in raw_catalog.items():
            if not isinstance(definition, dict):
                continue
            if definition.get('path') == f'references/{resource_id}.md':
                result.errors.append(f'context-routing resource {resource_id} redundantly persists its conventional path')
            if definition.get('readStrategy') == 'full':
                result.errors.append(f'context-routing resource {resource_id} redundantly persists default readStrategy')
        raw_policy = raw_routing_contract.get('routingPolicy') or {}
        if 'baseRouteTokenBudgetTargets' in raw_policy:
            result.errors.append('context-routing.json: normalized v2 must use baseRouteBudgetGroups')
        try:
            routing_contract = contract_normalization.materialize_context_routing(raw_routing_contract)
        except contract_normalization.ContractNormalizationError as exc:
            result.errors.append(f'context-routing.json: normalization failed: {exc}')
    ctx.update(expected=expected, key=key, overlap=overlap, path=path, present=present, resource_id=resource_id, routing_contract=routing_contract, semantic_contract=semantic_contract, skill=skill, source_map=source_map)

def _check_active_structure_section_2(ctx: CheckFlowContext) -> None:
    contract = ctx.contract
    expected = ctx.get('expected')
    key = ctx.get('key')
    resource_id = ctx.get('resource_id')
    result = ctx.result
    routing_contract = ctx.get('routing_contract')
    actual = None
    catalog = None
    condition = None
    field = None
    trace_fields = None
    if isinstance(routing_contract, dict):
        wk_v_routes = [route for route in routing_contract.get('routes', []) if isinstance(route, dict) and 'wk_v' in (route.get('taskTypes') or [])]
        if len(wk_v_routes) != 1 or 'page-design-core' not in (wk_v_routes[0].get('resources') if wk_v_routes else []):
            result.errors.append('context-routing.json must route wk_v exactly once to page-design-core')
        page_conditions = [condition for condition in routing_contract.get('promptConditions', []) if isinstance(condition, dict) and condition.get('id') == 'page-design']
        if len(page_conditions) != 1 or 'wk -v' not in page_conditions[0].get('keywords', []):
            result.errors.append('context-routing.json must have one explicit wk -v page-design condition')
        for condition in routing_contract.get('promptConditions', []):
            if isinstance(condition, dict) and condition.get('id') == 'minimal':
                if '跳过页面设计' in condition.get('keywords', []):
                    result.errors.append('minimal routing retains retired page-design suppression keyword')
        catalog = routing_contract.get('resourceCatalog', {})
        if catalog.get('semantic-contract', {}).get('path') != 'assets/semantic-contract.json':
            result.errors.append('context-routing.json must catalog assets/semantic-contract.json')
        expected_fixed = ['execution-core']
        if routing_contract.get('fixedResources') != expected_fixed:
            result.errors.append('context-routing.json fixedResources must use the single compact execution-core bootstrap')
        routing_policy = routing_contract.get('routingPolicy') or {}
        expected_policy_values = {'targetResolvedTokenBudget': 6500, 'reservedTaskFactTokenBudget': 5500, 'maxFixedTokenBudget': 1000, 'maxBaseRouteTokenBudget': 2500, 'maxMatchedPromptConditions': 2}
        for (key, expected) in expected_policy_values.items():
            if routing_policy.get(key) != expected:
                result.errors.append(f'context-routing.json routingPolicy {key} must be {expected}')
        fixed_budget = sum((int((catalog.get(resource_id) or {}).get('tokenBudget') or 0) for resource_id in routing_contract.get('fixedResources') or []))
        if fixed_budget > int(routing_policy.get('maxFixedTokenBudget') or 0):
            result.errors.append('context-routing.json fixed resources exceed maxFixedTokenBudget')
        optimized_types = set(routing_policy.get('optimizedBaseRouteTaskTypes') or [])
        max_base_budget = int(routing_policy.get('maxBaseRouteTokenBudget') or 0)
        route_targets = dict(routing_policy.get('baseRouteTokenBudgetTargets') or {})
        for task_type in optimized_types:
            matching = [route for route in routing_contract.get('routes', []) if isinstance(route, dict) and task_type in (route.get('taskTypes') or [])]
            if len(matching) != 1:
                result.errors.append(f'optimized context route must resolve exactly once: {task_type}')
                continue
            base_budget = fixed_budget + sum((int((catalog.get(resource_id) or {}).get('tokenBudget') or 0) for resource_id in matching[0].get('resources') or []))
            target = int(route_targets.get(task_type) or max_base_budget)
            if base_budget > max_base_budget or base_budget > target:
                result.errors.append(f'optimized base route exceeds budget: {task_type} budget={base_budget} max={max_base_budget} target={target}')
        required_dynamic_resources = {'execution-core', 'orchestration-core', 'requirement-confirmation-core', 'tdd-core', 'completion-gate-core', 'html-core', 'work-mode-core', 'wk-ar-state-core', 'archive-core', 'request-intake-core', 'debugging-core', 'development-core', 'backend-core', 'frontend-core', 'implementation-contract-summary', 'design-core', 'business-model-summary', 'enum-summary', 'business-flow-core', 'implementation-plan-core', 'auto-mode-core', 'task-verification-core', 'evidence-core', 'git-checkpoint-core', 'model-code-core', 'page-design-core', 'requirement-traceability-summary'}
        missing_dynamic = sorted(required_dynamic_resources - set(catalog))
        if missing_dynamic:
            result.errors.append(f'context-routing.json missing compact routing resources: {missing_dynamic}')
        if catalog.get('business-flow', {}).get('path') != 'references/business-flow.md':
            result.errors.append('context-routing.json must catalog references/business-flow.md')
        business_flow_routes = [route for route in routing_contract.get('routes', []) if isinstance(route, dict) and 'business_flow' in (route.get('taskTypes') or [])]
        if len(business_flow_routes) != 1 or 'business-flow-core' not in business_flow_routes[0].get('resources', []):
            result.errors.append('context-routing.json must route business_flow exactly once to business-flow-core')
        business_flow_conditions = [condition for condition in routing_contract.get('promptConditions', []) if isinstance(condition, dict) and condition.get('id') == 'business-flow']
        if len(business_flow_conditions) != 1 or '业务流程' not in business_flow_conditions[0].get('keywords', []):
            result.errors.append('context-routing.json must have one business-flow prompt condition')
        golden_path = TEST_FIXTURES / 'context-routing-golden.json'
        golden = load_json(golden_path, result) if golden_path.is_file() else None
        if isinstance(golden, dict):
            scenarios = golden.get('scenarios')
            if golden.get('schemaVersion') != 1 or not isinstance(scenarios, list) or (not scenarios):
                result.errors.append('tests/fixtures/context-routing-golden.json must contain non-empty schemaVersion 1 scenarios')
            else:
                seen_golden_ids: set[str] = set()
                for scenario in scenarios:
                    if not isinstance(scenario, dict):
                        result.errors.append('context routing golden scenario must be an object')
                        continue
                    scenario_id = str(scenario.get('id') or '')
                    if not scenario_id or scenario_id in seen_golden_ids:
                        result.errors.append(f'context routing golden scenario id is missing or duplicated: {scenario_id}')
                        continue
                    seen_golden_ids.add(scenario_id)
                    mode_data = scenario.get('mode') or {}
                    try:
                        resolved = context_resolver.resolve_context(str(scenario.get('taskType') or ''), prompt=str(scenario.get('prompt') or ''), mode=context_resolver.ResolvedMode(model_code=bool(mode_data.get('modelCode')), page_design=bool(mode_data.get('pageDesign')), minimal=bool(mode_data.get('minimal')), auto=bool(mode_data.get('auto')), architecture_review=bool(mode_data.get('architectureReview')), git_checkpoint=bool(mode_data.get('gitCheckpoint')), lightweight=bool(mode_data.get('lightweight'))), routing=routing_contract)
                    except (TypeError, ValueError) as exc:
                        result.errors.append(f'context routing golden scenario {scenario_id} cannot resolve: {exc}')
                        continue
                    expected_pairs = (('expectedConditionIds', resolved.get('matchedConditionIds')), ('expectedResourceIds', resolved.get('resourceIds')), ('expectedTotalTokenBudget', resolved.get('totalTokenBudget')), ('expectedOmittedIds', [item.get('id') for item in resolved.get('omittedResources', [])]))
                    for (field, actual) in expected_pairs:
                        if scenario.get(field) != actual:
                            result.errors.append(f'context routing golden scenario {scenario_id} {field} is stale: expected={scenario.get(field)!r} actual={actual!r}')
    for required in (REFERENCES / 'business-flow.md', STRUCTURED / 'business-flow.schema.json', STRUCTURED / 'business-flow.changeset.schema.json', STRUCTURED_FIXTURES / 'business-flow.example.yaml', ROOT / 'scripts' / 'business_flow.py', ROOT / 'tests' / 'test_business_flow.py', ASSETS / 'semantic-contract.json', ROOT / 'scripts' / 'semantic_audit.py', ROOT / 'tests' / 'test_semantic_audit.py', REFERENCES / 'git-checkpoint.md', ASSETS / 'git-checkpoint.schema.json', ASSETS / 'long-task' / 'git_checkpoints.yaml', ROOT / 'scripts' / 'git_checkpoint.py', ROOT / 'tests' / 'test_git_checkpoint.py', REFERENCES / 'task-context-optimization.md', ROOT / 'scripts' / 'task_verify.py', ROOT / 'scripts' / 'task_layout.py', ROOT / 'tests' / 'test_task_verify.py', ROOT / 'tests' / 'test_task_layout.py', ROOT / 'scripts' / 'work_record.py', ROOT / 'tests' / 'test_work_record.py', ROOT / 'scripts' / 'request_intake.py', ROOT / 'tests' / 'test_request_intake.py', ROOT / 'scripts' / 'wk.py', ROOT / 'tests' / 'test_wk.py', ASSETS / 'request-intake.schema.json', ASSETS / 'release-contract.json', ASSETS / 'stage-result.schema.json', TEST_FIXTURES / 'context-routing-golden.json', REFERENCES / 'request-intake.md', REFERENCES / 'wk-cli.md', ROOT / 'CHANGELOG.md', ASSETS / 'task-verification-result.schema.json', ASSETS / 'current-manifest.schema.json'):
        if not required.is_file():
            result.errors.append(f'missing business-flow artifact: {required.relative_to(ROOT)}')
    permissions = contract.get('scriptPermissions', {})
    for command in ('business_flow.init', 'business_flow.validate', 'business_flow.render', 'business_flow.impact'):
        if command not in permissions:
            result.errors.append(f'runtime contract missing script permission: {command}')
    if 'semantic_audit.check' not in permissions:
        result.errors.append('runtime contract missing script permission: semantic_audit.check')
    for command in ('git_checkpoint.commit', 'git_checkpoint.push', 'git_checkpoint.validate', 'git_checkpoint.status'):
        if command not in permissions:
            result.errors.append(f'runtime contract missing script permission: {command}')
    for command in ('task_verify.verify', 'task_layout.sync', 'task_layout.validate'):
        if command not in permissions:
            result.errors.append(f'runtime contract missing script permission: {command}')
    if 'long_task.validate-work-record' not in permissions:
        result.errors.append('runtime contract missing script permission: long_task.validate-work-record')
    checkpoint_schema = load_json(ASSETS / 'git-checkpoint.schema.json', result)
    if isinstance(checkpoint_schema, dict):
        schema_version = checkpoint_schema.get('properties', {}).get('schema_version')
        checkpoint_item = checkpoint_schema.get('properties', {}).get('checkpoints', {}).get('items', {})
        checkpoint_properties = checkpoint_item.get('properties', {}) if isinstance(checkpoint_item, dict) else {}
        if schema_version != {'const': 2}:
            result.errors.append('git-checkpoint.schema.json schema_version must be 2')
        for redundant in ('decision', 'commit_locator'):
            if redundant in checkpoint_properties:
                result.errors.append(f'git-checkpoint.schema.json retains redundant field: {redundant}')
    trace_fields = load_json(ASSETS / 'long-task' / 'record-contract.json', result)
    ctx.update(actual=actual, catalog=catalog, condition=condition, expected=expected, field=field, key=key, trace_fields=trace_fields)

def _check_active_structure_section_3(ctx: CheckFlowContext) -> None:
    catalog = ctx.get('catalog')
    condition = ctx.get('condition')
    contract = ctx.contract
    expected = ctx.get('expected')
    field = ctx.get('field')
    key = ctx.get('key')
    path = ctx.get('path')
    present = ctx.get('present')
    result = ctx.result
    routing_contract = ctx.get('routing_contract')
    skill = ctx.get('skill')
    trace_fields = ctx.get('trace_fields')
    active_references = None
    label = None
    line = None
    lineno = None
    matches = None
    pattern = None
    if isinstance(trace_fields, dict):
        trace_allowed = trace_fields.get('records', {}).get('traceabilityItem', {}).get('allowedFields', [])
        task_allowed = trace_fields.get('records', {}).get('taskPlanItem', {}).get('allowedFields', [])
        for field in ('business_flow_required', 'business_flow_refs', 'cross_module_implementation_required', 'cross_module_implementation_refs'):
            if field not in trace_allowed:
                result.errors.append(f'traceability contract missing {field}')
        for field in ('flow_refs', 'flow_step_refs'):
            if field not in task_allowed:
                result.errors.append(f'task plan contract missing {field}')
        if 'parallel_group' in task_allowed:
            result.errors.append('task plan contract retains retired parallel_group')
    if isinstance(routing_contract, dict):
        catalog = routing_contract.get('resourceCatalog', {})
        if catalog.get('git-checkpoint', {}).get('path') != 'references/git-checkpoint.md':
            result.errors.append('context-routing.json must catalog references/git-checkpoint.md')
        ar_routes = [route for route in routing_contract.get('routes', []) if isinstance(route, dict) and 'wk_ar' in (route.get('taskTypes') or [])]
        if len(ar_routes) != 1 or ar_routes[0].get('resources') != ['wk-ar-state-core', 'review-result-contract']:
            result.errors.append('context-routing.json must route wk_ar exactly once to the compact architecture Review state guidance')
        git_routes = [route for route in routing_contract.get('routes', []) if isinstance(route, dict) and 'wk_gc' in (route.get('taskTypes') or [])]
        if len(git_routes) != 1 or 'git-checkpoint-core' not in git_routes[0].get('resources', []):
            result.errors.append('context-routing.json must route wk_gc exactly once to git-checkpoint-core')
        push_routes = [route for route in routing_contract.get('routes', []) if isinstance(route, dict) and 'wk_gp' in (route.get('taskTypes') or [])]
        if len(push_routes) != 1 or 'git-checkpoint-core' not in push_routes[0].get('resources', []):
            result.errors.append('context-routing.json must route wk_gp exactly once to git-checkpoint-core')
        git_push_routes = [route for route in routing_contract.get('routes', []) if isinstance(route, dict) and 'git_push' in (route.get('taskTypes') or [])]
        if len(git_push_routes) != 1 or git_push_routes[0] is not push_routes[0]:
            result.errors.append('context-routing.json must route git_push with wk_gp')
        git_conditions = [condition for condition in routing_contract.get('promptConditions', []) if isinstance(condition, dict) and condition.get('id') == 'git-checkpoint']
        if len(git_conditions) != 1 or 'wk -gc' not in git_conditions[0].get('keywords', []):
            result.errors.append('context-routing.json must have one explicit wk -gc condition')
        push_conditions = [condition for condition in routing_contract.get('promptConditions', []) if isinstance(condition, dict) and condition.get('id') == 'git-push']
        if len(push_conditions) != 1 or 'wk -gp' not in push_conditions[0].get('keywords', []):
            result.errors.append('context-routing.json must have one explicit wk -gp condition')
        override = routing_contract.get('modeOverrides', {}).get('gitCheckpoint', {})
        if override.get('executionPolicyRef') != 'assets/runtime-contract.json#/executionPolicy':
            result.errors.append('context-routing.json gitCheckpoint override must reference the canonical execution policy')
        push_override = routing_contract.get('modeOverrides', {}).get('gitPush', {})
        if push_override.get('executionPolicyRef') != 'assets/runtime-contract.json#/executionPolicy':
            result.errors.append('context-routing.json gitPush override must reference the canonical execution policy')
        if push_override.get('requiresMode') != 'gitCheckpoint':
            result.errors.append('context-routing.json gitPush override must require gitCheckpoint')
    scope_text = (REFERENCES / 'scope.md').read_text(encoding='utf-8')
    if '`wk -min` 时当前版本跳过' in scope_text:
        result.errors.append('scope.md retains the retired rule that wk -min disables page design')
    required_references = ('code-comments.md', 'relationship-impact.md', 'cross-module-impact.md', 'implementation-contract.md', 'onepage-html.md', 'review-core.md', 'architecture-review-development.md')
    for required_reference in required_references:
        if not (REFERENCES / required_reference).is_file():
            result.errors.append(f'missing required AI reference: {required_reference}')
    if not (STRUCTURED / 'dependency-impact.schema.json').is_file():
        result.errors.append('missing dependency-impact schema')
    if not (STRUCTURED_FIXTURES / 'dependency_impact.yaml').is_file():
        result.errors.append('missing dependency-impact test fixture')
    capability_checks = {'代码注释': any((isinstance(condition, dict) and condition.get('id') == 'code-comments' for condition in (routing_contract.get('promptConditions', []) if isinstance(routing_contract, dict) else []))), '关联影响': (STRUCTURED_FIXTURES / 'dependency_impact.yaml').is_file(), '跨模块实现映射': 'crossModuleImplementations' in (STRUCTURED_FIXTURES / 'dependency_impact.yaml').read_text(encoding='utf-8'), 'additional_reviewers': 'additional_reviewers' in json.dumps(load_json(ASSETS / 'long-task' / 'record-contract.json', result), ensure_ascii=False)}
    for (capability, present) in capability_checks.items():
        if not present:
            result.errors.append(f'machine contracts missing required capability: {capability}')
    onepage = (REFERENCES / 'onepage-html.md').read_text(encoding='utf-8')
    if '架构设计 |' in onepage or '架构代码 |' in onepage or '页面行为演示' in onepage:
        result.errors.append('onepage-html.md retains duplicate lifecycle or prototype rules')
    backend_reference = (REFERENCES / 'backend-code-reference.md').read_text(encoding='utf-8')
    if 'HTML 不得充当实现契约输入' not in backend_reference:
        result.errors.append('backend implementation contract must state the current source-only HTML rule')
    active_docs = active_markdown_paths()
    lifecycle = [phase.get('id') for phase in contract.get('canonicalPhases', []) if isinstance(phase, dict) and isinstance(phase.get('id'), str)]
    if not lifecycle:
        result.errors.append('cannot derive lifecycle from runtime contract')
        lifecycle = ['__missing_lifecycle__']
    lifecycle_pattern = re.compile('\\s*→\\s*'.join(map(re.escape, lifecycle)))
    lifecycle_occurrences = sum((len(lifecycle_pattern.findall(path.read_text(encoding='utf-8'))) for path in active_docs))
    if lifecycle_occurrences != 1:
        result.errors.append(f'canonical lifecycle must be rendered only once in runtime-contract.md; found {lifecycle_occurrences}')
    lifecycle_core_text = (REFERENCES / 'lifecycle-core.md').read_text(encoding='utf-8')
    if 'canonicalPhases' not in lifecycle_core_text and 'runtime-contract' not in lifecycle_core_text:
        result.errors.append('lifecycle-core.md must point to runtime canonicalPhases instead of redefining lifecycle authority')
    h1_locations: dict[str, list[str]] = {}
    for path in active_docs:
        match = re.search('^#\\s+(.+)$', path.read_text(encoding='utf-8'), re.MULTILINE)
        if match:
            title = re.sub('\\s+', ' ', match.group(1).strip().lower())
            h1_locations.setdefault(title, []).append(path.relative_to(ROOT).as_posix())
    duplicate_h1 = {title: paths for (title, paths) in h1_locations.items() if len(paths) > 1}
    if duplicate_h1:
        result.errors.append(f'active references have duplicate top-level titles: {duplicate_h1}')
    paragraph_locations: dict[str, list[str]] = {}
    for path in active_docs:
        for paragraph in normalized_paragraphs(path):
            normalized = re.sub('[`*_\\[\\]()]', '', paragraph).lower()
            paragraph_locations.setdefault(normalized, []).append(path.relative_to(ROOT).as_posix())
    duplicate_paragraphs = {paragraph[:100]: sorted(set(paths)) for (paragraph, paths) in paragraph_locations.items() if len(set(paths)) > 1}
    if duplicate_paragraphs:
        result.errors.append(f'active references contain duplicated long paragraphs: {duplicate_paragraphs}')
    fenced_locations: dict[str, list[str]] = {}
    for path in active_docs:
        for (block_index, block) in normalized_fenced_blocks(path):
            fenced_locations.setdefault(block, []).append(f'{path.relative_to(ROOT).as_posix()}#block-{block_index}')
    duplicate_fenced_blocks = {block[:100]: locations for (block, locations) in fenced_locations.items() if len(locations) > 1}
    if duplicate_fenced_blocks:
        result.errors.append(f'active references contain duplicated long fenced blocks: {duplicate_fenced_blocks}')
    if '模式矩阵' not in skill or 'assets/runtime-contract.json' not in skill:
        result.errors.append('SKILL.md must point the canonical work-mode matrix to Runtime Contract')
    source_map_payload = load_json(ROOT / 'ai' / 'source-map.json', result)
    if isinstance(source_map_payload, dict):
        authoritative = source_map_payload.get('authoritativeSources', {})
        current_storage = source_map_payload.get('currentStoragePointers', {})
        expected_pointers = {'developmentTaskPlan': 'version/{VERSION}/task/{TARGET_ID}/development_task_plan.json', 'developmentTaskPlanReviews': 'version/{VERSION}/task/{TARGET_ID}/evidence/reviews.jsonl'}
        for (key, expected) in expected_pointers.items():
            if authoritative.get(key) != expected:
                result.errors.append(f'ai/source-map.json missing source ownership pointer: {key}')
        for key in ('workRecord', 'evidenceRegistry', 'reviewRegistry'):
            if not isinstance(current_storage.get(key), str) or not current_storage.get(key):
                result.errors.append(f'ai/source-map.json missing current storage pointer: {key}')
    retired_files = (STRUCTURED / 'archive-approval.schema.json', STRUCTURED / 'archive_approval.yaml')
    present_retired = [path.relative_to(ROOT).as_posix() for path in retired_files if path.exists()]
    if present_retired:
        result.errors.append(f'retired separate archive approval files remain: {present_retired}')
    retired_terms = {
        'separate archive approval': re.compile('archive[_ -]approval|archive approval', re.IGNORECASE),
        'misspelled DB username variable': re.compile('TEST_DB_USERNAM(?!E)'),
    }
    active_references = sorted(REFERENCES.rglob('*.md'))
    scan_paths = [ROOT / 'SKILL.md', ROOT / 'context-routing.json', *active_references, ROOT / 'scripts' / 'merge_docs.py']
    for (label, pattern) in retired_terms.items():
        matches = []
        for path in scan_paths:
            for (lineno, line) in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
                if pattern.search(line):
                    matches.append(f'{path.relative_to(ROOT).as_posix()}:{lineno}')
        if matches:
            result.errors.append(f'active runtime retains {label}: {matches}')
    ctx.update(active_references=active_references, expected=expected, key=key, label=label, line=line, lineno=lineno, matches=matches, path=path, pattern=pattern)

def _check_active_structure_section_4(ctx: CheckFlowContext) -> None:
    active_references = ctx.get('active_references')
    actual = ctx.get('actual')
    contract = ctx.contract
    expected = ctx.get('expected')
    key = ctx.get('key')
    label = ctx.get('label')
    line = ctx.get('line')
    lineno = ctx.get('lineno')
    matches = ctx.get('matches')
    overlap = ctx.get('overlap')
    path = ctx.get('path')
    pattern = ctx.get('pattern')
    result = ctx.result
    routing_contract = ctx.get('routing_contract')
    semantic_contract = ctx.get('semantic_contract')
    source_map = ctx.get('source_map')
    release_contract = load_json(ASSETS / 'release-contract.json', result)
    if isinstance(release_contract, dict):
        runtime_version = str(contract.get('packageVersion') or '')
        semantic_contract = load_json(ASSETS / 'semantic-contract.json', result)
        semantic_version = str(semantic_contract.get('packageVersion') or '') if isinstance(semantic_contract, dict) else ''
        if release_contract.get('packageVersion') != runtime_version or semantic_version != runtime_version:
            result.errors.append('release, runtime, and semantic package versions must match')
        release_stage = str(release_contract.get('releaseStage') or '')
        if release_stage != 'STABLE':
            result.errors.append('release-contract.json releaseStage must be STABLE for the initial package')
        if not re.fullmatch(r'[0-9a-f]{40}', str(release_contract.get('baselineTag') or '')):
            result.errors.append('release-contract.json baselineTag must be an existing commit reference')
        if release_contract.get('storageModelVersion') != contract.get('storageModelVersion'):
            result.errors.append('release-contract.json storageModelVersion must match runtime contract')
        storage_contract = load_json(ASSETS / 'storage-contract.json', result)
        if isinstance(storage_contract, dict):
            model_identity = storage_contract.get('modelIdentity')
            model_version = storage_contract.get('modelVersion')
            compatibility = storage_contract.get('compatibility') if isinstance(storage_contract.get('compatibility'), dict) else {}
            if model_identity != storage_contract.get('storageModel'):
                result.errors.append('storage-contract.json modelIdentity must match storageModel')
            if model_version != contract.get('storageModelVersion') or model_version != release_contract.get('storageModelVersion'):
                result.errors.append('Storage, Runtime, and Release storage model versions must match')
            if contract.get('storageModelIdentity') != model_identity or release_contract.get('storageModelIdentity') != model_identity:
                result.errors.append('Runtime and Release storageModelIdentity must match Storage Contract')
            if contract.get('storageModelSource') != 'assets/storage-contract.json#/modelVersion' or release_contract.get('storageModelSource') != 'assets/storage-contract.json#/modelVersion':
                result.errors.append('Runtime and Release storageModelSource must reference Storage Contract')
            if compatibility.get('runtimeStorageModelVersion') != model_version or compatibility.get('releaseStorageModelVersion') != model_version:
                result.errors.append('storage-contract.json compatibility mapping is inconsistent')
        if release_contract.get('longTaskSchemaVersion') != contract.get('longTaskSchemaVersion'):
            result.errors.append('release-contract.json longTaskSchemaVersion must match runtime contract')
        expected_flags = {'wk-ar', 'description-request-intake', 'simple-optimization-flow', 'unified-wk-entry', 'atomic-request-reclassification', 'review-policy-catalog', 'budget-aware-context-routing', 'explicit-context-omissions', 'granular-ai-routing', 'skill-bootstrap-compaction', 'routing-golden-snapshots', 'task-fact-budget-5500', 'compact-fixed-context', 'dynamic-context-slices', 'deep-route-splitting', 'granular-documentation-routing', 'granular-workflow-routing', 'granular-long-task-routing', 'granular-verification-routing', 'bounded-merge-test-batches', 'all-compact-base-routes', 'version-agnostic-release-tests', 'minimal-skill-bootstrap', 'numeric-feature-flag-consistency', 'consolidated-reference-layout', 'section-aware-reference-packs', 'reference-pack-boundary-gate', 'contract-normalization'}
        release_flags = list(release_contract.get('featureFlags') or [])
        if not expected_flags.issubset(set(release_flags)):
            result.errors.append('release-contract.json missing current feature flags')
        if len(release_flags) != len(set(release_flags)):
            result.errors.append('release-contract.json featureFlags must be unique')
        reserved = int((routing_contract.get('routingPolicy') or {}).get('reservedTaskFactTokenBudget') or 0)
        task_budget_flags = sorted((flag for flag in release_flags if re.fullmatch('task-fact-budget-[0-9]+', flag)))
        expected_task_flag = f'task-fact-budget-{reserved}'
        if task_budget_flags != [expected_task_flag]:
            result.errors.append(f'release-contract.json must keep only current task fact budget flag {expected_task_flag}')
        base_limit = int((routing_contract.get('routingPolicy') or {}).get('maxBaseRouteTokenBudget') or 0)
        base_budget_flags = sorted((flag for flag in release_flags if re.fullmatch('compact-base-route-budget-[0-9]+', flag)))
        expected_base_flag = f'compact-base-route-budget-{base_limit}'
        if base_budget_flags != [expected_base_flag]:
            result.errors.append(f'release-contract.json must keep only current compact base route flag {expected_base_flag}')
    query_policy = contract.get('contractQueryPolicy') if isinstance(contract, dict) else None
    if not isinstance(query_policy, dict):
        result.errors.append('assets/runtime-contract.json missing contractQueryPolicy')
    else:
        if query_policy.get('wholeDocumentOutputForbidden') is not True:
            result.errors.append('contractQueryPolicy must forbid whole-document output')
        if query_policy.get('defaultMaxSelectionBytes') != 24576 or query_policy.get('hardMaxSelectionBytes') != 65536:
            result.errors.append('contractQueryPolicy output byte limits are invalid')
        if query_policy.get('normalizedSourceSchemaVersion') != 2:
            result.errors.append('contractQueryPolicy normalizedSourceSchemaVersion must be 2')
        if query_policy.get('materializer') != 'scripts/contract_normalization.py':
            result.errors.append('contractQueryPolicy materializer must be scripts/contract_normalization.py')
        for (key, expected) in {'runtimeQueryCommand': 'python3 scripts/runtime_contract.py', 'routingQueryCommand': 'python3 scripts/context_routing.py', 'reviewQueryCommand': 'python3 scripts/review_contract.py'}.items():
            if query_policy.get(key) != expected:
                result.errors.append(f'contractQueryPolicy {key} must be {expected}')
    for path in (ROOT / 'scripts' / 'runtime_contract.py', ROOT / 'scripts' / 'context_routing.py', ROOT / 'scripts' / 'contract_query_utils.py', ROOT / 'scripts' / 'contract_normalization.py'):
        if not path.is_file():
            result.errors.append(f'missing bounded contract query artifact: {path.relative_to(ROOT)}')
    routing_catalog_paths = {str(item.get('path') or '') for item in (routing_contract.get('resourceCatalog') or {}).values() if isinstance(item, dict)} if isinstance(routing_contract, dict) else set()
    forbidden_whole_contract_resources = {'assets/runtime-contract.json', 'assets/review-contract.json', 'context-routing.json'}
    overlap = sorted(routing_catalog_paths & forbidden_whole_contract_resources)
    if overlap:
        result.errors.append(f'context routing must not load whole large contracts as resources: {overlap}')
    request_schema = load_json(ASSETS / 'request-intake.schema.json', result)
    if isinstance(request_schema, dict):
        schema_const = request_schema.get('properties', {}).get('schemaVersion')
        if schema_const != {'const': contract.get('requestIntakeSchemaVersion')} and schema_const != {'type': 'integer', 'const': contract.get('requestIntakeSchemaVersion')}:
            result.errors.append('request-intake.schema.json schemaVersion must match runtime contract')
    for (source_key, expected_path) in (('requestIntake', 'version/{VERSION}/task/{TARGET_ID}/request_intake.json'), ('requestIntakeSchema', 'assets/request-intake.schema.json'), ('unifiedWk', 'scripts/wk.py'), ('release', 'assets/release-contract.json')):
        actual = (source_map.get('authoritativeSources', {}) if isinstance(source_map, dict) else {}).get(source_key)
        if actual != expected_path:
            result.errors.append(f'ai/source-map.json missing current source pointer: {source_key}')
    result.ok('Active AI structure is concise, current-only, mode-aware, and free of duplicate rule blocks')

def check_active_structure(contract: dict[str, Any], result: CheckResult) -> None:
    ctx = CheckFlowContext(contract, result)
    for check in (
        _check_active_structure_section_1,
        _check_active_structure_section_2,
        _check_active_structure_section_3,
        _check_active_structure_section_4,
    ):
        check(ctx)

def check_current_document_contract(result: CheckResult) -> None:
    requirement_template = STRUCTURED / 'requirement_list.template.md'
    requirement_doc_template = STRUCTURED / 'requirement.template.md'
    requirement_doc_example = STRUCTURED_FIXTURES / 'requirement.multi-feature.example.md'
    page_design_template = STRUCTURED / 'page_design.template.md'
    page_design_example = REFERENCES / 'page-design-example.md'
    implementation_strategy_reference = REFERENCES / 'implementation-strategy.md'
    implementation_strategy_example = REFERENCES / 'implementation-strategy-example.md'
    implementation_strategy_module = ROOT / 'scripts' / '_core' / 'implementation_strategy.py'
    design_template = STRUCTURED / 'design.template.md'
    test_case_template = STRUCTURED / 'test_case.template.md'
    page_design_reference = REFERENCES / 'page-design.md'
    module_template = STRUCTURED / 'module_desc.template.md'
    work_template = ASSETS / 'work.template.md'
    work_record_template = ASSETS / 'work_record.template.md'
    document_script = ROOT / 'scripts' / 'document_facts.py'
    requirement_doc_script = ROOT / 'scripts' / 'requirement_doc.py'
    design_doc_script = ROOT / 'scripts' / 'design_doc.py'
    test_case_doc_script = ROOT / 'scripts' / 'case_doc.py'
    work_script = ROOT / 'scripts' / 'work_mode.py'
    test_environment_script = ROOT / 'scripts' / 'independent_test_environment.py'
    for path in (requirement_template, requirement_doc_template, requirement_doc_example, design_template, page_design_template, page_design_example, implementation_strategy_reference, implementation_strategy_example, implementation_strategy_module, test_case_template, page_design_reference, module_template, work_template, work_record_template, document_script, requirement_doc_script, design_doc_script, test_case_doc_script, work_script, test_environment_script):
        if not path.is_file():
            result.errors.append(f'missing current document contract file: {path.relative_to(ROOT)}')
            return
    requirement_text = requirement_template.read_text(encoding='utf-8')
    requirement_header = '| 需求编码 | 需求名称 | 版本号 | 需求文档 | 需求说明 | 对应变更需求编号 | 需求完成情况 |'
    if requirement_header not in requirement_text:
        result.errors.append('requirement_list.template.md does not contain the fixed requirement table')
    requirement_doc_text = requirement_doc_template.read_text(encoding='utf-8')
    requirement_doc_headings = tuple((f'## {index}. {title}' for (index, title) in enumerate(('需求信息', '背景与问题', '需求目标', '范围', '功能列表', '功能详细需求', '跨功能规则', '异常与边界场景', '验收标准', '非功能要求', '模块职责边界', '待确认事项与已确认决策', '追踪关系', '变更记录'), start=1)))
    missing_requirement_headings = [heading for heading in requirement_doc_headings if heading not in requirement_doc_text]
    if missing_requirement_headings:
        result.errors.append(f'requirement.template.md missing headings: {missing_requirement_headings}')
    for token in ('RequirementConfirmationAgent', 'RequirementAnalysisAgent', '{REQUIREMENT_ID}-F01', 'BR-{REQUIREMENT_ID}-001', 'AC-{REQUIREMENT_ID}-001', 'TR-{REQUIREMENT_ID}-001'):
        if token not in requirement_doc_text:
            result.errors.append(f'requirement.template.md missing lifecycle or trace contract: {token}')
    requirement_doc_script_text = requirement_doc_script.read_text(encoding='utf-8')
    for token in ('command_init', 'command_sync_list', 'command_validate', 'command_status', 'command_sync_traceability', 'parse_trace_sources', 'merge_traceability', 'FEATURE_RE', 'CONFIRMATION_HEADINGS', 'REQUIRED_HEADINGS'):
        if token not in requirement_doc_script_text:
            result.errors.append(f'requirement_doc.py missing executable document contract: {token}')
    for token in ('REQUIREMENT_LINK_RE', '_project_requirement_row', 'validate_requirement_list_links', 'upsert_requirement_list_row'):
        if token not in document_script.read_text(encoding='utf-8'):
            result.errors.append(f'document_facts.py missing requirement link contract: {token}')
    page_design_template_text = page_design_template.read_text(encoding='utf-8')
    page_design_reference_text = page_design_reference.read_text(encoding='utf-8')
    for token in ('wk -v', '页面业务行为设计', '不包含视觉设计'):
        if token not in page_design_template_text and token not in page_design_reference_text:
            result.errors.append(f'page design contract missing token: {token}')
    page_design_example_text = page_design_example.read_text(encoding='utf-8')
    for token in ('独立示例', '张三', 'PAGEACT-BOOKING-QUERY', 'CASE-BOOKING-QUERY-NAME-001', '推荐验证方式'):
        if token not in page_design_example_text:
            result.errors.append(f'page-design-example.md missing demo contract: {token}')
    test_case_template_text = test_case_template.read_text(encoding='utf-8')
    for (marker, columns) in case_doc.TABLE_CONTRACTS.items():
        if f'<!-- {marker} -->' not in test_case_template_text:
            result.errors.append(f'test_case.template.md missing Case table: {marker}')
        for column in columns:
            if column not in test_case_template_text:
                result.errors.append(f'test_case.template.md missing column: {marker}->{column}')
    test_case_structure = case_doc.validate_document(test_case_template, 'structure')
    if not test_case_structure.valid:
        result.errors.append(f'test_case.template.md fails structural validation: {test_case_structure.errors}')
    requirement_example_text = requirement_doc_example.read_text(encoding='utf-8')
    for token in ('DEMO1001-F01', 'DEMO1001-F02', 'DEMO1001-F03', 'CR-DEMO1001-001', 'AC-DEMO1001-003', 'TR-DEMO1001-003'):
        if token not in requirement_example_text:
            result.errors.append(f'requirement.multi-feature.example.md missing example contract: {token}')
    if re.search('\\{[A-Z][A-Z0-9_-]*\\}', requirement_example_text):
        result.errors.append('requirement.multi-feature.example.md contains unresolved placeholders')
    design_template_text = design_template.read_text(encoding='utf-8')
    design_doc_script_text = design_doc_script.read_text(encoding='utf-8')
    for heading in design_doc.REQUIRED_HEADINGS:
        if heading not in design_template_text:
            result.errors.append(f'design.template.md missing heading: {heading}')
    for (marker, columns) in design_doc.MATRIX_CONTRACTS.items():
        if f'<!-- {marker} -->' not in design_template_text:
            result.errors.append(f'design.template.md missing implementation matrix: {marker}')
        for column in columns:
            if column not in design_template_text:
                result.errors.append(f'design.template.md missing matrix column: {marker}->{column}')
    for token in ('command_init', 'command_validate', 'validate_design', 'MATRIX_CONTRACTS', 'uncheckedReadinessGates'):
        if token not in design_doc_script_text:
            result.errors.append(f'design_doc.py missing executable design contract: {token}')
    structure_result = design_doc.validate_design(design_template, 'structure')
    if not structure_result.valid:
        result.errors.append(f'design.template.md fails structural validation: {structure_result.errors}')
    if structure_result.counts.get('implementationStrategyContractVersion') != implementation_strategy.CONTRACT_VERSION:
        result.errors.append('design.template.md implementation strategy contract version differs from semantic contract')
    strategy_reference_text = implementation_strategy_reference.read_text(encoding='utf-8')
    for token in ('REUSE → COMPATIBLE_EXTEND → MODIFY → CREATE', 'REUSE_SHARED', 'SEPARATE_REFACTOR', '当前契约校验'):
        if token not in strategy_reference_text:
            result.errors.append(f'implementation-strategy.md missing contract token: {token}')
    strategy_example_text = implementation_strategy_example.read_text(encoding='utf-8')
    for token in ('独立示例', 'IMPL-DEC-LIBRARY-IMPORT', '平行新建', '布尔参数伪复用', '跨域万能工具类'):
        if token not in strategy_example_text:
            result.errors.append(f'implementation-strategy-example.md missing positive/negative example token: {token}')
    if design_doc.PLACEHOLDER_RE.search(strategy_example_text):
        result.errors.append('implementation-strategy-example.md contains unresolved design placeholders')
    source_map = load_json(ROOT / 'ai' / 'source-map.json', result)
    sources = source_map.get('authoritativeSources', {}) if isinstance(source_map, dict) else {}
    if sources.get('design') != 'assets/structured-docs/design.template.md' or sources.get('designValidation') != 'scripts/design_doc.py':
        result.errors.append('ai/source-map.json missing design template or validation authority')
    if sources.get('implementationStrategy') != 'assets/semantic-contract.json#canonicalTerms/implementationStrategyDecision':
        result.errors.append('ai/source-map.json missing implementation strategy authority')
    if sources.get('implementationStrategyExample') != 'references/implementation-strategy-example.md':
        result.errors.append('ai/source-map.json missing implementation strategy example')
    review_contract = load_json(ASSETS / 'review-contract.json', result)
    if isinstance(review_contract, dict):
        criteria = review_contract.get('criteriaCatalog', {})
        required_criteria = {'RC-SPEC-007', 'RC-MAINT-008', 'RC-ARCH-008'}
        if not required_criteria.issubset(criteria):
            result.errors.append('review contract missing implementation strategy criteria')
        profiles = review_contract.get('profiles', {})
        if 'RC-SPEC-007' not in (profiles.get('code_review:SpecComplianceReviewAgent', {}).get('criterionIds') or []):
            result.errors.append('Spec Compliance profile missing implementation strategy criterion')
        if 'RC-MAINT-008' not in (profiles.get('code_review:EngineeringStandardsReviewAgent', {}).get('criterionIds') or []):
            result.errors.append('Engineering Standards profile missing shared-logic criterion')
        if 'RC-ARCH-008' not in (profiles.get('design:ArchitectureReviewAgent', {}).get('criterionIds') or []):
            result.errors.append('Design Architecture profile missing CREATE ownership criterion')
    expected_test_case_sources = {'testCase': 'version/{VERSION}/doc/{REQUIREMENT_ID}/test_case.md', 'testCaseTemplate': 'assets/structured-docs/test_case.template.md', 'testCaseValidation': 'scripts/case_doc.py', 'pageDesignExample': 'references/page-design-example.md'}
    for (key, value) in expected_test_case_sources.items():
        if sources.get(key) != value:
            result.errors.append(f'ai/source-map.json missing test/page document authority: {key}')
    module_text = module_template.read_text(encoding='utf-8')
    required_headings = ('## 模块信息', '## 业务能力', '## 模块功能', '## 业务信息', '## 规则与约束', '## 模块依赖', '## 文档引用')
    missing = [heading for heading in required_headings if heading not in module_text]
    if missing:
        result.errors.append(f'module_desc.template.md missing headings: {missing}')
    forbidden = ('工作量', '实现证据', '缺口说明', '前端页面代码路径', '后端主方法入口')
    found = [term for term in forbidden if term in module_text]
    if found:
        result.errors.append(f'module_desc.template.md contains removed fields: {found}')
    script_text = document_script.read_text(encoding='utf-8')
    for token in ('discover_extra_documents', 'synchronize_module_desc_source', '补充文档（自动）'):
        if token not in script_text:
            result.errors.append(f'document_facts.py missing extra-document reference contract: {token}')
    work_text = work_template.read_text(encoding='utf-8')
    required_work_headers = ('页面设计', '测试用例', '前端伪代码', '后端伪代码', '架构代码', '测试')
    missing_work_headers = [header for header in required_work_headers if header not in work_text]
    if missing_work_headers:
        result.errors.append(f'work.template.md missing work mode columns: {missing_work_headers}')
    for token in ('common-develop-work-mode', 'wk -mc', 'wk -v', 'wk -min', 'wk -auto', 'wk -ar', 'wk -gc', 'wk -gp', 'wk -ws', 'wk -wd', 'wk -wc'):
        if token not in work_text:
            result.errors.append(f'work.template.md missing mode contract: {token}')
    work_script_text = work_script.read_text(encoding='utf-8')
    for token in ('MODEL_CODE_COLUMNS', 'PAGE_DESIGN_COLUMNS', 'MINIMAL_COLUMNS', 'MINIMAL_PHASES', 'LIGHTWEIGHT_COLUMNS', 'LIGHTWEIGHT_PHASES', 'AUTO_CONFIRMATION_POLICY', 'ARCHITECTURE_REVIEW_COLUMNS', 'ARCHITECTURE_REVIEW_POLICY', 'resolved_mode_policy', 'minimal_archive_gate_keys', 'lightweight_archive_gate_keys', 'validate_work_file', '--json'):
        if token not in work_script_text:
            result.errors.append(f'work_mode.py missing executable mode contract: {token}')
    work_record_text = work_record_template.read_text(encoding='utf-8')
    required_work_record_tokens = ('<!-- managed-by: common-develop/work-record-v5 -->', '<!-- work-record-events-start -->', '<!-- work-record-events-end -->', '## 使用规则')
    for token in required_work_record_tokens:
        if token not in work_record_text:
            result.errors.append(f'work_record.template.md missing canonical token: {token}')
    if 'work-record-meta:' in work_record_text:
        result.errors.append('work_record.template.md must not contain an example event')
    long_task_record_contract = load_json(ASSETS / 'long-task' / 'record-contract.json', result)
    if not isinstance(long_task_record_contract, dict) or 'taskAttempt' not in long_task_record_contract.get('records', {}):
        result.errors.append('record-contract.json missing records.taskAttempt')
    required_handlers = {'command_start_attempt', 'command_finish_attempt', 'command_advance_development_step', 'command_task_context', 'command_work_events', 'command_validate_work_record', 'command_render_work_record', 'command_help'}
    for handler in sorted(required_handlers):
        if not callable(getattr(long_task_runtime, handler, None)):
            result.errors.append(f'long_task.py missing structured attempt/work-record handler: {handler}')
    required_commands = {'start-attempt', 'finish-attempt', 'advance-development-step', 'task-context', 'work-events', 'validate-work-record', 'render-work-record', 'help'}
    missing_commands = sorted(required_commands - parser_command_names(long_task_runtime.build_parser()))
    if missing_commands:
        result.errors.append(f'long_task.py parser missing structured commands: {missing_commands}')
    test_environment_text = test_environment_script.read_text(encoding='utf-8')
    for token in ('resolve_independent_database', 'requiredEnvironmentGroups', 'secretValuesExposed', 'fallbackAllowed'):
        if token not in test_environment_text:
            result.errors.append(f'independent_test_environment.py missing independent DB guard: {token}')
    active_paths = [ROOT / 'SKILL.md', *sorted(REFERENCES.rglob('*.md'))]
    old_refs = []
    for path in active_paths:
        for (line_no, line) in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
            if '{MODULE_CODE}_requirement.md' in line or '{MODULE}_requirement.md' in line:
                old_refs.append(f'{path.relative_to(ROOT)}:{line_no}')
    if old_refs:
        result.errors.append(f'active documents still reference removed module requirement files: {old_refs}')
    if not result.errors:
        result.ok('Requirement, implementation-ready design, module description, and extra-document contracts are current')
