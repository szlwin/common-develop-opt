"""Shared document parsing helpers used by structured-document commands."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

REFERENCE_PLACEHOLDERS = frozenset({
    "-", "待补充", "无", "不适用", "N/A", "CURRENT", "{VERSION}",
})
_REFERENCE_SEPARATOR_RE = re.compile(r"[、,，;；\s]+")
_TABLE_SEPARATOR_RE = re.compile(r":?-{3,}:?")


def split_refs(value: str | None) -> list[str]:
    """Split a reference cell using the canonical placeholder and separators."""
    cleaned = (value or "").strip()
    if not cleaned or cleaned in REFERENCE_PLACEHOLDERS:
        return []
    if cleaned.upper() == "N/A":
        return []
    return [part.strip() for part in _REFERENCE_SEPARATOR_RE.split(cleaned) if part.strip()]


def _parse_table_lines(table_lines: list[str]) -> tuple[list[str], list[list[str]]]:
    if len(table_lines) < 2:
        return [], []
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in table_lines]
    header = rows[0]
    body = [row for row in rows[1:] if not all(_TABLE_SEPARATOR_RE.fullmatch(cell or "") for cell in row)]
    return header, body


def table_after_marker(text: str, marker: str) -> tuple[list[str], list[list[str]]]:
    """Read the first contiguous Markdown table following an HTML marker."""
    token = f"<!-- {marker} -->"
    start = text.find(token)
    if start < 0:
        return [], []
    table_lines: list[str] = []
    started = False
    for line in text[start + len(token):].splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            started = True
            table_lines.append(stripped)
            continue
        if started and stripped:
            break
    return _parse_table_lines(table_lines)


def table_rows_in_section(
    text: str,
    heading: str,
    next_headings: Iterable[str],
    section_reader,
) -> list[dict[str, str]]:
    """Read well-formed rows from a heading-delimited Markdown section."""
    body = section_reader(text, heading, next_headings)
    table_lines = [line.strip() for line in body.splitlines() if line.strip().startswith("|")]
    header, rows = _parse_table_lines(table_lines)
    return [dict(zip(header, row)) for row in rows if len(row) == len(header)]


