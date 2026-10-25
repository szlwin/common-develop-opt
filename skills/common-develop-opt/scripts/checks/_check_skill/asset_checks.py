"""Asset checks for the internal runtime."""
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
from checks._check_skill.routing import load_materialized_routing
def check_incremental_validity_contract(contract: dict[str, Any], result: CheckResult) -> None:
    path = ROOT / "assets" / 'runtime/incremental-validity-contract.json'
    data = load_json(path, result)
    if not isinstance(data, dict):
        return
    expected_classes = [
        "PROCESS_ONLY", "DOCUMENTATION_ONLY", "TEST_ONLY", "CONFIG",
        "PRODUCTION", "SEMANTIC_AUTHORITY", "COMPOSITE", "UNKNOWN",
    ]
    if data.get("schemaVersion") != 1 or data.get("authority") != "assets/runtime/incremental-validity-contract.json":
        result.errors.append("incremental-validity contract identity is invalid")
    if data.get("changeClasses") != expected_classes:
        result.errors.append("incremental-validity changeClasses are invalid")
    if data.get("unknownImpactPolicy") != "CONSERVATIVE_FULL_PROFILE":
        result.errors.append("incremental-validity unknown impact policy must fail conservatively")
    process_only = data.get("processOnlyPolicy") if isinstance(data.get("processOnlyPolicy"), dict) else {}
    if process_only.get("semanticArtifactChanged") is not False or process_only.get("productionChanged") is not False:
        result.errors.append("PROCESS_ONLY must forbid semantic and production change")
    review_policy = data.get("reviewPolicy") if isinstance(data.get("reviewPolicy"), dict) else {}
    if review_policy.get("reviewResultReuseAcrossExactRevision") is not False:
        result.errors.append("Review Result reuse across exact revision must remain forbidden")
    if review_policy.get("criterionEvidenceIdReuseAcrossExactRevision") is not False:
        result.errors.append("Criterion Evidence ID reuse across exact revision must remain forbidden")
    if review_policy.get("unaffectedEvidenceSourceReuse") != "CONDITIONAL_RECAPTURE_OR_REBIND":
        result.errors.append("unaffected Evidence source reuse must require current-revision recapture/rebind")
    revision_policy = data.get("versionRevisionPolicy") if isinstance(data.get("versionRevisionPolicy"), dict) else {}
    if revision_policy.get("stableVersionDirectory") is not True or revision_policy.get("appendOnlyCanonicalChanges") is not True:
        result.errors.append("version revisions must stay in one stable directory and append canonical changes")
    if revision_policy.get("sameVersionChangeDomains") != ["requirement", "flow", "business_model", "design", "development", "testing"]:
        result.errors.append("requirement, flow, business-model, design, development, and testing must share one version directory")
    if revision_policy.get("newVersionRequiresExplicitUserRequest") is not True:
        result.errors.append("new version directories require an explicit user request")
    boundary = revision_policy.get("reopenBoundaryDiscovery") if isinstance(revision_policy.get("reopenBoundaryDiscovery"), dict) else {}
    if boundary.get("direction") != "UPSTREAM" or boundary.get("readOnly") is not True:
        result.errors.append("reopen boundary must be discovered read-only toward upstream phases")
    if boundary.get("userConfirmationBeforeExpansion") is not True or boundary.get("mutationsBeforeBoundaryFixed") is not False:
        result.errors.append("upstream reopen expansion requires user confirmation before any mutation")
    if boundary.get("singleReopenFromEarliestAffectedPhase") is not True or boundary.get("implementationDirection") != "DOWNSTREAM":
        result.errors.append("reopen must happen once from the earliest affected phase and then proceed downstream")
    if revision_policy.get("neverReuseRetiredRevisionNumbers") is not True:
        result.errors.append("retired revision numbers must not be reused")
    historical_policy = data.get("historicalEvidencePolicy") if isinstance(data.get("historicalEvidencePolicy"), dict) else {}
    if historical_policy.get("ordinaryGateScope") != "CURRENT_ITERATION_CURRENT_REVISION":
        result.errors.append("ordinary Evidence gates must be scoped to the current iteration and revision")
    if historical_policy.get("bulkStatusMigrationOnReopen") is not False or historical_policy.get("historicalDigestFailureBlocksCurrentAttempt") is not False:
        result.errors.append("reopen must not require bulk historical Evidence migration or block on old digests")
    if review_policy.get("newIndependentConclusionRequiredWhenReviewRevisionChanges") is not True:
        result.errors.append("new exact Review revision must require a new independent conclusion")
    adapter = data.get("adapterProtocol") if isinstance(data.get("adapterProtocol"), dict) else {}
    if adapter.get("technologyNeutral") is not True or adapter.get("optional") is not True:
        result.errors.append("impact adapter protocol must remain optional and technology-neutral")
    if adapter.get("confidenceDomain") != ["HIGH", "MEDIUM", "LOW", "UNKNOWN"]:
        result.errors.append("impact adapter confidence domain is invalid")
    derived = data.get("derivedPlan") if isinstance(data.get("derivedPlan"), dict) else {}
    if derived.get("authority") != "DERIVED" or derived.get("mayAuthorizeLifecycleGate") is not False:
        result.errors.append("incremental revalidation plan must be DERIVED and may not authorize lifecycle gates")
    amendment = data.get("amendmentPolicy") if isinstance(data.get("amendmentPolicy"), dict) else {}
    if amendment.get("command") != "long_task.amend-phase":
        result.errors.append("incremental-validity amendment command is invalid")
    if amendment.get("currentPhaseOnly") is not True or amendment.get("requiresDependencyClosedTaskSet") is not True:
        result.errors.append("bounded amendments must remain current-phase and dependency-closed")
    if amendment.get("fallbackCommand") != "long_task.reopen-phase":
        result.errors.append("unsafe amendments must fall back to reopen-phase")
    orchestration = ((contract.get("commandInterface") or {}).get("orchestration") or {}) if isinstance(contract, dict) else {}
    if not isinstance(orchestration.get("impact"), dict) or orchestration["impact"].get("command") != "impact":
        result.errors.append("runtime contract must declare wk impact orchestration")
    permissions = contract.get("scriptPermissions", {}) if isinstance(contract, dict) else {}
    expected_permission = {
        "mode": "STATIC",
        "allowedAgents": ["ProjectManagerAgent", "CompletionVerificationAgent", "EngineeringStandardsReviewAgent"],
    }
    if permissions.get("wk.impact") != expected_permission:
        result.errors.append("runtime contract wk.impact permission is invalid")
    source_map = load_json(ROOT / "ai" / "source-map.json", result)
    if isinstance(source_map, dict):
        if (source_map.get("authoritativeSources") or {}).get("incrementalValidity") != "assets/runtime/incremental-validity-contract.json":
            result.errors.append("source map incremental validity authority pointer is invalid")
        source = (source_map.get("sources") or {}).get("incrementalValidity")
        if not isinstance(source, dict) or source.get("impact") != 'scripts/document_core/impact_analysis.py':
            result.errors.append("source map incremental validity impact pointer is invalid")
    for relative in (
        'scripts/document_core/impact_analysis.py',
        'scripts/runtime/_long_task/revision_provenance.py',
        "tests/test_evidence_validity.py",
    ):
        if not checked_path(ROOT / relative).is_file():
            result.errors.append(f"missing incremental-validity component: {relative}")
    if not result.errors:
        result.ok("Incremental validity, impact adapter, and tighten-only gate integration agree")


