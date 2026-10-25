"""Opt-in page selection/return contracts; no browser or test execution."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

from document_core import reference_resolution as refs
from document_core.tables import table_after_marker

RELATION_COLUMNS = (
    "关联编号", "来源操作", "目标页面", "目标操作", "展示方式", "输入上下文",
    "选择方式", "返回与回填", "取消行为",
)
CALLER_COLUMNS = ("关联编号", "来源页面", "来源操作")
CASE_COLUMNS = ("关联编号", "来源页面", "验证场景", "返回断言")
RELATION_ID = re.compile(r"PAGEREL-[A-Z0-9_]+-[A-Z0-9_-]+")
PAGE_ID = re.compile(r"PAGE-[A-Z0-9_]+-[A-Z0-9_-]+")
ACTION_ID = re.compile(r"PAGEACT-[A-Z0-9_]+-[A-Z0-9_-]+")


def _concrete(value: str) -> bool:
    value = re.sub(r"[`*]", "", value).strip()
    return bool(value) and not re.fullmatch(
        r"-|无|正确|正常|合法数据|预期结果|待补充|待确认|TODO|TBD", value, re.I
    ) and not re.search(r"\{[A-Z][A-Z0-9_-]*\}", value)


def _document(path: Path, pending: Mapping[Path, bytes]) -> str:
    data = pending.get(path.resolve())
    return data.decode("utf-8") if data is not None else path.read_text(encoding="utf-8")


def _visible(text: str) -> str:
    # Preserve table marker comments but ignore code samples and other comments.
    markers = ("PAGE-RELATIONS", "PAGE-CALLERS", "TEST-CASE-PAGE-RELATIONS")
    for marker in markers:
        text = text.replace(f"<!-- {marker} -->", f"@@{marker}@@")
    chars = list(text)
    refs._mask_block_code(text, chars)
    text = re.sub(r"<!--.*?-->", "__COMMENT__", "".join(chars), flags=re.S)
    for marker in markers:
        text = text.replace(f"@@{marker}@@", f"<!-- {marker} -->")
    return text


def _rows(text: str, marker: str, columns: tuple[str, ...]) -> list[dict[str, str]]:
    text = _visible(text)
    token = f"<!-- {marker} -->"
    if token not in text:
        return []
    if text.count(token) != 1:
        raise ValueError(f"{marker}: duplicate table marker")
    # Prevent a missing table from accidentally consuming a later section's table.
    tail = text.split(token, 1)[1].lstrip()
    if not tail.startswith("|"):
        raise ValueError(f"{marker}: table must immediately follow marker")
    header, rows = table_after_marker(text, marker)
    if tuple(header) != columns:
        raise ValueError(f"{marker}: expected columns {columns}")
    result = []
    seen = set()
    for row in rows:
        if len(row) != len(columns) or any(not value.strip() for value in row):
            raise ValueError(f"{marker}: incomplete row")
        item = dict(zip(header, row))
        rid = item["关联编号"]
        if not RELATION_ID.fullmatch(rid):
            raise ValueError(f"{marker}: invalid relation ID {rid}")
        if rid in seen:
            raise ValueError(f"{marker}: duplicate relation ID {rid}")
        seen.add(rid)
        result.append(item)
    return result


def _page(path: Path, text: str) -> str:
    # Use the same identity/Owner parser as page discovery, not ID mentions.
    from document_core.page_documents import _new_page
    if path.parent.name != "page-design" or not path.name.endswith("_design.md"):
        raise ValueError(f"relation target must be a canonical page-design/*_design.md: {path}")
    return _new_page(path, path.parent.parent.name, text).primary_page_id


def _actions(text: str) -> set[str]:
    # Only definitions in the operations table count, not cross-reference mentions.
    actions = set()
    in_operations = False
    for line in _visible(text).splitlines():
        if not line.strip().startswith("|"):
            if line.strip():
                in_operations = False
            continue
        cells = [value.strip() for value in line.strip().strip("|").split("|")]
        if cells[:2] == ["操作编号", "页面/组件"]:
            in_operations = True
        elif in_operations and ACTION_ID.fullmatch(cells[0].strip("`")):
            actions.add(cells[0].strip("`"))
    return actions


def _linked_page(cell: str, source: Path, root: Path, pending: Mapping[Path, bytes]) -> tuple[Path, str, str]:
    # Typed contract cells use one inline link; ordinary prose retains full Markdown support.
    match = re.fullmatch(r"\[(PAGE-[A-Z0-9_]+-[A-Z0-9_-]+)\]\(([^\n]+)\)", cell)
    if not match:
        raise ValueError(f"expected one [PAGE-*](relative-page-path) link: {cell}")
    errors = refs.validate_markdown_links(source, cell, root, pending=pending)
    if errors:
        raise ValueError("; ".join(errors))
    path_part = refs.markdown_link_path(match[2])
    if re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", path_part):
        raise ValueError("page relation must reference a local page")
    target = (source.parent / path_part).resolve() if path_part else source.resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("page relation escapes DOC_ROOT")
    text = _document(target, pending)
    identity = _page(target, text)
    if identity != match[1]:
        raise ValueError(f"page link label does not match target primary ID: {cell}")
    from document_core.page_documents import discover_module_page_documents
    discover_module_page_documents(target.parent.parent, target.parent.parent.name, pending)
    return target, text, identity


def relation_records(source: Path, text: str, root: Path, pending: Mapping[Path, bytes] | None = None) -> list[dict]:
    pending = {Path(p).resolve(): value for p, value in (pending or {}).items()}
    records = []
    rows = _rows(text, "PAGE-RELATIONS", RELATION_COLUMNS)
    if not rows:
        return records
    primary = _page(source, text)
    # Relation identity belongs to the source module, across all of its pages.
    from document_core.page_documents import discover_module_page_documents
    owners: dict[str, Path] = {}
    for document in discover_module_page_documents(source.parent.parent, source.parent.parent.name, pending):
        document_text = text if document.path.resolve() == source.resolve() else _document(document.path, pending)
        for item in _rows(document_text, "PAGE-RELATIONS", RELATION_COLUMNS):
            rid = item["关联编号"]
            if rid in owners:
                raise ValueError(f"duplicate relation ID {rid} in {owners[rid]} and {document.path}")
            owners[rid] = document.path
    for row in rows:
        rid = row["关联编号"]
        if not rid.startswith(f"PAGEREL-{source.parent.parent.name}-"):
            raise ValueError(f"{rid}: relation Owner must equal source module")
        if row["来源操作"] not in _actions(text) or not row["来源操作"].startswith(f"PAGEACT-{source.parent.parent.name}-"):
            raise ValueError(f"{rid}: source action is not defined in operations table")
        target, target_text, target_id = _linked_page(row["目标页面"], source, root, pending)
        if row["目标操作"] not in _actions(target_text) or not row["目标操作"].startswith(f"PAGEACT-{target.parent.parent.name}-"):
            raise ValueError(f"{rid}: target action is not defined in operations table")
        if row["展示方式"] not in {"MODAL", "DRAWER", "ROUTE", "INLINE"}:
            raise ValueError(f"{rid}: unsupported presentation")
        if row["选择方式"] not in {"SINGLE", "MULTIPLE", "NONE"}:
            raise ValueError(f"{rid}: unsupported selection mode")
        for key in ("输入上下文", "返回与回填", "取消行为"):
            value = row[key]
            if not _concrete(value):
                raise ValueError(f"{rid}: non-actionable {key}")
        callers = _rows(target_text, "PAGE-CALLERS", CALLER_COLUMNS)
        matches = [item for item in callers if item["关联编号"] == rid]
        if len(matches) != 1:
            raise ValueError(f"{rid}: target requires one reverse caller entry")
        caller, _, _ = _linked_page(matches[0]["来源页面"], target, root, pending)
        if caller != source.resolve() or matches[0]["来源操作"] != row["来源操作"]:
            raise ValueError(f"{rid}: reverse caller entry disagrees with source")
        records.append({**row, "source_id": primary, "target_id": target_id, "target_path": target})
    return records


def validate_relations(source: Path, text: str, root: Path, pending: Mapping[Path, bytes] | None = None) -> list[str]:
    pending = {Path(p).resolve(): value for p, value in (pending or {}).items()}
    try:
        relation_records(source, text, root, pending)
        for caller in _rows(text, "PAGE-CALLERS", CALLER_COLUMNS):
            path, source_text, _ = _linked_page(caller["来源页面"], source, root, pending)
            records = relation_records(path, source_text, root, pending)
            if not any(row["关联编号"] == caller["关联编号"] and row["target_path"] == source.resolve()
                       and row["来源操作"] == caller["来源操作"] for row in records):
                raise ValueError(f"{caller['关联编号']}: orphan reverse caller entry")
    except (ValueError, OSError, UnicodeError) as exc:
        return [f"page relation error in {source}: {exc}"]
    return []


def validate_case_relations(source: Path, block: str, root: Path) -> list[str]:
    try:
        rows = _rows(block, "TEST-CASE-PAGE-RELATIONS", CASE_COLUMNS)
        _, trace_rows = table_after_marker(block, "TEST-CASE-TRACE")
        trace = " ".join(" ".join(row) for row in trace_rows)
        for row in rows:
            path, text, _ = _linked_page(row["来源页面"], source, root, {})
            matches = [item for item in relation_records(path, text, root) if item["关联编号"] == row["关联编号"]]
            if len(matches) != 1:
                raise ValueError(f"{row['关联编号']}: Case refers to an undefined page relation")
            relation = matches[0]
            for identity in (relation["source_id"], relation["target_id"], relation["来源操作"], relation["目标操作"]):
                if not re.search(rf"(?<![\w-]){re.escape(identity)}(?![\w-])", trace):
                    raise ValueError(f"{row['关联编号']}: Case trace is missing {identity}")
            for key in ("验证场景", "返回断言"):
                if not _concrete(row[key]):
                    raise ValueError(f"{row['关联编号']}: Case requires concrete {key}")
        declared = set(RELATION_ID.findall(trace))
        covered = {row["关联编号"] for row in rows}
        if declared != covered:
            raise ValueError("Case relation table and trace PAGEREL IDs must agree")
    except (ValueError, OSError, UnicodeError) as exc:
        return [f"page relation Case error: {exc}"]
    return []
