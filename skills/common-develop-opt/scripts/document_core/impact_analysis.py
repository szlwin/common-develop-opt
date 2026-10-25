#!/usr/bin/env python3
"""Technology-neutral impact and minimum revalidation planner.

The core understands lifecycle semantics, Review profiles/criteria, capability
names, and one generic adapter-result protocol.  It does not understand build
systems, test frameworks, databases, languages, dependency graphs, or source
file semantics; projects may provide those through an optional adapter.
"""
from __future__ import annotations

import sys
from pathlib import Path
_COMPAT_ROOT = Path(__file__).resolve().parent.parent / "compat"
_SCRIPT_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_SCRIPT_ROOT), str(_COMPAT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import json
from pathlib import Path
from typing import Any, Iterable

from runtime import project_environment
from runtime import review_contract
from document_core import engineering_graph
from resource_paths import asset_path
from runtime._long_task import contracts as lifecycle_contract
from runtime._long_task import reason_codes as lifecycle_reason_codes, revision_provenance

ADAPTER_SCHEMA_VERSION = 1
CONFIDENCE = {"HIGH", "MEDIUM", "LOW", "UNKNOWN"}


class ImpactAnalysisError(RuntimeError):
    pass


def _scope_from_task_plan(
    task_dir: Path,
    task_ids: Iterable[str],
    trace_ids: Iterable[str],
    acceptance_ids: Iterable[str],
    changed_refs: Iterable[str] | None,
) -> dict[str, list[str]]:
    """Close stable task/trace/Acceptance IDs using only generic plan links."""
    selected_tasks = set(revision_provenance.unique_values(task_ids))
    selected_traces = set(revision_provenance.unique_values(trace_ids))
    selected_acceptance = set(revision_provenance.unique_values(acceptance_ids))
    try:
        plan = json.loads((Path(task_dir) / "task_plan.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        plan = {}
    tasks = [item for item in plan.get("tasks", []) if isinstance(item, dict)] if isinstance(plan, dict) else []
    task_map = {str(item.get("id")): item for item in tasks if item.get("id")}
    changed = set(revision_provenance.unique_values(changed_refs or []))
    selected_tasks.update(changed.intersection(task_map))
    dependents: dict[str, set[str]] = {}
    for item in tasks:
        for dependency in item.get("depends_on") or []:
            dependents.setdefault(str(dependency), set()).add(str(item.get("id")))
    queue = list(selected_tasks)
    while queue:
        current = queue.pop(0)
        for dependent in dependents.get(current, set()):
            if dependent not in selected_tasks:
                selected_tasks.add(dependent)
                queue.append(dependent)
    for task_id in selected_tasks:
        selected_traces.update(str(item) for item in (task_map.get(task_id) or {}).get("acceptance_trace_ids") or [] if item)
    try:
        trace_doc = json.loads((Path(task_dir) / "traceability.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        trace_doc = {}
    traces = trace_doc.get("items", []) if isinstance(trace_doc, dict) else []
    for trace in traces:
        if not isinstance(trace, dict) or not trace.get("id"):
            continue
        if str(trace.get("id")) in selected_traces or selected_tasks.intersection(set(trace.get("plan_task_ids") or [])):
            selected_traces.add(str(trace["id"]))
            selected_acceptance.update(str(item) for item in trace.get("acceptance_criteria") or [] if item)
    return {
        "taskIds": sorted(selected_tasks),
        "traceIds": sorted(selected_traces),
        "acceptanceIds": sorted(selected_acceptance),
    }



def _resolve_profile_for_phase(
    contract: dict[str, Any], profile_id: str, phase: str
) -> dict[str, Any]:
    if ":" not in profile_id:
        raise ImpactAnalysisError(f"unknown Review profile: {profile_id}")
    profile_phase, reviewer = profile_id.split(":", 1)
    try:
        profile = review_contract.resolve_profile(contract, profile_phase, reviewer)
    except (ValueError, RuntimeError) as exc:
        raise ImpactAnalysisError(f"unknown Review profile: {profile_id}") from exc
    resolved_phase = str(profile.get("phase") or "")
    if resolved_phase != phase:
        raise ImpactAnalysisError(
            f"Review profile {profile_id} belongs to phase {resolved_phase or '<unknown>'}; expected {phase}"
        )
    return profile


def _profiles_for_phase(contract: dict[str, Any], phase: str, requested: Iterable[str] | None) -> list[str]:
    profiles = contract.get("profiles") if isinstance(contract.get("profiles"), dict) else {}
    selected = revision_provenance.unique_values(requested)
    if selected:
        for item in selected:
            _resolve_profile_for_phase(contract, item, phase)
        return selected
    return [
        profile_id for profile_id, profile in profiles.items()
        if isinstance(profile, dict) and str(profile.get("phase") or "") == phase
    ]


def _profile_criteria(
    contract: dict[str, Any], profile_ids: Iterable[str], phase: str
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for profile_id in profile_ids:
        profile = _resolve_profile_for_phase(contract, profile_id, phase)
        result[profile_id] = revision_provenance.unique_values(profile.get("criterionIds") or [])
    return result


def load_adapter_result(path: Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImpactAnalysisError(f"cannot read impact adapter result {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ImpactAnalysisError("impact adapter result must be a JSON object")
    if payload.get("schemaVersion") != ADAPTER_SCHEMA_VERSION:
        raise ImpactAnalysisError(f"impact adapter schemaVersion must be {ADAPTER_SCHEMA_VERSION}")
    allowed = {
        "schemaVersion", "baseRevision", "revision", "confidence", "affectedScopes",
        "affectedArtifacts", "affectedReviewCriteria", "affectedProfiles",
        "affectedTaskIds", "affectedTraceIds", "affectedAcceptanceIds",
        "requiredCapabilities", "notes",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ImpactAnalysisError(f"impact adapter result contains unknown fields: {unknown}")
    confidence = str(payload.get("confidence") or "UNKNOWN").upper()
    if confidence not in CONFIDENCE:
        raise ImpactAnalysisError(f"invalid impact adapter confidence: {confidence}")
    for key in ("affectedScopes", "affectedArtifacts", "affectedReviewCriteria", "affectedProfiles", "affectedTaskIds", "affectedTraceIds", "affectedAcceptanceIds", "requiredCapabilities", "notes"):
        if key in payload and not isinstance(payload.get(key), list):
            raise ImpactAnalysisError(f"impact adapter {key} must be an array")
    payload = dict(payload)
    payload["confidence"] = confidence
    return payload


def _validate_adapter_revision(adapter: dict[str, Any] | None, provenance: dict[str, Any]) -> None:
    if not adapter:
        return
    for key in ("baseRevision", "revision"):
        value = str(adapter.get(key) or "")
        expected = str(provenance.get(key) or "")
        if value and expected and value != expected:
            raise ImpactAnalysisError(f"impact adapter {key}={value} does not match provenance {expected}")


def _criteria_plan(
    contract: dict[str, Any],
    profile_criteria: dict[str, list[str]],
    provenance: dict[str, Any],
    adapter: dict[str, Any] | None,
) -> tuple[dict[str, list[str]], dict[str, list[str]], str, list[str]]:
    catalog = contract.get("criteriaCatalog") if isinstance(contract.get("criteriaCatalog"), dict) else {}
    all_selected = set(item for values in profile_criteria.values() for item in values)
    if provenance.get("processOnly"):
        affected: set[str] = set()
        fallback = "PROCESS_ONLY_NO_SEMANTIC_REVALIDATION"
    else:
        confidence = str((adapter or {}).get("confidence") or "UNKNOWN")
        adapter_criteria = set(revision_provenance.unique_values((adapter or {}).get("affectedReviewCriteria") or []))
        adapter_profiles = revision_provenance.unique_values((adapter or {}).get("affectedProfiles") or [])
        unknown_criteria = sorted(adapter_criteria - set(catalog))
        if unknown_criteria:
            raise ImpactAnalysisError(f"impact adapter references unknown Review criteria: {unknown_criteria}")
        outside_selected = sorted(adapter_criteria - all_selected)
        if outside_selected:
            raise ImpactAnalysisError(
                f"impact adapter Review criteria are outside selected/current-phase profiles: {outside_selected}"
            )
        unknown_profiles = sorted(set(adapter_profiles) - set(profile_criteria))
        if unknown_profiles:
            raise ImpactAnalysisError(
                f"impact adapter affectedProfiles are outside selected/current-phase profiles: {unknown_profiles}"
            )
        for profile_id in adapter_profiles:
            adapter_criteria.update(profile_criteria[profile_id])
        if adapter and confidence in {"HIGH", "MEDIUM"} and (adapter_criteria or provenance.get("processOnly")):
            affected = adapter_criteria & all_selected
            fallback = "ADAPTER_SCOPED"
        else:
            affected = set(all_selected)
            fallback = "CONSERVATIVE_FULL_PROFILE"
    revalidate: dict[str, list[str]] = {}
    unaffected: dict[str, list[str]] = {}
    for profile_id, criterion_ids in profile_criteria.items():
        revalidate[profile_id] = [item for item in criterion_ids if item in affected]
        unaffected[profile_id] = [item for item in criterion_ids if item not in affected]
    warnings: list[str] = []
    if fallback == "CONSERVATIVE_FULL_PROFILE" and all_selected:
        warnings.append("impact is absent or insufficiently confident; all selected Review criteria require revalidation")
    return revalidate, unaffected, fallback, warnings



def _graph_impact(graph_path: Path | None, changed_refs: Iterable[str] | None) -> dict[str, Any] | None:
    refs = revision_provenance.unique_values(changed_refs or [])
    if not graph_path or not refs:
        return None
    try:
        graph = json.loads(Path(graph_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImpactAnalysisError(f"cannot read engineering graph {graph_path}: {exc}") from exc
    validation = engineering_graph.validate(graph)
    if validation.get("status") != "PASSED":
        raise ImpactAnalysisError(f"engineering graph is invalid: {validation.get('errors')}")
    node_by_id = {str(item.get("id")): item for item in graph.get("nodes", []) if isinstance(item, dict)}
    unknown = [ref for ref in refs if ref not in node_by_id]
    if unknown:
        raise ImpactAnalysisError(f"changedRefs not present in engineering graph: {unknown}")
    impacted = engineering_graph.impact(graph, refs)
    contract = json.loads(asset_path('assets/runtime/incremental-validity-contract.json').read_text(encoding="utf-8"))
    policy = contract.get("graphImpactPolicy") if isinstance(contract.get("graphImpactPolicy"), dict) else {}
    risk_mapping = policy.get("riskMapping") if isinstance(policy.get("riskMapping"), dict) else {}
    risks: set[str] = set()
    contexts: set[str] = set()
    for item in impacted:
        node = node_by_id.get(str(item.get("ref"))) or {}
        context = str(node.get("contextRef") or "")
        if context:
            contexts.add(context)
        for risk in risk_mapping.get(str(node.get("type") or ""), []) or []:
            risks.add(str(risk))
    if len(contexts) > 1:
        risks.update(str(x) for x in policy.get("crossContextRiskTemplates", []) or [])
    return {
        "graphPath": str(graph_path),
        "changedRefs": refs,
        "impactedRefs": impacted,
        "impactedNodeTypes": sorted({str((node_by_id.get(str(item.get('ref'))) or {}).get('type') or '') for item in impacted if (node_by_id.get(str(item.get('ref'))) or {}).get('type')}),
        "contexts": sorted(contexts),
        "crossContext": len(contexts) > 1,
        "requiredRiskTemplates": sorted(risks),
        "confidence": "HIGH",
    }


def _graph_selected_criteria(contract: dict[str, Any], phase: str, profile_criteria: dict[str, list[str]], graph_impact: dict[str, Any] | None) -> set[str]:
    if not graph_impact:
        return set()
    selected: set[str] = set()
    # Semantic/domain changes conservatively revalidate all core criteria for the current phase.
    semantic_types = {"SYSTEM","DOMAIN","BOUNDED_CONTEXT","TERM","ENTITY","VALUE_OBJECT","AGGREGATE","INVARIANT","STATE_MACHINE","DOMAIN_SERVICE","REQUIREMENT","FEATURE","BUSINESS_RULE","ACCEPTANCE_CRITERION","PROCESS"}
    if semantic_types & set(graph_impact.get("impactedNodeTypes") or []):
        for profile_id, criteria in profile_criteria.items():
            selected.update(criteria)
    # Risk templates materialize exact phase-specific criterion sets only when graph says the risk applies.
    for risk_id in graph_impact.get("requiredRiskTemplates") or []:
        definition = (contract.get("riskReviewTemplates") or {}).get(risk_id)
        if not isinstance(definition, dict):
            raise ImpactAnalysisError(f"unknown risk Review template: {risk_id}")
        allowed_phases = definition.get("allowedPhases")
        if not isinstance(allowed_phases, list) or phase not in set(allowed_phases):
            raise ImpactAnalysisError(
                f"risk Review template {risk_id} is not allowed for phase {phase}; use reopen-phase"
            )
        profile = _resolve_profile_for_phase(
            contract, f"{phase}:{definition.get('reviewerAgent') or ''}", phase
        )
        selected.update(profile.get("criterionIds") or [])
    return selected


def build_plan(
    *,
    task_dir: Path,
    phase: str,
    base_revision: str,
    revision: str,
    change_class: str,
    changed_scopes: Iterable[str] | None = None,
    changed_paths: Iterable[str] | None = None,
    semantic_artifact_changed: str | bool | None = None,
    production_changed: str | bool | None = None,
    profile_ids: Iterable[str] | None = None,
    required_capabilities: Iterable[str] | None = None,
    adapter_result: dict[str, Any] | None = None,
    project_root: Path | None = None,
    version: str = "",
    graph_path: Path | None = None,
    changed_refs: Iterable[str] | None = None,
    affected_task_ids: Iterable[str] | None = None,
    affected_trace_ids: Iterable[str] | None = None,
    affected_acceptance_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    normalized_phase = str(phase or "").strip()
    if normalized_phase not in set(lifecycle_contract.PHASES):
        raise ImpactAnalysisError(f"unknown impact-analysis phase: {phase}")
    phase = normalized_phase
    provenance = revision_provenance.build(
        base_revision=base_revision,
        revision=revision,
        change_class=change_class,
        changed_scopes=changed_scopes,
        changed_paths=changed_paths,
        semantic_artifact_changed=semantic_artifact_changed,
        production_changed=production_changed,
    )
    _validate_adapter_revision(adapter_result, provenance)
    contract = review_contract.load_contract()
    selected_profiles = _profiles_for_phase(contract, phase, profile_ids)
    profile_criteria = _profile_criteria(contract, selected_profiles, phase)
    graph_impact = None if provenance.get("processOnly") else _graph_impact(graph_path, changed_refs)
    if graph_impact and not provenance.get("processOnly"):
        # Graph risks can materialize dynamic Review profiles that are not in
        # the static profile catalog. Include them before deriving partitions.
        for risk_id in graph_impact.get("requiredRiskTemplates") or []:
            definition = (contract.get("riskReviewTemplates") or {}).get(risk_id)
            if not isinstance(definition, dict):
                raise ImpactAnalysisError(f"unknown risk Review template: {risk_id}")
            allowed_phases = definition.get("allowedPhases")
            if not isinstance(allowed_phases, list) or phase not in set(allowed_phases):
                raise ImpactAnalysisError(
                    f"risk Review template {risk_id} is not allowed for phase {phase}; use reopen-phase"
                )
            reviewer = str(definition.get("reviewerAgent") or "")
            profile_id = f"{phase}:{reviewer}"
            profile_criteria.setdefault(
                profile_id,
                revision_provenance.unique_values(
                    _resolve_profile_for_phase(contract, profile_id, phase).get("criterionIds") or []
                ),
            )
    revalidate, unaffected, fallback, warnings = _criteria_plan(
        contract, profile_criteria, provenance, adapter_result
    )
    graph_criteria = _graph_selected_criteria(contract, phase, profile_criteria, graph_impact)
    if graph_impact and graph_criteria and not provenance.get("processOnly"):
        all_selected = {c for values in profile_criteria.values() for c in values}
        affected = graph_criteria & all_selected
        revalidate = {pid: [c for c in criteria if c in affected] for pid, criteria in profile_criteria.items()}
        unaffected = {pid: [c for c in criteria if c not in affected] for pid, criteria in profile_criteria.items()}
        fallback = "GRAPH_SCOPED"
    capabilities = [] if provenance.get("processOnly") else revision_provenance.unique_values(
        list(required_capabilities or []) + list((adapter_result or {}).get("requiredCapabilities") or [])
    )
    required_risks = revision_provenance.unique_values((graph_impact or {}).get("requiredRiskTemplates") or [])
    scope = _scope_from_task_plan(
        task_dir,
        revision_provenance.unique_values(
            list(affected_task_ids or []) + list((adapter_result or {}).get("affectedTaskIds") or [])
        ),
        revision_provenance.unique_values(
            list(affected_trace_ids or []) + list((adapter_result or {}).get("affectedTraceIds") or [])
        ),
        revision_provenance.unique_values(
            list(affected_acceptance_ids or []) + list((adapter_result or {}).get("affectedAcceptanceIds") or [])
        ),
        changed_refs,
    )
    capability_resolution: dict[str, Any] = {"status": "NOT_EVALUATED", "required": capabilities}
    blockers: list[str] = []
    if project_root is not None:
        env = project_environment.project_capabilities(Path(project_root), version=version)
        declared = env.get("capabilities") if isinstance(env.get("capabilities"), dict) else {}
        missing = [name for name in capabilities if not isinstance(declared.get(name), dict) or declared[name].get("available") is not True]
        capability_resolution = {
            "status": "BLOCKED" if missing else "PASSED",
            "required": capabilities,
            "missing": missing,
            "environmentStatus": env.get("status"),
        }
        blockers.extend(f"required capability unavailable: {name}" for name in missing)
    revalidation_profiles = [
        {"profileId": profile_id, "criterionIds": criteria}
        for profile_id, criteria in revalidate.items() if criteria
    ]
    unaffected_profiles = [
        {"profileId": profile_id, "criterionIds": criteria}
        for profile_id, criteria in unaffected.items() if criteria
    ]
    revalidation_required = bool(revalidation_profiles or capabilities)
    if provenance.get("processOnly") and not revalidation_required:
        acceptance_disposition = "UNCHANGED"
    else:
        acceptance_disposition = "RECHECK_AFFECTED_ASSERTIONS"
    reason_codes: list[str] = []
    if provenance.get("processOnly"):
        reason_codes.append(lifecycle_reason_codes.PROCESS_ONLY_CHANGE)
    if revalidation_profiles:
        reason_codes.append(lifecycle_reason_codes.CRITERION_REVALIDATION_REQUIRED)
    if capabilities:
        reason_codes.append(lifecycle_reason_codes.VALIDATION_CAPABILITY_REQUIRED)
    if fallback == "CONSERVATIVE_FULL_PROFILE":
        reason_codes.append(lifecycle_reason_codes.IMPACT_UNKNOWN_CONSERVATIVE)
    if blockers:
        reason_codes.append(lifecycle_reason_codes.IMPACT_CAPABILITY_BLOCKED)
    warnings = list(provenance.get("warnings") or []) + warnings
    return {
        "schemaVersion": 1,
        "authority": "DERIVED",
        "kind": "incremental_revalidation_plan",
        "taskDir": str(Path(task_dir)),
        "phase": phase,
        "provenance": provenance,
        "graphImpact": graph_impact,
        "requiredRiskTemplates": required_risks,
        "affectedScope": scope,
        "adapter": {
            "used": adapter_result is not None and not provenance.get("processOnly"),
            "confidence": (
                str((adapter_result or {}).get("confidence") or "NONE")
                if not provenance.get("processOnly") else "NONE"
            ),
            "affectedScopes": (
                revision_provenance.unique_values((adapter_result or {}).get("affectedScopes") or [])
                if not provenance.get("processOnly") else []
            ),
            "affectedArtifacts": (
                revision_provenance.unique_values((adapter_result or {}).get("affectedArtifacts") or [])
                if not provenance.get("processOnly") else []
            ),
            "notes": revision_provenance.unique_values((adapter_result or {}).get("notes") or []),
        },
        "fallbackPolicy": fallback,
        "minimumRevalidation": {
            "reviews": revalidation_profiles,
            "validationCapabilities": capabilities,
            "acceptanceDisposition": acceptance_disposition,
        },
        "unaffected": {"reviewCriteria": unaffected_profiles},
        "reusePolicy": {
            "reviewResultReuseAcrossExactRevision": False,
            "criterionEvidenceIdReuseAcrossExactRevision": False,
            "unaffectedEvidenceSourceReuse": "CONDITIONAL_RECAPTURE_OR_REBIND",
            "conditions": [
                "source content/digest and criterion scope remain unchanged",
                "the source is recaptured or deterministically rebound as current-revision Evidence before it supports the criterion",
                "current Evidence remains ACTIVE and satisfies the criterion Evidence Policy",
                "Reviewer still emits a new independent Review Result when the exact review revision changes",
            ],
        },
        "revalidationRequired": revalidation_required,
        "reasonCodes": reason_codes,
        "capabilityResolution": capability_resolution,
        "warnings": warnings,
        "blockers": blockers,
        "status": "BLOCKED" if blockers else "PASSED",
    }


def load_plan(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImpactAnalysisError(f"cannot read impact plan {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schemaVersion") != 1 or payload.get("kind") != "incremental_revalidation_plan":
        raise ImpactAnalysisError("impact plan must be incremental_revalidation_plan schemaVersion=1")
    if payload.get("authority") != "DERIVED":
        raise ImpactAnalysisError("impact plan authority must be DERIVED")
    return payload


def tighten_lifecycle_decision(decision: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Only tighten a lifecycle decision; impact data can never authorize a gate."""
    result = dict(decision)
    result["impactPlan"] = {
        "revision": str(((plan.get("provenance") or {}).get("revision") or "")),
        "changeClass": str(((plan.get("provenance") or {}).get("changeClass") or "")),
        "fallbackPolicy": plan.get("fallbackPolicy"),
        "reasonCodes": list(plan.get("reasonCodes") or []),
    }
    if plan.get("status") == "BLOCKED":
        result.update({
            "classification": "BLOCKED",
            "currentState": lifecycle_reason_codes.IMPACT_ANALYSIS_BLOCKED,
            "preferredAction": "resolve-impact-blockers",
            "legalActions": ["wk.impact", "wk.env"],
        })
        result["reasonCodes"] = list(dict.fromkeys(list(result.get("reasonCodes") or []) + [lifecycle_reason_codes.IMPACT_ANALYSIS_BLOCKED]))
        result["blockers"] = list(dict.fromkeys(list(result.get("blockers") or []) + list(plan.get("blockers") or [])))
        return result
    if plan.get("revalidationRequired"):
        missing = list(result.get("missing") or [])
        for review in ((plan.get("minimumRevalidation") or {}).get("reviews") or []):
            if not isinstance(review, dict):
                continue
            profile_id = str(review.get("profileId") or "")
            criteria = ",".join(str(item) for item in review.get("criterionIds") or [])
            missing.append(f"Review revalidation {profile_id}: {criteria}")
        for capability in ((plan.get("minimumRevalidation") or {}).get("validationCapabilities") or []):
            missing.append(f"validation capability: {capability}")
        result.update({
            "classification": "REVALIDATION_REQUIRED",
            "currentState": "IMPACT_REVALIDATION_REQUIRED",
            "preferredAction": "revalidate-impacted-scope",
            "legalActions": ["manual_review.prepare", "manual_review.submit", "execution_evidence.run", "task_verify.verify"],
            "missing": list(dict.fromkeys(missing)),
        })
        result["reasonCodes"] = list(dict.fromkeys(list(result.get("reasonCodes") or []) + list(plan.get("reasonCodes") or [])))
    return result