def check_structured_schemas(contract: dict[str, Any], result: CheckResult) -> None:
    if tuple(merge_docs.PROJECT_FACT_ARTIFACTS) != ("business_model",):
        result.errors.append("project fact archive must exclude API/DB full documents")
    if tuple(merge_docs.VERSION_DELTA_ARTIFACTS) != ("api", "db"):
        result.errors.append("API/DB must be declared as version-local delta artifacts")
    for retired_template in ("api.yaml", "db.yaml"):
        if (STRUCTURED / retired_template).exists():
            result.errors.append(f"full API/DB runtime template must not return: structured-docs/{retired_template}")
    schemas = {
        "business-model.schema.json": "business_model.yaml",
        "api.schema.json": "api.yaml",
        "db.schema.json": "db.yaml",
        "changeset.schema.json": "changeset.yaml",
        "dependency-impact.schema.json": "dependency_impact.yaml",
        "business-flow.schema.json": "business-flow.example.yaml",
    }
    if jsonschema is None:
        result.errors.append("jsonschema is required to validate Skill schemas")
        return
    for schema_name, example_name in schemas.items():
        schema = load_json(STRUCTURED / schema_name, result)
        example = load_yaml(STRUCTURED_FIXTURES / example_name, result)
        if schema is None or example is None:
            continue
        try:
            jsonschema.Draft202012Validator.check_schema(schema)
            jsonschema.validate(example, schema)
        except jsonschema.ValidationError as exc:
            result.errors.append(f"{example_name} does not match {schema_name}: {exc.message}")
        except jsonschema.SchemaError as exc:
            result.errors.append(f"{schema_name} is invalid: {exc.message}")
    renderer = import_relationship_renderer(result)
    dependency_example = load_yaml(STRUCTURED_FIXTURES / "dependency_impact.yaml", result)
    if renderer is not None and isinstance(dependency_example, dict):
        try:
            renderer.validate_artifact(dependency_example)
            rendered = renderer.render(dependency_example)
            if "flowchart LR" not in rendered or "sequenceDiagram" not in rendered:
                result.errors.append("dependency-impact renderer did not produce required Mermaid views")
        except (ValueError, KeyError, TypeError) as exc:
            result.errors.append(f"dependency_impact.yaml semantic validation failed: {exc}")

    business_flow_changeset_schema = load_json(STRUCTURED / "business-flow.changeset.schema.json", result)
    if business_flow_changeset_schema is not None:
        try:
            jsonschema.Draft202012Validator.check_schema(business_flow_changeset_schema)
        except jsonschema.SchemaError as exc:
            result.errors.append(f"business-flow.changeset.schema.json is invalid: {exc.message}")

    version_delta_schema = load_json(STRUCTURED / "version-delta.schema.json", result)
    if version_delta_schema is not None:
        try:
            jsonschema.Draft202012Validator.check_schema(version_delta_schema)
        except jsonschema.SchemaError as exc:
            result.errors.append(f"version-delta.schema.json is invalid: {exc.message}")

    for schema_path in (
        ASSETS / 'documents/artifact-relation.schema.json',
        STRUCTURED / "dependency-impact.changeset.schema.json",
        STRUCTURED / "design-topic.schema.json",
        STRUCTURED / "archive-manifest.schema.json",
        ASSETS / 'runtime/project-governance.schema.json',
        ASSETS / 'runtime/project-constraints.schema.json',
    ):
        schema = load_json(schema_path, result)
        if schema is None:
            continue
        try:
            jsonschema.Draft202012Validator.check_schema(schema)
        except jsonschema.SchemaError as exc:
            result.errors.append(f"{schema_path.name} is invalid: {exc.message}")

    merge_schema = load_json(STRUCTURED / "merge-plan.schema.json", result)
    if merge_schema is not None:
        try:
            jsonschema.Draft202012Validator.check_schema(merge_schema)
        except jsonschema.SchemaError as exc:
            result.errors.append(f"merge-plan.schema.json is invalid: {exc.message}")
        required_plan_fields = set(merge_schema.get("required", []))
        for field in (
            "projectFactArtifacts", "versionDeltaArtifacts", "artifactMerges", "conflictDecisions"
        ):
            if field not in required_plan_fields:
                result.errors.append(f"merge plan must require deterministic archive field: {field}")
        merge_mode = merge_schema.get("properties", {}).get("mergeMode", {}).get("const")
        if merge_mode != "VERSION_DELTA_ARCHIVE":
            result.errors.append("merge plan must use VERSION_DELTA_ARCHIVE mode")
        expected_gates = list(contract.get("archiveGateMap", {}).values())
        gate_schema = (
            merge_schema.get("$defs", {})
            .get("archiveGate", {})
            .get("properties", {})
            .get("gates", {})
        )
        if gate_schema.get("required") != expected_gates:
            result.errors.append("merge plan archiveGate required gates differ from runtime archiveGateMap")
        if list(gate_schema.get("properties", {})) != expected_gates:
            result.errors.append("merge plan archiveGate properties differ from runtime archiveGateMap")
        work_mode_schema = merge_schema.get("$defs", {}).get("workMode", {})
        expected_work_mode_fields = [
            "modelCode", "pageDesign", "minimal", "auto", "architectureReview", "gitCheckpoint", "gitPush",
            "reviewOnly", "lightweight", "lightweightWaived", "skippedPhases", "workRef", "workDigest",
        ]
        if work_mode_schema.get("required") != expected_work_mode_fields:
            result.errors.append("merge plan workMode required fields differ from current runtime work modes")
        if list(work_mode_schema.get("properties", {})) != expected_work_mode_fields:
            result.errors.append("merge plan workMode properties differ from current runtime work modes")
        business_flow_merge_schema = merge_schema.get("$defs", {}).get("businessFlowMerge", {})
        expected_business_flow_fields = [
            "flowGroup", "baseRevision", "resultRevision", "changeSetIds", "affectedFlowIds",
            "sourceRefs", "targetYamlRef", "targetMarkdownRef", "previewYamlRef",
            "previewMarkdownRef", "yamlDigest", "markdownDigest",
        ]
        if business_flow_merge_schema.get("required") != expected_business_flow_fields:
            result.errors.append("merge plan businessFlowMerge required fields differ from current flow archive contract")
        if list(business_flow_merge_schema.get("properties", {})) != expected_business_flow_fields:
            result.errors.append("merge plan businessFlowMerge properties differ from current flow archive contract")
    frontend_template = load_json(ASSETS / 'package/frontend-code-spec.template.json', result)
    if isinstance(frontend_template, dict):
        required = {"meta", "componentMapping", "api", "dataSources", "codeRules", "commentRequirements", "confirmItems"}
        missing = sorted(required - set(frontend_template))
        if missing:
            result.errors.append(f"frontend-code-spec.template.json missing keys: {missing}")
    result.ok("Structured schemas, examples, relationship semantics, and code-spec templates validate")


