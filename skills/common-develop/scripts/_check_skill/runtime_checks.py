"""Runtime checks for the internal runtime."""
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
from _check_skill.cli import parser
from _check_skill.model import CheckResult

def check_agents(declared_agents: set[str], result: CheckResult) -> None:
    scan_paths = [ROOT / 'SKILL.md', ROOT / 'context-routing.json', ROOT / 'agents' / 'openai.yaml', ASSETS / 'review-contract.json']
    scan_paths.extend(sorted(REFERENCES.rglob('*.md')))
    scan_paths.extend(sorted((ASSETS / 'long-task').glob('*.md')))
    used: set[str] = set()
    for path in scan_paths:
        used.update(AGENT_PATTERN.findall(path.read_text(encoding='utf-8')))
    undeclared = sorted(used - declared_agents)
    if undeclared:
        result.errors.append(f'active documents reference undeclared Agents: {undeclared}')
    roles_text = (REFERENCES / 'roles.md').read_text(encoding='utf-8')
    missing_roles = sorted((agent for agent in declared_agents if agent not in roles_text))
    if missing_roles:
        result.errors.append(f'declared Agents missing from roles.md: {missing_roles}')
    result.ok('All active Agent names are declared and have role definitions')

def _check_runtime_contract_section_1(ctx: CheckFlowContext) -> None:
    agents = ctx.get('agents')
    contract = ctx.contract
    result = ctx.result
    command_interface = None
    expected = None
    field = None
    values = None
    if not isinstance(agents, list) or any((not isinstance(agent, str) for agent in agents)):
        result.errors.append('assets/runtime-contract.json: agents must be a string array')
        agents = []
    elif len(agents) != len(set(agents)):
        result.errors.append('assets/runtime-contract.json: duplicate Agent')
    statuses = contract.get('statuses')
    if not isinstance(statuses, dict):
        result.errors.append('assets/runtime-contract.json: statuses must be an object')
    else:
        for (domain, values) in statuses.items():
            if not isinstance(values, list) or not values or any((not isinstance(value, str) for value in values)):
                result.errors.append(f'assets/runtime-contract.json: invalid status domain {domain}')
            elif len(values) != len(set(values)):
                result.errors.append(f'assets/runtime-contract.json: duplicate status in {domain}')
    package_version = contract.get('packageVersion')
    if not isinstance(package_version, str) or not re.fullmatch('[0-9]+\\.[0-9]+', package_version):
        result.errors.append('assets/runtime-contract.json: packageVersion must use MAJOR.MINOR')
    expected_storage_versions = {'storageModelVersion': 6, 'workRecordSchemaVersion': 4, 'evidenceSchemaVersion': 4, 'reviewRegistrySchemaVersion': 1}
    for (field, expected) in expected_storage_versions.items():
        if contract.get(field) != expected:
            result.errors.append(f'assets/runtime-contract.json: {field} must be {expected}')
    optimization = contract.get('taskContextOptimization')
    if not isinstance(optimization, dict):
        result.errors.append('assets/runtime-contract.json: taskContextOptimization must be an object')
    else:
        verification = optimization.get('verification') if isinstance(optimization.get('verification'), dict) else {}
        projection = optimization.get('projection') if isinstance(optimization.get('projection'), dict) else {}
        context = optimization.get('context') if isinstance(optimization.get('context'), dict) else {}
        if verification.get('script') != 'scripts/task_verify.py':
            result.errors.append('taskContextOptimization.verification.script must be scripts/task_verify.py')
        if verification.get('levels') != ['summary', 'failures', 'full']:
            result.errors.append('taskContextOptimization verification levels are invalid')
        if verification.get('defaultLevel') != 'summary' or verification.get('cacheIsEvidence') is not False:
            result.errors.append('taskContextOptimization verification cache policy is invalid')
        if projection.get('script') != 'scripts/task_layout.py' or projection.get('kind') != 'CURRENT_MANIFEST':
            result.errors.append('taskContextOptimization projection contract is invalid')
        if projection.get('schema') != 'assets/current-manifest.schema.json' or projection.get('path') != 'task/{TARGET_ID}/cache/current_manifest.json':
            result.errors.append('taskContextOptimization projection path/schema is invalid')
        if projection.get('canonical') is not False or projection.get('registerAsBusinessEvidence') is not False:
            result.errors.append('generated task projections must not become canonical or business Evidence')
        if context.get('script') != 'scripts/ai_context.py' or context.get('minimumTokenBudget') != 500:
            result.errors.append('taskContextOptimization context contract is invalid')
        if context.get('defaultLazyObjectKinds') != ['evidence', 'review', 'snapshot']:
            result.errors.append('taskContextOptimization lazy object kinds are invalid')
        reference_reader_contract = context.get('referenceReader')
        expected_reference_reader = {'script': 'scripts/reference_reader.py', 'indexCommand': 'python3 scripts/reference_reader.py index -g {CURRENT_AGENT} --resource {RESOURCE_ID} --json', 'readSectionCommand': 'python3 scripts/reference_reader.py read -g {CURRENT_AGENT} --resource {RESOURCE_ID} --section {SECTION_ID}', 'readQueryCommand': 'python3 scripts/reference_reader.py read -g {CURRENT_AGENT} --resource {RESOURCE_ID} --query {TOPIC} --max-sections 3', 'policy': 'Resources with readStrategy=relevant_sections require explicit section or query selection; full reads require --full.'}
        if reference_reader_contract != expected_reference_reader:
            result.errors.append('taskContextOptimization context.referenceReader contract is invalid')
    execution_policy = contract.get('executionPolicy')
    expected_execution_policy = {'mode': 'SEQUENTIAL', 'allowParallelTasks': False, 'oneActiveTargetAtATime': True, 'multiTargetOrder': 'DECLARED_ORDER', 'multiTargetOrderSource': 'version/{VERSION}/project_process.md', 'description': 'All targets and plan tasks execute sequentially. A, B, and C may be grouped by lifecycle stage, but only one target or task may be active at a time in the declared order.'}
    if execution_policy != expected_execution_policy:
        result.errors.append('assets/runtime-contract.json: executionPolicy must define the canonical sequential-only policy')
    command_interface = contract.get('commandInterface')
    ctx.update(agents=agents, command_interface=command_interface, expected=expected, field=field, values=values)

def check_task_planning_interface(action, command_interface, contract, field, key, permission, policy, result, values):
    task_planning = command_interface.get('taskPlanning')
    if not isinstance(task_planning, dict):
        result.errors.append('assets/runtime-contract.json: commandInterface.taskPlanning must be an object')
    else:
        if task_planning.get('flags') != ['-tp', '--task-plan']:
            result.errors.append('assets/runtime-contract.json: taskPlanning flags must be -tp/--task-plan')
        trigger_policy = task_planning.get('triggerPolicy') if isinstance(task_planning.get('triggerPolicy'), dict) else {}
        if trigger_policy.get('ownerAgent') != 'ImplementationPlanAgent' or trigger_policy.get('finalizerAgent') != 'ProjectManagerAgent':
            result.errors.append('assets/runtime-contract.json: taskPlanning owner/finalizer Agents are invalid')
        files = task_planning.get('files') if isinstance(task_planning.get('files'), dict) else {}
        expected_files = {'canonical': 'version/{VERSION}/task/{TARGET_ID}/development_task_plan.json', 'rendered': 'version/{VERSION}/task/{TARGET_ID}/cache/current_manifest.json', 'reviewHistory': 'version/{VERSION}/task/{TARGET_ID}/evidence/reviews.jsonl'}
        if files != expected_files:
            result.errors.append('assets/runtime-contract.json: taskPlanning files are invalid')
        policy = task_planning.get('reviewPolicy') if isinstance(task_planning.get('reviewPolicy'), dict) else {}
        if policy.get('executionMode') != 'SEQUENTIAL':
            result.errors.append('assets/runtime-contract.json: taskPlanning Review must be sequential')
        if policy.get('standardReviewers') != ['PlanReviewAgent', 'ArchitectureReviewAgent', 'TestDesignAgent', 'DevelopAgent']:
            result.errors.append('assets/runtime-contract.json: taskPlanning standard Reviewers are invalid')
        if policy.get('minimalReviewers') != ['PlanReviewAgent', 'ArchitectureReviewAgent', 'DevelopAgent']:
            result.errors.append('assets/runtime-contract.json: taskPlanning minimal Reviewers are invalid')
        if policy.get('additionalTaskReviewersAllowed') is not True:
            result.errors.append('assets/runtime-contract.json: taskPlanning must allow task-specific Reviewers')
        if policy.get('statusDomainRef') != '#/statuses/review':
            result.errors.append('assets/runtime-contract.json: taskPlanning statusDomainRef must reuse review status domain')
        if policy.get('resultDomainRef') != '#/statuses/reviewConclusion':
            result.errors.append('assets/runtime-contract.json: taskPlanning resultDomainRef must reuse reviewConclusion domain')
        if policy.get('findingSeverityDomain') != ['P0', 'P1', 'P2']:
            result.errors.append('assets/runtime-contract.json: taskPlanning finding severities must be P0/P1/P2')
        required_fields = task_planning.get('requiredTaskFields')
        if not isinstance(required_fields, list) or 'depends_on' not in required_fields or 'dependencies' in required_fields:
            result.errors.append('assets/runtime-contract.json: taskPlanning dependency field must be depends_on only')
        commands = task_planning.get('commands') if isinstance(task_planning.get('commands'), dict) else {}
        if set(commands) != {'init', 'validate', 'seal', 'submitReview', 'revise', 'finalize', 'status', 'query'}:
            result.errors.append('assets/runtime-contract.json: taskPlanning commands are incomplete')
        permissions = contract.get('scriptPermissions') if isinstance(contract.get('scriptPermissions'), dict) else {}
        for action in ('init', 'seal', 'revise'):
            permission = permissions.get(f'task_plan.{action}') if isinstance(permissions.get(f'task_plan.{action}'), dict) else {}
            if permission.get('mode') != 'STATIC' or permission.get('allowedAgents') != ['ImplementationPlanAgent']:
                result.errors.append(f'assets/runtime-contract.json: task_plan.{action} must be owned only by ImplementationPlanAgent')
        final_permission = permissions.get('task_plan.finalize') if isinstance(permissions.get('task_plan.finalize'), dict) else {}
        if final_permission.get('mode') != 'STATIC' or final_permission.get('allowedAgents') != ['ProjectManagerAgent']:
            result.errors.append('assets/runtime-contract.json: task_plan.finalize must be owned only by ProjectManagerAgent')
    return (action, field, key, permission, policy, values)

