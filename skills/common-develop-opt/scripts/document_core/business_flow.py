#!/usr/bin/env python3
"""Create, validate, render, inspect, and merge layered project business flows.

Business-flow YAML stores stable IDs and DOC_ROOT-relative document references. Markdown
links are derived from the output file location, so version previews and project-level
archive documents remain valid after ``wk -d`` changes directories.
"""

from __future__ import annotations

import sys
from pathlib import Path
_COMPAT_ROOT = Path(__file__).resolve().parent.parent / "compat"
_SCRIPT_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_SCRIPT_ROOT), str(_COMPAT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import argparse
import copy
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from document_core import agent_context
from document_core import simple_yaml
from document_core import document_facts
from document_core.document_facts import _parse_module_desc
from document_core import document_portal
from runtime import doc_layout
from document_core.common_utils import sha256_bytes as _sha256_bytes
from runtime._core.validation import make_require_helpers
from runtime._core.cli_arguments import (add_agent_argument, add_doc_root_argument, add_file_argument, add_json_argument, add_repeatable_argument, add_task_dir_argument, build_parser as build_common_parser, set_handler)
from runtime._core.document_utils import write_preview_files
from resource_paths import asset_path

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_ROOT = asset_path('assets/documents/structured-docs/business-flow.schema.json').parent
GENERATED_MARKER = "<!-- generated-by: common-develop/business_flow.py -->"
INITIAL_REVISION = "FLOW-R00@init"
LEVEL_ORDER = {"L0": 0, "L1": 1, "L2": 2}


class BusinessFlowError(ValueError):
    pass


@dataclass
class GroupSimulation:
    group: str
    document: dict[str, Any]
    records: list[dict[str, Any]]
    summaries: list[str]
    skipped: list[str]
    source_paths: list[Path]


def _schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_ROOT / name).read_text(encoding="utf-8"))


def validate_schema(value: Any, name: str, label: str) -> None:
    if jsonschema is None:
        raise BusinessFlowError("jsonschema is required")
    try:
        jsonschema.validate(value, _schema(name))
    except jsonschema.ValidationError as exc:
        location = ".".join(str(item) for item in exc.absolute_path) or "<root>"
        raise BusinessFlowError(f"{label} schema violation at {location}: {exc.message}") from exc


require_mapping, require_list, require_text = make_require_helpers(BusinessFlowError)


def sha256_bytes(value: bytes) -> str:
    """Hash bytes through the shared digest helper."""
    return _sha256_bytes(value)


def canonical_yaml_path(doc_root: Path, group: str) -> Path:
    return doc_root / "docs" / "_flows" / f"{group}_flow.yaml"


def canonical_markdown_path(doc_root: Path, group: str) -> Path:
    return doc_root / "docs" / "_flows" / f"{group}_flow.md"


def flow_index_yaml_path(doc_root: Path) -> Path:
    return doc_root / "docs" / "_flows" / "flow_index.yaml"


def flow_index_markdown_path(doc_root: Path) -> Path:
    return doc_root / "docs" / "_flows" / "flow_index.md"


def version_group_root(doc_root: Path, version: str, group: str) -> Path:
    return doc_root / "version" / version / "module" / "_flows" / group


def changes_root(doc_root: Path, version: str, group: str) -> Path:
    return version_group_root(doc_root, version, group) / "changes"


def preview_root(doc_root: Path, version: str, group: str) -> Path:
    return version_group_root(doc_root, version, group) / "generated"


def empty_document(group: str, name: str | None = None) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "artifact": "business_flow",
        "flowGroup": {"code": group, "name": name or group, "description": ""},
        "revision": INITIAL_REVISION,
        "baseRevision": "",
        "flows": [],
    }


def empty_changeset(version: str, module: str, group: str, group_name: str) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "artifact": "business_flow_changeset",
        "changeSet": {
            "id": f"FLOW-CHG-{version}-{group}-001",
            "version": version,
            "module": module,
            "flowGroup": group,
            "flowGroupName": group_name,
            "reason": "replace with confirmed business-flow increment reason",
            "baseRevision": INITIAL_REVISION,
            "resultRevision": "FLOW-R01@replace",
            "affectedTraceIds": ["TR-REPLACE-001"],
        },
        "operations": [],
    }


def _safe_root_ref(doc_root: Path, value: str, label: str, *, must_exist: bool = True, pending: Iterable[Path] = ()) -> Path:
    ref = require_text(value, label)
    candidate_ref = Path(ref)
    if candidate_ref.is_absolute() or ".." in candidate_ref.parts:
        raise BusinessFlowError(f"{label} must be a DOC_ROOT-relative path without '..': {ref}")
    candidate = (doc_root / candidate_ref).resolve()
    root = doc_root.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise BusinessFlowError(f"{label} escapes DOC_ROOT: {ref}") from exc
    pending_resolved = {path.resolve() for path in pending}
    if must_exist and not candidate.is_file() and candidate not in pending_resolved:
        raise BusinessFlowError(f"{label} target does not exist: {ref}")
    return candidate


