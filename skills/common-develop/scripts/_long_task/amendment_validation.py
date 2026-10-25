"""Shared validation and canonical-document helpers for phase amendments."""
from __future__ import annotations

import hashlib
import json
import re
import fcntl
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

from _long_task import task_events
from _long_task import revision_provenance
from _long_task.contracts import (
    AMENDMENT_FIELDS, KNOWN_AGENTS, KNOWN_PHASES, PROJECT_MANAGER_AGENT,
)
import review_contract
from _long_task import reason_codes as lifecycle_reason_codes


_DIGEST = r"[0-9a-f]{64}"
_REVISION = re.compile(rf"^(?P<base>.+)-AM(?P<number>[0-9]+)@(?P<digest>{_DIGEST})$")
_BLOCK = re.compile(
    r"(?ms)^<!-- common-develop:amendment\n(?P<payload>\{.*?\})\n-->$"
)
_BLOCK_MARKER = "<!-- common-develop:amendment"
_REVISION_PLACEHOLDER = "__AMENDMENT_REVISION_DIGEST__"
_DOCUMENT_PLACEHOLDER = "__AMENDMENT_DOCUMENT_DIGEST__"
_IMPACT_PLACEHOLDER = "__AMENDMENT_IMPACT_PLAN_DIGEST__"
_HIGH_RISK_SCOPE_TOKENS = {
    "authorization", "permission", "security", "transaction", "trust_boundary",
    "migration", "cross_context", "cross_module",
}
_IMPACT_PLAN_FIELDS = {
    "schemaVersion", "authority", "kind", "taskDir", "phase", "provenance",
    "graphImpact", "requiredRiskTemplates", "affectedScope", "adapter",
    "fallbackPolicy", "minimumRevalidation", "unaffected", "reusePolicy",
    "revalidationRequired", "reasonCodes", "capabilityResolution", "warnings",
    "blockers", "status",
}
_IMPACT_PLAN_OPTIONAL_FIELDS = {"derivedOutputPath"}
_PROVENANCE_FIELDS = {
    "schemaVersion", "authority", "baseRevision", "revision", "changeClass",
    "changedScopes", "changedPaths", "semanticArtifactChanged",
    "productionChanged", "processOnly", "warnings",
}
_GRAPH_FIELDS = {
    "graphPath", "changedRefs", "impactedRefs", "impactedNodeTypes", "contexts",
    "crossContext", "requiredRiskTemplates", "confidence",
}


def safe_resolve(path: Path, label: str) -> Path:
    try:
        return Path(path).resolve()
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"{label} cannot be resolved: {exc}") from exc


def assert_no_symlink_tree(root: Path, label: str) -> list[Path]:
    """Reject symlinks without following them and return the scanned entries."""
    root = Path(root)
    if root.is_symlink():
        raise ValueError(f"{label} must not be a symbolic link: {root}")
    if not root.exists():
        return []
    pending = [root]
    scanned: list[Path] = []
    while pending:
        current = pending.pop()
        try:
            entries = list(current.iterdir())
        except OSError as exc:
            raise ValueError(f"cannot scan {label}: {current}: {exc}") from exc
        for entry in entries:
            if entry.is_symlink():
                raise ValueError(f"{label} contains unsupported symbolic link: {entry}")
            scanned.append(entry)
            try:
                if entry.is_dir():
                    pending.append(entry)
            except OSError as exc:
                raise ValueError(f"cannot inspect {label}: {entry}: {exc}") from exc
    return scanned


def _validated_string_array(value: Any, field: str, prefix: str, errors: list[str]) -> list[str]:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        errors.append(f"{prefix} {field} must be an array of non-empty strings")
        return []
    if len(value) != len(set(value)):
        errors.append(f"{prefix} {field} must not contain duplicates")
    return value


def _validate_object_fields(
    value: dict[str, Any],
    *,
    required: set[str],
    allowed: set[str] | None = None,
    field: str,
    prefix: str,
    errors: list[str],
) -> None:
    missing = sorted(required - set(value))
    if missing:
        errors.append(f"{prefix} {field} is missing fields: {missing}")
    unknown = sorted(set(value) - (allowed or required))
    if unknown:
        errors.append(f"{prefix} {field} contains unknown fields: {unknown}")


