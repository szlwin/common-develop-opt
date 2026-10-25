"""Read, validate, and render compact version-level work events.

The canonical event store is a human-readable ``work_record.md`` document.
Each visible event block is paired with compact machine metadata and a body digest,
so people can read the history directly without maintaining a duplicate JSONL file.
"""
from __future__ import annotations

import copy
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from common_utils import canonical_json, sha256_text as _sha256_text

WORK_RECORD_SCHEMA_VERSION = 4
FORMAT_NAME = "v5-markdown"
V5_MARKER = "<!-- managed-by: common-develop/work-record-v5 -->"
EVENTS_START = "<!-- work-record-events-start -->"
EVENTS_END = "<!-- work-record-events-end -->"

META_PATTERN = re.compile(r"<!--\s*work-record-meta:\s*(\{.*?\})\s*-->", re.DOTALL)
TABLE_ROW_PATTERN = re.compile(r"^\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|\s*$")
VERSION_HEADING_PATTERN = re.compile(r"^#\s+(.+?)\s+工作记录\s*$", re.MULTILINE)
RECORD_ID_PATTERN = re.compile(r"^WR-\d{8}-\d{6}-[A-Za-z0-9_.-]+$")

LIST_FIELDS = (
    "stage_outcome_refs", "evidence_ids", "review_result_refs", "issue_ids",
    "git_checkpoint_refs", "blockers", "modified_files_summary",
)
STRING_FIELDS = (
    "timestamp", "event_type", "status", "agent", "source", "target_id", "scope",
    "phase", "task_type", "execution_mode", "task_id", "attempt_id", "iteration_id",
    "input_revision", "output_revision", "summary", "state_change",
    "sql_change_summary", "validation_summary", "next_action", "next_agent",
    "correction_of",
)
REQUIRED_FIELDS = (
    "schema_version", "record_id", "event_id", "timestamp", "event_type",
    "status", "agent", "version", "summary", "next_action",
)


def sha256_text(text: str) -> str:
    """Hash text using the shared canonical digest helper."""
    return _sha256_text(text)


