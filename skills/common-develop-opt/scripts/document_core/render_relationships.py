#!/usr/bin/env python3
"""Render dependency-impact YAML into Markdown and Mermaid diagrams."""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path
from typing import Any

_SCRIPT_ROOT = Path(__file__).resolve().parents[1]
_COMPAT_ROOT = _SCRIPT_ROOT / "compat"
if str(_COMPAT_ROOT) not in sys.path:
    sys.path.insert(0, str(_COMPAT_ROOT))

from document_core import simple_yaml
from document_core import agent_context
from runtime import artifact_relations
from runtime._core.validation import make_require_helpers


class RelationshipError(ValueError):
    """Raised when the relationship artifact is internally inconsistent."""


ACYCLIC_RELATIONSHIP_TYPES = {"REFINES", "DEPENDS_ON", "PRECONDITION_OF", "SUPERSEDES"}
REQUIREMENT_NODE_TYPES = {"REQUIREMENT", "BUSINESS_RULE", "ACCEPTANCE_CRITERION"}
SOLUTION_NODE_TYPES = {
    "FEATURE", "MODULE", "PROCESS", "DATA_OBJECT", "API", "EVENT", "TEST_CASE",
    "SYSTEM", "DOMAIN", "BOUNDED_CONTEXT", "TERM", "ENTITY", "VALUE_OBJECT",
    "AGGREGATE", "INVARIANT", "STATE_MACHINE", "DOMAIN_SERVICE", "COMPONENT",
}


def detect_cycle(edges: dict[str, set[str]]) -> list[str]:
    """Return one directed cycle, or an empty list when the graph is acyclic."""
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def walk(node: str) -> list[str]:
        if node in visiting:
            start = stack.index(node)
            return stack[start:] + [node]
        if node in visited:
            return []
        visiting.add(node)
        stack.append(node)
        for target in sorted(edges.get(node, set())):
            cycle = walk(target)
            if cycle:
                return cycle
        stack.pop()
        visiting.remove(node)
        visited.add(node)
        return []

    for node in sorted(edges):
        cycle = walk(node)
        if cycle:
            return cycle
    return []


require_mapping, require_list, require_text = make_require_helpers(
    RelationshipError,
    mapping_requirement="an object",
    list_requirement="an array",
    text_requirement="non-empty text",
)


def unique_objects(items: list[Any], name: str) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(items):
        item = require_mapping(raw, f"{name}[{index}]")
        item_id = require_text(item.get("id"), f"{name}[{index}].id")
        if item_id in result:
            raise RelationshipError(f"duplicate {name} id: {item_id}")
        result[item_id] = item
    return result


