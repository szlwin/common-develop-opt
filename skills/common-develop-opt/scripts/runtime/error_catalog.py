"""Stable machine-readable error codes with user-facing Chinese messages."""
from __future__ import annotations

from dataclasses import dataclass
import json
import sys
import re
import shlex
from pathlib import Path
from typing import Any, TextIO

_SCRIPT_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT_RE = re.compile(r"^python3 scripts/([A-Za-z_][A-Za-z0-9_]*\.py)(?=\s|$)")

def _bind_command(value: str) -> str:
    return _SCRIPT_RE.sub(lambda m: "python3 " + shlex.quote(str(_SCRIPT_ROOT / m.group(1))), value, count=1)


@dataclass
class SkillError(ValueError):
    code: str
    message: str
    repair_command: str | None = None

    def __init__(self, code: str, message: str, repair_command: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.repair_command = repair_command

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    def to_record(self) -> dict[str, Any]:
        value: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.repair_command:
            value["repairCommand"] = _bind_command(self.repair_command)
        details = getattr(self, "rollback_details", None)
        if isinstance(details, dict) and details:
            value["details"] = details
        return value


def _derived_code(prefix: str, message: str, fallback: str) -> str:
    """Derive a stable coarse code when a legacy call has no explicit code."""
    text = message.lower()
    rules = (
        ("invalid phase", "PHASE_INVALID"),
        ("unknown phase", "PHASE_INVALID"),
        ("cannot advance", "PHASE_TRANSITION_INVALID"),
        ("requires", "PRECONDITION_FAILED"),
        ("must", "VALIDATION_FAILED"),
        ("missing", "REQUIRED_DATA_MISSING"),
        ("stale", "STALE_STATE"),
        ("does not exist", "REFERENCE_NOT_FOUND"),
        ("incomplete", "INCOMPLETE_STATE"),
    )
    suffix = next((suffix for needle, suffix in rules if needle in text), fallback)
    return f"{prefix}_{suffix}"


class WorkModeError(SkillError):
    def __init__(self, message: str, *, code: str = "WORKMODE_ERROR", repair_command: str | None = None) -> None:
        super().__init__(code, message, repair_command)


class LifecycleError(SkillError):
    def __init__(self, message: str, *, code: str | None = None, repair_command: str | None = None) -> None:
        # Keep the original English diagnostic as a searchable detail while
        # giving users a Chinese domain label and callers a stable code.
        localized = message if any("\u4e00" <= char <= "\u9fff" for char in message) else f"生命周期校验失败：{message}"
        super().__init__(code or _derived_code("LIFECYCLE", message, "ERROR"), localized, repair_command)


class ArchiveError(SkillError):
    def __init__(self, message: str, *, code: str | None = None, repair_command: str | None = None) -> None:
        localized = message if any("\u4e00" <= char <= "\u9fff" for char in message) else f"归档校验失败：{message}"
        super().__init__(code or _derived_code("ARCHIVE", message, "ERROR"), localized, repair_command)


class RequestIntakeError(SkillError):
    def __init__(self, message: str, *, code: str | None = None, repair_command: str | None = None) -> None:
        localized = message if any("\u4e00" <= char <= "\u9fff" for char in message) else f"请求受理校验失败：{message}"
        super().__init__(code or _derived_code("INTAKE", message, "ERROR"), localized, repair_command)


def error_record(exc: BaseException, default_code: str = "CLI_ERROR") -> dict[str, Any]:
    """Return one stable error object for text and JSON CLI boundaries."""
    if isinstance(exc, SkillError):
        return exc.to_record()
    value: dict[str, Any] = {"code": default_code, "message": str(exc)}
    details = getattr(exc, "rollback_details", None)
    if isinstance(details, dict) and details:
        value["details"] = details
    return value


def recovery_record(record: dict[str, Any]) -> dict[str, Any]:
    """Shared failure projection, preserving the legacy error object for callers."""
    command = record.get("repairCommand") or ""
    from runtime._wk.command_plan import executable_command
    return {"status": "BLOCKED", "reasonCode": record["code"], "message": record["message"],
            "repairPlan": [{"command": command, "reason": record["message"]}] if executable_command(command) else [],
            "requiresUserDecision": False, "nextReviewer": "ProjectManagerAgent"}


def emit_cli_error(
    exc: BaseException,
    *,
    json_output: bool = False,
    stream: TextIO | None = None,
    default_code: str = "CLI_ERROR",
    blocked: bool = False,
) -> None:
    """Render a domain error without leaking a second, incompatible format."""
    output = stream or sys.stderr
    record = error_record(exc, default_code)
    if json_output:
        payload = {**recovery_record(record), "status": "BLOCKED" if blocked else "FAILED", "error": record}
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=output)
    else:
        print(f"ERROR: {record['code']}: {record['message']}", file=output)


def raise_error(code: str, message: str, repair_command: str | None = None) -> None:
    raise SkillError(code, message, repair_command)


__all__ = ["SkillError", "WorkModeError", "LifecycleError", "ArchiveError", "RequestIntakeError", "error_record", "emit_cli_error", "raise_error"]
