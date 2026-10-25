"""Task validation helpers for the review and phase domain."""
from __future__ import annotations

from _long_task.validation.task_support import *


def _validate_review_history(review: dict, phase: str, errors: list[str]) -> None:
    history = review.get("review_history", [])
    if not isinstance(history, list):
        errors.append(f"collaboration_reviews.{phase}.review_history must be an array when present")
        return
    seen_history_iterations: set[str] = set()
    for history_index, history_item in enumerate(history):
        history_prefix = f"collaboration_reviews.{phase}.review_history[{history_index}]"
        if not isinstance(history_item, dict):
            errors.append(f"{history_prefix} must be an object")
            continue
        required_history_fields = {
            "iteration_id", "artifact_revision", "status",
            "independent_conclusions", "archived_at",
        }
        extra_history_fields = sorted(set(history_item) - required_history_fields)
        if extra_history_fields:
            errors.append(f"{history_prefix} contains unknown fields: {extra_history_fields}")
        history_iteration = str(history_item.get("iteration_id") or "").strip()
        if not history_iteration:
            errors.append(f"{history_prefix} missing iteration_id")
        elif history_iteration in seen_history_iterations:
            errors.append(f"{history_prefix} duplicates iteration_id {history_iteration}")
        seen_history_iterations.add(history_iteration)
        if history_iteration == review.get("current_iteration_id"):
            errors.append(f"{history_prefix} must not reference current_iteration_id")
        if history_item.get("status") not in COLLABORATION_STATUSES:
            errors.append(f"{history_prefix} invalid status: {history_item.get('status')}")
        if not isinstance(history_item.get("independent_conclusions"), dict):
            errors.append(f"{history_prefix}.independent_conclusions must be an object")
        if not is_nonempty_string(history_item.get("archived_at")):
            errors.append(f"{history_prefix} missing archived_at")


def _validate_required_reviewers(
    review: dict,
    phase: str,
    expected_reviewers: set[str],
    errors: list[str],
) -> set[str]:
    required_reviewers = review.get("required_reviewers")
    if required_reviewers is None:
        return set(expected_reviewers)
    if not validate_string_list(required_reviewers):
        errors.append(
            f"collaboration_reviews.{phase}.required_reviewers must be a string array when present"
        )
        return set()
    reviewer_set = set(required_reviewers)
    missing = expected_reviewers - reviewer_set
    if missing:
        errors.append(f"collaboration_reviews.{phase} missing required reviewers: {sorted(missing)}")
    extra = reviewer_set - expected_reviewers
    if extra:
        errors.append(f"collaboration_reviews.{phase} has unexpected reviewers: {sorted(extra)}")
    unknown = reviewer_set - KNOWN_AGENTS if KNOWN_AGENTS else set()
    if unknown:
        errors.append(f"collaboration_reviews.{phase} has unknown reviewers: {sorted(unknown)}")
    return reviewer_set


def _validate_conditional_reviewers(
    review: dict,
    phase: str,
    schema_version: str,
    errors: list[str],
) -> dict[str, str]:
    expected_conditional = conditional_reviewers_for(schema_version).get(phase, {})
    conditional_reviewers = review.get("conditional_reviewers")
    if conditional_reviewers is None:
        conditional_reviewers = expected_conditional
    elif not isinstance(conditional_reviewers, dict):
        errors.append(
            f"collaboration_reviews.{phase}.conditional_reviewers must be an object when present"
        )
        conditional_reviewers = {}
    elif conditional_reviewers != expected_conditional:
        errors.append(f"collaboration_reviews.{phase}.conditional_reviewers differ from runtime contract")
    for reviewer, flag in expected_conditional.items():
        if reviewer not in KNOWN_AGENTS:
            errors.append(f"collaboration_reviews.{phase} unknown conditional reviewer: {reviewer}")
        if flag not in {"impact_required", "cross_module_implementation_required"}:
            errors.append(f"collaboration_reviews.{phase} unknown conditional flag: {flag}")
    return dict(conditional_reviewers)