def check_current_agent_interface(command_interface, agents, field, result):
    current_agent = command_interface.get('currentAgent')
    if not isinstance(current_agent, dict):
        result.errors.append('assets/runtime-contract.json: commandInterface.currentAgent must be an object')
        return field
    if current_agent.get('flags') != ['-g', '--agent']:
        result.errors.append('assets/runtime-contract.json: current Agent flags must be -g and --agent')
    if current_agent.get('default') not in agents:
        result.errors.append('assets/runtime-contract.json: current Agent default must reference a declared Agent')
    if current_agent.get('acceptedValuesRef') != '#/agents':
        result.errors.append('assets/runtime-contract.json: current Agent acceptedValuesRef must be #/agents')
    for field in ('requiredForDirectAgentExecution', 'enforcePhaseOwnerOrReviewer', 'requiredForScripts'):
        if current_agent.get(field) is not True:
            result.errors.append(f'assets/runtime-contract.json: current Agent {field} must be true')
    expected_record_fields = {
        'taskState': 'current_agent', 'stageResult': 'produced_by_agent', 'handoff': 'from_agent'
    }
    if current_agent.get('recordFields') != expected_record_fields:
        result.errors.append(
            'assets/runtime-contract.json: current Agent recordFields must map taskState, stageResult, and handoff'
        )
    position_rule = current_agent.get('positionRule')
    if not isinstance(position_rule, str) or '-f/-b' not in position_rule:
        result.errors.append('assets/runtime-contract.json: current Agent positionRule must cover -f/-b')
    if current_agent.get('authorizationRef') != '#/scriptPermissions':
        result.errors.append('assets/runtime-contract.json: current Agent authorizationRef must be #/scriptPermissions')
    return field


def _check_git_push_mode(git_push, result):
    key = None
    if not isinstance(git_push, dict) or git_push.get('flags') != ['-gp', '--git-push']:
        result.errors.append('assets/runtime-contract.json: gitPush mode must use -gp/--git-push')
    else:
        if git_push.get('default') is not False:
            result.errors.append('assets/runtime-contract.json: gitPush default must be false')
        if git_push.get('dependsOn') != ['gitCheckpoint']:
            result.errors.append('assets/runtime-contract.json: gitPush must depend on gitCheckpoint')
        push_policy = git_push.get('policy')
        if not isinstance(push_policy, dict):
            result.errors.append('assets/runtime-contract.json: gitPush policy must be an object')
        else:
            if push_policy.get('executorAgent') != 'ProjectManagerAgent':
                result.errors.append('assets/runtime-contract.json: gitPush executor must be ProjectManagerAgent')
            if push_policy.get('requiresGitCheckpoint') is not True:
                result.errors.append('assets/runtime-contract.json: gitPush must require gitCheckpoint')
            if push_policy.get('forceAllowed') is not False or push_policy.get('pushTags') is not False:
                result.errors.append('assets/runtime-contract.json: gitPush must forbid force and tag pushes')
            if push_policy.get('autoMerge') is not False:
                result.errors.append('assets/runtime-contract.json: gitPush must not merge')
            if push_policy.get('verifyRemoteHead') is not True or push_policy.get('failureBlocksPhaseAdvance') is not True:
                result.errors.append('assets/runtime-contract.json: gitPush verification/blocking policy is invalid')
            if push_policy.get('executionPolicyRef') != 'assets/runtime-contract.json#/executionPolicy':
                result.errors.append('assets/runtime-contract.json: gitPush executionPolicyRef is invalid')
            if push_policy.get('transportOrder') != ['NATIVE_GIT', 'GITHUB_CONNECTOR']:
                result.errors.append('assets/runtime-contract.json: gitPush transportOrder must prefer NATIVE_GIT then GITHUB_CONNECTOR')
            native_transport = push_policy.get('nativeTransport')
            if not isinstance(native_transport, dict):
                result.errors.append('assets/runtime-contract.json: gitPush nativeTransport is invalid')
            else:
                if native_transport.get('identityMode') != 'EXACT_COMMIT_SHA' or native_transport.get('remoteHeadMustEqualLocalCommitSha') is not True:
                    result.errors.append('assets/runtime-contract.json: native gitPush must preserve exact commit SHA identity')
                if native_transport.get('fallbackOn') != ['native_transport_unavailable', 'native_auth_unavailable']:
                    result.errors.append('assets/runtime-contract.json: native gitPush fallback classifications are invalid')
                if native_transport.get('timeoutSeconds') != 20:
                    result.errors.append('assets/runtime-contract.json: native gitPush transport timeout must be 20 seconds')
                if native_transport.get('neverFallbackOn') != ['git_divergence', 'remote_checkpoint_verification_failed']:
                    result.errors.append('assets/runtime-contract.json: gitPush must never Connector-fallback on divergence/verification failure')
            connector = push_policy.get('connectorFallback')
            if not isinstance(connector, dict) or connector.get('enabled') is not True:
                result.errors.append('assets/runtime-contract.json: gitPush connectorFallback must be enabled')
            else:
                expected_connector = {'provider': 'GITHUB_CONNECTOR', 'supportedRemoteHost': 'github.com', 'requestSchema': 'assets/git-connector-request.schema.json', 'receiptSchema': 'assets/git-connector-receipt.schema.json', 'adapterScript': 'scripts/git_connector_transport.py', 'identityMode': 'EXACT_TREE_MESSAGE_EQUIVALENT'}
                for (key, expected) in expected_connector.items():
                    if connector.get(key) != expected:
                        result.errors.append(f'assets/runtime-contract.json: gitPush connectorFallback {key} is invalid')
                if connector.get('requireExactTreeSha') is not True or connector.get('requireEquivalentParentTreeAndMessage') is not True:
                    result.errors.append('assets/runtime-contract.json: Connector fallback must require exact tree and equivalent parent provenance')
                if connector.get('remoteCommitShaMayDifferFromLocal') is not True or connector.get('rewriteLocalHistory') is not False:
                    result.errors.append('assets/runtime-contract.json: Connector fallback dual-SHA/local-history policy is invalid')
                if connector.get('receiptRequiredBeforeRemoteCheckpointed') is not True or connector.get('phaseAdvanceWhileConnectorPending') is not False:
                    result.errors.append('assets/runtime-contract.json: Connector receipt must gate REMOTE_CHECKPOINTED/phase advance')
                if connector.get('verifiedReceiptTtlSeconds') != 900:
                    result.errors.append('assets/runtime-contract.json: Connector verified receipt TTL must be 900 seconds')
                forbidden = set(connector.get('forbiddenConnectorActions') or [])
                required_forbidden = {'force_update_ref', 'create_other_branch', 'create_pull_request', 'merge', 'push_tag', 'release', 'deploy'}
                if not required_forbidden.issubset(forbidden):
                    result.errors.append('assets/runtime-contract.json: Connector fallback forbidden actions are incomplete')
            remote_state = push_policy.get('remoteCheckpointState')
            if not isinstance(remote_state, dict) or remote_state.get('remoteCheckpointedRequiresVerifiedTransportReceipt') is not True:
                result.errors.append('assets/runtime-contract.json: gitPush remoteCheckpointState must require verified transport evidence')
            for connector_path in (ROOT / 'scripts' / 'git_connector_transport.py', ROOT / 'assets' / 'git-connector-request.schema.json', ROOT / 'assets' / 'git-connector-receipt.schema.json'):
                if not connector_path.is_file():
                    result.errors.append(f'missing GitHub Connector transport asset: {connector_path.relative_to(ROOT)}')
    return key