def parse_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass
    for fmt in (
        "%Y-%m-%d %H:%M:%S %Z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def compact_timestamp(value: str) -> str:
    parsed = parse_timestamp(value)
    if parsed is not None:
        return parsed.strftime("%Y%m%d-%H%M%S")
    digits = re.sub(r"\D", "", str(value or ""))
    return (digits + "00000000000000")[:8] + "-" + (digits + "00000000000000")[8:14]


def safe_token(value: Any, fallback: str = "EVENT") -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-.")
    return token or fallback


def make_record_id(event: dict[str, Any], index: int = 1) -> str:
    existing = str(event.get("record_id") or "").strip()
    if existing:
        return existing
    event_id = str(event.get("event_id") or "").strip()
    if event_id.startswith("WR-") and RECORD_ID_PATTERN.fullmatch(event_id):
        return event_id
    subject = event.get("task_id") or event.get("target_id") or event.get("event_type") or f"EVENT-{index:04d}"
    status = event.get("status") or "RECORDED"
    return f"WR-{compact_timestamp(str(event.get('timestamp') or ''))}-{safe_token(subject)}-{safe_token(status)}"


def normalize_event(event: dict[str, Any], *, version: str = "", index: int = 1) -> dict[str, Any]:
    value = copy.deepcopy(event)
    value.pop("render_digest", None)
    value["schema_version"] = WORK_RECORD_SCHEMA_VERSION
    value["version"] = str(value.get("version") or version or "未登记")
    value["record_id"] = make_record_id(value, index)
    value["event_id"] = str(value.get("event_id") or value["record_id"])
    for key in STRING_FIELDS:
        if key in value:
            value[key] = str(value.get(key) or "")
    value.setdefault("source", "未登记")
    value.setdefault("scope", value.get("target_id") or "未登记")
    value.setdefault("task_type", value.get("phase") or value.get("event_type") or "未登记")
    value.setdefault("execution_mode", "未登记")
    value.setdefault("state_change", "未登记")
    value.setdefault("sql_change_summary", "无 / 未登记")
    if not value.get("validation_summary"):
        evidence_count = len(value.get("evidence_ids") or []) if isinstance(value.get("evidence_ids"), list) else 0
        value["validation_summary"] = f"登记 Evidence {evidence_count} 项" if evidence_count else "未登记"
    value.setdefault("next_action", "无 / 未登记")
    value.setdefault("next_agent", "未登记")
    try:
        value["iteration_no"] = int(value.get("iteration_no") or 0)
    except (TypeError, ValueError):
        value["iteration_no"] = 0
    for key in LIST_FIELDS:
        current = value.get(key, [])
        if current is None:
            current = []
        if not isinstance(current, list):
            current = [str(current)]
        value[key] = [str(item) for item in current if str(item).strip()]
    return value


def compact_event(event: dict[str, Any], *, version: str = "", index: int = 1) -> dict[str, Any]:
    """Return the canonical minimal record written to JSONL."""
    value = normalize_event(event, version=version, index=index)
    compact = {key: value[key] for key in REQUIRED_FIELDS}
    for key in (
        "source", "target_id", "scope", "phase", "task_type", "execution_mode",
        "task_id", "attempt_id", "iteration_id", "input_revision", "output_revision",
        "state_change", "sql_change_summary", "validation_summary", "next_agent",
        "correction_of",
    ):
        current = value.get(key)
        if current not in (None, "", "未登记", "无 / 未登记"):
            compact[key] = current
    if value.get("iteration_no"):
        compact["iteration_no"] = value["iteration_no"]
    for key in LIST_FIELDS:
        if value.get(key):
            compact[key] = value[key]
    return compact


def canonical_line(event: dict[str, Any], *, version: str = "", index: int = 1) -> str:
    return canonical_json(compact_event(event, version=version, index=index))




def render_document(version: str, events: Iterable[dict[str, Any]]) -> str:
    """Render the canonical, human-readable Markdown work record."""
    normalized = [normalize_event(event, version=version, index=index) for index, event in enumerate(events, 1)]
    lines = [
        f"# {version} 工作记录",
        "",
        V5_MARKER,
        "",
        "> 本文件记录当前版本跨任务的工作摘要，只追加，不覆盖。",
        ">",
        "> 单次执行细节位于 `task/{TARGET_ID}/task_events.jsonl`；Evidence、Review、StageOutcome 和 Git 检查点仅通过 ID 引用。",
        "",
        EVENTS_START,
    ]
    for event in normalized:
        body = _render_v3_body(event)
        metadata = compact_event(event, version=version)
        metadata["render_digest"] = sha256_text(body)
        lines.extend([
            "<!-- work-record-meta: " + canonical_json(metadata) + " -->",
            body.rstrip(),
            "",
        ])
    lines.extend([
        EVENTS_END,
        "",
        "## 使用规则",
        "",
        "- 人类直接阅读本文件；AI 使用 `long_task.py work-events --json` 按隐藏元数据读取。",
        "- 所有记录必须通过 `finish-attempt` 或 `append-work-event` 追加，禁止手工覆盖历史。",
        "- `task_events.jsonl` 保存 attempt、StageOutcome、stale 与 auto-remediation 事件；本文件仅保存版本级摘要与索引。",
        "- 更正通过新增记录并填写 `correction_of`，不得修改旧记录。",
        "- `validate-work-record` 会校验隐藏元数据、可读正文和 SHA-256 一致性。",
        "",
    ])
    return "\n".join(lines)


def parse_v5(text: str, *, version: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    errors: list[str] = []
    raw_events: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    matches = list(META_PATTERN.finditer(text))
    for index, match in enumerate(matches, 1):
        try:
            raw = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            errors.append(f"work-record metadata[{index - 1}] is invalid JSON: {exc}")
            continue
        if not isinstance(raw, dict):
            errors.append(f"work-record metadata[{index - 1}] must be an object")
            continue
        body_start = match.end()
        boundary = matches[index].start() if index < len(matches) else text.find(EVENTS_END, body_start)
        body_end = boundary if boundary >= 0 else len(text)
        actual_body = text[body_start:body_end].lstrip("\n").rstrip() + "\n"
        metadata = dict(raw)
        digest = str(metadata.pop("render_digest", "") or "")
        event = normalize_event(metadata, version=version, index=index)
        expected = _render_v3_body(event)
        if not digest:
            errors.append(f"{event.get('record_id') or index}: missing visible body digest")
        elif digest != sha256_text(actual_body):
            errors.append(f"{event.get('record_id') or index}: visible body digest mismatch")
        if actual_body != expected:
            errors.append(f"{event.get('record_id') or index}: visible body differs from canonical rendering")
        raw_events.append(metadata)
        events.append(event)
    if V5_MARKER in text and EVENTS_START not in text:
        errors.append("work_record.md is missing the event-start marker")
    if V5_MARKER in text and EVENTS_END not in text:
        errors.append("work_record.md is missing the event-end marker")
    return raw_events, events, errors

def table_escape(value: Any) -> str:
    if isinstance(value, list):
        value = "、".join(str(item) for item in value) if value else "无"
    return str(value if value not in (None, "") else "无 / 未登记").replace("|", "\\|").replace("\n", "<br>")


def render_markdown(version: str, events: Iterable[dict[str, Any]]) -> str:
    return render_document(version, events)


def document_version(text: str, fallback: str = "") -> str:
    match = VERSION_HEADING_PATTERN.search(text)
    return match.group(1).strip() if match else fallback




def format_name(text: str) -> str:
    return FORMAT_NAME if V5_MARKER in text else "invalid-format"




def _render_v3_body(event: dict[str, Any]) -> str:
    """Render the canonical human-readable body for one work event."""
    e = normalize_event(event, version=str(event.get("version") or ""))
    basic = [
        ("时间", e.get("timestamp")), ("执行 Agent", e.get("agent")),
        ("命令或来源", e.get("source")), ("版本", e.get("version")),
        ("目标", e.get("target_id")), ("范围", e.get("scope")),
        ("阶段", e.get("phase")), ("任务类型", e.get("task_type")),
        ("事件类型", e.get("event_type")), ("执行模式", e.get("execution_mode")),
    ]
    trace = [
        ("本次结论", e.get("summary")), ("状态", e.get("status")),
        ("状态变更", e.get("state_change")), ("Task", e.get("task_id")),
        ("Attempt", e.get("attempt_id")),
        ("Iteration", f"{e.get('iteration_id') or '无'} / {e.get('iteration_no') or 0}"),
        ("输入 Revision", e.get("input_revision")), ("输出 Revision", e.get("output_revision")),
        ("StageOutcome", "、".join(e.get("stage_outcome_refs") or []) or "无"),
        ("Evidence", "、".join(e.get("evidence_ids") or []) or "无"),
        ("Review", "、".join(e.get("review_result_refs") or []) or "无"),
        ("开放问题", "、".join(e.get("issue_ids") or []) or "无"),
        ("Git 检查点", "、".join(e.get("git_checkpoint_refs") or []) or "无"),
        ("下一 Agent", e.get("next_agent")), ("后续事项", e.get("next_action")),
    ]
    lines = [f"## {e['record_id']}", "", "### 基本信息", "", "| 字段 | 内容 |", "|---|---|"]
    lines.extend(f"| {table_escape(key)} | {table_escape(value)} |" for key, value in basic)
    lines.extend(["", "### 执行结果与追踪", "", "| 字段 | 内容 |", "|---|---|"])
    lines.extend(f"| {table_escape(key)} | {table_escape(value)} |" for key, value in trace)
    lines.extend(["", "### 变更摘要", "", f"- {str(e.get('summary') or '未登记').strip()}"])
    if e.get("modified_files_summary"):
        lines.extend(["", "### 文件变更摘要", ""])
        lines.extend(f"- `{item}`" for item in e["modified_files_summary"])
    lines.extend([
        "", "### SQL、验证与阻塞", "", "| 项目 | 内容 |", "|---|---|",
        f"| SQL 变更 | {table_escape(e.get('sql_change_summary'))} |",
        f"| 测试与验证 | {table_escape(e.get('validation_summary'))} |",
        f"| 问题与阻塞 | {table_escape('、'.join(e.get('blockers') or []) or '无')} |",
    ])
    if e.get("correction_of"):
        lines.extend(["", "### 更正与替代", "", f"- 本记录更正：`{e['correction_of']}`。原记录保持不变。"])
    return "\n".join(lines).rstrip() + "\n"






def read_events(path: Path) -> tuple[str, list[dict[str, Any]], list[str]]:
    """Read the current work-record format only."""
    text = path.read_text(encoding="utf-8")
    version = document_version(text, path.parent.name)
    if V5_MARKER not in text:
        raise ValueError(
            f"{path}: work record does not use the current format"
        )
    _, events, errors = parse_v5(text, version=version)
    return text, events, errors



def append_event(path: Path, event: dict[str, Any], *, version: str) -> tuple[str, list[dict[str, Any]]]:
    if path.exists():
        _, events, errors = read_events(path)
        if errors:
            raise ValueError("; ".join(errors))
    else:
        events = []
    normalized = normalize_event(event, version=version, index=len(events) + 1)
    if any(item.get("event_id") == normalized["event_id"] for item in events):
        raise ValueError(f"duplicate work event id: {normalized['event_id']}")
    if any(item.get("record_id") == normalized["record_id"] for item in events):
        raise ValueError(f"duplicate work record id: {normalized['record_id']}")
    events.append(normalized)
    return render_document(version, events), events



def validate_document(
    path: Path,
    *,
    allowed_fields: set[str],
    known_agents: set[str],
    statuses: set[str],
) -> tuple[list[str], list[dict[str, Any]]]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    version = document_version(text, path.parent.name)
    raw_events, events, parse_errors = parse_v5(text, version=version)
    errors.extend(parse_errors)
    if path.name != "work_record.md" or V5_MARKER not in text:
        errors.append("canonical work record must be work_record.md in the current work-record format")
    event_ids: set[str] = set()
    record_ids: set[str] = set()
    for index, (raw, event) in enumerate(zip(raw_events, events), 1):
        prefix = f"work-record[{index - 1}]"
        unknown = set(raw) - allowed_fields
        if unknown:
            errors.append(f"{prefix} contains unsupported fields: {sorted(unknown)}")
        if raw.get("schema_version") != WORK_RECORD_SCHEMA_VERSION:
            errors.append(f"{prefix} schema_version must be {WORK_RECORD_SCHEMA_VERSION}")
        if canonical_json(raw) != canonical_line(event, version=str(event.get("version") or ""), index=index):
            errors.append(f"{prefix} metadata is not canonical compact JSON")
        for key in REQUIRED_FIELDS:
            if key != "schema_version" and not str(event.get(key) or "").strip():
                errors.append(f"{prefix} missing {key}")
        event_id = str(event.get("event_id") or "")
        record_id = str(event.get("record_id") or "")
        if event_id in event_ids:
            errors.append(f"duplicate work event id: {event_id}")
        if record_id in record_ids:
            errors.append(f"duplicate work record id: {record_id}")
        event_ids.add(event_id)
        record_ids.add(record_id)
        if not RECORD_ID_PATTERN.fullmatch(record_id):
            errors.append(f"{prefix} invalid record_id: {record_id}")
        if parse_timestamp(str(event.get("timestamp") or "")) is None:
            errors.append(f"{prefix} invalid timestamp: {event.get('timestamp')}")
        if event.get("status") not in statuses:
            errors.append(f"{prefix} invalid status: {event.get('status')}")
        if event.get("agent") not in known_agents:
            errors.append(f"{prefix} unknown agent: {event.get('agent')}")
        for key in LIST_FIELDS:
            if not isinstance(event.get(key, []), list) or any(not isinstance(item, str) for item in event.get(key, [])):
                errors.append(f"{prefix} {key} must be a string array")
        if event.get("attempt_id"):
            for key in ("task_id", "iteration_id", "phase"):
                if not str(event.get(key) or "").strip():
                    errors.append(f"{prefix} attempt event missing {key}")
    if not errors and text != render_document(version, events):
        errors.append("work_record.md is not canonical; regenerate it through the work-record writer")
    return errors, events
