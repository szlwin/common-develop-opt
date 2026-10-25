"""Canonical discovery and identity checks for version page-design documents."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from document_core import reference_resolution, page_relations


PAGE_ID_RE = re.compile(r"\bPAGE-[A-Z0-9_]+-[A-Z0-9_-]+\b")
PAGE_ACTION_ID_RE = re.compile(r"\bPAGEACT-[A-Z0-9_]+-[A-Z0-9_-]+\b")
NEW_PAGE_PATTERN = "*_design.md"


class PageDocumentError(ValueError):
    """Raised when page identity, ownership, or layout is ambiguous."""


@dataclass(frozen=True)
class PageDocument:
    path: Path
    module: str
    primary_page_id: str
    page_name: str
    document_stem: str
    page_ids: tuple[str, ...]
    page_action_ids: tuple[str, ...]
    legacy: bool = False

    @property
    def stable_id(self) -> str:
        return self.primary_page_id or f"PAGE-{self.module}-BEHAVIOR"


def _content(path: Path, pending: Mapping[Path, bytes]) -> str:
    resolved = path.resolve()
    if resolved in pending:
        return pending[resolved].decode("utf-8")
    return path.read_text(encoding="utf-8")


def _paths(directory: Path, pending: Mapping[Path, bytes], pattern: str) -> list[Path]:
    values = set(directory.glob(pattern)) if directory.is_dir() else set()
    for candidate in pending:
        try:
            relative = candidate.relative_to(directory.resolve())
        except ValueError:
            continue
        if len(relative.parts) == 1 and relative.match(pattern):
            values.add(candidate)
    return sorted(
        (Path(item) for item in values if Path(item).resolve() in pending or Path(item).is_file()),
        key=lambda item: item.name.casefold(),
    )


def _table_values(text: str, label: str) -> list[str]:
    values: list[str] = []
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) >= 2 and cells[0] == label:
            values.append(cells[1])
    return values


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            return re.sub(r"页面业务行为设计(?:\s+Demo)?$", "", title).strip() or fallback
    return fallback


def _new_page(path: Path, module: str, text: str) -> PageDocument:
    module_values = _table_values(text, "模块")
    if len(module_values) != 1 or module_values[0] != module:
        raise PageDocumentError(
            f"page document module declaration must equal owner directory {module}: {path}"
        )
    primary_values = _table_values(text, "主页面编号")
    if len(primary_values) != 1:
        raise PageDocumentError(f"page document must declare exactly one 主页面编号: {path}")
    primary_ids = PAGE_ID_RE.findall(primary_values[0])
    if len(primary_ids) != 1 or primary_values[0].strip("` ") != primary_ids[0]:
        raise PageDocumentError(f"主页面编号 must contain exactly one bare PAGE-* ID: {path}")
    primary_page_id = primary_ids[0]
    if not primary_page_id.startswith(f"PAGE-{module}-"):
        raise PageDocumentError(
            f"page owner namespace does not match module {module}: {primary_page_id} in {path}"
        )
    page_names = _table_values(text, "页面中文名称")
    page_name = page_names[0].strip() if len(page_names) == 1 else _title(text, primary_page_id)
    if not page_name:
        raise PageDocumentError(f"page document is missing 页面中文名称: {path}")
    page_ids = tuple(sorted(set(PAGE_ID_RE.findall(text))))
    if primary_page_id not in page_ids:
        raise PageDocumentError(f"primary PAGE ID is not present in page document: {path}")
    return PageDocument(
        path=path,
        module=module,
        primary_page_id=primary_page_id,
        page_name=page_name,
        document_stem=path.stem,
        page_ids=page_ids,
        page_action_ids=tuple(sorted(set(PAGE_ACTION_ID_RE.findall(text)))),
    )


def _legacy_page(path: Path, module: str, text: str) -> PageDocument:
    return PageDocument(
        path=path,
        module=module,
        primary_page_id="",
        page_name=f"{module} 旧版页面行为",
        document_stem=path.stem,
        page_ids=tuple(sorted(set(PAGE_ID_RE.findall(text)))),
        page_action_ids=tuple(sorted(set(PAGE_ACTION_ID_RE.findall(text)))),
        legacy=True,
    )


def discover_module_page_documents(
    module_dir: Path,
    module: str,
    pending: Mapping[Path, bytes] | None = None,
) -> list[PageDocument]:
    """Discover new per-page documents plus the optional read-only legacy source."""
    pending_map = {Path(path).resolve(): bytes(content) for path, content in (pending or {}).items()}
    page_dir = module_dir / "page-design"
    documents = [
        _new_page(path, module, _content(path, pending_map))
        for path in _paths(page_dir, pending_map, NEW_PAGE_PATTERN)
    ]
    by_primary: dict[str, Path] = {}
    for document in documents:
        previous = by_primary.get(document.primary_page_id)
        if previous is not None:
            raise PageDocumentError(
                f"duplicate primary PAGE ID {document.primary_page_id}: {previous} and {document.path}"
            )
        by_primary[document.primary_page_id] = document.path

    legacy_path = module_dir / f"{module}_page_design.md"
    if legacy_path.resolve() in pending_map or legacy_path.is_file():
        legacy = _legacy_page(legacy_path, module, _content(legacy_path, pending_map))
        overlap = sorted(set(legacy.page_ids) & set(by_primary))
        if overlap:
            raise PageDocumentError(
                f"legacy and canonical page documents declare the same PAGE IDs: {overlap}"
            )
        documents.append(legacy)
    return documents


def validate_page_document_links(
    doc_root: Path,
    documents: list[PageDocument],
    pending: Mapping[Path, bytes] | None = None,
) -> list[str]:
    pending_map = {Path(path).resolve(): bytes(content) for path, content in (pending or {}).items()}
    errors: list[str] = []
    for document in documents:
        text = _content(document.path, pending_map)
        errors.extend(page_relations.validate_relations(document.path, text, doc_root, pending_map))
        errors.extend(
            reference_resolution.validate_markdown_links(
                document.path,
                text,
                doc_root,
                pending=pending_map,
            )
        )
    return errors


__all__ = [
    "NEW_PAGE_PATTERN",
    "PAGE_ACTION_ID_RE",
    "PAGE_ID_RE",
    "PageDocument",
    "PageDocumentError",
    "discover_module_page_documents",
    "validate_page_document_links",
]
