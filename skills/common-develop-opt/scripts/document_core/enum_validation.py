"""Validate enum definitions and cross-document enum references."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from document_core import simple_yaml
from document_core.enum_contract import EnumContractError, enum_anchor, parse_enums

ENUM_LINK_RE = re.compile(r"\[`?([A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*)`?\]\(([^)#]+)#(enum-[^)]+)\)")
ENUM_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*$")
ENUM_USE_RE = re.compile(r"\b([A-Z][A-Z0-9_]*\.[A-Z][A-Z0-9_]*)(?:\.[A-Z][A-Z0-9_]*)?\b")


def _without_fenced_code(text: str) -> list[tuple[int, str]]:
    """Return Markdown lines outside fenced code blocks."""
    lines: list[tuple[int, str]] = []
    fenced = False
    for line_no, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if not fenced:
            lines.append((line_no, line))
    return lines


def _definition_map(module_root: Path) -> dict[str, tuple[Path, str]]:
    result: dict[str, tuple[Path, str]] = {}
    if not module_root.is_dir():
        return result
    for module_dir in sorted(path for path in module_root.iterdir() if path.is_dir() and not path.name.startswith("_")):
        module = module_dir.name
        desc = module_dir / f"{module}_desc.md"
        if not desc.is_file():
            continue
        for item in parse_enums(desc.read_text(encoding="utf-8"), module):
            if item.code in result:
                raise EnumContractError(f"duplicate enum code across modules: {item.code}")
            result[item.code] = (desc, enum_anchor(item.code))
    return result


def _expected_definition(
    code: str,
    path: Path,
    project_defs: dict[str, tuple[Path, str]],
    version_defs: dict[str, tuple[Path, str]],
    version_root: Path | None,
) -> tuple[Path, str] | None:
    # Version documents see the in-version delta first; project documents always
    # resolve against the archived project baseline.
    if version_root is not None:
        try:
            path.resolve().relative_to(version_root.resolve())
            return version_defs.get(code) or project_defs.get(code)
        except ValueError:
            pass
    return project_defs.get(code)


def validate_markdown_refs(
    paths: list[Path],
    project_defs: dict[str, tuple[Path, str]],
    version_defs: dict[str, tuple[Path, str]],
    version_root: Path | None,
) -> list[str]:
    errors: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for code, target, anchor in ENUM_LINK_RE.findall(text):
            expected = _expected_definition(code, path, project_defs, version_defs, version_root)
            if expected is None:
                errors.append(f"{path}: enum reference is undefined: {code}")
                continue
            resolved = (path.parent / target).resolve()
            if resolved != expected[0].resolve():
                errors.append(f"{path}: enum {code} links to {target}, expected {expected[0]}")
            if anchor != expected[1]:
                errors.append(f"{path}: enum {code} anchor mismatch: {anchor} != {expected[1]}")

        # The module description is the definition document; its tables contain
        # the enum code and symbols by design. Every other Markdown document that
        # uses an enum code must put the canonical clickable reference on the same
        # line. This catches bare API/test/page/design prose that would otherwise
        # look like a reference while being impossible to navigate.
        if path.name.endswith("_desc.md"):
            continue
        for line_no, line in _without_fenced_code(text):
            links_on_line = {match.group(1) for match in ENUM_LINK_RE.finditer(line)}
            for code in ENUM_USE_RE.findall(line):
                if code not in project_defs and code not in version_defs:
                    continue
                if code not in links_on_line:
                    errors.append(
                        f"{path}:{line_no}: enum {code} is used without a canonical enum link"
                    )
    return errors


def _walk_enum_refs(value: Any, trail: str = ""):
    if isinstance(value, dict):
        if "enumRef" in value or "enumLink" in value:
            yield trail or "$", value
        for key, child in value.items():
            yield from _walk_enum_refs(child, f"{trail}.{key}" if trail else str(key))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_enum_refs(child, f"{trail}[{index}]")


def validate_structured_refs(
    paths: list[Path],
    project_defs: dict[str, tuple[Path, str]],
    version_defs: dict[str, tuple[Path, str]],
    version_root: Path | None,
) -> list[str]:
    errors: list[str] = []
    for path in paths:
        try:
            document = simple_yaml.load(path)
        except (OSError, simple_yaml.YamlError) as exc:
            errors.append(f"{path}: cannot parse structured document: {exc}")
            continue
        for trail, obj in _walk_enum_refs(document):
            code = str(obj.get("enumRef") or "").strip()
            link = str(obj.get("enumLink") or "").strip()
            if not code or not link:
                errors.append(f"{path}:{trail}: enumRef and enumLink must be declared together")
                continue
            if not ENUM_CODE_RE.fullmatch(code):
                errors.append(f"{path}:{trail}: invalid enumRef: {code}")
                continue
            expected = _expected_definition(code, path, project_defs, version_defs, version_root)
            if expected is None:
                errors.append(f"{path}:{trail}: enum reference is undefined: {code}")
                continue
            if "#" not in link:
                errors.append(f"{path}:{trail}: enumLink must include an anchor: {link}")
                continue
            target, anchor = link.rsplit("#", 1)
            resolved = (path.parent / target).resolve()
            if resolved != expected[0].resolve():
                errors.append(f"{path}:{trail}: enum {code} links to {target}, expected {expected[0]}")
            if anchor != expected[1]:
                errors.append(f"{path}:{trail}: enum {code} anchor mismatch: {anchor} != {expected[1]}")
    return errors



def validate_version(doc_root: Path, version: str) -> dict[str, object]:
    from runtime import doc_layout
    root = doc_layout.validate_doc_root(doc_root, version)
    project_defs = _definition_map(doc_layout.project_module_root(root))
    version_root = doc_layout.version_module_root(root, version)
    version_defs = _definition_map(version_root)
    markdown_paths: list[Path] = []
    structured_paths: list[Path] = []
    for base in (doc_layout.project_module_root(root), doc_layout.version_requirement_root(root, version), version_root):
        if base.is_dir():
            markdown_paths.extend(base.rglob("*.md"))
    if version_root.is_dir():
        structured_paths.extend(version_root.glob("*/changes/*.yaml"))
        structured_paths.extend(version_root.glob("*/changes/*.yml"))
    errors = validate_markdown_refs(markdown_paths, project_defs, version_defs, version_root)
    errors.extend(validate_structured_refs(structured_paths, project_defs, version_defs, version_root))
    return {
        "status": "PASSED" if not errors else "FAILED",
        "projectDefinitionCount": len(project_defs),
        "versionDefinitionCount": len(version_defs),
        "errors": errors,
    }
