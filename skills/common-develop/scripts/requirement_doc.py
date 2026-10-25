#!/usr/bin/env python3
"""Create and validate canonical requirement.md documents.

The document is created during requirement confirmation and completed during
requirement analysis. One requirement may contain one or many features; all
features share the same ID, rule, acceptance, and traceability conventions.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

import agent_context
import common_utils
import storage
import document_facts
import doc_layout
import document_portal
import artifact_relations
from _core.document_utils import split_refs, table_rows_in_section
from _core.cli_arguments import (add_agent_argument, add_doc_root_argument, add_file_argument, add_json_argument, add_repeatable_argument, add_task_dir_argument, build_parser as build_common_parser, set_handler)


ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "assets" / "structured-docs" / "requirement.template.md"

REQUIRED_HEADINGS = (
    "## 1. 需求信息",
    "## 2. 背景与问题",
    "## 3. 需求目标",
    "## 4. 范围",
    "## 5. 功能列表",
    "## 6. 功能详细需求",
    "## 7. 跨功能规则",
    "## 8. 异常与边界场景",
    "## 9. 验收标准",
    "## 10. 非功能要求",
    "## 11. 模块职责边界",
    "## 12. 待确认事项与已确认决策",
    "## 13. 追踪关系",
    "## 14. 变更记录",
)
READABILITY_HEADING = "### 1.1 阅读摘要"
CONFIRMATION_HEADINGS = REQUIRED_HEADINGS[:4] + REQUIRED_HEADINGS[11:12] + REQUIRED_HEADINGS[13:14]
METADATA_KEYS = (
    "需求编号",
    "需求名称",
    "版本目标编号",
    "需求类型",
    "主责模块",
    "协作模块",
    "受影响角色",
    "当前状态",
    "对应变更需求编号",
)

FEATURE_RE = re.compile(r"\b[A-Z][A-Z0-9_-]*-F\d{2,}\b")
BR_RE = re.compile(r"\bBR-[A-Z][A-Z0-9_-]*-\d{3,}\b")
CR_RE = re.compile(r"\bCR-[A-Z][A-Z0-9_-]*-\d{3,}\b")
AC_RE = re.compile(r"\bAC-[A-Z][A-Z0-9_-]*-\d{3,}\b")
TR_RE = re.compile(r"\bTR-[A-Z][A-Z0-9_-]*-\d{3,}\b")
FLOW_RE = re.compile(r"\bFLOW-[A-Z0-9_-]+\b")
EX_RE = re.compile(r"\bEX-[A-Z][A-Z0-9_-]*-\d{3,}\b")
TRACE_STATUSES = {"PENDING", "COVERED", "GAP", "STALE", "NOT_APPLICABLE"}
PLACEHOLDER_RE = re.compile(r"\{[A-Z][A-Z0-9_-]*\}")


@dataclass(frozen=True)
class Feature:
    code: str
    name: str
    owner: str
    collaborators: str
    description: str




@dataclass(frozen=True)
class TraceSource:
    trace_id: str
    feature_id: str
    rule_ids: tuple[str, ...]
    acceptance_ids: tuple[str, ...]
    impact_refs: tuple[str, ...]
    model_refs: tuple[str, ...]
    design_refs: tuple[str, ...]
    test_case_ids: tuple[str, ...]
    status: str
    flow_refs: tuple[str, ...] = ()
    source_digest: str = ""
    cross_module_required: bool = False


def markdown_table_rows(text: str, heading: str, next_headings: Iterable[str]) -> list[dict[str, str]]:
    return table_rows_in_section(text, heading, next_headings, section)


def feature_names(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        match = re.match(r"^###\s+6\.\d+\s+([A-Z][A-Z0-9_-]*-F\d{2,})\s+(.+?)\s*$", line)
        if match:
            result[match.group(1)] = match.group(2).strip()
    return result


def parse_trace_sources(text: str) -> list[TraceSource]:
    rows = markdown_table_rows(text, "## 13. 追踪关系", REQUIRED_HEADINGS[13:])
    features = {
        row.get("功能编号", ""): row
        for row in markdown_table_rows(text, "## 5. 功能列表", REQUIRED_HEADINGS[5:])
    }
    source_digest = common_utils.sha256_text(text)
    result: list[TraceSource] = []
    for row in rows:
        trace_ids = TR_RE.findall(row.get("追踪编号", ""))
        feature_ids = FEATURE_RE.findall(row.get("功能编号", ""))
        if not trace_ids and not feature_ids:
            continue
        trace_id = trace_ids[0] if trace_ids else row.get("追踪编号", "").strip()
        feature_id = feature_ids[0] if feature_ids else row.get("功能编号", "").strip()
        feature = features.get(feature_id, {})
        modules = set(split_refs(feature.get("协作模块", "")))
        modules.discard(feature.get("主责模块", ""))
        modules.difference_update({"-", "无", "不涉及", "N/A", "待补充"})
        rule_cell = row.get("业务规则/跨功能规则", "")
        acceptance_cell = row.get("验收标准", "")
        status = row.get("状态", "PENDING").strip() or "PENDING"
        result.append(TraceSource(
            trace_id=trace_id,
            feature_id=feature_id,
            rule_ids=tuple(BR_RE.findall(rule_cell) + CR_RE.findall(rule_cell)),
            acceptance_ids=tuple(AC_RE.findall(acceptance_cell)),
            flow_refs=tuple(FLOW_RE.findall(row.get("关联流程", ""))),
            impact_refs=tuple(split_refs(row.get("影响分析", ""))),
            model_refs=tuple(split_refs(row.get("后续业务模型", ""))),
            design_refs=tuple(split_refs(row.get("后续设计", ""))),
            test_case_ids=tuple(split_refs(row.get("测试 Case", ""))),
            status=status,
            source_digest=source_digest,
            cross_module_required=bool(modules),
        ))
    return result


def requirement_ref_path(requirement_path: Path, task_dir: Path) -> str:
    version_root = task_dir.parent.parent
    doc_root = version_root.parent.parent
    try:
        return requirement_path.resolve().relative_to(doc_root.resolve()).as_posix()
    except ValueError:
        return requirement_path.resolve().as_posix()


@dataclass
class ValidationResult:
    stage: str
    path: str
    errors: list[str]
    warnings: list[str]
    counts: dict[str, int]

    @property
    def valid(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "stage": self.stage,
            "path": self.path,
            "errors": self.errors,
            "warnings": self.warnings,
            "counts": self.counts,
        }


def parse_feature(value: str, requirement_id: str, index: int, primary_module: str) -> Feature:
    parts = [part.strip() for part in value.split("|")]
    if len(parts) > 5:
        raise ValueError("--feature accepts CODE|NAME|OWNER|COLLABORATORS|DESCRIPTION")
    parts += [""] * (5 - len(parts))
    code, name, owner, collaborators, description = parts
    code = code or f"{requirement_id}-F{index:02d}"
    if not FEATURE_RE.fullmatch(code):
        raise ValueError(f"invalid feature code: {code}")
    return Feature(
        code=code,
        name=name or f"待分析功能{index}",
        owner=owner or primary_module,
        collaborators=collaborators or "-",
        description=description or "待需求分析补充",
    )


def feature_detail(feature: Feature, index: int, requirement_id: str) -> str:
    base = (index - 1) * 2 + 1
    return f"""### 6.{index} {feature.code} {feature.name}