def check_task_template(contract: dict[str, Any], result: CheckResult) -> None:
    """Validate programmatic task-state initialization and long-task records."""
    expected_schema = contract.get("longTaskSchemaVersion")
    default_current_agent = contract.get("commandInterface", {}).get("currentAgent", {}).get("default")
    long_task = import_long_task(result)
    if long_task is None:
        return
    try:
        state = long_task.build_initial_task_state(
            "TARGET",
            "V_TEST",
            str(default_current_agent),
            {
                "ref": "version/V_TEST/work.md",
                "digest": "0" * 64,
                "model_code": False,
                "page_design": False,
                "minimal": False,
                "auto": False,
                "architecture_review": False,
                "git_checkpoint": False,
            },
            "2026-01-01T00:00:00Z",
        )
    except Exception as exc:
        result.errors.append(f"programmatic task-state initialization failed: {exc}")
        return
    expected = [
        item["id"] for item in contract.get("canonicalPhases", [])
        if isinstance(item, dict) and item.get("phaseKind", "CANONICAL") == "CANONICAL"
    ]
    if state.get("schema_version") != expected_schema:
        result.errors.append(f"initial task state must use schema_version {expected_schema}")
    if state.get("current_agent") != default_current_agent:
        result.errors.append("initial task state current_agent must match runtime default")
    project_management = contract.get("projectManagement", {})
    if state.get("project_manager_agent") != project_management.get("defaultAgent"):
        result.errors.append("initial task state project_manager_agent must match runtime projectManagement.defaultAgent")
    actual_fields = set(state)
    extra_fields = actual_fields - long_task.TASK_STATE_FIELDS
    if extra_fields:
        result.errors.append(f"initial task state contains fields outside record contract: extra={sorted(extra_fields)}")
    required_sparse_fields = {
        "schema_version", "target_id", "version", "task_status",
        "current_phase", "current_round", "current_agent",
        "project_manager_agent", "execution_mode", "active_task_ids",
        "current_attempt_id", "work_mode", "request_intake",
        "architecture_review", "artifact_revisions",
        "collaboration_reviews", "open_issue_ids", "last_gate",
        "next_action", "next_agent", "checkpoint_at",
    }
    missing_required = required_sparse_fields - actual_fields
    if missing_required:
        result.errors.append(f"initial task state missing required fields: missing={sorted(missing_required)}")
    if list(state.get("artifact_revisions", {})) != expected:
        result.errors.append("initial task state artifact_revisions do not follow canonical phase order")
    expected_review_phases = {
        item["id"] for item in contract.get("canonicalPhases", [])
        if isinstance(item, dict) and item.get("phaseKind", "CANONICAL") == "CANONICAL" and item.get("requiredReviewerAgents")
    }
    actual_review_phases = set(state.get("collaboration_reviews", {}))
    if actual_review_phases != expected_review_phases:
        result.errors.append("initial task state collaboration review phases differ from runtime contract")
    for phase, review in state.get("collaboration_reviews", {}).items():
        for policy_field in ("required_reviewers", "conditional_reviewers"):
            if policy_field in review:
                result.errors.append(
                    f"initial task state must derive {policy_field} from runtime contract: {phase}"
                )
    record_contract = load_json(ASSETS / 'storage/long-task' / "record-contract.json", result)
    if isinstance(record_contract, dict):
        if record_contract.get("schemaVersion") != expected_schema:
            result.errors.append("long-task record contract schemaVersion differs from runtime contract")
        if record_contract.get("longTaskSchemaVersion") != expected_schema:
            result.errors.append("long-task record contract version differs from runtime contract")
        records = record_contract.get("records")
        expected_records = {
            "taskState", "artifactRevision", "collaborationReview", "additionalReviewer",
            "independentConclusion", "reviewResult", "criterionResult", "reviewEvidence", "taskPlanItem", "traceabilityItem", "reviewIssue",
            "stageOutcome", "handoff", "staleEvent", "taskAttempt", "workEvent", "workModeState",
            "architectureReviewState", "requestIntakeState", "taskEvent", "eventProjection", "autoRemediationProjection", "amendmentEvent", "traceabilityRevalidation",
        }
        if not isinstance(records, dict) or set(records) != expected_records:
            result.errors.append("long-task record contract record set differs from runtime implementation")
        if isinstance(records, dict):
            field_map = {
                "taskState": long_task.TASK_STATE_FIELDS,
                "artifactRevision": long_task.ARTIFACT_REVISION_FIELDS,
                "collaborationReview": long_task.COLLABORATION_REVIEW_FIELDS,
                "additionalReviewer": long_task.ADDITIONAL_REVIEWER_FIELDS,
                "independentConclusion": long_task.INDEPENDENT_CONCLUSION_FIELDS,
                "reviewResult": long_task.REVIEW_RESULT_FIELDS,
                "criterionResult": long_task.CRITERION_RESULT_FIELDS,
                "reviewEvidence": long_task.REVIEW_EVIDENCE_FIELDS,
                "taskPlanItem": long_task.TASK_PLAN_FIELDS,
                "traceabilityItem": long_task.TRACEABILITY_FIELDS,
                "reviewIssue": long_task.REVIEW_ISSUE_FIELDS,
                "stageOutcome": long_task.STAGE_OUTCOME_FIELDS,
                "handoff": long_task.HANDOFF_FIELDS,
                "staleEvent": long_task.STALE_EVENT_FIELDS,
                "taskAttempt": long_task.TASK_ATTEMPT_FIELDS,
                "workEvent": long_task.WORK_EVENT_FIELDS,
                "workModeState": long_task.WORK_MODE_STATE_FIELDS,
                "architectureReviewState": long_task.ARCHITECTURE_REVIEW_STATE_FIELDS,
                "requestIntakeState": long_task.REQUEST_INTAKE_STATE_FIELDS,
                "taskEvent": long_task.TASK_EVENT_FIELDS,
                "eventProjection": long_task.EVENT_PROJECTION_FIELDS,
                "autoRemediationProjection": long_task.AUTO_REMEDIATION_PROJECTION_FIELDS,
                "amendmentEvent": long_task.AMENDMENT_FIELDS,
                "traceabilityRevalidation": long_task.TRACEABILITY_REVALIDATION_FIELDS,
            }
            all_allowed_fields: set[str] = set()
            for record_name, expected_fields in field_map.items():
                definition = records.get(record_name, {})
                actual_fields = set(definition.get("allowedFields", [])) if isinstance(definition, dict) else set()
                all_allowed_fields.update(actual_fields)
                if actual_fields != expected_fields:
                    result.errors.append(f"record contract differs from long_task for {record_name}")
            required_current_fields = {
                "verification_evidence_ids", "command_evidence_ids", "evidence_ids",
            }
            missing_current_fields = sorted(required_current_fields - all_allowed_fields)
            if missing_current_fields:
                result.errors.append(f"long-task record contract missing current evidence fields: {missing_current_fields}")
    result.ok("Programmatic task-state initialization and long-task record contracts are current")


