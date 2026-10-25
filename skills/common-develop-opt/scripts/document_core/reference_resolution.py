#!/usr/bin/env python3
"""Canonical, fail-closed resolution for project artifact references.

References are deliberately resolved by artifact structure rather than substring
search.  Markdown fragments resolve to real headings/explicit anchors; JSON,
YAML and JSONL fragments resolve to JSON Pointer targets or exact structured
stable IDs.  Relative paths are always relative to the declaration document
(the project root is used only when no declaration source is available).
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import quote, unquote

if __package__:
    from . import simple_yaml
else:
    from document_core import simple_yaml

_REFERENCE_ID_RE = re.compile(r"^[A-Z][A-Z0-9_.-]+$")
_PATH_SUFFIXES = {".md", ".markdown", ".json", ".jsonl", ".yaml", ".yml", ".txt"}
_IGNORED_PARTS = {".git", "cache", "generated", "evidence", "objects"}
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")
_BRACE_ANCHOR_RE = re.compile(r"\{#([A-Za-z0-9_.:-]+)\}")
_ANCHOR_RE = re.compile(
    r"<(?:a|span)\b[^>]*\bid\s*=\s*(?:\"([^\"]+)\"|'([^']+)'|([^\s>]+))[^>]*>",
    re.IGNORECASE,
)
_MARKDOWN_LINK_RE = re.compile(r"(?P<prefix>!?\[[^\]\n]*\]\()(?P<target>[^)\n]+)(?P<suffix>\))")
_REFERENCE_DEFINITION_RE = re.compile(
    r"^(?P<prefix>[ \t]{0,3}\[(?!\^)[^\]\n]+\]:[ \t]*)"
    r"(?P<target><[^>\n]*>|[^\s\n]+)"
    r"(?P<suffix>[^\r\n]*)$",
    re.MULTILINE,
)
_MULTILINE_REFERENCE_DEFINITION_RE = re.compile(
    r"^(?P<prefix>[ \t]{0,3}\[(?!\^)[^\]\n]+\]:[ \t]*\r?\n[ \t]*)"
    r"(?P<target><[^>\n]*>|[^\s\n]+)"
    r"(?P<suffix>[^\r\n]*)$",
    re.MULTILINE,
)
_REFERENCE_LABEL_ONLY_RE = re.compile(r"^[ \t]{0,3}\[(?!\^)[^\]\n]+\]:[ \t]*$")
_FENCE_START_RE = re.compile(r"^[ \t]{0,3}(?P<marker>`{3,}|~{3,})")
_REMOTE_PREFIXES = ("http://", "https://", "mailto:")
_URL_PATH_SAFE = "/:@-._~!$&'()*+,;="


class ReferenceResolutionError(ValueError):
    """A reference is malformed, ambiguous, or does not identify an artifact."""


@dataclass(frozen=True)
class _MarkdownLinkToken:
    start: int
    end: int
    prefix: str
    target: str
    suffix: str


@dataclass(frozen=True)
class ArtifactLocation:
    artifact_id: str
    path: Path
    pointer: str
    kind: str

    def as_dict(self, root: Path) -> dict[str, str]:
        return {
            "id": self.artifact_id,
            "path": _relative_path(self.path, root),
            "pointer": self.pointer,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class ResolvedReference:
    reference: str
    path: Path
    fragment: str
    kind: str
    pointer: str = ""
    artifact_id: str = ""

    def as_details(self, root: Path) -> dict[str, str]:
        value = {
            "path": _relative_path(self.path, root),
            "fragment": self.fragment,
            "kind": self.kind,
        }
        if self.pointer:
            value["pointer"] = self.pointer
        if self.artifact_id:
            value["artifactId"] = self.artifact_id
        return value


def _relative_path(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise ReferenceResolutionError(f"reference path escapes project root: {path}") from exc


def slugify(value: str) -> str:
    """Generate the deterministic fragment slug used by Markdown references."""
    normalized = unicodedata.normalize("NFKC", re.sub(r"[`*_]", "", str(value))).casefold()
    chars: list[str] = []
    pending_dash = False
    for char in normalized:
        if char.isalnum():
            if pending_dash and chars:
                chars.append("-")
            chars.append(char)
            pending_dash = False
        else:
            pending_dash = True
    return "".join(chars).strip("-") or "section"


def _split_reference(reference: str) -> tuple[str, str]:
    raw = str(reference or "").strip()
    if "#" not in raw:
        return raw, ""
    path, fragment = raw.split("#", 1)
    return path.strip(), unquote(fragment.strip())


def resolve_path(raw_path: str, project_root: Path, source_path: Path | None = None) -> Path | None:
    """Resolve a project-local path using one and only one relative-path base."""
    if not raw_path:
        return None
    candidate = Path(raw_path)
    if candidate.is_absolute() or raw_path.startswith("file://"):
        return None
    root = Path(project_root).resolve()
    base = Path(source_path).resolve().parent if source_path is not None else root
    resolved = (base / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved if resolved.is_file() else None


def _markdown_destination(raw: str) -> tuple[str, str, bool]:
    """Return destination token, optional title suffix, and angle-bracket form."""
    value = raw.strip()
    if value.startswith("<"):
        end = value.find(">")
        if end < 0:
            return value, "", False
        return value[1:end], value[end + 1 :], True
    parts = value.split(maxsplit=1)
    return parts[0] if parts else "", (" " + parts[1] if len(parts) > 1 else ""), False


def _mask_range(chars: list[str], start: int, end: int) -> None:
    for index in range(start, end):
        if chars[index] not in "\r\n":
            chars[index] = " "


def _leading_indent_columns(line: str) -> int:
    columns = 0
    for char in line:
        if char == " ":
            columns += 1
        elif char == "\t":
            columns += 4 - (columns % 4)
        else:
            break
    return columns


def _is_fence_closer(line: str, marker: str) -> bool:
    match = re.fullmatch(r"[ \t]{0,3}(?P<marker>`{3,}|~{3,})[ \t]*", line)
    return bool(
        match
        and match.group("marker")[0] == marker[0]
        and len(match.group("marker")) >= len(marker)
    )


def _mask_block_code(text: str, chars: list[str]) -> None:
    offset = 0
    fence_marker = ""
    indented_code = False
    previous_blank = True
    reference_target_follows = False
    for line in text.splitlines(keepends=True):
        visible = line.rstrip("\r\n")
        blank = not visible.strip()
        if fence_marker:
            _mask_range(chars, offset, offset + len(line))
            if _is_fence_closer(visible, fence_marker):
                fence_marker = ""
            previous_blank = blank
            offset += len(line)
            continue

        is_reference_target = reference_target_follows and not blank
        reference_target_follows = False
        fence = _FENCE_START_RE.match(visible)
        if fence and not is_reference_target:
            marker = fence.group("marker")
            remainder = visible[fence.end():]
            if marker[0] == "~" or "`" not in remainder:
                fence_marker = marker
                indented_code = False
                _mask_range(chars, offset, offset + len(line))
                previous_blank = blank
                offset += len(line)
                continue

        indent = _leading_indent_columns(visible)
        if blank and indented_code:
            _mask_range(chars, offset, offset + len(line))
        elif indent >= 4 and (indented_code or previous_blank) and not is_reference_target:
            indented_code = True
            _mask_range(chars, offset, offset + len(line))
        elif not blank:
            indented_code = False

        if _REFERENCE_LABEL_ONLY_RE.fullmatch(visible):
            reference_target_follows = True
        previous_blank = blank
        offset += len(line)


def _mask_inline_code_and_comments(text: str, chars: list[str]) -> None:
    """Mask inline code and comments in source order so their markers cannot cross-pair."""
    masked = "".join(chars)
    index = 0
    while index < len(masked):
        if masked.startswith("<!--", index):
            closing = masked.find("-->", index + 4)
            end = len(masked) if closing < 0 else closing + 3
            _mask_range(chars, index, end)
            masked = "".join(chars)
            index = end
            continue
        if masked[index] != "`":
            index += 1
            continue
        end_run = index + 1
        while end_run < len(masked) and masked[end_run] == "`":
            end_run += 1
        run_length = end_run - index
        closer = re.compile(rf"(?<!`)`{{{run_length}}}(?!`)").search(masked, end_run)
        if closer is None:
            index = end_run
            continue
        _mask_range(chars, index, closer.end())
        masked = "".join(chars)
        index = closer.end()


def _masked_markdown(text: str) -> str:
    """Mask code and comments without changing offsets or line boundaries."""
    chars = list(text)
    _mask_block_code(text, chars)
    _mask_inline_code_and_comments(text, chars)
    return "".join(chars)


def _markdown_link_tokens(text: str) -> list[_MarkdownLinkToken]:
    masked = _masked_markdown(text)
    matches = [
        *_MARKDOWN_LINK_RE.finditer(masked),
        *_REFERENCE_DEFINITION_RE.finditer(masked),
        *_MULTILINE_REFERENCE_DEFINITION_RE.finditer(masked),
    ]
    matches.sort(key=lambda item: item.start())
    tokens: list[_MarkdownLinkToken] = []
    occupied_until = -1
    for match in matches:
        if match.start() < occupied_until:
            continue
        target_start, target_end = match.span("target")
        tokens.append(
            _MarkdownLinkToken(
                start=match.start(),
                end=match.end(),
                prefix=text[match.start():target_start],
                target=text[target_start:target_end],
                suffix=text[target_end:match.end()],
            )
        )
        occupied_until = match.end()
    return tokens


def markdown_link_destinations(text: str) -> tuple[str, ...]:
    """Return effective inline and reference-definition destinations."""
    return tuple(token.target for token in _markdown_link_tokens(text))


def markdown_link_path(raw: str) -> str:
    """Return the decoded path portion of one Markdown link destination."""
    destination, _, _ = _markdown_destination(raw)
    return _split_reference(unquote(destination.strip()))[0]


def _pending_content(path: Path, pending: Mapping[Path, bytes]) -> str | None:
    value = pending.get(path.resolve())
    if value is not None:
        return value.decode("utf-8")
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return None


def _pending_exists(path: Path, pending: Mapping[Path, bytes]) -> bool:
    resolved = path.resolve()
    return resolved in pending or path.exists() or any(resolved == parent for item in pending for parent in item.parents)


def validate_markdown_links(
    source_path: Path,
    text: str,
    project_root: Path,
    *,
    pending: Mapping[Path, bytes] | None = None,
) -> list[str]:
    """Validate every local Markdown link against its declaring file and fragment."""
    root = Path(project_root).resolve()
    source = Path(source_path).resolve()
    pending_map = {Path(path).resolve(): bytes(content) for path, content in (pending or {}).items()}
    errors: list[str] = []
    for token in _markdown_link_tokens(text):
        raw = token.target
        destination, _, _ = _markdown_destination(raw)
        destination = unquote(destination.strip())
        lowered = destination.casefold()
        if not destination or lowered.startswith(_REMOTE_PREFIXES):
            continue
        if lowered.startswith("file://"):
            errors.append(f"file:// Markdown link is forbidden: {source_path} -> {raw}")
            continue
        path_part, fragment = _split_reference(destination)
        if path_part and Path(path_part).is_absolute():
            errors.append(f"absolute Markdown link is forbidden: {source_path} -> {raw}")
            continue
        candidate = source if not path_part and fragment else (source.parent / path_part).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            errors.append(f"Markdown link escapes DOC_ROOT: {source_path} -> {raw}")
            continue
        if not _pending_exists(candidate, pending_map):
            errors.append(f"Markdown link target does not exist: {source_path} -> {raw}")
            continue
        if not fragment:
            continue
        is_pending_file = candidate.resolve() in pending_map
        if candidate.suffix.lower() not in {".md", ".markdown"} or not (is_pending_file or candidate.is_file()):
            errors.append(f"Markdown fragment target is not a Markdown file: {source_path} -> {raw}")
            continue
        try:
            target_text = _pending_content(candidate, pending_map)
            if target_text is None:
                errors.append(f"Markdown fragment target is not a Markdown file: {source_path} -> {raw}")
                continue
            targets = _markdown_targets_text(target_text)
        except (OSError, UnicodeDecodeError, ReferenceResolutionError) as exc:
            errors.append(f"invalid Markdown target document: {candidate}: {exc}")
            continue
        if fragment not in targets and fragment.casefold() not in targets:
            errors.append(f"Markdown heading/anchor does not exist: {source_path} -> {raw}")
    return errors


def rebase_markdown_links(text: str, source_path: Path, target_path: Path, project_root: Path) -> str:
    """Preserve local link targets when a Markdown document is copied to another directory."""
    root = Path(project_root).resolve()
    source = Path(source_path).resolve()
    target = Path(target_path).resolve()

    def replacement(link_token: _MarkdownLinkToken) -> str:
        raw = link_token.target
        destination, title_suffix, angled = _markdown_destination(raw)
        decoded = unquote(destination.strip())
        lowered = decoded.casefold()
        if not decoded or lowered.startswith(_REMOTE_PREFIXES) or lowered.startswith("file://"):
            return text[link_token.start:link_token.end]
        path_part, fragment = _split_reference(decoded)
        if not path_part or Path(path_part).is_absolute():
            return text[link_token.start:link_token.end]
        resolved = (source.parent / path_part).resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            return text[link_token.start:link_token.end]
        relative = Path(os.path.relpath(resolved, start=target.parent)).as_posix()
        rebased = quote(relative, safe=_URL_PATH_SAFE)
        if fragment:
            rebased += f"#{quote(fragment, safe=_URL_PATH_SAFE)}"
        destination_token = f"<{rebased}>" if angled else rebased
        return f"{link_token.prefix}{destination_token}{title_suffix}{link_token.suffix}"

    updated = text
    for token in reversed(_markdown_link_tokens(text)):
        updated = updated[:token.start] + replacement(token) + updated[token.end:]
    return updated


def _read_json_like(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReferenceResolutionError(f"cannot read reference: {path}") from exc
    try:
        if path.suffix.lower() == ".json":
            return json.loads(text)
        return simple_yaml.loads(text)
    except (json.JSONDecodeError, simple_yaml.YamlError) as exc:
        raise ReferenceResolutionError(f"invalid structured reference {path}: {exc}") from exc


def _pointer(path: list[str]) -> str:
    return "" if not path else "/" + "/".join(str(item).replace("~", "~0").replace("/", "~1") for item in path)


def _walk(value: Any, path: list[str] | None = None) -> Iterable[tuple[list[str], Any]]:
    current = [] if path is None else path
    yield current, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk(item, current + [str(key)])
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, current + [str(index)])


def _stable_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate if _REFERENCE_ID_RE.fullmatch(candidate) else None


def _structured_id_locations(value: Any, path: Path, kind: str) -> list[ArtifactLocation]:
    locations: list[ArtifactLocation] = []
    for pointer_path, item in _walk(value):
        if not isinstance(item, dict):
            continue
        # Only identity fields are canonical.  A value in prose, description,
        # source, or an arbitrary string is never promoted into an artifact ID.
        for key in ("id", "stableId", "stable_id"):
            artifact_id = _stable_id(item.get(key))
            if artifact_id:
                locations.append(ArtifactLocation(artifact_id, path, _pointer(pointer_path), kind))
                break
    return locations


def _iter_reference_files(project_root: Path, version: str = "") -> list[Path]:
    root = Path(project_root).resolve()
    roots = [root, root / "project_doc"]
    if version:
        roots.extend([root / "version" / version, root / "project_doc" / "version" / version])
    files: set[Path] = set()
    for base in roots:
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if path.is_file() and not (set(path.parts) & _IGNORED_PARTS) and not any(part.startswith("stage_") for part in path.parts) and path.suffix.lower() in _PATH_SUFFIXES:
                files.add(path.resolve())
    return sorted(files, key=lambda item: item.as_posix())


class CanonicalArtifactIndex:
    """Deterministic index of exact stable IDs in structured artifacts."""

    def __init__(self, project_root: Path, *, version: str = "", files: Iterable[Path] | None = None):
        self.root = Path(project_root).resolve()
        self.version = str(version or "")
        self.files = sorted({Path(path).resolve() for path in (files or _iter_reference_files(self.root, self.version))})
        self.locations: dict[str, list[ArtifactLocation]] = {}
        self.errors: list[str] = []
        self._build()

    def _add(self, location: ArtifactLocation) -> None:
        self.locations.setdefault(location.artifact_id, []).append(location)

    def _build(self) -> None:
        for path in self.files:
            suffix = path.suffix.lower()
            try:
                if suffix in {".json", ".yaml", ".yml"}:
                    for location in _structured_id_locations(_read_json_like(path), path, "structured"):
                        self._add(location)
                elif suffix == ".jsonl":
                    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                        if not line.strip():
                            continue
                        try:
                            value = json.loads(line)
                        except json.JSONDecodeError as exc:
                            self.errors.append(f"{path}:{line_number}: invalid JSONL record: {exc}")
                            continue
                        for location in _structured_id_locations(value, path, "jsonl"):
                            self._add(ArtifactLocation(location.artifact_id, path, f"/{line_number - 1}{location.pointer}", "jsonl"))
            except (OSError, UnicodeDecodeError, ReferenceResolutionError) as exc:
                # Unrelated malformed files must not make a stable ID appear
                # valid; explicit references to such a file still fail closed.
                self.errors.append(str(exc))
        for locations in self.locations.values():
            locations.sort(key=lambda item: (item.path.as_posix(), item.pointer, item.kind))

    def resolve(self, artifact_id: str) -> ArtifactLocation:
        candidate = str(artifact_id or "").strip()
        if not _REFERENCE_ID_RE.fullmatch(candidate):
            raise ReferenceResolutionError(f"stable ID has invalid canonical format: {candidate}")
        locations = self.locations.get(candidate, [])
        if not locations:
            raise ReferenceResolutionError(f"stable ID was not found: {candidate}")
        if len(locations) != 1:
            rendered = ", ".join(f"{_relative_path(item.path, self.root)}{item.pointer}" for item in locations)
            raise ReferenceResolutionError(f"stable ID is ambiguous: {candidate}: {rendered}")
        return locations[0]

    def summary(self) -> dict[str, Any]:
        return {
            "schemaVersion": 1,
            "root": str(self.root),
            "version": self.version,
            "artifactCount": len(self.locations),
            "duplicateIds": sorted(key for key, value in self.locations.items() if len(value) > 1),
            "errors": list(self.errors),
            "artifacts": {
                key: [location.as_dict(self.root) for location in value]
                for key, value in sorted(self.locations.items())
            },
        }


def _markdown_targets_text(text: str) -> dict[str, tuple[str, str]]:
    lines = text.splitlines()
    targets: dict[str, tuple[str, str]] = {}
    used: dict[str, int] = {}
    in_fence = False
    first_title = ""
    for line_number, line in enumerate(lines, 1):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        anchor_match = _ANCHOR_RE.search(line)
        brace_match = _BRACE_ANCHOR_RE.search(line)
        if anchor_match or brace_match:
            anchor = (next(value for value in anchor_match.groups() if value) if anchor_match else brace_match.group(1))
            normalized = anchor.strip()
            if normalized:
                if normalized in targets:
                    raise ReferenceResolutionError(f"duplicate Markdown anchor: {normalized}")
                targets[normalized] = ("anchor", f"/{line_number}")
        heading_match = _HEADING_RE.match(line)
        if not heading_match:
            continue
        title = heading_match.group(2).strip().rstrip("#").strip()
        if not first_title:
            first_title = title
        base = slugify(title)
        count = used.get(base, 0) + 1
        used[base] = count
        anchor = base if count == 1 else f"{base}-{count}"
        targets[anchor] = ("heading", f"/{line_number}")
    # A document identity is canonical, unlike arbitrary substring matching.
    # This preserves useful `policy.md#policy` references for documents whose
    # content has no heading while keeping plain prose non-addressable.
    if first_title:
        targets.setdefault(slugify(first_title), ("document", "/1"))
    return targets


def _markdown_targets(path: Path) -> dict[str, tuple[str, str]]:
    targets = _markdown_targets_text(path.read_text(encoding="utf-8"))
    targets.setdefault(slugify(path.stem), ("document", ""))
    return targets


def resolve_reference(
    reference: str,
    project_root: Path,
    source_path: Path | None = None,
    *,
    version: str = "",
    label: str = "reference",
) -> ResolvedReference:
    """Resolve one reference or raise a fail-closed diagnostic."""
    raw = str(reference or "").strip()
    if not raw:
        raise ReferenceResolutionError(f"{label} is required")
    path_part, fragment = _split_reference(raw)
    candidate = resolve_path(path_part, project_root, source_path) if path_part else None
    root = Path(project_root).resolve()
    if not path_part and fragment and source_path is not None:
        local = Path(source_path).resolve()
        try:
            local.relative_to(root)
        except ValueError:
            local = Path()
        if local.is_file():
            candidate = local
    if path_part and candidate is None:
        if Path(path_part).is_absolute() or ".." in Path(path_part).parts:
            raise ReferenceResolutionError(f"{label} must be a project-local relative reference: {raw}")
        if not _REFERENCE_ID_RE.fullmatch(path_part):
            raise ReferenceResolutionError(f"{label} does not exist: {raw}")
        if fragment:
            raise ReferenceResolutionError(f"{label} stable ID references cannot contain a second fragment: {raw}")
        try:
            location = CanonicalArtifactIndex(root, version=version).resolve(path_part)
        except ReferenceResolutionError as exc:
            raise ReferenceResolutionError(f"{label} {exc}") from exc
        return ResolvedReference(raw, location.path, fragment or path_part, "stable-id", location.pointer, path_part)
    if not path_part and candidate is None:
        raise ReferenceResolutionError(f"{label} must identify a path or canonical stable ID: {raw}")
    suffix = candidate.suffix.lower()
    if suffix in {".md", ".markdown"}:
        try:
            targets = _markdown_targets(candidate)
        except (OSError, UnicodeDecodeError) as exc:
            raise ReferenceResolutionError(f"{label} cannot be read: {raw}") from exc
        if not fragment:
            return ResolvedReference(raw, candidate, "", "document")
        target = targets.get(fragment) or targets.get(fragment.casefold())
        if target is None:
            raise ReferenceResolutionError(f"{label} Markdown heading/anchor does not exist: {raw}")
        kind, pointer = target
        return ResolvedReference(raw, candidate, fragment, kind, pointer)
    if suffix in {".json", ".yaml", ".yml"}:
        value = _read_json_like(candidate)
        if not fragment:
            return ResolvedReference(raw, candidate, "", "document")
        if fragment.startswith("/"):
            current = value
            for token in fragment[1:].split("/") if fragment != "/" else []:
                token = token.replace("~1", "/").replace("~0", "~")
                if isinstance(current, dict) and token in current:
                    current = current[token]
                elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
                    current = current[int(token)]
                else:
                    raise ReferenceResolutionError(f"{label} JSON Pointer does not exist: {raw}")
            return ResolvedReference(raw, candidate, fragment, "json-pointer", fragment)
        local = [item for item in _structured_id_locations(value, candidate, "structured") if item.artifact_id == fragment]
        if len(local) != 1:
            reason = "was not found" if not local else "is ambiguous"
            raise ReferenceResolutionError(f"{label} stable ID {reason}: {raw}")
        return ResolvedReference(raw, candidate, fragment, "stable-id", local[0].pointer, fragment)
    if suffix == ".jsonl":
        if not fragment:
            return ResolvedReference(raw, candidate, "", "document")
        index = CanonicalArtifactIndex(root, version=version, files=[candidate])
        location = index.resolve(fragment)
        return ResolvedReference(raw, candidate, fragment, "stable-id", location.pointer, fragment)
    # Text files have no canonical fragment grammar.  A path without a
    # fragment is addressable; a fragment must never be accepted by substring.
    if fragment:
        raise ReferenceResolutionError(f"{label} does not support fragments for {candidate.suffix}: {raw}")
    return ResolvedReference(raw, candidate, "", "document")
