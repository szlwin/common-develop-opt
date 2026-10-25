from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import document_facts


class DocumentFactsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project_doc"
        self.version = "V_1.0"
        self.module = "PRD"
        self.module_dir = self.root / "version" / self.version / "doc" / self.module
        self.module_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def write_module_desc(self) -> Path:
        path = self.module_dir / f"{self.module}_desc.md"
        path.write_text(document_facts.module_desc_template(self.module, self.version), encoding="utf-8")
        return path

    def test_module_template_uses_confirmed_structure(self) -> None:
        text = document_facts.module_desc_template(self.module, self.version)
        for heading in (
            "## 模块信息",
            "## 业务能力",
            "## 模块功能",
            "## 业务信息",
            "## 规则与约束",
            "## 模块依赖",
            "## 文档引用",
        ):
            self.assertIn(heading, text)
        for removed in ("工作量", "实现证据", "缺口说明", "前端页面代码路径"):
            self.assertNotIn(removed, text)

    def test_discover_extra_documents_matches_prd_shape(self) -> None:
        self.write_module_desc()
        for name in (
            "PRD_business_model.md",
            "PRD_db.md",
            "PRD_api.md",
            "PRD_ER.md",
            "PRD_desc.html",
            "PRD_asset_lifecycle_integration.md",
            "PRD_process.md",
            "PRD_frontend_code_spec.json",
            "product_info.md",
            "PRD_method_contract.md",
        ):
            (self.module_dir / name).write_text(name, encoding="utf-8")
        (self.module_dir / "generated").mkdir()
        (self.module_dir / "generated" / "preview.md").write_text("preview", encoding="utf-8")
        self.assertEqual(
            [
                "PRD_asset_lifecycle_integration.md",
                "PRD_frontend_code_spec.json",
                "PRD_method_contract.md",
                "PRD_process.md",
                "product_info.md",
            ],
            document_facts.discover_extra_documents(self.module_dir, self.module),
        )

    def test_source_desc_is_synchronized_idempotently(self) -> None:
        path = self.write_module_desc()
        (self.module_dir / "PRD_process.md").write_text("process", encoding="utf-8")
        first, changed, extras = document_facts.synchronize_module_desc_source(path, self.module, self.version)
        self.assertTrue(changed)
        self.assertEqual(["PRD_process.md"], extras)
        self.assertIn("补充文档（自动）：[PRD_process.md](PRD_process.md)", first)
        second, changed_again, _ = document_facts.synchronize_module_desc_source(path, self.module, self.version)
        self.assertFalse(changed_again)
        self.assertEqual(first, second)
        self.assertEqual(1, second.count("补充文档（自动）：[PRD_process.md](PRD_process.md)"))

    def test_project_merge_rewrites_extra_refs_to_version_source(self) -> None:
        source_path = self.write_module_desc()
        (self.module_dir / "product_info.md").write_text("product", encoding="utf-8")
        source, _, extras = document_facts.synchronize_module_desc_source(source_path, self.module, self.version)
        merged = document_facts.merge_module_desc(
            source,
            None,
            self.module,
            self.version,
            extra_documents=extras,
        )
        self.assertIn(
            "补充文档（V_1.0，自动）：[product_info.md](../../version/V_1.0/doc/PRD/product_info.md)",
            merged,
        )
        self.assertIn("需求列表：[打开](../requirement_list.md)", merged)
        self.assertIn("最后合并版本摘要：[打开](../../version/V_1.0/version_summary.md)", merged)
        self.assertNotIn("需求列表：[打开](../../requirement_list.md)", merged)

    def test_remerge_replaces_current_version_auto_refs_but_keeps_old_versions(self) -> None:
        source_path = self.write_module_desc()
        (self.module_dir / "current.md").write_text("current", encoding="utf-8")
        source, _, extras = document_facts.synchronize_module_desc_source(source_path, self.module, self.version)
        target = document_facts.merge_module_desc(source, None, self.module, self.version, extra_documents=extras)
        target = target.replace(
            "- 补充文档（V_1.0，自动）：[current.md](../../version/V_1.0/doc/PRD/current.md)",
            "- 补充文档（V_0.9，自动）：[legacy.md](../../version/V_0.9/doc/PRD/legacy.md)\n"
            "- 补充文档（V_1.0，自动）：[stale.md](../../version/V_1.0/doc/PRD/stale.md)",
        )
        merged = document_facts.merge_module_desc(source, target, self.module, self.version, extra_documents=extras)
        self.assertIn("V_0.9，自动", merged)
        self.assertIn("[current.md]", merged)
        self.assertNotIn("[stale.md]", merged)

    def test_merge_upserts_business_facts_and_validates_references(self) -> None:
        source = document_facts.module_desc_template(self.module, self.version)
        source = source.replace(
            "| --- | --- | --- | --- | --- |\n\n## 模块功能",
            "| --- | --- | --- | --- | --- |\n"
            "| CAP-PRD-001 | 商品管理 | 管理商品资料 | 不负责库存 | ACTIVE |\n\n## 模块功能",
        )
        source = source.replace(
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n\n## 业务信息",
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| REQ-PRD-001-F01 | 查询商品 | 查询商品信息 | CAP-PRD-001 | REQ-PRD-001 | API-PRD-001 | DB-PRD-PRODUCT | ACTIVE |\n\n## 业务信息",
        )
        merged = document_facts.merge_module_desc(source, None, self.module, self.version)
        self.assertIn("REQ-PRD-001-F01", merged)
        bad = source.replace("CAP-PRD-001 | REQ", "CAP-PRD-999 | REQ")
        with self.assertRaisesRegex(document_facts.DocumentFactError, "missing capabilities"):
            document_facts.merge_module_desc(bad, None, self.module, self.version)

    def test_module_desc_rejects_retired_feature_and_rule_namespaces(self) -> None:
        source = document_facts.module_desc_template(self.module, self.version)
        source = source.replace(
            "| --- | --- | --- | --- | --- |\n\n## 模块功能",
            "| --- | --- | --- | --- | --- |\n"
            "| CAP-PRD-001 | 商品管理 | 管理商品资料 | 不负责库存 | ACTIVE |\n\n## 模块功能",
        )
        retired_feature = source.replace(
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n\n## 业务信息",
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| FEAT-PRD-001 | 查询商品 | 查询商品信息 | CAP-PRD-001 | PRD0013 | 无 | 无 | ACTIVE |\n\n## 业务信息",
        )
        with self.assertRaisesRegex(document_facts.DocumentFactError, "REQUIREMENT_ID"):
            document_facts.merge_module_desc(retired_feature, None, self.module, self.version)

        current_feature = source.replace(
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n\n## 业务信息",
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| PRD0013-F01 | 查询商品 | 查询商品信息 | CAP-PRD-001 | PRD0013 | 无 | 无 | ACTIVE |\n\n## 业务信息",
        )
        retired_rule = current_feature.replace(
            "| --- | --- | --- | --- |\n\n## 模块依赖",
            "| --- | --- | --- | --- |\n"
            "| RULE-PRD-001 | 旧规则 | PRD0013 | PRD0013-F01 |\n\n## 模块依赖",
        )
        with self.assertRaisesRegex(document_facts.DocumentFactError, "BR-/CR-"):
            document_facts.merge_module_desc(retired_rule, None, self.module, self.version)

    def test_program_scoped_rule_id_maps_to_one_requirement_namespace(self) -> None:
        source = document_facts.module_desc_template(self.module, self.version)
        source = source.replace(
            "| --- | --- | --- | --- | --- |\n\n## 模块功能",
            "| --- | --- | --- | --- | --- |\n"
            "| CAP-PRD-001 | 商品管理 | 管理商品资料 | 不负责库存 | ACTIVE |\n\n## 模块功能",
        )
        source = source.replace(
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n\n## 业务信息",
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| P1-COMPILER-F01 | 编译 | 编译配置 | CAP-PRD-001 | P1-COMPILER | 无 | 无 | ACTIVE |\n\n## 业务信息",
        )
        source = source.replace(
            "| --- | --- | --- | --- |\n\n## 模块依赖",
            "| --- | --- | --- | --- |\n"
            "| BR-P1-001 | 失败不得发布 | P1-COMPILER | P1-COMPILER-F01 |\n\n## 模块依赖",
        )
        merged = document_facts.merge_module_desc(source, None, self.module, self.version)
        self.assertIn("BR-P1-001", merged)

    def test_program_scoped_rule_id_rejects_ambiguous_requirement_namespace(self) -> None:
        source = document_facts.module_desc_template(self.module, self.version)
        source = source.replace(
            "| --- | --- | --- | --- | --- |\n\n## 模块功能",
            "| --- | --- | --- | --- | --- |\n"
            "| CAP-PRD-001 | 商品管理 | 管理商品资料 | 不负责库存 | ACTIVE |\n\n## 模块功能",
        )
        source = source.replace(
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n\n## 业务信息",
            "| --- | --- | --- | --- | --- | --- | --- | --- |\n"
            "| P1-COMPILER-F01 | 编译 | 编译配置 | CAP-PRD-001 | P1-COMPILER | 无 | 无 | ACTIVE |\n\n## 业务信息",
        )
        source = source.replace(
            "| --- | --- | --- | --- |\n\n## 模块依赖",
            "| --- | --- | --- | --- |\n"
            "| BR-P1-001 | 失败不得发布 | P1-COMPILER, P1-OTHER | P1-COMPILER-F01 |\n\n## 模块依赖",
        )
        with self.assertRaisesRegex(document_facts.DocumentFactError, "exactly one owning requirement"):
            document_facts.merge_module_desc(source, None, self.module, self.version)

    def test_requirement_list_upserts_by_code(self) -> None:
        source = document_facts.requirement_list_template(self.version).replace(
            "| --- | --- | --- | --- | --- | --- | --- |",
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| REQ-PRD-001 | 商品管理 | V_1.0 | [requirement.md](doc/V_1.0_0029/requirement.md) | 管理商品资料 | - | COMPLETED |",
        )
        target = document_facts.requirement_list_template("CURRENT", project=True).replace(
            "| --- | --- | --- | --- | --- | --- | --- |",
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| REQ-PRD-001 | 旧名称 | V_0.9 | [requirement.md](../version/V_0.9/doc/V_0.9_0001/requirement.md) | 旧说明 | - | IN_PROGRESS |",
        )
        merged = document_facts.merge_requirement_list(source, target)
        self.assertIn("| REQ-PRD-001 | 商品管理 | V_1.0 | [requirement.md](../version/V_1.0/doc/V_1.0_0029/requirement.md) | 管理商品资料 | - | COMPLETED |", merged)
        self.assertNotIn("旧名称", merged)


    def test_requirement_list_rewrites_links_for_project_archive(self) -> None:
        source = document_facts.requirement_list_template("V_2.0").replace(
            "| --- | --- | --- | --- | --- | --- | --- |",
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| REQ-001 | 示例 | V_2.0 | [requirement.md](doc/V_2.0_0030/requirement.md) | 示例需求 | - | COMPLETED |",
        )
        merged = document_facts.merge_requirement_list(source, None)
        self.assertIn("[requirement.md](../version/V_2.0/doc/V_2.0_0030/requirement.md)", merged)

    def test_requirement_list_rejects_invalid_link_scope(self) -> None:
        source = document_facts.requirement_list_template("V_2.0").replace(
            "| --- | --- | --- | --- | --- | --- | --- |",
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| REQ-001 | 示例 | V_2.0 | [requirement.md](../../outside.md) | 示例需求 | - | COMPLETED |",
        )
        with self.assertRaisesRegex(document_facts.DocumentFactError, "invalid path"):
            document_facts.merge_requirement_list(source, None)

    def test_requirement_list_rejects_version_mismatch_with_source_directory(self) -> None:
        source_path = self.root / "version" / self.version / "requirement_list.md"
        source_path.parent.mkdir(parents=True, exist_ok=True)
        requirement_path = source_path.parent / "doc" / "V_1.0_0029" / "requirement.md"
        requirement_path.parent.mkdir(parents=True, exist_ok=True)
        requirement_path.write_text("# requirement\n", encoding="utf-8")
        source = document_facts.requirement_list_template(self.version).replace(
            "| --- | --- | --- | --- | --- | --- | --- |",
            "| --- | --- | --- | --- | --- | --- | --- |\n"
            "| REQ-001 | 示例 | V_9.9 | [requirement.md](doc/V_1.0_0029/requirement.md) | 示例需求 | - | COMPLETED |",
        )
        with self.assertRaisesRegex(document_facts.DocumentFactError, "must equal source version directory"):
            document_facts.validate_requirement_list_links(source_path, source)

    def test_simulate_merges_synchronizes_and_tracks_extra_only_change(self) -> None:
        document_facts.ensure_templates(self.root, self.version, self.module)
        (self.module_dir / "PRD_process.md").write_text("process", encoding="utf-8")
        records = document_facts.simulate_merges(self.root, self.version, self.module)
        self.assertEqual(["module_desc"], [item["artifactType"] for item in records])
        source_text = (self.module_dir / "PRD_desc.md").read_text(encoding="utf-8")
        self.assertIn("PRD_process.md", source_text)
        result = bytes(records[0]["resultContent"]).decode("utf-8")
        self.assertIn("../../version/V_1.0/doc/PRD/PRD_process.md", result)

    def test_ready_record_becomes_stale_when_extra_doc_changes_after_preview(self) -> None:
        document_facts.ensure_templates(self.root, self.version, self.module)
        (self.module_dir / "first.md").write_text("first", encoding="utf-8")
        record = document_facts.simulate_merges(self.root, self.version, self.module)[0]
        public = document_facts.public_record(record, "preview.md")
        (self.module_dir / "second.md").write_text("second", encoding="utf-8")
        with self.assertRaisesRegex(document_facts.DocumentFactError, "result digest is stale"):
            document_facts.verify_ready_record(public, self.root)

    def test_hidden_html_standard_and_generated_files_are_not_referenced(self) -> None:
        self.write_module_desc()
        names = [".DS_Store", "PRD_desc.html", "PRD_api.yaml", "Thumbs.db", "temp.tmp"]
        for name in names:
            (self.module_dir / name).write_text("x", encoding="utf-8")
        self.assertEqual([], document_facts.discover_extra_documents(self.module_dir, self.module))


if __name__ == "__main__":
    unittest.main()
