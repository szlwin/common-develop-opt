"""Classify, validate, and reclassify wk -b / wk -f request intake facts."""
from __future__ import annotations

import argparse
import contextlib
import io
import copy
import hashlib
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

from document_core import agent_context
from document_core import storage
from resource_paths import asset_path
from runtime.error_catalog import RequestIntakeError, emit_cli_error
from document_core.common_utils import attach_rollback_details, atomic_write_text, now, restore_files, sha256_file

SCHEMA_PATH = asset_path('assets/development/request-intake.schema.json')
PHASES = [
    str(item.get("id")) for item in agent_context.RUNTIME_CONTRACT.get("canonicalPhases", [])
    if isinstance(item, dict) and item.get("phaseKind", "CANONICAL") == "CANONICAL"
]
SIMPLE_REQUIRED_PHASES = ["development", "code_review", "testing", "completion_verification"]
SIMPLE_SKIPPED_PHASES = [phase for phase in PHASES if phase not in SIMPLE_REQUIRED_PHASES]
IMPACT_FIELDS = (
    "businessLogic", "dataModel", "apiContract", "permission", "workflow",
    "persistence", "externalSideEffect", "migration", "dependency",
)
BUG_ID_RE = re.compile(r"^BUG-[A-Z0-9][A-Z0-9_.-]*$", re.IGNORECASE)
FEATURE_ID_RE = re.compile(r"^[A-Z][A-Z0-9_-]*-F[0-9]{2,}$", re.IGNORECASE)
CLASSIFICATION_ALIASES = {
    "new-requirement": "NEW_REQUIREMENT",
    "requirement-change": "REQUIREMENT_CHANGE",
    "simple-optimization": "SIMPLE_OPTIMIZATION",
    "needs-confirmation": "NEEDS_CONFIRMATION",
    "NEW_REQUIREMENT": "NEW_REQUIREMENT",
    "REQUIREMENT_CHANGE": "REQUIREMENT_CHANGE",
    "SIMPLE_OPTIMIZATION": "SIMPLE_OPTIMIZATION",
    "NEEDS_CONFIRMATION": "NEEDS_CONFIRMATION",
}



def normalize_description(value: str) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise RequestIntakeError("request description must not be empty")
    return text


def stable_description_id(prefix: str, description: str) -> str:
    normalized = normalize_description(description)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}-DESC-{digest}"


