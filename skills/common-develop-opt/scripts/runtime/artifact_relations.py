#!/usr/bin/env python3
"""Build, validate, render, and query the derived cross-document artifact graph."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import unquote

from document_core import agent_context
from document_core import common_utils
from runtime import doc_layout
from document_core import simple_yaml
from resource_paths import asset_path

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = asset_path('assets/documents/artifact-relation.schema.json')
GENERATED_MARKER = "<!-- generated-by: common-develop/artifact_relations.py -->"
ID_RE = re.compile(
    r"\b(?:REQ-[A-Z0-9_-]+|[A-Z][A-Z0-9_]*[0-9]{3,}|[A-Z][A-Z0-9_-]*-F[0-9]+|"
    r"PAGEACT-[A-Z0-9_-]+|PAGE-[A-Z0-9_-]+|DESIGN-[A-Z0-9_-]+|FLOW-[A-Z0-9_-]+|"
    r"STEP-[A-Z0-9_-]+|(?:AGG|INV|SM|EVT|ENT|VO|SVC|POL|TERM)-[A-Z0-9_-]+|"
    r"CMI-[A-Z0-9_-]+|CMSTEP-[A-Z0-9_-]+|CMFAIL-[A-Z0-9_-]+|"
    r"(?:TC|CASE|AC|BR|CR|TR)-[A-Z0-9_-]+)\b"
)
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
TEST_CASE_BLOCK_RE = re.compile(r"<!--\s*TEST-CASE:\s*((?:CASE|TC)-[A-Z0-9_-]+)\s*-->")

COMMAND_HELP_METADATA = {
    "build": {"mutation": True, "previewSupported": False, "effect": "DERIVED_RELATION_VIEWS_WRITE"},
    "validate": {"mutation": False, "previewSupported": False, "effect": "READ_ONLY"},
    "query": {"mutation": False, "previewSupported": False, "effect": "READ_ONLY"},
}


class ArtifactRelationError(ValueError):
    pass


def _pending_map(pending: Mapping[Path, bytes] | None) -> dict[Path, bytes]:
    normalized: dict[Path, bytes] = {}
    if pending:
        for path, content in pending.items():
            normalized[Path(path).resolve()] = bytes(content)
    return normalized


def _read(path: Path, pending: Mapping[Path, bytes]) -> bytes | None:
    resolved = path.resolve()
    if resolved in pending:
        return pending[resolved]
    return path.read_bytes() if path.is_file() else None


def _files(root: Path, pending: Mapping[Path, bytes], suffixes: tuple[str, ...]) -> list[Path]:
    values = {path.resolve() for path in root.rglob("*") if path.is_file() and path.suffix.lower() in suffixes}
    for path in pending:
        try:
            path.relative_to(root.resolve())
        except ValueError:
            continue
        if path.suffix.lower() in suffixes:
            values.add(path)
    return sorted(values, key=lambda item: item.as_posix())


def _document_ref(doc_root: Path, path: Path, anchor: str = "") -> str:
    try:
        value = path.resolve().relative_to(doc_root.resolve()).as_posix()
    except ValueError as exc:
        raise ArtifactRelationError(f"artifact source escapes DOC_ROOT: {path}") from exc
    return value + (f"#{anchor}" if anchor else "")


def _readable_path(path: Path, pending: Mapping[Path, bytes]) -> Path:
    markdown = path.with_suffix(".md")
    return markdown if _read(markdown, pending) is not None else path


def _kind(value: str) -> str | None:
    if value.startswith("PAGEACT-"):
        return "page_action"
    if value.startswith("PAGE-"):
        return "page"
    if value.startswith("DESIGN-"):
        return "design"
    if value.startswith("FLOW-"):
        return "business_flow"
    if value.startswith("STEP-"):
        return "flow_step"
    if value.startswith(("AGG-", "INV-", "SM-", "EVT-", "ENT-", "VO-", "SVC-", "POL-", "TERM-")):
        return "business_model"
    if value.startswith(("CMI-", "CMSTEP-", "CMFAIL-")):
        return "cross_module"
    if value.startswith(("TC-", "CASE-", "AC-")):
        return "validation"
    if re.search(r"-F[0-9]+$", value):
        return "feature"
    if value.startswith(("BR-", "CR-", "TR-")):
        return None
    if value.startswith("REQ-") or re.fullmatch(r"[A-Z][A-Z0-9_]*[0-9]{3,}", value):
        return "requirement"
    return None


def _anchor(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", value.lower()).strip("-")


def _heading_anchor(value: str) -> str:
    normalized = re.sub(r"[^\w\s-]", "", value.lower(), flags=re.UNICODE)
    return re.sub(r"[\s_]+", "-", normalized).strip("-")


def _document_priority(doc_root: Path, path: Path) -> int:
    relative = path.resolve().relative_to(doc_root.resolve()).as_posix()
    if re.match(r"^docs/[^/]+/design/(?:pages/)?[^/]+\.md$", relative):
        return 0
    if relative.startswith("docs/") and not relative.endswith("_design.md"):
        return 1
    if relative.startswith("version/"):
        return 2
    return 3


def _dependency_kind(node_type: str, node_id: str) -> str | None:
    direct = _kind(node_id)
    if direct:
        return direct
    return {
        "REQUIREMENT": "requirement", "BUSINESS_RULE": "requirement", "FEATURE": "feature",
        "ACCEPTANCE_CRITERION": "validation", "TEST_CASE": "validation",
        "PROCESS": "business_flow", "MODULE": "module",
        "TERM": "business_model", "ENTITY": "business_model",
        "VALUE_OBJECT": "business_model", "AGGREGATE": "business_model",
        "INVARIANT": "business_model", "STATE_MACHINE": "business_model",
        "DOMAIN_SERVICE": "business_model", "EVENT": "business_model",
        "API": "cross_module", "DATA_OBJECT": "cross_module",
        "SYSTEM": "cross_module", "DOMAIN": "cross_module",
        "BOUNDED_CONTEXT": "cross_module", "COMPONENT": "cross_module",
    }.get(node_type)


class Builder:
    def __init__(self, doc_root: Path, pending: Mapping[Path, bytes] | None = None):
        self.doc_root = doc_layout.validate_doc_root(doc_root)
        self.pending = _pending_map(pending)
        self.nodes: dict[str, dict[str, Any]] = {}
        self.node_priorities: dict[str, int] = {}
        self.relationships: dict[tuple[str, str, str], dict[str, Any]] = {}

    def node(
        self,
        node_id: str,
        kind: str,
        path: Path,
        *,
        name: str = "",
        anchor: str = "",
        revision: str = "",
        status: str = "ACTIVE",
    ) -> None:
        source = _document_ref(self.doc_root, path, anchor)
        current = self.nodes.get(node_id)
        if current and current["kind"] != kind:
            raise ArtifactRelationError(f"artifact ID has conflicting kinds: {node_id}")
        if current:
            current["sourceRefs"] = sorted(set(current["sourceRefs"] + [source]))
            priority = _document_priority(self.doc_root, path)
            if priority < self.node_priorities[node_id]:
                current["documentRef"] = source
                self.node_priorities[node_id] = priority
            if revision and not current.get("revision"):
                current["revision"] = revision
            return
        value = {
            "id": node_id,
            "kind": kind,
            "name": name.strip() or node_id,
            "status": status if status in {"ACTIVE", "DEPRECATED", "SUPERSEDED"} else "ACTIVE",
            "documentRef": source,
            "sourceRefs": [source],
        }
        if revision:
            value["revision"] = revision
        self.nodes[node_id] = value
        self.node_priorities[node_id] = _document_priority(self.doc_root, path)

    def relation(self, source: str, target: str, relation_type: str, path: Path) -> None:
        if not source or not target or source == target:
            return
        key = (source, target, relation_type)
        ref = _document_ref(self.doc_root, path)
        if key in self.relationships:
            self.relationships[key]["sourceRefs"] = sorted(set(self.relationships[key]["sourceRefs"] + [ref]))
            return
        digest = hashlib.sha256("\0".join(key).encode("utf-8")).hexdigest()[:16].upper()
        self.relationships[key] = {
            "id": f"ARREL-{digest}",
            "from": source,
            "to": target,
            "type": relation_type,
            "sourceRefs": [ref],
        }

    def scan_module_descriptions(self) -> None:
        candidates = [path for path in _files(self.doc_root, self.pending, (".md",)) if path.name.endswith("_desc.md")]
        for path in candidates:
            raw = _read(path, self.pending)
            if raw is None:
                continue
            text = raw.decode("utf-8")
            module = path.name.removesuffix("_desc.md")
            self.node(f"MODULE-{module}", "module", path, name=module)
            for line in text.splitlines():
                if not line.startswith("|") or "---" in line:
                    continue
                cells = [cell.strip() for cell in line.strip("|").split("|")]
                if not cells:
                    continue
                feature = cells[0]
                if _kind(feature) != "feature":
                    continue
                self.node(feature, "feature", path, name=cells[1] if len(cells) > 1 else feature, anchor="模块功能")
                self.relation(f"MODULE-{module}", feature, "CONTAINS", path)
                if len(cells) > 4:
                    for requirement in ID_RE.findall(cells[4]):
                        if _kind(requirement) == "requirement":
                            self.node(requirement, "requirement", path, name=requirement, anchor="模块功能")
                            self.relation(requirement, feature, "CONTAINS", path)

    def scan_requirements_and_designs(self) -> None:
        for path in _files(self.doc_root, self.pending, (".md",)):
            if any(part in {"task", "evidence", "reviews", "cache", "generated"} for part in path.parts):
                continue
            raw = _read(path, self.pending)
            if raw is None:
                continue
            text = raw.decode("utf-8")
            ids = sorted(set(ID_RE.findall(text)))
            design_match = re.search(r"^>\s*Design topic：`([^`]+)`", text, re.M)
            revision_match = re.search(r"^>\s*Design revision：`([^`]+)`", text, re.M)
            design_id = design_match.group(1) if design_match else ""
            if design_id:
                self.node(design_id, "design", path, name=path.stem, revision=revision_match.group(1) if revision_match else "")
                self.nodes[design_id]["sourceRefs"] = sorted(set(
                    self.nodes[design_id]["sourceRefs"]
                    + [_document_ref(self.doc_root, path, anchor) for anchor in (
                        "page-behavior", "data-write", "api-mapping", "code-blueprint"
                    ) if f'id="{anchor}"' in text]
                ))
            for value in ids:
                kind = _kind(value)
                if kind:
                    self.node(value, kind, path, name=value)
            requirements = [value for value in ids if _kind(value) == "requirement"]
            features = [value for value in ids if _kind(value) == "feature"]
            pages = [value for value in ids if _kind(value) == "page"]
            actions = [value for value in ids if _kind(value) == "page_action"]
            for requirement in requirements:
                for feature in features:
                    self.relation(requirement, feature, "CONTAINS", path)
            for feature in features:
                for page in pages:
                    self.relation(feature, page, "EXPOSED_BY", path)
                if design_id:
                    self.relation(feature, design_id, "DESIGNED_BY", path)
            for page in pages:
                for action in actions:
                    self.relation(page, action, "HAS_ACTION", path)
                if design_id:
                    self.relation(page, design_id, "REFERENCES", path)
            for requirement in requirements:
                if design_id:
                    self.relation(requirement, design_id, "DESIGNED_BY", path)

    def scan_business_models(self) -> None:
        for path in _files(self.doc_root, self.pending, (".yaml", ".yml")):
            if not path.name.endswith("_business_model.yaml"):
                continue
            raw = _read(path, self.pending)
            if raw is None:
                continue
            data = simple_yaml.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                continue
            revision = str(data.get("revision") or "")
            view = _readable_path(path, self.pending)
            model_ids: list[str] = []
            for collection in ("aggregates", "invariants", "stateMachines", "events", "entities", "valueObjects", "services", "policies", "terms"):
                for item in data.get(collection, []) or []:
                    if isinstance(item, dict) and isinstance(item.get("id"), str):
                        model_id = item["id"]
                        model_ids.append(model_id)
                        self.node(model_id, "business_model", view, name=str(item.get("name") or item.get("statement") or model_id), revision=revision)
            for trace in data.get("traceability", []) or []:
                if not isinstance(trace, dict):
                    continue
                sources = [str(value) for value in trace.get("requirementRefs", []) or trace.get("featureRefs", []) or []]
                targets = [str(value) for value in trace.get("modelRefs", []) or []]
                for source in sources:
                    kind = _kind(source)
                    if kind:
                        self.node(source, kind, view, name=source)
                        for target in targets:
                            if target in self.nodes:
                                self.relation(source, target, "MODELED_BY", path)

    def scan_test_cases(self) -> None:
        """Project requirement/page/design/flow facts onto requirement-scoped Case nodes."""
        for path in _files(self.doc_root, self.pending, (".md",)):
            if path.name != "test_case.md":
                continue
            raw = _read(path, self.pending)
            if raw is None:
                continue
            text = raw.decode("utf-8")
            matches = list(TEST_CASE_BLOCK_RE.finditer(text))
            for index, match in enumerate(matches):
                case_id = match.group(1)
                end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
                block = text[match.end():end]
                self.node(case_id, "validation", path, name=case_id, anchor=case_id)
                for source in sorted(set(ID_RE.findall(block))):
                    kind = _kind(source)
                    if not kind or source == case_id or kind == "validation":
                        continue
                    self.node(source, kind, path, name=source)
                    self.relation(source, case_id, "VERIFIED_BY", path)

    def scan_flows(self) -> None:
        for path in _files(self.doc_root, self.pending, (".yaml", ".yml")):
            if not path.name.endswith("_flow.yaml"):
                continue
            raw = _read(path, self.pending)
            if raw is None:
                continue
            data = simple_yaml.loads(raw.decode("utf-8"))
            if not isinstance(data, dict) or data.get("artifact") != "business_flow":
                continue
            revision = str(data.get("revision") or "")
            view = _readable_path(path, self.pending)
            for flow in data.get("flows", []) or []:
                if not isinstance(flow, dict) or not flow.get("id"):
                    continue
                flow_id = str(flow["id"])
                self.node(flow_id, "business_flow", view, name=str(flow.get("name") or flow_id), anchor=_anchor(flow_id) if view.suffix == ".md" else "", revision=revision, status=str(flow.get("status") or "ACTIVE"))
                features = [str(item.get("id")) for item in flow.get("featureRefs", []) or [] if isinstance(item, dict) and item.get("id")]
                requirements = [str(item.get("id")) for item in flow.get("requirementRefs", []) or [] if isinstance(item, dict) and item.get("id")]
                for value in features + requirements:
                    kind = _kind(value)
                    if kind:
                        self.node(value, kind, view, name=value)
                        self.relation(value, flow_id, "PARTICIPATES_IN", path)
                for step in flow.get("steps", []) or []:
                    if isinstance(step, dict) and step.get("id"):
                        step_id = str(step["id"])
                        self.node(step_id, "flow_step", view, name=str(step.get("name") or step_id), anchor=_anchor(flow_id) if view.suffix == ".md" else "")
                        self.relation(flow_id, step_id, "CONTAINS", path)
                for model_id in flow.get("businessModelRefs", []) or []:
                    if str(model_id) in self.nodes:
                        self.relation(flow_id, str(model_id), "MODELED_BY", path)
                for design_id in flow.get("designRefs", []) or []:
                    if str(design_id) in self.nodes:
                        self.relation(flow_id, str(design_id), "DESIGNED_BY", path)
                for cross_id in flow.get("dependencyImpactRefs", []) or []:
                    if str(cross_id) in self.nodes:
                        self.relation(flow_id, str(cross_id), "IMPLEMENTED_BY", path)
                for case_id in flow.get("testCaseIds", []) or []:
                    case = str(case_id)
                    self.node(case, "validation", path, name=case, anchor=_anchor(flow_id))
                    self.relation(flow_id, case, "VERIFIED_BY", path)

    def scan_dependency_impact(self) -> None:
        path = self.doc_root / "docs" / "_relations" / "dependency_impact.yaml"
        raw = _read(path, self.pending)
        if raw is None:
            return
        data = simple_yaml.loads(raw.decode("utf-8"))
        if not isinstance(data, dict):
            raise ArtifactRelationError(f"dependency impact must be a mapping: {path}")
        view = _readable_path(path, self.pending)
        for item in data.get("nodes", []) or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            node_id = str(item["id"])
            kind = _dependency_kind(str(item.get("type") or ""), node_id)
            if kind:
                self.node(node_id, kind, view, name=str(item.get("name") or node_id), revision=str(data.get("revision") or ""), status=str(item.get("status") or "ACTIVE"))
        for item in data.get("relationships", []) or []:
            if not isinstance(item, dict):
                continue
            source, target = str(item.get("from") or ""), str(item.get("to") or "")
            relation_type = str(item.get("type") or "REFERENCES")
            normalized = relation_type if relation_type in {"CONTAINS", "IMPLEMENTED_BY", "VERIFIED_BY", "AFFECTS", "DEPENDS_ON"} else "REFERENCES"
            self.relation(source, target, normalized, path)
        for item in data.get("crossModuleImplementations", []) or []:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            cross_id = str(item["id"])
            self.node(cross_id, "cross_module", view, name=str(item.get("name") or cross_id), revision=str(data.get("revision") or ""))
            for step in item.get("steps", []) or []:
                if isinstance(step, dict) and step.get("id"):
                    step_id = str(step["id"])
                    self.node(step_id, "cross_module", view, name=str(step.get("action") or step_id))
                    self.relation(cross_id, step_id, "CONTAINS", path)

    def build(self) -> dict[str, Any]:
        self.scan_module_descriptions()
        self.scan_requirements_and_designs()
        self.scan_test_cases()
        self.scan_business_models()
        self.scan_dependency_impact()
        self.scan_flows()
        dangling = sorted({value for item in self.relationships.values() for value in (item["from"], item["to"]) if value not in self.nodes})
        if dangling:
            raise ArtifactRelationError(f"artifact relationships contain dangling nodes: {dangling}")
        semantic = {
            "nodes": [self.nodes[key] for key in sorted(self.nodes)],
            "relationships": [self.relationships[key] for key in sorted(self.relationships)],
        }
        digest = hashlib.sha256(json.dumps(semantic, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        result = {"schemaVersion": 1, "artifact": "artifact_relations", "authority": "DERIVED", "revision": f"AR-R01@{digest[:12]}", **semantic}
        validate_document(result, self.doc_root, self.pending)
        return result


def _target_path(ref: str, doc_root: Path) -> Path:
    value = unquote(ref.split("#", 1)[0])
    target = (doc_root / value).resolve()
    try:
        target.relative_to(doc_root.resolve())
    except ValueError as exc:
        raise ArtifactRelationError(f"documentRef escapes DOC_ROOT: {ref}") from exc
    return target


def _anchor_exists(ref: str, content: bytes) -> bool:
    _, separator, raw_anchor = ref.partition("#")
    if not separator:
        return True
    anchor = unquote(raw_anchor).strip()
    if not anchor:
        return False
    text = content.decode("utf-8")
    if re.search(rf'<a\s+(?:name|id)=["\']{re.escape(anchor)}["\']', text, re.I):
        return True
    for heading in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", text, re.M):
        plain = re.sub(r"[`*_~]", "", heading)
        if _heading_anchor(plain) == anchor:
            return True
    return False


def validate_document(document: dict[str, Any], doc_root: Path, pending: Mapping[Path, bytes] | None = None) -> None:
    if jsonschema is None:
        raise ArtifactRelationError("jsonschema is required")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    try:
        jsonschema.validators.validator_for(schema)(schema).validate(document)
    except jsonschema.ValidationError as exc:
        where = ".".join(str(part) for part in exc.absolute_path) or "<root>"
        raise ArtifactRelationError(f"artifact relation schema violation at {where}: {exc.message}") from exc
    pending_map = _pending_map(pending)
    ids = [str(item["id"]) for item in document["nodes"]]
    if len(ids) != len(set(ids)):
        raise ArtifactRelationError("artifact relation nodes contain duplicate IDs")
    known = set(ids)
    relation_ids: set[str] = set()
    semantic: set[tuple[str, str, str]] = set()
    for item in document["relationships"]:
        if item["id"] in relation_ids:
            raise ArtifactRelationError(f"duplicate artifact relationship ID: {item['id']}")
        relation_ids.add(item["id"])
        key = (item["from"], item["to"], item["type"])
        if key in semantic:
            raise ArtifactRelationError(f"duplicate artifact relationship: {key}")
        semantic.add(key)
        for endpoint in (item["from"], item["to"]):
            if endpoint not in known:
                raise ArtifactRelationError(f"relationship {item['id']} references missing node: {endpoint}")
    for item in document["nodes"]:
        for ref in sorted(set([item["documentRef"], *item.get("sourceRefs", [])])):
            target = _target_path(ref, doc_root)
            content = pending_map.get(target.resolve())
            if content is None and target.is_file():
                content = target.read_bytes()
            if content is None:
                raise ArtifactRelationError(f"node {item['id']} document reference does not exist: {ref}")
            if target.suffix.lower() == ".md" and not _anchor_exists(ref, content):
                raise ArtifactRelationError(f"node {item['id']} document anchor does not exist: {ref}")


def _relative(source: Path, target_ref: str, doc_root: Path, label: str) -> str:
    target, _, anchor = target_ref.partition("#")
    value = os.path.relpath(doc_root / target, source.parent).replace(os.sep, "/")
    return f"[{label}]({value}{('#' + anchor) if anchor else ''})"


def _reachable(document: dict[str, Any], start: str, depth: int = 5) -> set[str]:
    adjacency: dict[str, set[str]] = defaultdict(set)
    for item in document["relationships"]:
        adjacency[item["from"]].add(item["to"])
        adjacency[item["to"]].add(item["from"])
    seen = {start}
    queue = deque([(start, 0)])
    while queue:
        current, level = queue.popleft()
        if level >= depth:
            continue
        for target in sorted(adjacency[current]):
            if target not in seen:
                seen.add(target)
                queue.append((target, level + 1))
    return seen


def render_traceability(document: dict[str, Any], doc_root: Path, output: Path) -> str:
    nodes = {item["id"]: item for item in document["nodes"]}
    columns = [
        ("feature", "功能"), ("page", "页面"), ("business_flow", "流程"),
        ("business_model", "业务模型"), ("cross_module", "跨模块"),
        ("design", "设计"), ("validation", "验证"),
    ]
    lines = [GENERATED_MARKER, "# 全量研发事实追踪矩阵", "", "> 导航：[项目文档首页](../README.md) · [依赖关系图](dependency_graph.md)", "", f"- Revision：`{document['revision']}`", "", "| 需求 | " + " | ".join(label for _, label in columns) + " |", "| --- | " + " | ".join("---" for _ in columns) + " |"]
    requirements = [item for item in document["nodes"] if item["kind"] == "requirement"]
    for requirement in requirements:
        reachable = _reachable(document, requirement["id"])
        row = [_relative(output, requirement["documentRef"], doc_root, requirement["id"])]
        for kind, _ in columns:
            values = [nodes[value] for value in sorted(reachable) if value in nodes and nodes[value]["kind"] == kind]
            row.append("<br>".join(_relative(output, item["documentRef"], doc_root, item["id"]) for item in values) or "-")
        lines.append("| " + " | ".join(row) + " |")
    if not requirements:
        lines.append("| - | " + " | ".join("-" for _ in columns) + " |")
    lines.extend(["", "## 设计反向来源", "", "| 设计主题 | 来源需求/功能 | 当前文档 |", "| --- | --- | --- |"])
    incoming: dict[str, list[str]] = defaultdict(list)
    for relation in document["relationships"]:
        if relation["type"] == "DESIGNED_BY":
            incoming[relation["to"]].append(relation["from"])
    for design in [item for item in document["nodes"] if item["kind"] == "design"]:
        refs = [_relative(output, nodes[value]["documentRef"], doc_root, value) for value in sorted(set(incoming[design["id"]])) if value in nodes]
        lines.append(f"| `{design['id']}` | {'<br>'.join(refs) or '-'} | {_relative(output, design['documentRef'], doc_root, '打开')} |")
    return "\n".join(lines).rstrip() + "\n"


def render_dependency(document: dict[str, Any], doc_root: Path, output: Path) -> str:
    nodes = {item["id"]: item for item in document["nodes"]}
    lines = [GENERATED_MARKER, "# 研发事实依赖关系图", "", "> 导航：[项目文档首页](../README.md) · [全量追踪矩阵](traceability_matrix.md)", "", f"- Revision：`{document['revision']}`", "", "```mermaid", "flowchart LR"]
    aliases = {node_id: "N" + hashlib.sha256(node_id.encode()).hexdigest()[:10] for node_id in nodes}
    for node_id in sorted(nodes):
        label = f"{node_id}<br/>{nodes[node_id]['kind']}".replace('"', "'")
        lines.append(f'  {aliases[node_id]}["{label}"]')
    for item in document["relationships"]:
        lines.append(f"  {aliases[item['from']]} -->|{item['type']}| {aliases[item['to']]}")
    lines.extend(["```", "", "## 可跳转关系明细", "", "| 来源 | 关系 | 目标 | 事实来源 |", "| --- | --- | --- | --- |"])
    for item in document["relationships"]:
        source = nodes[item["from"]]
        target = nodes[item["to"]]
        refs = "<br>".join(_relative(output, ref, doc_root, "来源") for ref in item["sourceRefs"])
        lines.append(f"| {_relative(output, source['documentRef'], doc_root, source['id'])} | `{item['type']}` | {_relative(output, target['documentRef'], doc_root, target['id'])} | {refs} |")
    return "\n".join(lines).rstrip() + "\n"


def build_payloads(doc_root: Path, pending: Mapping[Path, bytes] | None = None) -> dict[Path, bytes]:
    root = doc_layout.validate_doc_root(doc_root)
    document = Builder(root, pending).build()
    relation_root = root / "docs" / "_relations"
    yaml_path = relation_root / "engineering_graph.yaml"
    trace_path = relation_root / "traceability_matrix.md"
    dependency_path = relation_root / "dependency_graph.md"
    return {
        yaml_path: simple_yaml.dumps(document).encode("utf-8"),
        trace_path: render_traceability(document, root, trace_path).encode("utf-8"),
        dependency_path: render_dependency(document, root, dependency_path).encode("utf-8"),
    }


def write_views(doc_root: Path) -> list[Path]:
    payloads = build_payloads(doc_root)
    for path, content in payloads.items():
        common_utils.atomic_write_text(path, content.decode("utf-8"))
    return sorted(payloads)


def load_graph(path: Path, doc_root: Path) -> dict[str, Any]:
    document = simple_yaml.load(path)
    if not isinstance(document, dict):
        raise ArtifactRelationError(f"graph must be a mapping: {path}")
    validate_document(document, doc_root)
    return document


def command_build(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "artifact_relations", "build")
    paths = write_views(Path(args.doc_root))
    payload = {"status": "BUILT", "files": [str(path) for path in paths]}
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else "\n".join(payload["files"]))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "artifact_relations", "validate", declared=True)
    root = doc_layout.validate_doc_root(Path(args.doc_root))
    graph = Path(args.graph) if args.graph else root / "docs" / "_relations" / "engineering_graph.yaml"
    document = load_graph(graph, root)
    payload = {"status": "PASSED", "revision": document["revision"], "nodeCount": len(document["nodes"]), "relationshipCount": len(document["relationships"])}
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else f"VALID {graph}")
    return 0


def command_query(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "artifact_relations", "query", declared=True)
    root = doc_layout.validate_doc_root(Path(args.doc_root))
    graph = Path(args.graph) if args.graph else root / "docs" / "_relations" / "engineering_graph.yaml"
    document = load_graph(graph, root)
    nodes = {item["id"]: item for item in document["nodes"]}
    if args.ref not in nodes:
        raise ArtifactRelationError(f"unknown artifact ref: {args.ref}")
    reachable_ids = _reachable(document, args.ref, args.depth)
    connected = [
        item for item in document["relationships"]
        if item["from"] in reachable_ids and item["to"] in reachable_ids
    ]
    related_by_kind: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node_id in sorted(reachable_ids - {args.ref}):
        related_by_kind[str(nodes[node_id]["kind"])].append(nodes[node_id])
    payload = {
        "status": "FOUND",
        "node": nodes[args.ref],
        "depth": args.depth,
        "relatedByKind": dict(sorted(related_by_kind.items())),
        "relationships": connected,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="rebuild engineering graph and human trace views")
    agent_context.add_agent_argument(build)
    build.add_argument("--doc-root", required=True)
    build.add_argument("--json", action="store_true")
    build.set_defaults(handler=command_build)
    validate = sub.add_parser("validate", help="validate graph schema, references, and document links")
    agent_context.add_agent_argument(validate)
    validate.add_argument("--doc-root", required=True)
    validate.add_argument("--graph", default="")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(handler=command_validate)
    query = sub.add_parser("query", help="query one artifact and its direct forward/reverse relationships")
    agent_context.add_agent_argument(query)
    query.add_argument("--doc-root", required=True)
    query.add_argument("--graph", default="")
    query.add_argument("--ref", required=True)
    query.add_argument("--depth", type=int, default=5, choices=range(1, 9))
    query.add_argument("--json", action="store_true")
    query.set_defaults(handler=command_query)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        doc_layout.validate_cli_namespace(args)
        return int(args.handler(args))
    except (ArtifactRelationError, OSError, ValueError, simple_yaml.YamlError, agent_context.AgentAuthorizationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
