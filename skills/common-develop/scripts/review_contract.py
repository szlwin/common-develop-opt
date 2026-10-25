#!/usr/bin/env python3
"""Query the current common-develop Review acceptance contract without loading it wholesale."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path
from typing import Any

import agent_context
import contract_normalization

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = ROOT / "assets" / "review-contract.json"


def load_source_contract() -> dict[str, Any]:
    """Load the canonical review contract."""
    try:
        data = contract_normalization.load_review_contract(CONTRACT_PATH)
    except contract_normalization.ContractNormalizationError as exc:
        raise RuntimeError(str(exc)) from exc
    if data.get("schemaVersion") != 2:
        raise RuntimeError("review contract schemaVersion must be 2")
    if not isinstance(data.get("profiles"), dict) or not isinstance(data.get("criteriaCatalog"), dict):
        raise RuntimeError("review contract profiles/criteriaCatalog are invalid")
    if not isinstance(data.get("evidencePolicyCatalog"), dict):
        raise RuntimeError("review contract evidencePolicyCatalog is invalid")
    if not isinstance(data.get("profileTemplates"), dict):
        raise RuntimeError("review contract profileTemplates is invalid")
    if not isinstance(data.get("riskReviewTemplates", {}), dict):
        raise RuntimeError("review contract riskReviewTemplates is invalid")
    return data


def expand_contract(source: dict[str, Any]) -> dict[str, Any]:
    """Return the executable contract with refs expanded in memory.

    The JSON file stays compact and single-sourced. Runtime callers continue to
    receive ``criterion.evidencePolicy`` and ``profile.criterionIds`` from one
    normalized source without spreading reference-resolution logic.
    """
    data = copy.deepcopy(source)
    policies = data.get("evidencePolicyCatalog") or {}
    templates = data.get("profileTemplates") or {}
    for criterion_id, criterion in data["criteriaCatalog"].items():
        if not isinstance(criterion, dict):
            raise RuntimeError(f"review criterion is invalid: {criterion_id}")
        policy_ref = criterion.get("evidencePolicyRef")
        policy = policies.get(policy_ref)
        if not isinstance(policy_ref, str) or not isinstance(policy, dict):
            raise RuntimeError(f"review criterion references invalid evidence policy: {criterion_id}")
        criterion["evidencePolicy"] = copy.deepcopy(policy)
    for profile_id, profile in data["profiles"].items():
        if not isinstance(profile, dict):
            raise RuntimeError(f"review profile is invalid: {profile_id}")
        template_ref = profile.get("templateRef")
        if template_ref is not None:
            template = templates.get(template_ref)
            if not isinstance(template_ref, str) or not isinstance(template, dict):
                raise RuntimeError(f"review profile references invalid template: {profile_id}")
            criterion_ids = template.get("criterionIds")
            if not isinstance(criterion_ids, list):
                raise RuntimeError(f"review profile template criterionIds are invalid: {template_ref}")
            profile["criterionIds"] = list(criterion_ids)
        elif not isinstance(profile.get("criterionIds"), list):
            raise RuntimeError(f"review profile criterionIds are invalid: {profile_id}")
    return data


def _risk_profile(contract: dict[str, Any], phase: str, reviewer: str) -> dict[str, Any] | None:
    templates = contract.get("riskReviewTemplates", {})
    profile_templates = contract.get("profileTemplates", {})
    for risk_id, definition in templates.items():
        if not isinstance(definition, dict):
            continue
        if definition.get("reviewerAgent") != reviewer or phase not in set(definition.get("allowedPhases") or []):
            continue
        criterion_ids = definition.get("criterionIds")
        template_ref = definition.get("templateRef")
        if criterion_ids is None and isinstance(template_ref, str):
            template = profile_templates.get(template_ref)
            criterion_ids = template.get("criterionIds") if isinstance(template, dict) else None
        if not isinstance(criterion_ids, list):
            raise RuntimeError(f"risk Review template has no criterionIds: {risk_id}")
        return {
            "profileId": f"{phase}:{reviewer}",
            "phase": phase,
            "reviewerAgent": reviewer,
            "origin": "RISK_TEMPLATE",
            "riskType": risk_id,
            "axis": definition.get("axis"),
            "purpose": definition.get("purpose"),
            "scopeSourceTypes": list(definition.get("scopeSourceTypes") or []),
            "criterionIds": list(criterion_ids),
        }
    return None


def resolve_profile(contract: dict[str, Any], phase: str, reviewer: str) -> dict[str, Any]:
    profile_id = f"{phase}:{reviewer}"
    profile = contract.get("profiles", {}).get(profile_id)
    if isinstance(profile, dict):
        return profile
    risk = _risk_profile(contract, phase, reviewer)
    if isinstance(risk, dict):
        return risk
    raise ValueError(f"unknown review profile: {profile_id}")


def load_contract() -> dict[str, Any]:
    """Load the expanded runtime view of the Review contract."""
    return expand_contract(load_source_contract())


def profile_summary(profile: dict[str, Any]) -> dict[str, Any]:
    criterion_ids = profile.get("criterionIds", [])
    return {
        "profileId": profile.get("profileId"),
        "phase": profile.get("phase"),
        "reviewerAgent": profile.get("reviewerAgent"),
        "purpose": profile.get("purpose"),
        "criterionCount": len(criterion_ids) if isinstance(criterion_ids, list) else 0,
        "scopeSourceTypes": profile.get("scopeSourceTypes", []),
    }


def expanded_profile(contract: dict[str, Any], profile_id: str) -> dict[str, Any]:
    if ":" not in profile_id:
        raise ValueError(f"invalid review profile: {profile_id}")
    phase, reviewer = profile_id.split(":", 1)
    profile = resolve_profile(contract, phase, reviewer)
    catalog = contract["criteriaCatalog"]
    criteria: list[dict[str, Any]] = []
    for criterion_id in profile.get("criterionIds", []):
        criterion = catalog.get(criterion_id)
        if not isinstance(criterion, dict):
            raise RuntimeError(f"profile {profile_id} references missing criterion: {criterion_id}")
        criteria.append({"criterionId": criterion_id, **criterion})
    return {
        **profile,
        "criteria": criteria,
        "criterionStatuses": contract.get("criterionStatuses", []),
        "evidenceTypes": contract.get("evidenceTypes", {}),
        "conclusionDerivation": contract.get("conclusionDerivation", {}),
    }



def criterion_payload(contract: dict[str, Any], criterion_id: str) -> dict[str, Any]:
    criterion = contract.get("criteriaCatalog", {}).get(criterion_id)
    if not isinstance(criterion, dict):
        raise ValueError(f"unknown review criterion: {criterion_id}")
    return {"criterionId": criterion_id, **criterion}


def policy_payload(source: dict[str, Any], policy_id: str) -> dict[str, Any]:
    policy = source.get("evidencePolicyCatalog", {}).get(policy_id)
    if not isinstance(policy, dict):
        raise ValueError(f"unknown evidence policy: {policy_id}")
    users = sorted(
        criterion_id for criterion_id, criterion in source.get("criteriaCatalog", {}).items()
        if isinstance(criterion, dict) and criterion.get("evidencePolicyRef") == policy_id
    )
    return {"policyId": policy_id, **policy, "criterionIds": users}


def required_payload(contract: dict[str, Any], phase: str, reviewer: str) -> dict[str, Any]:
    profile_id = f"{phase}:{reviewer}"
    profile = resolve_profile(contract, phase, reviewer)
    catalog = contract.get("criteriaCatalog", {})
    criteria = []
    for criterion_id in profile.get("criterionIds", []):
        criterion = catalog.get(criterion_id)
        if not isinstance(criterion, dict):
            raise RuntimeError(f"profile {profile_id} references missing criterion: {criterion_id}")
        criteria.append({
            "criterionId": criterion_id,
            "title": criterion.get("title"),
            "question": criterion.get("question"),
            "evidencePolicyRef": criterion.get("evidencePolicyRef"),
        })
    return {
        "profile": profile_summary(profile),
        "criteria": criteria,
        "conclusionDerivation": contract.get("conclusionDerivation", {}),
    }

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="list compact Review profiles")
    agent_context.add_agent_argument(list_parser)
    list_parser.add_argument("--phase", help="filter by canonical phase")
    list_parser.add_argument("--reviewer", help="filter by Reviewer Agent")
    list_parser.add_argument("--include-risk", action="store_true", help="include reusable risk templates materialized for the phase")
    list_parser.add_argument("--json", action="store_true", help="emit JSON")

    show_parser = subparsers.add_parser("show", help="show one expanded Review profile")
    agent_context.add_agent_argument(show_parser)
    selector = show_parser.add_mutually_exclusive_group(required=True)
    selector.add_argument("--profile", help="profile id in <phase>:<reviewerAgent> form")
    selector.add_argument("--phase", help="canonical phase; requires --reviewer")
    show_parser.add_argument("--reviewer", help="Reviewer Agent used with --phase")
    show_parser.add_argument("--json", action="store_true", help="emit JSON")

    criterion_parser = subparsers.add_parser("criterion", help="show one Review criterion")
    agent_context.add_agent_argument(criterion_parser)
    criterion_parser.add_argument("--id", required=True, dest="criterion_id")
    criterion_parser.add_argument("--json", action="store_true", help="emit JSON")

    policy_parser = subparsers.add_parser("policy", help="show one Evidence Policy and its criterion users")
    agent_context.add_agent_argument(policy_parser)
    policy_parser.add_argument("--id", required=True, dest="policy_id")
    policy_parser.add_argument("--json", action="store_true", help="emit JSON")

    required_parser = subparsers.add_parser("required", help="show compact required criteria for one phase and Reviewer")
    agent_context.add_agent_argument(required_parser)
    required_parser.add_argument("--phase", required=True)
    required_parser.add_argument("--reviewer", required=True)
    required_parser.add_argument("--json", action="store_true", help="emit JSON")

    risk_parser = subparsers.add_parser("risk-templates", help="list reusable risk Review templates")
    agent_context.add_agent_argument(risk_parser)
    risk_parser.add_argument("--phase", help="filter by allowed phase")
    risk_parser.add_argument("--json", action="store_true")
    return parser


def command_list(args: argparse.Namespace) -> int:
    agent_context.authorize_declared(args.agent, "review_contract", "list")
    contract = load_contract()
    profiles = []
    for profile_id, profile in sorted(contract["profiles"].items()):
        if not isinstance(profile, dict):
            continue
        if args.phase and profile.get("phase") != args.phase:
            continue
        if args.reviewer and profile.get("reviewerAgent") != args.reviewer:
            continue
        profiles.append(profile_summary(profile))
    if args.include_risk:
        for risk_id, definition in sorted((contract.get("riskReviewTemplates") or {}).items()):
            if not isinstance(definition, dict):
                continue
            for phase in definition.get("allowedPhases") or []:
                if args.phase and phase != args.phase:
                    continue
                reviewer = str(definition.get("reviewerAgent") or "")
                if args.reviewer and reviewer != args.reviewer:
                    continue
                profiles.append(profile_summary(resolve_profile(contract, phase, reviewer)))
    payload = {"count": len(profiles), "profiles": profiles}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in profiles:
            print(f"{item['profileId']}\t{item['criterionCount']}\t{item['purpose']}")
    return 0


def command_risk_templates(args: argparse.Namespace) -> int:
    agent_context.authorize_declared(args.agent, "review_contract", "list")
    contract = load_contract()
    items = []
    for risk_id, definition in sorted((contract.get("riskReviewTemplates") or {}).items()):
        if not isinstance(definition, dict):
            continue
        phases = list(definition.get("allowedPhases") or [])
        if args.phase and args.phase not in phases:
            continue
        items.append({"riskType": risk_id, **definition})
    payload = {"count": len(items), "riskReviewTemplates": items}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in items:
            print(f"{item['riskType']}\t{item.get('reviewerAgent')}\t{','.join(item.get('allowedPhases') or [])}")
    return 0


def command_show(args: argparse.Namespace) -> int:
    agent_context.authorize_declared(args.agent, "review_contract", "show")
    if args.phase and not args.reviewer:
        raise ValueError("--phase requires --reviewer")
    if args.profile and args.reviewer:
        raise ValueError("--reviewer cannot be combined with --profile")
    profile_id = args.profile or f"{args.phase}:{args.reviewer}"
    payload = expanded_profile(load_contract(), profile_id)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Profile: {payload['profileId']}")
        print(f"Purpose: {payload['purpose']}")
        print("Criteria:")
        for criterion in payload["criteria"]:
            print(f"- {criterion['criterionId']}: {criterion['title']} — {criterion['question']}")
    return 0



def command_criterion(args: argparse.Namespace) -> int:
    agent_context.authorize_declared(args.agent, "review_contract", "criterion")
    payload = criterion_payload(load_contract(), args.criterion_id)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Criterion: {payload['criterionId']}")
        print(f"Title: {payload.get('title', '')}")
        print(f"Question: {payload.get('question', '')}")
        print(f"Evidence Policy: {payload.get('evidencePolicyRef', '')}")
    return 0


def command_policy(args: argparse.Namespace) -> int:
    agent_context.authorize_declared(args.agent, "review_contract", "policy")
    payload = policy_payload(load_source_contract(), args.policy_id)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Policy: {payload['policyId']}")
        print(f"Criteria: {','.join(payload['criterionIds'])}")
    return 0


def command_required(args: argparse.Namespace) -> int:
    agent_context.authorize_declared(args.agent, "review_contract", "required")
    payload = required_payload(load_contract(), args.phase, args.reviewer)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Profile: {payload['profile']['profileId']}")
        for criterion in payload["criteria"]:
            print(f"- {criterion['criterionId']}: {criterion['title']}")
    return 0

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "list":
        return command_list(args)
    if args.command == "show":
        return command_show(args)
    if args.command == "criterion":
        return command_criterion(args)
    if args.command == "policy":
        return command_policy(args)
    if args.command == "required":
        return command_required(args)
    if args.command == "risk-templates":
        return command_risk_templates(args)
    raise RuntimeError(f"unsupported command: {args.command}")


def cli() -> int:
    try:
        return main()
    except (ValueError, RuntimeError, agent_context.AgentAuthorizationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(cli())