def check_asset_boundary(result: CheckResult) -> None:
    """Keep runtime assets limited to current contracts and test-free resources."""
    leaked_examples = sorted(
        path.relative_to(ROOT).as_posix()
        for path in ASSETS.rglob("*")
        if checked_path(path).is_file() and ("golden" in path.name.lower() or ".example." in path.name.lower())
    )
    if leaked_examples:
        result.errors.append(f"test fixtures must live under tests/fixtures, not assets: {leaked_examples}")
    structured_yaml = sorted(path.name for path in STRUCTURED.glob("*.yaml"))
    if structured_yaml:
        result.errors.append(f"structured-doc runtime assets must contain schemas/templates only, not YAML fixtures: {structured_yaml}")
    required_fixtures = {
        "context-routing-golden.json",
        "structured-docs/api.yaml",
        "structured-docs/business-flow.example.yaml",
        "structured-docs/business_model.yaml",
        "structured-docs/changeset.yaml",
        "structured-docs/db.yaml",
        "structured-docs/dependency_impact.yaml",
        "structured-docs/requirement.multi-feature.example.md",
    }
    missing = sorted(path for path in required_fixtures if not checked_path(TEST_FIXTURES / path).is_file())
    if missing:
        result.errors.append(f"missing test fixtures after assets split: {missing}")
    if not result.errors:
        result.ok("Runtime assets contain current contracts and no test-only fixtures")


