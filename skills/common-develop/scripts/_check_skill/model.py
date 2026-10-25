"""Model for the internal runtime."""
from __future__ import annotations
import sys
from typing import Any

sys.dont_write_bytecode = True

from _check_skill.context import *
from _check_skill.module_loader import *
from _check_skill.routing import *
from _check_skill.manifest import *

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None
class CheckResult:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.checks: list[str] = []
        self.details: dict[str, Any] = {}

    def ok(self, message: str) -> None:
        self.checks.append(message)