def _validate_additional_reviewers(
    review: dict,
    phase: str,
    artifact: dict | None,
    artifacts: dict,
    risk_report: dict,
    task_dir,
    evidence_entries: dict,
    reviewer_set: set[str],
    expected_conditional: dict[str, str],
    errors: list[str],
) -> set[str]:
    additional_reviewers = review.get("additional_reviewers", {})
    additional_reviewer_set: set[str] = set()
    if not isinstance(additional_reviewers, dict):
        errors.append(
            f"collaboration_reviews.{phase}.additional_reviewers must be an object when present"
        )
        additional_reviewers = {}
    elif isinstance(artifact, dict) and artifact.get("status") in {"STALE", "NOT_APPLICABLE"}:
        # Reopened/skipped phases can retain historical risk registrations,
        # but they are not current reviewer authority.
        additional_reviewers = {}
    for reviewer, trigger_info in additional_reviewers.items():
        prefix = f"collaboration_reviews.{phase}.additional_reviewers.{reviewer}"
        if reviewer in reviewer_set or reviewer in expected_conditional:
            if not is_current_risk_reviewer_registration(
                risk_report, artifacts, phase, reviewer, trigger_info
            ):
                errors.append(f"{prefix} duplicates a required or conditional reviewer")
                continue
        catalog_entry = RISK_REVIEWERS_BY_AGENT.get(reviewer)
        if catalog_entry is None:
            errors.append(f"{prefix} is not declared in runtime riskReviewerCatalog")
            continue
        expected_trigger, allowed_phases = catalog_entry
        if phase not in allowed_phases:
            errors.append(f"{prefix} is not allowed for phase {phase}")
        if not isinstance(trigger_info, dict):
            errors.append(f"{prefix} must be an object")
            continue
        validate_allowed_fields(trigger_info, ADDITIONAL_REVIEWER_FIELDS, prefix, errors)
        if trigger_info.get("trigger") != expected_trigger:
            errors.append(f"{prefix}.trigger must be {expected_trigger}")
        if not is_nonempty_string(trigger_info.get("reason")):
            errors.append(f"{prefix} missing reason")
        if not validate_nonempty_string_list(trigger_info.get("evidence_ids")):
            errors.append(f"{prefix}.evidence_ids must contain current trigger evidence IDs")
        else:
            trigger_revision = str(review.get("artifact_revision") or "")
            allowed_trigger_revisions = {trigger_revision}
            if phase == "code_review":
                development_artifact = artifacts.get("development") if isinstance(artifacts, dict) else None
                if isinstance(development_artifact, dict) and development_artifact.get("revision"):
                    allowed_trigger_revisions.add(str(development_artifact.get("revision")))
            for evidence_id in trigger_info.get("evidence_ids") or []:
                try:
                    entry = evidence.require_evidence(
                        task_dir, evidence_id, validated_entries=evidence_entries
                    )
                    if entry.get("revision") not in allowed_trigger_revisions:
                        raise ValueError(
                            f"evidence revision {entry.get('revision')} is not one of "
                            f"{sorted(allowed_trigger_revisions)}"
                        )
                except ValueError as exc:
                    errors.append(f"{prefix} {exc}")
        additional_reviewer_set.add(reviewer)
    return additional_reviewer_set


def _validate_review_conclusions(
    review: dict,
    phase: str,
    task_dir,
    issues: list[dict],
    evidence_entries: dict,
    reviewer_set: set[str],
    expected_conditional: dict[str, str],
    additional_reviewer_set: set[str],
    traceability: list[dict],
    errors: list[str],
) -> tuple[dict, str, set[str]]:
    conclusions = review.get("independent_conclusions", {})
    if not isinstance(conclusions, dict):
        errors.append(
            f"collaboration_reviews.{phase}.independent_conclusions must be an object when present"
        )
        conclusions = {}
    allowed_reviewers = reviewer_set | set(expected_conditional) | additional_reviewer_set
    unexpected_conclusions = sorted(set(conclusions) - allowed_reviewers)
    if unexpected_conclusions:
        errors.append(f"collaboration_reviews.{phase} has unexpected conclusions: {unexpected_conclusions}")
    triggered_conditional = {
        reviewer
        for reviewer, flag in expected_conditional.items()
        if any(trace.get(flag) is True for trace in traceability)
    }
    artifact_revision = str(review.get("artifact_revision") or "").strip()
    for reviewer, conclusion in conclusions.items():
        if isinstance(conclusion, dict) and reviewer in allowed_reviewers:
            validate_independent_conclusion(
                task_dir, phase, reviewer, conclusion, artifact_revision,
                issues, errors, validated_entries=evidence_entries,
            )
    return conclusions, artifact_revision, triggered_conditional


