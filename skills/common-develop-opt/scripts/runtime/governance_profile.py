#!/usr/bin/env python3
"""Recommend project governance depth without changing workflow state.

The recommendation is deliberately advisory: project governance profile, request
workflow profile, and task work mode remain separate authorities.
"""
from __future__ import annotations

import argparse
import json
import hashlib
import re
import sys
from pathlib import Path
from typing import Any, Iterable

from document_core import agent_context
from runtime import project_environment
from document_core import impact_analysis
from runtime import project_constraints


class GovernanceProfileError(ValueError):
    """Invalid governance recommendation input."""


PROFILE_ORDER = ("SMALL", "PRODUCT", "HIGH_RISK")
_SIGNAL_ALIASES = {
    "db": "database", "data": "database", "sql": "database", "migration": "database",
    "auth": "permissions", "authorization": "permissions", "access-control": "permissions",
    "integration": "external_integration", "third-party": "external_integration", "external": "external_integration",
    "compliance": "regulated_audit", "regulated": "regulated_audit", "audit": "regulated_audit",
    "availability": "high_availability", "distributed": "high_availability",
}
_SIGNAL_TERMS = {
    "database": ("database", "db", "sql", "ddl", "migration", "persistence", "数据", "数据库", "迁移"),
    "permissions": ("permission", "authorization", "auth", "role", "access control", "权限", "授权", "角色"),
    "external_integration": ("external", "integration", "webhook", "payment", "third-party", "第三方", "外部"),
    "regulated_audit": ("compliance", "regulated", "audit", "financial", "medical", "production data", "合规", "审计", "生产数据"),
    "high_availability": ("availability", "distributed", "queue", "consistency", "failover", "高可用", "分布式"),
}
_HIGH_IMPACT_FIELDS = {
    "dataModel", "apiContract", "permission", "persistence", "externalSideEffect", "migration",
}
_IMPACT_WEIGHT = {"NONE": 0, "UNKNOWN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}


def _text_sources(project_root: Path) -> list[Path]:
    root = Path(project_root).resolve()
    candidates = [root / "project_env.md", root / "harness_env.md"]
    if root.name == "project_doc":
        candidates.extend([root / "project_env.md", root / "harness_env.md"])
    else:
        candidates.extend([root / "project_doc" / "project_env.md", root / "project_doc" / "harness_env.md"])
    return list(dict.fromkeys(path for path in candidates if path.is_file()))


def normalize_signals(signals: Iterable[str] = ()) -> list[str]:
    result: set[str] = set()
    for raw in signals:
        value = str(raw).strip().lower().replace(" ", "_")
        value = _SIGNAL_ALIASES.get(value, value)
        if value not in _SIGNAL_TERMS:
            raise GovernanceProfileError(
                f"unknown governance signal {raw!r}; allowed: {', '.join(sorted(_SIGNAL_TERMS))}"
            )
        result.add(value)
    return sorted(result)


def infer_project_signals(project_root: Path, *, capabilities: dict[str, Any] | None = None) -> list[str]:
    """Infer conservative signals from structured capabilities and environment text."""
    root = Path(project_root).resolve()
    projection = capabilities if capabilities is not None else project_environment.project_capabilities(root)
    haystack_parts: list[str] = []
    for name, definition in (projection.get("capabilities") or {}).items():
        haystack_parts.append(str(name))
        if isinstance(definition, dict):
            haystack_parts.extend(str(value) for value in definition.values())
    for path in _text_sources(root):
        try:
            haystack_parts.append(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    haystack = "\n".join(haystack_parts).lower()
    inferred = []
    for signal, terms in _SIGNAL_TERMS.items():
        if any(term.lower() in haystack for term in terms):
            inferred.append(signal)
    return sorted(set(inferred))


def _impact_signals(impact_assessment: dict[str, Any] | None) -> tuple[list[str], int]:
    signals: set[str] = set()
    max_weight = 0
    for field, value in (impact_assessment or {}).items():
        level = str(value or "UNKNOWN").upper()
        weight = _IMPACT_WEIGHT.get(level, 0)
        max_weight = max(max_weight, weight)
        if field in _HIGH_IMPACT_FIELDS and weight >= 2:
            signals.add({"dataModel": "database", "persistence": "database", "migration": "database", "permission": "permissions", "apiContract": "external_integration", "externalSideEffect": "external_integration"}.get(field, "regulated_audit"))
    return sorted(signals), max_weight


def _normalize_changed_scopes(scopes: Iterable[str] = ()) -> list[str]:
    return sorted({str(item).strip() for item in scopes if str(item).strip()})


def _scope_signals(scopes: Iterable[str]) -> list[str]:
    signals: set[str] = set()
    for raw in scopes:
        value = str(raw).lower().replace("-", "_")
        for signal, terms in _SIGNAL_TERMS.items():
            if any(term.lower() in value for term in terms):
                signals.add(signal)
    return sorted(signals)


def _hard_constraint_signals(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    constraints = payload.get("constraints") if isinstance(payload, dict) else []
    result: list[dict[str, Any]] = []
    for item in constraints if isinstance(constraints, list) else []:
        if not isinstance(item, dict) or str(item.get("type") or "").upper() != "HARD":
            continue
        status = str(item.get("status") or "UNVALIDATED").upper()
        if status == "WAIVED":
            continue
        result.append({
            "id": str(item.get("id") or ""),
            "gate": str(item.get("gate") or ""),
            "status": status,
        })
    return result


def _load_constraint_projection(project_root: Path) -> dict[str, Any] | None:
    try:
        projection = project_constraints.validate_project_constraints(project_root)
    except (OSError, ValueError):
        return None
    return projection if isinstance(projection, dict) and projection.get("status") != "NOT_DECLARED" else None


def _recommendation_input_digest(
    *,
    impact_assessment: dict[str, Any] | None,
    classification: str,
    changed_scopes: list[str],
    changed_refs: list[str],
    graph_impact: dict[str, Any] | None,
    hard_constraints: list[dict[str, Any]],
    current_profile: str,
    exploration: bool,
    override_reason: str,
) -> str:
    payload = {
        "impactAssessment": {str(k): str(v) for k, v in sorted((impact_assessment or {}).items())},
        "classification": str(classification or ""),
        "changedScopes": list(changed_scopes),
        "changedRefs": list(changed_refs),
        "graphImpact": graph_impact or {},
        "hardConstraintSignals": hard_constraints,
        "currentProfile": str(current_profile or ""),
        "exploration": bool(exploration),
        "overrideReason": str(override_reason or ""),
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _recommend_internal(
    project_root: Path,
    *,
    signals: Iterable[str] = (),
    impact_assessment: dict[str, Any] | None = None,
    classification: str = "",
    changed_scopes: Iterable[str] = (),
    graph_path: Path | None = None,
    changed_refs: Iterable[str] = (),
    constraints_payload: dict[str, Any] | None = None,
    current_profile: str = "",
    exploration: bool = False,
    override_reason: str = "",
) -> dict[str, Any]:
    """Return a stable, advisory governance profile and decision guide.

    Optional graph/constraint inputs only tighten the recommendation; they do
    not mutate request intake or workflow mode.
    """
    root = Path(project_root).resolve()
    explicit = normalize_signals(signals)
    try:
        capabilities = project_environment.project_capabilities(root)
    except (OSError, ValueError) as exc:
        raise GovernanceProfileError(str(exc)) from exc
    inferred = infer_project_signals(root, capabilities=capabilities)
    impact, max_impact = _impact_signals(impact_assessment)
    normalized_scopes = _normalize_changed_scopes(changed_scopes)
    scope_signals = _scope_signals(normalized_scopes)
    graph_impact: dict[str, Any] | None = None
    refs = list(changed_refs)
    if graph_path and refs:
        try:
            graph_impact = impact_analysis._graph_impact(Path(graph_path), refs)
        except (OSError, ValueError, impact_analysis.ImpactAnalysisError) as exc:
            raise GovernanceProfileError(str(exc)) from exc
    if graph_impact:
        if graph_impact.get("crossContext"):
            scope_signals = sorted(set(scope_signals) | {"external_integration"})
        if graph_impact.get("requiredRiskTemplates"):
            scope_signals = sorted(set(scope_signals) | {"regulated_audit"})
    constraints = constraints_payload if constraints_payload is not None else _load_constraint_projection(root)
    hard_constraints = _hard_constraint_signals(constraints)
    if hard_constraints:
        scope_signals = sorted(set(scope_signals) | {"regulated_audit"})
    all_signals = sorted(set(explicit) | set(inferred) | set(impact) | set(scope_signals))
    risk_signals = {"database", "permissions", "external_integration", "regulated_audit", "high_availability"}
    high = len(set(all_signals) & risk_signals) >= 2
    high = high or max_impact >= 3 or bool(set(all_signals) & {"regulated_audit", "high_availability"})
    high = high or bool(graph_impact and (graph_impact.get("crossContext") or graph_impact.get("requiredRiskTemplates")))
    high = high or len(hard_constraints) >= 2
    profile = "HIGH_RISK" if high else ("PRODUCT" if all_signals or classification not in {"", "SIMPLE_OPTIMIZATION"} else "SMALL")
    if profile == "HIGH_RISK":
        workflow = "STANDARD_WITH_RISK_GATES"
        suggested_mode = "-ar -gc"
        reason = "检测到多个高影响面或高风险项目约束，建议保留架构/检查点门禁。"
    elif profile == "PRODUCT":
        workflow = "STANDARD"
        suggested_mode = "standard"
        reason = "项目存在持续交付或外部影响信号，建议使用标准生命周期并按需求影响面加门禁。"
    elif classification == "SIMPLE_OPTIMIZATION":
        workflow = "SIMPLE_OPTIMIZATION_FLOW"
        suggested_mode = "standard"
        reason = "当前请求是简单优化；保持简单优化快速路径，不建议把治理 Profile 误当成 -min。"
    else:
        workflow = "MINIMAL_FOR_LOW_RISK"
        suggested_mode = "-min"
        reason = "未发现显著项目风险信号，低风险请求可考虑最小治理深度；仍须遵守需求级门禁。"
    if exploration:
        suggested_mode = "-ws -ar -gc" if profile == "HIGH_RISK" else "-ws"
        reason += " 当前请求包含会话式探索，建议使用 -ws；该建议仍不改变用户显式模式。"
    current = str(current_profile or "").strip()
    override = {"active": False, "currentProfile": current, "reason": ""}
    if current and current != profile:
        if not str(override_reason or "").strip():
            raise GovernanceProfileError("override_reason is required when current_profile differs from the recommendation")
        override = {
            "active": True, "currentProfile": current,
            "recommendedProfile": profile, "reason": str(override_reason).strip(),
        }
    confidence = "HIGH" if explicit or impact or graph_impact or hard_constraints else ("MEDIUM" if inferred or normalized_scopes else "LOW")
    normalized_refs = sorted({str(item).strip() for item in refs if str(item).strip()})
    input_digest = _recommendation_input_digest(
        impact_assessment=impact_assessment, classification=classification,
        changed_scopes=normalized_scopes, changed_refs=normalized_refs,
        graph_impact=graph_impact, hard_constraints=hard_constraints,
        current_profile=current, exploration=exploration, override_reason=override_reason,
    )
    return {
        "profile": profile,
        "signals": all_signals,
        "inferredSignals": inferred,
        "explicitSignals": explicit,
        "impactSignals": impact,
        "changeScopeSignals": scope_signals,
        "graphImpact": graph_impact or {},
        "hardConstraintSignals": hard_constraints,
        "currentGovernanceProfile": current,
        "override": override,
        "changedScopes": normalized_scopes,
        "changedRefs": normalized_refs,
        "exploration": bool(exploration),
        "inputDigest": input_digest,
        "recommendedWorkflow": workflow,
        "recommendedMode": suggested_mode,
        "confidence": confidence,
        "reason": reason,
        "advisory": True,
        "overridable": True,
        "overrideAllowed": True,
        "overrideReasonRequired": True,
        "whatWillNotChange": "不会覆盖用户显式选择的工作模式。",
        "projectRoot": str(root),
        "classification": classification,
    }


_PUBLIC_PROFILE_FIELDS = (
    "profile", "signals", "recommendedWorkflow", "recommendedMode", "confidence",
    "reason", "advisory", "overridable", "overrideAllowed", "overrideReasonRequired",
    "whatWillNotChange", "classification",
)


def public_governance_profile(internal: dict[str, Any]) -> dict[str, Any]:
    """Project an internal recommendation into the strict public profile.

    Graph diagnostics, input digests, override details and other calculation
    inputs stay in request_intake.json as an auditable internal snapshot. They
    are not allowed to leak into the public governance profile contract.
    """
    if not isinstance(internal, dict):
        raise GovernanceProfileError("governance recommendation must be an object")
    return {
        key: internal[key]
        for key in _PUBLIC_PROFILE_FIELDS
        if key in internal
    }


def recommend(
    project_root: Path,
    *,
    signals: Iterable[str] = (),
    impact_assessment: dict[str, Any] | None = None,
    classification: str = "",
    changed_scopes: Iterable[str] = (),
    graph_path: Path | None = None,
    changed_refs: Iterable[str] = (),
    constraints_payload: dict[str, Any] | None = None,
    current_profile: str = "",
    exploration: bool = False,
    override_reason: str = "",
) -> dict[str, Any]:
    """Return only the strict public governance profile projection."""
    return public_governance_profile(_recommend_internal(
        project_root,
        signals=signals,
        impact_assessment=impact_assessment,
        classification=classification,
        changed_scopes=changed_scopes,
        graph_path=graph_path,
        changed_refs=changed_refs,
        constraints_payload=constraints_payload,
        current_profile=current_profile,
        exploration=exploration,
        override_reason=override_reason,
    ))


def _decision_guide_next_command(next_command: dict[str, Any] | None, project_root: str) -> dict[str, Any]:
    """Return the strict, human-oriented next command projection used by APG-4."""
    raw = next_command if isinstance(next_command, dict) else {}
    command = str(raw.get("command") or raw.get("recommendedCommand") or "").strip()
    reason = str(raw.get("reason") or "").strip()
    required_agent = str(raw.get("requiredAgent") or "ProjectManagerAgent").strip()
    if not command:
        command = f"python3 scripts/governance_profile.py recommend --project-root {project_root} --json"
    if not reason:
        reason = "根据当前事实查看或确认治理建议。"
    return {
        "command": command,
        "reason": reason,
        "requiredAgent": required_agent,
        "requiresConfirmation": bool(raw.get("requiresConfirmation", False)),
        "readOnly": bool(raw.get("readOnly", True)),
    }


def build_decision_guide(
    governance: dict[str, Any],
    *,
    next_command: dict[str, Any] | None = None,
    current_facts: str = "",
    actual_risk: str = "",
    selected_mode: str = "",
) -> dict[str, Any]:
    """Build the seven-part APG-4 explanation without changing workflow state.

    The guide is a strict public projection. Internal recommendation fields are
    never copied into this communication object. ``governance`` remains
    advisory and never becomes a workflow-mode authority.
    """
    source = governance if isinstance(governance, dict) else {}
    profile = str(source.get("profile") or "UNKNOWN")
    workflow = str(source.get("recommendedWorkflow") or "").strip()
    mode = str(source.get("recommendedMode") or "").strip()
    reason = str(source.get("reason") or "").strip() or "根据当前项目事实给出治理建议。"
    signals = source.get("signals") if isinstance(source.get("signals"), list) else []
    facts = str(current_facts or "").strip() or (
        f"当前项目治理画像为 {profile}，已识别影响信号：{', '.join(map(str, signals)) or '无'}。"
    )
    risk = str(actual_risk or "").strip() or (
        "存在多个高影响面，需保留相应风险门禁。" if profile == "HIGH_RISK"
        else "当前未识别显著高风险信号，但仍须遵守需求级门禁。"
    )
    choice = f"{workflow}（建议工作模式：{mode}）" if workflow and mode else (workflow or mode)
    will_happen = (
        f"按建议使用 {workflow or '当前流程'}，并由对应门禁校验；不会自动替换用户已选择的工作模式。"
    )
    not_change = str(source.get("whatWillNotChange") or "").strip() or "不会覆盖用户显式选择的工作模式，也不会跳过生命周期门禁。"
    guide = {
        "currentFacts": facts,
        "actualRisk": risk,
        "recommendedChoice": choice or "保留当前流程并先查看建议。",
        "why": reason,
        "whatWillHappen": will_happen,
        "whatWillNotChange": not_change,
        "nextCommand": _decision_guide_next_command(next_command, str(source.get("projectRoot") or "<project-root>")),
        "advisory": True,
        "overrideAllowed": True,
        "overrideReasonRequired": True,
    }
    return guide


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    recommend_parser = commands.add_parser("recommend", help="recommend advisory project governance depth")
    agent_context.add_agent_argument(recommend_parser)
    recommend_parser.add_argument("--project-root", required=True)
    recommend_parser.add_argument("--signal", action="append", default=[], help="explicit risk signal; repeatable")
    recommend_parser.add_argument("--classification", default="")
    recommend_parser.add_argument("--impact", action="append", default=[], metavar="FIELD=LEVEL", help="request impact, repeatable")
    recommend_parser.add_argument("--changed-scope", action="append", default=[], help="changed domain/scope, repeatable")
    recommend_parser.add_argument("--graph", default="", help="optional engineering graph JSON")
    recommend_parser.add_argument("--changed-ref", action="append", default=[], help="changed graph ref, repeatable")
    recommend_parser.add_argument("--current-profile", default="")
    recommend_parser.add_argument("--exploration", action="store_true")
    recommend_parser.add_argument("--override-reason", default="")
    recommend_parser.add_argument("--json", action="store_true")
    recommend_parser.set_defaults(func=command_recommend)
    return parser


def command_recommend(args: argparse.Namespace) -> int:
    agent_context.authorize_command(args, "governance_profile", "recommend")
    impacts: dict[str, str] = {}
    for raw in args.impact:
        if "=" not in raw:
            raise GovernanceProfileError(f"impact must use FIELD=LEVEL: {raw}")
        field, level = raw.split("=", 1)
        impacts[field] = level
    internal = _recommend_internal(
        Path(args.project_root), signals=args.signal, impact_assessment=impacts,
        classification=args.classification, changed_scopes=args.changed_scope,
        graph_path=Path(args.graph) if args.graph else None, changed_refs=args.changed_ref,
        current_profile=args.current_profile, exploration=bool(args.exploration),
        override_reason=args.override_reason,
    )
    payload = public_governance_profile(internal)
    if args.json:
        output = dict(payload)
        output["decisionGuide"] = build_decision_guide(internal)
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print(f"GOVERNANCE {payload['profile']} confidence={payload['confidence']} advisory=true")
        print(f"signals={','.join(payload['signals']) or 'none'}")
        print(f"recommendedWorkflow={payload['recommendedWorkflow']} recommendedMode={payload['recommendedMode']}")
        print(payload["reason"])
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return command_recommend(args)
    except (GovernanceProfileError, agent_context.AgentAuthorizationError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
