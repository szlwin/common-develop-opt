"""Markdown and Mermaid rendering for merged structured documents."""
from __future__ import annotations

import json
from typing import Any


GENERATED_MARKER = '<!-- generated-by: common-develop/merge_docs.py -->'

def md_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).replace("|", "\\|").replace("\n", "<br>")


def render_business_model(document: dict[str, Any]) -> str:
    lines = [
        GENERATED_MARKER,
        f"# {document['module']['name']}业务模型",
        "",
        f"- Revision：`{document['revision']}`",
        f"- Base Revision：`{document.get('baseRevision', '')}`",
        "",
    ]
    sections = (
        ("术语", "terms"),
        ("场景", "scenarios"),
        ("实体", "entities"),
        ("值对象", "valueObjects"),
        ("聚合", "aggregates"),
        ("不变量", "invariants"),
        ("状态机", "stateMachines"),
        ("领域服务", "services"),
        ("策略", "policies"),
        ("事件", "events"),
        ("业务错误", "businessErrors"),
    )
    for title, collection in sections:
        lines.extend([f"## {title}", ""])
        items = document[collection]
        if not items:
            lines.extend(["- 无", ""])
            continue
        lines.extend(["| 标识 | 名称/定义 | 内容 |", "|---|---|---|"])
        for item in items:
            identifier = item.get("id", item.get("traceId", ""))
            name = item.get("name", item.get("definition", item.get("statement", "")))
            lines.append(f"| {md_cell(identifier)} | {md_cell(name)} | {md_cell(item)} |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"









__all__ = ["md_cell", "render_business_model"]
