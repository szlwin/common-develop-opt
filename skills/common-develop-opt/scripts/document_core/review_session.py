#!/usr/bin/env python3
"""Plan and validate an advisory ReviewSession.

ReviewSession is an advisory planning/coordination object.  It binds the
inputs used to select independent review axes, but it is not a lifecycle gate
and it never finalizes a phase.  Canonical ReviewResult records remain the
source of review conclusions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_SCRIPT_ROOT = Path(__file__).resolve().parents[1]
_COMPAT_ROOT = _SCRIPT_ROOT / "compat"
if str(_COMPAT_ROOT) not in sys.path:
    sys.path.insert(0, str(_COMPAT_ROOT))

from document_core import agent_context
from document_core import impact_analysis
from runtime import review_contract
from runtime import work_mode

SCHEMA_VERSION = 1
SESSION_STATUSES = ("PLANNED", "IN_REVIEW", "COMPLETE")


def _read_json(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON must be an object: {path}")
    return value


def _detected_risks(report: dict[str, Any] | None, explicit: list[str]) -> set[str]:
    risks = {str(value) for value in explicit if value}
    for item in (report or {}).get("detections", []):
        if not isinstance(item, dict) or item.get("status") != "DETECTED":
            continue
        risk_type = str(item.get("risk_type") or item.get("riskType") or "")
        if risk_type:
            risks.add(risk_type)
    return risks


def _delta_maps(plan: dict[str, Any] | None) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    affected: dict[str, list[str]] = {}
    unaffected: dict[str, list[str]] = {}
    if not plan:
        return affected, unaffected
    for item in ((plan.get("minimumRevalidation") or {}).get("reviews") or []):
        if isinstance(item, dict) and item.get("profileId"):
            affected[str(item["profileId"])] = list(item.get("criterionIds") or [])
    for item in ((plan.get("unaffected") or {}).get("reviewCriteria") or []):
        if isinstance(item, dict) and item.get("profileId"):
            unaffected[str(item["profileId"])] = list(item.get("criterionIds") or [])
    return affected, unaffected


from runtime._core.canonical_json import digest_json as _digest


def _session_id(payload: dict[str, Any]) -> str:
    return f"RS-{_digest(payload)[:12].upper()}"


def _revision(value: Any, fallback: str) -> str:
    return str(value or fallback)


def plan_session(
    *,
    phase: str,
    revision: str,
    explicit_risks: list[str],
    risk_report: dict[str, Any] | None,
    impact_plan: dict[str, Any] | None,
    mode: str = "standard",
    input_digest: str = "",
    evidence_snapshot: dict[str, Any] | None = None,
    planner_agent: str = "ProjectManagerAgent",
) -> dict[str, Any]:
    if not phase or not revision:
        raise ValueError("phase and revision are required")
    if mode not in {"standard", "minimal", "lightweight"}:
        raise ValueError(f"unsupported ReviewSession work mode: {mode}")
    contract = review_contract.load_contract()
    runtime = agent_context.RUNTIME_CONTRACT
    canonical = {
        str(item.get("id")) for item in runtime.get("canonicalPhases", [])
        if isinstance(item, dict) and item.get("phaseKind", "CANONICAL") == "CANONICAL"
    }
    if phase not in canonical:
        raise ValueError(f"phase is not a selectable canonical phase: {phase}")
    risks = _detected_risks(risk_report, explicit_risks)
    risks.update(str(x) for x in (impact_plan or {}).get("requiredRiskTemplates", []) if x)
    unknown_risks = sorted(risks - set(contract.get("riskReviewTemplates", {})))
    if unknown_risks:
        raise ValueError(f"unknown risk Review templates: {unknown_risks}")
    affected, unaffected = _delta_maps(impact_plan)
    suppressed = work_mode.suppressed_reviewers(mode, phase)
    axes: list[dict[str, Any]] = []
    for profile_id, profile in sorted((contract.get("profiles") or {}).items()):
        if not isinstance(profile, dict) or profile.get("phase") != phase:
            continue
        reviewer = str(profile.get("reviewerAgent") or "")
        if reviewer in suppressed:
            continue
        criterion_ids = list(profile.get("criterionIds") or [])
        selected = affected.get(profile_id, criterion_ids if not impact_plan else [])
        stable = unaffected.get(profile_id, [c for c in criterion_ids if c not in set(selected)])
        axes.append({
            "axisId": "+".join(profile.get("axes") or [reviewer or "core"]),
            "origin": "CORE",
            "profileId": profile_id,
            "reviewerAgent": reviewer,
            "reviewerIdentity": {"agent": reviewer, "role": "REVIEWER"},
            "criterionIds": criterion_ids,
            "affectedCriterionIds": selected,
            "unaffectedCriterionIds": stable,
            "independentReviewResultRequired": True,
            "reviewResult": {"status": "PENDING", "revision": revision, "resultRef": ""},
        })
    applicability: list[dict[str, Any]] = []
    for risk_id, definition in sorted((contract.get("riskReviewTemplates") or {}).items()):
        if not isinstance(definition, dict) or phase not in set(definition.get("allowedPhases") or []):
            continue
        reviewer = str(definition.get("reviewerAgent") or "")
        applicable = risk_id in risks
        suppressed_risk = reviewer in suppressed
        decision = {
            "riskType": risk_id,
            "reviewerAgent": reviewer,
            "reviewerIdentity": {"agent": reviewer, "role": "REVIEWER"},
            "applicable": applicable,
            "suppressed": suppressed_risk,
            "reason": "RISK_DETECTED" if applicable else "RISK_NOT_DETECTED",
            "reviewResultEmittedWhenNotApplicable": False,
        }
        applicability.append(decision)
        if not applicable or suppressed_risk:
            continue
        profile = review_contract.resolve_profile(contract, phase, reviewer)
        profile_id = str(profile["profileId"])
        criterion_ids = list(profile.get("criterionIds") or [])
        selected = affected.get(profile_id, criterion_ids)
        stable = unaffected.get(profile_id, [c for c in criterion_ids if c not in set(selected)])
        axes.append({
            "axisId": str(definition.get("axis") or risk_id),
            "origin": "RISK",
            "riskType": risk_id,
            "profileId": profile_id,
            "reviewerAgent": reviewer,
            "reviewerIdentity": {"agent": reviewer, "role": "REVIEWER"},
            "criterionIds": criterion_ids,
            "affectedCriterionIds": selected,
            "unaffectedCriterionIds": stable,
            "independentReviewResultRequired": True,
            "reviewResult": {"status": "PENDING", "revision": revision, "resultRef": ""},
        })
    source = {"phase": phase, "revision": revision, "risks": sorted(risks), "riskReport": risk_report or {}, "impactPlan": impact_plan or {}, "evidenceSnapshot": evidence_snapshot or {}}
    digest = input_digest or _digest(source)
    snapshot = dict(evidence_snapshot or {})
    snapshot.setdefault("revision", revision)
    snapshot.setdefault("inputDigest", digest)
    risk_revision = _revision((risk_report or {}).get("input_revision") or (risk_report or {}).get("revision"), revision)
    impact_revision = _revision((((impact_plan or {}).get("provenance") or {}).get("revision") or (impact_plan or {}).get("revision")), revision)
    seed = {"phase": phase, "revision": revision, "inputDigest": digest, "mode": mode, "risks": sorted(risks), "axes": [(a["profileId"], a["affectedCriterionIds"]) for a in axes]}
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "kind": "review_session",
        "advisory": True,
        "reviewSessionId": _session_id(seed),
        "plannerAgent": planner_agent,
        "mode": mode,
        "phase": phase,
        "revision": revision,
        "executionIsolation": {
            "required": True,
            "preferredContext": "ISOLATED_AGENT_SESSION",
            "sameOwnerContextAgentSwitchAllowed": False,
            "fallbackContext": "INDEPENDENT_HUMAN_REVIEW_WITH_LIMITATION",
        },
        "sharedFreeze": {
            "phase": phase,
            "revision": revision,
            "inputDigest": digest,
            "evidenceSnapshot": snapshot,
            "riskDetection": {"revision": risk_revision, "detectedRiskTypes": sorted(risks)},
            "impactPlan": {"revision": impact_revision, "present": impact_plan is not None},
        },
        "axes": axes,
        "applicabilityDecisions": applicability,
        "deltaPolicy": {
            "enabled": impact_plan is not None,
            "reviewResultReuseAcrossExactRevision": False,
            "affectedCriterionAction": "INDEPENDENT_REVIEW_REQUIRED",
            "unaffectedCriterionAction": "RECAPTURE_OR_REBIND_CURRENT_REVISION_EVIDENCE",
        },
        "status": "PLANNED",
        "lifecycleGate": "NOT_REQUIRED",
    }
    return payload


def validate_session(value: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(value, dict):
        return ["review session must be an object"]
    if value.get("schemaVersion") != SCHEMA_VERSION or value.get("kind") != "review_session":
        errors.append("review session schema/kind is invalid")
        return errors
    phase = str(value.get("phase") or "")
    revision = str(value.get("revision") or "")
    session_id = str(value.get("reviewSessionId") or "")
    if not phase or not revision or not session_id.startswith("RS-") or len(session_id) != 15:
        errors.append("review session identity is incomplete")
    if value.get("advisory") is not True:
        errors.append("review session must be explicitly advisory")
    if value.get("lifecycleGate") not in {"NOT_REQUIRED", "ADVISORY_ONLY", None}:
        errors.append("review session must not act as a lifecycle gate")
    if value.get("status") not in SESSION_STATUSES:
        errors.append("review session status is invalid")
    isolation = value.get("executionIsolation")
    if not isinstance(isolation, dict) or isolation.get("required") is not True:
        errors.append("review session requires an isolated execution context")
    elif isolation.get("sameOwnerContextAgentSwitchAllowed") is not False:
        errors.append("review session must reject same-owner-context Agent switching")
    mode = str(value.get("mode") or "standard")
    try:
        suppressed = work_mode.suppressed_reviewers(mode, phase)
    except Exception as exc:
        errors.append(str(exc)); suppressed = set()
    freeze = value.get("sharedFreeze")
    if not isinstance(freeze, dict):
        errors.append("sharedFreeze must be an object")
        freeze = {}
    if freeze.get("phase") not in {None, phase}:
        errors.append("sharedFreeze phase differs from session")
    if freeze.get("revision") != revision:
        errors.append("sharedFreeze revision differs from session")
    digest = str(freeze.get("inputDigest") or "")
    if not re_fullmatch_hex64(digest):
        errors.append("sharedFreeze inputDigest is invalid")
    snapshot = freeze.get("evidenceSnapshot")
    if not isinstance(snapshot, dict):
        errors.append("sharedFreeze evidenceSnapshot must be an object")
    else:
        if str(snapshot.get("revision") or "") != revision:
            errors.append("evidence snapshot revision differs from session")
        if str(snapshot.get("inputDigest") or "") != digest:
            errors.append("evidence snapshot inputDigest differs from shared freeze")
    for key in ("riskDetection", "impactPlan"):
        item = freeze.get(key)
        if not isinstance(item, dict) or str(item.get("revision") or "") != revision:
            errors.append(f"sharedFreeze {key} revision differs from session")
    contract = review_contract.load_contract()
    seen: set[str] = set()
    axis_results: set[str] = set()
    for index, axis in enumerate(value.get("axes") or []):
        if not isinstance(axis, dict):
            errors.append(f"axes[{index}] must be an object"); continue
        profile_id = str(axis.get("profileId") or "")
        if profile_id in seen:
            errors.append(f"duplicate Review axis profile: {profile_id}")
        seen.add(profile_id)
        if ":" not in profile_id:
            errors.append(f"axes[{index}] profileId is invalid"); continue
        pphase, reviewer = profile_id.split(":", 1)
        if pphase != phase:
            errors.append(f"axes[{index}] profile phase differs from session"); continue
        try:
            profile = review_contract.resolve_profile(contract, pphase, reviewer)
        except ValueError as exc:
            errors.append(str(exc)); continue
        if reviewer in suppressed:
            errors.append(f"axes[{index}] reviewer is suppressed in {mode} mode: {reviewer}")
        if axis.get("reviewerAgent") != reviewer:
            errors.append(f"axes[{index}] reviewer identity differs from profile")
        identity = axis.get("reviewerIdentity")
        if not isinstance(identity, dict) or identity.get("agent") != reviewer or identity.get("role") != "REVIEWER":
            errors.append(f"axes[{index}] reviewerIdentity is invalid")
        allowed = set(profile.get("criterionIds") or [])
        all_ids = set(axis.get("criterionIds") or [])
        affected = set(axis.get("affectedCriterionIds") or [])
        unaffected = set(axis.get("unaffectedCriterionIds") or [])
        if all_ids != allowed:
            errors.append(f"axes[{index}] criterionIds differ from current profile")
        if affected & unaffected or affected | unaffected != all_ids:
            errors.append(f"axes[{index}] delta partition is invalid")
        result = axis.get("reviewResult")
        if not isinstance(result, dict):
            errors.append(f"axes[{index}] reviewResult is required"); continue
        if str(result.get("revision") or "") != revision:
            errors.append(f"axes[{index}] reviewResult revision differs from session")
        if result.get("status") not in {"PENDING", "PASSED", "FAILED", "BLOCKED", "NOT_APPLICABLE"}:
            errors.append(f"axes[{index}] reviewResult status is invalid")
        if result.get("status") != "PENDING":
            result_ref = str(result.get("resultRef") or "")
            if not result_ref:
                errors.append(f"axes[{index}] concluded reviewResult requires resultRef")
            axis_results.add(profile_id)
    for decision in value.get("applicabilityDecisions") or []:
        if not isinstance(decision, dict):
            errors.append("applicabilityDecisions entries must be objects"); continue
        risk = str(decision.get("riskType") or "")
        definition = (contract.get("riskReviewTemplates") or {}).get(risk)
        if not isinstance(definition, dict) or phase not in set(definition.get("allowedPhases") or []):
            errors.append(f"risk applicability is invalid for phase: {risk}")
        reviewer = str(decision.get("reviewerAgent") or "")
        identity = decision.get("reviewerIdentity")
        if not isinstance(identity, dict) or identity.get("agent") != reviewer:
            errors.append(f"risk applicability reviewer identity is invalid: {risk}")
    if value.get("status") == "COMPLETE":
        if not value.get("axes"):
            errors.append("complete review session requires at least one axis")
        if len(axis_results) != len(seen):
            errors.append("complete review session requires a concluded result for every axis")
    if value.get("status") == "IN_REVIEW" and not value.get("axes"):
        errors.append("in-review session requires at least one axis")
    return errors


def re_fullmatch_hex64(value: str) -> bool:
    return len(value) == 64 and all(char in "0123456789abcdefABCDEF" for char in value)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    agent_context.add_agent_argument(plan)
    plan.add_argument("--phase", required=True)
    plan.add_argument("--revision", required=True)
    plan.add_argument("--risk", action="append", default=[])
    plan.add_argument("--risk-report")
    plan.add_argument("--impact-plan")
    plan.add_argument("--mode", choices=["standard", "minimal", "lightweight"], default="standard")
    plan.add_argument("--input-digest", default="")
    plan.add_argument("--evidence-snapshot")
    plan.add_argument("--out")
    plan.add_argument("--json", action="store_true")
    val = sub.add_parser("validate")
    agent_context.add_agent_argument(val)
    val.add_argument("--file", required=True)
    val.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    agent_context.authorize_declared(args.agent, "review_session", args.command)
    if args.command == "plan":
        risk_report = _read_json(args.risk_report)
        impact_plan = _read_json(args.impact_plan)
        snapshot = _read_json(args.evidence_snapshot)
        if impact_plan:
            impact_analysis.load_plan(Path(args.impact_plan))
        payload = plan_session(phase=args.phase, revision=args.revision, explicit_risks=args.risk, risk_report=risk_report, impact_plan=impact_plan, mode=args.mode, input_digest=args.input_digest, evidence_snapshot=snapshot, planner_agent=args.agent)
        if args.out:
            from document_core.common_utils import atomic_write_text
            atomic_write_text(Path(args.out), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json or not args.out else payload["reviewSessionId"])
        return 0
    payload = _read_json(args.file) or {}
    errors = validate_session(payload)
    result = {"status": "PASSED" if not errors else "FAILED", "errors": errors, "reviewSessionId": payload.get("reviewSessionId")}
    print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else result["status"])
    return 0 if not errors else 1


def review_session_cli() -> int:
    try:
        return int(main())
    except (ValueError, RuntimeError, agent_context.AgentAuthorizationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(review_session_cli())
