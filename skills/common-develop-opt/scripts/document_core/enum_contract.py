"""Module enum parsing, validation, rendering, and semantic merge."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

ENUM_HEADING_RE = re.compile(r"^###\s+(ENUM-[A-Z0-9._-]+)\s*/\s*(.+?)\s*$")
ENUM_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*$")
ENUM_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
ENUM_STATUSES = {"ACTIVE", "DEPRECATED", "DISABLED"}
ENUM_CHANGE_TYPES = {"ADD", "EXTEND", "MODIFY", "DEPRECATE", "CURRENT"}
META_HEADERS = ["属性", "内容"]
VALUE_HEADERS = ["标识", "值", "名称", "业务含义", "状态", "引入版本", "废弃版本"]


class EnumContractError(ValueError):
    pass


@dataclass
class EnumDefinition:
    stable_id: str
    title: str
    properties: dict[str, str]
    values: list[list[str]]

    @property
    def code(self) -> str:
        return self.properties.get("枚举编码", "")


def _split_row(line: str) -> list[str]:
    text = line.strip()
    if not (text.startswith("|") and text.endswith("|")):
        raise EnumContractError(f"invalid Markdown table row: {line}")
    return [item.strip().replace("<br>", "\n").strip("`") for item in text[1:-1].split("|")]


def _table(lines: list[str], headers: list[str], start: int = 0) -> tuple[int, int, list[list[str]]]:
    for i in range(start, len(lines) - 1):
        if lines[i].strip().startswith("|"):
            row = _split_row(lines[i])
            if row == headers:
                j = i + 2
                rows: list[list[str]] = []
                while j < len(lines) and lines[j].strip().startswith("|"):
                    rows.append(_split_row(lines[j]))
                    j += 1
                return i, j, rows
    raise EnumContractError(f"missing enum table: {headers}")


def parse_enums(text: str, module: str) -> list[EnumDefinition]:
    lines = text.splitlines()
    try:
        h2 = next(i for i, line in enumerate(lines) if line.strip() == "## 枚举")
    except StopIteration:
        return []
    end = len(lines)
    for i in range(h2 + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    definitions: list[EnumDefinition] = []
    starts = [i for i in range(h2 + 1, end) if ENUM_HEADING_RE.match(lines[i].strip())]
    for pos, start in enumerate(starts):
        block_end = starts[pos + 1] if pos + 1 < len(starts) else end
        block = lines[start:block_end]
        match = ENUM_HEADING_RE.match(block[0].strip())
        assert match
        stable_id, title = match.groups()
        _, _, meta_rows = _table(block, META_HEADERS, 1)
        props = {row[0]: row[1] for row in meta_rows if len(row) == 2 and row[0]}
        _, _, values = _table(block, VALUE_HEADERS, 1)
        definition = EnumDefinition(stable_id, title.strip(), props, values)
        validate_enum(definition, module)
        definitions.append(definition)
    codes = [item.code for item in definitions]
    if len(codes) != len(set(codes)):
        raise EnumContractError("duplicate enum code in module description")
    ids = [item.stable_id for item in definitions]
    if len(ids) != len(set(ids)):
        raise EnumContractError("duplicate enum stable ID in module description")
    return definitions


def validate_enum(item: EnumDefinition, module: str) -> None:
    required = ["枚举编码", "名称", "枚举分类", "值类型", "说明", "状态"]
    missing = [key for key in required if not item.properties.get(key)]
    if missing:
        raise EnumContractError(f"{item.stable_id} missing properties: {missing}")
    code = item.code
    if not ENUM_CODE_RE.fullmatch(code) or not code.startswith(module + "."):
        raise EnumContractError(f"enum code must belong to module {module}: {code}")
    if item.properties["状态"] not in ENUM_STATUSES:
        raise EnumContractError(f"invalid enum status for {code}: {item.properties['状态']}")
    change = item.properties.get("变更类型")
    if change and change not in ENUM_CHANGE_TYPES:
        raise EnumContractError(f"invalid enum change type for {code}: {change}")
    seen_symbols: set[str] = set()
    seen_values: dict[str, str] = {}
    for index, raw in enumerate(item.values, 1):
        if len(raw) != len(VALUE_HEADERS):
            raise EnumContractError(f"{code} value row {index} has invalid width")
        symbol, value, name, meaning, status, introduced, deprecated = [x.strip() for x in raw]
        if not ENUM_SYMBOL_RE.fullmatch(symbol):
            raise EnumContractError(f"{code} has invalid symbol: {symbol}")
        if not value or not name or not meaning or not introduced:
            raise EnumContractError(f"{code}.{symbol} has empty required fields")
        if status not in ENUM_STATUSES:
            raise EnumContractError(f"{code}.{symbol} has invalid status: {status}")
        if status == "DEPRECATED" and deprecated in {"", "-", "无"}:
            raise EnumContractError(f"{code}.{symbol} is DEPRECATED but has no 废弃版本")
        if symbol in seen_symbols:
            raise EnumContractError(f"duplicate enum symbol: {code}.{symbol}")
        if value in seen_values:
            raise EnumContractError(f"enum value reused by {code}.{seen_values[value]} and {code}.{symbol}: {value}")
        seen_symbols.add(symbol)
        seen_values[value] = symbol


def enum_anchor(code: str) -> str:
    return "enum-" + code.lower().replace(".", "-").replace("_", "-")


def render_enum(item: EnumDefinition, *, project: bool) -> list[str]:
    props = dict(item.properties)
    if project:
        props.pop("变更类型", None)
        props.pop("变更说明", None)
    ordered_keys = ["枚举编码", "名称", "枚举分类", "值类型", "说明", "状态"]
    if not project:
        ordered_keys += ["变更类型", "变更说明"]
    meta = [[key, props.get(key, "-")] for key in ordered_keys if key in props or not project]
    lines = [
        f'<a id="{enum_anchor(item.code)}"></a>',
        "",
        f"### {item.stable_id} / {item.title}",
        "",
        "| 属性 | 内容 |",
        "| --- | --- |",
        *[f"| {a} | {b} |" for a, b in meta],
        "",
        "| 标识 | 值 | 名称 | 业务含义 | 状态 | 引入版本 | 废弃版本 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        *["| " + " | ".join(row) + " |" for row in item.values],
        "",
    ]
    return lines


def render_enum_section(items: Iterable[EnumDefinition], *, project: bool) -> list[str]:
    lines = [
        "## 枚举",
        "",
        "> 本节是本模块枚举的唯一业务定义。API、DB、测试用例和页面设计只引用枚举编码与锚点，不重复定义值含义。",
        "",
    ]
    for item in items:
        lines.extend(render_enum(item, project=project))
    return lines


def merge_enums(source: list[EnumDefinition], target: list[EnumDefinition], module: str, version: str) -> list[EnumDefinition]:
    merged = {item.code: EnumDefinition(item.stable_id, item.title, dict(item.properties), [list(r) for r in item.values]) for item in target}
    order = [item.code for item in target]
    for src in source:
        validate_enum(src, module)
        current = merged.get(src.code)
        if current is None:
            values = [list(row) for row in src.values]
            props = dict(src.properties)
            props.pop("变更类型", None); props.pop("变更说明", None)
            merged[src.code] = EnumDefinition(src.stable_id, src.title, props, values)
            order.append(src.code)
            continue
        if src.properties.get("值类型") not in {"", current.properties.get("值类型")}:
            raise EnumContractError(f"ENUM_ARCHIVE_CONFLICT {src.code}: 值类型 cannot change")
        if src.stable_id != current.stable_id:
            raise EnumContractError(f"ENUM_ARCHIVE_CONFLICT {src.code}: stable ID changed")
        for key in ("名称", "枚举分类", "说明", "状态"):
            value = src.properties.get(key)
            if value:
                current.properties[key] = value
        by_symbol = {row[0]: row for row in current.values}
        by_value = {row[1]: row[0] for row in current.values}
        for row in src.values:
            symbol, value = row[0], row[1]
            if symbol in by_symbol:
                existing = by_symbol[symbol]
                if existing[1] != value:
                    raise EnumContractError(f"ENUM_ARCHIVE_CONFLICT {src.code}.{symbol}: published value cannot change")
                existing[:] = list(row)
            else:
                owner = by_value.get(value)
                if owner:
                    raise EnumContractError(f"ENUM_ARCHIVE_CONFLICT {src.code}: value {value} already belongs to {owner}")
                current.values.append(list(row))
                by_symbol[symbol] = current.values[-1]
                by_value[value] = symbol
        current.properties.pop("变更类型", None); current.properties.pop("变更说明", None)
        validate_enum(current, module)
    return [merged[code] for code in order]
