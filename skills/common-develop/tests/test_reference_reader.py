#!/usr/bin/env python3
"""Regression tests for selective routed Reference reading."""
from __future__ import annotations

import unittest

import reference_reader


class ReferenceReaderTests(unittest.TestCase):
    def test_index_ignores_headings_inside_code_fences(self) -> None:
        result = reference_reader.index_resource("business-model")
        titles = [item["title"] for item in result["sections"]]
        self.assertIn("1. 目标", titles)
        self.assertNotIn("复制 changes/changeset.template.yaml 为真实 *.yaml changeset，并补全 revision、操作和追踪 ID", titles)


    def test_index_exposes_level_three_subsections_for_large_references(self) -> None:
        result = reference_reader.index_resource("implementation-plan")
        sections = {item["title"]: item for item in result["sections"]}
        self.assertIn("12.1 覆盖", sections)
        self.assertEqual(3, sections["12.1 覆盖"]["level"])
        selected = reference_reader.read_resource(
            "implementation-plan", section_ids=[sections["12.1 覆盖"]["id"]]
        )
        self.assertIn("### 12.1 覆盖", selected["content"])
        self.assertNotIn("### 12.2", selected["content"])

    def test_consolidated_core_references_are_section_addressable(self) -> None:
        expected = {
            "orchestration-core": {"3. Dispatch", "4. Independent Review", "6. Handoff and recovery"},
            "requirement-confirmation-core": {"3. Authoritative Fact Location", "4. Stable ID and Revision", "5. Scope Lock and Change Control"},
            "archive-core": {"3. Preview", "4. Apply", "7. Recovery"},
            "html-core": {"3. Command Semantics", "5. Accessibility", "8. Bug Report HTML"},
            "lifecycle-core": {"通用生命周期", "阶段执行规则", "回退、STALE 与需求变更"},
            "long-task-core": {"记录与状态", "Evidence、Review 与阶段结果", "Checkpoint、重开与恢复"},
            "review-core": {"Review 执行", "Finding 生命周期"},
            "review-evidence": {"Evidence 使用规范", "Criterion Evidence 与人工 Review"},
            "documentation-core": {"事实目录与稳定标识", "项目过程与工作记录", "文档阅读副本"},
        }
        for resource_id, titles in expected.items():
            with self.subTest(resource=resource_id):
                index = reference_reader.index_resource(resource_id)
                self.assertEqual("relevant_sections", index["readStrategy"])
                actual = {item["title"] for item in index["sections"]}
                self.assertTrue(titles.issubset(actual))
                with self.assertRaisesRegex(reference_reader.ReferenceReadError, "provide --section"):
                    reference_reader.read_resource(resource_id)

    def test_relevant_reference_requires_selection(self) -> None:
        with self.assertRaisesRegex(reference_reader.ReferenceReadError, "provide --section"):
            reference_reader.read_resource("lifecycle-core")

    def test_read_by_stable_section_id(self) -> None:
        index = reference_reader.index_resource("lifecycle-core")
        section_id = next(item["id"] for item in index["sections"] if item["title"] == "通用阶段协议")
        result = reference_reader.read_resource("lifecycle-core", section_ids=[section_id])
        self.assertEqual([section_id], result["selectedSectionIds"])
        self.assertIn("## 通用阶段协议", result["content"])
        self.assertNotIn("## `wk -d`", result["content"])

    def test_query_selects_matching_sections_only(self) -> None:
        result = reference_reader.read_resource("lifecycle-core", query="Code Review", max_sections=2)
        self.assertGreaterEqual(len(result["selectedSectionIds"]), 1)
        self.assertLessEqual(len(result["selectedSectionIds"]), 2)
        self.assertIn("Code Review", result["content"])

    def test_full_strategy_defaults_to_full_document(self) -> None:
        result = reference_reader.read_resource("scope")
        self.assertIn("# common-develop 范围边界", result["content"])
        self.assertIn("## 5. 范围门禁输出", result["content"])

    def test_alias_resource_defaults_to_its_compact_section(self) -> None:
        result = reference_reader.read_resource("acceptance-summary")
        self.assertEqual(["acceptance-quick-rules"], result["selectedSectionIds"])
        self.assertIn("每个验收项必须有稳定 ID", result["content"])
        self.assertNotIn("Acceptance Assertions", result["content"])

    def test_unknown_resource_is_rejected(self) -> None:
        with self.assertRaisesRegex(reference_reader.ReferenceReadError, "unknown routed resource"):
            reference_reader.index_resource("missing-resource")


if __name__ == "__main__":
    unittest.main()
