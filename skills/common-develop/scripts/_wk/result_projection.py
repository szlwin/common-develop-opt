"""Normalize lifecycle diagnostics without overwriting the canonical decision."""
from __future__ import annotations
from typing import Any
from .command_plan import executable_command


def classification(value: dict[str, Any]) -> str:
    nested = value.get("lifecycleDecision") or value.get("decision")
    if isinstance(nested, dict):
        return str(nested.get("classification") or nested.get("status") or "")
    return str(value.get("classification") or nested or value.get("status") or "")


def project(decision: dict[str, Any], *, action: str) -> dict[str, Any]:
    result = dict(decision)
    inner = decision.get("lifecycleDecision") or decision.get("decision")
    inner = inner if isinstance(inner, dict) else decision
    next_command = decision.get("nextCommand") if isinstance(decision.get("nextCommand"), dict) else {}
    result["action"] = action
    for key in ("currentPhase", "requiredAgent", "reasonCodes", "missing", "blockers"):
        if key not in result and key in inner:
            result[key] = inner[key]
    result["actionPlan"] = result.get("actionPlan") or []
    result["blockingReasons"] = result.get("blockingReasons") or result.get("blockers") or []
    result["requiredInputs"] = result.get("requiredInputs") or []
    result.setdefault("stopConditions", ["P0/P1 risk", "cross-module impact", "requiresUserDecision=true"])
    result["executionContext"] = next_command.get("executionContext") or result.get("executionContext") or "OWNER_SESSION"
    result["requiresUserDecision"] = bool(result.get("requiresUserDecision") or result.get("requiresConfirmation") or next_command.get("requiresConfirmation"))
    blocked = (classification(decision) in {"BLOCKED", "INVALID", "FAILED"}
               or decision.get("status") in {"BLOCKED", "INVALID", "FAILED"} or bool(decision.get("errors")))
    result.setdefault("status", "BLOCKED" if blocked else "READY")
    if blocked:
        result["status"] = "BLOCKED"
        codes = [result["reasonCode"]] if result.get("reasonCode") else result.get("reasonCodes") or ["LIFECYCLE_BLOCKED"]
        result["reasonCode"] = str(codes[0])
        result["message"] = str(result.get("message") or result.get("reason") or "当前阶段尚有条件未满足，请按修复计划处理。")
        command = next_command.get("command") or result.get("recommendedCommand") or ""
        result["repairPlan"] = result.get("repairPlan") or ([{"command": command, "reason": result["message"]}] if executable_command(command) else [])
        result["nextReviewer"] = result.get("requiredAgent") or result.get("nextAgent") or "ProjectManagerAgent"
    if result["requiresUserDecision"]:
        # Keep the reducer explanation available even when a user decision is requested.
        result["lifecycleDecision"] = inner
        supplied = result.get("decision")
        if not isinstance(supplied, dict) or not supplied.get("question"):
            result["decision"] = {"type": "LIFECYCLE_CONFIRMATION", "question": "是否确认当前计划要求的范围或生命周期变更？", "options": ["确认", "暂停"]}
    return result
