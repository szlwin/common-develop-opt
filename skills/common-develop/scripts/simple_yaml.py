#!/usr/bin/env python3
"""A deterministic YAML subset used by common-develop structured documents.

Supported: indentation-based mappings/lists, quoted or plain scalars, booleans,
null, numbers, and JSON-style inline arrays/objects. Anchors, tags, merge keys,
multiline scalars, tabs, and implicit dates are intentionally unsupported.
"""

from __future__ import annotations

import ast
import json
import math
import re
from pathlib import Path
from typing import Any


class YamlError(ValueError):
    pass


def _scalar(text: str) -> Any:
    value = text.strip()
    if not value:
        return ""
    lowered = value.lower()
    if lowered in {"null", "~"}:
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if value[0:1] in {"'", '"'}:
        try:
            return ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise YamlError(f"invalid quoted scalar: {value}") from exc
    if value[0:1] in {"[", "{"}:
        try:
            return json.loads(value)
        except json.JSONDecodeError as exc:
            raise YamlError(f"inline collections must use JSON syntax: {value}") from exc
    if re.fullmatch(r"[-+]?\d+", value):
        return int(value)
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\d*\.\d+)(?:[eE][-+]?\d+)?", value):
        return float(value)
    if value in {"|", ">"} or value.startswith(("&", "*", "!")):
        raise YamlError(f"unsupported YAML feature: {value}")
    if re.search(r"\s#", value):
        raise YamlError("inline comments are unsupported; quote '#' when it is data")
    return value


def _key_value(content: str, line_number: int) -> tuple[str, str]:
    if ":" not in content:
        raise YamlError(f"line {line_number}: mapping entry must contain ':'")
    key, value = content.split(":", 1)
    key = key.strip()
    if not key:
        raise YamlError(f"line {line_number}: mapping key must not be empty")
    if key[0:1] in {"'", '"'}:
        key = str(_scalar(key))
    return key, value.strip()


def loads(text: str) -> Any:
    rows: list[tuple[int, str, int]] = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        if "\t" in raw:
            raise YamlError(f"line {line_number}: tabs are not supported")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped == "---":
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent % 2:
            raise YamlError(f"line {line_number}: indentation must use multiples of two spaces")
        rows.append((indent, raw[indent:], line_number))
    if not rows:
        return {}
    if rows[0][0] != 0:
        raise YamlError(f"line {rows[0][2]}: document root must start at indentation 0")
    if len(rows) == 1:
        content = rows[0][1]
        if content.startswith(("[", "{", "'", '"')) or ":" not in content:
            return _scalar(content)

    def parse_node(index: int, indent: int) -> tuple[Any, int]:
        if index >= len(rows):
            raise YamlError("unexpected end of document")
        actual, content, line_number = rows[index]
        if actual != indent:
            raise YamlError(f"line {line_number}: expected indentation {indent}, got {actual}")
        if content == "-" or content.startswith("- "):
            return parse_list(index, indent)
        return parse_map(index, indent)

    def parse_map(index: int, indent: int) -> tuple[dict[str, Any], int]:
        result: dict[str, Any] = {}
        while index < len(rows):
            actual, content, line_number = rows[index]
            if actual < indent:
                break
            if actual > indent:
                raise YamlError(f"line {line_number}: unexpected indentation")
            if content == "-" or content.startswith("- "):
                break
            key, raw_value = _key_value(content, line_number)
            if key in result:
                raise YamlError(f"line {line_number}: duplicate key '{key}'")
            index += 1
            if raw_value:
                result[key] = _scalar(raw_value)
            elif index < len(rows) and rows[index][0] > indent:
                if rows[index][0] != indent + 2:
                    raise YamlError(
                        f"line {rows[index][2]}: nested mapping/list must indent exactly two spaces"
                    )
                result[key], index = parse_node(index, indent + 2)
            else:
                result[key] = None
        return result, index

    def parse_list(index: int, indent: int) -> tuple[list[Any], int]:
        result: list[Any] = []
        while index < len(rows):
            actual, content, line_number = rows[index]
            if actual < indent:
                break
            if actual > indent:
                raise YamlError(f"line {line_number}: unexpected indentation")
            if not (content == "-" or content.startswith("- ")):
                break
            rest = content[1:].strip()
            index += 1
            if not rest:
                if index >= len(rows) or rows[index][0] <= indent:
                    result.append(None)
                else:
                    if rows[index][0] != indent + 2:
                        raise YamlError(
                            f"line {rows[index][2]}: list item content must indent exactly two spaces"
                        )
                    item, index = parse_node(index, indent + 2)
                    result.append(item)
                continue
            if not re.match(r"^[^:]+:(?:\s|$)", rest):
                result.append(_scalar(rest))
                continue
            key, raw_value = _key_value(rest, line_number)
            item: dict[str, Any] = {}
            if raw_value:
                item[key] = _scalar(raw_value)
            elif index < len(rows) and rows[index][0] > indent + 2:
                if rows[index][0] != indent + 4:
                    raise YamlError(
                        f"line {rows[index][2]}: nested value in list mapping must indent exactly four spaces"
                    )
                item[key], index = parse_node(index, indent + 4)
            else:
                item[key] = None
            if index < len(rows) and rows[index][0] == indent + 2:
                continuation, index = parse_map(index, indent + 2)
                for continuation_key, continuation_value in continuation.items():
                    if continuation_key in item:
                        raise YamlError(
                            f"line {line_number}: duplicate list mapping key '{continuation_key}'"
                        )
                    item[continuation_key] = continuation_value
            result.append(item)
        return result, index

    document, end = parse_node(0, rows[0][0])
    if end != len(rows):
        _, _, line_number = rows[end]
        raise YamlError(f"line {line_number}: unparsed content")
    return document


def load(path: Path | str) -> Any:
    return loads(Path(path).read_text(encoding="utf-8"))


def _key(key: str) -> str:
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", key):
        return key
    return json.dumps(key, ensure_ascii=False)


def _dump_scalar(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise YamlError("non-finite floats are unsupported")
        return str(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    raise YamlError(f"unsupported scalar type: {type(value).__name__}")


def dumps(data: Any) -> str:
    if data == {}:
        return "{}\n"
    if data == []:
        return "[]\n"
    lines: list[str] = []

    def emit(value: Any, indent: int) -> None:
        prefix = " " * indent
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str):
                    raise YamlError(f"mapping keys must be strings, got {type(key).__name__}")
                if isinstance(child, (dict, list)):
                    if not child:
                        lines.append(f"{prefix}{_key(str(key))}: {'{}' if isinstance(child, dict) else '[]'}")
                    else:
                        lines.append(f"{prefix}{_key(str(key))}:")
                        emit(child, indent + 2)
                else:
                    lines.append(f"{prefix}{_key(str(key))}: {_dump_scalar(child)}")
            return
        if isinstance(value, list):
            for child in value:
                if isinstance(child, (dict, list)):
                    if not child:
                        lines.append(f"{prefix}- {'{}' if isinstance(child, dict) else '[]'}")
                    else:
                        lines.append(f"{prefix}-")
                        emit(child, indent + 2)
                else:
                    lines.append(f"{prefix}- {_dump_scalar(child)}")
            return
        lines.append(f"{prefix}{_dump_scalar(value)}")

    emit(data, 0)
    return "\n".join(lines) + "\n"


def dump(data: Any, path: Path | str) -> None:
    Path(path).write_text(dumps(data), encoding="utf-8")