def validate_artifact(document: dict[str, Any]) -> None:
    if document.get("artifact") != "dependency_impact":
        raise RelationshipError("artifact must be dependency_impact")
    nodes = unique_objects(require_list(document.get("nodes"), "nodes"), "nodes")
    allowed_node_types = REQUIREMENT_NODE_TYPES | SOLUTION_NODE_TYPES
    for node_id, node in nodes.items():
        node_type = require_text(node.get("type"), f"nodes.{node_id}.type")
        if node_type not in allowed_node_types:
            raise RelationshipError(f"node {node_id} has unsupported type {node_type}")
    relationships = unique_objects(
        require_list(document.get("relationships"), "relationships"), "relationships"
    )
    policies = unique_objects(
        require_list(document.get("impactPolicies"), "impactPolicies"), "impactPolicies"
    )
    implementations = unique_objects(
        require_list(document.get("crossModuleImplementations"), "crossModuleImplementations"), "crossModuleImplementations"
    )

    semantic_edges: set[tuple[str, str, str, str]] = set()
    acyclic_edges: dict[str, set[str]] = {}
    for relationship_id, relationship in relationships.items():
        refs: dict[str, str] = {}
        for key in ("from", "to"):
            node_id = require_text(relationship.get(key), f"relationships.{relationship_id}.{key}")
            refs[key] = node_id
            if node_id not in nodes:
                raise RelationshipError(
                    f"relationship {relationship_id} references unknown node {node_id}"
                )
        relation_type = require_text(relationship.get("type"), f"relationships.{relationship_id}.type")
        direction = require_text(
            relationship.get("direction"), f"relationships.{relationship_id}.direction"
        )
        semantic_key = (refs["from"], refs["to"], relation_type, direction)
        if semantic_key in semantic_edges:
            raise RelationshipError(
                f"duplicate semantic relationship: {refs['from']} {relation_type} {refs['to']}"
            )
        semantic_edges.add(semantic_key)
        if relation_type in ACYCLIC_RELATIONSHIP_TYPES:
            acyclic_edges.setdefault(refs["from"], set()).add(refs["to"])
            if direction == "BIDIRECTIONAL":
                acyclic_edges.setdefault(refs["to"], set()).add(refs["from"])

    cycle = detect_cycle(acyclic_edges)
    if cycle:
        raise RelationshipError(f"dependency relationship cycle: {' -> '.join(cycle)}")

    for policy_id, policy in policies.items():
        source_ref = require_text(policy.get("sourceRef"), f"impactPolicies.{policy_id}.sourceRef")
        if source_ref not in nodes:
            raise RelationshipError(f"impact policy {policy_id} references unknown source {source_ref}")
        targets = require_list(policy.get("targetRefs"), f"impactPolicies.{policy_id}.targetRefs")
        if not targets:
            raise RelationshipError(f"impact policy {policy_id} targetRefs must be non-empty")
        for target_ref in targets:
            target = require_text(target_ref, f"impactPolicies.{policy_id}.targetRefs[]")
            if target not in nodes:
                raise RelationshipError(f"impact policy {policy_id} references unknown target {target}")

    for implementation_id, implementation in implementations.items():
        participants = unique_objects(
            require_list(implementation.get("participants"), f"crossModuleImplementations.{implementation_id}.participants"),
            f"crossModuleImplementations.{implementation_id}.participants",
        )
        if len(participants) < 2:
            raise RelationshipError(f"cross-module implementation {implementation_id} must have at least two participants")
        steps = unique_objects(
            require_list(implementation.get("steps"), f"crossModuleImplementations.{implementation_id}.steps"),
            f"crossModuleImplementations.{implementation_id}.steps",
        )
        orders: set[int] = set()
        for step_id, step in steps.items():
            order = step.get("order")
            if not isinstance(order, int) or isinstance(order, bool) or order < 1:
                raise RelationshipError(f"implementation step {step_id}.order must be a positive integer")
            if order in orders:
                raise RelationshipError(f"cross-module implementation {implementation_id} has duplicate step order {order}")
            orders.add(order)
            for key in ("from", "to"):
                participant_id = require_text(step.get(key), f"implementation step {step_id}.{key}")
                if participant_id not in participants:
                    raise RelationshipError(
                        f"implementation step {step_id} references unknown participant {participant_id}"
                    )
        expected_orders = set(range(1, len(steps) + 1))
        if orders != expected_orders:
            raise RelationshipError(
                f"cross-module implementation {implementation_id} step orders must be contiguous from 1: "
                f"actual={sorted(orders)}"
            )
        for index, raw_failure in enumerate(
            require_list(implementation.get("failurePaths"), f"crossModuleImplementations.{implementation_id}.failurePaths")
        ):
            failure = require_mapping(raw_failure, f"crossModuleImplementations.{implementation_id}.failurePaths[{index}]")
            at_step = require_text(
                failure.get("atStep"), f"crossModuleImplementations.{implementation_id}.failurePaths[{index}].atStep"
            )
            if at_step not in steps:
                raise RelationshipError(
                    f"cross-module implementation {implementation_id} failure path references unknown step {at_step}"
                )


def mermaid_text(value: Any) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ").replace('"', "'")
    return re.sub(r"\s+", " ", text).strip()