def _check_work_modes(work_modes, result, values):
    first_step = policy = None
    model_code = work_modes.get('modelCode')
    page_design = work_modes.get('pageDesign')
    minimal = work_modes.get('minimal')
    auto = work_modes.get('auto')
    architecture_review = work_modes.get('architectureReview')
    lightweight = work_modes.get('lightweight')
    git_checkpoint = work_modes.get('gitCheckpoint')
    git_push = work_modes.get('gitPush')
    if not isinstance(model_code, dict) or model_code.get('flags') != ['-mc']:
        result.errors.append('assets/runtime-contract.json: modelCode mode must use -mc')
    else:
        if model_code.get('default') is not False:
            result.errors.append('assets/runtime-contract.json: modelCode default must be false')
        if model_code.get('workColumns') != ['前端伪代码', '后端伪代码']:
            result.errors.append('assets/runtime-contract.json: modelCode workColumns are invalid')
        if model_code.get('disabledValue') != '无' or model_code.get('enabledValue') != 'AIW':
            result.errors.append('assets/runtime-contract.json: modelCode work values are invalid')
    if not isinstance(page_design, dict) or page_design.get('flags') != ['-v']:
        result.errors.append('assets/runtime-contract.json: pageDesign mode must use -v')
    else:
        if page_design.get('default') is not False:
            result.errors.append('assets/runtime-contract.json: pageDesign default must be false')
        if page_design.get('workColumns') != ['页面设计']:
            result.errors.append('assets/runtime-contract.json: pageDesign workColumns are invalid')
        if page_design.get('disabledValue') != '无' or page_design.get('enabledValue') != 'AIW':
            result.errors.append('assets/runtime-contract.json: pageDesign work values are invalid')
    if not isinstance(minimal, dict) or minimal.get('flags') != ['-min']:
        result.errors.append('assets/runtime-contract.json: minimal mode must use -min')
    else:
        if minimal.get('default') is not False:
            result.errors.append('assets/runtime-contract.json: minimal default must be false')
        if minimal.get('workColumns') != ['测试用例', '测试']:
            result.errors.append('assets/runtime-contract.json: minimal workColumns are invalid')
        if minimal.get('disabledValue') != '无':
            result.errors.append('assets/runtime-contract.json: minimal disabledValue must be 无')
        expected_skipped = ['test_design', 'testing']
        if minimal.get('skippedPhases') != expected_skipped:
            result.errors.append('assets/runtime-contract.json: minimal skippedPhases are invalid')
        if minimal.get('phaseOutcome') != 'SKIPPED':
            result.errors.append('assets/runtime-contract.json: minimal phaseOutcome must be SKIPPED')
        if minimal.get('archiveGateStatus') != 'NOT_APPLICABLE':
            result.errors.append('assets/runtime-contract.json: minimal archiveGateStatus must be NOT_APPLICABLE')
        if minimal.get('skipReason') != '当前版本使用 wk -min，按已声明范围跳过全部测试阶段。':
            result.errors.append('assets/runtime-contract.json: minimal skipReason is invalid')
        expected_suppressed = {'requirement_confirmation': ['TestDesignAgent'], 'requirement_analysis': ['TestDesignAgent'], 'business_model': ['TestDesignAgent'], 'design': ['TestDesignAgent'], 'implementation_plan': ['TestDesignAgent'], 'development': ['TDDReviewAgent']}
        if minimal.get('suppressedReviewerAssignments') != expected_suppressed:
            result.errors.append('assets/runtime-contract.json: minimal suppressedReviewerAssignments are invalid')
        expected_non_test_evidence = ['compile', 'type_check', 'static_analysis', 'build', 'spec_compliance_review', 'engineering_standards_review', 'untested_risk_statement']
        if minimal.get('requiredNonTestEvidence') != expected_non_test_evidence:
            result.errors.append('assets/runtime-contract.json: minimal requiredNonTestEvidence are invalid')
    if not isinstance(auto, dict) or auto.get('flags') != ['-auto']:
        result.errors.append('assets/runtime-contract.json: auto mode must use -auto')
    else:
        if auto.get('default') is not False:
            result.errors.append('assets/runtime-contract.json: auto default must be false')
        policy = auto.get('confirmationPolicy')
        if not isinstance(policy, dict):
            result.errors.append('assets/runtime-contract.json: auto confirmationPolicy must be an object')
        else:
            if policy.get('mode') != 'AUTOMATED':
                result.errors.append('assets/runtime-contract.json: auto confirmation mode must be AUTOMATED')
            if policy.get('userConfirmationRequired') is not False or policy.get('autoAdvance') is not True:
                result.errors.append('assets/runtime-contract.json: auto confirmation booleans are invalid')
            if policy.get('decisionAgent') != 'ProjectManagerAgent':
                result.errors.append('assets/runtime-contract.json: auto decisionAgent is invalid')
            for key in ('continueOnlyWhen', 'stopWhen', 'remediationContinueWhen', 'remediationStopWhen'):
                values = policy.get(key)
                if not isinstance(values, list) or not values or len(values) != len(set(values)):
                    result.errors.append(f'assets/runtime-contract.json: auto confirmationPolicy.{key} is invalid')
            if policy.get('stopScope') != 'BATCH_FORWARD_ONLY':
                result.errors.append('assets/runtime-contract.json: auto stopScope must be BATCH_FORWARD_ONLY')
        remediation = auto.get('remediationPolicy')
        if not isinstance(remediation, dict) or remediation.get('enabled') is not True:
            result.errors.append('assets/runtime-contract.json: auto remediationPolicy must be enabled')
        else:
            if remediation.get('batchForwardStopDoesNotStopRemediation') is not True:
                result.errors.append('assets/runtime-contract.json: auto remediation must separate batch-forward stop')
            revision_policy = remediation.get('revisionPolicy') if isinstance(remediation.get('revisionPolicy'), dict) else {}
            if revision_policy.get('codeConfigOrTestChangeRequiresNewExactRevision') is not True:
                result.errors.append('assets/runtime-contract.json: auto remediation exact-revision policy is invalid')
            p1_policy = remediation.get('p1Policy') if isinstance(remediation.get('p1Policy'), dict) else {}
            if p1_policy.get('batchForward') != 'FROZEN' or p1_policy.get('autoRemediation') != 'ACTIVE':
                result.errors.append('assets/runtime-contract.json: auto P1 remediation policy is invalid')
            prechecks = remediation.get('productionMutationPrecheckOrder')
            if not isinstance(prechecks, list) or len(prechecks) != 5 or len(prechecks) != len(set(prechecks)):
                result.errors.append('assets/runtime-contract.json: auto production mutation prechecks are invalid')
        boundary = auto.get('architectureBoundaryPolicy')
        if not isinstance(boundary, dict) or boundary.get('mode') != 'DETECT_BEFORE_AR':
            result.errors.append('assets/runtime-contract.json: auto architecture boundary policy is invalid')
        elif boundary.get('ordinaryDevelopmentRepeatsSkeletonGate') is not False:
            result.errors.append('assets/runtime-contract.json: ordinary auto DEV must not repeat Skeleton Gate')
        final_head = auto.get('finalHeadGatePolicy')
        if not isinstance(final_head, dict) or final_head.get('requiredGate') != 'P0':
            result.errors.append('assets/runtime-contract.json: auto final HEAD gate policy is invalid')
        elif final_head.get('bindToTrueFinalPrHead') is not True:
            result.errors.append('assets/runtime-contract.json: auto final HEAD P0 must bind true PR HEAD')
    if not isinstance(architecture_review, dict) or architecture_review.get('flags') != ['-ar']:
        result.errors.append('assets/runtime-contract.json: architectureReview mode must use -ar')
    else:
        if architecture_review.get('default') is not False:
            result.errors.append('assets/runtime-contract.json: architectureReview default must be false')
        if architecture_review.get('workColumns') != ['架构代码']:
            result.errors.append('assets/runtime-contract.json: architectureReview workColumns are invalid')
        if architecture_review.get('disabledValue') != '无' or architecture_review.get('enabledValue') != 'AIW':
            result.errors.append('assets/runtime-contract.json: architectureReview work values are invalid')
        ar_policy = architecture_review.get('policy')
        if not isinstance(ar_policy, dict):
            result.errors.append('assets/runtime-contract.json: architectureReview policy must be an object')
        else:
            if ar_policy.get('lifecyclePhase') != 'development':
                result.errors.append('assets/runtime-contract.json: architectureReview lifecyclePhase must be development')
    if not isinstance(lightweight, dict) or lightweight.get('flags') != ['-ws'] or lightweight.get('closeFlags') != ['-wc']:
        result.errors.append('assets/runtime-contract.json: lightweight mode must use -ws and -wc')
    else:
        if lightweight.get('default') is not False:
            result.errors.append('assets/runtime-contract.json: lightweight default must be false')
        if lightweight.get('workColumns') != ['测试用例', 'Review', '测试']:
            result.errors.append('assets/runtime-contract.json: lightweight workColumns are invalid')
        if lightweight.get('skippedPhases') != ['test_design', 'tdd', 'code_review', 'testing']:
            result.errors.append('assets/runtime-contract.json: lightweight skippedPhases are invalid')
        if lightweight.get('interactionPolicy', {}).get('allowInterleavedRequirementDesignDevelopment') is not True:
            result.errors.append('assets/runtime-contract.json: lightweight mode must allow interleaved conversation')
        if lightweight.get('interactionPolicy', {}).get('userExplicitTestingRequestOverrides') is not False:
            result.errors.append('assets/runtime-contract.json: lightweight mode must reject explicit testing override while active')
        if lightweight.get('skipReason') != '当前版本使用 wk -ws：采用对话式结对开发；轻量模式强制跳过 TDD、测试设计、Review、测试代码、测试用例和测试执行，不允许在模式保持开启时恢复。':
            result.errors.append('assets/runtime-contract.json: lightweight skipReason is invalid')
        if lightweight.get('archivePolicy', {}).get('flags') != ['-wd']:
            result.errors.append('assets/runtime-contract.json: lightweight archive policy must use -wd')
            if ar_policy.get('stateSource') != 'task/{TARGET_ID}/task_events.jsonl#TASK_STATE_PATCHED(architecture_review)':
                result.errors.append('assets/runtime-contract.json: architectureReview stateSource is invalid')
            first_step = ar_policy.get('firstStep') if isinstance(ar_policy.get('firstStep'), dict) else {}
            second_step = ar_policy.get('secondStep') if isinstance(ar_policy.get('secondStep'), dict) else {}
            if first_step.get('id') != 'ARCHITECTURE_SKELETON':
                result.errors.append('assets/runtime-contract.json: architectureReview firstStep id is invalid')
            if first_step.get('requiredReviewers') != ['ArchitectureReviewAgent', 'SpecComplianceReviewAgent']:
                result.errors.append('assets/runtime-contract.json: architectureReview first-step Reviewers are invalid')
            if first_step.get('reviewProfiles') != ['development:ArchitectureReviewAgent', 'development:SpecComplianceReviewAgent']:
                result.errors.append('assets/runtime-contract.json: architectureReview first-step profiles are invalid')
            required_content = first_step.get('requiredContent')
            forbidden_content = first_step.get('forbiddenContent')
            if not isinstance(required_content, list) or len(required_content) < 5 or len(required_content) != len(set(required_content)):
                result.errors.append('assets/runtime-contract.json: architectureReview first-step requiredContent is invalid')
            if not isinstance(forbidden_content, list) or len(forbidden_content) < 3 or len(forbidden_content) != len(set(forbidden_content)):
                result.errors.append('assets/runtime-contract.json: architectureReview first-step forbiddenContent is invalid')
            if first_step.get('passedStatus') != 'SKELETON_REVIEW_PASSED':
                result.errors.append('assets/runtime-contract.json: architectureReview first-step passedStatus is invalid')
            if ar_policy.get('transitionCommand') != 'python3 scripts/long_task.py advance-development-step -g ProjectManagerAgent --task-dir {TASK_DIR}':
                result.errors.append('assets/runtime-contract.json: architectureReview transitionCommand is invalid')
            if second_step.get('id') != 'CONCRETE_IMPLEMENTATION':
                result.errors.append('assets/runtime-contract.json: architectureReview secondStep id is invalid')
            if second_step.get('requiredPredecessorStatus') != 'SKELETON_REVIEW_PASSED':
                result.errors.append('assets/runtime-contract.json: architectureReview second-step predecessor is invalid')
            if second_step.get('developmentGateReviewers') != ['TDDReviewAgent', 'SpecComplianceReviewAgent']:
                result.errors.append('assets/runtime-contract.json: architectureReview second-step development gate Reviewers are invalid')
            if second_step.get('readyForFinalReviewStatus') != 'IMPLEMENTATION_READY_FOR_REVIEW':
                result.errors.append('assets/runtime-contract.json: architectureReview second-step ready status is invalid')
            final_review = ar_policy.get('finalCodeReview')
            if not isinstance(final_review, dict):
                result.errors.append('assets/runtime-contract.json: architectureReview finalCodeReview is invalid')
            else:
                if final_review.get('requiredReviewers') != ['SpecComplianceReviewAgent', 'EngineeringStandardsReviewAgent', 'ArchitectureReviewAgent']:
                    result.errors.append('assets/runtime-contract.json: architectureReview final Reviewers are invalid')
                if final_review.get('passedStatus') != 'IMPLEMENTATION_REVIEW_PASSED':
                    result.errors.append('assets/runtime-contract.json: architectureReview final Review passedStatus is invalid')
                if final_review.get('requiresSameSkeletonBoundary') is not True:
                    result.errors.append('assets/runtime-contract.json: architectureReview final Review must preserve skeleton boundary')
            if ar_policy.get('autoAdvanceAcrossReviewBoundary') is not False:
                result.errors.append('assets/runtime-contract.json: architectureReview must not auto-advance across first Review')
    if not isinstance(git_checkpoint, dict) or git_checkpoint.get('flags') != ['-gc', '--git-checkpoint']:
        result.errors.append('assets/runtime-contract.json: gitCheckpoint mode must use -gc/--git-checkpoint')
    else:
        if git_checkpoint.get('default') is not False:
            result.errors.append('assets/runtime-contract.json: gitCheckpoint default must be false')
        checkpoint_policy = git_checkpoint.get('policy')
        if not isinstance(checkpoint_policy, dict):
            result.errors.append('assets/runtime-contract.json: gitCheckpoint policy must be an object')
        else:
            if checkpoint_policy.get('executorAgent') != 'ProjectManagerAgent':
                result.errors.append('assets/runtime-contract.json: gitCheckpoint executor must be ProjectManagerAgent')
            if checkpoint_policy.get('executionPolicyRef') != 'assets/runtime-contract.json#/executionPolicy':
                result.errors.append('assets/runtime-contract.json: gitCheckpoint executionPolicyRef is invalid')
            if checkpoint_policy.get('autoPush') is not False or checkpoint_policy.get('autoMerge') is not False:
                result.errors.append('assets/runtime-contract.json: gitCheckpoint must not auto push or merge')
            if checkpoint_policy.get('recordFile') != 'task/{TARGET_ID}/git_checkpoints.yaml':
                result.errors.append('assets/runtime-contract.json: gitCheckpoint recordFile is invalid')
            if checkpoint_policy.get('commitTrailers') != ['WK-Checkpoint', 'WK-Revision']:
                result.errors.append('assets/runtime-contract.json: gitCheckpoint trailers are invalid')
    key = _check_git_push_mode(git_push, result)
    if work_modes.get('combinationAllowed') is not True:
        result.errors.append('assets/runtime-contract.json: work modes must be combinable')
    if work_modes.get('stateSource') != 'version/{VERSION}/work.md':
        result.errors.append('assets/runtime-contract.json: work mode stateSource is invalid')
    return (first_step, git_checkpoint, key, policy, values)


