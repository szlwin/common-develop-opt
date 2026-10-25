"""Bind generated runtime commands to the currently installed package."""
from __future__ import annotations

import re
import shlex
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parents[1]
_PREFIX = re.compile(r"^python3 scripts/([A-Za-z_][A-Za-z0-9_]*\.py)(?=\s|$)")


def bind_command(command: str) -> str:
    # Only rewrite our generated CLI prefix, never arbitrary project commands.
    return _PREFIX.sub(lambda match: "python3 " + shlex.quote(str(SCRIPTS / match[1])), command, count=1)


def bind_commands(value: Any, *, instruction: bool = False) -> Any:
    if isinstance(value, list):
        return [bind_commands(item, instruction=instruction) for item in value]
    if isinstance(value, dict):
        return {key: bind_command(item) if isinstance(item, str) and
                (key.endswith("Command") or key == "command" and instruction) else
                bind_commands(item, instruction=key in {"actionPlan", "repairPlan", "nextCommand", "recommendedCommand",
                                                       "actions", "steps", "completedSteps", "failedStep"})
                for key, item in value.items()}
    return value
