"""Only concrete existing atomic commands belong in an executable action plan."""
from __future__ import annotations
import re
from typing import Any
from .command_paths import bind_command


def executable_command(command: Any) -> bool:
    return isinstance(command, str) and bool(command.strip()) and not re.search(r"[<{][A-Z][A-Z0-9_ -]*[>}]", command)


def required_inputs(command: Any) -> list[str]:
    if not isinstance(command, str):
        return []
    return list(dict.fromkeys(re.findall(r"[<{]([A-Z][A-Z0-9_ -]*)[>}]", command)))


def build_action_plan(decision: dict[str, Any], *, task_dir: str = "") -> list[dict[str, Any]]:
    raw = decision.get("nextCommand") or decision.get("recommendedCommand") or ""
    details = raw if isinstance(raw, dict) else {}
    command = details.get("command") if details else raw
    if not executable_command(command):
        return []
    return [{"action": "execute_next", "command": bind_command(command),
             "requiredAgent": details.get("requiredAgent") or decision.get("requiredAgent") or "",
             "reason": str(details.get("reason") or decision.get("reason") or decision.get("nextReason") or "执行当前合法原子命令"),
             "atomic": True,
             "executionContext": details.get("executionContext") or "OWNER_SESSION"}]