def _check_runtime_contract_section_2(ctx: CheckFlowContext) -> None:
    agents = ctx.get('agents')
    command_interface = ctx.get('command_interface')
    contract = ctx.contract
    expected = ctx.get('expected')
    field = ctx.get('field')
    result = ctx.result
    values = ctx.get('values')
    action = first_step = git_checkpoint = key = permission = policy = None
    if not isinstance(command_interface, dict):
        result.errors.append('assets/runtime-contract.json: commandInterface must be an object')
    else:
        if command_interface.get('entry') != '$Common-develop':
            result.errors.append('assets/runtime-contract.json: commandInterface.entry must be $Common-develop')
        field = check_current_agent_interface(command_interface, agents, field, result)
        work_modes = command_interface.get('workModes')
        if not isinstance(work_modes, dict):
            result.errors.append('assets/runtime-contract.json: commandInterface.workModes must be an object')
        else:
            (first_step, git_checkpoint, key, policy, values) = _check_work_modes(
                work_modes, result, values
            )
        (action, field, key, permission, policy, values) = check_task_planning_interface(action, command_interface, contract, field, key, permission, policy, result, values)
    ctx.update(action=action, field=field, first_step=first_step, git_checkpoint=git_checkpoint, key=key, permission=permission, policy=policy, values=values)

