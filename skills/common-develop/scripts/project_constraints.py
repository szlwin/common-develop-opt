#!/usr/bin/env python3
"""Validate an optional project Hard/Soft/Assumption constraint declaration."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import agent_context
import contract_normalization
import project_environment
import simple_yaml
import reference_resolution

try:
    import jsonschema
except ImportError:  # pragma: no cover - canonical environments include it
    jsonschema = None

ROOT = Path(__file__).resolve().parent.parent
CONSTRAINT_SCHEMA_PATH = ROOT / "assets" / "project-constraints.schema.json"
RUNTIME_CONTRACT_PATH = ROOT / "assets" / "runtime-contract.json"
BLOCK_RE = re.compile(
    r"```(?P<format>json|yaml)\s+common-develop-constraints\s*\n(?P<body>.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)
ALLOWED_TYPES = {"HARD", "SOFT", "ASSUMPTION"}
ALLOWED_STATUSES = {"UNVALIDATED", "VALIDATED", "INVALIDATED", "WAIVED"}


class ProjectConstraintsError(ValueError):
    """Invalid project constraint declaration."""


def _candidate_paths(project_root: Path, version: str = "") -> list[Path]:
    root = Path(project_root).resolve()
    roots = [root] if root.name == "project_doc" else [root, root / "project_doc"]
    result: list[Path] = []
    for base in roots:
        result.extend([base / "project_env.md", base / "harness_env.md"])
        if version:
            result.extend([
                base / "version" / version / "project_env.md",
                base / "version" / version / "harness_env.md",
            ])
    seen: set[Path] = set()
    return [path for path in result if path.is_file() and not (path in seen or seen.add(path))]


def _load_blocks(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    blocks = list(BLOCK_RE.finditer(text))
    values: list[dict[str, Any]] = []
    for match in blocks:
        body = match.group("body")
        try:
            value = json.loads(body) if match.group("format").lower() == "json" else simple_yaml.loads(body)
        except (json.JSONDecodeError, simple_yaml.YamlError) as exc:
            raise ProjectConstraintsError(f"{path}: invalid constraint block: {exc}") from exc
        if not isinstance(value, dict):
            raise ProjectConstraintsError(f"{path}: constraint block must be a mapping")
        values.append(value)
    return values


def _contract_strings(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, str):
        found.add(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            found.add(str(key))
            found.update(_contract_strings(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_contract_strings(item))
    return found


_REFERENCE_ID_RE = reference_resolution._REFERENCE_ID_RE
_REFERENCE_SUFFIXES = reference_resolution._PATH_SUFFIXES


def _split_reference(reference: str) -> tuple[str, str]:
    return reference_resolution._split_reference(reference)


def _safe_reference_path(raw_path: str, project_root: Path, source_path: Path | None) -> Path | None:
    """Resolve references relative to the declaration document, fail-closed."""
    return reference_resolution.resolve_path(raw_path, project_root, source_path)


def _reference_files(project_root: Path, version: str = "") -> list[Path]:
    return reference_resolution._iter_reference_files(project_root, version)


def _resolve_reference(
    reference: str,
    project_root: Path,
    source_path: Path | None = None,
    *,
    version: str = "",
    label: str = "reference",
) -> tuple[bool, str, dict[str, str]]:
    """Resolve a canonical path, Markdown fragment, JSON Pointer, or stable ID."""
    try:
        resolved = reference_resolution.resolve_reference(
            reference, project_root, source_path, version=version, label=label
        )
    except reference_resolution.ReferenceResolutionError as exc:
        return False, str(exc), {}
    return True, "", resolved.as_details(Path(project_root).resolve())


def _resolve_source(source: str, project_root: Path, source_path: Path | None) -> Path:
    """Compatibility helper retained for callers that only need the source path."""
    path_part, _ = _split_reference(source)
    return _safe_reference_path(path_part, project_root, source_path) or (project_root / path_part).resolve()

def _validate_adapter(adapter: str, project_root: Path, version: str) -> tuple[bool, str]:
    """Require project-specific adapters to be registered capabilities."""
    raw = str(adapter or "").strip()
    if not raw:
        return False, "adapter is required"
    path_part, fragment = _split_reference(raw)
    capability_name = fragment if path_part in {"project_env", "harness_env"} and fragment else raw
    try:
        projection = project_environment.project_capabilities(project_root, version=version)
    except (OSError, ValueError) as exc:
        return False, f"adapter cannot be loaded: {exc}"
    definition = (projection.get("capabilities") or {}).get(capability_name)
    if not isinstance(definition, dict) or definition.get("available") is not True:
        return False, f"adapter is not registered by project capabilities: {raw}"
    return True, ""


def _validate_evidence_reference(
    reference: str, project_root: Path, version: str, source_path: Path | None = None
) -> tuple[bool, str, dict[str, str]]:
    return _resolve_reference(reference, project_root, source_path, version=version, label="evidenceRef")


def _validate_task_reference(
    reference: str, project_root: Path, version: str, source_path: Path | None = None
) -> tuple[bool, str, dict[str, str]]:
    return _resolve_reference(reference, project_root, source_path, version=version, label="verificationTask")


def _schema_errors(value: dict[str, Any], source: Path) -> list[str]:
    if jsonschema is None:
        raise ProjectConstraintsError("jsonschema is required to validate constraint declarations")
    try:
        schema = json.loads(CONSTRAINT_SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectConstraintsError(f"cannot load constraint schema: {exc}") from exc
    errors: list[str] = []
    validator = jsonschema.Draft202012Validator(schema)
    for error in sorted(validator.iter_errors(value), key=lambda item: list(item.path)):
        location = ".".join(str(item) for item in error.path) or "$"
        errors.append(f"{source}: declaration schema {location}: {error.message}")
    return errors


def _find_declaration(project_root: Path, version: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    declarations: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    schema_errors: list[str] = []
    for path in _candidate_paths(project_root, version):
        blocks = _load_blocks(path)
        if blocks:
            sources.append({"path": str(path), "blocks": len(blocks)})
        for block in blocks:
            schema_errors.extend(_schema_errors(block, path))
            raw = block.get("constraints", block)
            if not isinstance(raw, list):
                raise ProjectConstraintsError(f"{path}: constraints must be a list")
            for item in raw:
                if not isinstance(item, dict):
                    raise ProjectConstraintsError(f"{path}: each constraint must be a mapping")
                declarations.append({"_sourcePath": str(path), **item})
    return declarations, sources, schema_errors


def validate_project_constraints(
    project_root: Path,
    version: str = "",
    runtime_contract_path: Path | None = None,
) -> dict[str, Any]:
    """Validate an optional project constraint declaration without changing state.

    ``status`` is retained as the legacy validator result for callers that only
    need pass/partial/fail.  ``completionStatus`` is the governance projection:
    it is derived from the declaration and can never be supplied by the input
    document to claim completion.
    """
    root = Path(project_root).resolve()
    checks: list[dict[str, Any]] = []
    errors: list[str] = []
    warnings: list[str] = []
    try:
        declarations, sources, schema_errors = _find_declaration(root, str(version))
    except (OSError, ProjectConstraintsError) as exc:
        return {
            "status": "FAILED",
            "completionStatus": "INVALID",
            "constraints": [],
            "checks": [],
            "errors": [str(exc)],
            "warnings": [],
            "source": {"projectRoot": str(root), "files": []},
        }
    if not declarations and schema_errors:
        return {
            "status": "FAILED",
            "completionStatus": "INVALID",
            "constraints": [],
            "checks": [{
                "id": "declaration-schema", "status": "FAILED",
                "message": "common-develop-constraints block does not match the strict schema",
            }],
            "errors": schema_errors,
            "warnings": [],
            "source": {"projectRoot": str(root), "version": str(version), "files": sources},
        }
    if not declarations:
        checks.append({
            "id": "declaration",
            "status": "NOT_DECLARED",
            "message": "未声明 common-develop-constraints block",
        })
        return {
            "status": "NOT_DECLARED",
            "completionStatus": "NOT_DECLARED",
            "constraints": [],
            "checks": checks,
            "errors": [],
            "warnings": ["项目尚未声明 Hard/Soft/Assumption 约束"],
            "source": {"projectRoot": str(root), "version": str(version), "files": sources},
        }

    errors.extend(schema_errors)

    contract_path = Path(runtime_contract_path or RUNTIME_CONTRACT_PATH)
    try:
        contract = contract_normalization.load_runtime_contract(contract_path)
        contract_values = _contract_strings(contract)
    except (OSError, ValueError, contract_normalization.ContractNormalizationError) as exc:
        contract_values = set()
        warnings.append(f"无法读取 Runtime Contract，无法完整校验 gate：{exc}")

    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    completion_statuses: list[str] = []
    partial = False
    for index, raw in enumerate(declarations, start=1):
        # completionStatus is output-only.  Drop any user-supplied value before
        # deriving the authoritative result below.
        item = {
            key: value
            for key, value in raw.items()
            if key not in {"_sourcePath", "completionStatus"}
        }
        source_path = Path(str(raw.get("_sourcePath"))) if raw.get("_sourcePath") else None
        cid = str(item.get("id") or "").strip()
        ctype = str(item.get("type") or "").upper().strip()
        prefix = f"constraint[{index}]"
        # The declaration block is atomic. Any JSON Schema violation (unknown
        # field, wrong type, missing required field, or invalid enum) prevents
        # every item in that block set from claiming completion.
        item_invalid = bool(schema_errors)
        item_blocked = False
        item_partial = False

        if not cid:
            errors.append(f"{prefix}: id is required")
            item_invalid = True
        elif cid in seen:
            errors.append(f"duplicate constraint id: {cid}")
            item_invalid = True
        else:
            seen.add(cid)
        if ctype not in ALLOWED_TYPES:
            errors.append(f"{prefix} {cid or '<missing-id>'}: type must be HARD, SOFT, or ASSUMPTION")
            item_invalid = True
        if not str(item.get("statement") or "").strip():
            errors.append(f"{prefix} {cid or '<missing-id>'}: statement is required")
            item_invalid = True
        if not str(item.get("owner") or "").strip():
            errors.append(f"{prefix} {cid or '<missing-id>'}: owner is required")
            item_invalid = True

        source = str(item.get("source") or "").strip()
        if not source:
            errors.append(f"{cid or prefix}: source is required")
            item_invalid = True
        else:
            valid_source, source_error, _ = _resolve_reference(
                source, root, source_path, version=str(version), label="source"
            )
            if not valid_source:
                errors.append(f"{cid or prefix}: {source_error}")
                item_blocked = True

        gate = str(item.get("gate") or "").strip()
        adapter = str(item.get("adapter") or "").strip()
        status = str(item.get("status") or "UNVALIDATED").upper().strip()
        item["type"] = ctype
        item["status"] = status
        if status not in ALLOWED_STATUSES:
            errors.append(f"{cid or prefix}: invalid status {status}")
            item_invalid = True

        if ctype == "HARD":
            if not gate:
                errors.append(f"{cid or prefix}: HARD constraint requires gate")
                item_invalid = True
            elif gate not in contract_values and not adapter:
                errors.append(
                    f"{cid or prefix}: gate is not declared by Runtime Contract or project adapter: {gate}"
                )
                item_blocked = True
            if adapter:
                adapter_ok, adapter_error = _validate_adapter(adapter, root, str(version))
                if not adapter_ok:
                    errors.append(f"{cid or prefix}: {adapter_error}")
                    item_blocked = True
        elif ctype == "SOFT":
            if not str(item.get("decision") or "").strip() and not str(item.get("decisionRef") or "").strip():
                errors.append(f"{cid or prefix}: SOFT constraint requires decision or decisionRef")
                item_invalid = True
            decision_ref = str(item.get("decisionRef") or "").strip()
            if decision_ref:
                valid_ref, ref_error, _ = _resolve_reference(
                    decision_ref, root, source_path, version=str(version), label="decisionRef"
                )
                if not valid_ref:
                    errors.append(f"{cid or prefix}: {ref_error}")
                    item_blocked = True
        elif ctype == "ASSUMPTION":
            if not str(item.get("validation") or "").strip():
                errors.append(f"{cid or prefix}: ASSUMPTION constraint requires validation")
                item_invalid = True
            validation_ref = str(item.get("validationRef") or "").strip()
            verification_task = str(item.get("verificationTask") or "").strip()
            evidence_refs = item.get("evidenceRefs")
            if evidence_refs is None:
                evidence_refs = []
            if not isinstance(evidence_refs, list) or any(not str(ref).strip() for ref in evidence_refs):
                errors.append(f"{cid or prefix}: evidenceRefs must be a non-empty-string list")
                item_invalid = True
                evidence_refs = []
            if not validation_ref and not verification_task and not evidence_refs:
                errors.append(
                    f"{cid or prefix}: ASSUMPTION requires validationRef, verificationTask, or evidenceRefs"
                )
                item_blocked = True
            if validation_ref:
                valid_ref, ref_error, _ = _resolve_reference(
                    validation_ref, root, source_path, version=str(version), label="validationRef"
                )
                if not valid_ref:
                    errors.append(f"{cid or prefix}: {ref_error}")
                    item_blocked = True
            if verification_task:
                valid_ref, ref_error, _ = _validate_task_reference(verification_task, root, str(version), source_path)
                if not valid_ref:
                    errors.append(f"{cid or prefix}: {ref_error}")
                    item_blocked = True
            for evidence_ref in evidence_refs:
                valid_ref, ref_error, _ = _validate_evidence_reference(str(evidence_ref), root, str(version), source_path)
                if not valid_ref:
                    errors.append(f"{cid or prefix}: {ref_error}")
                    item_blocked = True
            if status in {"UNVALIDATED", "INVALIDATED"}:
                item_partial = True
                partial = True
                warnings.append(f"{cid or prefix}: assumption is {status.lower()}")
        if ctype == "HARD" and status == "INVALIDATED":
            item_partial = True
            partial = True
            warnings.append(f"{cid or prefix}: hard constraint is invalidated")

        # Structural declaration errors take precedence over unresolved external
        # references: an invalid record cannot be made merely BLOCKED by adding
        # a missing source/path diagnostic.
        completion = (
            "INVALID" if item_invalid
            else "BLOCKED" if item_blocked
            else "PARTIAL" if item_partial
            else "COMPLETE"
        )
        item["completionStatus"] = completion
        completion_statuses.append(completion)
        normalized.append(item)

    checks.extend([
        {"id": "declaration", "status": "PASSED", "message": f"发现 {len(normalized)} 条项目约束"},
        {
            "id": "ids",
            "status": "FAILED" if any("constraint id" in e or "id is required" in e for e in errors) else "PASSED",
            "message": "约束 ID 唯一且存在",
        },
        {
            "id": "type-requirements",
            "status": "FAILED" if errors else ("WARNING" if partial else "PASSED"),
            "message": "HARD/SOFT/ASSUMPTION 字段要求已检查",
        },
        {
            "id": "sources",
            "status": "FAILED" if any(
                marker in e for e in errors for marker in (
                    "source is required", "source does not exist",
                    "source fragment", "source must be a project-local",
                    "source stable ID was not found",
                )
            ) else "PASSED",
            "message": "source 引用可解析",
        },
        {
            "id": "gates",
            "status": "FAILED" if any("gate is not declared" in e or "requires gate" in e or "adapter is not registered" in e for e in errors) else "PASSED",
            "message": "gate 可由 Runtime Contract 或项目 adapter 解释",
        },
        {
            "id": "references",
            "status": "FAILED" if any(
                marker in e for e in errors for marker in (
                    "source is required", "source does not exist", "source fragment",
                    "decisionRef", "validationRef", "verificationTask", "evidenceRef",
                )
            ) else "PASSED",
            "message": "source、决策、验证任务和 Evidence 引用可解析",
        },
    ])
    # Preserve the strongest diagnosis when a record has both malformed
    # structure and unresolved references.  INVALID is actionable schema/data
    # corruption; BLOCKED is reserved for otherwise valid declarations whose
    # external facts cannot currently be resolved.
    if any(value == "INVALID" for value in completion_statuses):
        completion_status = "INVALID"
    elif any(value == "BLOCKED" for value in completion_statuses):
        completion_status = "BLOCKED"
    elif any(value == "PARTIAL" for value in completion_statuses):
        completion_status = "PARTIAL"
    else:
        completion_status = "COMPLETE"
    checks.append({
        "id": "completion",
        "status": "PASSED" if completion_status == "COMPLETE" else (
            "WARNING" if completion_status == "PARTIAL" else "FAILED"
        ),
        "message": f"约束完成状态为 {completion_status}",
    })

    status = "FAILED" if errors else ("PARTIAL" if partial else "PASSED")
    return {
        "status": status,
        "completionStatus": completion_status,
        "constraints": normalized,
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
        "source": {"projectRoot": str(root), "version": str(version), "files": sources},
    }


def render(payload: dict[str, Any]) -> str:
    lines = [f"{payload['status']} project constraints"]
    for check in payload.get("checks", []):
        lines.append(f"[{check['status']}] {check['id']}: {check.get('message', '')}")
    for message in payload.get("errors", []):
        lines.append(f"ERROR: {message}")
    for message in payload.get("warnings", []):
        lines.append(f"WARNING: {message}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate_parser = commands.add_parser(
        "validate", help="validate project Hard/Soft/Assumption constraints"
    )
    status_parser = commands.add_parser(
        "status", help="show project constraint status"
    )
    for command in (validate_parser, status_parser):
        agent_context.add_agent_argument(command)
        command.add_argument("--project-root", required=True)
        command.add_argument("--version", default="")
        command.add_argument("--json", action="store_true")
        command.set_defaults(func=command_validate)
    return parser


def command_validate(args: argparse.Namespace) -> int:
    agent_context.authorize_command(args, "project_constraints", args.command, declared=True)
    payload = validate_project_constraints(Path(args.project_root), str(args.version))
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(render(payload))
    return 0 if payload["status"] in {"PASSED", "PARTIAL", "NOT_DECLARED"} else 1


def main(argv: Iterable[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return int(args.func(args))
    except (ProjectConstraintsError, agent_context.AgentAuthorizationError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
