"""Static checks for APG-4 non-expert communication and advisory guides."""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from _check_skill.context import ASSETS, REFERENCES, ROOT
from _check_skill.model import CheckResult


def _read(path: Path, result: CheckResult) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        result.errors.append(f"APG-4 missing communication artifact {path.relative_to(ROOT)}: {exc}")
        return ""


def _schema(path: Path, result: CheckResult) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result.errors.append(f"APG-4 cannot load schema {path.relative_to(ROOT)}: {exc}")
        return {}
    return value if isinstance(value, dict) else {}


def _find_function(path: Path, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        return None
    return next((node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name), None)


def check_apg4_contract_and_communication(result: CheckResult) -> None:
    """Verify the fixed seven-part guide and its advisory implementation contract."""
    docs = _read(REFERENCES / "request-intake.md", result)
    required_labels = ("当前事实", "实际风险", "建议选择", "为什么", "会发生什么", "不会改变什么", "下一步命令")
    missing = [label for label in required_labels if label not in docs]
    if missing:
        result.errors.append(f"APG-4 request-intake communication protocol missing: {missing}")
    if "不会替用户改变实际工作模式" not in docs and "不会自动改变" not in docs:
        result.errors.append("APG-4 communication protocol must state that recommendations do not change workflow mode")

    governance = _schema(ASSETS / "project-governance.schema.json", result)
    props = governance.get("properties") if isinstance(governance.get("properties"), dict) else {}
    for key in (*required_labels, "overrideReasonRequired"):
        # Chinese labels map to their contract camelCase names.
        mapped = {
            "当前事实": "currentFacts", "实际风险": "actualRisk", "建议选择": "recommendedChoice",
            "为什么": "why", "会发生什么": "whatWillHappen", "不会改变什么": "whatWillNotChange",
            "下一步命令": "nextCommand",
        }.get(key, key)
        if mapped not in props:
            result.errors.append(f"APG-4 governance schema missing property: {mapped}")
    for key in ("advisory", "overrideAllowed", "overrideReasonRequired"):
        if props.get(key, {}).get("const") is not True:
            result.errors.append(f"APG-4 governance schema must force {key}=true")
    next_schema = props.get("nextCommand") or {}
    # A direct nextCommand ref is accepted; the named definition is checked below.
    if "$ref" not in next_schema and next_schema.get("type") != "object":
        result.errors.append("APG-4 governance schema nextCommand must be an object")
    next_def = (governance.get("$defs") or {}).get("nextCommand") or {}
    next_props = next_def.get("properties") or {}
    next_required = set(next_def.get("required") or [])
    for key in ("command", "reason", "requiredAgent"):
        if key not in next_required or next_props.get(key, {}).get("type") != "string" or next_props.get(key, {}).get("minLength") != 1:
            result.errors.append(f"APG-4 nextCommand requires non-empty string {key}")
    for key in ("requiresConfirmation", "readOnly"):
        if key not in next_required or next_props.get(key, {}).get("type") != "boolean":
            result.errors.append(f"APG-4 nextCommand requires boolean {key}")

    function = _find_function(ROOT / "scripts" / "governance_profile.py", "build_decision_guide")
    if function is None:
        result.errors.append("APG-4 governance_profile.py must define build_decision_guide")
    else:
        try:
            source = (ROOT / "scripts" / "governance_profile.py").read_text(encoding="utf-8")
            segment = ast.get_source_segment(source, function) or ""
            for token in ("advisory", "overrideAllowed", "overrideReasonRequired", "nextCommand", "whatWillNotChange"):
                if token not in segment:
                    result.errors.append(f"APG-4 decision guide builder missing {token}")
        except (OSError, SyntaxError):
            result.errors.append("APG-4 decision guide builder could not be inspected")

    if not result.errors:
        result.ok("APG-4 advisory decision guide and non-expert communication contract are consistent")