def _check_runtime_contract_section_3(ctx: CheckFlowContext) -> None:
    agents = ctx.get('agents')
    command_interface = ctx.get('command_interface')
    contract = ctx.contract
    result = ctx.result
    script_permissions = None
    test_execution = contract.get('testExecution')
    independent_db = test_execution.get('independentDatabase') if isinstance(test_execution, dict) else None
    if not isinstance(independent_db, dict):
        result.errors.append('assets/runtime-contract.json: testExecution.independentDatabase must be an object')
    else:
        expected_groups = {'url': ['TEST_DB_URL'], 'username': ['TEST_DB_USER'], 'password': ['TEST_DB_PASSWORD']}
        if independent_db.get('requiredEnvironmentGroups') != expected_groups:
            result.errors.append('assets/runtime-contract.json: independent database environment groups are invalid')
        if independent_db.get('readyStatus') != 'READY' or independent_db.get('missingStatus') != 'SKIPPED':
            result.errors.append('assets/runtime-contract.json: independent database statuses are invalid')
        if independent_db.get('neverFallbackToBusinessDatabase') is not True:
            result.errors.append('assets/runtime-contract.json: independent database fallback must be disabled')
        if independent_db.get('secretOutputPolicy') != 'VARIABLE_NAMES_ONLY':
            result.errors.append('assets/runtime-contract.json: independent database secret output policy is invalid')
    project_management = contract.get('projectManagement')
    if not isinstance(project_management, dict):
        result.errors.append('assets/runtime-contract.json: projectManagement must be an object')
        project_management = {}
    if project_management.get('taskStateField') != 'project_manager_agent':
        result.errors.append('assets/runtime-contract.json: projectManagement.taskStateField must be project_manager_agent')
    default_manager = project_management.get('defaultAgent')
    allowed_managers = project_management.get('allowedAgents')
    capabilities = project_management.get('capabilities')
    if default_manager not in agents:
        result.errors.append('assets/runtime-contract.json: projectManagement.defaultAgent must be declared')
    if not isinstance(allowed_managers, list) or default_manager not in allowed_managers or any((agent not in agents for agent in (allowed_managers if isinstance(allowed_managers, list) else []))):
        result.errors.append('assets/runtime-contract.json: projectManagement.allowedAgents is invalid')
    if not isinstance(capabilities, list) or not capabilities or any((not isinstance(value, str) or not value for value in (capabilities if isinstance(capabilities, list) else []))) or (len(capabilities) != len(set(capabilities or []))):
        result.errors.append('assets/runtime-contract.json: projectManagement.capabilities must be unique non-empty strings')
    if isinstance(command_interface, dict):
        selector = command_interface.get('currentAgent', {})
        if isinstance(selector, dict) and selector.get('default') != default_manager:
            result.errors.append('assets/runtime-contract.json: current Agent default must equal projectManagement.defaultAgent')
    script_permissions = contract.get('scriptPermissions')
    if not isinstance(script_permissions, dict) or not script_permissions:
        result.errors.append('assets/runtime-contract.json: scriptPermissions must be a non-empty object')
        script_permissions = {}
    try:
        expected_script_permissions = cli_help.declared_permission_ids()
    except (cli_help.CliHelpError, ImportError, RuntimeError, ValueError) as exc:
        result.errors.append(f'cannot derive parser permission catalog: {exc}')
        expected_script_permissions = set()
    # The archive action is implemented by wk's workflow but has no standalone
    # argparse subcommand.
    expected_script_permissions.add('wk.document-archive')
    if set(script_permissions) != expected_script_permissions:
        result.errors.append(f'assets/runtime-contract.json: scriptPermissions keys differ from executable commands: missing={sorted(expected_script_permissions - set(script_permissions))} extra={sorted(set(script_permissions) - expected_script_permissions)}')
    ctx.update(script_permissions=script_permissions)