def default_impact(value: str = "UNKNOWN") -> dict[str, str]:
    return {field: value for field in IMPACT_FIELDS}


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_document(document: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        import jsonschema
        validator = jsonschema.Draft202012Validator(_schema())
        for error in sorted(validator.iter_errors(document), key=lambda item: list(item.path)):
            path = ".".join(str(part) for part in error.path) or "$"
            errors.append(f"{path}: {error.message}")
    except ImportError as exc:
        raise RequestIntakeError(
            "jsonschema dependency is required for request intake validation",
            code="JSONSCHEMA_DEPENDENCY_MISSING",
        ) from exc
    if not isinstance(document, dict):
        return ["request intake must be an object"]
    phases = set(PHASES)
    for key in ("requiredPhases", "skippedPhases"):
        values = document.get(key)
        if isinstance(values, list):
            unknown = sorted(set(values) - phases)
            if unknown:
                errors.append(f"{key} contains unknown phases: {unknown}")
    required = set(document.get("requiredPhases") or [])
    skipped = set(document.get("skippedPhases") or [])
    if required & skipped:
        errors.append(f"requiredPhases and skippedPhases overlap: {sorted(required & skipped)}")
    classification = document.get("classification")
    profile = document.get("workflowProfile")
    impacts = document.get("impactAssessment") if isinstance(document.get("impactAssessment"), dict) else {}
    if classification == "BUG" and profile != "BUG_FLOW":
        errors.append("BUG classification must use BUG_FLOW")
    if classification == "SIMPLE_OPTIMIZATION":
        if profile != "SIMPLE_OPTIMIZATION_FLOW":
            errors.append("SIMPLE_OPTIMIZATION must use SIMPLE_OPTIMIZATION_FLOW")
        if not str(document.get("relatedFeature") or "").strip():
            errors.append("SIMPLE_OPTIMIZATION requires relatedFeature")
        changed = {key: impacts.get(key) for key in IMPACT_FIELDS if impacts.get(key) != "NONE"}
        if changed:
            errors.append(f"SIMPLE_OPTIMIZATION requires every impact to be NONE: {changed}")
        if document.get("entryPhase") != "development":
            errors.append("SIMPLE_OPTIMIZATION entryPhase must be development")
        if list(document.get("requiredPhases") or []) != SIMPLE_REQUIRED_PHASES:
            errors.append("SIMPLE_OPTIMIZATION requiredPhases differ from canonical fast path")
        if list(document.get("skippedPhases") or []) != SIMPLE_SKIPPED_PHASES:
            errors.append("SIMPLE_OPTIMIZATION skippedPhases differ from canonical fast path")
    if classification == "REQUIREMENT_CHANGE" and not (
        str(document.get("relatedRequirement") or "").strip()
        or str(document.get("relatedFeature") or "").strip()
    ):
        errors.append("REQUIREMENT_CHANGE requires relatedRequirement or relatedFeature")
    if classification == "NEEDS_CONFIRMATION":
        if profile != "BLOCKED" or document.get("entryPhase") != "":
            errors.append("NEEDS_CONFIRMATION must use BLOCKED with empty entryPhase")
    return errors


def require_valid(document: dict[str, Any]) -> dict[str, Any]:
    errors = validate_document(document)
    if errors:
        raise RequestIntakeError("; ".join(errors))
    return document


def classify_bug(value: str, *, agent: str, reason: str = "", evidence_refs: list[str] | None = None) -> dict[str, Any]:
    original = normalize_description(value)
    is_id = BUG_ID_RE.fullmatch(original) is not None
    target_id = original.upper() if is_id else stable_description_id("BUG", original)
    document = {
        "schemaVersion": 1,
        "targetId": target_id,
        "targetType": "BUG",
        "originalInput": original,
        "inputType": "ID" if is_id else "DESCRIPTION",
        "classification": "BUG",
        "workflowProfile": "BUG_FLOW",
        "entryPhase": "requirement_confirmation",
        "requiredPhases": list(PHASES),
        "skippedPhases": [],
        "relatedRequirement": "",
        "relatedFeature": "",
        "impactAssessment": default_impact(),
        "reason": reason or ("Existing BUG_ID" if is_id else "Description supplied with wk -b; BUG flow is mandatory."),
        "evidenceRefs": list(dict.fromkeys(evidence_refs or [])),
        "decisionAgent": agent,
        "createdAt": now(),
        "classificationRevision": 1,
        "supersedesIntakeRef": "",
        "reclassifiedFrom": "",
        "reclassificationReason": "",
        "reclassifiedAt": "",
        "historyRefs": [],
    }
    return require_valid(document)


def classify_feature(
    value: str,
    *,
    agent: str,
    classification: str = "",
    related_requirement: str = "",
    related_feature: str = "",
    impact_assessment: dict[str, str] | None = None,
    reason: str = "",
    evidence_refs: list[str] | None = None,
) -> dict[str, Any]:
    original = normalize_description(value)
    is_id = FEATURE_ID_RE.fullmatch(original) is not None
    impacts = {field: str((impact_assessment or {}).get(field) or "UNKNOWN").upper() for field in IMPACT_FIELDS}
    if is_id:
        target_id = original.upper()
        resolved = "EXISTING_FEATURE"
        profile = "FEATURE_FLOW"
        entry = "requirement_confirmation"
        required = list(PHASES)
        skipped: list[str] = []
        related_feature = related_feature or target_id
    else:
        target_id = stable_description_id("FEATURE", original)
        resolved = CLASSIFICATION_ALIASES.get(classification, "NEEDS_CONFIRMATION")
        if resolved == "NEW_REQUIREMENT":
            profile, entry, required, skipped = "STANDARD_FEATURE_FLOW", "requirement_confirmation", list(PHASES), []
        elif resolved == "REQUIREMENT_CHANGE":
            profile, entry, required, skipped = "REQUIREMENT_CHANGE_FLOW", "requirement_confirmation", list(PHASES), []
        elif resolved == "SIMPLE_OPTIMIZATION":
            profile, entry, required, skipped = "SIMPLE_OPTIMIZATION_FLOW", "development", list(SIMPLE_REQUIRED_PHASES), list(SIMPLE_SKIPPED_PHASES)
        else:
            resolved, profile, entry, required, skipped = "NEEDS_CONFIRMATION", "BLOCKED", "", [], []
    document = {
        "schemaVersion": 1,
        "targetId": target_id,
        "targetType": "FEATURE",
        "originalInput": original,
        "inputType": "ID" if is_id else "DESCRIPTION",
        "classification": resolved,
        "workflowProfile": profile,
        "entryPhase": entry,
        "requiredPhases": required,
        "skippedPhases": skipped,
        "relatedRequirement": str(related_requirement or ""),
        "relatedFeature": str(related_feature or ""),
        "impactAssessment": impacts,
        "reason": reason or ("Existing FEATURE_ID" if is_id else "Classification requires explicit project facts."),
        "evidenceRefs": list(dict.fromkeys(evidence_refs or [])),
        "decisionAgent": agent,
        "createdAt": now(),
        "classificationRevision": 1,
        "supersedesIntakeRef": "",
        "reclassifiedFrom": "",
        "reclassificationReason": "",
        "reclassifiedAt": "",
        "historyRefs": [],
    }
    return require_valid(document)


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RequestIntakeError(f"cannot read request intake {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RequestIntakeError("request intake must be an object")
    return require_valid(value)


def write(path: Path, document: dict[str, Any]) -> Path:
    require_valid(document)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    return path


def resolve_output(template: str, target_id: str) -> Path:
    return Path(str(template).replace("{TARGET_ID}", target_id))


def _governance_recommendation_digest(recommendation: Any) -> str:
    canonical = json.dumps(recommendation, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def intake_summary(path: Path, document: dict[str, Any], *, synced_at: str | None = None) -> dict[str, Any]:
    summary = {
        "ref": path.name,
        "digest": sha256_file(path),
        "classification": document["classification"],
        "workflow_profile": document["workflowProfile"],
        "entry_phase": document["entryPhase"],
        "classification_revision": document["classificationRevision"],
        "synced_at": synced_at or now(),
    }
    recommendation = document.get("governanceRecommendation")
    if isinstance(recommendation, dict):
        summary.update({
            "governance_profile": str(recommendation.get("profile") or ""),
            "governance_recommendation_digest": str(
                recommendation.get("inputDigest") or _governance_recommendation_digest(recommendation)
            ),
            "governance_override_active": bool((recommendation.get("override") or {}).get("active")),
        })
    return summary



def workflow_skipped_phases(document: dict[str, Any]) -> set[str]:
    return set(document.get("skippedPhases") or [])


def is_simple_optimization(document: dict[str, Any]) -> bool:
    return document.get("workflowProfile") == "SIMPLE_OPTIMIZATION_FLOW"


def bind_to_task(task_dir: Path, document: dict[str, Any], *, agent: str, replace: bool = False) -> dict[str, Any]:
    """Bind a validated intake to an initialized task without deleting prior facts."""
    require_valid(document)
    from runtime import long_task

    task_dir = Path(task_dir)
    if document.get("targetId") != task_dir.name:
        raise RequestIntakeError(
            f"request intake targetId differs from task directory: {document.get('targetId')} != {task_dir.name}"
        )
    intake_path = task_dir / "request_intake.json"
    state_path = long_task.task_state_path(task_dir)
    if not long_task.task_events.event_path(task_dir).is_file():
        raise RequestIntakeError("task must be initialized before binding request intake")
    if intake_path.exists() and not replace:
        existing = load(intake_path)
        if existing != document:
            raise RequestIntakeError("request_intake.json already exists with different content")
        return existing

    original_intake = intake_path.read_bytes() if intake_path.exists() else None
    original_state = state_path.read_bytes() if state_path.exists() else None
    tracked_history = [
        long_task.task_events.event_path(task_dir), long_task.task_events.state_cache_path(task_dir),
    ]
    original_history = {path: path.read_bytes() if path.exists() else None for path in tracked_history}
    timestamp = now()
    try:
        state_document, cached_state = long_task.read_task_state_cache(task_dir, authoritative=True)
        history = long_task.load_task_history(task_dir)
        state = long_task.task_events.project_state(cached_state, history, synced_at=str((cached_state.get("event_projection") or {}).get("synced_at") or ""))
        outcomes = [item for item in history.get("outcomes", []) if isinstance(item, dict)]
        new_outcomes: list[dict[str, Any]] = []
        task_runtime_specs: list[dict[str, Any]] = []
        actual_mode = long_task.actual_work_mode_state(task_dir)
        try:
            work_mode = long_task.work_mode.WorkMode(
                model_code=bool(actual_mode.get("model_code")),
                page_design=bool(actual_mode.get("page_design")),
                minimal=bool(actual_mode.get("minimal")),
                auto=bool(actual_mode.get("auto")),
                architecture_review=bool(actual_mode.get("architecture_review")),
                git_checkpoint=bool(actual_mode.get("git_checkpoint")),
                git_push=bool(actual_mode.get("git_push")),
                review_only=bool(actual_mode.get("review_only")),
                lightweight=bool(actual_mode.get("lightweight")),
            )
            long_task.work_mode.validate_workflow_mode_compatibility(
                str(document.get("workflowProfile") or ""), work_mode
            )
        except long_task.work_mode.WorkModeError as exc:
            raise RequestIntakeError(str(exc)) from exc
        write(intake_path, document)
        state["request_intake"] = intake_summary(intake_path, document, synced_at=timestamp)
        if document.get("workflowProfile") == "BLOCKED":
            state["task_status"] = "BLOCKED"
            state["next_agent"] = "ProjectManagerAgent"
            state["next_action"] = "补充事实并重新分类 Request Intake"
        elif is_simple_optimization(document):
            skipped = set(document.get("skippedPhases") or [])
            artifacts = state.get("artifact_revisions") or {}
            for phase in skipped:
                item = artifacts.get(phase)
                if not isinstance(item, dict):
                    raise RequestIntakeError(f"task state missing artifact phase: {phase}")
                item["status"] = "NOT_APPLICABLE"
                item["revision"] = f"SKIP-{phase.upper()}-INTAKE-R{int(document.get('classificationRevision') or 1):02d}"
                current = next((
                    outcome for outcome in outcomes
                    if isinstance(outcome, dict) and outcome.get("phase") == phase and outcome.get("is_current") is True
                ), None)
                if current is None:
                    skipped_outcome = {
                        "id": f"SO-{task_dir.name}-{phase.upper()}-INTAKE-SKIP-R{int(document.get('classificationRevision') or 1):02d}",
                        "phase": phase,
                        "round": f"{phase.upper()}-INTAKE-SKIP-R{int(document.get('classificationRevision') or 1):02d}",
                        "status": "SKIPPED",
                        "produced_by_agent": agent,
                        "input_revisions": {},
                        "output_revision": item["revision"],
                        "evidence_ids": [],
                        "open_risks": [
                            "Request Intake classified this task as presentation-only SIMPLE_OPTIMIZATION_FLOW; "
                            "the skipped phase remains recorded and is not treated as passed."
                        ],
                        "started_at": timestamp,
                        "completed_at": timestamp,
                        "iteration_id": item["iteration_id"],
                        "iteration_no": item["iteration_no"],
                        "is_current": True,
                        "superseded_by": "",
                    }
                    outcomes.append(skipped_outcome)
                    new_outcomes.append(skipped_outcome)
            try:
                _, static_tasks = long_task.read_static_task_plan(task_dir)
            except (OSError, ValueError):
                static_tasks = []
            for task in static_tasks:
                if isinstance(task, dict) and task.get("phase") in skipped:
                    task_runtime_specs.append({
                        "type": "TASK_RUNTIME_SET",
                        "entity_id": str(task.get("id") or ""),
                        "timestamp": timestamp,
                        "source": "request_intake.py bind_to_task",
                        "payload": {"runtime": {"status": "NOT_APPLICABLE"}},
                    })
            state["current_phase"] = "development"
            development = artifacts["development"]
            state["current_round"] = f"DEVELOPMENT-I{int(development.get('iteration_no') or 1):03d}"
            state["task_status"] = "READY"
            state["current_agent"] = agent
            state["next_agent"] = "DevelopAgent"
            state["next_action"] = "按 SIMPLE_OPTIMIZATION_FLOW 开始 development；随后必须 code_review、testing 和 completion_verification"
            state["last_gate"] = "REQUEST_INTAKE_SIMPLE_OPTIMIZATION"
            state["checkpoint_at"] = timestamp
            long_task.sync_mode_review_requirements(state, minimal=False)
        else:
            state["task_status"] = "READY"
            state["current_phase"] = str(document.get("entryPhase") or "requirement_confirmation")
            artifact = (state.get("artifact_revisions") or {}).get(state["current_phase"], {})
            state["current_round"] = f"{state['current_phase'].upper()}-I{int(artifact.get('iteration_no') or 1):03d}"
            state["current_agent"] = agent
            state["next_agent"] = long_task.PHASE_DEFINITIONS[state["current_phase"]].get("ownerAgent", "ProjectManagerAgent")
            state["next_action"] = f"按 {document.get('workflowProfile')} 开始 {state['current_phase']}"
            state["last_gate"] = "REQUEST_INTAKE_CLASSIFIED"
            state["checkpoint_at"] = timestamp
        event_specs = list(task_runtime_specs) + [
            {
                "type": "STAGE_OUTCOME_RECORDED",
                "entity_id": str(outcome.get("id") or ""),
                "timestamp": timestamp,
                "source": "request_intake.py bind_to_task",
                "payload": {"outcome": outcome},
            }
            for outcome in new_outcomes
        ]
        payloads, state, _history = long_task.append_task_events_payloads(
            task_dir, state_path, state_document, state, event_specs, synced_at=timestamp
        )
        long_task.transactional_text_write(payloads)
        return document
    except Exception as original_error:
        originals = {state_path: original_state, intake_path: original_intake, **original_history}
        details = restore_files(originals)
        attach_rollback_details(original_error, details)
        raise


def validate_task_binding(task_dir: Path, state: dict[str, Any], actual_mode: dict[str, Any], errors: list[str]) -> dict[str, Any] | None:
    summary = state.get("request_intake")
    if not isinstance(summary, dict):
        errors.append("request_intake must be an object")
        return None
    allowed = {
        "ref", "digest", "classification", "workflow_profile", "entry_phase",
        "classification_revision", "synced_at", "governance_profile",
        "governance_recommendation_digest", "governance_override_active",
    }
    extra = sorted(set(summary) - allowed)
    if extra:
        errors.append(f"request_intake contains unsupported fields: {extra}")
    ref = str(summary.get("ref") or "")
    if not ref:
        # Tasks created before Request Intake binding may remain unbound until explicitly classified.
        for key in ("digest", "classification", "workflow_profile", "entry_phase", "synced_at",
                    "governance_profile", "governance_recommendation_digest"):
            if summary.get(key) not in ("", None):
                errors.append(f"request_intake.{key} must be empty when ref is empty")
        if summary.get("classification_revision") not in (0, None):
            errors.append("request_intake.classification_revision must be 0 when ref is empty")
        if summary.get("governance_override_active") not in (False, None):
            errors.append("request_intake.governance_override_active must be false when ref is empty")
        return None
    path = task_dir / ref
    if path.name != "request_intake.json" or path.parent != task_dir:
        errors.append("request_intake.ref must equal request_intake.json")
        return None
    try:
        document = load(path)
    except RequestIntakeError as exc:
        errors.append(str(exc))
        return None
    expected = intake_summary(path, document, synced_at=str(summary.get("synced_at") or ""))
    for key in ("digest", "classification", "workflow_profile", "entry_phase", "classification_revision"):
        if summary.get(key) != expected.get(key):
            errors.append(f"request_intake.{key} differs from request_intake.json")
    if "governanceRecommendation" in document:
        for key in ("governance_profile", "governance_recommendation_digest", "governance_override_active"):
            if summary.get(key) != expected.get(key):
                errors.append(f"request_intake.{key} differs from request_intake.json")
    if is_simple_optimization(document) and bool(actual_mode.get("minimal")):
        errors.append("SIMPLE_OPTIMIZATION_FLOW is incompatible with wk -min because testing is mandatory")
    return document

def command_classify(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "request_intake", "classify")
    evidence_refs = list(args.evidence_ref or [])
    if args.bug is not None:
        document = classify_bug(args.bug, agent=agent, reason=args.reason, evidence_refs=evidence_refs)
    else:
        impacts = {
            "businessLogic": args.business_logic_impact,
            "dataModel": args.data_model_impact,
            "apiContract": args.api_contract_impact,
            "permission": args.permission_impact,
            "workflow": args.workflow_impact,
            "persistence": args.persistence_impact,
            "externalSideEffect": args.external_side_effect_impact,
            "migration": args.migration_impact,
            "dependency": args.dependency_impact,
        }
        document = classify_feature(
            args.feature,
            agent=agent,
            classification=args.classification,
            related_requirement=args.related_requirement,
            related_feature=args.related_feature,
            impact_assessment=impacts,
            reason=args.reason,
            evidence_refs=evidence_refs,
        )
    if args.output:
        write(resolve_output(args.output, document["targetId"]), document)
    print(json.dumps(document, ensure_ascii=False, indent=2 if args.pretty else None, sort_keys=True))
    return 2 if document["classification"] == "NEEDS_CONFIRMATION" else 0


def command_validate(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "request_intake", "validate")
    document = load(Path(args.path))
    payload = {"status": "PASSED", "path": str(args.path), "targetId": document["targetId"]}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0



def _reclassify_task_locked(
    task_dir: Path, *, agent: str, classification: str, reason: str,
    related_requirement: str = "", related_feature: str = "", evidence_refs: list[str] | None = None,
    confirmation_ref: str = "",
) -> dict[str, Any]:
    """Atomically upgrade SIMPLE_OPTIMIZATION to a full requirement workflow."""
    from runtime import long_task

    task_dir = Path(task_dir)
    intake_path = task_dir / "request_intake.json"
    old = load(intake_path)
    if old.get("classification") != "SIMPLE_OPTIMIZATION":
        raise RequestIntakeError("reclassify currently requires SIMPLE_OPTIMIZATION as the source classification")
    resolved = CLASSIFICATION_ALIASES.get(classification, classification)
    if resolved not in {"NEW_REQUIREMENT", "REQUIREMENT_CHANGE"}:
        raise RequestIntakeError("reclassify target must be NEW_REQUIREMENT or REQUIREMENT_CHANGE")
    if not normalize_description(reason):
        raise RequestIntakeError("reclassification reason is required")
    if resolved == "REQUIREMENT_CHANGE" and not (related_requirement or related_feature or old.get("relatedFeature")):
        raise RequestIntakeError("REQUIREMENT_CHANGE requires relatedRequirement or relatedFeature")
    state = long_task.runtime_state(task_dir)
    changed_phase = str(state.get("current_phase") or "requirement_confirmation")
    source_revision = ""
    if changed_phase in long_task.PHASES and long_task.PHASES.index("requirement_confirmation") < long_task.PHASES.index(changed_phase):
        from runtime._long_task import authorization, task_events
        confirmation_ref = str(confirmation_ref or "").strip()
        if not confirmation_ref:
            raise RequestIntakeError(
                "cross-phase reclassify requires an existing --confirmation-ref from wk confirm --reopen-from"
            )
        confirmation = next((
            event for event in task_events.read_event_history(task_dir)
            if event.get("type") == "AUTHORIZATION_BATCH_CONFIRMED"
            and isinstance(event.get("payload"), dict)
            and event.get("payload", {}).get("requestFingerprint") == confirmation_ref
        ), None)
        payload = confirmation.get("payload") if isinstance(confirmation, dict) else {}
        source_revision = str(payload.get("sourceRevision") or "") if isinstance(payload, dict) else ""
        if not source_revision or authorization.find_valid_reopen_boundary(
            task_dir, state, changed_phase=changed_phase, from_phase="requirement_confirmation",
            source_revision=source_revision, reason=reason, confirmation_ref=confirmation_ref,
            phases=long_task.PHASES,
        ) is None:
            raise RequestIntakeError(
                "confirmation_ref does not resolve to a valid prior authorization for this reclassification boundary"
            )
    elif str(confirmation_ref or "").strip():
        raise RequestIntakeError("--confirmation-ref is only valid for cross-phase reclassification")
    timestamp = now()
    next_revision = int(old.get("classificationRevision") or 1) + 1
    history_dir = task_dir / "request_intake_history"
    history_dir_existed = history_dir.exists()
    history_ref = f"request_intake_history/RI-{next_revision - 1:04d}.json"
    history_path = task_dir / history_ref
    new = copy.deepcopy(old)
    new.update({
        "classification": resolved,
        "workflowProfile": "STANDARD_FEATURE_FLOW" if resolved == "NEW_REQUIREMENT" else "REQUIREMENT_CHANGE_FLOW",
        "entryPhase": "requirement_confirmation",
        "requiredPhases": list(PHASES),
        "skippedPhases": [],
        "relatedRequirement": str(related_requirement or old.get("relatedRequirement") or ""),
        "relatedFeature": str(related_feature or old.get("relatedFeature") or ""),
        "impactAssessment": default_impact("UNKNOWN"),
        "reason": reason,
        "evidenceRefs": list(dict.fromkeys([*(old.get("evidenceRefs") or []), *(evidence_refs or [])])),
        "decisionAgent": agent,
        "classificationRevision": next_revision,
        "supersedesIntakeRef": history_ref,
        "reclassifiedFrom": str(old.get("classification") or ""),
        "reclassificationReason": reason,
        "reclassifiedAt": timestamp,
        "historyRefs": list(dict.fromkeys([*(old.get("historyRefs") or []), history_ref])),
    })
    require_valid(new)

    tracked = [
        intake_path, history_path,
        long_task.task_state_path(task_dir), storage.path(task_dir, "plan"),
        task_dir / "task_events.jsonl",
        storage.path(task_dir, "acceptance"),
        task_dir.parent.parent / "work_record.md",
    ]
    originals = {path: path.read_bytes() for path in tracked if path.exists()}
    missing = {path for path in tracked if not path.exists()}
    try:
        write(intake_path, new)
        state_path = long_task.task_state_path(task_dir)
        state_document, cached_state = long_task.read_task_state_cache(task_dir, authoritative=True)
        state = long_task.runtime_state(task_dir)
        state["request_intake"] = intake_summary(intake_path, new, synced_at=timestamp)
        payloads, state, _ = long_task.append_task_events_payloads(
            task_dir, state_path, state_document, state, [], synced_at=timestamp
        )
        long_task.transactional_text_write(payloads)
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.command_reopen_phase(argparse.Namespace(
                task_dir=str(task_dir), from_phase="requirement_confirmation", round="",
                source_revision=source_revision or f"RI-R{next_revision:04d}@{sha256_file(intake_path)[:12]}",
                changed_phase=changed_phase,
                user_confirmed=True,
                confirmation_ref=confirmation_ref,
                reason=reason, agent=agent,
            ))
        history_dir.mkdir(parents=True, exist_ok=True)
        if history_path.exists():
            existing_history = load(history_path)
            if existing_history != old:
                raise RequestIntakeError(f"request intake history already exists with different content: {history_path}")
        else:
            write(history_path, old)
        # Refresh digest after the history reference is finalized.
        write(intake_path, new)
        state_document, cached_state = long_task.read_task_state_cache(task_dir, authoritative=True)
        state = long_task.runtime_state(task_dir)
        state["request_intake"] = intake_summary(intake_path, new, synced_at=timestamp)
        state["last_gate"] = "REQUEST_INTAKE_RECLASSIFIED"
        state["next_action"] = "从 requirement_confirmation 开始新的需求 iteration；保留原简单优化历史"
        payloads, state, _ = long_task.append_task_events_payloads(
            task_dir, state_path, state_document, state, [], synced_at=timestamp
        )
        long_task.transactional_text_write(payloads)
        return new
    except Exception as original_error:
        details = restore_files({**originals, **{path: None for path in missing}})
        removed_directories: list[str] = []
        if not history_dir_existed and history_dir.is_dir():
            try:
                history_dir.rmdir()
                removed_directories.append(str(history_dir))
            except OSError as exc:
                details["rollbackErrors"].append(f"{history_dir}: {exc}")
        details["removedDirectories"] = removed_directories
        recovered = details.get("recoveredPaths") or []
        errors = details.get("rollbackErrors") or []
        details["rollbackStatus"] = "COMPLETE" if not errors else (
            "PARTIAL" if recovered or removed_directories else "FAILED"
        )
        attach_rollback_details(original_error, details)
        raise


def reclassify_task(
    task_dir: Path, *, agent: str, classification: str, reason: str,
    related_requirement: str = "", related_feature: str = "", evidence_refs: list[str] | None = None,
    confirmation_ref: str = "",
) -> dict[str, Any]:
    """Upgrade one Intake while holding the shared task lifecycle writer lock."""
    from runtime._long_task.amendment_validation import task_writer_lock
    with task_writer_lock(Path(task_dir)):
        return _reclassify_task_locked(
            Path(task_dir), agent=agent, classification=classification, reason=reason,
            related_requirement=related_requirement, related_feature=related_feature,
            evidence_refs=evidence_refs, confirmation_ref=confirmation_ref,
        )


def command_reclassify(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "request_intake", "reclassify")
    document = reclassify_task(
        Path(args.task_dir), agent=agent, classification=args.classification,
        reason=args.reason, related_requirement=args.related_requirement,
        related_feature=args.related_feature, evidence_refs=list(args.evidence_ref or []),
        confirmation_ref=str(getattr(args, "confirmation_ref", "") or ""),
    )
    print(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True))
    return 0

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    classify_parser = commands.add_parser("classify", help="classify wk -b / wk -f input")
    agent_context.add_agent_argument(classify_parser)
    input_group = classify_parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument("-b", "--bug", help="BUG_ID or BUG description")
    input_group.add_argument("-f", "--feature", help="FEATURE_ID or feature/requirement description")
    classify_parser.add_argument("--classification", choices=["new-requirement", "requirement-change", "simple-optimization", "needs-confirmation"], default="")
    classify_parser.add_argument("--related-requirement", default="")
    classify_parser.add_argument("--related-feature", default="")
    classify_parser.add_argument("--reason", default="")
    classify_parser.add_argument("--evidence-ref", action="append", default=[])
    for flag, dest in (
        ("business-logic", "business_logic"), ("data-model", "data_model"),
        ("api-contract", "api_contract"), ("permission", "permission"),
        ("workflow", "workflow"), ("persistence", "persistence"),
        ("external-side-effect", "external_side_effect"), ("migration", "migration"),
        ("dependency", "dependency"),
    ):
        classify_parser.add_argument(f"--{flag}-impact", dest=f"{dest}_impact", choices=["NONE", "CHANGED", "UNKNOWN"], default="UNKNOWN")
    classify_parser.add_argument("--output", default="")
    classify_parser.add_argument("--pretty", action="store_true")
    classify_parser.add_argument("--json", action="store_true", help="emit machine-readable result or error")
    classify_parser.set_defaults(func=command_classify)

    validate_parser = commands.add_parser("validate", help="validate one request_intake.json")
    agent_context.add_agent_argument(validate_parser)
    validate_parser.add_argument("--path", required=True)
    validate_parser.add_argument("--json", action="store_true", help="emit machine-readable result or error")
    validate_parser.set_defaults(func=command_validate)

    reclassify_parser = commands.add_parser("reclassify", help="atomically upgrade a simple optimization into a full requirement workflow")
    agent_context.add_agent_argument(reclassify_parser)
    reclassify_parser.add_argument("--task-dir", required=True)
    reclassify_parser.add_argument("--classification", required=True, choices=["new-requirement", "requirement-change"])
    reclassify_parser.add_argument("--related-requirement", default="")
    reclassify_parser.add_argument("--related-feature", default="")
    reclassify_parser.add_argument("--reason", required=True)
    reclassify_parser.add_argument("--evidence-ref", action="append", default=[])
    reclassify_parser.add_argument("--confirmation-ref", default="", help="existing wk confirm --reopen-from requestFingerprint; required only for cross-phase reclassification")
    reclassify_parser.add_argument("--json", action="store_true", help="emit machine-readable result or error")
    reclassify_parser.set_defaults(func=command_reclassify)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except (RequestIntakeError, agent_context.AgentAuthorizationError, OSError, ValueError) as exc:
        emit_cli_error(exc, json_output=bool(getattr(args, "json", False)), stream=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
