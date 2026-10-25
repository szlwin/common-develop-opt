#!/usr/bin/env python3
"""Regression coverage for P3/P4/P6-A document semantics."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from document_core import document_facts, enum_contract, enum_validation, simple_yaml, union_language


class DocumentSemanticContractTests(unittest.TestCase):
    version = "V_2.0"
    module = "CAT"

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project_doc"
        (self.root / "version" / self.version / "requirement").mkdir(parents=True)
        (self.root / "version" / self.version / "module" / self.module).mkdir(parents=True)
        (self.root / "version" / self.version / "union").mkdir(parents=True)
        (self.root / "docs" / "module" / self.module).mkdir(parents=True)
        (self.root / "docs" / "union").mkdir(parents=True)

        (self.root / "version" / self.version / "requirement" / "requirement_list.md").write_text(
            document_facts.requirement_list_template(self.version, project=False), encoding="utf-8"
        )
        (self.root / "docs" / "requirement_list.md").write_text(
            document_facts.requirement_list_template(self.version, project=True), encoding="utf-8"
        )
        (self.root / "docs" / "module" / self.module / f"{self.module}_desc.md").write_text(
            document_facts.module_desc_template(self.module, self.version, project=True), encoding="utf-8"
        )
        union_language.project_path(self.root).write_text(union_language.render_project([]), encoding="utf-8")
        union_language.changes_path(self.root, self.version).write_text(
            union_language.render_changes(self.version, []), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def enum_def(self, *, value: str = "1", change: str = "ADD") -> enum_contract.EnumDefinition:
        return enum_contract.EnumDefinition(
            "ENUM-CAT-PRODUCT-STATUS",
            "商品状态",
            {
                "枚举编码": "CAT.PRODUCT_STATUS",
                "名称": "商品状态",
                "枚举分类": "状态",
                "值类型": "INTEGER",
                "说明": "表示商品当前状态",
                "状态": "ACTIVE",
                "变更类型": change,
                "变更说明": "P3 regression",
            },
            [["ACTIVE", value, "启用", "商品可用", "ACTIVE", self.version, "-"]],
        )

    @staticmethod
    def with_enums(text: str, items: list[enum_contract.EnumDefinition], *, project: bool) -> str:
        lines = text.splitlines()
        start = lines.index("## 枚举")
        end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("## "))
        replacement = enum_contract.render_enum_section(items, project=project)
        return "\n".join([*lines[:start], *replacement, *lines[end:]]).rstrip() + "\n"

    def write_source_desc(self, *, value: str = "1", term: str = "") -> Path:
        path = self.root / "version" / self.version / "module" / self.module / f"{self.module}_desc.md"
        text = document_facts.module_desc_template(self.module, self.version, project=False)
        text = self.with_enums(text, [self.enum_def(value=value)], project=False)
        if term:
            text = text.replace("| 模块说明 | 待补充 |", f"| 模块说明 | 管理[{term}]目录 |")
        path.write_text(text, encoding="utf-8")
        return path

    def test_enum_reference_and_semantic_archive_projection(self) -> None:
        self.write_source_desc()
        design = self.root / "version" / self.version / "module" / self.module / "CAT_design.md"
        design.write_text("订单状态使用 `CAT.PRODUCT_STATUS.ACTIVE`。\n", encoding="utf-8")
        checked = enum_validation.validate_version(self.root, self.version)
        self.assertEqual("FAILED", checked["status"])
        self.assertTrue(any("without a canonical enum link" in item for item in checked["errors"]), checked)
        design.write_text(
            "订单状态使用 [`CAT.PRODUCT_STATUS`](CAT_desc.md#enum-cat-product-status) 的 `ACTIVE` 值。\n",
            encoding="utf-8",
        )
        change_dir = self.root / "version" / self.version / "module" / self.module / "changes"
        change_dir.mkdir()
        ref_file = change_dir / "enum-ref.yaml"
        simple_yaml.dump({
            "endpoint": {
                "enumRefs": [{
                    "usedBy": "request.status",
                    "enumRef": "CAT.PRODUCT_STATUS",
                    "enumLink": "../CAT_desc.md#enum-cat-product-status",
                }]
            }
        }, ref_file)
        checked = enum_validation.validate_version(self.root, self.version)
        self.assertEqual("PASSED", checked["status"], checked)

        records = document_facts.simulate_merges(self.root, self.version, self.module)
        module_record = next(item for item in records if item["artifactType"] == "module_desc")
        merged = bytes(module_record["resultContent"]).decode("utf-8")
        self.assertIn("CAT.PRODUCT_STATUS", merged)
        self.assertIn("enum-cat-product-status", merged)
        self.assertNotIn("| 变更类型 |", merged)

        payload = simple_yaml.load(ref_file)
        payload["endpoint"]["enumRefs"][0]["enumLink"] = "../CAT_desc.md#wrong-anchor"
        simple_yaml.dump(payload, ref_file)
        checked = enum_validation.validate_version(self.root, self.version)
        self.assertEqual("FAILED", checked["status"])
        self.assertTrue(any("anchor mismatch" in item for item in checked["errors"]), checked)

    def test_published_enum_value_change_blocks_semantic_merge(self) -> None:
        project_path = self.root / "docs" / "module" / self.module / f"{self.module}_desc.md"
        project_text = self.with_enums(project_path.read_text(encoding="utf-8"), [self.enum_def(value="1", change="CURRENT")], project=True)
        project_path.write_text(project_text, encoding="utf-8")
        source = self.write_source_desc(value="2")
        source_text = source.read_text(encoding="utf-8").replace("| 变更类型 | ADD |", "| 变更类型 | MODIFY |")
        source.write_text(source_text, encoding="utf-8")
        with self.assertRaisesRegex(document_facts.DocumentFactError, "ENUM_ARCHIVE_CONFLICT"):
            document_facts.simulate_merges(self.root, self.version, self.module)

    def test_union_language_delta_is_merged_and_bare_term_must_resolve(self) -> None:
        self.write_source_desc(term="商品")
        term = union_language.Term(
            "UL-CAT-001",
            "商品",
            {
                "统一名称": "商品",
                "英文/代码名称": "Product",
                "领域": "商品",
                "Owner": "CAT",
                "别名": "-",
                "首次引入": self.version,
                "状态": "ACTIVE",
                "变更类型": "ADD",
                "变更原因": "建立项目统一命名",
                "替代术语": "-",
            },
            "可被目录管理并对外提供的商品对象。",
            "这里只定义跨模块共享的商品概念。",
            ["不要用商品表示库存实例。"],
            [],
            ["查询商品目录。"],
        )
        union_language.changes_path(self.root, self.version).write_text(
            union_language.render_changes(self.version, [term]), encoding="utf-8"
        )
        records = document_facts.simulate_merges(self.root, self.version, self.module)
        union_record = next(item for item in records if item["artifactType"] == "union_language")
        merged = bytes(union_record["resultContent"]).decode("utf-8")
        self.assertIn("UL-CAT-001", merged)
        self.assertIn("商品", merged)

        source = self.root / "version" / self.version / "module" / self.module / f"{self.module}_desc.md"
        source.write_text(source.read_text(encoding="utf-8").replace("管理[商品]目录", "管理[未知术语]目录"), encoding="utf-8")
        with self.assertRaisesRegex(document_facts.DocumentFactError, "UNION_LANGUAGE_REFERENCE_ERROR"):
            document_facts.simulate_merges(self.root, self.version, self.module)

    def test_union_language_conflict_is_fail_closed(self) -> None:
        base = union_language.Term(
            "UL-CAT-001", "商品",
            {"统一名称":"商品","英文/代码名称":"Product","领域":"商品","Owner":"CAT","别名":"-","首次引入":"V_1.0","状态":"ACTIVE"},
            "商品定义。", "商品边界。", [], [], []
        )
        conflict = union_language.Term(
            "UL-ORD-001", "商品",
            {"统一名称":"商品","英文/代码名称":"Offering","领域":"订单","Owner":"ORD","别名":"-","首次引入":self.version,"状态":"ACTIVE","变更类型":"ADD","变更原因":"冲突测试"},
            "另一个商品定义。", "另一个边界。", [], [], []
        )
        with self.assertRaisesRegex(union_language.UnionLanguageError, "UNION_LANGUAGE_CONFLICT"):
            union_language.merge_terms([conflict], [base])

    def test_new_document_cannot_keep_reference_to_deprecated_term(self) -> None:
        deprecated = union_language.Term(
            stable_id="UL-OLD", heading_name="旧术语",
            properties={"统一名称": "旧术语", "英文/代码名称": "OldTerm", "领域": "订单",
                        "Owner": "ORD", "首次引入": "V_1.0", "状态": "DEPRECATED",
                        "替代术语": "新术语"},
            definition="已由新术语替代", boundary="", avoid=[], related=[], examples=[])
        document = self.root / "version" / self.version / "requirement" / "example.md"
        document.parent.mkdir(parents=True, exist_ok=True)
        document.write_text("当前规则仍使用[旧术语]。", encoding="utf-8")
        self.assertTrue(union_language.validate_references([document], [deprecated]))

    def test_project_baseline_terms_are_checked_before_archive(self) -> None:
        base = union_language.Term(
            "UL-CAT-001", "商品",
            {"统一名称": "商品", "英文/代码名称": "Product", "领域": "商品", "Owner": "CAT",
             "别名": "-", "首次引入": "V_1.0", "状态": "ACTIVE"},
            "商品定义。", "商品边界。", [], [], [],
        )
        union_language.project_path(self.root).write_text(
            union_language.render_project([base]), encoding="utf-8"
        )
        baseline_doc = self.root / "docs" / "module" / self.module / "baseline.md"
        baseline_doc.write_text("当前规则使用[未登记术语]。\n", encoding="utf-8")
        with self.assertRaisesRegex(document_facts.DocumentFactError, "UNION_LANGUAGE_REFERENCE_ERROR"):
            self.write_source_desc()
            document_facts.simulate_merges(self.root, self.version, self.module)

    def test_generated_markdown_rejects_boilerplate(self) -> None:
        source = self.write_source_desc()
        source.write_text(
            source.read_text(encoding="utf-8").replace(
                "| 模块说明 | 待补充 |",
                "| 模块说明 | 通过上述设计，可以有效提升系统的可维护性。 |",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(document_facts.DocumentFactError, "DOCUMENT_STYLE_ERROR"):
            document_facts.simulate_merges(self.root, self.version, self.module)


if __name__ == "__main__":
    unittest.main()
