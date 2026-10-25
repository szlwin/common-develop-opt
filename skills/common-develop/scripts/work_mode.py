#!/usr/bin/env python3
"""Create, apply, and validate common-develop work.md execution modes."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

import agent_context
import work_mode_events
from error_catalog import WorkModeError as CatalogWorkModeError
from common_utils import attach_rollback_details, atomic_write_text, restore_files
from _core.mode_policy import mode_enabled, patch_from_args, resolve_mode


ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_PATH = ROOT / "assets" / "work.template.md"
MODE_PATTERN = re.compile(
    r"<!--\s*common-develop-work-mode:\s*model_code=(true|false)\s+page_design=(true|false)\s+minimal=(true|false)\s+auto=(true|false)\s+architecture_review=(true|false)\s+git_checkpoint=(true|false)\s+git_push=(true|false)\s+lightweight=(true|false)\s*-->"
)
SUMMARY_PATTERN = re.compile(r"^>\s*工作模式（wk）：.*$", re.MULTILINE)
MUTATION_POLICY_PATTERN = re.compile(r"<!--\s*common-develop-mutation-policy:\s*review_only=(true|false)\s*-->")
LIGHTWEIGHT_BACKUP_PATTERN = re.compile(r"<!--\s*common-develop-lightweight-backup:\s*(\{.*?\})\s*-->")
WORK_HEADERS = [
    "需求编号",
    "需求名称",
    "功能名称",
    "功能编号",
    "需求分析",
    "页面设计",
    "设计",
    "架构设计",
    "测试用例",
    "前端伪代码",
    "后端伪代码",
    "开发",
    "架构代码",
    "Review",
    "测试",
    "归档",
]
WORK_MODE_CONTRACT = agent_context.RUNTIME_CONTRACT.get("commandInterface", {}).get("workModes", {})
MODEL_CODE_CONTRACT = WORK_MODE_CONTRACT.get("modelCode", {})
PAGE_DESIGN_CONTRACT = WORK_MODE_CONTRACT.get("pageDesign", {})
MINIMAL_CONTRACT = WORK_MODE_CONTRACT.get("minimal", {})
AUTO_CONTRACT = WORK_MODE_CONTRACT.get("auto", {})
ARCHITECTURE_REVIEW_CONTRACT = WORK_MODE_CONTRACT.get("architectureReview", {})
GIT_CHECKPOINT_CONTRACT = WORK_MODE_CONTRACT.get("gitCheckpoint", {})
GIT_PUSH_CONTRACT = WORK_MODE_CONTRACT.get("gitPush", {})
REVIEW_ONLY_CONTRACT = WORK_MODE_CONTRACT.get("reviewOnly", {})
LIGHTWEIGHT_CONTRACT = WORK_MODE_CONTRACT.get("lightweight", {})
MODEL_CODE_COLUMNS = tuple(MODEL_CODE_CONTRACT.get("workColumns", ()))
PAGE_DESIGN_COLUMNS = tuple(PAGE_DESIGN_CONTRACT.get("workColumns", ()))
MINIMAL_COLUMNS = tuple(MINIMAL_CONTRACT.get("workColumns", ()))
ARCHITECTURE_REVIEW_COLUMNS = tuple(ARCHITECTURE_REVIEW_CONTRACT.get("workColumns", ()))
LIGHTWEIGHT_COLUMNS = tuple(LIGHTWEIGHT_CONTRACT.get("workColumns", ()))
LIGHTWEIGHT_BACKUP_COLUMNS = tuple(dict.fromkeys((*LIGHTWEIGHT_COLUMNS, *ARCHITECTURE_REVIEW_COLUMNS)))
MINIMAL_PHASES = tuple(MINIMAL_CONTRACT.get("skippedPhases", ()))
LIGHTWEIGHT_PHASES = tuple(LIGHTWEIGHT_CONTRACT.get("skippedPhases", ()))
MINIMAL_SUPPRESSED_REVIEWERS = MINIMAL_CONTRACT.get("suppressedReviewerAssignments", {})
MINIMAL_NON_TEST_EVIDENCE = tuple(MINIMAL_CONTRACT.get("requiredNonTestEvidence", ()))
MINIMAL_PHASE_OUTCOME = MINIMAL_CONTRACT.get("phaseOutcome", "")
MINIMAL_ARCHIVE_GATE_STATUS = MINIMAL_CONTRACT.get("archiveGateStatus", "")
MINIMAL_SKIP_REASON = MINIMAL_CONTRACT.get("skipReason", "")
LIGHTWEIGHT_SUPPRESSED_REVIEWERS = LIGHTWEIGHT_CONTRACT.get("suppressedReviewerAssignments", {})
LIGHTWEIGHT_NON_TEST_EVIDENCE = tuple(LIGHTWEIGHT_CONTRACT.get("requiredNonTestEvidence", ()))
LIGHTWEIGHT_PHASE_OUTCOME = LIGHTWEIGHT_CONTRACT.get("phaseOutcome", "")
LIGHTWEIGHT_ARCHIVE_GATE_STATUS = LIGHTWEIGHT_CONTRACT.get("archiveGateStatus", "")
LIGHTWEIGHT_SKIP_REASON = LIGHTWEIGHT_CONTRACT.get("skipReason", "")
LIGHTWEIGHT_ARCHIVE_POLICY = LIGHTWEIGHT_CONTRACT.get("archivePolicy", {})
AUTO_CONFIRMATION_POLICY = AUTO_CONTRACT.get("confirmationPolicy", {})
AUTO_REMEDIATION_POLICY = AUTO_CONTRACT.get("remediationPolicy", {})
AUTO_SEMANTIC_PREFLIGHT_POLICY = AUTO_CONTRACT.get("semanticPreflightPolicy", {})
AUTO_ARCHITECTURE_BOUNDARY_POLICY = AUTO_CONTRACT.get("architectureBoundaryPolicy", {})
AUTO_FINAL_HEAD_GATE_POLICY = AUTO_CONTRACT.get("finalHeadGatePolicy", {})
ARCHITECTURE_REVIEW_POLICY = ARCHITECTURE_REVIEW_CONTRACT.get("policy", {})

if (
    not MODEL_CODE_COLUMNS
    or not PAGE_DESIGN_COLUMNS
    or not MINIMAL_COLUMNS
    or not MINIMAL_PHASES
    or not AUTO_CONFIRMATION_POLICY
    or not AUTO_REMEDIATION_POLICY
    or not AUTO_SEMANTIC_PREFLIGHT_POLICY
    or not AUTO_ARCHITECTURE_BOUNDARY_POLICY
    or not AUTO_FINAL_HEAD_GATE_POLICY
    or not ARCHITECTURE_REVIEW_COLUMNS
    or not ARCHITECTURE_REVIEW_POLICY
    or not LIGHTWEIGHT_COLUMNS
    or not LIGHTWEIGHT_PHASES
    or not LIGHTWEIGHT_SUPPRESSED_REVIEWERS
    or not LIGHTWEIGHT_ARCHIVE_POLICY
    or not GIT_CHECKPOINT_CONTRACT
    or not GIT_PUSH_CONTRACT
    or not REVIEW_ONLY_CONTRACT
):
    raise RuntimeError("runtime contract work mode columns/phases are invalid")


class WorkModeError(CatalogWorkModeError):
    """Raised when work.md does not satisfy the selected mode contract."""


@dataclass(frozen=True)
class WorkMode:
    model_code: bool = False
    page_design: bool = False
    minimal: bool = False
    auto: bool = False
    architecture_review: bool = False
    git_checkpoint: bool = False
    git_push: bool = False
    review_only: bool = False
    lightweight: bool = False

    def marker(self) -> str:
        return (
            "<!-- common-develop-work-mode: "
            f"model_code={'true' if self.model_code else 'false'} "
            f"page_design={'true' if self.page_design else 'false'} "
            f"minimal={'true' if self.minimal else 'false'} "
            f"auto={'true' if self.auto else 'false'} "
            f"architecture_review={'true' if self.architecture_review else 'false'} "
            f"git_checkpoint={'true' if self.git_checkpoint else 'false'} "
            f"git_push={'true' if self.git_push else 'false'} "
            f"lightweight={'true' if self.lightweight else 'false'} -->"
        )

    def mutation_marker(self) -> str:
        return f"<!-- common-develop-mutation-policy: review_only={'true' if self.review_only else 'false'} -->"

    def summary(self) -> str:
        model_text = "启用" if self.model_code else "关闭"
        page_design_text = "启用" if self.page_design else "关闭"
        minimal_text = "启用" if self.minimal else "关闭"
        auto_text = "启用" if self.auto else "关闭"
        architecture_review_text = "启用" if self.architecture_review else "关闭"
        git_checkpoint_text = "启用" if self.git_checkpoint else "关闭"
        git_push_text = "启用" if self.git_push else "关闭"
        review_only_text = "启用" if self.review_only else "关闭"
        lightweight_text = "启用" if self.lightweight else "关闭"
        return f"> 工作模式（wk）：前后端伪代码={model_text}；页面设计={page_design_text}；最小流程={minimal_text}；自动执行={auto_text}；两阶段开发Review={architecture_review_text}；Git阶段提交={git_checkpoint_text}；Git自动推送={git_push_text}；只Review写入边界={review_only_text}；轻量对话开发={lightweight_text}。"

    def to_record(self) -> dict[str, object]:
        return {
            "modelCode": self.model_code,
            "pageDesign": self.page_design,
            "minimal": self.minimal,
            "auto": self.auto,
            "architectureReview": self.architecture_review,
            "gitCheckpoint": self.git_checkpoint,
            "gitPush": self.git_push,
            "reviewOnly": self.review_only,
            "lightweight": self.lightweight,
            "skippedPhases": list(
                LIGHTWEIGHT_PHASES if self.lightweight else (MINIMAL_PHASES if self.minimal else ())
            ),
        }


def validate_workflow_mode_compatibility(profile: str, mode: WorkMode) -> None:
    """Reject work modes that violate the selected intake workflow profile."""
    if not profile:
        return
    definition = agent_context.RUNTIME_CONTRACT.get("workflowProfiles", {}).get(profile, {})
    if not isinstance(definition, dict):
        return
    incompatible = definition.get("incompatibleWorkModes") or []
    try:
        conflicts = [str(name) for name in incompatible if mode_enabled(mode, str(name))]
    except KeyError as exc:
        raise WorkModeError(
            f"{profile} declares unknown incompatible work mode: {exc.args[0]}",
            code="WORKMODE_PROFILE_CONTRACT_INVALID",
        ) from exc
    if conflicts:
        names = {
            "minimal": "wk -min", "lightweight": "wk -ws",
            "modelCode": "wk -mc", "pageDesign": "wk -v",
            "architectureReview": "wk -ar", "gitCheckpoint": "wk -gc",
            "gitPush": "wk -gp", "reviewOnly": "wk -review-only",
        }
        selected = ", ".join(names.get(name, f"wk -{name}") for name in conflicts)
        reason = "code review and testing are mandatory" if profile == "SIMPLE_OPTIMIZATION_FLOW" else "the workflow profile forbids this mode"
        raise WorkModeError(
            f"{selected} is incompatible with {profile} because {reason}",
            code="WORKMODE_PROFILE_INCOMPATIBLE",
        )


def validate_mode_dependencies(mode: WorkMode) -> None:
    if mode.git_push and not mode.git_checkpoint:
        raise WorkModeError("wk -gp/--git-push requires wk -gc/--git-checkpoint", code="WORKMODE_GIT_PUSH_REQUIRES_CHECKPOINT")


MUTATION_SCOPES = ("taskFacts", "projectDocs", "tests", "config", "productionCode")


def mutation_scope_policy(mode: WorkMode, phase: str) -> dict[str, str]:
    """Resolve a technology-agnostic mutation boundary for the current phase."""
    if mode.review_only:
        return {
            "taskFacts": "ALLOW", "projectDocs": "ALLOW", "tests": "DENY",
            "config": "DENY", "productionCode": "DENY",
        }
    if mode.lightweight:
        return {
            "taskFacts": "ALLOW", "projectDocs": "ALLOW",
            "tests": "DENY",
            "config": "ALLOW" if phase == "development" else "DENY",
            "productionCode": "ALLOW" if phase == "development" else "DENY",
        }
    early_docs = {
        "requirement_confirmation", "requirement_analysis", "business_model", "design",
        "test_design", "implementation_plan", "development",
    }
    return {
        "taskFacts": "ALLOW",
        "projectDocs": "ALLOW" if phase in early_docs else "DENY",
        "tests": "ALLOW" if phase == "development" else "DENY",
        "config": "ALLOW" if phase == "development" else "DENY",
        "productionCode": "ALLOW" if phase == "development" else "DENY",
    }


def mutation_decision(mode: WorkMode, phase: str, scope: str) -> dict[str, object]:
    if scope not in MUTATION_SCOPES:
        raise WorkModeError(f"unknown mutation scope: {scope}", code="WORKMODE_MUTATION_SCOPE_UNKNOWN")
    policy = mutation_scope_policy(mode, phase)
    allowed = policy[scope] == "ALLOW"
    return {
        "phase": phase, "scope": scope, "allowed": allowed, "decision": policy[scope],
        "mode": "REVIEW_ONLY" if mode.review_only else ("LIGHTWEIGHT" if mode.lightweight else "STANDARD"), "policy": policy,
    }


def suppressed_reviewers(mode: str, phase: str) -> set[str]:
    """Resolve reviewer suppression from the Runtime Contract for one mode/phase."""
    if mode not in {"standard", "minimal", "lightweight"}:
        raise WorkModeError(f"unknown review mode: {mode}", code="WORKMODE_REVIEW_MODE_UNKNOWN")
    if mode == "minimal":
        mapping = MINIMAL_SUPPRESSED_REVIEWERS
    elif mode == "lightweight":
        mapping = LIGHTWEIGHT_SUPPRESSED_REVIEWERS
    else:
        mapping = {}
    values = mapping.get(phase, []) if isinstance(mapping, dict) else []
    if not isinstance(values, (list, tuple)):
        raise WorkModeError(f"invalid suppressed reviewer declaration for {mode}/{phase}", code="WORKMODE_REVIEW_SUPPRESSION_INVALID")
    return {str(value) for value in values if str(value)}


def minimal_archive_gate_keys() -> tuple[str, ...]:
    gate_map = agent_context.RUNTIME_CONTRACT.get("archiveGateMap", {})
    if not isinstance(gate_map, dict):
        raise RuntimeError("runtime contract archiveGateMap is invalid")
    try:
        return tuple(str(gate_map[phase]) for phase in MINIMAL_PHASES)
    except KeyError as exc:
        raise RuntimeError(f"runtime contract has no archive gate for minimal phase: {exc.args[0]}") from exc


def lightweight_archive_gate_keys() -> tuple[str, ...]:
    gate_map = agent_context.RUNTIME_CONTRACT.get("archiveGateMap", {})
    if not isinstance(gate_map, dict):
        raise RuntimeError("runtime contract archiveGateMap is invalid")
    return tuple(str(gate_map[phase]) for phase in LIGHTWEIGHT_PHASES if phase in gate_map)






def resolved_mode_policy(mode: WorkMode) -> dict[str, object]:
    """Return one machine-readable mode policy resolved from the runtime contract."""

    validate_mode_dependencies(mode)
    record = mode.to_record()
    lightweight = mode.lightweight
    record.update(
        {
            "phaseOutcome": LIGHTWEIGHT_PHASE_OUTCOME if lightweight else (MINIMAL_PHASE_OUTCOME if mode.minimal else "PASSED"),
            "archiveGateStatus": LIGHTWEIGHT_ARCHIVE_GATE_STATUS if lightweight else (MINIMAL_ARCHIVE_GATE_STATUS if mode.minimal else "PASSED"),
            "archiveGateKeys": list(minimal_archive_gate_keys() if mode.minimal and not lightweight else ()),
            "skipReason": LIGHTWEIGHT_SKIP_REASON if lightweight else (MINIMAL_SKIP_REASON if mode.minimal else ""),
            "suppressedReviewerAssignments": (
                LIGHTWEIGHT_SUPPRESSED_REVIEWERS if lightweight else (MINIMAL_SUPPRESSED_REVIEWERS if mode.minimal else {})
            ),
            "requiredNonTestEvidence": list(LIGHTWEIGHT_NON_TEST_EVIDENCE if lightweight else (MINIMAL_NON_TEST_EVIDENCE if mode.minimal else ())),
            "lightweightPolicy": LIGHTWEIGHT_ARCHIVE_POLICY if lightweight else {"enabled": False},
            "confirmationPolicy": AUTO_CONFIRMATION_POLICY if mode.auto else {
                "mode": "USER_CONFIRMED",
                "userConfirmationRequired": True,
                "autoAdvance": False,
            },
            "autoRemediationPolicy": AUTO_REMEDIATION_POLICY if mode.auto else {"enabled": False},
            "semanticPreflightPolicy": AUTO_SEMANTIC_PREFLIGHT_POLICY if mode.auto else {"enabled": False},
            "architectureBoundaryPolicy": AUTO_ARCHITECTURE_BOUNDARY_POLICY if mode.auto else {"enabled": False},
            "finalHeadGatePolicy": AUTO_FINAL_HEAD_GATE_POLICY if mode.auto else {"enabled": False},
            "architectureReviewPolicy": ARCHITECTURE_REVIEW_POLICY if mode.architecture_review else {"enabled": False},
            "executionPolicy": agent_context.RUNTIME_CONTRACT.get("executionPolicy", {}),
            "gitCheckpointPolicy": GIT_CHECKPOINT_CONTRACT.get("policy", {}) if mode.git_checkpoint else {
                "enabled": False,
                "autoCommit": False,
            },
            "gitPushPolicy": GIT_PUSH_CONTRACT.get("policy", {}) if mode.git_push else {
                "enabled": False,
                "autoPush": False,
                "verifyRemoteHead": False,
            },
            "mutationPolicy": REVIEW_ONLY_CONTRACT.get("policy", {}) if mode.review_only else {
                "mode": "STANDARD",
                "taskFacts": "ALLOW",
                "projectDocs": "PHASE_CONTROLLED",
                "tests": "PHASE_CONTROLLED",
                "config": "PHASE_CONTROLLED",
                "productionCode": "PHASE_CONTROLLED",
            },
        }
    )
    return record


def work_path(doc_root: Path, version: str) -> Path:
    return doc_root / "version" / version / "work.md"


def _cells(line: str) -> list[str] | None:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return None
    return [value.strip() for value in stripped[1:-1].split("|")]


def _separator(cells: Iterable[str]) -> bool:
    values = list(cells)
    return bool(values) and all(re.fullmatch(r":?-{3,}:?", value or "") for value in values)


def _render_row(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _find_work_table(lines: list[str]) -> tuple[int, dict[str, int]]:
    for index, line in enumerate(lines):
        values = _cells(line)
        if values is None:
            continue
        if all(header in values for header in WORK_HEADERS):
            if len(values) != len(set(values)):
                raise WorkModeError("work.md 工作表表头存在重复列", code="WORKMODE_TABLE_INVALID")
            return index, {header: values.index(header) for header in WORK_HEADERS}
    raise WorkModeError("work.md 缺少规定的工作分工表头", code="WORKMODE_TABLE_INVALID")




def _iter_work_rows(
    lines: list[str], header_index: int, table_width: int
) -> Iterable[tuple[int, list[str]]]:
    """Yield work-table data rows, tolerating blank lines around the table."""
    for line_index in range(header_index + 1, len(lines)):
        stripped = lines[line_index].strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            break
        row = _cells(lines[line_index])
        if row is None:
            break
        if len(row) != table_width:
            raise WorkModeError(
                f"work.md 第 {line_index + 1} 行列数与表头不一致",
                code="WORKMODE_TABLE_ROW_INVALID",
            )
        if _separator(row):
            continue
        yield line_index, row

def parse_mode(text: str) -> WorkMode:
    current = MODE_PATTERN.findall(text)
    if len(current) != 1:
        raise WorkModeError(
            "work.md 必须且只能包含一个 common-develop 工作模式标记",
            code="WORKMODE_MARKER_INVALID",
        )
    policy_matches = MUTATION_POLICY_PATTERN.findall(text)
    if len(policy_matches) > 1:
        raise WorkModeError(
            "work.md 最多包含一个 common-develop mutation policy 标记",
            code="WORKMODE_MUTATION_MARKER_INVALID",
        )
    model_code, page_design, minimal, auto, architecture_review, git_checkpoint, git_push, lightweight = current[0]
    mode = WorkMode(
        model_code=model_code == "true", page_design=page_design == "true", minimal=minimal == "true",
        auto=auto == "true", architecture_review=architecture_review == "true", git_checkpoint=git_checkpoint == "true",
        git_push=git_push == "true",
        review_only=bool(policy_matches and policy_matches[0] == "true"),
        lightweight=lightweight == "true",
    )
    validate_mode_dependencies(mode)
    return mode


def render_template(version: str, mode: WorkMode = WorkMode()) -> str:
    text = TEMPLATE_PATH.read_text(encoding="utf-8").replace("{VERSION}", version)
    text = MODE_PATTERN.sub(mode.marker(), text, count=1)
    if MUTATION_POLICY_PATTERN.search(text):
        text = MUTATION_POLICY_PATTERN.sub(mode.mutation_marker(), text, count=1)
    else:
        text = mode.mutation_marker() + "\n" + text
    text = SUMMARY_PATTERN.sub(mode.summary(), text, count=1)
    return text


def ensure_work_file(doc_root: Path, version: str, mode: WorkMode = WorkMode()) -> Path:
    path = work_path(doc_root, version)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        # Seed lightweight work with a snapshot of the requested non-lightweight
        # assignments.  Callers commonly ensure the file before apply_mode(),
        # so the snapshot must already exist when the first mode is -ws.
        seed_mode = replace(mode, lightweight=False) if mode.lightweight else mode
        text = render_template(version, seed_mode)
        if mode.lightweight:
            text = _with_lightweight_backup(text, _lightweight_backup(text))
            text = apply_mode_text(text, mode)
        atomic_write_text(path, text)
    return path


def _work_table_rows(text: str) -> tuple[list[str], int, dict[str, int], int]:
    lines = text.splitlines()
    header_index, indexes = _find_work_table(lines)
    table_width = len(_cells(lines[header_index]) or [])
    return lines, header_index, indexes, table_width


def _lightweight_backup(text: str) -> dict[str, object]:
    lines, header_index, indexes, table_width = _work_table_rows(text)
    rows = [
        [row[indexes[name]] for name in LIGHTWEIGHT_BACKUP_COLUMNS]
        for _, row in _iter_work_rows(lines, header_index, table_width)
    ]
    return {"version": 1, "columns": list(LIGHTWEIGHT_BACKUP_COLUMNS), "rows": rows}


def _read_lightweight_backup(text: str) -> dict[str, object] | None:
    matches = list(LIGHTWEIGHT_BACKUP_PATTERN.finditer(text))
    if not matches:
        return None
    if len(matches) != 1:
        raise WorkModeError("work.md 轻量模式分工快照必须且只能包含一个", code="WORKMODE_BACKUP_INVALID")
    try:
        backup = json.loads(matches[0].group(1))
    except json.JSONDecodeError as exc:
        raise WorkModeError(
            f"work.md 轻量模式分工快照格式无效: {exc}",
            code="WORKMODE_BACKUP_FORMAT_INVALID",
        ) from exc
    if not isinstance(backup, dict) or backup.get("version") != 1 or backup.get("columns") != list(LIGHTWEIGHT_BACKUP_COLUMNS):
        raise WorkModeError(
            "work.md 轻量模式分工快照版本或列定义无效",
            code="WORKMODE_BACKUP_COLUMNS_INVALID",
        )
    rows = backup.get("rows")
    if not isinstance(rows, list) or any(not isinstance(row, list) or len(row) != len(LIGHTWEIGHT_BACKUP_COLUMNS) or any(not isinstance(value, str) for value in row) for row in rows):
        raise WorkModeError("work.md 轻量模式分工快照行数据无效", code="WORKMODE_BACKUP_ROWS_INVALID")
    return backup


def _restore_lightweight_backup(text: str, mode: WorkMode) -> str:
    backup = _read_lightweight_backup(text)
    if backup is None:
        raise WorkModeError("work.md 启用轻量模式但缺少分工快照，拒绝关闭以避免数据丢失", code="WORKMODE_LIGHTWEIGHT_BACKUP_MISSING")
    lines, header_index, indexes, table_width = _work_table_rows(text)
    rows = list(_iter_work_rows(lines, header_index, table_width))
    saved_rows = backup["rows"]
    if len(rows) != len(saved_rows):
        raise WorkModeError("work.md 轻量模式分工快照与当前工作表行数不一致", code="WORKMODE_LIGHTWEIGHT_BACKUP_DRIFT")
    for (line_index, row), saved in zip(rows, saved_rows):
        for name, value in zip(LIGHTWEIGHT_BACKUP_COLUMNS, saved):
            # A direct transition to a mode without -ar must keep 架构代码
            # disabled; -wc normally carries the existing -ar flag through
            # mode_from_args, so an active architecture review is restored.
            if name in ARCHITECTURE_REVIEW_COLUMNS and not mode.architecture_review:
                continue
            row[indexes[name]] = value
        lines[line_index] = _render_row(row)
    return LIGHTWEIGHT_BACKUP_PATTERN.sub("", "\n".join(lines), count=1)


def _with_lightweight_backup(text: str, backup: dict[str, object]) -> str:
    marker = "<!-- common-develop-lightweight-backup: " + json.dumps(backup, ensure_ascii=False, separators=(",", ":")) + " -->"
    if LIGHTWEIGHT_BACKUP_PATTERN.search(text):
        return LIGHTWEIGHT_BACKUP_PATTERN.sub(marker, text, count=1)
    return marker + "\n" + text

def apply_mode_text(text: str, mode: WorkMode) -> str:
    lines = text.splitlines()
    header_index, indexes = _find_work_table(lines)
    table_width = len(_cells(lines[header_index]) or [])

    for line_index, row in _iter_work_rows(lines, header_index, table_width):
        if mode.model_code:
            for name in MODEL_CODE_COLUMNS:
                column = indexes[name]
                if not row[column] or row[column] == "无":
                    row[column] = "AIW"
        else:
            for name in MODEL_CODE_COLUMNS:
                row[indexes[name]] = "无"
        if mode.page_design:
            for name in PAGE_DESIGN_COLUMNS:
                column = indexes[name]
                if not row[column] or row[column] == "无":
                    row[column] = "AIW"
        else:
            for name in PAGE_DESIGN_COLUMNS:
                row[indexes[name]] = "无"
        if mode.architecture_review:
            for name in ARCHITECTURE_REVIEW_COLUMNS:
                column = indexes[name]
                if not row[column] or row[column] == "无":
                    row[column] = "AIW"
        else:
            for name in ARCHITECTURE_REVIEW_COLUMNS:
                row[indexes[name]] = "无"
        if mode.minimal:
            for name in MINIMAL_COLUMNS:
                row[indexes[name]] = "无"
        if mode.lightweight:
            for name in LIGHTWEIGHT_COLUMNS:
                row[indexes[name]] = "无"
        lines[line_index] = _render_row(row)

    new_text = "\n".join(lines)
    marker = mode.marker()
    validate_mode_dependencies(mode)
    if not MODE_PATTERN.search(new_text):
        raise WorkModeError("work.md 缺少 common-develop 工作模式标记", code="WORKMODE_MARKER_MISSING")
    new_text = MODE_PATTERN.sub(marker, new_text, count=1)
    mutation_marker = mode.mutation_marker()
    if not MUTATION_POLICY_PATTERN.search(new_text):
        raise WorkModeError("work.md 缺少 common-develop mutation policy 标记", code="WORKMODE_MUTATION_MARKER_MISSING")
    new_text = MUTATION_POLICY_PATTERN.sub(mutation_marker, new_text, count=1)
    summary = mode.summary()
    if SUMMARY_PATTERN.search(new_text):
        new_text = SUMMARY_PATTERN.sub(summary, new_text, count=1)
    else:
        title_end = new_text.find("\n")
        if title_end >= 0:
            new_text = new_text[: title_end + 1] + "\n" + summary + new_text[title_end + 1 :]
        else:
            new_text += "\n\n" + summary
    return new_text.rstrip() + "\n"


def apply_mode(path: Path, mode: WorkMode) -> WorkMode:
    if not path.is_file():
        raise WorkModeError(f"work.md 不存在：{path}", code="WORKMODE_FILE_MISSING")
    original = path.read_text(encoding="utf-8")
    previous = parse_mode(original)
    text = original
    if previous.lightweight and not mode.lightweight:
        text = _restore_lightweight_backup(text, mode)
    elif mode.lightweight and not previous.lightweight:
        text = _with_lightweight_backup(text, _lightweight_backup(text))
    text = apply_mode_text(text, mode)
    atomic_write_text(path, text)
    validate_work_file(path)
    return mode


def validate_work_text(text: str) -> WorkMode:
    mode = parse_mode(text)
    summaries = [match.group(0).strip() for match in SUMMARY_PATTERN.finditer(text)]
    if len(summaries) != 1:
        raise WorkModeError("work.md 必须且只能包含一个工作模式摘要", code="WORKMODE_SUMMARY_INVALID")
    expected_summary = mode.summary()
    if summaries[0] != expected_summary:
        raise WorkModeError("work.md 工作模式摘要与 common-develop 标记不一致", code="WORKMODE_SUMMARY_DRIFT")
    if mode.lightweight:
        _read_lightweight_backup(text)
    lines = text.splitlines()
    header_index, indexes = _find_work_table(lines)
    table_width = len(_cells(lines[header_index]) or [])
    for line_index, row in _iter_work_rows(lines, header_index, table_width):
        if not mode.model_code:
            for name in MODEL_CODE_COLUMNS:
                if row[indexes[name]] != "无":
                    raise WorkModeError(f"未使用 wk -mc 时，{name}必须填写为无", code="WORKMODE_COLUMN_INVALID")
        else:
            for name in MODEL_CODE_COLUMNS:
                if row[indexes[name]] in {"", "无"}:
                    raise WorkModeError(f"使用 wk -mc 时，{name}必须进入工作范围", code="WORKMODE_COLUMN_INVALID")
        if not mode.page_design:
            for name in PAGE_DESIGN_COLUMNS:
                if row[indexes[name]] != "无":
                    raise WorkModeError(f"未使用 wk -v 时，{name}必须填写为无", code="WORKMODE_COLUMN_INVALID")
        else:
            for name in PAGE_DESIGN_COLUMNS:
                if row[indexes[name]] in {"", "无"}:
                    raise WorkModeError(f"使用 wk -v 时，{name}必须进入工作范围", code="WORKMODE_COLUMN_INVALID")
        if not mode.architecture_review:
            for name in ARCHITECTURE_REVIEW_COLUMNS:
                if row[indexes[name]] != "无":
                    raise WorkModeError(f"未使用 wk -ar 时，{name}必须填写为无", code="WORKMODE_COLUMN_INVALID")
        else:
            for name in ARCHITECTURE_REVIEW_COLUMNS:
                if row[indexes[name]] in {"", "无"}:
                    raise WorkModeError(f"使用 wk -ar 时，{name}必须进入工作范围", code="WORKMODE_COLUMN_INVALID")
        if mode.minimal:
            for name in MINIMAL_COLUMNS:
                if row[indexes[name]] != "无":
                    raise WorkModeError(f"使用 wk -min 时，{name}必须填写为无", code="WORKMODE_COLUMN_INVALID")
        if mode.lightweight:
            for name in LIGHTWEIGHT_COLUMNS:
                if row[indexes[name]] != "无":
                    raise WorkModeError(f"使用 wk -ws 时，{name}必须填写为无", code="WORKMODE_COLUMN_INVALID")
    return mode


def validate_work_file(path: Path) -> WorkMode:
    if not path.is_file():
        raise WorkModeError(f"work.md 不存在：{path}", code="WORKMODE_FILE_MISSING")
    return validate_work_text(path.read_text(encoding="utf-8"))


def load_mode(doc_root: Path, version: str, *, create_default: bool = False) -> tuple[WorkMode, Path]:
    path = work_path(doc_root, version)
    if create_default:
        ensure_work_file(doc_root, version)
    return validate_work_file(path), path


def existing_task_state_paths(doc_root: Path, version: str) -> list[Path]:
    """Return current task manifest paths for tasks with canonical runtime data."""
    task_root = doc_root / "version" / version / "task"
    if not task_root.is_dir():
        return []
    result: list[Path] = []
    for task_dir in sorted(path for path in task_root.iterdir() if path.is_dir()):
        cache = task_dir / "cache" / "current_manifest.json"
        ledger = task_dir / "task_events.jsonl"
        plans = (task_dir / "task_plan.json", task_dir / "development_task_plan.json")
        if ledger.is_file() or any(plan.is_file() for plan in plans):
            result.append(cache)
    return result


def _task_dir_for_state_path(state_path: Path) -> Path:
    return state_path.parent.parent if state_path.parent.name == "cache" else state_path.parent


def existing_task_runtime_paths(doc_root: Path, version: str) -> list[Path]:
    paths: list[Path] = []
    for state_path in existing_task_state_paths(doc_root, version):
        task_dir = _task_dir_for_state_path(state_path)
        for candidate in (
            state_path,
            task_dir / "task_plan.json",
            task_dir / "development_task_plan.json",
            task_dir / "task_events.jsonl",
        ):
            if candidate.exists():
                paths.append(candidate)
    return sorted(set(paths))


def sync_existing_tasks(doc_root: Path, version: str, agent: str) -> list[str]:
    # Local import avoids a module cycle because long_task imports work_mode.
    import long_task

    task_dirs: list[Path] = []
    for state_path in existing_task_state_paths(doc_root, version):
        task_dir = _task_dir_for_state_path(state_path)
        try:
            long_task.runtime_state(task_dir)
        except (OSError, ValueError) as exc:
            raise WorkModeError(
                f"invalid task runtime: {task_dir}: {exc}",
                code="WORKMODE_TASK_RUNTIME_INVALID",
            ) from exc
        missing = [name for name in ("task_events.jsonl",) if not (task_dir / name).is_file()]
        if missing:
            raise WorkModeError(
                f"invalid task runtime: {task_dir} (missing={missing})",
                code="WORKMODE_TASK_RUNTIME_INVALID",
            )
        task_dirs.append(task_dir)

    synced: list[str] = []
    for task_dir in task_dirs:
        long_task.command_sync_work_mode(argparse.Namespace(task_dir=str(task_dir), agent=agent))
        synced.append(str(task_dir))
    return synced


def validate_request_mode_compatibility(doc_root: Path, version: str, mode: WorkMode) -> None:
    task_root = doc_root / "version" / version / "task"
    if not task_root.is_dir():
        return
    conflicts: list[str] = []
    for intake_path in sorted(task_root.glob("*/request_intake.json")):
        try:
            value = json.loads(intake_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WorkModeError(
                f"invalid request_intake.json: {intake_path}: {exc}",
                code="WORKMODE_REQUEST_INTAKE_INVALID",
            ) from exc
        if isinstance(value, dict):
            try:
                validate_workflow_mode_compatibility(str(value.get("workflowProfile") or ""), mode)
            except WorkModeError as exc:
                conflicts.append(f"{intake_path.parent.name}: {exc}")
    if conflicts:
        raise WorkModeError("\n".join(conflicts), code="WORKMODE_PROFILE_INCOMPATIBLE")

def apply_mode_transaction(
    doc_root: Path, version: str, path: Path, mode: WorkMode, *, sync_tasks: bool, agent: str
) -> list[str]:
    event_file = work_mode_events.event_path(doc_root, version)
    tracked = [path, event_file, *existing_task_runtime_paths(doc_root, version)]
    backups = {item: item.read_bytes() for item in tracked if item.exists()}
    missing = {item for item in tracked if not item.exists()}
    # Capture the pre-transaction mode before ensure_work_file() can seed a new
    # version.  This makes the first requested mode an auditable change from
    # the canonical default instead of silently losing the initial event.
    before = validate_work_file(path) if path.is_file() else WorkMode()
    try:
        validate_request_mode_compatibility(doc_root, version, mode)
        ensure_work_file(doc_root, version, mode)
        apply_mode(path, mode)
        synced = sync_existing_tasks(doc_root, version, agent) if sync_tasks else []
        work_mode_events.append_change(doc_root, version, before.to_record(), mode.to_record(), agent=agent, source="work_mode.apply")
        return synced
    except Exception as original_error:
        details = restore_files({**backups, **{item: None for item in missing}})
        attach_rollback_details(original_error, details)
        raise


def add_mode_arguments(parser: argparse.ArgumentParser, *, include_sync: bool = False) -> None:
    """Register the shared incremental mode patch arguments."""
    mode_specs = (
        ("model_code", "-mc", "--no-mc", "require frontend and backend pseudocode"),
        ("page_design", "-v", "--no-v", "include page business behavior and interaction design"),
        ("minimal", "-min", "--no-min", "skip all test phases"),
        ("auto", "-auto", "--no-auto", "continue the Skill workflow without user confirmation"),
        ("architecture_review", "-ar", "--no-ar", "split development into reviewed architecture skeleton and concrete implementation steps"),
        ("git_checkpoint", "-gc", "--no-gc", "commit a local Git checkpoint after each passed phase gate"),
        ("git_push", "-gp", "--no-gp", "push each successful Git checkpoint to its configured remote branch and verify the remote head"),
        ("review_only", "-review-only", "--no-review-only", "allow task facts/docs only and deny test/config/production mutations"),
    )
    for dest, enable_flag, disable_flag, help_text in mode_specs:
        group = parser.add_mutually_exclusive_group()
        group.add_argument(enable_flag, dest=dest, action="store_const", const=True, default=None, help=help_text)
        group.add_argument(disable_flag, dest=dest, action="store_const", const=False, help=f"disable {enable_flag}")
    lightweight = parser.add_mutually_exclusive_group()
    lightweight.add_argument("-ws", dest="lightweight", action="store_const", const=True, default=None, help="enable conversational pair-programming mode")
    lightweight.add_argument("-wc", "--no-ws", dest="lightweight", action="store_const", const=False, help="close conversational pair-programming mode")
    if include_sync:
        sync_group = parser.add_mutually_exclusive_group()
        sync_group.add_argument(
            "--sync-existing-tasks", dest="sync_existing_tasks", action="store_true", default=True,
            help="atomically synchronize all existing task event runtimes after changing work.md (default)",
        )
        sync_group.add_argument(
            "--no-sync-existing-tasks", dest="sync_existing_tasks", action="store_false",
            help="do not synchronize existing task event runtimes",
        )


def add_scope_arguments(parser: argparse.ArgumentParser, *, include_modes: bool) -> None:
    agent_context.add_agent_argument(parser)
    parser.add_argument("--doc-root", required=True, help="project document root")
    parser.add_argument("--version", required=True, help="version code, e.g. V_X.Y")
    if include_modes:
        add_mode_arguments(parser, include_sync=True)


def mode_from_args(args: argparse.Namespace, current: WorkMode | None = None) -> WorkMode:
    if current is None:
        current = WorkMode()
    return resolve_mode(current, patch_from_args(args))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init_parser = commands.add_parser("init", help="create work.md and apply selected modes")
    add_scope_arguments(init_parser, include_modes=True)
    apply_parser = commands.add_parser("apply", help="apply selected modes to work.md")
    add_scope_arguments(apply_parser, include_modes=True)
    validate_parser = commands.add_parser("validate", help="validate work.md against its declared modes")
    add_scope_arguments(validate_parser, include_modes=False)
    status_parser = commands.add_parser("status", help="show declared work modes and resolved policy")
    add_scope_arguments(status_parser, include_modes=False)
    status_parser.add_argument("--json", action="store_true", help="emit machine-readable mode policy")
    return parser


def _scope(args: argparse.Namespace) -> tuple[Path, str, Path]:
    doc_root = Path(args.doc_root)
    version = str(args.version)
    return doc_root, version, work_path(doc_root, version)


def command_init(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "work_mode", "init")
    doc_root, version, path = _scope(args)
    mode = mode_from_args(args, validate_work_file(path) if path.is_file() else None)
    created = not path.exists()
    synced = apply_mode_transaction(
        doc_root, version, path, mode, sync_tasks=bool(getattr(args, "sync_existing_tasks", True)), agent=agent
    )
    print(f"{'CREATED' if created else 'UPDATED'} {path}")
    if synced:
        print(f"SYNCED_TASKS {len(synced)}")
    print(f"MODE model_code={str(mode.model_code).lower()} page_design={str(mode.page_design).lower()} minimal={str(mode.minimal).lower()} auto={str(mode.auto).lower()} architecture_review={str(mode.architecture_review).lower()} git_checkpoint={str(mode.git_checkpoint).lower()} git_push={str(mode.git_push).lower()} review_only={str(mode.review_only).lower()} lightweight={str(mode.lightweight).lower()}")
    return 0


def command_apply(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "work_mode", "apply")
    doc_root, version, path = _scope(args)
    mode = mode_from_args(args, validate_work_file(path) if path.is_file() else None)
    synced = apply_mode_transaction(
        doc_root, version, path, mode, sync_tasks=bool(getattr(args, "sync_existing_tasks", True)), agent=agent
    )
    print(f"UPDATED {path}")
    if synced:
        print(f"SYNCED_TASKS {len(synced)}")
    print(f"MODE model_code={str(mode.model_code).lower()} page_design={str(mode.page_design).lower()} minimal={str(mode.minimal).lower()} auto={str(mode.auto).lower()} architecture_review={str(mode.architecture_review).lower()} git_checkpoint={str(mode.git_checkpoint).lower()} git_push={str(mode.git_push).lower()} review_only={str(mode.review_only).lower()} lightweight={str(mode.lightweight).lower()}")
    return 0


def command_validate(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "work_mode", "validate")
    _, _, path = _scope(args)
    mode = validate_work_file(path)
    print(f"VALID {path} model_code={str(mode.model_code).lower()} page_design={str(mode.page_design).lower()} minimal={str(mode.minimal).lower()} auto={str(mode.auto).lower()} architecture_review={str(mode.architecture_review).lower()} git_checkpoint={str(mode.git_checkpoint).lower()} git_push={str(mode.git_push).lower()} review_only={str(mode.review_only).lower()} lightweight={str(mode.lightweight).lower()}")
    return 0


def command_status(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "work_mode", "status")
    _, _, path = _scope(args)
    mode = validate_work_file(path)
    policy = resolved_mode_policy(mode)
    if getattr(args, "json", False):
        print(json.dumps({"workRef": str(path), **policy}, ensure_ascii=False, indent=2))
        return 0
    print(f"WORK {path}")
    print(f"model_code={str(mode.model_code).lower()}")
    print(f"page_design={str(mode.page_design).lower()}")
    print(f"minimal={str(mode.minimal).lower()}")
    print(f"auto={str(mode.auto).lower()}")
    print(f"architecture_review={str(mode.architecture_review).lower()}")
    print(f"git_checkpoint={str(mode.git_checkpoint).lower()}")
    print(f"review_only={str(mode.review_only).lower()}")
    print(f"lightweight={str(mode.lightweight).lower()}")
    print("mutation_policy=" + json.dumps(policy["mutationPolicy"], ensure_ascii=False, sort_keys=True))
    print("skipped_phases=" + ",".join(policy["skippedPhases"]))
    print("suppressed_reviewers=" + json.dumps(policy["suppressedReviewerAssignments"], ensure_ascii=False, sort_keys=True))
    print("required_non_test_evidence=" + ",".join(policy["requiredNonTestEvidence"]))
    print("confirmation_policy=" + json.dumps(policy["confirmationPolicy"], ensure_ascii=False, sort_keys=True))
    print("architecture_review_policy=" + json.dumps(policy["architectureReviewPolicy"], ensure_ascii=False, sort_keys=True))
    print("git_checkpoint_policy=" + json.dumps(policy["gitCheckpointPolicy"], ensure_ascii=False, sort_keys=True))
    print(f"git_push={str(mode.git_push).lower()}")
    print("git_push_policy=" + json.dumps(policy["gitPushPolicy"], ensure_ascii=False, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    commands = {
        "init": command_init,
        "apply": command_apply,
        "validate": command_validate,
        "status": command_status,
    }
    try:
        return commands[args.command](args)
    except (WorkModeError, agent_context.AgentAuthorizationError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
