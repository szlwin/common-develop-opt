#!/usr/bin/env python3
"""Deterministic Markdown fact handling for requirement lists and module descriptions.

The module description merge keeps stable business facts in tables and maintains a
version-aware document index.  Extra source documents created below
``version/{VERSION}/module/{MODULE}`` are discovered automatically and linked from the
version module description.  When ``wk -d`` merges the description into project
facts, those links are rewritten to the version source so they remain resolvable.
"""

from __future__ import annotations

import sys
from pathlib import Path
_COMPAT_ROOT = Path(__file__).resolve().parent.parent / "compat"
_SCRIPT_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_SCRIPT_ROOT), str(_COMPAT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import document_style
from runtime import doc_layout
from document_core.common_utils import sha256_bytes as _sha256_bytes
from document_core import page_documents
from document_core import enum_contract
from document_core import enum_validation
from document_core import union_language
from runtime._core.document_utils import split_refs, write_preview_files

MANAGED_MARKER = "<!-- managed-by: common-develop/wk-d -->"

REQUIREMENT_HEADERS = ["需求编码", "需求名称", "版本号", "需求文档", "需求说明", "对应变更需求编号", "需求完成情况"]
REQUIREMENT_LINK_RE = re.compile(r"^\[([^\]]+)\]\(([^)]+)\)$")
MODULE_INFO_HEADERS = ["字段", "内容"]
CAPABILITY_HEADERS = ["能力编码", "能力名称", "能力说明", "能力边界", "状态"]
FEATURE_HEADERS = ["功能编码", "功能名称", "功能说明", "所属能力", "关联需求", "API 引用", "DB 引用", "功能状态"]
INFORMATION_HEADERS = ["信息编码", "信息名称", "信息说明", "DB引用"]
RULE_HEADERS = ["规则编码", "规则摘要", "关联需求", "关联功能"]
DEPENDENCY_HEADERS = ["依赖模块", "依赖类型", "依赖说明", "关联功能", "接口引用"]
FLOW_HEADERS = ["流程编号", "流程名称", "模块角色", "关联功能", "流程层级", "状态", "流程文档"]
PAGE_HEADERS = ["页面编号", "页面中文名称", "页面设计", "原型图", "关联功能", "状态"]

MODULE_INFO_FIELDS = ["模块编码", "模块名称", "模块说明", "职责边界", "最后合并版本"]
LIFECYCLE_STATUSES = {"PLANNED", "IN_PROGRESS", "ACTIVE", "DEPRECATED", "DISABLED"}
FLOW_STATUSES = {"PROPOSED", "ACTIVE", "STALE", "DEPRECATED", "SUPERSEDED"}
FLOW_LEVELS = {"L0", "L1", "L2"}
FLOW_ROLES = {"OWNER", "PARTICIPANT", "SUPPORTING"}

FEATURE_ID_RE = re.compile(r"^[A-Z][A-Z0-9_-]*-F[0-9]{2,}$")
RULE_ID_RE = re.compile(r"^(?:BR|CR)-[A-Z0-9_-]+-[0-9]{3,}$")

FORBIDDEN_MODULE_TERMS = {
    "模块总览",
    "功能列表",
    "数据依赖与来源",
    "数据来源",
    "核心表/目标表",
    "模块枚举",
    "关联文档",
    "版本变更记录",
    "工作量",
    "实现证据",
    "缺口",
    "缺口说明",
    "前端页面代码路径",
    "后端主方法入口",
    "后续文档",
}
AUTO_SOURCE_PREFIX = "补充文档（自动）"
AUTO_PROJECT_PREFIX_RE = re.compile(r"^补充文档（(?P<version>[^，]+)，自动）：")
IGNORED_DIRECTORY_NAMES = {"changes", "generated", "__pycache__", ".git"}
IGNORED_FILE_SUFFIXES = {".html", ".htm", ".tmp", ".bak", ".swp", ".pyc", ".class"}
IGNORED_FILE_NAMES = {".DS_Store", "Thumbs.db"}


class DocumentFactError(ValueError):
    """Raised when a current-format Markdown fact is invalid."""


@dataclass(frozen=True)
class Table:
    headers: list[str]
    rows: list[list[str]]


def sha256_bytes(content: bytes) -> str:
    """Hash bytes through the shared digest helper."""
    return _sha256_bytes(content)


def _split_row(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        raise DocumentFactError(f"invalid Markdown table row: {line}")
    body = stripped[1:-1]
    cells: list[str] = []
    current: list[str] = []
    escaped = False
    for char in body:
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            cells.append("".join(current).strip().replace("<br>", "\n"))
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    cells.append("".join(current).strip().replace("<br>", "\n"))
    return cells


def _escape_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _render_row(cells: Iterable[str]) -> str:
    return "| " + " | ".join(_escape_cell(str(cell)) for cell in cells) + " |"


def render_table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    return [
        _render_row(headers),
        _render_row(["---"] * len(headers)),
        *[_render_row(row) for row in rows],
    ]


def _is_separator(line: str, width: int) -> bool:
    try:
        cells = _split_row(line)
    except DocumentFactError:
        return False
    return len(cells) == width and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def _heading_index(lines: Sequence[str], heading: str) -> int:
    try:
        return next(index for index, line in enumerate(lines) if line.strip() == heading)
    except StopIteration as exc:
        raise DocumentFactError(f"missing section: {heading}") from exc


def _section_end(lines: Sequence[str], heading_index: int) -> int:
    heading = lines[heading_index].strip()
    level = len(heading) - len(heading.lstrip("#"))
    for index in range(heading_index + 1, len(lines)):
        stripped = lines[index].strip()
        if not stripped.startswith("#"):
            continue
        current_level = len(stripped) - len(stripped.lstrip("#"))
        if current_level <= level:
            return index
    return len(lines)


def find_table(lines: list[str], heading: str, required_headers: Sequence[str]) -> tuple[int, int, Table]:
    heading_index = _heading_index(lines, heading)
    section_end = _section_end(lines, heading_index)
    header_index: int | None = None
    for index in range(heading_index + 1, section_end):
        line = lines[index]
        if not line.lstrip().startswith("|"):
            continue
        try:
            cells = _split_row(line)
        except DocumentFactError:
            continue
        if cells == list(required_headers):
            header_index = index
            break
    if header_index is None:
        raise DocumentFactError(f"{heading} missing fixed table header: {' | '.join(required_headers)}")
    if header_index + 1 >= len(lines) or not _is_separator(lines[header_index + 1], len(required_headers)):
        raise DocumentFactError(f"{heading} missing Markdown separator row")
    rows: list[list[str]] = []
    end = header_index + 2
    while end < section_end and lines[end].lstrip().startswith("|"):
        row = _split_row(lines[end])
        if len(row) != len(required_headers):
            raise DocumentFactError(f"invalid row width under {heading}: {row}")
        if any(cell.strip() for cell in row):
            rows.append(row)
        end += 1
    return header_index, end, Table(headers=list(required_headers), rows=rows)


def _parse_bullets(lines: Sequence[str], heading: str) -> list[str]:
    start = _heading_index(lines, heading)
    end = _section_end(lines, start)
    values: list[str] = []
    for line in lines[start + 1 : end]:
        stripped = line.strip()
        if stripped.startswith("- "):
            value = stripped[2:].strip()
            if value and value != "无":
                values.append(value)
    return values


def _replace_bullets(text: str, heading: str, values: Sequence[str]) -> str:
    lines = text.splitlines()
    start = _heading_index(lines, heading)
    end = _section_end(lines, start)
    replacement = [heading, "", *([f"- {value}" for value in values] or ["- 无"]), ""]
    lines[start:end] = replacement
    return "\n".join(lines).rstrip() + "\n"


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _placeholder(value: str | None) -> bool:
    return not value or value.strip() in {"待补充", "无", "-", "CURRENT", "{VERSION}"}


def _id_pattern(prefix: str, module: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(prefix)}-{re.escape(module)}-[A-Z0-9][A-Z0-9._-]*$")


_split_refs = split_refs


def requirement_list_template(version: str, *, project: bool = False) -> str:
    title = "项目需求列表" if project else f"{version} 需求列表"
    note = (
        "本文件由各版本 `requirement_list.md` 通过 `wk -d` 按需求编码合并；需求文档链接已改写为对应版本目录中的稳定相对路径。"
        if project
        else "需求确认通过后登记；每行必须包含版本号和 requirement.md 链接；需求变更通过“对应变更需求编号”关联原需求。"
    )
    return "\n".join(
        [
            MANAGED_MARKER if project else "<!-- template: common-develop/requirement-list-v2 -->",
            f"# {title}",
            "",
            f"> {note}",
            "",
            "## 需求列表",
            "",
            *render_table(REQUIREMENT_HEADERS, []),
            "",
        ]
    )


def _standard_source_document_refs(module: str) -> list[str]:
    return [
        "项目文档首页：[打开](../../../../docs/README.md)",
        "版本摘要：[打开](../../version_summary.md)",
        "需求列表：[打开](../../requirement/requirement_list.md)",
        f"业务模型：[打开]({module}_business_model.yaml)",
        "API/DB 版本增量：[打开目录](changes/)",
        "API/DB 当前实现：代码、OpenAPI、DDL、Migration 或项目声明的等价来源",
        f"模块设计：[打开]({module}_design.md)",
        "页面设计：见上方“页面设计”逐页入口",
        "依赖关系：[项目关联事实](../../../../docs/_relations/dependency_impact.yaml)",
    ]


def _standard_project_document_refs(module: str, version: str = "{VERSION}") -> list[str]:
    return [
        "项目文档首页：[打开](../../README.md)",
        "需求列表：[打开](../../requirement_list.md)",
        f"最后合并版本摘要：[打开](../../../version/{version}/version_summary.md)",
        f"业务模型阅读版：`{module}_business_model.md`（存在时由项目首页提供可点击入口）",
        "API/DB 增量历史：`../../../archive_manifest.yaml`",
        "API/DB 当前实现：代码、OpenAPI、DDL、Migration 或项目声明的等价来源",
        f"本版本设计：`../../../version/{version}/module/{module}/{module}_design.md`（存在时由项目首页和版本摘要提供可点击入口）",
        "页面设计：见上方“页面设计”逐页入口",
        "依赖关系：`../../_relations/dependency_graph.md`（存在时由项目首页提供可点击入口）",
    ]


def module_desc_template(module: str, version: str, *, project: bool = False) -> str:
    note = (
        "本文件由版本级模块描述通过 `wk -d` 合并，是模块能力、功能、业务信息、规则、依赖和文档索引的当前事实源。"
        if project
        else "版本级文件记录本版本新增或调整的模块事实；`wk -d` 会自动发现同目录补充文档并维护“文档引用”。"
    )
    documents = _standard_project_document_refs(module, version) if project else _standard_source_document_refs(module)
    return "\n".join(
        [
            MANAGED_MARKER if project else "<!-- template: common-develop/module-desc-v2 -->",
            f"# {module} 模块说明",
            "",
            f"> {note}",
            "",
            (
                "> 文档导航：[项目文档首页](../../README.md) · [项目需求列表](../../requirement_list.md)"
                if project
                else "> 文档导航：[项目文档首页](../../../../docs/README.md) · [版本摘要](../../version_summary.md) · [本版本需求列表](../../requirement/requirement_list.md)"
            ),
            "",
            "## 模块信息",
            "",
            *render_table(
                MODULE_INFO_HEADERS,
                [
                    ["模块编码", module],
                    ["模块名称", "待补充"],
                    ["模块说明", "待补充"],
                    ["职责边界", "待补充"],
                    ["最后合并版本", version],
                ],
            ),
            "",
            "## 业务能力",
            "",
            *render_table(CAPABILITY_HEADERS, []),
            "",
            "## 模块功能",
            "",
            *render_table(FEATURE_HEADERS, []),
            "",
            "## 业务信息",
            "",
            *render_table(INFORMATION_HEADERS, []),
            "",
            *enum_contract.render_enum_section([], project=project),
            "## 规则与约束",
            "",
            *render_table(RULE_HEADERS, []),
            "",
            "## 模块依赖",
            "",
            *render_table(DEPENDENCY_HEADERS, []),
            "",
            "## 参与业务流程",
            "",
            *render_table(FLOW_HEADERS, []),
            "",
            "## 页面设计",
            "",
            *render_table(PAGE_HEADERS, []),
            "",
            "## 文档引用",
            "",
            *[f"- {item}" for item in documents],
            "",
        ]
    )


def _standard_document_names(module: str) -> set[str]:
    names = {
        f"{module}_desc.md",
        f"{module}_desc.html",
        f"{module}_design.md",
        f"{module}_design.html",
        f"{module}_page_design.md",
        f"{module}_page_design.html",
        f"{module}_web_code.md",
        f"{module}_back_code.md",
        f"{module}_ER.drawio",
        f"{module}_ER.md",
        f"{module}_ER.html",
        f"{module}_ER.generated.md",
    }
    for stem in (f"{module}_business_model", f"{module}_api", f"{module}_db"):
        for suffix in (".yaml", ".yml", ".md", ".html", ".generated.md"):
            names.add(stem + suffix)
    return names


def discover_extra_documents(module_dir: Path, module: str) -> list[str]:
    """Return source-document paths not covered by the standard module contract."""
    if not module_dir.is_dir():
        return []
    standard_names = _standard_document_names(module)
    results: list[str] = []
    for path in module_dir.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(module_dir)
        if relative.parts and relative.parts[0] == "page-design":
            continue
        if any(part.startswith(".") or part in IGNORED_DIRECTORY_NAMES for part in relative.parts[:-1]):
            continue
        if path.name.startswith(".") or path.name in IGNORED_FILE_NAMES:
            continue
        if path.name in standard_names or path.suffix.lower() in IGNORED_FILE_SUFFIXES:
            continue
        if path.name.endswith(("~", ".orig", ".rej")):
            continue
        results.append(relative.as_posix())
    return sorted(set(results), key=lambda value: value.casefold())


def _auto_source_ref(relative: str) -> str:
    return f"{AUTO_SOURCE_PREFIX}：[{relative}]({relative})"


def _page_rows(
    module_dir: Path,
    module: str,
    existing_rows: Sequence[Sequence[str]] = (),
) -> list[list[str]]:
    existing = {
        row[0]: row
        for row in _normalize_page_rows(Table(PAGE_HEADERS, [list(item) for item in existing_rows]), module)
    }
    rows: list[list[str]] = []
    documents = page_documents.discover_module_page_documents(module_dir, module)
    for document in documents:
        relative = document.path.relative_to(module_dir).as_posix()
        link = f"[打开]({relative})"
        if document.legacy:
            for page_id in document.page_ids:
                if not page_id.startswith(f"PAGE-{module}-"):
                    raise DocumentFactError(
                        f"legacy page ID does not belong to module {module}: {page_id}"
                    )
                row = list(existing.get(page_id, [
                    page_id, page_id, link, "待补充", "待补充", "PLANNED",
                ]))
                row[2] = link
                rows.append(row)
            continue
        row = list(existing.get(document.primary_page_id, [
            document.primary_page_id,
            document.page_name,
            link,
            "待补充",
            "待补充",
            "PLANNED",
        ]))
        row[2] = link
        rows.append(row)
    return rows


def _replace_or_insert_table(
    text: str,
    heading: str,
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    *,
    before_heading: str,
) -> str:
    lines = text.splitlines()
    try:
        start, end, _ = find_table(lines, heading, headers)
        lines[start:end] = render_table(headers, rows)
    except DocumentFactError as exc:
        if f"missing section: {heading}" not in str(exc):
            raise
        insert_at = _heading_index(lines, before_heading)
        lines[insert_at:insert_at] = [heading, "", *render_table(headers, rows), ""]
    return "\n".join(lines).rstrip() + "\n"


def _auto_project_ref(version: str, module: str, relative: str) -> str:
    target = f"../../../version/{version}/module/{module}/{relative}"
    return f"补充文档（{version}，自动）：[{relative}]({target})"


def _is_auto_ref(value: str) -> bool:
    return value.startswith(f"{AUTO_SOURCE_PREFIX}：") or AUTO_PROJECT_PREFIX_RE.match(value) is not None


def _is_standard_ref(value: str, module: str) -> bool:
    if value in set(_standard_source_document_refs(module)):
        return True
    normalized = re.sub(r"version/[^/]+/", "version/{VERSION}/", value)
    return normalized in set(_standard_project_document_refs(module))


def synchronize_module_desc_source(path: Path, module: str, version: str) -> tuple[str, bool, list[str]]:
    """Synchronize automatic extra-document links into a version module description."""
    if not path.is_file():
        raise DocumentFactError(f"missing current-format version fact: {path}; run merge_docs.py init first")
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    existing = _parse_bullets(lines, "## 文档引用")
    manual = [item for item in existing if not _is_standard_ref(item, module) and not _is_auto_ref(item)]
    extra_documents = discover_extra_documents(path.parent, module)
    existing_pages = _optional_table(lines, "## 页面设计", PAGE_HEADERS)
    page_rows = _page_rows(path.parent, module, existing_pages.rows)
    desired = _dedupe(
        [*_standard_source_document_refs(module), *manual, *[_auto_source_ref(item) for item in extra_documents]]
    )
    updated = _replace_bullets(text, "## 文档引用", desired)
    updated = _replace_or_insert_table(
        updated,
        "## 页面设计",
        PAGE_HEADERS,
        page_rows,
        before_heading="## 文档引用",
    )
    changed = updated != text
    if changed:
        path.write_text(updated, encoding="utf-8")
    return updated, changed, extra_documents


def ensure_templates(doc_root: Path, version: str, module: str) -> list[Path]:
    doc_root = doc_layout.validate_doc_root(doc_root, version)
    created: list[Path] = []
    requirement_source = doc_layout.version_requirement_root(doc_root, version) / "requirement_list.md"
    module_source = doc_layout.module_dir(doc_root, version, module) / f"{module}_desc.md"
    if not requirement_source.exists():
        requirement_source.parent.mkdir(parents=True, exist_ok=True)
        requirement_source.write_text(requirement_list_template(version), encoding="utf-8")
        created.append(requirement_source)
    if not module_source.exists():
        module_source.parent.mkdir(parents=True, exist_ok=True)
        module_source.write_text(module_desc_template(module, version), encoding="utf-8")
        created.append(module_source)
    synchronize_module_desc_source(module_source, module, version)
    return created


def _parse_requirement_link(value: str, *, version: str, project: bool) -> tuple[str, str]:
    match = REQUIREMENT_LINK_RE.fullmatch(value.strip())
    if not match:
        raise DocumentFactError(f"requirement document link must be Markdown: {value}")
    label, target = match.group(1).strip(), match.group(2).strip()
    if not label or not target or target.startswith(("/", "http://", "https://")):
        raise DocumentFactError(f"requirement document link must be a relative Markdown link: {value}")
    expected_prefix = f"../version/{version}/requirement/" if project else ""
    if not target.startswith(expected_prefix) or not target.endswith("/requirement.md"):
        scope = "project" if project else "version"
        raise DocumentFactError(f"{scope} requirement document link has invalid path: {target}")
    if ".." in Path(target).parts[2 if project else 0:]:
        raise DocumentFactError(f"requirement document link escapes its expected directory: {target}")
    return label, target


def _normalize_requirement_rows(table: Table, *, project: bool) -> list[list[str]]:
    seen: set[str] = set()
    rows: list[list[str]] = []
    for index, raw in enumerate(table.rows):
        code, name, version, document_link, description, changed_code, status = [cell.strip() for cell in raw]
        if not code or not name or not version or not document_link or not description or not status:
            raise DocumentFactError(f"requirement_list.md row {index + 1} has empty required fields")
        if code in seen:
            raise DocumentFactError(f"duplicate requirement code: {code}")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", version):
            raise DocumentFactError(f"invalid requirement version: {version}")
        _parse_requirement_link(document_link, version=version, project=project)
        seen.add(code)
        rows.append([code, name, version, document_link, description, changed_code or "-", status])
    return rows


def _project_requirement_row(row: list[str]) -> list[str]:
    code, name, version, document_link, description, changed_code, status = row
    label, target = _parse_requirement_link(document_link, version=version, project=False)
    project_target = f"../version/{version}/requirement/{target}"
    return [code, name, version, f"[{label}]({project_target})", description, changed_code, status]


def validate_requirement_list_links(source_path: Path, source_text: str) -> None:
    _, _, table = find_table(source_text.splitlines(), "## 需求列表", REQUIREMENT_HEADERS)
    rows = _normalize_requirement_rows(table, project=False)
    expected_version = source_path.parent.parent.name
    for row in rows:
        if row[2] != expected_version:
            raise DocumentFactError(
                f"requirement version must equal source version directory: expected {expected_version}, got {row[2]}"
            )
        _, target = _parse_requirement_link(row[3], version=row[2], project=False)
        resolved = (source_path.parent / target).resolve()
        try:
            resolved.relative_to(source_path.parent.resolve())
        except ValueError as exc:
            raise DocumentFactError(f"requirement document link escapes version root: {target}") from exc
        if not resolved.is_file():
            raise DocumentFactError(f"requirement document link target does not exist: {target}")


def upsert_requirement_list_row(
    path: Path,
    *,
    code: str,
    name: str,
    version: str,
    document_target: str,
    description: str,
    changed_code: str = "-",
    status: str,
) -> None:
    text = path.read_text(encoding="utf-8") if path.is_file() else requirement_list_template(version)
    _, _, table = find_table(text.splitlines(), "## 需求列表", REQUIREMENT_HEADERS)
    rows = _normalize_requirement_rows(table, project=False)
    row = [code.strip(), name.strip(), version.strip(), f"[requirement.md]({document_target.strip()})", description.strip(), changed_code.strip() or "-", status.strip()]
    # validate before writing
    temp = Table(REQUIREMENT_HEADERS, [row])
    normalized = _normalize_requirement_rows(temp, project=False)[0]
    ordered = [list(item) for item in rows]
    positions = {item[0]: index for index, item in enumerate(ordered)}
    if normalized[0] in positions:
        ordered[positions[normalized[0]]] = normalized
    else:
        ordered.append(normalized)
    lines = requirement_list_template(version).splitlines()
    start, end, _ = find_table(lines, "## 需求列表", REQUIREMENT_HEADERS)
    lines[start:end] = render_table(REQUIREMENT_HEADERS, ordered)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def merge_requirement_list(source_text: str, target_text: str | None) -> str:
    _, _, source_table = find_table(source_text.splitlines(), "## 需求列表", REQUIREMENT_HEADERS)
    source_rows = [_project_requirement_row(row) for row in _normalize_requirement_rows(source_table, project=False)]
    if target_text:
        _, _, target_table = find_table(target_text.splitlines(), "## 需求列表", REQUIREMENT_HEADERS)
        target_rows = _normalize_requirement_rows(target_table, project=True)
    else:
        target_rows = []
    ordered = [list(row) for row in target_rows]
    positions = {row[0]: index for index, row in enumerate(ordered)}
    for row in source_rows:
        if row[0] in positions:
            ordered[positions[row[0]]] = row
        else:
            positions[row[0]] = len(ordered)
            ordered.append(row)
    lines = requirement_list_template("CURRENT", project=True).splitlines()
    start, end, _ = find_table(lines, "## 需求列表", REQUIREMENT_HEADERS)
    lines[start:end] = render_table(REQUIREMENT_HEADERS, ordered)
    return "\n".join(lines).rstrip() + "\n"


def _validate_module_headers(lines: Sequence[str]) -> None:
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("|"):
            overlap = FORBIDDEN_MODULE_TERMS.intersection(_split_row(stripped))
            if overlap:
                raise DocumentFactError(f"module desc contains removed columns: {sorted(overlap)}")
        if stripped.startswith("#"):
            title = stripped.lstrip("#").strip()
            if title in FORBIDDEN_MODULE_TERMS:
                raise DocumentFactError(f"module desc contains removed section: {title}")


def _parse_module_info(table: Table, module: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for index, row in enumerate(table.rows):
        key, value = [cell.strip() for cell in row]
        if not key:
            raise DocumentFactError(f"module info row {index + 1} has empty field")
        if key in result:
            raise DocumentFactError(f"duplicate module info field: {key}")
        result[key] = value
    missing = [field for field in MODULE_INFO_FIELDS if field not in result]
    if missing:
        raise DocumentFactError(f"module info missing fields: {missing}")
    if result["模块编码"] != module:
        raise DocumentFactError(f"module desc 模块编码 must equal {module}: {result['模块编码']}")
    return result


def _normalize_coded_rows(
    table: Table,
    *,
    module: str,
    prefix: str,
    label: str,
    required_positions: Sequence[int],
    status_position: int | None = None,
) -> list[list[str]]:
    pattern = _id_pattern(prefix, module)
    seen: set[str] = set()
    rows: list[list[str]] = []
    for index, raw in enumerate(table.rows):
        row = [cell.strip() for cell in raw]
        code = row[0]
        if any(not row[position] for position in required_positions):
            raise DocumentFactError(f"{label} row {index + 1} has empty required fields")
        if not pattern.fullmatch(code):
            raise DocumentFactError(f"{label} code must match {prefix}-{module}-*: {code}")
        if code in seen:
            raise DocumentFactError(f"duplicate {label} code: {code}")
        if status_position is not None and row[status_position] not in LIFECYCLE_STATUSES:
            raise DocumentFactError(
                f"{label} {code} status must be one of {sorted(LIFECYCLE_STATUSES)}: {row[status_position]}"
            )
        seen.add(code)
        rows.append([cell or "无" for cell in row])
    return rows


def _normalize_feature_rows(table: Table) -> list[list[str]]:
    seen: set[str] = set()
    rows: list[list[str]] = []
    for index, raw in enumerate(table.rows):
        row = [cell.strip() for cell in raw]
        code = row[0]
        if any(not row[position] for position in (0, 1, 2, 3, 4, 7)):
            raise DocumentFactError(f"feature row {index + 1} has empty required fields")
        if not FEATURE_ID_RE.fullmatch(code):
            raise DocumentFactError(
                f"feature code must match {{REQUIREMENT_ID}}-F{{NN}}: {code}"
            )
        requirement_id = re.sub(r"-F[0-9]{2,}$", "", code)
        requirement_refs = set(_split_refs(row[4]))
        if requirement_id not in requirement_refs:
            raise DocumentFactError(
                f"feature {code} must reference its owning requirement {requirement_id}"
            )
        if code in seen:
            raise DocumentFactError(f"duplicate feature code: {code}")
        if row[7] not in LIFECYCLE_STATUSES:
            raise DocumentFactError(
                f"feature {code} status must be one of {sorted(LIFECYCLE_STATUSES)}: {row[7]}"
            )
        seen.add(code)
        rows.append([cell or "无" for cell in row])
    return rows


def _normalize_rule_rows(table: Table) -> list[list[str]]:
    seen: set[str] = set()
    rows: list[list[str]] = []
    for index, raw in enumerate(table.rows):
        row = [cell.strip() for cell in raw]
        code = row[0]
        if any(not row[position] for position in (0, 1, 2, 3)):
            raise DocumentFactError(f"rule row {index + 1} has empty required fields")
        if not RULE_ID_RE.fullmatch(code):
            raise DocumentFactError(f"rule code must use BR-/CR- requirement rule ID: {code}")
        match = re.fullmatch(r"(?:BR|CR)-(.+)-[0-9]{3,}", code)
        requirement_id = match.group(1) if match else ""
        requirement_refs = set(_split_refs(row[2]))
        exact_owner = requirement_id in requirement_refs
        scoped_owners = sorted(
            ref for ref in requirement_refs
            if re.fullmatch(r"P[0-9]+", requirement_id) and ref.startswith(requirement_id + "-")
        )
        if not exact_owner and len(scoped_owners) != 1:
            raise DocumentFactError(
                f"rule {code} must reference exactly one owning requirement in namespace {requirement_id}"
            )
        if code in seen:
            raise DocumentFactError(f"duplicate rule code: {code}")
        seen.add(code)
        rows.append([cell or "无" for cell in row])
    return rows


def _normalize_dependencies(table: Table) -> list[list[str]]:
    seen: set[tuple[str, str, str, str]] = set()
    rows: list[list[str]] = []
    for index, raw in enumerate(table.rows):
        row = [cell.strip() or "无" for cell in raw]
        if any(row[position] == "无" for position in (0, 1, 2)):
            raise DocumentFactError(f"module dependency row {index + 1} has empty required fields")
        key = (row[0], row[1], row[3], row[4])
        if key in seen:
            raise DocumentFactError(f"duplicate module dependency: {key}")
        seen.add(key)
        rows.append(row)
    return rows


def _optional_table(lines: Sequence[str], heading: str, headers: Sequence[str]) -> Table:
    try:
        _, _, table = find_table(lines, heading, headers)
        return table
    except DocumentFactError as exc:
        if f"missing heading: {heading}" in str(exc):
            return Table(list(headers), [])
        raise


def _normalize_flow_rows(table: Table, module: str) -> list[list[str]]:
    rows: list[list[str]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(table.rows):
        row = [cell.strip() or "无" for cell in raw]
        if not re.fullmatch(r"FLOW-[A-Z0-9_-]+", row[0]):
            raise DocumentFactError(f"business flow row {index + 1} has invalid flow ID: {row[0]}")
        if row[1] == "无" or row[3] == "无" or row[6] == "无":
            raise DocumentFactError(f"business flow row {index + 1} has empty required fields")
        if row[2] not in FLOW_ROLES:
            raise DocumentFactError(f"business flow {row[0]} role must be one of {sorted(FLOW_ROLES)}")
        if row[4] not in FLOW_LEVELS:
            raise DocumentFactError(f"business flow {row[0]} level must be one of {sorted(FLOW_LEVELS)}")
        if row[5] not in FLOW_STATUSES:
            raise DocumentFactError(f"business flow {row[0]} status must be one of {sorted(FLOW_STATUSES)}")
        key = (row[0], row[3])
        if key in seen:
            raise DocumentFactError(f"duplicate business flow participation: {row[0]} / {row[3]}")
        seen.add(key)
        rows.append(row)
    return rows


def _normalize_page_rows(table: Table, module: str) -> list[list[str]]:
    rows: list[list[str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(table.rows):
        row = [cell.strip() or "无" for cell in raw]
        page_id = row[0]
        if not page_documents.PAGE_ID_RE.fullmatch(page_id) or not page_id.startswith(f"PAGE-{module}-"):
            raise DocumentFactError(
                f"page row {index + 1} owner namespace must match module {module}: {page_id}"
            )
        if page_id in seen:
            raise DocumentFactError(f"duplicate page row: {page_id}")
        if row[1] == "无" or row[2] == "无":
            raise DocumentFactError(f"page row {index + 1} is missing name or page-design link")
        link_match = REQUIREMENT_LINK_RE.fullmatch(row[2])
        if not link_match:
            raise DocumentFactError(f"page row {page_id} must use one Markdown document link: {row[2]}")
        target = link_match.group(2).split("#", 1)[0]
        if not target or Path(target).is_absolute() or target.startswith("file://"):
            raise DocumentFactError(f"page row {page_id} link must be relative: {row[2]}")
        if row[5] not in LIFECYCLE_STATUSES:
            raise DocumentFactError(
                f"page {page_id} status must be one of {sorted(LIFECYCLE_STATUSES)}: {row[5]}"
            )
        seen.add(page_id)
        rows.append(row)
    return rows


def _parse_module_desc(text: str, module: str) -> dict[str, object]:
    lines = text.splitlines()
    _validate_module_headers(lines)
    _, _, info = find_table(lines, "## 模块信息", MODULE_INFO_HEADERS)
    _, _, capabilities = find_table(lines, "## 业务能力", CAPABILITY_HEADERS)
    _, _, features = find_table(lines, "## 模块功能", FEATURE_HEADERS)
    _, _, information = find_table(lines, "## 业务信息", INFORMATION_HEADERS)
    _, _, rules = find_table(lines, "## 规则与约束", RULE_HEADERS)
    _, _, dependencies = find_table(lines, "## 模块依赖", DEPENDENCY_HEADERS)
    flows = _optional_table(lines, "## 参与业务流程", FLOW_HEADERS)
    pages = _optional_table(lines, "## 页面设计", PAGE_HEADERS)
    return {
        "info": _parse_module_info(info, module),
        "capabilities": _normalize_coded_rows(
            capabilities,
            module=module,
            prefix="CAP",
            label="capability",
            required_positions=(0, 1, 2, 4),
            status_position=4,
        ),
        "features": _normalize_feature_rows(features),
        "information": _normalize_coded_rows(
            information,
            module=module,
            prefix="INFO",
            label="information",
            required_positions=(0, 1, 2),
        ),
        "enums": enum_contract.parse_enums(text, module),
        "rules": _normalize_rule_rows(rules),
        "dependencies": _normalize_dependencies(dependencies),
        "flows": _normalize_flow_rows(flows, module),
        "pages": _normalize_page_rows(pages, module),
        "documents": _parse_bullets(lines, "## 文档引用"),
    }


def _upsert(target: Sequence[Sequence[str]], source: Sequence[Sequence[str]], key_positions: Sequence[int]) -> list[list[str]]:
    ordered = [list(row) for row in target]
    positions = {tuple(row[position] for position in key_positions): index for index, row in enumerate(ordered)}
    for raw in source:
        row = list(raw)
        key = tuple(row[position] for position in key_positions)
        if key in positions:
            ordered[positions[key]] = row
        else:
            positions[key] = len(ordered)
            ordered.append(row)
    return ordered


def _validate_module_references(
    capabilities: Sequence[Sequence[str]],
    features: Sequence[Sequence[str]],
    rules: Sequence[Sequence[str]],
    dependencies: Sequence[Sequence[str]],
) -> None:
    capability_ids = {row[0] for row in capabilities}
    feature_ids = {row[0] for row in features}
    for row in features:
        missing = sorted(set(_split_refs(row[3])) - capability_ids)
        if missing:
            raise DocumentFactError(f"feature {row[0]} references missing capabilities: {', '.join(missing)}")
    for row in rules:
        missing = sorted(set(_split_refs(row[3])) - feature_ids)
        if missing:
            raise DocumentFactError(f"rule {row[0]} references missing features: {', '.join(missing)}")
    for row in dependencies:
        missing = sorted(set(_split_refs(row[3])) - feature_ids)
        if missing:
            raise DocumentFactError(
                f"module dependency {row[0]} / {row[1]} references missing features: {', '.join(missing)}"
            )


def _manual_document_refs(values: Sequence[str], module: str) -> list[str]:
    return [item for item in values if not _is_standard_ref(item, module) and not _is_auto_ref(item)]


def _target_auto_refs_for_other_versions(values: Sequence[str], current_version: str) -> list[str]:
    result: list[str] = []
    for item in values:
        match = AUTO_PROJECT_PREFIX_RE.match(item)
        if match and match.group("version") != current_version:
            result.append(item)
    return result


def _project_page_rows(rows: Sequence[Sequence[str]]) -> list[list[str]]:
    projected: list[list[str]] = []
    for raw in rows:
        row = list(raw)
        match = REQUIREMENT_LINK_RE.fullmatch(row[2].strip())
        if not match:
            raise DocumentFactError(f"page-design link must be Markdown: {row[2]}")
        filename = Path(match.group(2).split("#", 1)[0]).name
        if not filename:
            raise DocumentFactError(f"page-design link has no filename: {row[2]}")
        row[2] = f"[打开](design/pages/{filename})"
        if row[3] not in {"不适用", "无"}:
            row[3] = "见页面设计"
        projected.append(row)
    return projected


def merge_module_desc(
    source_text: str,
    target_text: str | None,
    module: str,
    version: str,
    *,
    extra_documents: Sequence[str] = (),
) -> str:
    source = _parse_module_desc(source_text, module)
    target = _parse_module_desc(target_text, module) if target_text else _parse_module_desc(
        module_desc_template(module, version, project=True), module
    )

    info = dict(target["info"])
    for field in ("模块名称", "模块说明", "职责边界"):
        candidate = str(source["info"].get(field, ""))
        if not _placeholder(candidate):
            info[field] = candidate
    info["模块编码"] = module
    info["最后合并版本"] = version

    capabilities = _upsert(target["capabilities"], source["capabilities"], (0,))
    features = _upsert(target["features"], source["features"], (0,))
    information = _upsert(target["information"], source["information"], (0,))
    try:
        enums = enum_contract.merge_enums(source["enums"], target["enums"], module, version)
    except enum_contract.EnumContractError as exc:
        raise DocumentFactError(str(exc)) from exc
    rules = _upsert(target["rules"], source["rules"], (0,))
    dependencies = _upsert(target["dependencies"], source["dependencies"], (0, 1, 3, 4))
    flows = _upsert(target["flows"], source["flows"], (0, 3))
    pages = _upsert(target["pages"], _project_page_rows(source["pages"]), (0,))
    _validate_module_references(capabilities, features, rules, dependencies)
    feature_ids = {row[0] for row in features}
    for row in flows:
        missing = sorted(set(_split_refs(row[3])) - feature_ids)
        if missing:
            raise DocumentFactError(f"business flow {row[0]} references missing features: {', '.join(missing)}")

    target_docs = list(target["documents"])
    source_docs = list(source["documents"])
    documents = _dedupe(
        [
            *_standard_project_document_refs(module, version),
            *_manual_document_refs(target_docs, module),
            *_manual_document_refs(source_docs, module),
            *_target_auto_refs_for_other_versions(target_docs, version),
            *[_auto_project_ref(version, module, relative) for relative in extra_documents],
        ]
    )

    lines = [
        MANAGED_MARKER,
        f"# {module} 模块说明",
        "",
        "> 本文件由版本级模块描述通过 `wk -d` 合并，是模块能力、功能、业务信息、规则、依赖和文档索引的当前事实源。",
        "",
        "> 文档导航：[项目文档首页](../../README.md) · [项目需求列表](../../requirement_list.md) · "
        f"[最后合并版本摘要](../../../version/{version}/version_summary.md)",
        "",
        "## 模块信息",
        "",
        *render_table(MODULE_INFO_HEADERS, [[field, info.get(field, "待补充")] for field in MODULE_INFO_FIELDS]),
        "",
        "## 业务能力",
        "",
        *render_table(CAPABILITY_HEADERS, capabilities),
        "",
        "## 模块功能",
        "",
        *render_table(FEATURE_HEADERS, features),
        "",
        "## 业务信息",
        "",
        *render_table(INFORMATION_HEADERS, information),
        "",
        *enum_contract.render_enum_section(enums, project=True),
        "## 规则与约束",
        "",
        *render_table(RULE_HEADERS, rules),
        "",
        "## 模块依赖",
        "",
        *render_table(DEPENDENCY_HEADERS, dependencies),
        "",
        "## 参与业务流程",
        "",
        *render_table(FLOW_HEADERS, flows),
        "",
        "## 页面设计",
        "",
        *render_table(PAGE_HEADERS, pages),
        "",
        "## 文档引用",
        "",
        *([f"- {item}" for item in documents] or ["- 无"]),
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"





def replace_project_flow_rows(
    target_text: str, module: str, affected_flow_ids: Sequence[str], rows: Sequence[Sequence[str]]
) -> str:
    """Replace all module-index rows for affected flows, preserving unrelated rows."""
    parsed = _parse_module_desc(target_text, module)
    affected = {str(item).strip() for item in affected_flow_ids if str(item).strip()}
    feature_ids = {row[0] for row in parsed["features"]}
    normalized_rows = _normalize_flow_rows(Table(FLOW_HEADERS, [list(row) for row in rows]), module)
    for row in normalized_rows:
        missing = sorted(set(_split_refs(row[3])) - feature_ids)
        if missing:
            raise DocumentFactError(f"business flow {row[0]} references missing features: {', '.join(missing)}")
    retained = [row for row in parsed["flows"] if row[0] not in affected]
    flows = _upsert(retained, normalized_rows, (0, 3))
    lines = target_text.splitlines()
    try:
        start, end, _ = find_table(lines, "## 参与业务流程", FLOW_HEADERS)
        lines[start:end] = render_table(FLOW_HEADERS, flows)
    except DocumentFactError as exc:
        if "missing heading: ## 参与业务流程" not in str(exc):
            raise
        insert_at = _heading_index(lines, "## 文档引用")
        section = ["## 参与业务流程", "", *render_table(FLOW_HEADERS, flows), ""]
        lines[insert_at:insert_at] = section
    return "\n".join(lines).rstrip() + "\n"

def _source_paths(doc_root: Path, version: str, module: str) -> list[tuple[str, str, Path, Path]]:
    return [
        (
            "requirement_list",
            "TABLE_UPSERT",
            doc_layout.version_requirement_root(doc_root, version) / "requirement_list.md",
            doc_root / "docs" / "requirement_list.md",
        ),
        (
            "module_desc",
            "MODULE_DESC_MERGE",
            doc_layout.module_dir(doc_root, version, module) / f"{module}_desc.md",
            doc_layout.project_module_dir(doc_root, module) / f"{module}_desc.md",
        ),
    ]


def simulate_merges(doc_root: Path, version: str, module: str) -> list[dict[str, object]]:
    enum_check = enum_validation.validate_version(doc_root, version)
    if enum_check.get("status") != "PASSED":
        raise DocumentFactError("ENUM_REFERENCE_ERROR: " + "; ".join(str(x) for x in enum_check.get("errors", [])))
    doc_root = doc_layout.validate_doc_root(doc_root, version)
    # P6-A: validate ubiquitous-language references before building archive payloads.
    # This is content validation only; lifecycle/mode archive gates remain outside this layer.
    try:
        terms = union_language.combined_terms(doc_root, version)
        ul_paths: list[Path] = []
        # Validate both the incoming version facts and the current project
        # baseline. A stale unresolved [term] in docs/ must not survive merely
        # because the current version does not touch that document.
        for base in (
            doc_layout.version_requirement_root(doc_root, version),
            doc_layout.version_module_root(doc_root, version),
            doc_root / "docs",
        ):
            if base.is_dir():
                ul_paths.extend(path for path in base.rglob("*.md") if path.name not in {"union_language.md", "union_language_changes.md"})
        ul_errors = union_language.validate_references(ul_paths, terms)
    except union_language.UnionLanguageError as exc:
        raise DocumentFactError(str(exc)) from exc
    if ul_errors:
        raise DocumentFactError("UNION_LANGUAGE_REFERENCE_ERROR: " + "; ".join(ul_errors))
    module_source = doc_layout.module_dir(doc_root, version, module) / f"{module}_desc.md"
    _, _, extra_documents = synchronize_module_desc_source(module_source, module, version)
    result: list[dict[str, object]] = []
    for artifact_type, mode, source, target in _source_paths(doc_root, version, module):
        if not source.is_file():
            raise DocumentFactError(f"missing current-format version fact: {source}; run merge_docs.py init first")
        source_text = source.read_text(encoding="utf-8")
        target_text = target.read_text(encoding="utf-8") if target.is_file() else None
        if artifact_type == "requirement_list":
            validate_requirement_list_links(source, source_text)
            merged_text = merge_requirement_list(source_text, target_text)
            _, _, source_table = find_table(source_text.splitlines(), "## 需求列表", REQUIREMENT_HEADERS)
            meaningful = bool(source_table.rows)
            preview_name = "requirement_list_preview.md"
        else:
            merged_text = merge_module_desc(
                source_text,
                target_text,
                module,
                version,
                extra_documents=extra_documents,
            )
            parsed = _parse_module_desc(source_text, module)
            info = parsed["info"]
            meaningful_info = any(
                not _placeholder(str(info.get(field, "")))
                for field in ("模块名称", "模块说明", "职责边界")
            )
            meaningful = bool(
                parsed["capabilities"]
                or parsed["features"]
                or parsed["information"]
                or parsed["enums"]
                or parsed["rules"]
                or parsed["dependencies"]
                or parsed["pages"]
                or _manual_document_refs(parsed["documents"], module)
                or extra_documents
                or meaningful_info
            )
            preview_name = f"{module}_desc_preview.md"
        style_errors, _style_warnings = document_style.check_text(
            Path(f"<generated:{artifact_type}>"), merged_text, ready=False
        )
        if style_errors:
            raise DocumentFactError(
                "DOCUMENT_STYLE_ERROR: " + "; ".join(style_errors)
            )
        source_bytes = source_text.encode("utf-8")
        target_bytes = target_text.encode("utf-8") if target_text is not None else b""
        merged_bytes = merged_text.encode("utf-8")
        if not meaningful or merged_bytes == target_bytes:
            continue
        source_digest = sha256_bytes(source_bytes)
        target_digest = sha256_bytes(target_bytes)
        merged_digest = sha256_bytes(merged_bytes)
        result.append(
            {
                "artifactType": artifact_type,
                "sourceRef": str(source),
                "targetRef": str(target),
                "mergeMode": mode,
                "sourceRevision": f"{artifact_type.upper()}@{source_digest[:12]}",
                "resultRevision": f"{artifact_type.upper()}@{merged_digest[:12]}",
                "status": "READY",
                "sourceDigest": source_digest,
                "targetDigest": target_digest,
                "resultDigest": merged_digest,
                "evidenceRefs": [],
                "reason": "当前格式 Markdown 事实由 wk -d 确定性合并",
                "previewName": preview_name,
                "resultContent": merged_bytes,
                "sourcePath": source,
                "targetPath": target,
            }
        )
    union_source = union_language.changes_path(doc_root, version)
    if union_source.is_file():
        union_target = union_language.project_path(doc_root)
        source_text = union_source.read_text(encoding="utf-8")
        source_terms = union_language.parse_terms(source_text, changes=True)
        target_text = union_target.read_text(encoding="utf-8") if union_target.is_file() else union_language.render_project([])
        target_terms = union_language.parse_terms(target_text, changes=False)
        try:
            merged_terms = union_language.merge_terms(source_terms, target_terms)
        except union_language.UnionLanguageError as exc:
            raise DocumentFactError(str(exc)) from exc
        merged_text = union_language.render_project(merged_terms)
        style_errors, _style_warnings = document_style.check_text(
            Path("<generated:union_language>"), merged_text, ready=False
        )
        if style_errors:
            raise DocumentFactError(
                "DOCUMENT_STYLE_ERROR: " + "; ".join(style_errors)
            )
        if source_terms and merged_text.encode("utf-8") != target_text.encode("utf-8"):
            source_bytes = source_text.encode("utf-8"); target_bytes = target_text.encode("utf-8"); merged_bytes = merged_text.encode("utf-8")
            source_digest = sha256_bytes(source_bytes); target_digest = sha256_bytes(target_bytes); merged_digest = sha256_bytes(merged_bytes)
            result.append({
                "artifactType": "union_language",
                "sourceRef": str(union_source), "targetRef": str(union_target),
                "mergeMode": "SEMANTIC_UPSERT",
                "sourceRevision": f"UNION_LANGUAGE@{source_digest[:12]}",
                "resultRevision": f"UNION_LANGUAGE@{merged_digest[:12]}",
                "status": "READY", "sourceDigest": source_digest, "targetDigest": target_digest, "resultDigest": merged_digest,
                "evidenceRefs": [], "reason": "当前版本统一语言增量由 wk -d 语义合并并执行冲突检测",
                "previewName": "union_language_preview.md", "resultContent": merged_bytes, "sourcePath": union_source, "targetPath": union_target,
            })
    return result


def public_record(record: dict[str, object], evidence_ref: str | None = None) -> dict[str, object]:
    keys = (
        "artifactType",
        "sourceRef",
        "targetRef",
        "mergeMode",
        "sourceRevision",
        "resultRevision",
        "status",
        "sourceDigest",
        "targetDigest",
        "resultDigest",
        "reason",
    )
    result = {key: record[key] for key in keys}
    result["evidenceRefs"] = [evidence_ref] if evidence_ref else list(record.get("evidenceRefs", []))
    return result


def write_previews(root: Path, records: list[dict[str, object]]) -> list[dict[str, object]]:
    root.mkdir(parents=True, exist_ok=True)
    write_preview_files(
        records,
        path_for=lambda record: root / str(record["previewName"]),
        content_for=lambda record: bytes(record["resultContent"]),
    )
    return [public_record(record, str(root / str(record["previewName"]))) for record in records]


def verify_ready_record(record: dict[str, object], doc_root: Path) -> None:
    artifact_type = str(record.get("artifactType", ""))
    if artifact_type not in {"requirement_list", "module_desc", "union_language"}:
        raise DocumentFactError(f"READY is only supported for automatic current-format documents: {artifact_type}")
    source = Path(str(record.get("sourceRef", "")))
    target = Path(str(record.get("targetRef", "")))
    if not source.is_absolute():
        source = (doc_root.parent / source) if str(source).startswith(str(doc_root.name) + "/") else doc_root / source
    if not target.is_absolute():
        target = (doc_root.parent / target) if str(target).startswith(str(doc_root.name) + "/") else doc_root / target
    if not source.is_file():
        raise DocumentFactError(f"document merge source does not exist: {source}")
    source_digest = sha256_bytes(source.read_bytes())
    target_digest = sha256_bytes(target.read_bytes() if target.is_file() else b"")
    if source_digest != record.get("sourceDigest"):
        raise DocumentFactError(f"document merge source changed after preview: {source}")
    if target_digest != record.get("targetDigest"):
        raise DocumentFactError(f"document merge target changed after preview: {target}")
    source_text = source.read_text(encoding="utf-8")
    target_text = target.read_text(encoding="utf-8") if target.is_file() else None
    if artifact_type == "requirement_list":
        validate_requirement_list_links(source, source_text)
        result_text = merge_requirement_list(source_text, target_text)
    elif artifact_type == "union_language":
        source_terms = union_language.parse_terms(source_text, changes=True)
        baseline = union_language.parse_terms(target_text, changes=False) if target_text else []
        result_text = union_language.render_project(union_language.merge_terms(source_terms, baseline))
    else:
        module = target.name.removesuffix("_desc.md")
        try:
            version_index = source.parts.index("version") + 1
            version = source.parts[version_index]
        except (ValueError, IndexError):
            version = "CURRENT"
        extra_documents = discover_extra_documents(source.parent, module)
        result_text = merge_module_desc(
            source_text,
            target_text,
            module,
            version,
            extra_documents=extra_documents,
        )
    result_digest = sha256_bytes(result_text.encode("utf-8"))
    if result_digest != record.get("resultDigest"):
        raise DocumentFactError(f"document merge result digest is stale: {target}")
