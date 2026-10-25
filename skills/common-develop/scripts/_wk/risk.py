"""Risk depth is derived from structured facts and cannot waive lifecycle gates."""
from __future__ import annotations
from typing import Any
import runtime_contract


def observation_error(payload: Any) -> str:
    """Validate consumed fields at every depth before an orchestration mutation."""
    if not isinstance(payload, dict):
        return "risk facts must be a JSON object"
    policy = runtime_contract.load_contract()["aiOrchestration"]["riskPolicy"]
    strings = {"revision", "status", "severity", "priority", "explicitMode", "executionMode", "workMode"}
    lists = {"changedModules", "modules", "changedScopes", "riskTriggers", "risk_triggers",
             "escalationTriggers", "openCriticalIssueIds"}
    pending = [(payload, "$", 0)]
    while pending:
        value, path, depth = pending.pop()
        if depth > 64:
            return f"{path}: risk facts nesting exceeds 64 levels"
        if isinstance(value, list):
            pending.extend((child, f"{path}[{i}]", depth + 1) for i, child in enumerate(value))
        elif isinstance(value, dict):
            for key, child in value.items():
                field = f"{path}.{key}"
                if key in strings and not isinstance(child, str):
                    return f"{field}: expected a string"
                if key in {"riskLevel", "risk_level"} and (
                        not isinstance(child, str) or child.upper() not in policy["profiles"]):
                    return f"{field}: expected LOW, MEDIUM or HIGH"
                if key in lists and (not isinstance(child, list) or
                                     any(not isinstance(item, str) or not item.strip() for item in child)):
                    return f"{field}: expected an array of non-empty strings"
                canonical = policy["signalAliases"].get(key, key)
                if canonical in policy["escalationTriggers"] and not (
                        isinstance(child, bool) or isinstance(child, str) and
                        child.upper() in {"NONE", "HIGH", "MEDIUM", "LOW", "YES", "CHANGED", "PRESENT"}):
                    return f"{field}: expected a boolean or a recognized risk signal"
                pending.append((child, field, depth + 1))
    return ""


def assess(payload: dict[str, Any]) -> dict[str, Any]:
    contract = runtime_contract.load_contract()
    policy = contract["aiOrchestration"]["riskPolicy"]
    levels = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    declared: list[str] = []
    hits: set[str] = set()
    modules: set[str] = set()
    negatives: set[str] = set()
    explicit_standard = False
    malformed = bool(observation_error(payload))

    pending = [payload]
    while pending:
        value = pending.pop()
        if isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, dict):
            for key, child in value.items():
                if key in {"riskLevel", "risk_level"} and isinstance(child, str) and child.upper() in levels:
                    declared.append(child.upper())
                if key in {"explicitMode", "executionMode", "workMode"} and isinstance(child, str) and child.upper() == "STANDARD":
                    explicit_standard = True
                status = value.get("status")
                closed = isinstance(status, str) and status.upper() in {"CLOSED", "RESOLVED", "WAIVED"}
                if key in {"severity", "priority"} and isinstance(child, str) and child.upper() in {"P0", "P1"} and not closed:
                    hits.add(child.upper())
                if key == "openCriticalIssueIds" and child:
                    hits.add("P0/P1")
                if key in {"changedModules", "modules"} and isinstance(child, list):
                    modules.update(str(x) for x in child if isinstance(x, str))
                if key in {"changedScopes", "riskTriggers", "risk_triggers", "escalationTriggers"} and isinstance(child, list):
                    hits.update(x for x in child if isinstance(x, str) and
                                (x in policy["escalationTriggers"] or x in contract["riskReviewerCatalog"]))
                canonical = policy["signalAliases"].get(key, key)
                if canonical in policy["escalationTriggers"]:
                    if child is False or child == "NONE":
                        negatives.add(canonical)
                    elif child is True or (isinstance(child, str) and child.upper() in {"HIGH", "MEDIUM", "LOW", "YES", "CHANGED", "PRESENT"}):
                        hits.add(canonical)
                # Do not turn explanatory text or cached recommendations into facts.
                if key not in {"message", "reason", "description", "repairPlan", "actionPlan", "stopConditions"}:
                    pending.append(child)
    if len(modules) > 1:
        hits.add("cross_module")
    proven_low = not malformed and len(modules) == 1 and set(policy["lowRequiredAbsentSignals"]) <= negatives
    level = "HIGH" if hits else (max(declared, key=levels.get) if declared else ("LOW" if proven_low else "MEDIUM"))
    if level == "LOW" and not proven_low:
        level = "MEDIUM"
    profile = policy["profiles"][level]
    if explicit_standard and profile == "FAST":
        profile = "STANDARD"
    # This is a depth recommendation, never a second skip/stage policy.
    return {"riskLevel": level, "governanceProfile": profile,
            "requiredStages": [p["id"] for p in contract["canonicalPhases"]],
            "escalationTriggers": sorted(hits), "riskAdvisoryOnly": True,
            "validationDepth": "FAST" if profile == "FAST" else "FULL"}
