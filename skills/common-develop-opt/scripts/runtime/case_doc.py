#!/usr/bin/env python3
"""Create and validate one requirement-scoped test_case.md document."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from document_core import agent_context
from runtime import artifact_relations
from document_core import common_utils
from document_core import page_relations
import document_portal
from resource_paths import asset_path
from runtime._core.document_utils import table_after_marker


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = asset_path('assets/documents/structured-docs/test_case.template.md')
CASE_MARKER_RE = re.compile(r"<!--\s*TEST-CASE:\s*([^\s]+)\s*-->")
CASE_ID_RE = re.compile(r"^(?:CASE|TC)-[A-Z0-9_-]+$")
PLACEHOLDER_RE = re.compile(r"\{[A-Z][A-Z0-9_-]*\}")
VAGUE_RE = re.compile(r"^(?:-|无|正确|正常|合法数据|预期结果|待补充|待确认|TODO|TBD)$", re.I)
REQUIRED_HEADINGS = (
    "## 1. 测试范围与环境",
    "## 2. Case 索引",
    "## 4. 完成门禁",
)
TABLE_CONTRACTS: dict[str, tuple[str, ...]] = {
    "TEST-CASE-TRACE": ("对象", "稳定引用", "来源文档"),
    "TEST-CASE-INPUT": ("输入项", "输入值", "输入方式", "来源/约束"),
    "TEST-CASE-STEPS": ("序号", "操作", "页面/接口", "预期中间结果"),
    "TEST-CASE-OUTPUT": ("输出位置", "预期输出", "验证方式", "通过标准"),
    "TEST-CASE-AUTOMATION": ("测试数据初始化", "清理脚本", "自动化测试文件与方法", "执行命令", "状态/不自动化理由"),
}

COMMAND_HELP_METADATA = {
    "init": {
        "mutation": True,
        "previewSupported": False,
        "effect": "REQUIREMENT_TEST_DESIGN_WRITE",
    },
    "validate": {
        "mutation": False,
        "previewSupported": False,
        "effect": "READ_ONLY",
    },
}


@dataclass
class ValidationResult:
    path: str
    stage: str
    errors: list[str]
    warnings: list[str]
    counts: dict[str, int]

    @property
    def valid(self) -> bool:
        return not self.errors

_table_after_marker = table_after_marker


def _reasoned_na(value: str) -> bool:
    normalized = value.strip()
    return bool(re.search(r"(?:不适用|N/A).*(?:理由|因为|无需|未涉及|不依赖)", normalized, re.I))


def _actionable(value: str) -> bool:
    normalized = re.sub(r"[`*]", "", value).strip()
    return bool(normalized) and not VAGUE_RE.fullmatch(normalized) and not PLACEHOLDER_RE.search(normalized)


def _case_blocks(text: str) -> list[tuple[str, str]]:
    matches = list(CASE_MARKER_RE.finditer(text))
    return [
        (match.group(1), text[match.end():matches[index + 1].start() if index + 1 < len(matches) else len(text)])
        for index, match in enumerate(matches)
    ]


def expected_path(doc_root: Path, version: str, requirement_id: str) -> Path:
    return doc_layout.requirement_dir(doc_root, version, requirement_id) / "test_case.md"


def render_template(requirement_id: str, revision: str, requirement_revision: str) -> str:
    if not re.fullmatch(r"[A-Z][A-Z0-9_-]{2,}", requirement_id):
        raise ValueError(f"invalid requirement ID: {requirement_id}")
    case_id = f"CASE-{requirement_id}-001"
    text = TEMPLATE.read_text(encoding="utf-8")
    for source, target in {
        "{REQUIREMENT_ID}": requirement_id,
        "{CASE_ID}": case_id,
        "{TEST_REVISION}": revision,
        "{REQUIREMENT_REVISION}": requirement_revision,
    }.items():
        text = text.replace(source, target)
    return text


def validate_document(path: Path, stage: str, doc_root: Path | None = None) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    if not path.is_file():
        return ValidationResult(str(path), stage, ["test case document does not exist"], [], {})
    text = path.read_text(encoding="utf-8")
    for heading in REQUIRED_HEADINGS:
        if heading not in text:
            errors.append(f"missing required heading: {heading}")
    blocks = _case_blocks(text)
    if not blocks:
        errors.append("at least one <!-- TEST-CASE: CASE-* --> block is required")

    requirement_id = ""
    if doc_root is not None:
        try:
            relative = path.resolve().relative_to(doc_root.resolve()).as_posix()
        except ValueError:
            errors.append("test_case.md escapes DOC_ROOT")
        else:
            pattern = re.compile(r"^version/[^/]+/requirement/([^/]+)/test_case\.md$")
            match = pattern.fullmatch(relative)
            if not match:
                errors.append("test_case.md must be version/{VERSION}/requirement/{REQUIREMENT_ID}/test_case.md")
            else:
                requirement_id = match.group(1)
                if not (path.parent / "requirement.md").is_file():
                    errors.append("requirement-scoped test_case.md requires sibling requirement.md")
    if requirement_id and f"> Requirement：`{requirement_id}`" not in text:
        errors.append("Requirement metadata must equal the requirement directory name")

    case_rows = 0
    for case_id, block in blocks:
        if stage == "ready" and not CASE_ID_RE.fullmatch(case_id):
            errors.append(f"invalid test Case ID: {case_id}")
        if not any(case_id in line for line in block.splitlines()[0:4]):
            warnings.append(f"{case_id}: heading should repeat the stable Case ID")
        for marker, columns in TABLE_CONTRACTS.items():
            header, rows = _table_after_marker(block, marker)
            if not header:
                errors.append(f"{case_id}: missing table {marker}")
                continue
            missing = [column for column in columns if column not in header]
            if missing:
                errors.append(f"{case_id}: {marker} missing columns: {missing}")
            if not rows:
                errors.append(f"{case_id}: {marker} requires at least one row")
                continue
            case_rows += len(rows)
            if stage != "ready":
                continue
            for index, row in enumerate(rows, 1):
                if len(row) != len(header):
                    errors.append(f"{case_id}: {marker} row {index} has {len(row)} cells; expected {len(header)}")
                    continue
                values = dict(zip(header, row))
                for column in columns:
                    value = values.get(column, "")
                    if marker == "TEST-CASE-AUTOMATION" and _reasoned_na(value):
                        continue
                    if not _actionable(value):
                        errors.append(f"{case_id}: {marker} row {index} has non-actionable {column}")
        if stage == "ready":
            if doc_root is not None:
                errors.extend(f"{case_id}: {error}" for error in
                              page_relations.validate_case_relations(path, block, doc_root))
            trace_header, trace_rows = _table_after_marker(block, "TEST-CASE-TRACE")
            trace_values = " ".join(" ".join(row) for row in trace_rows)
            if requirement_id and (requirement_id not in trace_values or "requirement.md" not in trace_values):
                errors.append(f"{case_id}: trace must link the current requirement.md")
            if "PAGE-" in trace_values and "PAGEACT-" not in trace_values:
                errors.append(f"{case_id}: page Case must bind both PAGE-* and PAGEACT-*")
            output_header, output_rows = _table_after_marker(block, "TEST-CASE-OUTPUT")
            if output_header and output_rows:
                validation_index = output_header.index("验证方式") if "验证方式" in output_header else -1
                if validation_index >= 0 and any(not _actionable(row[validation_index]) for row in output_rows if len(row) > validation_index):
                    errors.append(f"{case_id}: every expected output requires a concrete verification method")

    placeholders = PLACEHOLDER_RE.findall(text)
    unchecked = len(re.findall(r"^- \[ \]", text, re.M))
    checked = len(re.findall(r"^- \[[xX]\]", text, re.M))
    if stage == "ready":
        if placeholders:
            errors.append(f"unresolved test case placeholders: {sorted(set(placeholders))}")
        if unchecked:
            errors.append(f"completion checklist has {unchecked} unchecked item(s)")
        if "> 状态：测试设计完成" not in text:
            errors.append("ready test design must set status to 测试设计完成")
    elif unchecked:
        warnings.append(f"draft has {unchecked} unchecked completion gate(s)")
    return ValidationResult(
        str(path), stage, errors, warnings,
        {
            "cases": len(blocks),
            "tableRows": case_rows,
            "unresolvedPlaceholders": len(placeholders),
            "checkedCompletionGates": checked,
            "uncheckedCompletionGates": unchecked,
        },
    )


def _emit(result: ValidationResult, as_json: bool) -> None:
    if as_json:
        print(json.dumps({
            "valid": result.valid,
            "path": result.path,
            "stage": result.stage,
            "errors": result.errors,
            "warnings": result.warnings,
            "counts": result.counts,
        }, ensure_ascii=False, indent=2))
        return
    print(f"test case document: {result.path}")
    print(f"stage: {result.stage}")
    print(f"valid: {'YES' if result.valid else 'NO'}")
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    for error in result.errors:
        print(f"ERROR: {error}")


def command_init(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "case_doc", "init")
    doc_root = Path(args.doc_root)
    output = expected_path(doc_root, args.version, args.requirement_id)
    requirement = output.parent / "requirement.md"
    if not requirement.is_file():
        raise FileNotFoundError(f"requirement document does not exist: {requirement}")
    if output.exists() and not args.overwrite:
        raise FileExistsError(f"test case document already exists: {output}")
    rendered = render_template(args.requirement_id, args.revision, args.requirement_revision)
    pending_case = {output.resolve(): rendered.encode("utf-8")}
    try:
        relation_payloads = artifact_relations.build_payloads(doc_root, pending=pending_case)
        portal_payloads = document_portal.build_payloads(
            doc_root,
            versions=[args.version],
            pending={**pending_case, **relation_payloads},
        )
    except (artifact_relations.ArtifactRelationError, document_portal.DocumentPortalError, OSError, ValueError) as exc:
        raise ValueError(
            "cannot initialize test case because derived relation/portal refresh failed; "
            "inspect the referenced project document and run artifact_relations validation: "
            f"{exc}"
        ) from exc
    common_utils.atomic_write_text(output, rendered)
    for path, content in {**relation_payloads, **portal_payloads}.items():
        common_utils.atomic_write_text(path, content.decode("utf-8"))
    draft_validation = validate_document(output, "structure", doc_root)
    payload = {
        "created": output.as_posix(),
        "stage": "draft",
        "template": "assets/documents/structured-docs/test_case.template.md",
        "draftValidation": "PASSED" if draft_validation.valid else "FAILED",
        "unresolvedPlaceholders": draft_validation.counts.get("unresolvedPlaceholders", 0),
        "next": "complete the placeholders in this same file, then validate --stage ready; placeholders are expected in draft",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else output)
    return 0


def command_validate(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "case_doc", "validate", declared=True)
    result = validate_document(Path(args.file), args.stage, Path(args.doc_root))
    _emit(result, args.json)
    return 0 if result.valid else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create test_case.md under one requirement directory")
    agent_context.add_agent_argument(init)
    init.add_argument("--doc-root", required=True)
    init.add_argument("--version", required=True)
    init.add_argument("--requirement-id", required=True)
    init.add_argument("--revision", required=True)
    init.add_argument("--requirement-revision", required=True)
    init.add_argument("--overwrite", action="store_true")
    init.add_argument("--json", action="store_true")
    init.set_defaults(func=command_init)
    validate = commands.add_parser("validate", help="validate requirement-scoped test design structure or readiness")
    agent_context.add_agent_argument(validate)
    validate.add_argument("--doc-root", required=True)
    validate.add_argument("--file", required=True)
    validate.add_argument("--stage", choices=("structure", "ready"), default="ready")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=command_validate)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except (ValueError, FileNotFoundError, FileExistsError, agent_context.AgentAuthorizationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