def _validate_passed_review(
    review: dict,
    phase: str,
    artifact: dict | None,
    status: str | None,
    artifact_revision: str,
    conclusions: dict,
    reviewer_set: set[str],
    triggered_conditional: set[str],
    additional_reviewer_set: set[str],
    issues: list[dict],
    traceability: list[dict],
    errors: list[str],
    check_current_traceability: bool = True,
) -> None:
    if status != "PASSED":
        return
    if not artifact_revision:
        errors.append(f"collaboration_reviews.{phase} PASSED without artifact_revision")
    for reviewer in reviewer_set | triggered_conditional | additional_reviewer_set:
        conclusion = conclusions.get(reviewer)
        prefix = f"collaboration_reviews.{phase}.{reviewer}"
        if not isinstance(conclusion, dict):
            errors.append(f"{prefix} missing independent conclusion")
            continue
        if conclusion.get("conclusion") != "PASSED":
            errors.append(f"{prefix} must be PASSED before phase passes")
    open_critical = [
        issue.get("id") for issue in issues
        if issue.get("phase") == phase
        and issue.get("status") in OPEN_ISSUE_STATUSES
        and issue.get("severity") in {"P0", "P1"}
    ]
    if open_critical:
        errors.append(f"collaboration_reviews.{phase} PASSED with open P0/P1: {open_critical}")
    if not check_current_traceability:
        return
    invalid_traces = [
        trace.get("id", f"index-{i}")
        for i, trace in enumerate(traceability)
        if trace.get("status") in {"GAP", "STALE"}
    ]
    if invalid_traces:
        errors.append(f"collaboration_reviews.{phase} PASSED with GAP/STALE traces: {invalid_traces}")
    _validate_passed_traceability(phase, traceability, errors)


def _validate_passed_traceability(
    phase: str, traceability: list[dict], errors: list[str]
) -> None:
    """Validate phase-specific traceability requirements after a review passes."""
    if phase == "requirement_analysis":
        if not traceability:
            errors.append("requirement_analysis cannot pass without traceability entries")
        missing = [
            trace.get("id", f"index-{i}") for i, trace in enumerate(traceability)
            if not trace.get("requirement_refs") or not trace.get("acceptance_criteria")
        ]
        if missing:
            errors.append(f"requirement_analysis traces missing refs/acceptance: {missing}")
        missing_impact = [
            trace.get("id", f"index-{i}") for i, trace in enumerate(traceability)
            if trace.get("impact_required") is True and not trace.get("dependency_impact_refs")
        ]
        if missing_impact:
            errors.append(f"requirement_analysis impact-required traces missing dependency refs: {missing_impact}")
        missing_business_flow = [
            trace.get("id", f"index-{i}") for i, trace in enumerate(traceability)
            if trace.get("business_flow_required") is True and not trace.get("business_flow_refs")
        ]
        if missing_business_flow:
            errors.append(f"requirement_analysis flow-required traces missing business flow refs: {missing_business_flow}")
        missing_implementation = [
            trace.get("id", f"index-{i}") for i, trace in enumerate(traceability)
            if trace.get("cross_module_implementation_required") is True
            and not trace.get("cross_module_implementation_refs")
        ]
        if missing_implementation:
            errors.append(f"requirement_analysis cross-module traces missing implementation refs: {missing_implementation}")
    elif phase == "business_model":
        missing = [
            trace.get("id", f"index-{i}") for i, trace in enumerate(traceability)
            if trace.get("status") != "NOT_APPLICABLE" and not trace.get("business_model_refs")
        ]
        if missing:
            errors.append(f"business_model traces missing refs: {missing}")
    elif phase == "design":
        missing = [
            trace.get("id", f"index-{i}") for i, trace in enumerate(traceability)
            if trace.get("status") != "NOT_APPLICABLE" and not trace.get("design_refs")
        ]
        if missing:
            errors.append(f"design traces missing refs: {missing}")
    elif phase in {"test_case", "test_design"}:
        incomplete = [
            trace.get("id", f"index-{i}") for i, trace in enumerate(traceability)
            if trace.get("status") != "NOT_APPLICABLE"
            and (trace.get("status") != "COVERED" or not trace.get("test_case_ids"))
        ]
        if incomplete:
            errors.append(f"{phase} traces incomplete: {incomplete}")
    elif phase == "implementation_plan":
        missing = [
            trace.get("id", f"index-{i}") for i, trace in enumerate(traceability)
            if trace.get("status") != "NOT_APPLICABLE" and not trace.get("plan_task_ids")
        ]
        if missing:
            errors.append(f"implementation_plan traces missing tasks: {missing}")
    elif phase == "code_review":
        missing = [
            trace.get("id", f"index-{i}") for i, trace in enumerate(traceability)
            if trace.get("status") != "NOT_APPLICABLE" and not trace.get("implementation_refs")
        ]
        if missing:
            errors.append(f"code_review traces missing implementation refs: {missing}")
    elif phase == "testing":
        incomplete = [
            trace.get("id", f"index-{i}") for i, trace in enumerate(traceability)
            if trace.get("status") != "NOT_APPLICABLE"
            and (trace.get("status") != "COVERED" or not trace.get("verification_evidence_ids"))
        ]
        if incomplete:
            errors.append(f"testing traces missing current evidence: {incomplete}")