def _validate_profile_selections(
    value: Any,
    *,
    field: str,
    prefix: str,
    errors: list[str],
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        errors.append(f"{prefix} {field} must be an array of objects")
        return []
    selections = list(value)
    profile_ids: list[str] = []
    for index, item in enumerate(selections):
        _validate_object_fields(
            item,
            required={"profileId", "criterionIds"},
            field=f"{field}[{index}]",
            prefix=prefix,
            errors=errors,
        )
        profile_id = item.get("profileId")
        if not isinstance(profile_id, str) or not profile_id.strip():
            errors.append(f"{prefix} {field}[{index}].profileId must be non-empty")
        else:
            profile_ids.append(profile_id)
        _validated_string_array(
            item.get("criterionIds"), f"{field}[{index}].criterionIds", prefix, errors
        )
    if len(profile_ids) != len(set(profile_ids)):
        errors.append(f"{prefix} {field} must not contain duplicate profileId values")
    return selections


def _validate_impact_plan_derivations(
    plan: dict[str, Any],
    *,
    change_class: str,
    prefix: str,
    errors: list[str],
) -> None:
    """Validate derived Review partitions and lifecycle reason codes."""
    try:
        contract = review_contract.load_contract()
    except (OSError, RuntimeError, ValueError) as exc:
        errors.append(f"{prefix} cannot load Review Contract: {exc}")
        return
    catalog = contract.get("criteriaCatalog") if isinstance(contract.get("criteriaCatalog"), dict) else {}
    minimum = plan.get("minimumRevalidation") if isinstance(plan.get("minimumRevalidation"), dict) else {}
    unaffected = plan.get("unaffected") if isinstance(plan.get("unaffected"), dict) else {}
    capabilities = minimum.get("validationCapabilities") if isinstance(minimum.get("validationCapabilities"), list) else []
    plan_phase = str(plan.get("phase") or "")
    resolved_profiles: dict[str, dict[str, Any]] = {}
    selected: dict[str, set[str]] = {}
    for field, container in (
        ("minimumRevalidation.reviews", minimum.get("reviews")),
        ("unaffected.reviewCriteria", unaffected.get("reviewCriteria")),
    ):
        if not isinstance(container, list):
            continue
        for index, item in enumerate(container):
            if not isinstance(item, dict):
                continue
            profile_id = item.get("profileId")
            criteria = item.get("criterionIds")
            if not isinstance(profile_id, str) or not profile_id.strip():
                continue
            if ":" not in profile_id:
                errors.append(f"{prefix} {field}[{index}] references unknown Review profile: {profile_id}")
                continue
            profile_phase, reviewer = profile_id.split(":", 1)
            try:
                profile = review_contract.resolve_profile(contract, profile_phase, reviewer)
            except (ValueError, RuntimeError):
                errors.append(f"{prefix} {field}[{index}] references unknown Review profile: {profile_id}")
                continue
            resolved_phase = str(profile.get("phase") or "")
            if resolved_phase != plan_phase:
                errors.append(
                    f"{prefix} {field}[{index}] Review profile {profile_id} belongs to phase {resolved_phase or '<unknown>'}; expected {plan_phase}"
                )
            resolved_profiles[profile_id] = profile
            expected = profile.get("criterionIds")
            if not isinstance(expected, list):
                errors.append(f"{prefix} Review profile has invalid criterionIds: {profile_id}")
                continue
            if not isinstance(criteria, list):
                continue
            if any(not isinstance(value, str) or not value.strip() for value in criteria):
                continue
            if len(criteria) != len(set(criteria)):
                continue
            unknown = sorted(set(criteria) - set(catalog))
            if unknown:
                errors.append(f"{prefix} {field}[{index}] references unknown Review criteria: {unknown}")
            selected.setdefault(profile_id, set())
            selected[profile_id].update(str(value) for value in criteria)
            if not set(criteria).issubset(set(expected)):
                errors.append(f"{prefix} {field}[{index}] criteria are outside profile {profile_id}")

    affected_entries = minimum.get("reviews") if isinstance(minimum.get("reviews"), list) else []
    unaffected_entries = unaffected.get("reviewCriteria") if isinstance(unaffected.get("reviewCriteria"), list) else []
    def criteria_set(item: dict[str, Any]) -> set[str]:
        criteria = item.get("criterionIds")
        if not isinstance(criteria, list) or any(
            not isinstance(value, str) or not value.strip() for value in criteria
        ):
            return set()
        return set(criteria)

    affected_by_profile = {
        str(item.get("profileId")): criteria_set(item)
        for item in affected_entries if isinstance(item, dict) and isinstance(item.get("profileId"), str)
    }
    unaffected_by_profile = {
        str(item.get("profileId")): criteria_set(item)
        for item in unaffected_entries if isinstance(item, dict) and isinstance(item.get("profileId"), str)
    }
    for profile_id in sorted(set(affected_by_profile) & set(unaffected_by_profile)):
        overlap = sorted(affected_by_profile[profile_id] & unaffected_by_profile[profile_id])
        if overlap:
            errors.append(f"{prefix} affected and unaffected criteria overlap for {profile_id}: {overlap}")
    all_profiles = set(affected_by_profile) | set(unaffected_by_profile)
    if change_class != "PROCESS_ONLY" and not all_profiles and not capabilities:
        errors.append(
            f"{prefix} semantic Amendment must declare Review criteria or validation capabilities"
        )
    if change_class != "PROCESS_ONLY" and not any(affected_by_profile.values()) and not capabilities:
        errors.append(
            f"{prefix} semantic Amendment must affect at least one Review criterion or declare validation capabilities"
        )
    expected_selected: set[str] = set()
    actual_selected: set[str] = set()
    for profile_id in sorted(all_profiles):
        profile = resolved_profiles.get(profile_id)
        if not isinstance(profile, dict):
            continue
        expected_criteria = set(profile.get("criterionIds") or [])
        actual_affected = affected_by_profile.get(profile_id, set())
        actual_unaffected = unaffected_by_profile.get(profile_id, set())
        expected_selected.update(f"{profile_id}:{criterion}" for criterion in expected_criteria)
        actual_selected.update(f"{profile_id}:{criterion}" for criterion in actual_affected | actual_unaffected)
        if actual_affected | actual_unaffected != expected_criteria:
            errors.append(f"{prefix} Review criteria partition is incomplete for profile {profile_id}")
    if expected_selected != actual_selected:
        errors.append(f"{prefix} Review criteria partition does not cover selected profiles")

    revalidation_required = plan.get("revalidationRequired")
    expected_revalidation = bool(affected_by_profile and any(affected_by_profile.values())) or bool(capabilities)
    if revalidation_required is True and not expected_revalidation:
        errors.append(f"{prefix} revalidationRequired is true without Review criteria or capabilities")
    if revalidation_required is False and expected_revalidation:
        errors.append(f"{prefix} revalidationRequired is false despite required revalidation")
    disposition = minimum.get("acceptanceDisposition")
    expected_disposition = "UNCHANGED" if change_class == "PROCESS_ONLY" and not expected_revalidation else "RECHECK_AFFECTED_ASSERTIONS"
    if disposition != expected_disposition:
        errors.append(f"{prefix} acceptanceDisposition does not match derived revalidation")

    reason_codes: list[str] = []
    provenance = plan.get("provenance") if isinstance(plan.get("provenance"), dict) else {}
    if provenance.get("processOnly") is True:
        reason_codes.append(lifecycle_reason_codes.PROCESS_ONLY_CHANGE)
    if any(affected_by_profile.values()):
        reason_codes.append(lifecycle_reason_codes.CRITERION_REVALIDATION_REQUIRED)
    if capabilities:
        reason_codes.append(lifecycle_reason_codes.VALIDATION_CAPABILITY_REQUIRED)
    if plan.get("fallbackPolicy") == "CONSERVATIVE_FULL_PROFILE":
        reason_codes.append(lifecycle_reason_codes.IMPACT_UNKNOWN_CONSERVATIVE)
    capability = plan.get("capabilityResolution") if isinstance(plan.get("capabilityResolution"), dict) else {}
    if capability.get("status") == "BLOCKED" or capability.get("missing"):
        reason_codes.append(lifecycle_reason_codes.IMPACT_CAPABILITY_BLOCKED)
    actual_reasons = plan.get("reasonCodes")
    if isinstance(actual_reasons, list) and all(
        isinstance(value, str) and value.strip() for value in actual_reasons
    ) and set(actual_reasons) != set(reason_codes):
        errors.append(
            f"{prefix} reasonCodes do not match derived plan: expected={sorted(set(reason_codes))} actual={sorted(set(actual_reasons))}"
        )


def validate_bounded_impact_plan(
    plan: dict[str, Any],
    *,
    change_class: str,
    prefix: str = "impact plan",
) -> list[str]:
    """Re-play the safety gate that permits a semantic in-place amendment."""
    errors: list[str] = []
    if not isinstance(plan, dict):
        return [f"{prefix} snapshot must be an object"]
    try:
        contract = review_contract.load_contract()
    except (OSError, RuntimeError, ValueError) as exc:
        contract = {}
        errors.append(f"{prefix} cannot load Review Contract: {exc}")
    _validate_object_fields(
        plan,
        required=_IMPACT_PLAN_FIELDS,
        allowed=_IMPACT_PLAN_FIELDS | _IMPACT_PLAN_OPTIONAL_FIELDS,
        field="snapshot",
        prefix=prefix,
        errors=errors,
    )
    if "derivedOutputPath" in plan and (
        not isinstance(plan.get("derivedOutputPath"), str)
        or not plan.get("derivedOutputPath", "").strip()
    ):
        errors.append(f"{prefix} derivedOutputPath must be a non-empty string")
    schema_version = plan.get("schemaVersion")
    if isinstance(schema_version, bool) or schema_version != 1 or plan.get("kind") != "incremental_revalidation_plan":
        errors.append(f"{prefix} must be incremental_revalidation_plan schemaVersion=1")
    if plan.get("authority") != "DERIVED":
        errors.append(f"{prefix} authority must be DERIVED")
    if not isinstance(plan.get("status"), str) or plan.get("status") != "PASSED":
        errors.append(f"{prefix} must be PASSED")
    for field in ("taskDir", "phase"):
        if not isinstance(plan.get(field), str) or not plan.get(field, "").strip():
            errors.append(f"{prefix} {field} must be a non-empty string")
    blockers = _validated_string_array(plan.get("blockers"), "blockers", prefix, errors)
    if blockers:
        errors.append(f"{prefix} must not contain blockers")
    fallback_value = plan.get("fallbackPolicy")
    fallback = fallback_value if isinstance(fallback_value, str) else ""
    if fallback not in {
        "ADAPTER_SCOPED", "GRAPH_SCOPED", "CONSERVATIVE_FULL_PROFILE",
        "PROCESS_ONLY_NO_SEMANTIC_REVALIDATION",
    }:
        errors.append(f"{prefix} fallbackPolicy is invalid")
    provenance_value = plan.get("provenance")
    provenance = provenance_value if isinstance(provenance_value, dict) else {}
    if not isinstance(provenance_value, dict):
        errors.append(f"{prefix} provenance must be an object")
    else:
        _validate_object_fields(
            provenance,
            required=_PROVENANCE_FIELDS,
            field="provenance",
            prefix=prefix,
            errors=errors,
        )
    provenance_schema = provenance.get("schemaVersion")
    if isinstance(provenance_schema, bool) or provenance_schema != 1:
        errors.append(f"{prefix} provenance.schemaVersion must equal 1")
    if provenance.get("authority") != "DERIVED":
        errors.append(f"{prefix} provenance.authority must be DERIVED")
    for field in ("baseRevision", "revision"):
        if not isinstance(provenance.get(field), str) or not provenance.get(field, "").strip():
            errors.append(f"{prefix} provenance.{field} must be a non-empty string")
    plan_change_class = provenance.get("changeClass")
    if plan_change_class not in revision_provenance.CHANGE_CLASSES:
        errors.append(f"{prefix} provenance.changeClass is invalid")
    elif plan_change_class != change_class:
        errors.append(f"{prefix} provenance.changeClass does not match the Amendment")
    process_only = provenance.get("processOnly")
    if not isinstance(process_only, bool):
        errors.append(f"{prefix} provenance.processOnly must be a boolean")
    changed_scopes = _validated_string_array(
        provenance.get("changedScopes"), "provenance.changedScopes", prefix, errors
    )
    _validated_string_array(
        provenance.get("changedPaths"), "provenance.changedPaths", prefix, errors
    )
    _validated_string_array(
        provenance.get("warnings"), "provenance.warnings", prefix, errors
    )
    for field in ("semanticArtifactChanged", "productionChanged"):
        if provenance.get(field) not in {"TRUE", "FALSE", "UNKNOWN"}:
            errors.append(f"{prefix} provenance.{field} is invalid")
    if change_class == "UNKNOWN" or fallback == "CONSERVATIVE_FULL_PROFILE":
        errors.append(f"{prefix} unknown or conservative impact requires reopen-phase")
    if process_only is True or change_class == "PROCESS_ONLY":
        errors.append(f"{prefix} PROCESS_ONLY changes do not form a semantic Amendment")
    if process_only is not (change_class == "PROCESS_ONLY"):
        errors.append(f"{prefix} provenance.processOnly is inconsistent with changeClass")

    adapter_value = plan.get("adapter")
    adapter = adapter_value if isinstance(adapter_value, dict) else {}
    if not isinstance(adapter_value, dict):
        errors.append(f"{prefix} adapter must be an object")
    else:
        _validate_object_fields(
            adapter,
            required={"used", "confidence", "affectedScopes", "affectedArtifacts", "notes"},
            field="adapter",
            prefix=prefix,
            errors=errors,
        )
    adapter_used = adapter.get("used")
    if not isinstance(adapter_used, bool):
        errors.append(f"{prefix} adapter.used must be a boolean")
    adapter_confidence = adapter.get("confidence")
    if not isinstance(adapter_confidence, str) or adapter_confidence not in {
        "HIGH", "MEDIUM", "LOW", "UNKNOWN", "NONE",
    }:
        errors.append(f"{prefix} adapter.confidence is invalid")
        adapter_confidence = ""
    adapter_scopes = _validated_string_array(
        adapter.get("affectedScopes"), "adapter.affectedScopes", prefix, errors
    )
    for field in ("affectedArtifacts", "notes"):
        _validated_string_array(adapter.get(field), f"adapter.{field}", prefix, errors)
    if adapter_used is False and adapter_confidence != "NONE":
        errors.append(f"{prefix} unused adapter must have confidence NONE")
    if adapter_used is True and adapter_confidence == "NONE":
        errors.append(f"{prefix} used adapter must declare a real confidence")

    graph_value = plan.get("graphImpact")
    graph = graph_value if isinstance(graph_value, dict) else {}
    if graph_value is not None and not isinstance(graph_value, dict):
        errors.append(f"{prefix} graphImpact must be an object or null")
    graph_confidence = ""
    graph_risks: list[str] = []
    if isinstance(graph_value, dict):
        _validate_object_fields(
            graph,
            required=_GRAPH_FIELDS,
            field="graphImpact",
            prefix=prefix,
            errors=errors,
        )
        graph_path = graph.get("graphPath")
        if not isinstance(graph_path, str) or not graph_path.strip():
            errors.append(f"{prefix} graphImpact.graphPath must be a non-empty string")
        graph_confidence = graph.get("confidence")
        if not isinstance(graph_confidence, str) or graph_confidence not in {
            "HIGH", "MEDIUM", "LOW", "UNKNOWN",
        }:
            errors.append(f"{prefix} graphImpact.confidence is invalid")
            graph_confidence = ""
        cross_context = graph.get("crossContext")
        if not isinstance(cross_context, bool):
            errors.append(f"{prefix} graphImpact.crossContext must be a boolean")
        changed_refs = _validated_string_array(
            graph.get("changedRefs"), "graphImpact.changedRefs", prefix, errors
        )
        impacted_node_types = _validated_string_array(
            graph.get("impactedNodeTypes"), "graphImpact.impactedNodeTypes", prefix, errors
        )
        contexts = _validated_string_array(
            graph.get("contexts"), "graphImpact.contexts", prefix, errors
        )
        graph_risks = _validated_string_array(
            graph.get("requiredRiskTemplates"),
            "graphImpact.requiredRiskTemplates",
            prefix,
            errors,
        )
        impacted_refs = graph.get("impactedRefs")
        if not isinstance(impacted_refs, list) or any(not isinstance(item, dict) for item in impacted_refs):
            errors.append(f"{prefix} graphImpact.impactedRefs must be an array of objects")
            impacted_refs = []
        for index, item in enumerate(impacted_refs):
            _validate_object_fields(
                item,
                required={"ref", "depth", "reasonPath"},
                field=f"graphImpact.impactedRefs[{index}]",
                prefix=prefix,
                errors=errors,
            )
            if not isinstance(item.get("ref"), str) or not item.get("ref", "").strip():
                errors.append(f"{prefix} graphImpact.impactedRefs[{index}].ref must be non-empty")
            depth = item.get("depth")
            if isinstance(depth, bool) or not isinstance(depth, int) or depth < 0:
                errors.append(f"{prefix} graphImpact.impactedRefs[{index}].depth must be non-negative")
            _validated_string_array(
                item.get("reasonPath"),
                f"graphImpact.impactedRefs[{index}].reasonPath",
                prefix,
                errors,
            )
        impacted_ids = {
            str(item.get("ref")) for item in impacted_refs
            if isinstance(item.get("ref"), str) and item.get("ref")
        }
        if len(impacted_ids) != len(impacted_refs):
            errors.append(f"{prefix} graphImpact.impactedRefs must not contain duplicate refs")
        if not set(changed_refs).issubset(impacted_ids):
            errors.append(f"{prefix} graphImpact.changedRefs must be included in impactedRefs")
        if graph.get("crossContext") != (len(set(contexts)) > 1):
            errors.append(f"{prefix} graphImpact.crossContext is inconsistent with contexts")
        if not impacted_node_types and impacted_refs:
            errors.append(f"{prefix} graphImpact.impactedNodeTypes must not be empty")
        if fallback == "GRAPH_SCOPED" and (not changed_refs or not impacted_refs):
            errors.append(f"{prefix} GRAPH_SCOPED requires non-empty Graph impact paths")

    risk_templates = _validated_string_array(
        plan.get("requiredRiskTemplates"), "requiredRiskTemplates", prefix, errors
    )
    if set(risk_templates) != set(graph_risks):
        errors.append(
            f"{prefix} requiredRiskTemplates must equal graphImpact.requiredRiskTemplates"
        )
    risk_catalog = contract.get("riskReviewTemplates")
    if not isinstance(risk_catalog, dict):
        risk_catalog = {}
    plan_phase = plan.get("phase") if isinstance(plan.get("phase"), str) else ""
    for risk_id in sorted(set(risk_templates) | set(graph_risks)):
        definition = risk_catalog.get(risk_id)
        if not isinstance(definition, dict):
            errors.append(f"{prefix} unknown risk Review template: {risk_id}")
            continue
        allowed_phases = definition.get("allowedPhases")
        if not isinstance(allowed_phases, list) or plan_phase not in allowed_phases:
            errors.append(
                f"{prefix} risk Review template {risk_id} is not allowed for phase {plan_phase or '<unknown>'}"
            )
            continue
    affected_scope_value = plan.get("affectedScope")
    affected_scope = affected_scope_value if isinstance(affected_scope_value, dict) else {}
    if not isinstance(affected_scope_value, dict):
        errors.append(f"{prefix} affectedScope must be an object")
    else:
        _validate_object_fields(
            affected_scope,
            required={"taskIds", "traceIds", "acceptanceIds"},
            field="affectedScope",
            prefix=prefix,
            errors=errors,
        )
    scope_task_ids = _validated_string_array(
        affected_scope.get("taskIds"), "affectedScope.taskIds", prefix, errors
    )
    _validated_string_array(
        affected_scope.get("traceIds"), "affectedScope.traceIds", prefix, errors
    )
    _validated_string_array(
        affected_scope.get("acceptanceIds"), "affectedScope.acceptanceIds", prefix, errors
    )
    if not scope_task_ids:
        errors.append(f"{prefix} affectedScope.taskIds must not be empty")

    minimum_value = plan.get("minimumRevalidation")
    minimum = minimum_value if isinstance(minimum_value, dict) else {}
    if not isinstance(minimum_value, dict):
        errors.append(f"{prefix} minimumRevalidation must be an object")
    else:
        _validate_object_fields(
            minimum,
            required={"reviews", "validationCapabilities", "acceptanceDisposition"},
            field="minimumRevalidation",
            prefix=prefix,
            errors=errors,
        )
    minimum_reviews = _validate_profile_selections(
        minimum.get("reviews"), field="minimumRevalidation.reviews", prefix=prefix, errors=errors
    )
    affected_by_profile = {
        str(item.get("profileId")): set(item.get("criterionIds") or [])
        for item in minimum_reviews
        if isinstance(item.get("profileId"), str)
        and isinstance(item.get("criterionIds"), list)
        and all(isinstance(value, str) and value.strip() for value in item.get("criterionIds"))
    }
    for risk_id in sorted(set(risk_templates) | set(graph_risks)):
        definition = risk_catalog.get(risk_id)
        if not isinstance(definition, dict):
            continue
        reviewer = definition.get("reviewerAgent")
        if not isinstance(reviewer, str) or not reviewer.strip():
            continue
        profile_id = f"{plan_phase}:{reviewer}"
        try:
            profile = review_contract.resolve_profile(contract, plan_phase, reviewer)
        except (ValueError, RuntimeError) as exc:
            errors.append(
                f"{prefix} risk Review template {risk_id} resolves no valid profile {profile_id}: {exc}"
            )
            continue
        required_criteria = set(profile.get("criterionIds") or [])
        affected_criteria = affected_by_profile.get(profile_id, set())
        if affected_criteria != required_criteria:
            errors.append(
                f"{prefix} risk Review template {risk_id} requires all criteria of affected profile {profile_id}"
            )
    capabilities = _validated_string_array(
        minimum.get("validationCapabilities"),
        "minimumRevalidation.validationCapabilities",
        prefix,
        errors,
    )
    if minimum.get("acceptanceDisposition") not in {
        "UNCHANGED", "RECHECK_AFFECTED_ASSERTIONS",
    }:
        errors.append(f"{prefix} minimumRevalidation.acceptanceDisposition is invalid")
    elif change_class != "PROCESS_ONLY" and minimum.get(
        "acceptanceDisposition"
    ) != "RECHECK_AFFECTED_ASSERTIONS":
        errors.append(
            f"{prefix} semantic Amendment must recheck affected Acceptance assertions"
        )

    unaffected_value = plan.get("unaffected")
    unaffected = unaffected_value if isinstance(unaffected_value, dict) else {}
    if not isinstance(unaffected_value, dict):
        errors.append(f"{prefix} unaffected must be an object")
    else:
        _validate_object_fields(
            unaffected,
            required={"reviewCriteria"},
            field="unaffected",
            prefix=prefix,
            errors=errors,
        )
    _validate_profile_selections(
        unaffected.get("reviewCriteria"),
        field="unaffected.reviewCriteria",
        prefix=prefix,
        errors=errors,
    )

    reuse_value = plan.get("reusePolicy")
    reuse = reuse_value if isinstance(reuse_value, dict) else {}
    reuse_fields = {
        "reviewResultReuseAcrossExactRevision", "criterionEvidenceIdReuseAcrossExactRevision",
        "unaffectedEvidenceSourceReuse", "conditions",
    }
    if not isinstance(reuse_value, dict):
        errors.append(f"{prefix} reusePolicy must be an object")
    else:
        _validate_object_fields(
            reuse, required=reuse_fields, field="reusePolicy", prefix=prefix, errors=errors
        )
    for field in (
        "reviewResultReuseAcrossExactRevision", "criterionEvidenceIdReuseAcrossExactRevision",
    ):
        if not isinstance(reuse.get(field), bool):
            errors.append(f"{prefix} reusePolicy.{field} must be a boolean")
        elif reuse.get(field) is not False:
            errors.append(f"{prefix} reusePolicy.{field} must be false")
    if not isinstance(reuse.get("unaffectedEvidenceSourceReuse"), str) or not reuse.get(
        "unaffectedEvidenceSourceReuse", ""
    ).strip():
        errors.append(f"{prefix} reusePolicy.unaffectedEvidenceSourceReuse must be non-empty")
    _validated_string_array(reuse.get("conditions"), "reusePolicy.conditions", prefix, errors)

    revalidation_required = plan.get("revalidationRequired")
    if not isinstance(revalidation_required, bool):
        errors.append(f"{prefix} revalidationRequired must be a boolean")
    elif revalidation_required != bool(minimum_reviews or capabilities):
        errors.append(f"{prefix} revalidationRequired is inconsistent with minimumRevalidation")
    _validated_string_array(plan.get("reasonCodes"), "reasonCodes", prefix, errors)
    _validated_string_array(plan.get("warnings"), "warnings", prefix, errors)

    capability_value = plan.get("capabilityResolution")
    capability = capability_value if isinstance(capability_value, dict) else {}
    if not isinstance(capability_value, dict):
        errors.append(f"{prefix} capabilityResolution must be an object")
    else:
        _validate_object_fields(
            capability,
            required={"status", "required"},
            allowed={"status", "required", "missing", "environmentStatus"},
            field="capabilityResolution",
            prefix=prefix,
            errors=errors,
        )
    if capability.get("status") not in {"NOT_EVALUATED", "PASSED", "BLOCKED"}:
        errors.append(f"{prefix} capabilityResolution.status is invalid")
    required_capabilities = _validated_string_array(
        capability.get("required"), "capabilityResolution.required", prefix, errors
    )
    missing_capabilities: list[str] = []
    if "missing" in capability:
        missing_capabilities = _validated_string_array(
            capability.get("missing"), "capabilityResolution.missing", prefix, errors
        )
    if set(required_capabilities) != set(capabilities):
        errors.append(
            f"{prefix} capabilityResolution.required must equal validationCapabilities"
        )
    if not set(missing_capabilities).issubset(required_capabilities):
        errors.append(f"{prefix} capabilityResolution.missing must be a subset of required")
    if capability.get("status") == "BLOCKED" or missing_capabilities:
        errors.append(f"{prefix} unresolved validation capabilities require reopen-phase")
    _validate_impact_plan_derivations(
        plan,
        change_class=change_class,
        prefix=prefix,
        errors=errors,
    )
    trusted_scope = (
        fallback == "ADAPTER_SCOPED"
        and adapter_used is True
        and adapter_confidence in {"HIGH", "MEDIUM"}
    ) or (
        fallback == "GRAPH_SCOPED"
        and isinstance(graph_value, dict)
        and graph_confidence == "HIGH"
    )
    if not trusted_scope:
        errors.append(f"{prefix} cannot prove a bounded amendment; use reopen-phase")
    if graph.get("crossContext") is True:
        errors.append(f"{prefix} cross-context impact requires reopen-phase")
    scopes = {
        str(value).strip().lower().replace("-", "_").replace(" ", "_")
        for value in [*adapter_scopes, *changed_scopes]
    }
    normalized_risks = {value.lower() for value in risk_templates}
    if normalized_risks & {"cross_module_integration", "security", "concurrency", "data_migration"} or any(
        token in scope for scope in scopes for token in _HIGH_RISK_SCOPE_TOKENS
    ):
        errors.append(f"{prefix} high-risk or cross-boundary impact requires reopen-phase")
    return list(dict.fromkeys(errors))


def version_root(task_dir: Path) -> Path:
    task_dir = safe_resolve(Path(task_dir), "task directory")
    if task_dir.parent.name != "task":
        raise ValueError(f"task directory must be under version/<VERSION>/task: {task_dir}")
    return task_dir.parent.parent


def _declared_canonical_paths(
    task_dir: Path,
    tasks: Iterable[dict[str, Any]],
    affected_task_ids: Iterable[str],
    *,
    doc_root: Path | None = None,
) -> set[Path]:
    """Resolve exact version-document paths declared by affected plan tasks."""
    root = version_root(task_dir)
    resolved_doc_root = doc_root or safe_resolve(root / "doc", "canonical doc tree")
    project_doc_root = root.parent.parent
    version_prefix = Path("version") / root.name
    affected = {str(item) for item in affected_task_ids}
    declared: set[Path] = set()
    for task in tasks:
        if not isinstance(task, dict) or str(task.get("id") or "") not in affected:
            continue
        for raw in task.get("allowed_files") or []:
            value = str(raw or "").split("#", 1)[0].strip()
            if not value or any(token in value for token in ("*", "?", "[")):
                continue
            candidate = Path(value)
            if candidate.is_absolute():
                resolved = safe_resolve(candidate, "declared canonical file")
            elif candidate.parts[:2] == version_prefix.parts:
                resolved = safe_resolve(project_doc_root / candidate, "declared canonical file")
            elif candidate.parts and candidate.parts[0] == "doc":
                resolved = safe_resolve(root / candidate, "declared canonical file")
            else:
                resolved = safe_resolve(project_doc_root.parent / candidate, "declared canonical file")
            if resolved.is_relative_to(resolved_doc_root):
                declared.add(resolved)
    return declared


def canonical_file_path(
    task_dir: Path,
    value: str | Path,
    *,
    phase: str = "",
    tasks: Iterable[dict[str, Any]] = (),
    affected_task_ids: Iterable[str] = (),
    validated_doc_root: Path | None = None,
) -> Path:
    """Resolve a canonical document and prove that it belongs to this task/phase."""
    root = version_root(task_dir)
    raw_doc_root = root / "doc"
    if validated_doc_root is None:
        assert_no_symlink_tree(raw_doc_root, "canonical doc tree")
        doc_root = safe_resolve(raw_doc_root, "canonical doc tree")
    else:
        doc_root = validated_doc_root
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = safe_resolve(candidate, "canonical amendment file")
    if not resolved.is_relative_to(doc_root):
        raise ValueError(f"canonical file must be inside the current version doc tree: {resolved}")
    if resolved.suffix.lower() not in {".md", ".markdown"}:
        raise ValueError(f"canonical amendment file must be Markdown: {resolved}")
    if not resolved.is_file():
        raise ValueError(f"canonical amendment file does not exist: {resolved}")
    target_id = safe_resolve(Path(task_dir), "task directory").name
    owned_requirement = safe_resolve(
        doc_root / target_id / "requirement.md", "canonical requirement file"
    )
    owned_test_design = safe_resolve(
        doc_root / target_id / "test_case.md", "canonical test design file"
    )
    if phase in {"requirement_confirmation", "requirement_analysis"}:
        allowed = {owned_requirement}
    elif phase == "test_design":
        allowed = {owned_test_design}
    else:
        allowed = _declared_canonical_paths(
            task_dir, tasks, affected_task_ids, doc_root=doc_root
        )
    if resolved not in allowed:
        raise ValueError(
            "canonical amendment file ownership cannot be proven for the current task/phase: "
            f"phase={phase or '<missing>'} target={target_id} file={resolved}"
        )
    return resolved


_LOCK_STATE = threading.local()


@contextmanager
def task_writer_lock(task_dir: Path) -> Iterator[Path]:
    """Acquire the non-blocking task-scoped lock used by every lifecycle writer.

    The small in-process re-entrancy layer is intentional: ``advance-development-step``
    delegates to ``reopen-phase`` and ``amend-phase`` is also protected at its
    command boundary for callers that bypass the public facade.
    """
    resolved_task = safe_resolve(Path(task_dir), "task directory")
    if not resolved_task.is_dir():
        raise ValueError(f"task directory does not exist: {resolved_task}")
    lock_path = resolved_task / "cache" / "amend-phase.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    held = getattr(_LOCK_STATE, "held", None)
    if held is None:
        held = {}
        _LOCK_STATE.held = held
    key = str(lock_path)
    existing = held.get(key)
    if existing is not None:
        handle, depth = existing
        held[key] = (handle, depth + 1)
        try:
            yield lock_path
        finally:
            handle, depth = held[key]
            if depth <= 1:
                held.pop(key, None)
            else:
                held[key] = (handle, depth - 1)
        return
    with lock_path.open("a+b") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(
                f"another amend-phase writer (task lifecycle writer) holds the task lock: {lock_path}"
            ) from exc
        held[key] = (handle, 1)
        try:
            yield lock_path
        finally:
            held.pop(key, None)
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def amendment_lock(task_dir: Path) -> Iterator[Path]:
    """Backward-compatible name for the task writer lock."""
    with task_writer_lock(task_dir) as lock_path:
        yield lock_path


def _normal_payload(payload: dict[str, Any]) -> dict[str, Any]:
    value = dict(payload)
    revision = str(value.get("revision") or "")
    match = _REVISION.fullmatch(revision)
    if match:
        value["revision"] = f"{match.group('base')}-AM{match.group('number')}@{_REVISION_PLACEHOLDER}"
    elif revision:
        value["revision"] = re.sub(r"@[0-9a-f]{64}$", f"@{_REVISION_PLACEHOLDER}", revision)
    if "canonical_document_digest" in value:
        value["canonical_document_digest"] = _DOCUMENT_PLACEHOLDER
    if "impact_plan_digest" in value:
        value["impact_plan_digest"] = _IMPACT_PLACEHOLDER
    if "impact_plan_path" in value:
        value["impact_plan_path"] = "__AMENDMENT_IMPACT_PLAN_PATH__"
    # Timestamps describe the audit event, not the canonical semantic bytes.
    # Excluding them keeps the revision computable before the append transaction.
    if "created_at" in value:
        value["created_at"] = "__AMENDMENT_CREATED_AT__"
    return value


def render_amendment_block(amendment: dict[str, Any], *, placeholders: bool = False) -> str:
    payload = _normal_payload(amendment) if placeholders else dict(amendment)
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"<!-- common-develop:amendment\n{data}\n-->"


def append_candidate(document: str, amendment: dict[str, Any], *, placeholders: bool = False) -> str:
    suffix = render_amendment_block(amendment, placeholders=placeholders)
    heading = f"## Amendment {amendment['amendment_id']}"
    base = document.rstrip("\n")
    return f"{base}\n\n{heading}\n{suffix}\n"


def canonical_document_digest(document: str) -> str:
    """Hash a document after removing only the self-referential current block."""
    normalized = _BLOCK.sub(
        lambda match: render_amendment_block(
            _normal_payload(json.loads(match.group("payload"))), placeholders=True
        ),
        document,
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def canonical_document_digest_for_amendment(document: str, amendment_id: str) -> str:
    """Hash the canonical document as it stood when this Amendment was appended.

    Later in-place Amendments must not retroactively change the digest of an
    earlier revision, so the prefix through the selected block is authoritative.
    """
    for match in _BLOCK.finditer(document):
        try:
            payload = json.loads(match.group("payload"))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and payload.get("amendment_id") == amendment_id:
            end = match.end()
            if end < len(document) and document[end] == "\n":
                end += 1
            return canonical_document_digest(document[:end])
    raise ValueError(f"canonical Amendment block not found: {amendment_id}")


def prepare_canonical_append(
    path: Path,
    amendment: dict[str, Any],
) -> tuple[str, str]:
    """Return final text and its digest without modifying the file."""
    original = path.read_text(encoding="utf-8")
    candidate = append_candidate(original, amendment, placeholders=True)
    digest = canonical_document_digest(candidate)
    final = dict(amendment)
    base_name = re.sub(r"-AM[0-9]+$", "", str(amendment["base_revision"]).split("@", 1)[0])
    final["revision"] = f"{base_name}-AM{int(amendment['amendment_no']):02d}@{digest}"
    final["canonical_document_digest"] = digest
    return append_candidate(original, final), digest


def amendment_blocks(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    blocks: list[dict[str, Any]] = []
    for match in _BLOCK.finditer(text):
        try:
            value = json.loads(match.group("payload"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid canonical Amendment block in {path}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"canonical Amendment block must be an object: {path}")
        blocks.append(value)
    if text.count(_BLOCK_MARKER) != len(blocks):
        raise ValueError(f"malformed canonical Amendment block in {path}")
    return blocks


def validate_canonical_amendment(path: Path, amendment: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        blocks = amendment_blocks(path)
    except (OSError, ValueError) as exc:
        return [str(exc)]
    matches = [item for item in blocks if item.get("amendment_id") == amendment.get("amendment_id")]
    if len(matches) != 1:
        return [f"canonical file must contain exactly one Amendment {amendment.get('amendment_id')}"]
    actual = matches[0]
    for key in (
        "amendment_id", "base_revision", "revision", "change_class", "reason",
        "impact_plan_digest", "impact_plan_path", "canonical_file", "amendment_no",
        "canonical_document_digest", "executed_by_agent",
    ):
        if actual.get(key) != amendment.get(key):
            errors.append(f"canonical Amendment {key} mismatch")
    for key in (
        "changed_sections", "affected_phases", "affected_task_ids", "affected_trace_ids",
        "affected_acceptance_ids", "preserved_evidence_ids", "invalidated_evidence_ids",
    ):
        if list(actual.get(key) or []) != list(amendment.get(key) or []):
            errors.append(f"canonical Amendment {key} mismatch")
    expected = str(amendment.get("canonical_document_digest") or "")
    if not re.fullmatch(_DIGEST, expected):
        errors.append("canonical_document_digest must be a 64-character lowercase SHA-256")
    else:
        try:
            actual_digest = canonical_document_digest_for_amendment(
                path.read_text(encoding="utf-8"), str(amendment.get("amendment_id") or "")
            )
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
        else:
            if actual_digest != expected:
                errors.append(f"canonical document digest mismatch: expected={expected} actual={actual_digest}")
    return errors


def validate_canonical_amendment_file(
    path: Path,
    amendments: Iterable[dict[str, Any]],
) -> list[str]:
    """Compare every canonical block with every event targeting one file."""
    expected = [dict(item) for item in amendments if isinstance(item, dict)]
    try:
        actual = amendment_blocks(path)
    except (OSError, ValueError) as exc:
        return [str(exc)]
    errors: list[str] = []
    for index, block in enumerate(actual):
        unknown = sorted(set(block) - AMENDMENT_FIELDS)
        if unknown:
            errors.append(f"canonical Amendment block[{index}] contains unknown fields: {unknown}")
        missing = sorted(AMENDMENT_FIELDS - set(block))
        if missing:
            errors.append(f"canonical Amendment block[{index}] is missing fields: {missing}")
        for key in ("amendment_id", "revision"):
            if not str(block.get(key) or "").strip():
                errors.append(f"canonical Amendment block[{index}] has empty {key}")
    for label, values in (("ID", [item.get("amendment_id") for item in actual]),
                          ("revision", [item.get("revision") for item in actual])):
        duplicates = sorted({str(value) for value in values if value and values.count(value) > 1})
        if duplicates:
            errors.append(f"canonical Amendment contains duplicate {label}s: {duplicates}")
    expected_by_id = {
        str(item.get("amendment_id") or ""): item for item in expected if item.get("amendment_id")
    }
    actual_by_id = {
        str(item.get("amendment_id") or ""): item for item in actual if item.get("amendment_id")
    }
    missing = sorted(set(expected_by_id) - set(actual_by_id))
    rogue = sorted(set(actual_by_id) - set(expected_by_id))
    if missing:
        errors.append(f"canonical Amendment blocks missing for events: {missing}")
    if rogue:
        errors.append(f"canonical Amendment blocks have no ARTIFACT_AMENDED event: {rogue}")
    for amendment_id in sorted(set(expected_by_id) & set(actual_by_id)):
        if actual_by_id[amendment_id] != expected_by_id[amendment_id]:
            errors.append(f"canonical Amendment payload mismatch: {amendment_id}")
        errors.extend(validate_canonical_amendment(path, expected_by_id[amendment_id]))
    return list(dict.fromkeys(errors))


def affected_scope_closure(
    tasks: Iterable[dict[str, Any]],
    traceability: Iterable[dict[str, Any]],
    affected_task_ids: Iterable[str],
) -> tuple[set[str], set[str], list[str]]:
    """Return the exact Trace/Acceptance closure required by affected tasks."""
    affected = {str(item) for item in affected_task_ids if str(item)}
    task_map = {
        str(item.get("id")): item for item in tasks
        if isinstance(item, dict) and item.get("id")
    }
    trace_map = {
        str(item.get("id")): item for item in traceability
        if isinstance(item, dict) and item.get("id")
    }
    required_traces: set[str] = set()
    for task_id in affected:
        task = task_map.get(task_id)
        if isinstance(task, dict):
            required_traces.update(
                str(item) for item in task.get("acceptance_trace_ids") or [] if str(item)
            )
    for trace_id, trace in trace_map.items():
        if affected.intersection(str(item) for item in trace.get("plan_task_ids") or []):
            required_traces.add(trace_id)
    errors = [
        f"affected task references unknown Trace ID: {trace_id}"
        for trace_id in sorted(required_traces - set(trace_map))
    ]
    required_acceptance: set[str] = set()
    for trace_id in required_traces & set(trace_map):
        required_acceptance.update(
            str(item) for item in trace_map[trace_id].get("acceptance_criteria") or [] if str(item)
        )
    return required_traces, required_acceptance, errors


def validate_affected_scope_closure(
    tasks: Iterable[dict[str, Any]],
    traceability: Iterable[dict[str, Any]],
    affected_task_ids: Iterable[str],
    affected_trace_ids: Iterable[str],
    affected_acceptance_ids: Iterable[str],
) -> list[str]:
    required_traces, required_acceptance, errors = affected_scope_closure(
        tasks, traceability, affected_task_ids
    )
    actual_traces = {str(item) for item in affected_trace_ids if str(item)}
    actual_acceptance = {str(item) for item in affected_acceptance_ids if str(item)}
    if actual_traces != required_traces:
        errors.append(
            "affected Trace IDs must equal the complete task Trace closure: "
            f"required={sorted(required_traces)} actual={sorted(actual_traces)}"
        )
    if actual_acceptance != required_acceptance:
        errors.append(
            "affected Acceptance IDs must equal the complete Trace Acceptance closure: "
            f"required={sorted(required_acceptance)} actual={sorted(actual_acceptance)}"
        )
    return errors


def validate_amendment_revision(base_revision: str, revision: str, amendment_no: int) -> None:
    if base_revision == revision:
        raise ValueError("amend-phase requires revision to differ from base revision")
    match = _REVISION.fullmatch(revision)
    if not match or int(match.group("number")) != amendment_no:
        raise ValueError("revision must be <BASE>-AM<NN>@<64 lowercase hex SHA-256>")
    base_name = re.sub(r"-AM[0-9]+$", "", str(base_revision).split("@", 1)[0])
    if match.group("base") != base_name:
        raise ValueError("revision must extend the base revision with the next Amendment number")


_AMENDMENT_REQUIRED_FIELDS = {
    "amendment_id", "phase", "base_revision", "revision", "change_class",
    "changed_sections", "affected_phases", "affected_task_ids", "affected_trace_ids",
    "affected_acceptance_ids", "preserved_evidence_ids", "invalidated_evidence_ids",
    "impact_plan_digest", "impact_plan_path", "canonical_file",
    "canonical_document_digest", "amendment_no", "reason", "created_at",
    "executed_by_agent",
}
_AMENDMENT_ARRAY_FIELDS = {
    "changed_sections", "affected_phases", "affected_task_ids", "affected_trace_ids",
    "affected_acceptance_ids", "preserved_evidence_ids", "invalidated_evidence_ids",
}


def _string_array_errors(prefix: str, value: Any, field: str, *, nonempty: bool = False) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        errors.append(f"{prefix} {field} must be an array of non-empty strings")
        return errors
    if len(value) != len(set(value)):
        errors.append(f"{prefix} {field} must not contain duplicates")
    if nonempty and not value:
        errors.append(f"{prefix} {field} must not be empty")
    return errors


def validate_amendment_record(
    task_dir: Path,
    amendment: dict[str, Any],
    *,
    tasks: Iterable[dict[str, Any]] = (),
    traceability: Iterable[dict[str, Any]] = (),
    acceptance_assertions: dict[str, Any] | None = None,
    evidence_entries: dict[str, dict[str, Any]] | None = None,
    impact_plan: dict[str, Any] | None = None,
    require_impact_file: bool = True,
    validated_amendments_root: Path | None = None,
    amendments_tree_checked: bool = False,
    prefix: str = "amendment",
) -> list[str]:
    """Validate the semantic shape shared by command, history, and projection paths."""
    errors: list[str] = []
    if not isinstance(amendment, dict):
        return [f"{prefix} must be an object"]
    unknown = sorted(set(amendment) - AMENDMENT_FIELDS)
    if unknown:
        errors.append(f"{prefix} contains unknown fields: {unknown}")
    missing = sorted(_AMENDMENT_REQUIRED_FIELDS - set(amendment))
    errors.extend(f"{prefix} missing {field}" for field in missing)
    if not isinstance(amendment.get("amendment_id"), str) or not amendment.get("amendment_id", "").strip():
        errors.append(f"{prefix} amendment_id must be non-empty")

    phase = str(amendment.get("phase") or "")
    if phase not in KNOWN_PHASES:
        errors.append(f"{prefix} invalid phase: {phase}")
    if amendment.get("affected_phases") != [phase]:
        errors.append(f"{prefix} affected_phases must equal [phase]")
    for field in _AMENDMENT_ARRAY_FIELDS:
        errors.extend(_string_array_errors(
            prefix, amendment.get(field), field,
            nonempty=field in {"changed_sections", "affected_phases", "affected_task_ids"},
        ))
    change_class = str(amendment.get("change_class") or "").upper()
    if change_class not in revision_provenance.CHANGE_CLASSES:
        errors.append(f"{prefix} invalid change_class: {change_class}")
    if change_class == "PROCESS_ONLY":
        errors.append(f"{prefix} PROCESS_ONLY cannot be a semantic Amendment")
    raw_no = amendment.get("amendment_no")
    if isinstance(raw_no, bool) or not isinstance(raw_no, int) or raw_no <= 0:
        errors.append(f"{prefix} amendment_no must be a positive integer")
    revision = str(amendment.get("revision") or "")
    match = _REVISION.fullmatch(revision)
    if not match:
        errors.append(f"{prefix} revision must be <BASE>-AM<NN>@<64 lowercase hex SHA-256>")
    elif isinstance(raw_no, int) and not isinstance(raw_no, bool) and int(match.group("number")) != raw_no:
        errors.append(f"{prefix} amendment_no must equal revision AM number")
    if match and isinstance(raw_no, int) and not isinstance(raw_no, bool) and raw_no > 0:
        try:
            validate_amendment_revision(str(amendment.get("base_revision") or ""), revision, raw_no)
        except ValueError as exc:
            errors.append(f"{prefix} {exc}")
    try:
        created = datetime.fromisoformat(str(amendment.get("created_at") or "").replace("Z", "+00:00"))
        if created.tzinfo is None or created.utcoffset() is None:
            errors.append(f"{prefix} created_at must include a timezone")
    except (TypeError, ValueError):
        errors.append(f"{prefix} created_at must be a valid timezone-aware ISO-8601 timestamp")
    executed_by = amendment.get("executed_by_agent")
    if executed_by not in KNOWN_AGENTS:
        errors.append(f"{prefix} executed_by_agent is not a known Agent: {executed_by}")
    if executed_by != PROJECT_MANAGER_AGENT:
        errors.append(f"{prefix} executed_by_agent must be {PROJECT_MANAGER_AGENT}")
    if not isinstance(amendment.get("reason"), str) or not amendment.get("reason", "").strip():
        errors.append(f"{prefix} reason must be non-empty")

    impact_digest = str(amendment.get("impact_plan_digest") or "")
    if not re.fullmatch(_DIGEST, impact_digest):
        errors.append(f"{prefix} impact_plan_digest must be a 64-character lowercase SHA-256")
    impact_raw = str(amendment.get("impact_plan_path") or "")
    impact_path = Path(impact_raw)
    try:
        resolved_task_dir = safe_resolve(Path(task_dir), "task directory")
    except ValueError as exc:
        errors.append(f"{prefix} {exc}")
        return list(dict.fromkeys(errors))
    if impact_path.is_absolute():
        raw_impact = impact_path
    else:
        raw_impact = resolved_task_dir / impact_path
    amendments_root = resolved_task_dir / "history" / "amendments"
    amendments_tree_valid = True
    if not amendments_tree_checked:
        try:
            assert_no_symlink_tree(amendments_root, "impact plan snapshot tree")
            resolved_amendments_root = safe_resolve(
                amendments_root, "impact plan snapshot tree"
            )
        except (OSError, ValueError) as exc:
            errors.append(f"{prefix} {exc}")
            amendments_tree_valid = False
            resolved_amendments_root = amendments_root
    else:
        amendments_tree_valid = validated_amendments_root is not None
        resolved_amendments_root = validated_amendments_root or amendments_root
    try:
        resolved_impact = safe_resolve(raw_impact, "impact_plan_path")
    except ValueError as exc:
        errors.append(f"{prefix} {exc}")
        resolved_impact = None
    if resolved_impact is not None and not resolved_impact.is_relative_to(resolved_amendments_root):
        errors.append(f"{prefix} impact_plan_path must be inside task history/amendments: {resolved_impact}")
    plan = impact_plan
    if amendments_tree_valid and plan is None and resolved_impact is not None and resolved_impact.is_file():
        try:
            plan = json.loads(resolved_impact.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f"{prefix} impact plan snapshot is invalid: {exc}")
    elif amendments_tree_valid and plan is None and require_impact_file and resolved_impact is not None:
        errors.append(f"{prefix} impact plan is missing: {resolved_impact}")
    if amendments_tree_valid and impact_plan is None and resolved_impact is not None and resolved_impact.is_file():
        try:
            actual_impact_digest = hashlib.sha256(resolved_impact.read_bytes()).hexdigest()
            if actual_impact_digest != impact_digest:
                errors.append(f"{prefix} impact plan digest mismatch")
        except OSError as exc:
            errors.append(f"{prefix} cannot read impact plan snapshot: {exc}")
    if plan is not None:
        if not isinstance(plan, dict):
            errors.append(f"{prefix} impact plan snapshot must be an object")
        else:
            provenance = plan.get("provenance") if isinstance(plan.get("provenance"), dict) else {}
            expected_plan = {
                "taskDir": str(resolved_task_dir),
                "phase": phase,
                "baseRevision": str(amendment.get("base_revision") or ""),
                "revision": revision,
                "changeClass": change_class,
            }
            try:
                plan_task_dir = str(safe_resolve(
                    Path(str(plan.get("taskDir") or "")), "impact plan taskDir"
                ))
            except ValueError as exc:
                errors.append(f"{prefix} {exc}")
                plan_task_dir = ""
            actual_plan = {
                "taskDir": plan_task_dir,
                "phase": str(plan.get("phase") or ""),
                "baseRevision": str(provenance.get("baseRevision") or ""),
                "revision": str(provenance.get("revision") or ""),
                "changeClass": str(provenance.get("changeClass") or ""),
            }
            for field, expected in expected_plan.items():
                if actual_plan[field] != expected:
                    errors.append(f"{prefix} impact plan {field} mismatch")
            if plan.get("status") != "PASSED":
                errors.append(f"{prefix} impact plan status must be PASSED")
            if plan.get("blockers"):
                errors.append(f"{prefix} impact plan must not contain blockers")
            errors.extend(validate_bounded_impact_plan(
                plan, change_class=change_class, prefix=prefix + " bounded gate"
            ))
            scope = plan.get("affectedScope")
            if not isinstance(scope, dict):
                errors.append(f"{prefix} impact plan affectedScope must be an object")
            else:
                for plan_field, amendment_field in (("taskIds", "affected_task_ids"), ("traceIds", "affected_trace_ids"), ("acceptanceIds", "affected_acceptance_ids")):
                    scope_errors = _string_array_errors(prefix, scope.get(plan_field), f"affectedScope.{plan_field}")
                    errors.extend(scope_errors)
                    if isinstance(scope.get(plan_field), list) and set(scope[plan_field]) != set(amendment.get(amendment_field) or []):
                        errors.append(f"{prefix} impact plan affectedScope.{plan_field} mismatch")

    canonical_digest = str(amendment.get("canonical_document_digest") or "")
    if not re.fullmatch(_DIGEST, canonical_digest):
        errors.append(f"{prefix} canonical_document_digest must be a 64-character lowercase SHA-256")
    if not isinstance(amendment.get("canonical_file"), str) or not amendment.get("canonical_file", "").strip():
        errors.append(f"{prefix} canonical_file must be non-empty")

    task_list = list(tasks)
    trace_list = list(traceability)
    task_ids = set(str(item) for item in (amendment.get("affected_task_ids") or []))
    trace_ids = set(str(item) for item in (amendment.get("affected_trace_ids") or []))
    acceptance_ids = set(str(item) for item in (amendment.get("affected_acceptance_ids") or []))
    task_map = {str(item.get("id")): item for item in task_list if isinstance(item, dict) and item.get("id")}
    for task_id in sorted(task_ids):
        if task_id not in task_map or task_map[task_id].get("phase") != phase:
            errors.append(f"{prefix} affected task is not in phase: {task_id}")
    errors.extend(f"{prefix} {item}" for item in validate_affected_scope_closure(task_list, trace_list, task_ids, trace_ids, acceptance_ids))
    evidence_map = evidence_entries or {}
    preserve = set(str(item) for item in (amendment.get("preserved_evidence_ids") or []))
    invalidate = set(str(item) for item in (amendment.get("invalidated_evidence_ids") or []))
    if preserve & invalidate:
        errors.append(f"{prefix} preserved and invalidated Evidence overlap")
    for evidence_id in sorted(preserve | invalidate):
        if evidence_map and evidence_id not in evidence_map:
            errors.append(f"{prefix} unknown Evidence ID: {evidence_id}")
    return list(dict.fromkeys(errors))


def validate_amendment_history(
    task_dir: Path,
    state: dict[str, Any],
    history: dict[str, Any],
    tasks: list[dict[str, Any]],
    traceability: list[dict[str, Any]],
    acceptance_assertions: dict[str, Any],
    evidence_entries: dict[str, dict[str, Any]],
    errors: list[str],
) -> None:
    amendments = [item for item in history.get("amendment_events", []) if isinstance(item, dict)]
    try:
        version = version_root(task_dir)
        resolved_task_dir = safe_resolve(Path(task_dir), "task directory")
    except ValueError as exc:
        errors.append(f"amendment-history {exc}")
        return
    raw_doc_root = version / "doc"
    raw_amendments_root = resolved_task_dir / "history" / "amendments"
    doc_tree_valid = True
    amendments_tree_valid = True
    doc_entries: list[Path] = []
    resolved_doc_root: Path | None = None
    resolved_amendments_root: Path | None = None
    try:
        doc_entries = assert_no_symlink_tree(raw_doc_root, "canonical doc tree")
        resolved_doc_root = safe_resolve(raw_doc_root, "canonical doc tree")
    except (OSError, ValueError) as exc:
        errors.append(f"canonical-history {exc}")
        doc_tree_valid = False
    try:
        assert_no_symlink_tree(raw_amendments_root, "impact plan snapshot tree")
        resolved_amendments_root = safe_resolve(
            raw_amendments_root, "impact plan snapshot tree"
        )
    except (OSError, ValueError) as exc:
        errors.append(f"amendment-history {exc}")
        amendments_tree_valid = False
    materialized = task_events.materialize_tasks(tasks, history)
    task_map = {str(item.get("id")): item for item in materialized if isinstance(item, dict) and item.get("id")}
    trace_map = {str(item.get("id")): item for item in traceability if isinstance(item, dict) and item.get("id")}
    acceptance_ids = {
        str(item.get("acceptance_id")) for item in acceptance_assertions.values()
        if isinstance(item, dict) and item.get("acceptance_id")
    }
    for trace in traceability:
        if isinstance(trace, dict):
            acceptance_ids.update(str(item) for item in trace.get("acceptance_criteria") or [] if item)
    seen_ids: set[str] = set()
    seen_revisions: set[str] = set()
    previous_by_phase: dict[str, str] = {}
    previous_no: dict[str, int] = {}
    canonical_events: dict[Path, list[dict[str, Any]]] = {}
    for index, amendment in enumerate(amendments):
        prefix = f"amendment-history[{index}]"
        unknown = sorted(set(amendment) - AMENDMENT_FIELDS)
        if unknown:
            errors.append(f"{prefix} contains unknown fields: {unknown}")
        amendment_id = str(amendment.get("amendment_id") or "")
        revision = str(amendment.get("revision") or "")
        phase = str(amendment.get("phase") or "")
        required = {
            "amendment_id", "phase", "base_revision", "revision", "change_class",
            "changed_sections", "affected_phases", "affected_task_ids", "affected_trace_ids",
            "affected_acceptance_ids", "preserved_evidence_ids", "invalidated_evidence_ids",
            "impact_plan_digest", "impact_plan_path", "canonical_file",
            "canonical_document_digest", "amendment_no", "reason", "created_at", "executed_by_agent",
        }
        for key in sorted(required - set(amendment)):
            errors.append(f"{prefix} missing {key}")
        if not amendment_id or amendment_id in seen_ids:
            errors.append(f"{prefix} amendment_id is missing or duplicated")
        seen_ids.add(amendment_id)
        if not revision or revision in seen_revisions:
            errors.append(f"{prefix} revision is missing or duplicated")
        seen_revisions.add(revision)
        if phase not in KNOWN_PHASES:
            errors.append(f"{prefix} invalid phase: {phase}")
        if amendment.get("affected_phases") != [phase]:
            errors.append(f"{prefix} affected_phases must equal [phase]")
        number_match = re.search(r"-AM([0-9]+)@", revision)
        if not number_match:
            errors.append(f"{prefix} revision has invalid Amendment format")
        else:
            number = int(number_match.group(1))
            if number != previous_no.get(phase, 0) + 1:
                errors.append(f"{prefix} Amendment number is not contiguous for {phase}")
            previous_no[phase] = number
            try:
                validate_amendment_revision(str(amendment.get("base_revision") or ""), revision, number)
            except ValueError as exc:
                errors.append(f"{prefix} {exc}")
        if phase in previous_by_phase and amendment.get("base_revision") != previous_by_phase[phase]:
            errors.append(f"{prefix} base_revision does not continue the previous Amendment")
        previous_by_phase[phase] = revision
        if doc_tree_valid and resolved_doc_root is not None:
            try:
                canonical = canonical_file_path(
                    task_dir,
                    amendment.get("canonical_file", ""),
                    phase=phase,
                    tasks=materialized,
                    affected_task_ids=amendment.get("affected_task_ids") or [],
                    validated_doc_root=resolved_doc_root,
                )
                canonical_events.setdefault(canonical, []).append(amendment)
            except (OSError, ValueError) as exc:
                errors.append(f"{prefix} {exc}")
        errors.extend(validate_amendment_record(
            task_dir,
            amendment,
            tasks=materialized,
            traceability=traceability,
            acceptance_assertions=acceptance_assertions,
            evidence_entries=evidence_entries,
            validated_amendments_root=(
                resolved_amendments_root if amendments_tree_valid else None
            ),
            amendments_tree_checked=True,
            prefix=prefix,
        ))
        task_ids = set(amendment.get("affected_task_ids") or [])
        for task_id in sorted(task_ids):
            if task_id not in task_map or task_map[task_id].get("phase") != phase:
                errors.append(f"{prefix} affected task is not in phase: {task_id}")
        trace_ids = set(amendment.get("affected_trace_ids") or [])
        for trace_id in sorted(trace_ids):
            trace = trace_map.get(trace_id)
            if not trace:
                errors.append(f"{prefix} unknown affected trace: {trace_id}")
            elif task_ids and not task_ids.intersection(set(trace.get("plan_task_ids") or [])):
                errors.append(f"{prefix} affected trace is not mapped to affected tasks: {trace_id}")
        acceptance_set = set(amendment.get("affected_acceptance_ids") or [])
        for acceptance_id in sorted(acceptance_set - acceptance_ids):
            errors.append(f"{prefix} unknown affected acceptance: {acceptance_id}")
        errors.extend(
            f"{prefix} {item}" for item in validate_affected_scope_closure(
                materialized,
                traceability,
                task_ids,
                trace_ids,
                acceptance_set,
            )
        )
        preserve = set(amendment.get("preserved_evidence_ids") or [])
        invalidate = set(amendment.get("invalidated_evidence_ids") or [])
        if preserve & invalidate:
            errors.append(f"{prefix} preserved and invalidated Evidence overlap")
        for evidence_id in sorted(preserve | invalidate):
            if evidence_id not in evidence_entries:
                errors.append(f"{prefix} unknown Evidence ID: {evidence_id}")
        for task in materialized:
            if not isinstance(task, dict) or task.get("status") not in {"PASSED", "NOT_APPLICABLE"}:
                continue
            if str(task.get("id")) in task_ids:
                continue
            used = set(task.get("validation_evidence_ids") or [])
            leaked = sorted(used & invalidate)
            if leaked:
                errors.append(f"{prefix} invalidated Evidence remains on unaffected task {task.get('id')}: {leaked}")
    for canonical, file_amendments in canonical_events.items():
        relative = canonical.relative_to(version).as_posix()
        errors.extend(
            f"canonical-history[{relative}] {item}"
            for item in validate_canonical_amendment_file(canonical, file_amendments)
        )
    # Do not limit the audit universe to files already named by an event.  A
    # valid-looking block in an otherwise unreferenced version document is a
    # rogue semantic write and must fail closed as well.
    doc_root = resolved_doc_root or raw_doc_root
    event_canonicals = set(canonical_events)
    try:
        canonical_files: list[Path] = []
        if doc_tree_valid:
            for path in doc_entries:
                if not path.is_file() or path.suffix.lower() not in {".md", ".markdown"}:
                    continue
                resolved = safe_resolve(path, "canonical document")
                if not resolved.is_relative_to(doc_root):
                    errors.append(f"canonical document escapes version doc tree: {path}")
                    continue
                canonical_files.append(resolved)
        canonical_files.sort()
    except OSError as exc:
        errors.append(f"cannot scan canonical document tree: {exc}")
        canonical_files = []
    for canonical in canonical_files:
        try:
            blocks = amendment_blocks(canonical)
        except (OSError, ValueError) as exc:
            errors.append(f"canonical-history[{canonical.relative_to(version).as_posix()}] {exc}")
            continue
        if not blocks:
            continue
        if canonical not in event_canonicals:
            relative = canonical.relative_to(version).as_posix()
            errors.append(
                f"canonical-history[{relative}] contains Amendment blocks with no ARTIFACT_AMENDED event"
            )
            for block_index, block in enumerate(blocks):
                errors.extend(validate_amendment_record(
                    task_dir,
                    block,
                    tasks=materialized,
                    traceability=traceability,
                    acceptance_assertions=acceptance_assertions,
                    evidence_entries=evidence_entries,
                    validated_amendments_root=(
                        resolved_amendments_root if amendments_tree_valid else None
                    ),
                    amendments_tree_checked=True,
                    prefix=f"canonical-history[{relative}] block[{block_index}]",
                ))
    active = state.get("active_amendment")
    if isinstance(active, dict):
        if not amendments:
            errors.append("active_amendment has no corresponding Amendment history")
        elif active.get("revision") != amendments[-1].get("revision"):
            errors.append("active_amendment does not match the latest Amendment history")
        errors.extend(validate_amendment_record(
            task_dir,
            active,
            tasks=materialized,
            traceability=traceability,
            acceptance_assertions=acceptance_assertions,
            evidence_entries=evidence_entries,
            validated_amendments_root=(
                resolved_amendments_root if amendments_tree_valid else None
            ),
            amendments_tree_checked=True,
            prefix="active_amendment",
        ))


__all__ = [
    "affected_scope_closure", "amendment_lock", "task_writer_lock", "assert_no_symlink_tree",
    "safe_resolve",
    "canonical_file_path",
    "canonical_document_digest", "canonical_document_digest_for_amendment",
    "prepare_canonical_append", "validate_affected_scope_closure",
    "validate_amendment_record", "validate_amendment_revision", "validate_amendment_history",
    "validate_bounded_impact_plan",
    "validate_canonical_amendment", "validate_canonical_amendment_file",
]