def check_request_intake_contract(contract: dict[str, Any], result: CheckResult) -> None:
    schema = load_json(ASSETS / 'development/request-intake.schema.json', result)
    profiles = contract.get("workflowProfiles", {}) if isinstance(contract, dict) else {}
    expected_profiles = {
        "BUG_FLOW", "FEATURE_FLOW", "STANDARD_FEATURE_FLOW",
        "REQUIREMENT_CHANGE_FLOW", "SIMPLE_OPTIMIZATION_FLOW", "BLOCKED",
    }
    if not isinstance(profiles, dict) or set(profiles) != expected_profiles:
        result.errors.append("runtime workflowProfiles must define the exact current request flows")
        profiles = {}
    simple = profiles.get("SIMPLE_OPTIMIZATION_FLOW", {}) if isinstance(profiles, dict) else {}
    expected_sequence = ["development", "code_review", "testing", "completion_verification"]
    expected_impacts = {
        "businessLogic", "dataModel", "apiContract", "permission", "workflow",
        "persistence", "externalSideEffect", "migration", "dependency",
    }
    if simple.get("entryPhase") != "development" or simple.get("requiredSequence") != expected_sequence:
        result.errors.append("SIMPLE_OPTIMIZATION_FLOW must execute development, code_review, testing, completion_verification")
    if set(simple.get("requiredImpactFields") or []) != expected_impacts or simple.get("requiredImpactValue") != "NONE":
        result.errors.append("SIMPLE_OPTIMIZATION_FLOW must require all nine impact fields to be NONE")
    if set(simple.get("incompatibleWorkModes") or []) != {"minimal", "lightweight"}:
        result.errors.append("SIMPLE_OPTIMIZATION_FLOW must be incompatible with wk -min and wk -ws")
    if simple.get("requiresRelatedFeature") is not True:
        result.errors.append("SIMPLE_OPTIMIZATION_FLOW must require an existing FEATURE relation")

    request_interface = contract.get("commandInterface", {}).get("requestIntake", {}) if isinstance(contract, dict) else {}
    if request_interface.get("schemaRef") != "assets/development/request-intake.schema.json":
        result.errors.append("runtime requestIntake.schemaRef is not current")
    if request_interface.get("source") != "task/{TARGET_ID}/request_intake.json":
        result.errors.append("runtime requestIntake source is not current")
    if "resolved IDs only" not in str(request_interface.get("listCommandPolicy") or ""):
        result.errors.append("runtime requestIntake must separate wk -l ID filters from description classification")

    if isinstance(schema, dict):
        required = set(schema.get("required") or [])
        for field in (
            "schemaVersion", "targetId", "targetType", "originalInput", "inputType",
            "classification", "workflowProfile", "entryPhase", "requiredPhases",
            "skippedPhases", "impactAssessment", "classificationRevision",
        ):
            if field not in required:
                result.errors.append(f"request-intake.schema.json missing required field: {field}")
        classification_enum = set(
            schema.get("properties", {}).get("classification", {}).get("enum", [])
        )
        if not {"BUG", "EXISTING_FEATURE", "NEW_REQUIREMENT", "REQUIREMENT_CHANGE", "SIMPLE_OPTIMIZATION", "NEEDS_CONFIRMATION"}.issubset(classification_enum):
            result.errors.append("request-intake.schema.json classification enum is incomplete")

    routing = load_materialized_routing(result)
    if isinstance(routing, dict):
        catalog = routing.get("resourceCatalog", {})
        for resource_id, path in (("request-intake", "references/core/request-intake.md"), ("wk-cli", "references/recovery-archive/wk-cli.md")):
            definition = catalog.get(resource_id) if isinstance(catalog, dict) else None
            if not isinstance(definition, dict) or definition.get("path") != path:
                result.errors.append(f"context-routing.json missing current resource: {resource_id}")
        routed_types = {
            task_type
            for route in routing.get("routes", [])
            if isinstance(route, dict)
            for task_type in route.get("taskTypes", [])
        }
        for task_type in ("request_intake", "wk_b", "wk_f", "wk_start", "wk_status", "wk_next"):
            if task_type not in routed_types:
                result.errors.append(f"context-routing.json missing request task type: {task_type}")

    request_script = read_check_text(ROOT / "scripts" / 'runtime' / 'request_intake.py', encoding="utf-8")
    for token in ("def classify_bug(", "def classify_feature(", "def reclassify_task(", "NEEDS_CONFIRMATION", "SIMPLE_OPTIMIZATION_FLOW"):
        if token not in request_script:
            result.errors.append(f"request_intake.py missing current behavior token: {token}")
    if "keyword" in request_script.lower() and "classification" in request_script.lower():
        # Keyword text may appear in an explanatory error, but no keyword classifier is allowed.
        for forbidden in ("KEYWORD_CLASS", "keyword_map", "classify_by_keyword"):
            if forbidden in request_script:
                result.errors.append("request_intake.py must not classify feature descriptions by keyword")

    work_mode_script = read_check_text(ROOT / "scripts" / 'runtime' / 'work_mode.py', encoding="utf-8")
    if "SIMPLE_OPTIMIZATION_FLOW" not in work_mode_script or "minimal" not in work_mode_script:
        result.errors.append("work_mode.py must block wk -min for simple optimization tasks")
    for attribute in ("request_intake", "workflow_skipped_phases"):
        if not hasattr(long_task_runtime, attribute):
            result.errors.append(f"long_task.py missing Request Intake gate attribute: {attribute}")
    if not callable(getattr(long_task_runtime.request_intake, "validate_task_binding", None)):
        result.errors.append("request_intake.py missing validate_task_binding gate")
    wk_script = read_check_text(ROOT / "scripts" / 'runtime' / 'wk.py', encoding="utf-8")
    for token in ("command_start", "command_status", "command_next", "--apply"):
        if token not in wk_script:
            result.errors.append(f"wk.py missing unified entry behavior: {token}")

    if not result.errors:
        result.ok("Request Intake, workflow profiles, unified wk entry, and mode combinations agree")


def check_semantic_contract(contract: dict[str, Any], result: CheckResult) -> None:
    semantic_contract = load_json(ASSETS / 'runtime/semantic-contract.json', result)
    if not isinstance(semantic_contract, dict):
        return
    if semantic_contract.get("packageVersion") != contract.get("packageVersion"):
        result.errors.append("assets/runtime/semantic-contract.json packageVersion must match runtime contract")
    audit_result = semantic_audit.audit(ROOT)
    for error in audit_result.get("errors", []):
        result.errors.append(f"semantic audit: {error}")
    for warning in audit_result.get("warnings", []):
        result.warnings.append(f"semantic audit: {warning}")
    if not audit_result.get("errors"):
        result.ok("Semantic vocabulary, ID namespaces, and canonical terminology are current")
