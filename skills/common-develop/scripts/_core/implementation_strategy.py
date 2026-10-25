"""Shared implementation-strategy vocabulary from the semantic contract."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
SEMANTIC_CONTRACT_PATH = ROOT / "assets" / "semantic-contract.json"


def load_contract() -> dict[str, Any]:
    payload = json.loads(SEMANTIC_CONTRACT_PATH.read_text(encoding="utf-8"))
    contract = payload.get("canonicalTerms", {}).get("implementationStrategyDecision")
    if not isinstance(contract, dict):
        raise ValueError("semantic contract is missing implementationStrategyDecision")
    return contract


CONTRACT = load_contract()
CONTRACT_VERSION = int(CONTRACT["contractVersion"])
DECISION_ORDER = tuple(str(value) for value in CONTRACT["decisionOrder"])
SHARED_LOGIC_DISPOSITIONS = frozenset(str(value) for value in CONTRACT["sharedLogicDispositions"])
DECISION_ID_RE = re.compile(r"\bIMPL-DEC-[A-Z0-9_-]+\b")
BLUEPRINT_ID_RE = re.compile(r"\bBP-[A-Z0-9_-]+\b")


def extract_id(value: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(value or "")
    return match.group(0) if match else ""