def _check_runtime_contract_section_4(ctx: CheckFlowContext) -> None:
    action = ctx.get('action')
    agents = ctx.get('agents')
    contract = ctx.contract
    field = ctx.get('field')
    key = ctx.get('key')
    permission = ctx.get('permission')
    phases = ctx.get('phases')
    policy = ctx.get('policy')
    result = ctx.result
    script_permissions = ctx.get('script_permissions')
    agent = None
    allowed_phases = None
    archive_gate_map = None
    criterion = None
    criterion_id = None
    definition = None
    phase = None
    phase_id = None
    phase_ids = None
    policy_id = None
    prefix = None
    profile = None
    profile_id = None
    review_contract = None
    risk_catalog = None
    for (command_id, permission) in script_permissions.items():
        if not isinstance(permission, dict) or permission.get('mode') not in {'STATIC', 'TASK_CONTEXT', 'DECLARED_AGENT'}:
            result.errors.append(f'scriptPermissions.{command_id} has invalid mode')
            continue
        allowed = permission.get('allowedAgents')
        if permission.get('mode') == 'STATIC':
            if not isinstance(allowed, list) or not allowed or any((agent not in agents for agent in allowed)):
                result.errors.append(f'scriptPermissions.{command_id}.allowedAgents is invalid')
        elif permission.get('mode') in {'TASK_CONTEXT', 'DECLARED_AGENT'} and 'allowedAgents' in permission:
            result.errors.append(f"scriptPermissions.{command_id} {permission.get('mode')} must not duplicate allowedAgents")
    auxiliary = contract.get('auxiliaryActions', [])
    if not isinstance(auxiliary, list):
        result.errors.append('assets/runtime-contract.json: auxiliaryActions must be an array')
        auxiliary = []
    auxiliary_ids: set[str] = set()
    for action in auxiliary:
        if not isinstance(action, dict):
            result.errors.append('assets/runtime-contract.json: auxiliary action must be an object')
            continue
        action_id = action.get('id')
        if not isinstance(action_id, str) or not action_id:
            result.errors.append('assets/runtime-contract.json: auxiliary action missing id')
        elif action_id in auxiliary_ids:
            result.errors.append(f'assets/runtime-contract.json: duplicate auxiliary action {action_id}')
        auxiliary_ids.add(str(action_id))
        allowed_action_fields = {'id', 'command', 'description', 'ownerAgent', 'reference'}
        unknown_action_fields = sorted(set(action) - allowed_action_fields)
        if unknown_action_fields:
            result.errors.append(f'auxiliary action {action_id} contains unknown fields: {unknown_action_fields}')
        if action.get('ownerAgent') not in agents:
            result.errors.append(f"auxiliary action {action_id} references undeclared Agent: {action.get('ownerAgent')}")
    phase_ids = [phase.get('id') for phase in phases if isinstance(phase, dict)]
    canonical_phase_ids = [phase.get('id') for phase in phases if isinstance(phase, dict) and phase.get('phaseKind', 'CANONICAL') == 'CANONICAL']
    if len(phase_ids) != len(phases):
        result.errors.append('assets/runtime-contract.json: every phase must be an object')
    if len(phase_ids) != len(set(phase_ids)):
        result.errors.append('assets/runtime-contract.json: duplicate phase id')
    archive_gate_map = contract.get('archiveGateMap')
    if not isinstance(archive_gate_map, dict):
        result.errors.append('assets/runtime-contract.json: archiveGateMap must be an object')
        archive_gate_map = {}
    elif list(archive_gate_map) != canonical_phase_ids:
        result.errors.append('assets/runtime-contract.json: archiveGateMap must cover canonical phases in order')
    elif len(set(archive_gate_map.values())) != len(archive_gate_map):
        result.errors.append('assets/runtime-contract.json: archiveGateMap values must be unique')
    minimal_skipped = set(contract.get('commandInterface', {}).get('workModes', {}).get('minimal', {}).get('skippedPhases', []))
    for phase in phases:
        if not isinstance(phase, dict):
            continue
        for key in ('id', 'ownerAgent', 'artifact', 'requiredReviewerAgents'):
            if key not in phase:
                result.errors.append(f'runtime phase missing {key}: {phase}')
        conditional = phase.get('conditionalReviewerAgents', {})
        if not isinstance(conditional, dict):
            result.errors.append(f"runtime phase {phase.get('id')} conditionalReviewerAgents must be an object")
            conditional = {}
        invalid_flags = sorted(set(conditional.values()) - {'impact_required', 'cross_module_implementation_required'})
        if invalid_flags:
            result.errors.append(f"runtime phase {phase.get('id')} has invalid conditional reviewer flags: {invalid_flags}")
        phase_id = phase.get('id')
        if phase.get('phaseKind', 'CANONICAL') != 'CANONICAL':
            expected_skip_when = None
        else:
            expected_skip_when = 'workMode.minimal' if phase_id in minimal_skipped else None
        if phase.get('skipWhen') != expected_skip_when:
            result.errors.append(f'runtime phase {phase_id} skipWhen must be {expected_skip_when!r}')
        referenced = [phase.get('ownerAgent'), *phase.get('requiredReviewerAgents', []), *conditional.keys()]
        for agent in referenced:
            if agent not in agents:
                result.errors.append(f"runtime phase {phase.get('id')} references undeclared Agent: {agent}")
    risk_catalog = contract.get('riskReviewerCatalog')
    if not isinstance(risk_catalog, dict) or not risk_catalog:
        result.errors.append('assets/runtime-contract.json: riskReviewerCatalog must be non-empty')
        risk_catalog = {}
    risk_agents: set[str] = set()
    for (trigger_id, definition) in risk_catalog.items():
        prefix = f'riskReviewerCatalog.{trigger_id}'
        if not isinstance(trigger_id, str) or not trigger_id or (not isinstance(definition, dict)):
            result.errors.append(f'assets/runtime-contract.json: invalid {prefix}')
            continue
        agent = definition.get('reviewerAgent')
        allowed_phases = definition.get('allowedPhases')
        signals = definition.get('signals')
        if agent not in agents:
            result.errors.append(f'{prefix} references undeclared Agent: {agent}')
        if agent in risk_agents:
            result.errors.append(f'assets/runtime-contract.json: risk reviewer Agent mapped twice: {agent}')
        risk_agents.add(str(agent))
        if not isinstance(allowed_phases, list) or not allowed_phases or any((phase not in phase_ids for phase in allowed_phases)):
            result.errors.append(f'{prefix}.allowedPhases must reference canonical phases')
        if not isinstance(signals, list) or not signals or any((not isinstance(value, str) or not value for value in signals)):
            result.errors.append(f'{prefix}.signals must be a non-empty string array')
    review_contract_ref = contract.get('reviewContract')
    if not isinstance(review_contract_ref, dict):
        result.errors.append('assets/runtime-contract.json: reviewContract must be an object')
        review_contract_ref = {}
    if review_contract_ref.get('path') != 'assets/review-contract.json':
        result.errors.append('assets/runtime-contract.json: reviewContract.path must be assets/review-contract.json')
    if review_contract_ref.get('schemaVersion') != 2:
        result.errors.append('assets/runtime-contract.json: reviewContract.schemaVersion must be 2')
    if review_contract_ref.get('profileKeyFormat') != '<phase>:<reviewerAgent>':
        result.errors.append('assets/runtime-contract.json: reviewContract.profileKeyFormat is invalid')
    if review_contract_ref.get('resultRecord') != 'independentConclusion':
        result.errors.append('assets/runtime-contract.json: reviewContract.resultRecord must be independentConclusion')
    raw_review_contract = load_json(ROOT / 'assets' / 'review-contract.json', result)
    review_contract = None
    if isinstance(raw_review_contract, dict):
        if raw_review_contract.get('schemaVersion') != 2:
            result.errors.append('assets/review-contract.json: schemaVersion must be 2')
        if raw_review_contract.get('evidencePolicyDefaults') != {'minimumEvidenceCount': 1, 'allOfTypes': [], 'anyOfTypes': []}:
            result.errors.append('assets/review-contract.json: evidencePolicyDefaults are invalid')
        if raw_review_contract.get('criterionDefaults') != {'allowNotApplicable': False, 'failureSeverityFloor': 'P1'}:
            result.errors.append('assets/review-contract.json: criterionDefaults are invalid')
        for (policy_id, policy) in (raw_review_contract.get('evidencePolicyCatalog') or {}).items():
            if isinstance(policy, dict):
                if policy.get('minimumEvidenceCount') == 1 or policy.get('allOfTypes') == [] or policy.get('anyOfTypes') == []:
                    result.errors.append(f'review evidence policy {policy_id} redundantly persists a default value')
        for (criterion_id, criterion) in (raw_review_contract.get('criteriaCatalog') or {}).items():
            if isinstance(criterion, dict):
                if criterion.get('allowNotApplicable') is False or criterion.get('failureSeverityFloor') == 'P1':
                    result.errors.append(f'review criterion {criterion_id} redundantly persists a default value')
        for (profile_id, profile) in (raw_review_contract.get('profiles') or {}).items():
            if isinstance(profile, dict) and any((field in profile for field in ('profileId', 'phase', 'reviewerAgent'))):
                result.errors.append(f'review profile {profile_id} redundantly persists fields derivable from its key')
        try:
            review_contract = contract_normalization.materialize_review_contract(raw_review_contract)
        except contract_normalization.ContractNormalizationError as exc:
            result.errors.append(f'assets/review-contract.json: normalization failed: {exc}')
    ctx.update(agent=agent, allowed_phases=allowed_phases, archive_gate_map=archive_gate_map, criterion=criterion, criterion_id=criterion_id, definition=definition, phase=phase, phase_id=phase_id, phase_ids=phase_ids, policy=policy, policy_id=policy_id, prefix=prefix, profile=profile, profile_id=profile_id, review_contract=review_contract, risk_catalog=risk_catalog)