#### 6.{index}.1 功能目标

{{{feature.code}_GOAL}}

#### 6.{index}.2 角色与权限

- {{{feature.code}_ROLE_OR_PERMISSION}}

#### 6.{index}.3 前置条件

- {{{feature.code}_PRECONDITION}}

#### 6.{index}.4 正常流程

1. {{{feature.code}_NORMAL_STEP_1}}
2. {{{feature.code}_NORMAL_STEP_2}}

#### 6.{index}.5 业务规则

- BR-{requirement_id}-{base:03d}：{{{feature.code}_BUSINESS_RULE_1}}
- BR-{requirement_id}-{base + 1:03d}：{{{feature.code}_BUSINESS_RULE_2}}

#### 6.{index}.6 输入与输出约束

- 输入：{{{feature.code}_INPUT_CONSTRAINT}}
- 输出：{{{feature.code}_OUTPUT_CONSTRAINT}}

#### 6.{index}.7 状态、幂等、并发与事务语义

- 状态：{{{feature.code}_STATE_RULE}}
- 幂等：{{{feature.code}_IDEMPOTENCY_RULE}}
- 并发：{{{feature.code}_CONCURRENCY_RULE}}
- 事务/部分失败：{{{feature.code}_TRANSACTION_RULE}}

#### 6.{index}.8 异常与禁止副作用

- {{{feature.code}_EXCEPTION_AND_NO_SIDE_EFFECT}}
"""


def acceptance_block(feature: Feature, index: int, requirement_id: str) -> str:
    return f"""### AC-{requirement_id}-{index:03d} {feature.name}