def _validate_task_section_3(ctx):
    """Validate review records while retaining the historical section entry point."""
    artifacts = ctx.value("artifacts")
    collaboration_reviewers = ctx.value("collaboration_reviewers")
    collaboration_reviews = ctx.value("collaboration_reviews")
    errors = ctx.value("errors")
    evidence_entries = ctx.value("evidence_entries")
    minimal_mode = ctx.value("minimal_mode")
    risk_report = ctx.value("risk_report")
    schema_version = ctx.value("schema_version")
    state = ctx.value("state")
    task_dir = ctx.value("task_dir")
    traceability = ctx.value("traceability")
    for phase, _declared_reviewers in collaboration_reviewers.items():
        artifact = artifacts.get(phase) if isinstance(artifacts, dict) else None
        expected_reviewers = expected_required_reviewers(phase, state, minimal=minimal_mode)
        review = collaboration_reviews.get(phase)
        if not isinstance(review, dict):
            errors.append(f"collaboration_reviews missing phase: {phase}")
            continue
        validate_allowed_fields(review, COLLABORATION_REVIEW_FIELDS, f"collaboration_reviews.{phase}", errors)
        status = review.get("status")
        if status not in COLLABORATION_STATUSES:
            errors.append(f"collaboration_reviews.{phase} invalid status: {status}")
        if not is_nonempty_string(review.get("current_iteration_id")):
            errors.append(f"collaboration_reviews.{phase} missing current_iteration_id")
        _validate_review_history(review, phase, errors)
        reviewer_set = _validate_required_reviewers(review, phase, expected_reviewers, errors)
        expected_conditional = _validate_conditional_reviewers(review, phase, schema_version, errors)
        additional_reviewer_set = _validate_additional_reviewers(
            review, phase, artifact, artifacts, risk_report, task_dir, evidence_entries,
            reviewer_set, expected_conditional, errors,
        )
        conclusions, artifact_revision, triggered_conditional = _validate_review_conclusions(
            review, phase, task_dir, ctx.value("issues"), evidence_entries,
            reviewer_set, expected_conditional, additional_reviewer_set,
            traceability, errors,
        )
        _validate_passed_review(
            review, phase, artifact, status, artifact_revision, conclusions,
            reviewer_set, triggered_conditional, additional_reviewer_set,
            ctx.value("issues"), traceability, errors,
            check_current_traceability=not (
                phase == "requirement_confirmation"
                and state.get("current_phase") == "requirement_analysis"
                and any(event.get("from_phase") == "requirement_analysis"
                        for event in ctx.value("history").get("stale_events", []))
            ),
        )
        if isinstance(artifact, dict) and review.get("current_iteration_id") != artifact.get("iteration_id"):
            errors.append(f"collaboration_reviews.{phase}.current_iteration_id does not match artifact iteration")
        if status == "PASSED" and isinstance(artifact, dict) and artifact.get("revision") != artifact_revision:
            errors.append(f"collaboration_reviews.{phase}.artifact_revision does not match artifact revision")
        if isinstance(artifact, dict) and artifact.get("status") == "PASSED" and status != "PASSED":
            errors.append(f"artifact {phase} cannot be PASSED before collaboration review passes")