def _check_runtime_contract_section_5(ctx: CheckFlowContext) -> None:
    agents = ctx.get('agents')
    allowed_phases = ctx.get('allowed_phases')
    contract = ctx.contract
    criterion = ctx.get('criterion')
    criterion_id = ctx.get('criterion_id')
    definition = ctx.get('definition')
    field = ctx.get('field')
    first_step = ctx.get('first_step')
    phase = ctx.get('phase')
    phase_id = ctx.get('phase_id')
    phase_ids = ctx.get('phase_ids')
    phases = ctx.get('phases')
    policy = ctx.get('policy')
    policy_id = ctx.get('policy_id')
    prefix = ctx.get('prefix')
    profile = ctx.get('profile')
    profile_id = ctx.get('profile_id')
    result = ctx.result
    review_contract = ctx.get('review_contract')
    risk_catalog = ctx.get('risk_catalog')
    values = ctx.get('values')
    if isinstance(review_contract, dict):
        evidence_types = review_contract.get('evidenceTypes')
        policy_catalog = review_contract.get('evidencePolicyCatalog')
        criteria = review_contract.get('criteriaCatalog')
        profile_templates = review_contract.get('profileTemplates')
        profiles = review_contract.get('profiles')
        machine_gate_criterion_ids = review_contract.get('machineGateCriterionIds')
        criterion_statuses = review_contract.get('criterionStatuses')
        if not isinstance(evidence_types, dict) or not evidence_types:
            result.errors.append('assets/review-contract.json: evidenceTypes must be non-empty')
            evidence_types = {}
        if not isinstance(policy_catalog, dict) or not policy_catalog:
            result.errors.append('assets/review-contract.json: evidencePolicyCatalog must be non-empty')
            policy_catalog = {}
        if not isinstance(criteria, dict) or not criteria:
            result.errors.append('assets/review-contract.json: criteriaCatalog must be non-empty')
            criteria = {}
        if not isinstance(profile_templates, dict):
            result.errors.append('assets/review-contract.json: profileTemplates must be an object')
            profile_templates = {}
        if not isinstance(profiles, dict) or not profiles:
            result.errors.append('assets/review-contract.json: profiles must be non-empty')
            profiles = {}
        if not isinstance(machine_gate_criterion_ids, list) or any((criterion_id not in criteria for criterion_id in machine_gate_criterion_ids)) or len(machine_gate_criterion_ids) != len(set(machine_gate_criterion_ids)):
            result.errors.append('assets/review-contract.json: machineGateCriterionIds must contain unique declared criteria')
            machine_gate_criterion_ids = []
        runtime_criterion_statuses = contract.get('statuses', {}).get('reviewCriterion', [])
        if criterion_statuses != runtime_criterion_statuses:
            result.errors.append('review criterion statuses differ from runtime contract')
        normalized_policies: set[str] = set()
        for (policy_id, policy) in policy_catalog.items():
            prefix = f'review evidence policy {policy_id}'
            if not isinstance(policy_id, str) or not re.fullmatch('EP-[A-F0-9]{10}', policy_id):
                result.errors.append(f'{prefix} must use content-addressed EP-{{HEX10}} namespace')
            if not isinstance(policy, dict):
                result.errors.append(f'{prefix} must be an object')
                continue
            if set(policy) != {'minimumEvidenceCount', 'anyOfTypes', 'allOfTypes'}:
                result.errors.append(f'{prefix} fields are invalid')
            minimum = policy.get('minimumEvidenceCount')
            if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 0:
                result.errors.append(f'{prefix} minimumEvidenceCount is invalid')
            for field in ('anyOfTypes', 'allOfTypes'):
                values = policy.get(field)
                if not isinstance(values, list) or any((value not in evidence_types for value in values)):
                    result.errors.append(f'{prefix} {field} references invalid evidence types')
                elif len(values) != len(set(values)):
                    result.errors.append(f'{prefix} {field} contains duplicates')
            canonical = json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            if canonical in normalized_policies:
                result.errors.append(f'{prefix} duplicates another evidence policy')
            normalized_policies.add(canonical)
        used_policy_refs: set[str] = set()
        for (criterion_id, criterion) in criteria.items():
            prefix = f'review criteria {criterion_id}'
            if not isinstance(criterion_id, str) or not criterion_id or (not isinstance(criterion, dict)):
                result.errors.append(f'{prefix} is invalid')
                continue
            if not re.fullmatch('RC-[A-Z]+-[0-9]{3,}', criterion_id):
                result.errors.append(f'{prefix} must use RC-{{AXIS}}-{{NNN}} namespace')
            allowed_criterion_fields = {'title', 'question', 'allowNotApplicable', 'failureSeverityFloor', 'evidencePolicyRef'}
            unknown = sorted(set(criterion) - allowed_criterion_fields)
            if unknown:
                result.errors.append(f'{prefix} contains unknown fields: {unknown}')
            if not isinstance(criterion.get('title'), str) or not criterion.get('title'):
                result.errors.append(f'{prefix} missing title')
            if not isinstance(criterion.get('question'), str) or not criterion.get('question'):
                result.errors.append(f'{prefix} missing question')
            if criterion.get('allowNotApplicable') not in {True, False}:
                result.errors.append(f'{prefix} allowNotApplicable must be boolean')
            if criterion.get('failureSeverityFloor') not in {'P0', 'P1', 'P2'}:
                result.errors.append(f'{prefix} failureSeverityFloor is invalid')
            policy_ref = criterion.get('evidencePolicyRef')
            if not isinstance(policy_ref, str) or policy_ref not in policy_catalog:
                result.errors.append(f'{prefix} evidencePolicyRef is invalid')
            else:
                used_policy_refs.add(policy_ref)
        unused_policy_refs = sorted(set(policy_catalog) - used_policy_refs)
        if unused_policy_refs:
            result.errors.append(f'review evidence policies are not referenced by any criterion: {unused_policy_refs}')
        normalized_templates: set[tuple[str, ...]] = set()
        for (template_id, template) in profile_templates.items():
            prefix = f'review profile template {template_id}'
            if not isinstance(template_id, str) or not re.fullmatch('PT-[A-Z0-9-]+', template_id):
                result.errors.append(f'{prefix} id is invalid')
            if not isinstance(template, dict) or set(template) != {'criterionIds'}:
                result.errors.append(f'{prefix} must contain only criterionIds')
                continue
            criterion_ids = template.get('criterionIds')
            if not isinstance(criterion_ids, list) or not criterion_ids or any((cid not in criteria for cid in criterion_ids)):
                result.errors.append(f'{prefix} criterionIds are invalid')
                continue
            if len(criterion_ids) != len(set(criterion_ids)):
                result.errors.append(f'{prefix} criterionIds contain duplicates')
            normalized = tuple(criterion_ids)
            if normalized in normalized_templates:
                result.errors.append(f'{prefix} duplicates another profile template')
            normalized_templates.add(normalized)
        expected_profile_pairs: set[tuple[str, str]] = set()
        for phase in phases:
            if not isinstance(phase, dict):
                continue
            phase_id = phase.get('id')
            for reviewer in phase.get('requiredReviewerAgents', []):
                expected_profile_pairs.add((phase_id, reviewer))
            for reviewer in phase.get('conditionalReviewerAgents', {}):
                expected_profile_pairs.add((phase_id, reviewer))
        for definition in risk_catalog.values():
            if not isinstance(definition, dict):
                continue
            reviewer = definition.get('reviewerAgent')
            for phase_id in definition.get('allowedPhases', []):
                expected_profile_pairs.add((phase_id, reviewer))
        architecture_review_policy = ((contract.get('commandInterface') or {}).get('workModes') or {}).get('architectureReview', {}).get('policy', {})
        first_step = architecture_review_policy.get('firstStep') if isinstance(architecture_review_policy, dict) else {}
        if isinstance(first_step, dict):
            for profile_id in first_step.get('reviewProfiles', []):
                if isinstance(profile_id, str) and ':' in profile_id:
                    (phase_id, reviewer) = profile_id.split(':', 1)
                    expected_profile_pairs.add((phase_id, reviewer))
        actual_profile_pairs: set[tuple[str, str]] = set()
        profile_criterion_ids: set[str] = set()
        template_usage: dict[str, int] = {template_id: 0 for template_id in profile_templates}
        direct_profile_sets: dict[tuple[str, ...], str] = {}
        for (profile_key, profile) in profiles.items():
            prefix = f'review profile {profile_key}'
            if not isinstance(profile, dict):
                result.errors.append(f'{prefix} must be an object')
                continue
            allowed_profile_fields = {'profileId', 'phase', 'reviewerAgent', 'purpose', 'scopeSourceTypes', 'criterionIds', 'templateRef', 'axes'}
            unknown = sorted(set(profile) - allowed_profile_fields)
            if unknown:
                result.errors.append(f'{prefix} contains unknown fields: {unknown}')
            phase_id = profile.get('phase')
            reviewer = profile.get('reviewerAgent')
            expected_key = f'{phase_id}:{reviewer}'
            if profile_key != expected_key or profile.get('profileId') != expected_key:
                result.errors.append(f'{prefix} key/profileId must be {expected_key}')
            if phase_id not in phase_ids:
                result.errors.append(f'{prefix} references invalid phase: {phase_id}')
            if reviewer not in agents:
                result.errors.append(f'{prefix} references undeclared Agent: {reviewer}')
            purpose = profile.get('purpose')
            scope_sources = profile.get('scopeSourceTypes')
            template_ref = profile.get('templateRef')
            direct_criterion_ids = profile.get('criterionIds')
            if template_ref is not None and direct_criterion_ids is not None:
                result.errors.append(f'{prefix} cannot define both templateRef and criterionIds')
            if template_ref is not None:
                template = profile_templates.get(template_ref)
                criterion_ids = template.get('criterionIds') if isinstance(template, dict) else None
                if not isinstance(template_ref, str) or not isinstance(template, dict):
                    result.errors.append(f'{prefix} templateRef is invalid')
                else:
                    template_usage[template_ref] = template_usage.get(template_ref, 0) + 1
            else:
                criterion_ids = direct_criterion_ids
                if isinstance(criterion_ids, list):
                    normalized_set = tuple(criterion_ids)
                    previous = direct_profile_sets.get(normalized_set)
                    if previous:
                        result.errors.append(f'{prefix} duplicates criterionIds from {previous}; use profileTemplates')
                    else:
                        direct_profile_sets[normalized_set] = profile_key
            if not isinstance(purpose, str) or not purpose:
                result.errors.append(f'{prefix} missing purpose')
            if not isinstance(scope_sources, list) or not scope_sources or any((not isinstance(v, str) or not v for v in scope_sources)):
                result.errors.append(f'{prefix} scopeSourceTypes must be non-empty')
            if not isinstance(criterion_ids, list) or not criterion_ids or any((cid not in criteria for cid in criterion_ids)):
                result.errors.append(f'{prefix} criterionIds are invalid')
            elif len(criterion_ids) != len(set(criterion_ids)):
                result.errors.append(f'{prefix} criterionIds contain duplicates')
            else:
                profile_criterion_ids.update(criterion_ids)
            if isinstance(phase_id, str) and isinstance(reviewer, str):
                actual_profile_pairs.add((phase_id, reviewer))
        risk_review_templates = review_contract.get('riskReviewTemplates')
        if not isinstance(risk_review_templates, dict):
            result.errors.append('assets/review-contract.json: riskReviewTemplates must be an object')
            risk_review_templates = {}
        for (risk_id, definition) in risk_review_templates.items():
            prefix = f'risk Review template {risk_id}'
            if not isinstance(definition, dict):
                result.errors.append(f'{prefix} must be an object')
                continue
            reviewer = definition.get('reviewerAgent')
            allowed_phases = definition.get('allowedPhases')
            template_ref = definition.get('templateRef')
            criterion_ids = definition.get('criterionIds')
            if template_ref is not None:
                template = profile_templates.get(template_ref)
                criterion_ids = template.get('criterionIds') if isinstance(template, dict) else None
                if isinstance(template_ref, str) and isinstance(template, dict):
                    template_usage[template_ref] = template_usage.get(template_ref, 0) + 1
            if reviewer not in agents:
                result.errors.append(f'{prefix} references undeclared Agent: {reviewer}')
            if not isinstance(allowed_phases, list) or not allowed_phases or any((phase_id not in phase_ids for phase_id in allowed_phases)):
                result.errors.append(f'{prefix}.allowedPhases are invalid')
                allowed_phases = []
            if not isinstance(criterion_ids, list) or not criterion_ids or any((cid not in criteria for cid in criterion_ids)):
                result.errors.append(f'{prefix}.criterionIds are invalid')
                criterion_ids = []
            profile_criterion_ids.update(criterion_ids)
            for phase_id in allowed_phases:
                actual_profile_pairs.add((phase_id, reviewer))
        underused_templates = sorted((template_id for (template_id, usage) in template_usage.items() if usage < 1))
        if underused_templates:
            result.errors.append(f'review profile templates must be used by a Core Profile or Risk Template: {underused_templates}')
        if actual_profile_pairs != expected_profile_pairs:
            result.errors.append(f'review acceptance profiles do not exactly cover runtime reviewer assignments: missing={sorted(expected_profile_pairs - actual_profile_pairs)} extra={sorted(actual_profile_pairs - expected_profile_pairs)}')
        machine_gate_set = set(machine_gate_criterion_ids)
        duplicated_execution = sorted(profile_criterion_ids & machine_gate_set)
        if duplicated_execution:
            result.errors.append(f'review criteria cannot be both profile-driven and machine-gate-only: {duplicated_execution}')
        orphaned_criteria = sorted(set(criteria) - profile_criterion_ids - machine_gate_set)
        if orphaned_criteria:
            result.errors.append(f'review criteria are not executable by any profile or machine gate: {orphaned_criteria}')
    ctx.update(definition=definition, phase=phase)

