"""Context object shared by the task validation domain validators.

The validator used to pass a large tuple of loosely related values between
line-number-based sections.  The context keeps those values named and scoped
while preserving the validator's public return contract.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class TaskValidationContext:
    """Named state exchanged by task validation domains."""

    task_dir: Path
    errors: list[str]
    warnings: list[str]
    history: dict[str, Any]
    state: dict[str, Any]
    issues: list[dict[str, Any]]
    prevalidated: dict[str, Any] = field(default_factory=dict)
    values: dict[str, Any] = field(default_factory=dict)

    def bind(self, **values: Any) -> None:
        self.values.update(values)

    def value(self, name: str, default: Any = None) -> Any:
        if name == "errors":
            return self.errors
        if name == "warnings":
            return self.warnings
        if name == "history":
            return self.history
        if name == "state":
            return self.state
        if name == "task_dir":
            return self.task_dir
        if name == "issues":
            return self.issues
        if name == "prevalidated":
            return self.prevalidated
        return self.values.get(name, default)
