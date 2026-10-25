"""Path context shared by static Skill checks."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CheckContext:
    root: Path
    assets: Path
    references: Path
    fixtures: Path


ROOT = Path(__file__).resolve().parent.parent.parent
REFERENCES = ROOT / "references"
ASSETS = ROOT / "assets"
STRUCTURED = ASSETS / "structured-docs"
TEST_FIXTURES = ROOT / "tests" / "fixtures"
STRUCTURED_FIXTURES = TEST_FIXTURES / "structured-docs"
AGENT_PATTERN = re.compile(r"\b[A-Z][A-Za-z]+Agent\b")
CHECK_CONTEXT = CheckContext(ROOT, ASSETS, REFERENCES, TEST_FIXTURES)