def _check_runtime_contract_section_6(ctx: CheckFlowContext) -> None:
    agent = ctx.get('agent')
    archive_gate_map = ctx.get('archive_gate_map')
    contract = ctx.contract
    definition = ctx.get('definition')
    git_checkpoint = ctx.get('git_checkpoint')
    phase = ctx.get('phase')
    phases = ctx.get('phases')
    result = ctx.result
    risk_catalog = ctx.get('risk_catalog')
    long_task = import_long_task(result)
    if long_task is not None:
        if contract.get('longTaskSchemaVersion') != long_task.LONG_TASK_SCHEMA_VERSION:
            result.errors.append('runtime longTaskSchemaVersion differs from long_task script')
        selectable_phase_ids = [phase.get('id') for phase in phases if isinstance(phase, dict) and phase.get('phaseKind', 'CANONICAL') == 'CANONICAL']
        if selectable_phase_ids != list(long_task.PHASES):
            result.errors.append(f'runtime selectable phases differ from long_task.PHASES: runtime={selectable_phase_ids} script={long_task.PHASES}')
        runtime_task_statuses = set(contract.get('statuses', {}).get('task', []))
        if runtime_task_statuses != set(long_task.TASK_STATUSES):
            result.errors.append('runtime task statuses differ from long_task.TASK_STATUSES')
        runtime_reviewers = {phase['id']: set(phase.get('requiredReviewerAgents', [])) for phase in phases if phase.get('requiredReviewerAgents')}
        if runtime_reviewers != long_task.COLLABORATION_REVIEWERS:
            result.errors.append('runtime reviewers differ from long_task.COLLABORATION_REVIEWERS')
        runtime_conditional = {phase['id']: dict(phase.get('conditionalReviewerAgents', {})) for phase in phases if phase.get('conditionalReviewerAgents')}
        if runtime_conditional != long_task.CONDITIONAL_REVIEWERS:
            result.errors.append('runtime conditional reviewers differ from long_task.CONDITIONAL_REVIEWERS')
        script_risk = {agent: (trigger, phases) for (agent, (trigger, phases)) in long_task.RISK_REVIEWERS_BY_AGENT.items()}
        expected_risk = {definition['reviewerAgent']: (trigger, set(definition['allowedPhases'])) for (trigger, definition) in risk_catalog.items() if isinstance(definition, dict) and isinstance(definition.get('reviewerAgent'), str)}
        if script_risk != expected_risk:
            result.errors.append('runtime riskReviewerCatalog differs from long_task risk reviewers')
    merge_docs = import_merge_docs(result)
    baseline_guard = import_baseline_guard(result)
    if merge_docs is not None and tuple(archive_gate_map.values()) != tuple(merge_docs.ARCHIVE_GATE_KEYS):
        result.errors.append('runtime archiveGateMap differs from merge_docs.ARCHIVE_GATE_KEYS')
    renderer = import_relationship_renderer(result)
    review_cli = import_review_contract(result)
    review_session_cli = import_review_session(result)
    manual_review = import_manual_review(result)
    context_resolver = import_context_resolver(result)
    ai_context = import_ai_context(result)
    requirement_doc = import_requirement_doc(result)
    business_flow = import_business_flow(result)
    git_checkpoint = import_git_checkpoint(result)
    doc_layout = import_doc_layout(result)
    parser_checks = []
    if long_task is not None:
        parser_checks.append(('long_task.py', long_task.build_parser()))
    if merge_docs is not None:
        parser_checks.append(('merge_docs.py', merge_docs.parser()))
    if renderer is not None:
        parser_checks.append(('render_relationships.py', renderer.parser()))
    if review_cli is not None:
        parser_checks.append(('review_contract.py', review_cli.build_parser()))
    if review_session_cli is not None:
        parser_checks.append(('review_session.py', review_session_cli.build_parser()))
    if long_task is not None:
        parser_checks.extend([('evidence.py', long_task.evidence.build_parser()), ('acceptance.py', long_task.acceptance.build_parser()), ('risk_detect.py', long_task.risk_detect.build_parser())])
    if manual_review is not None:
        parser_checks.append(('manual_review.py', manual_review.build_parser()))
    if context_resolver is not None:
        parser_checks.append(('context_resolver.py', context_resolver.build_parser()))
    if ai_context is not None:
        parser_checks.append(('ai_context.py', ai_context.build_parser()))
    if requirement_doc is not None:
        parser_checks.append(('requirement_doc.py', requirement_doc.build_parser()))
    parser_checks.append(('design_doc.py', design_doc.build_parser()))
    parser_checks.append(('case_doc.py', case_doc.build_parser()))
    if business_flow is not None:
        parser_checks.append(('business_flow.py', business_flow.parser()))
    if git_checkpoint is not None:
        parser_checks.append(('git_checkpoint.py', git_checkpoint.build_parser()))
    if doc_layout is not None:
        parser_checks.append(('doc_layout.py', doc_layout.build_parser()))
    if baseline_guard is not None:
        parser_checks.append(('baseline_guard.py', baseline_guard.build_parser()))
    parser_checks.append(('cli_help.py', cli_help.build_parser()))
    parser_checks.append(('check_skill.py', parser()))
    for (label, command_parser) in parser_checks:
        if not agent_context.parser_has_agent_selector(command_parser):
            result.errors.append(f'{label}: every executable command must parse -g/--agent')
    result.ok('Runtime lifecycle, statuses, Agents, review profiles, archive gates, risk reviewers, and scripts agree')

def check_runtime_contract(result: CheckResult) -> tuple[dict[str, Any], set[str]]:
    raw_contract = load_json(ASSETS / 'runtime-contract.json', result)
    if not isinstance(raw_contract, dict):
        return ({}, set())
    if raw_contract.get('schemaVersion') != 2:
        result.errors.append('assets/runtime-contract.json: schemaVersion must be 2')
    if 'scriptPermissions' in raw_contract:
        result.errors.append('assets/runtime-contract.json: normalized v2 must not persist expanded scriptPermissions')
    permission_profiles = raw_contract.get('scriptPermissionProfiles')
    permission_assignments = raw_contract.get('scriptPermissionAssignments')
    if not isinstance(permission_profiles, dict) or not permission_profiles:
        result.errors.append('assets/runtime-contract.json: scriptPermissionProfiles must be non-empty')
    if not isinstance(permission_assignments, dict) or not permission_assignments:
        result.errors.append('assets/runtime-contract.json: scriptPermissionAssignments must be non-empty')
    try:
        contract = contract_normalization.materialize_runtime_contract(raw_contract)
    except contract_normalization.ContractNormalizationError as exc:
        result.errors.append(f'assets/runtime-contract.json: normalization failed: {exc}')
        return (raw_contract, set())
    if contract.get('longTaskSchemaVersion') != 2:
        result.errors.append('assets/runtime-contract.json: longTaskSchemaVersion must be 2')
    phases = contract.get('canonicalPhases')
    agents = contract.get('agents')
    if not isinstance(phases, list) or not phases:
        result.errors.append('assets/runtime-contract.json: canonicalPhases must be non-empty')
        return (contract, set())
    ctx = CheckFlowContext(contract, result, {"agents": agents, "phases": phases})
    for check in (
        _check_runtime_contract_section_1,
        _check_runtime_contract_section_2,
        _check_runtime_contract_section_3,
        _check_runtime_contract_section_4,
        _check_runtime_contract_section_5,
        _check_runtime_contract_section_6,
    ):
        check(ctx)
    return (contract, set(ctx.get("agents", [])))