def mermaid_node_id(prefix: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def md_cell(value: Any) -> str:
    if isinstance(value, list):
        value = "<br>".join(str(item) for item in value)
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def render_graph_view(
    title: str,
    document: dict[str, Any],
    relationships: list[dict[str, Any]],
) -> list[str]:
    node_by_id = {node["id"]: node for node in document["nodes"]}
    included_ids = {
        node_id
        for relationship in relationships
        for node_id in (relationship["from"], relationship["to"])
    }
    lines = [f"## {title}", ""]
    if not relationships:
        lines.extend(["- 本 revision 无适用关系。", ""])
        return lines
    node_ids = {node_id: mermaid_node_id("N", node_id) for node_id in sorted(included_ids)}
    lines.extend(["```mermaid", "flowchart LR"])
    for node_id in sorted(included_ids):
        node = node_by_id[node_id]
        label = mermaid_text(f"{node['id']}<br/>{node['name']}<br/>{node['type']}")
        lines.append(f'  {node_ids[node_id]}["{label}"]')
    for relationship in relationships:
        arrow = "<-->" if relationship.get("direction") == "BIDIRECTIONAL" else "-->"
        label = mermaid_text(relationship["type"])
        lines.append(
            f'  {node_ids[relationship["from"]]} {arrow}|"{label}"| {node_ids[relationship["to"]]}'
        )
    lines.extend(["```", ""])
    return lines


def render_relationship_views(document: dict[str, Any]) -> list[str]:
    node_types = {node["id"]: node["type"] for node in document["nodes"]}
    requirement_edges: list[dict[str, Any]] = []
    trace_edges: list[dict[str, Any]] = []
    function_edges: list[dict[str, Any]] = []
    for relationship in document["relationships"]:
        from_type = node_types[relationship["from"]]
        to_type = node_types[relationship["to"]]
        from_requirement = from_type in REQUIREMENT_NODE_TYPES
        to_requirement = to_type in REQUIREMENT_NODE_TYPES
        if from_requirement and to_requirement:
            requirement_edges.append(relationship)
        elif from_requirement != to_requirement:
            trace_edges.append(relationship)
        else:
            function_edges.append(relationship)

    lines: list[str] = []
    lines.extend(render_graph_view("需求—需求关系图", document, requirement_edges))
    lines.extend(render_graph_view("需求—功能追踪图", document, trace_edges))
    lines.extend(render_graph_view("功能—功能影响图", document, function_edges))
    return lines


def render_relationship_table(document: dict[str, Any]) -> list[str]:
    lines = [
        "## 关系明细",
        "",
        "| ID | From | 类型 | To | 条件 | 理由 | Trace IDs |",
        "|---|---|---|---|---|---|---|",
    ]
    for item in document["relationships"]:
        lines.append(
            "| {id} | {from_} | {type_} | {to} | {conditions} | {rationale} | {trace} |".format(
                id=md_cell(item["id"]),
                from_=md_cell(item["from"]),
                type_=md_cell(item["type"]),
                to=md_cell(item["to"]),
                conditions=md_cell(item.get("conditions", [])),
                rationale=md_cell(item["rationale"]),
                trace=md_cell(item.get("traceIds", [])),
            )
        )
    if not document["relationships"]:
        lines.append("| - | - | - | - | - | - | - |")
    lines.append("")
    return lines


def render_impact_policies(document: dict[str, Any]) -> list[str]:
    lines = [
        "## 关联对象处置策略",
        "",
        "| ID | 来源动作 | 目标 | 策略 | 条件 | 一致性 | 失败/补偿 | Case |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for item in document["impactPolicies"]:
        failure = item["failureHandling"]
        if item.get("compensation"):
            failure = f"{failure}; 补偿: {item['compensation']}"
        lines.append(
            "| {id} | {source}.{action} | {targets} | {policy} | {conditions} | {consistency} | {failure} | {cases} |".format(
                id=md_cell(item["id"]),
                source=md_cell(item["sourceRef"]),
                action=md_cell(item["sourceAction"]),
                targets=md_cell(item["targetRefs"]),
                policy=md_cell(item["policy"]),
                conditions=md_cell(item["conditions"]),
                consistency=md_cell(item["consistency"]),
                failure=md_cell(failure),
                cases=md_cell(item["testCaseIds"]),
            )
        )
    if not document["impactPolicies"]:
        lines.append("| - | - | - | - | - | - | - | - |")
    lines.append("")
    return lines


def render_implementation(implementation: dict[str, Any]) -> list[str]:
    participant_ids = {
        participant["id"]: mermaid_node_id("P", participant["id"])
        for participant in implementation["participants"]
    }
    lines = [f"## {implementation['id']} {implementation['name']}", "", f"- 触发：{implementation['trigger']}", "", "```mermaid", "sequenceDiagram"]
    for participant in implementation["participants"]:
        label = mermaid_text(f"{participant['module']}: {participant['responsibility']}")
        lines.append(f'  participant {participant_ids[participant["id"]]} as {label}')
    for step in sorted(implementation["steps"], key=lambda item: item["order"]):
        arrow = "-->>" if step["interactionType"] in {"ASYNC_EVENT", "CALLBACK"} else "->>"
        label_parts = [step["id"], step["action"]]
        if step.get("contractRef"):
            label_parts.append(step["contractRef"])
        label = mermaid_text(" / ".join(label_parts))
        lines.append(
            f'  {participant_ids[step["from"]]}{arrow}{participant_ids[step["to"]]}: {label}'
        )
        if step.get("failureHandling"):
            note = mermaid_text(f"失败: {step['failureHandling']}")
            lines.append(f'  Note over {participant_ids[step["from"]]},{participant_ids[step["to"]]}: {note}')
    lines.extend(["```", "", "### 成功条件", ""])
    lines.extend(f"- {item}" for item in implementation["successCriteria"])
    lines.extend(["", "### 失败与补偿", ""])
    if implementation["failurePaths"]:
        for failure in implementation["failurePaths"]:
            lines.append(
                f"- `{failure['id']}` @ `{failure['atStep']}`：{failure['condition']} → {failure['result']}；"
                f"补偿：{', '.join(failure['compensationSteps']) or '无'}；人工恢复：{failure.get('manualRecovery', '') or '无'}"
            )
    else:
        lines.append("- 无")
    lines.append("")
    return lines


def render(document: dict[str, Any], *, project_navigation: bool = False) -> str:
    validate_artifact(document)
    scope = document["scope"]
    lines = [
        "<!-- GENERATED by scripts/render_relationships.py; edit dependency_impact.yaml instead. -->",
        "# 需求关联、影响策略与跨模块实现映射",
        "",
    ]
    if project_navigation:
        lines.extend([
            "> 导航：[项目文档首页](../README.md) · [全量追踪矩阵](traceability_matrix.md) · "
            "[研发事实依赖图](dependency_graph.md)",
            "",
        ])
    lines.extend([
        f"- Project：`{scope['project']}`",
        f"- Version：`{scope['version']}`",
        f"- Revision：`{document['revision']}`",
        "",
    ])
    lines.extend(render_relationship_views(document))
    lines.extend(render_relationship_table(document))
    lines.extend(render_impact_policies(document))
    lines.extend(["# 跨模块实现映射", ""])
    for implementation in document["crossModuleImplementations"]:
        lines.extend(render_implementation(implementation))
    if not document["crossModuleImplementations"]:
        lines.extend(["- 无", ""])
    return "\n".join(lines).rstrip() + "\n"


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    agent_context.add_agent_argument(result)
    result.add_argument("--input", required=True, help="dependency_impact.yaml path")
    result.add_argument("--output", help="generated Markdown path; required unless --check")
    result.add_argument("--check", action="store_true", help="validate only; do not write output")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        agent = agent_context.selected_agent(args)
        operation = "check" if args.check else "generate"
        agent_context.authorize_static(agent, "render_relationships", operation)
        input_path = Path(args.input)
        document = require_mapping(simple_yaml.load(input_path), args.input)
        if document.get("artifact") == "artifact_relations":
            output = Path(args.output) if args.output else input_path.with_name("dependency_graph.md")
            doc_root = input_path.parent.parent.parent
            artifact_relations.validate_document(document, doc_root)
            rendered = artifact_relations.render_dependency(document, doc_root, output)
        else:
            rendered = render(document)
        if not args.check:
            if not args.output:
                raise RelationshipError("--output is required unless --check is used")
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(rendered, encoding="utf-8")
            print(f"GENERATED agent={agent} {output}")
        else:
            print(f"VALID agent={agent} dependency-impact artifact")
        return 0
    except (agent_context.AgentAuthorizationError, OSError, simple_yaml.YamlError, RelationshipError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
