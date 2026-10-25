#!/usr/bin/env python3
"""Create and validate implementation-ready canonical design documents."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from document_core import agent_context
from document_core import common_utils
from runtime import doc_layout
import document_portal
from runtime import artifact_relations
from resource_paths import asset_path
from runtime._core import implementation_strategy
from runtime._core.document_utils import table_after_marker


ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = asset_path('assets/documents/structured-docs/design.template.md')
IMPLEMENTATION_STRATEGY_CONTRACT_VERSION = implementation_strategy.CONTRACT_VERSION
ALLOWED_IMPLEMENTATION_STRATEGIES = implementation_strategy.DECISION_ORDER
ALLOWED_SHARED_LOGIC_DISPOSITIONS = implementation_strategy.SHARED_LOGIC_DISPOSITIONS
IMPLEMENTATION_DECISION_RE = implementation_strategy.DECISION_ID_RE
BLUEPRINT_ID_RE = implementation_strategy.BLUEPRINT_ID_RE
STRATEGY_CONTRACT_RE = re.compile(r"^>\s*Implementation strategy contract：`(\d+)`\s*$", re.M)

REQUIRED_HEADINGS = (
    "## 第一部分：设计正文",
    "### 1. 一页设计摘要",
    "### 2. 背景、现状与设计目标",
    "### 3. 影响范围与明确边界",
    "### 4. 目标方案与职责边界",
    "### 5. 核心流程、状态与失败路径",
    "### 6. 数据与持久化方案",
    "### 7. 接口、交互与兼容策略",
    "### 8. 开发者交接摘要",
    "## 第二部分：开发实施明细",
    "### 9. 需求映射与总变更清单",
    "### 10. 表、字段与数据读写明细",
    "### 11. 接口与字段映射明细",
    "### 12. 行为调整与代码改动",
    "### 13. 异常、安全、观测与验证明细",
    "## 附录：追踪、决策与未决项",
)
READABILITY_HEADING = "### 1. 一页设计摘要"
PLACEHOLDER_RE = re.compile(r"\{[A-Z][A-Z0-9_-]*\}")
VAGUE_RE = re.compile(r"(?:待补充|待确认|TODO|TBD)", re.I)

NARRATIVE_CONTRACTS = (
    "DESIGN-NARRATIVE-SUMMARY",
    "DESIGN-NARRATIVE-CURRENT",
    "DESIGN-NARRATIVE-TARGET",
    "DESIGN-NARRATIVE-FLOW",
    "DESIGN-NARRATIVE-DATA",
    "DESIGN-NARRATIVE-COMPATIBILITY",
    "DESIGN-NARRATIVE-HANDOFF",
)

MATRIX_CONTRACTS: dict[str, tuple[str, ...]] = {
    "DESIGN-CHANGE-INVENTORY": ("变更对象", "变更类型", "当前事实与证据", "目标变化", "作用", "Owner", "兼容要求"),
    "DESIGN-IMPLEMENTATION-DECISIONS": ("决策 ID", "对象", "策略", "现有候选与代码证据", "REUSE 不适用理由", "COMPATIBLE_EXTEND 不适用理由", "MODIFY 不适用理由", "公共逻辑处理", "公共逻辑 Owner", "兼容、调用方与验证", "关联蓝图"),
    "DESIGN-TABLE-CHANGES": ("表/存储对象", "变更类型", "作用与 Owner", "新增/修改内容", "迁移/回填", "DB 增量与实现锚点"),
    "DESIGN-FIELD-CHANGES": ("表", "字段", "变更类型", "业务语义与作用", "值来源", "创建时写入", "更新条件", "是否可变"),
    "DESIGN-API-CHANGES": ("接口/方法", "类型", "调用方", "请求变化", "响应变化", "校验与权限", "幂等", "错误码", "事务边界", "兼容策略", "API 增量与实现锚点"),
    "DESIGN-DATA-ACCESS": ("业务动作", "触发入口", "表/聚合", "操作", "字段", "查询/更新条件", "读取一致性/锁", "写入时机与保存方式", "事务", "失败后的事实", "Trace"),
    "DESIGN-BEHAVIOR-ADJUSTMENTS": ("功能/场景", "当前行为与证据", "调整后行为", "受影响入口/调用方", "数据策略", "API/UI 策略", "回归范围"),
    "DESIGN-CODE-BLUEPRINT": ("蓝图 ID", "顺序", "仓库/模块", "文件、类或配置", "变更类型", "方法/符号", "决策 ID", "具体改动", "作用", "公共逻辑 Owner", "来源文档", "依赖", "对应测试"),
    "DESIGN-VERIFICATION": ("验证项", "对应需求/风险", "测试层级", "前置数据", "操作", "可观察结果", "失败注入/边界", "自动化命令或证据入口"),
}
ALLOWED_CHANGE_TYPES = {"ADD", "MODIFY", "REMOVE", "REUSE", "UNCHANGED", "新增", "修改", "删除", "复用", "不变"}
NON_ACTIONABLE_VALUES = {"", "-", "无", "已明确", "同现有", "沿用现有", "见上文"}


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

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "path": self.path,
            "stage": self.stage,
            "errors": self.errors,
            "warnings": self.warnings,
            "counts": self.counts,
        }


def render_template(
    *,
    title: str,
    module_code: str,
    requirement_ref: str,
    revision: str,
    base_revision: str,
    topic_id: str = "",
) -> str:
    resolved_topic = topic_id or f"DESIGN-{module_code}-GENERAL"
    if not re.fullmatch(r"DESIGN-[A-Z0-9_]+-[A-Z0-9_-]+", resolved_topic):
        raise ValueError(f"invalid stable design topic ID: {resolved_topic}")
    text = TEMPLATE.read_text(encoding="utf-8")
    replacements = {
        "{TITLE}": title,
        "{MODULE_CODE}": module_code,
        "{REQUIREMENT_REF}": requirement_ref,
        "{DESIGN_REVISION}": revision,
        "{BASE_REVISION}": base_revision or "无（首次设计）",
        "{DESIGN_TOPIC_ID}": resolved_topic,
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text


_table_after_marker = table_after_marker


def _meaningful_na(row: list[str]) -> bool:
    if not row or not ("不适用" in row[0] or "N/A" in row[0]):
        return False
    joined = " ".join(row)
    return "理由" in joined or "因为" in joined or "本需求不涉及" in joined


def _narrative_after_marker(text: str, marker: str) -> str:
    marker_token = f"<!-- {marker} -->"
    start = text.find(marker_token)
    if start < 0:
        return ""
    paragraph: list[str] = []
    started = False
    for line in text[start + len(marker_token):].splitlines():
        stripped = line.strip()
        if not stripped:
            if started:
                break
            continue
        if stripped.startswith("#") or stripped.startswith("|") or stripped.startswith("<!--"):
            break
        started = True
        paragraph.append(stripped)
    return " ".join(paragraph)


def _cell_id(value: str, pattern: re.Pattern[str]) -> str:
    return implementation_strategy.extract_id(value, pattern)


def _has_reason(value: str) -> bool:
    normalized = re.sub(r"[`*_\[\]()#]", "", value or "").strip()
    return len(normalized) >= 6 and normalized not in NON_ACTIONABLE_VALUES and normalized.upper() not in {"N/A", "NA"}


def validate_design(path: Path, stage: str) -> ValidationResult:
    if not path.is_file():
        return ValidationResult(str(path), stage, ["design document does not exist"], [], {})
    text = path.read_text(encoding="utf-8")
    errors: list[str] = []
    warnings: list[str] = []

    strategy_contract_match = STRATEGY_CONTRACT_RE.search(text)
    strategy_contract_version = int(strategy_contract_match.group(1)) if strategy_contract_match else 0
    if strategy_contract_version == 0:
        errors.append("design is missing implementation strategy contract version")
    elif strategy_contract_version != IMPLEMENTATION_STRATEGY_CONTRACT_VERSION:
        errors.append(
            f"unsupported implementation strategy contract version: {strategy_contract_version}; "
            f"expected {IMPLEMENTATION_STRATEGY_CONTRACT_VERSION}"
        )

    missing_headings = [heading for heading in REQUIRED_HEADINGS if heading not in text]
    if missing_headings:
        errors.append(f"missing required headings: {missing_headings}")
    if READABILITY_HEADING not in text:
        errors.append(f"missing human-readable summary: {READABILITY_HEADING}")

    first_part = text.find("## 第一部分：设计正文")
    second_part = text.find("## 第二部分：开发实施明细")
    appendix = text.find("## 附录：追踪、决策与未决项")
    if not (0 <= first_part < second_part < appendix):
        errors.append("human-readable design, implementation details, and appendix must appear in that order")

    narrative_counts: dict[str, int] = {}
    for marker in NARRATIVE_CONTRACTS:
        paragraph = _narrative_after_marker(text, marker)
        narrative_counts[marker] = len(re.sub(r"\s+", "", paragraph))
        if not paragraph:
            errors.append(f"missing human-readable narrative: {marker}")
        elif stage == "ready":
            if narrative_counts[marker] < 40:
                errors.append(f"{marker} must explain the design in at least 40 non-whitespace characters")
            if not re.search(r"[。；：.!?！？]", paragraph):
                errors.append(f"{marker} must be prose with explicit sentence punctuation")

    matrix_counts: dict[str, int] = {}
    matrix_values: dict[str, tuple[list[str], list[list[str]]]] = {}
    for marker, declared_columns in MATRIX_CONTRACTS.items():
        required_columns = declared_columns
        marker_position = text.find(f"<!-- {marker} -->")
        if second_part >= 0 and marker_position >= 0 and marker_position < second_part:
            errors.append(f"implementation matrix must stay in the second part: {marker}")
        header, rows = _table_after_marker(text, marker)
        matrix_values[marker] = (header, rows)
        if not header:
            errors.append(f"missing implementation matrix: {marker}")
            matrix_counts[marker] = 0
            continue
        missing_columns = [column for column in required_columns if column not in header]
        if missing_columns:
            errors.append(f"{marker} missing columns: {missing_columns}")
        if not rows:
            errors.append(f"{marker} requires at least one detail row or a reasoned not-applicable row")
        matrix_counts[marker] = len(rows)
        if stage == "ready":
            for index, row in enumerate(rows, 1):
                joined = " ".join(row)
                row_declares_na = bool(row) and ("不适用" in row[0] or "N/A" in row[0])
                if row_declares_na and not _meaningful_na(row):
                    errors.append(f"{marker} row {index} uses not-applicable without a reason")
                    continue
                if _meaningful_na(row):
                    continue
                if len(row) != len(header):
                    errors.append(f"{marker} row {index} has {len(row)} cells; expected {len(header)}")
                    continue
                values = dict(zip(header, row))
                empty_columns = [column for column in required_columns if values.get(column, "").strip() in NON_ACTIONABLE_VALUES]
                if empty_columns:
                    errors.append(f"{marker} row {index} has non-actionable cells: {empty_columns}")
                if marker == "DESIGN-TABLE-CHANGES" and not re.search(r"(?:changes/.+\.ya?ml|migration|ddl|schema|[/\\]|#|\bDB-[A-Z0-9_-]+\b)", values.get("DB 增量与实现锚点", ""), re.I):
                    errors.append(f"{marker} row {index} must link a version DB delta and current implementation anchor")
                if marker == "DESIGN-API-CHANGES" and not re.search(r"(?:changes/.+\.ya?ml|openapi|controller|[/\\]|#|\bAPI-[A-Z0-9_-]+\b)", values.get("API 增量与实现锚点", ""), re.I):
                    errors.append(f"{marker} row {index} must link a version API delta and current implementation anchor")
                if marker == "DESIGN-CODE-BLUEPRINT" and not re.search(r"(?:[/\\]|\.[A-Za-z0-9]+\b|建议新增)", values.get("文件、类或配置", "")):
                    errors.append(f"{marker} row {index} must identify a real path/symbol or an explicitly proposed file")

    if stage == "ready":
        decision_header, decision_rows = matrix_values.get("DESIGN-IMPLEMENTATION-DECISIONS", ([], []))
        blueprint_header, blueprint_rows = matrix_values.get("DESIGN-CODE-BLUEPRINT", ([], []))
        decision_ids: list[str] = []
        decision_blueprints: dict[str, str] = {}
        for index, row in enumerate(decision_rows, 1):
            if len(row) != len(decision_header) or _meaningful_na(row):
                continue
            values = dict(zip(decision_header, row))
            decision_id = _cell_id(values.get("决策 ID", ""), IMPLEMENTATION_DECISION_RE)
            strategy = values.get("策略", "").strip()
            disposition = values.get("公共逻辑处理", "").strip()
            if not decision_id:
                errors.append(f"DESIGN-IMPLEMENTATION-DECISIONS row {index} has invalid IMPL-DEC-* ID")
            else:
                decision_ids.append(decision_id)
                decision_blueprints[decision_id] = _cell_id(values.get("关联蓝图", ""), BLUEPRINT_ID_RE)
            if strategy not in ALLOWED_IMPLEMENTATION_STRATEGIES:
                errors.append(
                    f"DESIGN-IMPLEMENTATION-DECISIONS row {index} strategy must be one of "
                    f"{list(ALLOWED_IMPLEMENTATION_STRATEGIES)}"
                )
                continue
            if disposition not in ALLOWED_SHARED_LOGIC_DISPOSITIONS:
                errors.append(
                    f"DESIGN-IMPLEMENTATION-DECISIONS row {index} shared logic disposition must be one of "
                    f"{sorted(ALLOWED_SHARED_LOGIC_DISPOSITIONS)}"
                )
            if not re.search(r"(?:[/\\]|\.[A-Za-z0-9]+\b|#|建议新增)", values.get("现有候选与代码证据", "")):
                errors.append(f"DESIGN-IMPLEMENTATION-DECISIONS row {index} must identify current code candidates or an explicitly proposed location")
            required_rejections = {
                "COMPATIBLE_EXTEND": ("REUSE 不适用理由",),
                "MODIFY": ("REUSE 不适用理由", "COMPATIBLE_EXTEND 不适用理由"),
                "CREATE": ("REUSE 不适用理由", "COMPATIBLE_EXTEND 不适用理由", "MODIFY 不适用理由"),
            }.get(strategy, ())
            missing_reasons = [column for column in required_rejections if not _has_reason(values.get(column, ""))]
            if missing_reasons:
                errors.append(
                    f"DESIGN-IMPLEMENTATION-DECISIONS row {index} {strategy} requires reasoned rejection: {missing_reasons}"
                )
            compatibility = values.get("兼容、调用方与验证", "")
            if not re.search(r"(?:验证|测试|命令|Test|Spec|检查)", compatibility, re.I):
                errors.append(f"DESIGN-IMPLEMENTATION-DECISIONS row {index} must name compatibility/caller verification")
            if strategy == "COMPATIBLE_EXTEND" and not re.search(r"(?:默认|旧调用|兼容)", compatibility):
                errors.append(f"DESIGN-IMPLEMENTATION-DECISIONS row {index} COMPATIBLE_EXTEND must preserve old/default behavior")
            if strategy == "MODIFY" and not re.search(r"(?:调用方|迁移|回归)", compatibility):
                errors.append(f"DESIGN-IMPLEMENTATION-DECISIONS row {index} MODIFY must identify caller, migration, or regression impact")
            if strategy == "CREATE" and disposition == "KEEP_LOCAL" and not re.search(r"(?:不抽取|局部|语义不同|边界不同)", " ".join(row)):
                errors.append(f"DESIGN-IMPLEMENTATION-DECISIONS row {index} KEEP_LOCAL must explain why shared extraction is inappropriate")

        if len(decision_ids) != len(set(decision_ids)):
            errors.append("implementation decision IDs must be unique")

        blueprint_ids: list[str] = []
        blueprint_decisions: list[str] = []
        for index, row in enumerate(blueprint_rows, 1):
            if len(row) != len(blueprint_header) or _meaningful_na(row):
                continue
            values = dict(zip(blueprint_header, row))
            blueprint_id = _cell_id(values.get("蓝图 ID", ""), BLUEPRINT_ID_RE)
            decision_id = _cell_id(values.get("决策 ID", ""), IMPLEMENTATION_DECISION_RE)
            if not blueprint_id:
                errors.append(f"DESIGN-CODE-BLUEPRINT row {index} has invalid BP-* ID")
            else:
                blueprint_ids.append(blueprint_id)
            if not decision_id:
                errors.append(f"DESIGN-CODE-BLUEPRINT row {index} has invalid IMPL-DEC-* reference")
            else:
                blueprint_decisions.append(decision_id)
            if not re.search(r"(?:\.md#|requirement\.md|design\.md|_design\.md)", values.get("来源文档", ""), re.I):
                errors.append(f"DESIGN-CODE-BLUEPRINT row {index} must link a source document and stable anchor")
        if len(blueprint_ids) != len(set(blueprint_ids)):
            errors.append("code blueprint IDs must be unique")
        unknown_decisions = sorted(set(blueprint_decisions) - set(decision_ids))
        if unknown_decisions:
            errors.append(f"code blueprints reference unknown implementation decisions: {unknown_decisions}")
        missing_blueprints = sorted(
            decision_id for decision_id, blueprint_id in decision_blueprints.items()
            if not blueprint_id or blueprint_id not in set(blueprint_ids)
        )
        if missing_blueprints:
            errors.append(f"implementation decisions reference missing code blueprints: {missing_blueprints}")

    placeholders = sorted(set(PLACEHOLDER_RE.findall(text)))
    vague_terms = sorted(set(match.group(0) for match in VAGUE_RE.finditer(text)))
    unchecked_gates = len(re.findall(r"^- \[ \]", text, flags=re.M))
    checked_gates = len(re.findall(r"^- \[[xX]\]", text, flags=re.M))

    if stage == "ready":
        if re.search(r"^>\s*状态：\s*草稿\s*$", text, flags=re.M):
            errors.append("development-ready design must change document status from 草稿")
        if placeholders:
            errors.append(f"unresolved design placeholders: {placeholders[:24]}")
        if vague_terms:
            errors.append(f"implementation-blocking vague terms remain: {vague_terms}")
        if unchecked_gates:
            errors.append(f"development readiness checklist has {unchecked_gates} unchecked item(s)")
    else:
        if placeholders:
            warnings.append(f"draft has {len(placeholders)} unresolved placeholder(s)")
        if unchecked_gates:
            warnings.append(f"draft has {unchecked_gates} unchecked development-readiness gate(s)")

    change_type_cells: list[str] = []
    for marker in ("DESIGN-CHANGE-INVENTORY", "DESIGN-TABLE-CHANGES", "DESIGN-FIELD-CHANGES"):
        header, rows = _table_after_marker(text, marker)
        if not header or "变更类型" not in header:
            continue
        column = header.index("变更类型")
        change_type_cells.extend(row[column] for row in rows if len(row) > column)
    if stage == "ready":
        invalid_types = sorted({value for value in change_type_cells if value not in ALLOWED_CHANGE_TYPES})
        if invalid_types:
            errors.append(f"invalid change types: {invalid_types}")

    counts = {
        "requiredHeadings": len(REQUIRED_HEADINGS) - len(missing_headings),
        "matrices": sum(1 for value in matrix_counts.values() if value > 0),
        "matrixRows": sum(matrix_counts.values()),
        "narratives": sum(1 for value in narrative_counts.values() if value > 0),
        "narrativeCharacters": sum(narrative_counts.values()),
        "unresolvedPlaceholders": len(placeholders),
        "checkedReadinessGates": checked_gates,
        "uncheckedReadinessGates": unchecked_gates,
        "implementationStrategyContractVersion": strategy_contract_version,
        "implementationDecisions": matrix_counts.get("DESIGN-IMPLEMENTATION-DECISIONS", 0),
    }
    return ValidationResult(str(path), stage, errors, warnings, counts)


def emit(result: ValidationResult, json_output: bool) -> None:
    if json_output:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return
    print(f"design document: {result.path}")
    print(f"stage: {result.stage}")
    print(f"valid: {'YES' if result.valid else 'NO'}")
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    for error in result.errors:
        print(f"ERROR: {error}")


def command_init(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "design_doc", "init")
    if not TEMPLATE.is_file():
        raise FileNotFoundError(f"missing design template: {TEMPLATE}")
    output = Path(args.output)
    if output.exists() and not args.overwrite:
        raise FileExistsError(f"design document already exists: {output}")
    common_utils.atomic_write_text(output, render_template(
        title=args.title,
        module_code=args.module_code,
        requirement_ref=args.requirement_ref,
        revision=args.revision,
        base_revision=args.base_revision,
        topic_id=args.topic_id,
    ))
    inferred = doc_layout.infer_doc_root(output)
    if inferred:
        artifact_relations.write_views(inferred[0])
        document_portal.write_portals(inferred[0], versions=[inferred[1]])
    payload = {"created": output.as_posix(), "stage": "draft", "next": "complete matrices and validate --stage ready"}
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else output)
    return 0


def command_validate(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "design_doc", "validate", declared=True)
    result = validate_design(Path(args.file), args.stage)
    emit(result, args.json)
    return 0 if result.valid else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="create a canonical implementation-ready design draft")
    agent_context.add_agent_argument(init)
    init.add_argument("--output", required=True)
    init.add_argument("--title", required=True)
    init.add_argument("--module-code", required=True)
    init.add_argument("--requirement-ref", required=True)
    init.add_argument("--revision", required=True)
    init.add_argument("--base-revision", default="")
    init.add_argument("--topic-id", default="", help="stable DESIGN-{MODULE_CODE}-{TOPIC}; defaults to DESIGN-{MODULE_CODE}-GENERAL")
    init.add_argument("--overwrite", action="store_true")
    init.add_argument("--json", action="store_true")
    init.set_defaults(func=command_init)

    validate = subparsers.add_parser("validate", help="validate design structure or development readiness")
    agent_context.add_agent_argument(validate)
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
