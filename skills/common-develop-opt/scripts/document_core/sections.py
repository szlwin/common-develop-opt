"""Parse/select physical Markdown sections under an explicit package root."""
from __future__ import annotations
import re,unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

HEADING_RE = re.compile(r"^(#{2,3})\s+(.+?)\s*$")
STABLE_ID_RE = re.compile(r"\s+\{#([A-Za-z][A-Za-z0-9._:-]*)\}\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")


class ReferenceReadError(ValueError):
    """Raised when a routed Reference cannot be indexed or read safely."""


@dataclass(frozen=True)
class Section:
    section_id: str
    title: str
    level: int
    start_line: int
    end_line: int
    content: str
    stable: bool = False
    source_path: str = ''

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.section_id,
            "title": self.title,
            "level": self.level,
            "startLine": self.start_line,
            "endLine": self.end_line,
            "stable": self.stable,
            "path": self.source_path,
        }


def slugify(title: str) -> str:
    value = unicodedata.normalize("NFKC", re.sub(r"[`*_]", "", title)).casefold()
    chars: list[str] = []
    pending_dash = False
    for char in value:
        if char.isalnum():
            if pending_dash and chars:
                chars.append("-")
            chars.append(char)
            pending_dash = False
        else:
            pending_dash = True
    slug = "".join(chars).strip("-")
    return slug or "section"


def parse_sections(path: Path, *, levels: tuple[int, ...] = (2, 3)) -> tuple[str, list[Section]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    document_title = path.stem
    in_fence = False
    headings: list[tuple[int, int, str, str | None]] = []
    for index, line in enumerate(lines, 1):
        if FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.startswith("# ") and document_title == path.stem:
            document_title = line[2:].strip()
        match = HEADING_RE.match(line)
        if not match:
            continue
        level = len(match.group(1))
        if level in levels:
            raw_title = match.group(2).strip()
            stable_match = STABLE_ID_RE.search(raw_title)
            stable_id = stable_match.group(1) if stable_match else None
            title = STABLE_ID_RE.sub("", raw_title).strip() if stable_match else raw_title
            headings.append((index, level, title, stable_id))

    used: dict[str, int] = {}
    sections: list[Section] = []
    for offset, (start_line, level, title, stable_id) in enumerate(headings):
        end_line = len(lines)
        for next_start, next_level, _, _ in headings[offset + 1:]:
            if next_level <= level:
                end_line = next_start - 1
                break
        base = stable_id or slugify(title)
        if stable_id:
            if stable_id in used:
                raise ReferenceReadError(f"duplicate stable section id: {stable_id}")
            used[stable_id] = 1
            section_id = stable_id
        else:
            count = used.get(base, 0) + 1
            used[base] = count
            section_id = base if count == 1 else f"{base}-{count}"
        content = "\n".join(lines[start_line - 1:end_line]).rstrip() + "\n"
        sections.append(Section(section_id, title, level, start_line, end_line, content, stable=bool(stable_id), source_path=str(path)))
    return document_title, sections


def resource_sections(path: Path, *, root: Path):
    """Read the classified topic's detail parts as one logical Reference."""
    detail = path.with_name(path.stem + '.detail.md')
    for link in re.findall(r'\]\(([^)]+\.detail(?:\.part-[0-9]+)?\.md)(?:#[^)]*)?\)', path.read_text(encoding='utf-8')):
        target = (path.parent / link).resolve()
        if not target.is_relative_to(root.resolve()) or not target.is_file():
            raise ReferenceReadError(f'missing or external detail source: {link}')
    paths = [path]
    if detail.is_file():
        paths = [detail, *sorted(path.parent.glob(path.stem + '.detail.part-*.md'))]
    sections = []
    title = path.stem
    for source in paths:
        if not source.resolve().is_relative_to(root.resolve()):
            raise ReferenceReadError('detail source escapes the Skill root')
        for link in re.findall(r'\]\(([^)]+\.detail(?:\.part-[0-9]+)?\.md)(?:#[^)]*)?\)', source.read_text(encoding='utf-8')):
            target = (source.parent / link).resolve()
            if not target.is_relative_to(root.resolve()) or not target.is_file():
                raise ReferenceReadError(f'missing or external detail source: {link}')
        part_title, part = parse_sections(source)
        if not sections:
            title = part_title
        sections.extend(part)
    stable_ids = [section.section_id for section in sections if section.stable]
    if len(stable_ids) != len(set(stable_ids)):
        raise ReferenceReadError('duplicate stable section id across detail parts')
    return title, sections, paths


def _query_terms(query: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", query).casefold().strip()
    if not normalized:
        return []
    terms = [normalized]
    terms.extend(
        item for item in re.split(r"[\s,，、;；:/]+", normalized)
        if item and item not in terms
    )
    return terms


def select_sections(
    sections: list[Section], *, section_ids: Iterable[str] = (), query: str = "", max_sections: int = 3
) -> list[Section]:
    by_id = {section.section_id: section for section in sections}
    selected: list[Section] = []
    for section_id in section_ids:
        if section_id not in by_id:
            raise ReferenceReadError(f"unknown section id: {section_id}")
        if by_id[section_id] not in selected:
            selected.append(by_id[section_id])
    if selected:
        return selected

    terms = _query_terms(query)
    if not terms:
        return []
    scored: list[tuple[int, int, int, Section]] = []
    for index, section in enumerate(sections):
        title = unicodedata.normalize("NFKC", section.title).casefold()
        body = unicodedata.normalize("NFKC", section.content).casefold()
        score = 0
        for term in terms:
            if term in title:
                score += 10
            elif term in body:
                score += 2
        if score:
            scored.append((score, section.level, -index, section))
    scored.sort(reverse=True, key=lambda item: (item[0], item[1], item[2]))
    chosen: list[Section] = []
    for _, _, _, section in scored:
        overlaps = any(
            section.source_path == existing.source_path and not (section.end_line < existing.start_line or section.start_line > existing.end_line)
            for existing in chosen
        )
        if overlaps:
            continue
        chosen.append(section)
        if len(chosen) >= max(1, max_sections):
            break
    return chosen
