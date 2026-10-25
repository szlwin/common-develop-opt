"""Mutable data flow shared by multi-stage Skill checks."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from checks._check_skill.model import CheckResult


@dataclass
class CheckFlowContext:
    """Carry validated contract data between check sections without tuple wiring."""

    contract: dict[str, Any]
    result: CheckResult
    values: dict[str, Any] = field(default_factory=dict)

    def get(self, name: str, default: Any = None) -> Any:
        return self.values.get(name, default)

    def update(self, **values: Any) -> None:
        self.values.update(values)