Given {{{feature.code}_ACCEPTANCE_GIVEN}}  
When {{{feature.code}_ACCEPTANCE_WHEN}}  
Then {{{feature.code}_ACCEPTANCE_THEN}}  
And {{{feature.code}_ACCEPTANCE_NO_SIDE_EFFECT}}
"""


def render_template(
    *,
    target_id: str,
    requirement_id: str,
    title: str,
    requirement_type: str,
    primary_module: str,
    collaboration_modules: str,
    affected_roles: str,
    change_requirement_id: str,
    features: list[Feature],
) -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        "{TARGET_ID}": target_id,
        "{REQUIREMENT_ID}": requirement_id,
        "{REQUIREMENT_TITLE}": title,
        "{REQUIREMENT_TYPE}": requirement_type,
        "{PRIMARY_MODULE}": primary_module,
        "{COLLABORATION_MODULES}": collaboration_modules or "-",
        "{AFFECTED_ROLES}": affected_roles or "待确认",
        "{CHANGE_REQUIREMENT_ID}": change_requirement_id or "-",
        "{DATE}": date.today().isoformat(),
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    feature_rows = "\n".join(
        f"| {item.code} | {item.name} | {item.owner} | {item.collaborators} | 待补充 | {item.description} | 待分析 |"
        for item in features
    )
    text = re.sub(
        r"\| [^\n]*-F01 \| \{FEATURE_1_NAME\}[^\n]*\|",
        feature_rows,
        text,
        count=1,
    )

    details = "\n".join(feature_detail(item, index, requirement_id) for index, item in enumerate(features, 1)).rstrip()
    text = re.sub(
        r"### 6\.1 .*?(?=\n## 7\. 跨功能规则)",
        details + "\n",
        text,
        count=1,
        flags=re.S,
    )

    exception_rows = "\n".join(
        f"| EX-{requirement_id}-{index:03d} | {item.code} | {{{item.code}_EXCEPTION_SCENARIO}} | "
        f"{{{item.code}_EXPECTED_RESULT}} | {{{item.code}_FORBIDDEN_SIDE_EFFECT}} |"
        for index, item in enumerate(features, 1)
    )
    text = re.sub(
        r"\| EX-[^\n]+\|",
        exception_rows,
        text,
        count=1,
    )

    acceptances = "\n\n".join(
        acceptance_block(item, index, requirement_id) for index, item in enumerate(features, 1)
    ).rstrip()
    text = re.sub(
        r"### AC-.*?(?=\n## 10\. 非功能要求)",
        acceptances + "\n",
        text,
        count=1,
        flags=re.S,
    )

    trace_rows_list: list[str] = []
    for index, item in enumerate(features, 1):
        base = (index - 1) * 2 + 1
        rule_ids = [
            f"BR-{requirement_id}-{base:03d}",
            f"BR-{requirement_id}-{base + 1:03d}",
        ]
        if index == 1:
            rule_ids.append(f"CR-{requirement_id}-001")
        trace_rows_list.append(
            f"| TR-{requirement_id}-{index:03d} | {item.code} | {'、'.join(rule_ids)} | "
            f"AC-{requirement_id}-{index:03d} | 待补充 | 待补充 | 待补充 | 待补充 | 待补充 | PENDING |"
        )
    trace_rows = "\n".join(trace_rows_list)
    text = re.sub(
        r"\| TR-[^\n]+\|",
        trace_rows,
        text,
        count=1,
    )
    return text


def section(text: str, heading: str, next_headings: Iterable[str]) -> str:
    start = text.find(heading)
    if start < 0:
        return ""
    end = len(text)
    for candidate in next_headings:
        position = text.find(candidate, start + len(heading))
        if 0 <= position < end:
            end = position
    return text[start:end]


def metadata(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in section(text, "## 1. 需求信息", REQUIRED_HEADINGS[1:]).splitlines():
        if not line.startswith("|") or line.startswith("|---"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 2 and cells[0] in METADATA_KEYS:
            result[cells[0]] = cells[1]
    return result


def definition_ids(text: str, pattern: re.Pattern[str], line_pattern: re.Pattern[str]) -> list[str]:
    values: list[str] = []
    for line in text.splitlines():
        if line_pattern.search(line):
            match = pattern.search(line)
            if match:
                values.append(match.group(0))
    return values


def duplicates(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    duplicate: set[str] = set()
    for value in values:
        if value in seen:
            duplicate.add(value)
        seen.add(value)
    return sorted(duplicate)


def validate_requirement(path: Path, stage: str) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    if not path.is_file():
        return ValidationResult(stage, str(path), ["requirement document does not exist"], [], {})
    text = path.read_text(encoding="utf-8")

    headings = CONFIRMATION_HEADINGS if stage == "confirmation" else REQUIRED_HEADINGS
    missing_headings = [heading for heading in headings if heading not in text]
    if missing_headings:
        errors.append(f"missing required headings: {missing_headings}")
    if READABILITY_HEADING not in section(text, REQUIRED_HEADINGS[0], REQUIRED_HEADINGS[1:]):
        errors.append(f"missing human-readable summary: {READABILITY_HEADING}")

    meta = metadata(text)
    missing_meta = [key for key in METADATA_KEYS if not meta.get(key)]
    if missing_meta:
        errors.append(f"missing requirement metadata: {missing_meta}")

    confirmation_text = "\n".join(
        section(text, heading, REQUIRED_HEADINGS[REQUIRED_HEADINGS.index(heading) + 1 :])
        for heading in CONFIRMATION_HEADINGS
        if heading in REQUIRED_HEADINGS
    )
    confirmation_placeholders = sorted(set(PLACEHOLDER_RE.findall(confirmation_text)))
    if confirmation_placeholders:
        errors.append(f"unresolved confirmation placeholders: {confirmation_placeholders[:12]}")

    feature_definitions = definition_ids(text, FEATURE_RE, re.compile(r"^###\s+6\.\d+\s+"))
    br_definitions = definition_ids(text, BR_RE, re.compile(r"^-\s+BR-"))
    cr_definitions = definition_ids(text, CR_RE, re.compile(r"^-\s+CR-"))
    ac_definitions = definition_ids(text, AC_RE, re.compile(r"^###\s+AC-"))
    tr_definitions = definition_ids(text, TR_RE, re.compile(r"^\|\s*TR-"))
    ex_definitions = definition_ids(text, EX_RE, re.compile(r"^\|\s*EX-"))

    counts = {
        "features": len(feature_definitions),
        "businessRules": len(br_definitions),
        "crossFeatureRules": len(cr_definitions),
        "acceptanceCriteria": len(ac_definitions),
        "traceItems": len(tr_definitions),
        "exceptionScenarios": len(ex_definitions),
        "unresolvedPlaceholders": len(set(PLACEHOLDER_RE.findall(text))),
    }

    if stage == "analysis":
        unresolved = sorted(set(PLACEHOLDER_RE.findall(text)))
        if unresolved:
            errors.append(f"unresolved analysis placeholders: {unresolved[:20]}")
        if not feature_definitions:
            errors.append("analysis requires at least one feature detail section")
        if not br_definitions and not cr_definitions:
            errors.append("analysis requires at least one business rule or cross-feature rule")
        if not ac_definitions:
            errors.append("analysis requires at least one acceptance criterion")
        if not tr_definitions:
            errors.append("analysis requires at least one traceability row")

        for label, values in (
            ("feature", feature_definitions),
            ("business rule", br_definitions),
            ("cross-feature rule", cr_definitions),
            ("acceptance criterion", ac_definitions),
            ("trace item", tr_definitions),
            ("exception scenario", ex_definitions),
        ):
            duplicate_values = duplicates(values)
            if duplicate_values:
                errors.append(f"duplicate {label} definitions: {duplicate_values}")

        feature_rows = markdown_table_rows(text, "## 5. 功能列表", REQUIRED_HEADINGS[5:])
        feature_flow_refs: dict[str, set[str]] = {}
        for row in feature_rows:
            feature_ids = FEATURE_RE.findall(row.get("功能编号", ""))
            if not feature_ids:
                continue
            raw_flow_cell = row.get("关联流程", "")
            invalid_flow_values = [
                item for item in split_refs(raw_flow_cell)
                if item not in {"待补充", "无", "-"} and not FLOW_RE.fullmatch(item)
            ]
            if invalid_flow_values:
                errors.append(f"feature has invalid business flow refs: {feature_ids[0]}->{invalid_flow_values}")
            feature_flow_refs[feature_ids[0]] = set(FLOW_RE.findall(raw_flow_cell))

        trace_sources = parse_trace_sources(text)
        traced_features = {item.feature_id for item in trace_sources}
        traced_rules = {rule_id for item in trace_sources for rule_id in item.rule_ids}
        traced_acceptances = {acceptance_id for item in trace_sources for acceptance_id in item.acceptance_ids}
        known_features = set(feature_definitions)
        known_rules = set(br_definitions) | set(cr_definitions)
        known_acceptances = set(ac_definitions)
        counts["tracedFeatures"] = len(traced_features)
        counts["tracedRules"] = len(traced_rules)
        counts["tracedAcceptanceCriteria"] = len(traced_acceptances)
        counts["businessFlowRefs"] = len({flow_id for item in trace_sources for flow_id in item.flow_refs})

        for item in trace_sources:
            if not TR_RE.fullmatch(item.trace_id):
                errors.append(f"invalid traceability id: {item.trace_id}")
            if item.feature_id not in known_features:
                errors.append(f"traceability references unknown feature: {item.trace_id}->{item.feature_id}")
            if not item.rule_ids:
                errors.append(f"traceability row has no business or cross-feature rule: {item.trace_id}")
            if not item.acceptance_ids:
                errors.append(f"traceability row has no acceptance criterion: {item.trace_id}")
            if item.status not in TRACE_STATUSES:
                errors.append(f"traceability row has invalid status: {item.trace_id}->{item.status}")
            declared_flows = feature_flow_refs.get(item.feature_id, set())
            missing_flow_links = sorted(declared_flows - set(item.flow_refs))
            if missing_flow_links:
                errors.append(
                    f"traceability row does not cover feature business flows: {item.trace_id}->{missing_flow_links}"
                )
            for rule_id in item.rule_ids:
                if rule_id not in known_rules:
                    errors.append(f"traceability references unknown rule: {item.trace_id}->{rule_id}")
            for acceptance_id in item.acceptance_ids:
                if acceptance_id not in known_acceptances:
                    errors.append(f"traceability references unknown acceptance criterion: {item.trace_id}->{acceptance_id}")

        for feature in feature_definitions:
            if feature not in traced_features:
                errors.append(f"feature is not covered by traceability: {feature}")
        for rule_id in br_definitions:
            if rule_id not in traced_rules:
                errors.append(f"business rule is not covered by traceability: {rule_id}")
        for rule_id in cr_definitions:
            if rule_id not in traced_rules:
                errors.append(f"cross-feature rule is not covered by traceability: {rule_id}")
        for acceptance in ac_definitions:
            if acceptance not in traced_acceptances:
                errors.append(f"acceptance criterion is not covered by traceability: {acceptance}")

        if len(feature_definitions) > 1 and not cr_definitions:
            warnings.append("multi-feature requirement has no cross-feature rule; confirm that no shared invariant or dependency exists")
        if len(feature_definitions) > 1 and not any(item.flow_refs for item in trace_sources):
            warnings.append("multi-feature requirement has no FLOW-* reference; confirm that no layered business flow is required")

        design_tokens = sorted(set(re.findall(r"\b(?:t_[a-zA-Z0-9_]+|[a-zA-Z][a-zA-Z0-9_]*_flag|(?:GET|POST|PUT|DELETE|PATCH)\s+/[^\s`]+)\b", text)))
        if design_tokens:
            warnings.append(
                "possible implementation details detected; keep them only as current-state evidence and move chosen table/field/API solutions to design: "
                + ", ".join(design_tokens[:10])
            )

    return ValidationResult(stage, str(path), errors, warnings, counts)


def emit(result: ValidationResult, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return
    print(f"requirement document: {result.path}")
    print(f"stage: {result.stage}")
    print(f"valid: {'YES' if result.valid else 'NO'}")
    for key, value in result.counts.items():
        print(f"{key}: {value}")
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    for error in result.errors:
        print(f"ERROR: {error}")


def command_init(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "requirement_doc", "init")
    if not TEMPLATE.is_file():
        raise FileNotFoundError(f"missing requirement template: {TEMPLATE}")
    feature_values = args.feature or [""]
    features = [
        parse_feature(value, args.requirement_id, index, args.primary_module)
        for index, value in enumerate(feature_values, 1)
    ]
    path = (
        Path(args.doc_root)
        / "version"
        / args.version
        / "doc"
        / args.target_id
        / "requirement.md"
    )
    if path.exists() and not args.overwrite:
        raise FileExistsError(f"requirement document already exists: {path}")
    text = render_template(
        target_id=args.target_id,
        requirement_id=args.requirement_id,
        title=args.title,
        requirement_type=args.requirement_type,
        primary_module=args.primary_module,
        collaboration_modules=args.collaboration_modules,
        affected_roles=args.affected_roles,
        change_requirement_id=args.change_requirement_id,
        features=features,
    )
    common_utils.atomic_write_text(path, text)
    artifact_relations.write_views(Path(args.doc_root))
    document_portal.write_portals(Path(args.doc_root), versions=[args.version])
    payload = {
        "created": path.as_posix(),
        "requirementId": args.requirement_id,
        "targetId": args.target_id,
        "featureIds": [feature.code for feature in features],
        "nextStage": "requirement_confirmation",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else path)
    return 0



def requirement_scope(path: Path) -> tuple[Path, str, str]:
    resolved = path.resolve()
    if resolved.name != "requirement.md" or resolved.parent.parent.name != "doc":
        raise ValueError("requirement.md must be located at version/{VERSION}/doc/{TARGET_ID}/requirement.md")
    target_id = resolved.parent.name
    version_root = resolved.parent.parent.parent
    if version_root.parent.name != "version":
        raise ValueError("requirement.md must be located below a version directory")
    return version_root, version_root.name, target_id


def command_sync_list(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "requirement_doc", "sync-list")
    path = Path(args.file)
    result = validate_requirement(path, "confirmation")
    if not result.valid:
        raise ValueError("requirement confirmation document is invalid: " + "; ".join(result.errors))
    meta = metadata(path.read_text(encoding="utf-8"))
    version_root, version, target_id = requirement_scope(path)
    declared_target = meta.get("版本目标编号", "").strip()
    if declared_target != target_id:
        raise ValueError(f"版本目标编号 must equal requirement directory: expected {target_id}, got {declared_target}")
    list_path = version_root / "requirement_list.md"
    document_facts.upsert_requirement_list_row(
        list_path,
        code=meta["需求编号"],
        name=meta["需求名称"],
        version=version,
        document_target=f"doc/{target_id}/requirement.md",
        description=args.description,
        changed_code=meta.get("对应变更需求编号", "-") or "-",
        status=args.status or meta.get("当前状态", "需求确认完成"),
    )
    payload = {
        "updated": list_path.as_posix(),
        "requirementId": meta["需求编号"],
        "version": version,
        "documentLink": f"doc/{target_id}/requirement.md",
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else list_path)
    return 0

def command_validate(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "requirement_doc", "validate", declared=True)
    result = validate_requirement(Path(args.file), args.stage)
    emit(result, args.json)
    return 0 if result.valid else 2


def command_status(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "requirement_doc", "status", declared=True)
    path = Path(args.file)
    confirmation = validate_requirement(path, "confirmation")
    analysis = validate_requirement(path, "analysis")
    payload = {
        "path": str(path),
        "confirmationReady": confirmation.valid,
        "analysisReady": analysis.valid,
        "confirmationErrors": confirmation.errors,
        "analysisErrors": analysis.errors,
        "warnings": analysis.warnings,
        "counts": analysis.counts,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"requirement document: {path}")
        print(f"confirmation ready: {'YES' if confirmation.valid else 'NO'}")
        print(f"analysis ready: {'YES' if analysis.valid else 'NO'}")
        for key, value in analysis.counts.items():
            print(f"{key}: {value}")
    return 0



def trace_item_from_source(
    source: TraceSource,
    *,
    source_path: str,
    feature_name: str,
) -> dict[str, Any]:
    requirement_refs = [
        f"{source_path}#{source.feature_id}",
        *(f"{source_path}#{rule_id}" for rule_id in source.rule_ids),
        *(f"{source_path}#{acceptance_id}" for acceptance_id in source.acceptance_ids),
        f"{source_path}#{source.trace_id}",
    ]
    relation = ", ".join(source.rule_ids) + " -> " + ", ".join(source.acceptance_ids)
    return {
        "id": source.trace_id,
        "description": f"{feature_name}: {relation}",
        "status": source.status,
        "acceptance_criteria": list(source.acceptance_ids),
        "requirement_refs": requirement_refs,
        **({"requirement_source_digest": source.source_digest} if source.source_digest else {}),
        "impact_required": source.cross_module_required,
        "dependency_impact_refs": [],
        "business_flow_required": bool(source.flow_refs),
        "business_flow_refs": list(source.flow_refs),
        "cross_module_implementation_required": source.cross_module_required,
        "cross_module_implementation_refs": [],
        "business_model_refs": [],
        "design_refs": [],
        "test_case_ids": [],
        "plan_task_ids": [],
        "contract_refs": [],
        "implementation_refs": [],
        "verification_evidence_ids": [],
        "verified_by_agents": [],
        "notes": f"Synced from {source_path} by requirement_doc.py",
    }


def merge_traceability(
    existing: list[dict[str, Any]],
    sources: list[TraceSource],
    *,
    source_path: str,
    names: dict[str, str],
    requirement_id: str,
) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    by_id = {str(item.get("id") or ""): dict(item) for item in existing if item.get("id")}
    ordered_existing_ids = [str(item.get("id") or "") for item in existing if item.get("id")]
    source_ids = {source.trace_id for source in sources}
    created: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []
    stale: list[str] = []
    impact_review_required: list[str] = []

    for source in sources:
        generated = trace_item_from_source(
            source,
            source_path=source_path,
            feature_name=names.get(source.feature_id, source.feature_id),
        )
        current = by_id.get(source.trace_id)
        if current is None:
            by_id[source.trace_id] = generated
            ordered_existing_ids.append(source.trace_id)
            created.append(source.trace_id)
            continue

        preserved_other_requirement_refs = [
            value for value in current.get("requirement_refs", [])
            if isinstance(value, str) and not value.startswith(source_path + "#")
        ]
        generated_refs = generated["requirement_refs"]
        next_requirement_refs = list(dict.fromkeys(preserved_other_requirement_refs + generated_refs))
        source_changed = (
            current.get("description") != generated["description"]
            or current.get("acceptance_criteria") != generated["acceptance_criteria"]
            or current.get("requirement_refs") != next_requirement_refs
            or (bool(source.source_digest) and current.get("requirement_source_digest") != source.source_digest)
            or current.get("business_flow_refs", []) != generated["business_flow_refs"]
            or (source.cross_module_required and not current.get("cross_module_implementation_required"))
        )
        current["description"] = generated["description"]
        current["acceptance_criteria"] = generated["acceptance_criteria"]
        current["requirement_refs"] = next_requirement_refs
        if source.source_digest:
            current["requirement_source_digest"] = source.source_digest
        current["business_flow_refs"] = generated["business_flow_refs"]
        current["business_flow_required"] = generated["business_flow_required"]
        if source.cross_module_required:
            current["impact_required"] = True
            current["cross_module_implementation_required"] = True
        elif current.get("cross_module_implementation_required"):
            impact_review_required.append(source.trace_id)
        if source_changed:
            downstream_fields = (
                "business_model_refs", "design_refs", "test_case_ids", "plan_task_ids",
                "contract_refs", "implementation_refs", "verification_evidence_ids",
            )
            has_downstream = any(current.get(field) for field in downstream_fields)
            if has_downstream or current.get("status") in {"COVERED", "NOT_APPLICABLE"}:
                current["status"] = "STALE"
                stale.append(source.trace_id)
            note = str(current.get("notes") or "").strip()
            sync_note = f"Requirement source synchronized from {source_path}; downstream references require revalidation."
            current["notes"] = sync_note if not note else note + "\n" + sync_note
            updated.append(source.trace_id)
        else:
            unchanged.append(source.trace_id)
        by_id[source.trace_id] = current

    removed_source_ids: list[str] = []
    source_prefix = f"TR-{requirement_id}-"
    for trace_id, item in by_id.items():
        if trace_id in source_ids or not trace_id.startswith(source_prefix):
            continue
        refs = item.get("requirement_refs") or []
        if any(isinstance(ref, str) and ref.startswith(source_path + "#") for ref in refs):
            item["status"] = "STALE"
            note = str(item.get("notes") or "").strip()
            removed_note = f"Trace is no longer present in {source_path}; preserved for audit and marked STALE."
            item["notes"] = removed_note if not note else note + "\n" + removed_note
            removed_source_ids.append(trace_id)
            if trace_id not in stale:
                stale.append(trace_id)

    merged = [by_id[trace_id] for trace_id in ordered_existing_ids if trace_id in by_id]
    return merged, {
        "created": created,
        "updated": updated,
        "unchanged": unchanged,
        "stale": stale,
        "removedSourceTraceIds": removed_source_ids,
        "impactReviewRequired": impact_review_required,
    }


def command_sync_traceability(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "requirement_doc", "sync-traceability")
    requirement_path = Path(args.file)
    validation = validate_requirement(requirement_path, "analysis")
    if not validation.valid:
        raise ValueError("requirement analysis validation failed: " + "; ".join(validation.errors))
    text = requirement_path.read_text(encoding="utf-8")
    sources = parse_trace_sources(text)
    meta = metadata(text)
    requirement_id = meta.get("需求编号", "").strip()
    if not requirement_id:
        raise ValueError("requirement document has no requirement id")
    task_dir = Path(args.task_dir)
    if not task_dir.is_dir():
        raise FileNotFoundError(f"task directory does not exist: {task_dir}")
    trace_path = storage.path(task_dir, "traceability")
    if trace_path.exists():
        document = trace_path.read_text(encoding="utf-8")
        value = json.loads(document)
        existing = value.get("items", []) if isinstance(value, dict) else []
    else:
        document = json.dumps({"schema_version": 1, "target_id": task_dir.name, "items": []}, ensure_ascii=False, indent=2)
        existing = []
    source_path = requirement_ref_path(requirement_path, task_dir)
    merged, summary = merge_traceability(
        existing,
        sources,
        source_path=source_path,
        names=feature_names(text),
        requirement_id=requirement_id,
    )
    updated_document = json.dumps({"schema_version": 1, "target_id": task_dir.name, "items": merged}, ensure_ascii=False, indent=2) + "\n"
    changed = updated_document != document
    if changed and not args.dry_run:
        common_utils.atomic_write_text(trace_path, updated_document)
    payload = {
        "requirement": requirement_path.as_posix(),
        "traceability": trace_path.as_posix(),
        "dryRun": bool(args.dry_run),
        "changed": changed,
        "sourceTraceCount": len(sources),
        "resultTraceCount": len(merged),
        **summary,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"traceability: {trace_path}")
        print(f"changed: {'YES' if changed else 'NO'}")
        for key in ("created", "updated", "unchanged", "stale", "removedSourceTraceIds"):
            print(f"{key}: {', '.join(payload[key]) if payload[key] else '-'}")
    return 0


def command_finalize_analysis(args: argparse.Namespace) -> int:
    """Atomically validate requirement analysis and initialize traceability."""
    agent = agent_context.authorize_command(args, "requirement_doc", "sync-traceability")
    requirement_path = Path(args.file)
    task_dir = Path(args.task_dir)
    trace_path = storage.path(task_dir, "traceability")
    existed = trace_path.exists()
    backup = trace_path.read_bytes() if existed else b""
    try:
        validation = validate_requirement(requirement_path, "analysis")
        if not validation.valid:
            raise ValueError("requirement analysis validation failed: " + "; ".join(validation.errors))
        sync_args = argparse.Namespace(
            file=str(requirement_path), task_dir=str(task_dir), dry_run=False, json=False, agent=agent
        )
        with contextlib.redirect_stdout(io.StringIO()):
            command_sync_traceability(sync_args)
        value = json.loads(trace_path.read_text(encoding="utf-8"))
        traces = value.get("items", []) if isinstance(value, dict) else []
        if not isinstance(traces, list) or not traces:
            raise ValueError("requirement analysis cannot finalize without traceability entries")
        payload = {
            "status": "PASSED",
            "requirement": requirement_path.as_posix(),
            "traceability": trace_path.as_posix(),
            "traceCount": len(traces),
        }
    except Exception:
        if existed:
            trace_path.write_bytes(backup)
        elif trace_path.exists():
            trace_path.unlink()
        raise
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"FINALIZED {requirement_path}")
        print(f"TRACEABILITY {trace_path} count={payload['traceCount']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = build_common_parser(__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create version/{VERSION}/doc/{TARGET_ID}/requirement.md")
    add_agent_argument(init)
    add_doc_root_argument(init, required=True)
    init.add_argument("--version", required=True)
    init.add_argument("--target-id", required=True)
    init.add_argument("--requirement-id", required=True)
    init.add_argument("--title", required=True)
    init.add_argument("--requirement-type", default="新增能力")
    init.add_argument("--primary-module", required=True)
    init.add_argument("--collaboration-modules", default="-")
    init.add_argument("--affected-roles", default="待确认")
    init.add_argument("--change-requirement-id", default="-")
    add_repeatable_argument(init, "--feature", help="repeat CODE|NAME|OWNER|COLLABORATORS|DESCRIPTION; omit for one draft feature")
    init.add_argument("--overwrite", action="store_true")
    add_json_argument(init)
    set_handler(init, command_init)

    sync_list = sub.add_parser("sync-list", help="upsert requirement metadata into version requirement_list.md")
    add_agent_argument(sync_list)
    add_file_argument(sync_list)
    sync_list.add_argument("--description", required=True)
    sync_list.add_argument("--status", default="")
    add_json_argument(sync_list)
    set_handler(sync_list, command_sync_list)

    validate = sub.add_parser("validate", help="validate confirmation or analysis completeness")
    add_agent_argument(validate)
    add_file_argument(validate)
    validate.add_argument("--stage", choices=("confirmation", "analysis"), required=True)
    add_json_argument(validate)
    set_handler(validate, command_validate)

    status = sub.add_parser("status", help="show confirmation and analysis readiness")
    add_agent_argument(status)
    add_file_argument(status)
    add_json_argument(status)
    set_handler(status, command_status)

    sync = sub.add_parser("sync-traceability", help="synchronize requirement trace rows into task/traceability.json")
    add_agent_argument(sync)
    add_file_argument(sync, help="canonical requirement.md")
    add_task_dir_argument(sync, help="version/{VERSION}/task/{TARGET_ID}")
    sync.add_argument("--dry-run", action="store_true")
    add_json_argument(sync)
    set_handler(sync, command_sync_traceability)

    finalize = sub.add_parser("finalize-requirement-analysis", help="atomically validate analysis and initialize task traceability")
    add_agent_argument(finalize)
    add_file_argument(finalize, help="canonical requirement.md")
    add_task_dir_argument(finalize, help="version/{VERSION}/task/{TARGET_ID}")
    add_json_argument(finalize)
    set_handler(finalize, command_finalize_analysis)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        doc_layout.validate_cli_namespace(args)
        return int(args.handler(args))
    except (ValueError, FileNotFoundError, FileExistsError, agent_context.AgentAuthorizationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
