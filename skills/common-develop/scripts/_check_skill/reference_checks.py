"""Reference checks for the internal runtime."""
from __future__ import annotations
import argparse
import ast
import hashlib
import importlib.util
import json
import py_compile
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import simple_yaml
import agent_context
import semantic_audit
import reference_reader
import context_resolver
import contract_normalization
import design_doc
import case_doc
import cli_help
import merge_docs
import long_task as long_task_runtime
from _core import capability_packs, implementation_strategy
from _check_skill.context import *
from _check_skill.model import CheckResult
from _check_skill.io import load_json, load_yaml
from _check_skill.module_loader import *
from _check_skill.routing import *
from _check_skill.manifest import *

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None

from _check_skill.model import CheckResult
from _check_skill.routing import load_materialized_routing
def _numbered_level2_headings(path: Path) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    in_fence = False
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if re.match(r"^\s*(```|~~~)", line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^## (\d+)\.\s", line)
        if match:
            result.append((int(match.group(1)), line_no))
    return result


def _check_reference_consolidation(
    routing: dict[str, Any], source_map: dict[str, Any], result: CheckResult
) -> None:
    """Keep section-aware References complete and uniquely routed."""
    groups = {
        "orchestration-core": {
            "sections": ["3. Dispatch", "4. Independent Review", "5. Retry and blocked work", "6. Handoff and recovery", "7. Recording"],
            "sourceMapKey": "projectManagement",
        },
        "requirement-confirmation-core": {
            "sections": ["3. Authoritative Fact Location", "4. Stable ID and Revision", "5. Scope Lock and Change Control"],
            "sourceMapKey": "requirementConfirmation",
        },
        "archive-core": {
            "sections": ["3. Preview", "4. Apply", "5. Review Gate", "6. Reference Check", "7. Recovery"],
            "sourceMapKey": "archive",
        },
        "html-core": {
            "sections": [
                "3. Command Semantics", "4. Page Structure", "5. Accessibility",
                "6. Project Process HTML", "7. Test Case HTML", "8. Bug Report HTML",
            ],
            "sourceMapKey": "html",
        },
        "lifecycle-core": {
            "sections": ["通用生命周期", "阶段执行规则", "回退、STALE 与需求变更"],
            "sourceMapKey": "workflow",
            "sourceMapExpected": ["references/lifecycle-core.md", "references/work-mode-core.md", "references/workflow-verification.md"],
        },
        "long-task-core": {
            "sections": ["记录与状态", "Evidence、Review 与阶段结果", "Checkpoint、重开与恢复"],
            "sourceMapKey": "longTask",
        },
        "review-core": {
            "sections": ["Review 执行", "Finding 生命周期"],
            "sourceMapKey": "review",
            "sourceMapExpected": ["references/review-core.md", "references/review-result-contract.md", "references/review-evidence.md"],
        },
        "review-evidence": {
            "sections": ["Evidence 使用规范", "Criterion Evidence 与人工 Review"],
            "sourceMapKey": "review",
            "sourceMapExpected": ["references/review-core.md", "references/review-result-contract.md", "references/review-evidence.md"],
        },
        "documentation-core": {
            "sections": ["事实目录与稳定标识", "项目过程与工作记录", "需求、设计与测试事实", "文档阅读副本"],
            "sourceMapKey": "documentation",
            "sourceMapExpected": ["references/documentation-core.md", "references/archive-documentation.md"],
        },
    }
    catalog = routing.get("resourceCatalog") or {}
    source_groups = (((source_map.get("sources") or {}).get("granularContextRouting") or {})
                     if isinstance(source_map, dict) else {})
    paths: dict[str, list[str]] = {}
    for resource_id, definition in catalog.items():
        if not isinstance(definition, dict) or definition.get("aliasOf"):
            continue
        path = definition.get("path")
        if isinstance(path, str) and path.startswith("references/"):
            paths.setdefault(path, []).append(resource_id)
    for path, resource_ids in paths.items():
        if len(resource_ids) > 1:
            result.errors.append(f"Reference path is routed more than once: {path} ({sorted(resource_ids)})")

    for core_id, spec in groups.items():
        definition = catalog.get(core_id) or {}
        if definition.get("readStrategy") != "relevant_sections":
            result.errors.append(f"consolidated Reference {core_id} must use relevant_sections")
        try:
            titles = {item.get("title") for item in reference_reader.index_resource(core_id).get("sections", [])}
        except (OSError, ValueError) as exc:
            result.errors.append(f"consolidated Reference {core_id} cannot be indexed: {exc}")
            titles = set()
        missing_sections = [title for title in spec["sections"] if title not in titles]
        if missing_sections:
            result.errors.append(f"consolidated Reference {core_id} missing sections: {missing_sections}")
        expected_source = spec.get("sourceMapExpected", [f"references/{core_id}.md"])
        if source_groups.get(spec["sourceMapKey"]) != expected_source:
            result.errors.append(
                f"ai/source-map.json granularContextRouting.{spec['sourceMapKey']} must be {expected_source}"
            )


def _check_tdd_reference_decomposition(
    routing: dict[str, Any], source_map: dict[str, Any], result: CheckResult
) -> None:
    """Keep TDD guidance split by execution concern."""

    focused = {
        "tdd-core.md": ["wk -min", "缺少 Harness 不构成绕过理由", "纯重构不人为制造 RED"],
        "test-matrix-design.md": ["case_id", "FLOW/STEP", "CMI/CMSTEP"],
        "red-green-refactor.md": ["有效 RED", "BUG 回归至少证明", "最小正确业务行为"],
        "test-seam.md": ["Highest stable seam", "反射调用 private", "Test Double"],
        "test-persistence.md": ["READY", "SKIPPED", "independent_test_environment.py", "不得把 `SKIPPED` 表述为通过"],
        "test-data-isolation.md": ["禁止无条件清空业务表", "事务自动回滚", "独立数据编号"],
        "test-coverage-complexity.md": ["harness_env.md", "不能仅看报告百分比", "复杂度阈值"],
        "tdd-evidence.md": ["StageOutcome", "revision", "旧 GREEN 不能证明新 revision"],
        "tdd-role-boundary.md": ["实现者不能作为唯一测试证据 Reviewer", "TDD Gate 不替代"],
    }
    for filename, required_tokens in focused.items():
        path = REFERENCES / filename
        if not path.is_file():
            result.errors.append(f"missing focused TDD Reference: references/{filename}")
            continue
        text = path.read_text(encoding="utf-8")
        for token in required_tokens:
            if token not in text:
                result.errors.append(f"references/{filename} missing decomposed TDD rule: {token}")

    expected_tdd_sources = [
        "references/tdd-core.md",
        "references/test-matrix-design.md",
        "references/red-green-refactor.md",
        "references/test-seam.md",
        "references/test-persistence.md",
        "references/test-data-isolation.md",
        "references/test-coverage-complexity.md",
        "references/tdd-evidence.md",
        "references/tdd-role-boundary.md",
    ]
    source_groups = (((source_map.get("sources") or {}).get("granularContextRouting") or {})
                     if isinstance(source_map, dict) else {})
    if source_groups.get("tdd") != expected_tdd_sources:
        result.errors.append("ai/source-map.json granularContextRouting.tdd must enumerate focused TDD References")

    implementation_contract = (REFERENCES / "implementation-contract.md").read_text(encoding="utf-8")
    if "`tdd.md`" in implementation_contract:
        result.errors.append("implementation-contract.md must not use tdd.md as an active input authority")
    for token in ("`tdd-core`", "`test-matrix-design`"):
        if token not in implementation_contract:
            result.errors.append(f"implementation-contract.md missing focused TDD input {token}")


def check_ai_reference_reading(contract: dict[str, Any], result: CheckResult) -> None:
    script = ROOT / "scripts" / "reference_reader.py"
    tests = ROOT / "tests" / "test_reference_reader.py"
    if not script.is_file() or not tests.is_file():
        result.errors.append("selective Reference reader and regression tests are required")
        return

    routing = load_materialized_routing(result)
    if not isinstance(routing, dict):
        return
    catalog = routing.get("resourceCatalog")
    if not isinstance(catalog, dict):
        result.errors.append("context-routing.json resourceCatalog must be an object")
        return
    for resource_id, definition in catalog.items():
        if not isinstance(definition, dict):
            continue
        strategy = definition.get("readStrategy")
        try:
            index = reference_reader.index_resource(str(resource_id))
        except (OSError, ValueError) as exc:
            result.errors.append(f"Reference resource {resource_id} cannot be indexed: {exc}")
            continue
        if strategy == "relevant_sections" and not index.get("sections"):
            result.errors.append(
                f"Reference resource {resource_id} uses relevant_sections but has no level-2 sections"
            )

    permissions = contract.get("scriptPermissions", {}) if isinstance(contract, dict) else {}
    for key in ("reference_reader.index", "reference_reader.read"):
        if permissions.get(key) != {"mode": "DECLARED_AGENT"}:
            result.errors.append(f"runtime contract permission {key} must be DECLARED_AGENT")

    source_map = load_json(ROOT / "ai" / "source-map.json", result)
    if isinstance(source_map, dict):
        if source_map.get("authoritativeSources", {}).get("referenceReader") != "scripts/reference_reader.py":
            result.errors.append("ai/source-map.json must point referenceReader to scripts/reference_reader.py")

    _check_reference_consolidation(routing, source_map if isinstance(source_map, dict) else {}, result)
    _check_tdd_reference_decomposition(routing, source_map if isinstance(source_map, dict) else {}, result)

    active_docs = [ROOT / "SKILL.md", *sorted(REFERENCES.glob("*.md")), ROOT / "ai" / "README.md"]
    for path in active_docs:
        headings = _numbered_level2_headings(path)
        if headings:
            numbers = [item[0] for item in headings]
            expected = list(range(numbers[0], numbers[0] + len(numbers)))
            if numbers != expected:
                result.errors.append(
                    f"{path.relative_to(ROOT)} has non-contiguous level-2 numbering: {numbers}"
                )
        if path.parent == REFERENCES:
            text = path.read_text(encoding="utf-8")
            release_refs = re.findall(r"\bv\d+\.\d+\b", text)
            if release_refs:
                result.errors.append(
                    f"{path.relative_to(ROOT)} contains release-coupled prose {sorted(set(release_refs))}; use version-neutral rules"
                )
    documentation_core = (REFERENCES / "documentation-core.md").read_text(encoding="utf-8")
    for token in ("### 2.1 最小固定骨架", "### 2.2 按需事实产物", "### 2.3 生成产物与阅读副本", "不得把目录示例当成初始化清单"):
        if token not in documentation_core:
            result.errors.append(f"references/documentation-core.md missing AI-safe directory rule: {token}")

    task_layout = (ROOT / "scripts" / "task_layout.py").read_text(encoding="utf-8")
    if "current_manifest" not in task_layout:
        result.errors.append("task_layout.py must expose the canonical current manifest path")

    ai_context_paths = [ROOT / "scripts" / "ai_context.py", ROOT / "scripts" / "_core" / "ai_context_payload.py"]
    ai_context_source = "\n".join(path.read_text(encoding="utf-8") for path in ai_context_paths if path.is_file())
    for token in ("executionCore", "completionGateSummary", "architectureReviewContext", "IMPLEMENTATION_READY_FOR_REVIEW", "finalReviewPassed"):
        if token not in ai_context_source:
            result.errors.append(f"ai_context.py missing wk -ar dynamic context token: {token}")

    work_mode_script = (ROOT / "scripts" / "work_mode.py").read_text(encoding="utf-8")
    if "MODE_PATTERN" not in work_mode_script:
        result.errors.append("work_mode.py must define the canonical mode marker")
    work_record_script = (ROOT / "scripts" / "work_record.py").read_text(encoding="utf-8")
    if "V5_MARKER" not in work_record_script:
        result.errors.append("work_record.py must define the canonical work-record marker")
    if not result.errors:
        result.ok("AI Reference reading, heading structure, and on-demand document layout are deterministic")
