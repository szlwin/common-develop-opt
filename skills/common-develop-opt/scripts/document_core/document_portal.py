#!/usr/bin/env python3
"""Generate and validate human-readable project and version document portals."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping

_SCRIPT_ROOT = Path(__file__).resolve().parents[1]
_COMPAT_ROOT = _SCRIPT_ROOT / "compat"
if str(_COMPAT_ROOT) not in sys.path:
    sys.path.insert(0, str(_COMPAT_ROOT))

from document_core import agent_context
from document_core import common_utils
from document_core import page_documents
from document_core import reference_resolution
from runtime import doc_layout
from resource_paths import asset_path


ROOT = Path(__file__).resolve().parents[2]
PROJECT_TEMPLATE = asset_path('assets/documents/structured-docs/project-index.template.md')
VERSION_TEMPLATE = asset_path('assets/documents/structured-docs/version-summary.template.md')
GENERATED_MARKER = "<!-- generated-by: common-develop/document_portal.py -->"
LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")

COMMAND_HELP_METADATA = {
    "render": {
        "mutation": True,
        "previewSupported": False,
        "effect": "GENERATED_DOCUMENT_PORTAL_WRITE",
    },
    "validate": {
        "mutation": False,
        "previewSupported": False,
        "effect": "READ_ONLY",
    },
}


class DocumentPortalError(ValueError):
    """Raised when a generated portal or one of its links is invalid."""


def _resolved_pending(pending: Mapping[Path, bytes] | None) -> dict[Path, bytes]:
    return {Path(path).resolve(): bytes(content) for path, content in (pending or {}).items()}


def _exists(path: Path, pending: Mapping[Path, bytes]) -> bool:
    resolved = path.resolve()
    return resolved in pending or path.exists() or any(parent == resolved for item in pending for parent in item.parents)


def _is_file(path: Path, pending: Mapping[Path, bytes]) -> bool:
    return path.resolve() in pending or path.is_file()


def _iter_files(directory: Path, pending: Mapping[Path, bytes], pattern: str) -> list[Path]:
    values = set(directory.glob(pattern)) if directory.is_dir() else set()
    for path in pending:
        try:
            relative = path.relative_to(directory.resolve())
        except ValueError:
            continue
        if relative.match(pattern):
            values.add(path)
    return sorted((Path(path) for path in values if _is_file(Path(path), pending)), key=lambda item: item.as_posix())


def _relative_link(source: Path, target: Path, label: str) -> str:
    relative = os.path.relpath(target, start=source.parent).replace(os.sep, "/")
    return f"[{label}]({relative})"


def _bullets(values: Iterable[str]) -> str:
    items = [value for value in values if value]
    return "\n".join(f"- {value}" for value in items) if items else "- 暂无"


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "暂无。"
    rendered = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    rendered.extend("| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in rows)
    return "\n".join(rendered)


def _version_names(doc_root: Path, pending: Mapping[Path, bytes]) -> list[str]:
    root = doc_root / "version"
    names = {item.name for item in root.iterdir() if item.is_dir()} if root.is_dir() else set()
    for path in pending:
        try:
            relative = path.relative_to(root.resolve())
        except ValueError:
            continue
        if relative.parts:
            names.add(relative.parts[0])
    return sorted(names)


def _module_names(doc_root: Path, pending: Mapping[Path, bytes]) -> list[str]:
    docs = doc_root / "docs" / "module"
    names: set[str] = set()
    if docs.is_dir():
        names.update(path.name for path in docs.iterdir() if path.is_dir() and not path.name.startswith("_"))
    for path in pending:
        try:
            relative = path.relative_to(docs.resolve())
        except ValueError:
            continue
        if len(relative.parts) > 1 and not relative.parts[0].startswith("_"):
            names.add(relative.parts[0])
    return sorted(names)


def _version_module_names(doc_root: Path, version: str, pending: Mapping[Path, bytes]) -> list[str]:
    root = doc_root / "version" / version / "module"
    names: set[str] = set()
    if root.is_dir():
        names.update(path.name for path in root.iterdir() if path.is_dir() and not path.name.startswith("_"))
    for path in pending:
        try:
            relative = path.relative_to(root.resolve())
        except ValueError:
            continue
        if len(relative.parts) > 1 and not relative.parts[0].startswith("_"):
            names.add(relative.parts[0])
    return sorted(names)


def _render(template: Path, replacements: Mapping[str, str]) -> str:
    if not template.is_file():
        raise DocumentPortalError(f"missing portal template: {template}")
    text = template.read_text(encoding="utf-8")
    for marker, value in replacements.items():
        text = text.replace(marker, value)
    unresolved = sorted(set(re.findall(r"\{[A-Z][A-Z0-9_]+\}", text)))
    if unresolved:
        raise DocumentPortalError(f"unresolved portal template markers: {unresolved}")
    return text.rstrip() + "\n"


def render_project_index(doc_root: Path, pending: Mapping[Path, bytes] | None = None) -> str:
    pending_map = _resolved_pending(pending)
    output = doc_root / "docs" / "README.md"
    quick: list[str] = []
    for target, label in (
        (doc_root / "docs" / "requirement_list.md", "当前需求与功能"),
        (doc_root / "docs" / "union" / "union_language.md", "统一语言"),
        (doc_root / "version" / "current_version.md", "当前版本声明"),
        (doc_root / "archive_manifest.yaml", "归档记录"),
    ):
        if _exists(target, pending_map):
            quick.append(_relative_link(output, target, label))
    archive_root = doc_root / "archives"
    if archive_root.is_dir():
        for archive_page in sorted(archive_root.glob("*/*/*/README.md")):
            quick.append(_relative_link(output, archive_page, "知识归档"))
    for candidate in pending_map:
        try:
            rel = candidate.relative_to(archive_root.resolve())
        except ValueError:
            continue
        if len(rel.parts) == 4 and rel.name == "README.md":
            quick.append(_relative_link(output, candidate, "知识归档"))

    module_rows: list[list[str]] = []
    for module in _module_names(doc_root, pending_map):
        module_dir = doc_root / "docs" / "module" / module
        links: list[str] = []
        for target, label in (
            (module_dir / f"{module}_desc.md", "模块说明"),
            (module_dir / f"{module}_design.md", "当前设计"),
            (module_dir / f"{module}_business_model.md", "业务模型"),
        ):
            if _exists(target, pending_map):
                links.append(_relative_link(output, target, label))
        module_rows.append([module, " / ".join(links) or "暂无可读文档"])

    flow_relation: list[str] = []
    for target, label in (
        (doc_root / "docs" / "_flows" / "flow_index.md", "业务流程索引"),
        (doc_root / "docs" / "_relations" / "dependency_impact.md", "跨模块影响事实"),
        (doc_root / "docs" / "_relations" / "dependency_graph.md", "跨模块关联图"),
        (doc_root / "docs" / "_relations" / "traceability_matrix.md", "追踪矩阵"),
    ):
        if _exists(target, pending_map):
            flow_relation.append(_relative_link(output, target, label))

    version_rows: list[list[str]] = []
    for version in _version_names(doc_root, pending_map):
        summary = doc_root / "version" / version / "version_summary.md"
        requirements = doc_root / "version" / version / "requirement" / "requirement_list.md"
        links = []
        if _exists(summary, pending_map):
            links.append(_relative_link(output, summary, "版本摘要"))
        if _exists(requirements, pending_map):
            links.append(_relative_link(output, requirements, "需求列表"))
        version_rows.append([version, " / ".join(links) or "暂无可读文档"])

    return _render(PROJECT_TEMPLATE, {
        "{QUICK_LINKS}": _bullets(quick),
        "{MODULE_LINKS}": _table(["模块", "文档"], module_rows),
        "{FLOW_RELATION_LINKS}": _bullets(flow_relation),
        "{VERSION_LINKS}": _table(["版本", "文档"], version_rows),
    })


def render_version_summary(
    doc_root: Path,
    version: str,
    pending: Mapping[Path, bytes] | None = None,
) -> str:
    pending_map = _resolved_pending(pending)
    output = doc_root / "version" / version / "version_summary.md"
    version_root = output.parent
    quick: list[str] = []
    for target, label in (
        (doc_root / "docs" / "README.md", "项目文档首页"),
        (version_root / "requirement" / "requirement_list.md", "本版本需求列表"),
        (version_root / "union" / "union_language_changes.md", "统一语言变更"),
        (version_root / "project_process.md", "研发进度"),
        (version_root / "work.md", "工作清单"),
        (version_root / "work_record.md", "工作记录"),
    ):
        if _exists(target, pending_map):
            quick.append(_relative_link(output, target, label))

    requirement_rows: list[list[str]] = []
    for path in _iter_files(version_root / "requirement", pending_map, "*/requirement.md"):
        test_case = path.parent / "test_case.md"
        links = [_relative_link(output, path, "需求正文")]
        if _exists(test_case, pending_map):
            links.append(_relative_link(output, test_case, "测试设计"))
        requirement_rows.append([path.parent.name, " / ".join(links)])

    module_rows: list[list[str]] = []
    for module in _version_module_names(doc_root, version, pending_map):
        module_dir = version_root / "module" / module
        links: list[str] = []
        for target, label in (
            (module_dir / f"{module}_desc.md", "模块说明"),
            (module_dir / f"{module}_design.md", "设计"),
            (module_dir / f"{module}_business_model.yaml", "业务模型事实"),
            (module_dir / f"{module}_web_code.md", "前端实现规格"),
            (module_dir / f"{module}_back_code.md", "后端实现规格"),
        ):
            if _exists(target, pending_map):
                links.append(_relative_link(output, target, label))
        pages = page_documents.discover_module_page_documents(module_dir, module, pending_map)
        for page in pages:
            label = "旧版页面行为" if page.legacy else f"页面：{page.page_name}"
            links.append(_relative_link(output, page.path, label))
        module_rows.append([module, " / ".join(links) or "暂无可读文档"])

    delta_links: list[str] = []
    for path in _iter_files(version_root / "module", pending_map, "*/changes/*.yaml"):
        delta_links.append(_relative_link(output, path, path.relative_to(version_root).as_posix()))
    for path in _iter_files(version_root / "module" / "_flows", pending_map, "*/changes/*.yaml"):
        delta_links.append(_relative_link(output, path, path.relative_to(version_root).as_posix()))

    task_rows: list[list[str]] = []
    task_root = version_root / "task"
    task_names = {item.name for item in task_root.iterdir() if item.is_dir()} if task_root.is_dir() else set()
    for path in pending_map:
        try:
            relative = path.relative_to(task_root.resolve())
        except ValueError:
            continue
        if relative.parts:
            task_names.add(relative.parts[0])
    for task_id in sorted(task_names):
        task_dir = task_root / task_id
        links = []
        for target, label in ((task_dir / "development_task_plan.json", "大需求实施计划"),):
            if _exists(target, pending_map):
                links.append(_relative_link(output, target, label))
        if links:
            task_rows.append([task_id, " / ".join(links)])

    return _render(VERSION_TEMPLATE, {
        "{VERSION}": version,
        "{QUICK_LINKS}": _bullets(quick),
        "{REQUIREMENT_LINKS}": _table(["目标", "需求文档"], requirement_rows),
        "{MODULE_LINKS}": _table(["模块", "文档"], module_rows),
        "{DELTA_LINKS}": _bullets(delta_links),
        "{TASK_LINKS}": _table(["任务", "阅读入口"], task_rows),
    })


def build_payloads(
    doc_root: Path,
    *,
    versions: Iterable[str] = (),
    pending: Mapping[Path, bytes] | None = None,
) -> dict[Path, bytes]:
    root = doc_layout.validate_doc_root(doc_root)
    pending_map = _resolved_pending(pending)
    selected = sorted(set(versions) or set(_version_names(root, pending_map)))
    targets = {root / "docs" / "README.md", *(root / "version" / value / "version_summary.md" for value in selected)}
    planned = dict(pending_map)
    planned.update({path.resolve(): b"" for path in targets})
    payloads = {
        (root / "version" / version / "version_summary.md"): render_version_summary(root, version, planned).encode("utf-8")
        for version in selected
    }
    planned.update({path.resolve(): content for path, content in payloads.items()})
    payloads[root / "docs" / "README.md"] = render_project_index(root, planned).encode("utf-8")
    validate_payloads(
        root,
        payloads,
        pending={**pending_map, **_resolved_pending(payloads)},
        versions=selected,
    )
    return payloads


def validate_links(
    path: Path,
    text: str,
    doc_root: Path,
    pending: Mapping[Path, bytes] | None = None,
) -> list[str]:
    return reference_resolution.validate_markdown_links(
        path,
        text,
        doc_root,
        pending=_resolved_pending(pending),
    )


def _link_target(raw: str) -> str:
    """Compatibility helper for archive traversal; validation uses the shared resolver."""
    return reference_resolution.markdown_link_path(raw)


def _page_link_errors(
    doc_root: Path,
    versions: Iterable[str],
    pending: Mapping[Path, bytes] | None = None,
) -> list[str]:
    pending_map = _resolved_pending(pending)
    errors: list[str] = []
    for version in sorted(set(versions)):
        for module in _version_module_names(doc_root, version, pending_map):
            module_dir = doc_root / "version" / version / "module" / module
            try:
                pages = page_documents.discover_module_page_documents(module_dir, module, pending_map)
            except page_documents.PageDocumentError as exc:
                errors.append(str(exc))
                continue
            errors.extend(page_documents.validate_page_document_links(doc_root, pages, pending_map))
    return errors


def validate_payloads(
    doc_root: Path,
    payloads: Mapping[Path, bytes],
    *,
    pending: Mapping[Path, bytes] | None = None,
    versions: Iterable[str] = (),
) -> None:
    combined = {**_resolved_pending(pending), **_resolved_pending(payloads)}
    selected = sorted(set(versions) or set(_version_names(doc_root, combined)))
    errors: list[str] = []
    for path, content in payloads.items():
        text = content.decode("utf-8")
        if GENERATED_MARKER not in text:
            errors.append(f"generated portal marker is missing: {path}")
        errors.extend(validate_links(Path(path), text, doc_root, combined))
    errors.extend(_page_link_errors(doc_root, selected, combined))
    if errors:
        raise DocumentPortalError("; ".join(errors))


def write_portals(doc_root: Path, *, versions: Iterable[str] = ()) -> list[Path]:
    payloads = build_payloads(doc_root, versions=versions)
    for path, content in payloads.items():
        common_utils.atomic_write_text(path, content.decode("utf-8"))
    return sorted(payloads)


def validate_existing(doc_root: Path, *, versions: Iterable[str] = ()) -> dict[str, Any]:
    root = doc_layout.validate_doc_root(doc_root)
    selected = sorted(set(versions) or set(_version_names(root, {})))
    paths = [root / "docs" / "README.md", *(root / "version" / value / "version_summary.md" for value in selected)]
    errors: list[str] = []
    for path in paths:
        if not path.is_file():
            errors.append(f"missing generated portal: {path}")
            continue
        text = path.read_text(encoding="utf-8")
        if GENERATED_MARKER not in text:
            errors.append(f"generated portal marker is missing: {path}")
        errors.extend(validate_links(path, text, root))
    errors.extend(_page_link_errors(root, selected))
    return {"valid": not errors, "files": [str(path) for path in paths], "errors": errors}


def command_render(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "document_portal", "render")
    paths = write_portals(Path(args.doc_root), versions=args.version)
    payload = {"status": "RENDERED", "files": [str(path) for path in paths]}
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else "\n".join(payload["files"]))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "document_portal", "validate", declared=True)
    payload = validate_existing(Path(args.doc_root), versions=args.version)
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else ("PASSED" if payload["valid"] else "\n".join(payload["errors"])))
    return 0 if payload["valid"] else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    render = sub.add_parser("render", help="generate project README and version summaries")
    agent_context.add_agent_argument(render)
    render.add_argument("--doc-root", required=True)
    render.add_argument("--version", action="append", default=[], help="version to render; repeatable; defaults to all")
    render.add_argument("--json", action="store_true")
    render.set_defaults(handler=command_render)
    validate = sub.add_parser("validate", help="validate generated portal markers and relative links")
    agent_context.add_agent_argument(validate)
    validate.add_argument("--doc-root", required=True)
    validate.add_argument("--version", action="append", default=[], help="version to validate; repeatable; defaults to all")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(handler=command_validate)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        doc_layout.validate_cli_namespace(args)
        return int(args.handler(args))
    except (DocumentPortalError, ValueError, OSError, agent_context.AgentAuthorizationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
