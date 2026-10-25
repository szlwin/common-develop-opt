"""R3 single-command write pilot for ReviewSession plans.

The pilot writes only an explicitly supplied output path and emits a receipt;
it never mutates task lifecycle, Evidence, or ReviewStore state.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_COMPAT = Path(__file__).resolve().parents[1] / "compat"
if str(_COMPAT) not in sys.path:
    sys.path.insert(0, str(_COMPAT))
from document_core.common_utils import atomic_write_text
from .review_session import plan_session, validate_session


def write_plan(output: Path, *, phase: str, revision: str, risks: list[str] | None = None,
               mode: str = "standard", input_digest: str = "", planner_agent: str = "ProjectManagerAgent") -> dict[str, Any]:
    payload = plan_session(phase=phase, revision=revision, explicit_risks=risks or [], risk_report=None, impact_plan=None,
                           mode=mode, input_digest=input_digest, planner_agent=planner_agent)
    errors = validate_session(payload)
    if errors:
        raise ValueError("invalid ReviewSession plan: " + "; ".join(errors))
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(Path(output), text)
    return {"status": "PILOT_COMMITTED", "path": str(Path(output).resolve()),
            "sha256": hashlib.sha256(text.encode()).hexdigest(), "kind": "REVIEW_SESSION_PLAN"}