def _contains_stable_id(text: str, value: str) -> bool:
    return re.search(rf"(?<![A-Z0-9_-]){re.escape(value)}(?![A-Z0-9_-])", text) is not None


def _validate_requirement_symbols(flow_id: str, flow: dict[str, Any], requirement_paths: list[Path]) -> None:
    texts = [path.read_text(encoding="utf-8") for path in requirement_paths]
    symbols = (
        [("feature", item["id"]) for item in flow["featureRefs"]]
        + [("rule", item) for item in flow["ruleRefs"]]
        + [("acceptance", item) for item in flow["acceptanceRefs"]]
        + [("trace", item) for item in flow["traceIds"]]
    )
    for kind, symbol in symbols:
        if not any(_contains_stable_id(text, str(symbol)) for text in texts):
            raise BusinessFlowError(f"flow {flow_id} references {kind} not found in requirement document: {symbol}")


def _stable_ids(items: list[Any], label: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(items):
        item = require_mapping(raw, f"{label}[{index}]")
        item_id = require_text(item.get("id"), f"{label}[{index}].id")
        if item_id in result:
            raise BusinessFlowError(f"duplicate {label} id: {item_id}")
        result[item_id] = item
    return result


def _validate_no_parent_cycle(flows: dict[str, dict[str, Any]]) -> None:
    for flow_id in flows:
        chain: set[str] = set()
        current = flow_id
        while current:
            if current in chain:
                raise BusinessFlowError(f"business flow parent cycle detected at {current}")
            chain.add(current)
            parent = flows[current].get("parentFlowId")
            if not parent:
                break
            if parent not in flows:
                raise BusinessFlowError(f"flow {current} references missing parentFlowId: {parent}")
            current = str(parent)


def validate_document(
    document: dict[str, Any],
    doc_root: Path | None = None,
    *,
    require_links: bool = False,
    pending: Iterable[Path] | Mapping[Path, bytes] = (),
) -> None:
    validate_schema(document, "business-flow.schema.json", "business flow")
    group = require_mapping(document.get("flowGroup"), "flowGroup")
    group_code = require_text(group.get("code"), "flowGroup.code")
    flows = _stable_ids(require_list(document.get("flows"), "flows"), "flow")
    _validate_no_parent_cycle(flows)
    pending_content = {
        Path(path).resolve(): bytes(content)
        for path, content in pending.items()
    } if isinstance(pending, Mapping) else {}
    all_feature_ids: set[str] = set()
    all_trace_ids: set[str] = set()
    for flow_id, flow in flows.items():
        parent = flow.get("parentFlowId")
        if parent:
            expected = LEVEL_ORDER[flows[str(parent)]["level"]] + 1
            if LEVEL_ORDER[flow["level"]] != expected:
                raise BusinessFlowError(
                    f"flow {flow_id} level must be one below parent {parent}: {flow['level']}"
                )
        if flow["ownerModule"] not in flow["participantModules"]:
            raise BusinessFlowError(f"flow {flow_id} ownerModule must be included in participantModules")
        requirement_ids: set[str] = set()
        requirement_paths: list[Path] = []
        for index, raw_ref in enumerate(flow["requirementRefs"]):
            ref = require_mapping(raw_ref, f"flow {flow_id}.requirementRefs[{index}]")
            requirement_id = require_text(ref.get("id"), f"flow {flow_id}.requirementRefs[{index}].id")
            if requirement_id in requirement_ids:
                raise BusinessFlowError(f"flow {flow_id} has duplicate requirement ref: {requirement_id}")
            requirement_ids.add(requirement_id)
            if doc_root is not None and require_links:
                requirement_paths.append(_safe_root_ref(doc_root, str(ref["documentRef"]), f"flow {flow_id} requirement document", pending=pending))
        feature_refs: dict[str, str] = {}
        for index, raw_ref in enumerate(flow["featureRefs"]):
            ref = require_mapping(raw_ref, f"flow {flow_id}.featureRefs[{index}]")
            feature_id = require_text(ref.get("id"), f"flow {flow_id}.featureRefs[{index}].id")
            if feature_id in feature_refs:
                raise BusinessFlowError(f"flow {flow_id} has duplicate feature ref: {feature_id}")
            feature_refs[feature_id] = require_text(ref.get("module"), f"flow {flow_id}.featureRefs[{index}].module")
            all_feature_ids.add(feature_id)
        all_trace_ids.update(str(item) for item in flow["traceIds"])
        step_ids = _stable_ids(flow["steps"], f"flow {flow_id}.steps")
        orders = [step["order"] for step in step_ids.values()]
        if len(set(orders)) != len(orders):
            raise BusinessFlowError(f"flow {flow_id} has duplicate step order")
        if sorted(orders) != list(range(1, len(orders) + 1)):
            raise BusinessFlowError(f"flow {flow_id} step order must be contiguous from 1")
        for step_id, step in step_ids.items():
            if step["ownerModule"] not in flow["participantModules"]:
                raise BusinessFlowError(f"step {step_id} ownerModule is not a flow participant")
            if step["featureRef"] not in feature_refs:
                raise BusinessFlowError(f"step {step_id} references feature not declared by flow: {step['featureRef']}")
            if feature_refs[step["featureRef"]] != step["ownerModule"]:
                raise BusinessFlowError(f"step {step_id} feature module does not match ownerModule")
            child = step.get("childFlowRef")
            if child and child not in flows:
                raise BusinessFlowError(f"step {step_id} references missing child flow: {child}")
            if child and flows[child].get("parentFlowId") != flow_id:
                raise BusinessFlowError(f"step {step_id} child flow {child} must declare parentFlowId {flow_id}")
        _stable_ids(flow["variants"], f"flow {flow_id}.variants")
        failures = _stable_ids(flow["failurePaths"], f"flow {flow_id}.failurePaths")
        for failure_id, failure in failures.items():
            if failure["atStep"] not in step_ids:
                raise BusinessFlowError(f"failure path {failure_id} references missing step: {failure['atStep']}")
            followup = failure.get("followupFlowRef")
            if followup and followup not in flows:
                raise BusinessFlowError(f"failure path {failure_id} references missing follow-up flow: {followup}")
            missing_acceptance = sorted(set(failure.get("acceptanceRefs", [])) - set(flow["acceptanceRefs"]))
            if missing_acceptance:
                raise BusinessFlowError(f"failure path {failure_id} references acceptance outside flow: {', '.join(missing_acceptance)}")
        if doc_root is not None and require_links:
            _validate_requirement_symbols(flow_id, flow, requirement_paths)
            feature_ids_by_module: dict[str, set[str]] = {}
            for module in flow["participantModules"]:
                module_path = _safe_root_ref(
                    doc_root,
                    f"docs/module/{module}/{module}_desc.md",
                    f"flow {flow_id} module document",
                    pending=pending,
                )
                module_content = pending_content.get(module_path.resolve())
                if module_content is not None:
                    parsed = _parse_module_desc(module_content.decode("utf-8"), module)
                    feature_ids_by_module[module] = {row[0] for row in parsed["features"]}
                elif module_path.is_file():
                    parsed = _parse_module_desc(module_path.read_text(encoding="utf-8"), module)
                    feature_ids_by_module[module] = {row[0] for row in parsed["features"]}
            for feature_id, module in feature_refs.items():
                if module in feature_ids_by_module and feature_id not in feature_ids_by_module[module]:
                    raise BusinessFlowError(f"flow {flow_id} feature {feature_id} not found in {module}_desc.md")
    if document.get("artifact") != "business_flow" or group_code != document["flowGroup"]["code"]:
        raise BusinessFlowError("invalid business flow document identity")


def validate_changeset(document: dict[str, Any], version: str, module: str, group: str) -> dict[str, Any]:
    validate_schema(document, "business-flow.changeset.schema.json", "business flow changeset")
    info = require_mapping(document.get("changeSet"), "changeSet")
    if info.get("version") != version:
        raise BusinessFlowError(f"changeSet.version must equal {version}")
    if info.get("module") != module:
        raise BusinessFlowError(f"changeSet.module must equal {module}")
    if info.get("flowGroup") != group:
        raise BusinessFlowError(f"changeSet.flowGroup must equal {group}")
    if info["baseRevision"] == info["resultRevision"]:
        raise BusinessFlowError("business flow resultRevision must differ from baseRevision")
    seen: set[str] = set()
    for index, raw in enumerate(document["operations"]):
        operation = require_mapping(raw, f"operations[{index}]")
        target = require_text(operation.get("targetId"), f"operations[{index}].targetId")
        if target in seen:
            raise BusinessFlowError(f"duplicate business flow operation target: {target}")
        seen.add(target)
        if operation["operation"] in {"add", "update"}:
            after = require_mapping(operation.get("after"), f"operations[{index}].after")
            if after.get("id") != target:
                raise BusinessFlowError(f"operations[{index}].after.id must equal targetId")
    return info


def _item_key(value: Any) -> tuple[str, str] | None:
    if not isinstance(value, dict):
        return None
    for key in ("id", "documentRef"):
        if isinstance(value.get(key), str) and value[key]:
            return key, value[key]
    return None


def merge_value(current: Any, changes: Any) -> Any:
    if isinstance(current, dict) and isinstance(changes, dict):
        result = copy.deepcopy(current)
        for key, value in changes.items():
            result[key] = merge_value(result[key], value) if key in result else copy.deepcopy(value)
        return result
    if isinstance(current, list) and isinstance(changes, list):
        combined = current + changes
        if combined and all(_item_key(item) is not None for item in combined):
            result = copy.deepcopy(current)
            positions = {_item_key(item): index for index, item in enumerate(result)}
            for item in changes:
                key = _item_key(item)
                if key in positions:
                    result[positions[key]] = merge_value(result[positions[key]], item)
                else:
                    positions[key] = len(result)
                    result.append(copy.deepcopy(item))
            return result
        return copy.deepcopy(changes)
    return copy.deepcopy(changes)


def load_canonical(doc_root: Path, group: str, name: str | None = None) -> dict[str, Any]:
    path = canonical_yaml_path(doc_root, group)
    if not path.is_file():
        return empty_document(group, name)
    document = require_mapping(simple_yaml.load(path), str(path))
    validate_document(document)
    if document["flowGroup"]["code"] != group:
        raise BusinessFlowError(f"flow group code mismatch in {path}")
    return document


def apply_changeset(state: dict[str, Any], changeset: dict[str, Any], version: str, module: str, group: str) -> tuple[dict[str, Any], list[str]]:
    info = validate_changeset(changeset, version, module, group)
    if state["revision"] != info["baseRevision"]:
        raise BusinessFlowError(
            f"stale business flow base revision for {group}: expected {info['baseRevision']}, actual {state['revision']}"
        )
    flows = state["flows"]
    summaries: list[str] = []
    for operation in changeset["operations"]:
        target = operation["targetId"]
        existing = next((item for item in flows if item["id"] == target), None)
        action = operation["operation"]
        if action == "add":
            if existing is not None:
                raise BusinessFlowError(f"add business flow target already exists: {target}")
            flows.append(copy.deepcopy(operation["after"]))
        elif action == "update":
            if existing is None:
                raise BusinessFlowError(f"update business flow target does not exist: {target}")
            replacement = merge_value(existing, operation["after"])
            if replacement["id"] != target:
                raise BusinessFlowError(f"business flow update cannot change stable ID: {target}")
            flows[flows.index(existing)] = replacement
        else:
            if existing is None:
                raise BusinessFlowError(f"deprecate business flow target does not exist: {target}")
            existing["status"] = "DEPRECATED"
            existing["lastChangedVersion"] = version
        summaries.append(f"{action} business_flow.{group}.{target}: {operation['reason']}")
    state["baseRevision"] = info["baseRevision"]
    state["revision"] = info["resultRevision"]
    if info.get("flowGroupName"):
        state["flowGroup"]["name"] = info["flowGroupName"]
    validate_document(state)
    return info, summaries


def discover_changesets(doc_root: Path, version: str, module: str, groups: set[str] | None = None) -> dict[str, list[Path]]:
    root = doc_root / "version" / version / "module" / "_flows"
    result: dict[str, list[Path]] = {}
    if not root.is_dir():
        return result
    for path in sorted(root.glob("*/changes/*.yaml")):
        if path.name.endswith(".template.yaml"):
            continue
        if groups is not None and path.parent.parent.name not in groups:
            continue
        document = require_mapping(simple_yaml.load(path), str(path))
        info = require_mapping(document.get("changeSet"), f"{path}.changeSet")
        if info.get("module") != module:
            continue
        group = path.parent.parent.name
        result.setdefault(group, []).append(path)
    return result


def simulate(
    doc_root: Path, version: str, module: str, applied_ids: set[str] | None = None,
    pending_paths: Iterable[Path] | Mapping[Path, bytes] = (),
    groups: set[str] | None = None,
    require_links: bool = True,
) -> list[GroupSimulation]:
    applied_ids = applied_ids or set()
    simulations: list[GroupSimulation] = []
    for group, paths in discover_changesets(doc_root, version, module, groups=groups).items():
        if groups is not None and group not in groups:
            continue
        state = load_canonical(doc_root, group)
        records: list[dict[str, Any]] = []
        summaries: list[str] = []
        skipped: list[str] = []
        seen: set[str] = set()
        for path in paths:
            changeset = require_mapping(simple_yaml.load(path), str(path))
            info = validate_changeset(changeset, version, module, group)
            if info["id"] in seen:
                raise BusinessFlowError(f"duplicate business flow changeset ID: {info['id']}")
            seen.add(info["id"])
            if info["id"] in applied_ids:
                skipped.append(info["id"])
                continue
            info, current = apply_changeset(state, changeset, version, module, group)
            affected_flow_ids = sorted({str(op["targetId"]) for op in changeset["operations"]})
            records.append({
                "id": info["id"],
                "kind": "BUSINESS_FLOW",
                "version": version,
                "module": module,
                "flowGroup": group,
                "featureCode": group,
                "reason": info["reason"],
                "artifacts": [f"business_flow__{group}"],
                "source": str(path),
                "baseRevisions": {f"business_flow__{group}": info["baseRevision"]},
                "resultRevisions": {f"business_flow__{group}": info["resultRevision"]},
                "affectedTraceIds": sorted(set(info["affectedTraceIds"])),
                "affectedFlowIds": affected_flow_ids,
            })
            summaries.extend(current)
        if records:
            validate_document(state, doc_root, require_links=require_links, pending=pending_paths)
            simulations.append(GroupSimulation(group, state, records, summaries, skipped, paths))
    return simulations


def _anchor(value: str) -> str:
    return value.lower().replace("_", "-")


def relative_link(
    doc_root: Path, output_path: Path, root_ref: str, *, anchor: str | None = None, must_exist: bool = True
) -> str:
    target = _safe_root_ref(doc_root, root_ref, "document reference", must_exist=must_exist)
    relative = os.path.relpath(target, output_path.parent.resolve()).replace(os.sep, "/")
    return relative + (f"#{anchor}" if anchor else "")


def render_markdown(document: dict[str, Any], doc_root: Path, output_path: Path) -> str:
    group = document["flowGroup"]
    lines = [
        GENERATED_MARKER,
        f"# {group['name']}（{group['code']}）",
        "",
        f"- Revision：`{document['revision']}`",
        f"- Base Revision：`{document.get('baseRevision', '')}`",
        "- 层级：L0 端到端场景、L1 业务阶段、L2 关键子流程",
        "",
        "## 流程目录",
        "",
        "| 流程编号 | 流程名称 | 层级 | 类型 | 主责模块 | 参与模块 | 状态 |",
        "|---|---|---|---|---|---|---|",
    ]
    for flow in document["flows"]:
        lines.append(
            f"| [{flow['id']}](#{_anchor(flow['id'])}) | {flow['name']} | {flow['level']} | {flow['type']} | "
            f"{flow['ownerModule']} | {', '.join(flow['participantModules'])} | {flow['status']} |"
        )
    if not document["flows"]:
        lines.append("| - | - | - | - | - | - | - |")
    for flow in document["flows"]:
        lines.extend(["", f'<a id="{_anchor(flow["id"])}"></a>', f"## {flow['id']} {flow['name']}", ""])
        participant_links = []
        for module in flow["participantModules"]:
            module_ref = f"docs/module/{module}/{module}_desc.md"
            participant_links.append(
                f"[{module}]({relative_link(doc_root, output_path, module_ref, must_exist=False)})"
            )
        lines.extend([
            f"- 层级/类型：`{flow['level']}` / `{flow['type']}`",
            f"- 父流程：`{flow.get('parentFlowId', '无')}`",
            f"- 主责模块：`{flow['ownerModule']}`",
            f"- 参与模块：{', '.join(participant_links)}",
            f"- 目标：{flow['goal']}",
            f"- 触发：{flow['trigger']}",
            f"- 状态：`{flow['status']}`",
            "",
            "### 需求、功能与追踪",
            "",
            "| 类型 | 引用 |",
            "|---|---|",
        ])
        for ref in flow["requirementRefs"]:
            link = relative_link(doc_root, output_path, ref["documentRef"])
            lines.append(f"| 需求 | [{ref['id']}]({link}) |")
        for ref in flow["featureRefs"]:
            module_link = relative_link(
                doc_root, output_path, f"docs/module/{ref['module']}/{ref['module']}_desc.md", must_exist=False
            )
            lines.append(f"| 功能 | [{ref['id']}]({module_link})（{ref['module']}） |")
        lines.append(f"| 规则 | {', '.join(flow['ruleRefs']) or '无'} |")
        lines.append(f"| 验收 | {', '.join(flow['acceptanceRefs']) or '无'} |")
        lines.append(f"| 追踪 | {', '.join(flow['traceIds'])} |")
        lines.extend(["", "### 前置条件", ""])
        lines.extend([f"- {item}" for item in flow["preconditions"]] or ["- 无"])
        lines.extend(["", "### 主流程", "", "| 顺序 | 步骤编号 | 关键动作 | 主责模块 | 关联功能 | 子流程 |", "|---|---|---|---|---|---|"])
        for step in sorted(flow["steps"], key=lambda item: item["order"]):
            child = step.get("childFlowRef", "无")
            if child != "无":
                child = f"[{child}](#{_anchor(child)})"
            lines.append(
                f"| {step['order']} | {step['id']} | {step['name']} | {step['ownerModule']} | {step['featureRef']} | {child} |"
            )
        lines.extend(["", "### 变体", ""])
        if flow["variants"]:
            lines.extend(["| 变体编号 | 名称 | 适用条件 | 关键差异 |", "|---|---|---|---|"])
            for variant in flow["variants"]:
                lines.append(f"| {variant['id']} | {variant['name']} | {variant['condition']} | {'；'.join(variant['differences'])} |")
        else:
            lines.append("- 无")
        lines.extend(["", "### 失败、回退与补偿", ""])
        if flow["failurePaths"]:
            lines.extend(["| 路径编号 | 发生步骤 | 条件 | 结果 | 后续流程 | 补偿 | 阻塞 |", "|---|---|---|---|---|---|---|"])
            for failure in flow["failurePaths"]:
                followup = failure.get("followupFlowRef", "无")
                if followup != "无":
                    followup = f"[{followup}](#{_anchor(followup)})"
                lines.append(
                    f"| {failure['id']} | {failure['atStep']} | {failure['condition']} | {failure['result']} | "
                    f"{followup} | {failure.get('compensation', '无') or '无'} | {str(failure['blocking']).lower()} |"
                )
        else:
            lines.append("- 无")
        lines.extend(["", "### 成功标准", ""])
        lines.extend([f"- {item}" for item in flow["successCriteria"]])
        lines.extend(["", "### 下游映射", ""])
        lines.append(f"- 业务模型：{', '.join(flow.get('businessModelRefs', [])) or '待补充'}")
        lines.append(f"- 影响分析：{', '.join(flow.get('dependencyImpactRefs', [])) or '待补充'}")
        lines.append(f"- 技术设计：{', '.join(flow.get('designRefs', [])) or '待补充'}")
        lines.append(f"- 测试 Case：{', '.join(flow.get('testCaseIds', [])) or '待补充'}")
    return "\n".join(lines).rstrip() + "\n"


def build_index(doc_root: Path, overrides: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    overrides = overrides or {}
    documents: dict[str, dict[str, Any]] = {}
    root = doc_root / "docs" / "_flows"
    if root.is_dir():
        for path in root.glob("*_flow.yaml"):
            if path.name == "flow_index.yaml":
                continue
            doc = require_mapping(simple_yaml.load(path), str(path))
            validate_document(doc)
            documents[doc["flowGroup"]["code"]] = doc
    documents.update(overrides)
    groups: list[dict[str, Any]] = []
    flows: list[dict[str, Any]] = []
    for group in sorted(documents):
        document = documents[group]
        groups.append({
            "code": group,
            "name": document["flowGroup"]["name"],
            "revision": document["revision"],
            "documentRef": f"docs/_flows/{group}_flow.md",
        })
        for flow in document["flows"]:
            flows.append({
                "id": flow["id"], "name": flow["name"], "group": group, "level": flow["level"],
                "ownerModule": flow["ownerModule"], "participantModules": flow["participantModules"],
                "featureRefs": [item["id"] for item in flow["featureRefs"]], "status": flow["status"],
                "documentRef": f"docs/_flows/{group}_flow.md#{_anchor(flow['id'])}",
            })
    return {"schemaVersion": 1, "artifact": "business_flow_index", "groups": groups, "flows": flows}


def render_index(index: dict[str, Any], doc_root: Path, output_path: Path) -> str:
    lines = [GENERATED_MARKER, "# 业务流程索引", "", "| 流程组 | Revision | 文档 |", "|---|---|---|"]
    for group in index["groups"]:
        link = relative_link(doc_root, output_path, group["documentRef"], must_exist=False)
        lines.append(f"| {group['code']} {group['name']} | {group['revision']} | [查看]({link}) |")
    lines.extend(["", "## 流程", "", "| 流程编号 | 流程名称 | 流程组 | 层级 | 主责模块 | 参与模块 | 功能 | 状态 |", "|---|---|---|---|---|---|---|---|"])
    for flow in index["flows"]:
        link = relative_link(
            doc_root, output_path, flow["documentRef"].split("#", 1)[0],
            anchor=flow["documentRef"].split("#", 1)[1], must_exist=False
        )
        lines.append(
            f"| [{flow['id']}]({link}) | {flow['name']} | {flow['group']} | {flow['level']} | {flow['ownerModule']} | "
            f"{', '.join(flow['participantModules'])} | {', '.join(flow['featureRefs'])} | {flow['status']} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def simulation_plan_records(doc_root: Path, version: str, simulations: list[GroupSimulation]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for simulation in simulations:
        yaml_content = simple_yaml.dumps(simulation.document).encode("utf-8")
        markdown_content = render_markdown(simulation.document, doc_root, canonical_markdown_path(doc_root, simulation.group)).encode("utf-8")
        records.append({
            "flowGroup": simulation.group,
            "baseRevision": simulation.records[0]["baseRevisions"][f"business_flow__{simulation.group}"],
            "resultRevision": simulation.document["revision"],
            "changeSetIds": [item["id"] for item in simulation.records],
            "affectedFlowIds": sorted({flow_id for item in simulation.records for flow_id in item.get("affectedFlowIds", [])}),
            "sourceRefs": [Path(item["source"]).relative_to(doc_root).as_posix() for item in simulation.records],
            "targetYamlRef": canonical_yaml_path(doc_root, simulation.group).relative_to(doc_root).as_posix(),
            "targetMarkdownRef": canonical_markdown_path(doc_root, simulation.group).relative_to(doc_root).as_posix(),
            "previewYamlRef": (preview_root(doc_root, version, simulation.group) / f"{simulation.group}_flow.preview.yaml").relative_to(doc_root).as_posix(),
            "previewMarkdownRef": (preview_root(doc_root, version, simulation.group) / f"{simulation.group}_flow.preview.md").relative_to(doc_root).as_posix(),
            "yamlDigest": sha256_bytes(yaml_content),
            "markdownDigest": sha256_bytes(markdown_content),
        })
    return records


def write_previews(doc_root: Path, version: str, simulations: list[GroupSimulation]) -> list[dict[str, Any]]:
    plan_records = simulation_plan_records(doc_root, version, simulations)
    preview_files = []
    for simulation in simulations:
        root = preview_root(doc_root, version, simulation.group)
        preview_files.extend([
            (root / f"{simulation.group}_flow.preview.yaml", simple_yaml.dumps(simulation.document).encode("utf-8")),
            (root / f"{simulation.group}_flow.preview.md", render_markdown(
                simulation.document, doc_root, root / f"{simulation.group}_flow.preview.md"
            ).encode("utf-8")),
        ])
    write_preview_files(
        preview_files,
        path_for=lambda item: item[0],
        content_for=lambda item: item[1],
    )
    return plan_records


def payloads(
    doc_root: Path, simulations: list[GroupSimulation], base_payloads: dict[Path, bytes] | None = None
) -> dict[Path, bytes]:
    result: dict[Path, bytes] = {}
    base_payloads = base_payloads or {}
    overrides = {simulation.group: simulation.document for simulation in simulations}
    affected_flow_ids: set[str] = {
        flow_id
        for simulation in simulations
        for record in simulation.records
        for flow_id in record.get("affectedFlowIds", [])
    }
    module_rows: dict[str, list[list[str]]] = {}
    for simulation in simulations:
        yaml_path = canonical_yaml_path(doc_root, simulation.group)
        markdown_path = canonical_markdown_path(doc_root, simulation.group)
        result[yaml_path] = simple_yaml.dumps(simulation.document).encode("utf-8")
        result[markdown_path] = render_markdown(simulation.document, doc_root, markdown_path).encode("utf-8")
        for flow in simulation.document["flows"]:
            if flow["id"] not in affected_flow_ids:
                continue
            features_by_module: dict[str, list[str]] = {}
            for feature in flow["featureRefs"]:
                features_by_module.setdefault(feature["module"], []).append(feature["id"])
            for module in flow["participantModules"]:
                role = "OWNER" if module == flow["ownerModule"] else "PARTICIPANT"
                feature_ids = features_by_module.get(module, [])
                if not feature_ids:
                    continue
                link = f"[{flow['id']}](../_flows/{simulation.group}_flow.md#{_anchor(flow['id'])})"
                module_rows.setdefault(module, []).append([
                    flow["id"], flow["name"], role, ", ".join(feature_ids),
                    flow["level"], flow["status"], link,
                ])
    if simulations:
        index = build_index(doc_root, overrides)
        result[flow_index_yaml_path(doc_root)] = simple_yaml.dumps(index).encode("utf-8")
        result[flow_index_markdown_path(doc_root)] = render_index(index, doc_root, flow_index_markdown_path(doc_root)).encode("utf-8")

    candidate_desc_paths: dict[Path, Path] = {}
    for path in (doc_root / "docs").glob("*/*_desc.md"):
        if path.is_file():
            candidate_desc_paths.setdefault(path.resolve(), path)
    for module in module_rows:
        path = doc_root / "docs" / "module" / module / f"{module}_desc.md"
        candidate_desc_paths.setdefault(path.resolve(), path)
    resolved_docs_root = (doc_root / "docs").resolve()
    for path in base_payloads:
        if path.parent.parent.resolve() == resolved_docs_root and path.name.endswith("_desc.md"):
            candidate_desc_paths[path.resolve()] = path
    for desc_path in sorted(candidate_desc_paths.values()):
        module = desc_path.parent.name
        source_bytes = base_payloads.get(desc_path)
        if source_bytes is None and desc_path.is_file():
            source_bytes = desc_path.read_bytes()
        if source_bytes is None:
            raise BusinessFlowError(f"missing module description required by business flow: {desc_path}")
        source_text = source_bytes.decode("utf-8")
        rows = module_rows.get(module, [])
        if not rows and not any(flow_id in source_text for flow_id in affected_flow_ids):
            continue
        try:
            updated = document_facts.replace_project_flow_rows(
                source_text, module, sorted(affected_flow_ids), rows
            )
        except document_facts.DocumentFactError as exc:
            raise BusinessFlowError(str(exc)) from exc
        result[desc_path] = updated.encode("utf-8")
    versions = {
        str(flow.get("lastChangedVersion") or "")
        for simulation in simulations
        for flow in simulation.document.get("flows", [])
        if str(flow.get("lastChangedVersion") or "")
    }
    requirement_index = doc_root / "docs" / "requirement_list.md"
    requirement_target = requirement_index.resolve()
    if (
        all(Path(path).resolve() != requirement_target for path in result)
        and all(Path(path).resolve() != requirement_target for path in base_payloads)
        and not requirement_index.is_file()
    ):
        result[requirement_index] = document_facts.requirement_list_template("CURRENT", project=True).encode("utf-8")
    result.update(document_portal.build_payloads(
        doc_root,
        versions=versions,
        pending={**base_payloads, **result},
    ))
    validate_rendered_links(doc_root, result, pending=base_payloads)
    return result


def validate_rendered_links(
    doc_root: Path,
    payload_map: dict[Path, bytes],
    *,
    pending: dict[Path, bytes] | None = None,
) -> None:
    pending_paths = {path.resolve() for path in payload_map}
    pending_paths.update(path.resolve() for path in (pending or {}))
    pattern = re.compile(r"\[[^\]]+\]\(([^)#]+)(?:#[^)]+)?\)")
    for path, content in payload_map.items():
        if path.suffix.lower() != ".md":
            continue
        text = content.decode("utf-8")
        for match in pattern.finditer(text):
            link = match.group(1)
            if "://" in link or link.startswith("#"):
                continue
            target = (path.parent / link).resolve()
            try:
                target.relative_to(doc_root.resolve())
            except ValueError as exc:
                raise BusinessFlowError(f"generated Markdown link escapes DOC_ROOT: {path} -> {link}") from exc
            if not target.is_file() and target not in pending_paths:
                raise BusinessFlowError(f"generated Markdown link target does not exist: {path} -> {link}")


def command_init(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "business_flow", "init")
    doc_root = Path(args.doc_root)
    root = changes_root(doc_root, args.version, args.group)
    root.mkdir(parents=True, exist_ok=True)
    template = root / "business-flow.changeset.template.yaml"
    if not template.exists():
        simple_yaml.dump(empty_changeset(args.version, args.module, args.group, args.name or args.group), template)
        print(f"CREATED {template}")
    # Project-level business-flow facts are created only by wk -d archive.
    # init prepares a version changeset and must not mutate DOC_ROOT/docs/_flows.
    return 0


def command_validate(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "business_flow", "validate", declared=True)
    path = Path(args.file)
    document = require_mapping(simple_yaml.load(path), str(path))
    if document.get("artifact") == "business_flow_changeset":
        info = document["changeSet"]
        validate_changeset(document, info["version"], info["module"], info["flowGroup"])
    else:
        validate_document(document, Path(args.doc_root), require_links=args.require_links)
    print(f"VALID {path}")
    return 0


def command_render(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "business_flow", "render")
    doc_root = Path(args.doc_root)
    path = Path(args.file)
    document = require_mapping(simple_yaml.load(path), str(path))
    validate_document(document, doc_root, require_links=True)
    output = Path(args.output) if args.output else path.with_suffix(".md")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render_markdown(document, doc_root, output), encoding="utf-8")
    validate_rendered_links(doc_root, {output: output.read_bytes()})
    print(f"RENDERED {output}")
    return 0


def command_impact(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "business_flow", "impact")
    doc_root = Path(args.doc_root)
    index = build_index(doc_root)
    identifiers = set(args.ref or [])
    affected: list[dict[str, Any]] = []
    for group in index["groups"]:
        document = require_mapping(simple_yaml.load(doc_root / group["documentRef"].replace(".md", ".yaml")), group["code"])
        for flow in document["flows"]:
            values = {
                *[item["id"] for item in flow["requirementRefs"]],
                *[item["id"] for item in flow["featureRefs"]],
                *flow["ruleRefs"], *flow["acceptanceRefs"], *flow["traceIds"], flow["id"],
            }
            if identifiers.intersection(values):
                affected.append({"flowGroup": group["code"], "flowId": flow["id"], "level": flow["level"], "status": flow["status"]})
    result = {
        "affectedFlows": affected,
        "earliestResponsiblePhase": "requirement_analysis" if affected else "requirement_confirmation",
        "downstreamToRevalidate": [
            "business_model", "design", "test_design", "implementation_plan", "development",
            "code_review", "testing", "completion_verification",
        ] if affected else [],
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in affected:
            print(f"{item['flowGroup']} {item['flowId']} {item['level']} {item['status']}")
    return 0


def parser() -> argparse.ArgumentParser:
    result = build_common_parser(__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    add_agent_argument(init)
    add_doc_root_argument(init)
    init.add_argument("--version", required=True)
    init.add_argument("--module", required=True)
    init.add_argument("--group", required=True)
    init.add_argument("--name")
    set_handler(init, command_init)
    validate = sub.add_parser("validate")
    add_agent_argument(validate)
    add_doc_root_argument(validate)
    add_file_argument(validate)
    validate.add_argument("--require-links", action="store_true")
    set_handler(validate, command_validate)
    render = sub.add_parser("render")
    add_agent_argument(render)
    add_doc_root_argument(render)
    add_file_argument(render)
    render.add_argument("--output")
    set_handler(render, command_render)
    impact = sub.add_parser("impact")
    add_agent_argument(impact)
    add_doc_root_argument(impact)
    add_repeatable_argument(impact, "--ref")
    add_json_argument(impact)
    set_handler(impact, command_impact)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        doc_layout.validate_cli_namespace(args)
        return int(args.handler(args))
    except (BusinessFlowError, doc_layout.DocumentLayoutError, agent_context.AgentAuthorizationError, simple_yaml.YamlError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
