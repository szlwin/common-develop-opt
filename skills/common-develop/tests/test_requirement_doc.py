#!/usr/bin/env python3
"""Regression tests for canonical single- and multi-feature requirement documents."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import requirement_doc


class RequirementDocumentTests(unittest.TestCase):
    def make_document(self, feature_count: int = 1) -> str:
        features = [
            requirement_doc.Feature(
                code=f"PRD0013-F{index:02d}",
                name=f"功能{index}",
                owner="PRD",
                collaborators="ORD" if index > 1 else "-",
                description=f"功能{index}说明",
            )
            for index in range(1, feature_count + 1)
        ]
        text = requirement_doc.render_template(
            target_id="V_1.0_0029",
            requirement_id="PRD0013",
            title="商品实例流向与区域销售追溯需求",
            requirement_type="新增能力",
            primary_module="PRD",
            collaboration_modules="ORD",
            affected_roles="平台运营人员、企业用户",
            change_requirement_id="-",
            features=features,
        )
        return requirement_doc.PLACEHOLDER_RE.sub("已确认", text)

    def validate_text(self, text: str, stage: str = "analysis") -> requirement_doc.ValidationResult:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "requirement.md"
            path.write_text(text, encoding="utf-8")
            return requirement_doc.validate_requirement(path, stage)

    def test_packaged_multi_feature_example_passes_analysis_validation(self) -> None:
        path = requirement_doc.ROOT / "tests" / "fixtures" / "structured-docs" / "requirement.multi-feature.example.md"
        result = requirement_doc.validate_requirement(path, "analysis")
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(3, result.counts["features"])
        self.assertEqual(3, result.counts["traceItems"])

    def test_single_feature_requirement_passes_analysis_validation(self) -> None:
        result = self.validate_text(self.make_document(1))
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(1, result.counts["features"])
        self.assertEqual(1, result.counts["acceptanceCriteria"])
        self.assertEqual(1, result.counts["traceItems"])

    def test_multi_feature_requirement_passes_and_uses_one_document(self) -> None:
        result = self.validate_text(self.make_document(3))
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(3, result.counts["features"])
        self.assertEqual(3, result.counts["acceptanceCriteria"])
        self.assertEqual(3, result.counts["traceItems"])

    def test_confirmation_validation_checks_only_confirmation_owned_sections(self) -> None:
        text = self.make_document(2)
        # Analysis sections may still contain placeholders during confirmation.
        start = text.index("## 5. 功能列表")
        end = text.index("## 12. 待确认事项与已确认决策")
        text = text[:start] + requirement_doc.TEMPLATE.read_text(encoding="utf-8")[
            requirement_doc.TEMPLATE.read_text(encoding="utf-8").index("## 5. 功能列表"):
            requirement_doc.TEMPLATE.read_text(encoding="utf-8").index("## 12. 待确认事项与已确认决策")
        ] + text[end:]
        result = self.validate_text(text, "confirmation")
        self.assertTrue(result.valid, result.errors)

    def test_analysis_rejects_feature_without_trace_row(self) -> None:
        text = self.make_document(2)
        text = "\n".join(
            line for line in text.splitlines()
            if not line.startswith("| TR-PRD0013-002 |")
        ) + "\n"
        result = self.validate_text(text)
        self.assertFalse(result.valid)
        self.assertIn("feature is not covered by traceability: PRD0013-F02", result.errors)
        self.assertIn("acceptance criterion is not covered by traceability: AC-PRD0013-002", result.errors)

    def test_analysis_rejects_unresolved_placeholder(self) -> None:
        text = self.make_document(1).replace("已确认", "{UNRESOLVED_VALUE}", 1)
        result = self.validate_text(text)
        self.assertFalse(result.valid)
        self.assertTrue(any("unresolved" in error for error in result.errors))

    def test_requirement_requires_human_readable_summary(self) -> None:
        text = self.make_document(1).replace("### 1.1 阅读摘要", "### 1.1 机器索引")
        result = self.validate_text(text)
        self.assertFalse(result.valid)
        self.assertIn(
            "missing human-readable summary: ### 1.1 阅读摘要",
            result.errors,
        )

    def test_possible_implementation_details_are_warnings(self) -> None:
        text = self.make_document(1).replace("当前行为与证据", "当前行为与证据 t_prd_flow current_flag")
        result = self.validate_text(text)
        self.assertTrue(result.valid, result.errors)
        self.assertTrue(any("implementation details" in warning for warning in result.warnings))

    def test_init_creates_multi_feature_document_at_target_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            doc_root = Path(temp) / "project_doc"
            command = [
                sys.executable,
                str(requirement_doc.ROOT / "scripts" / "requirement_doc.py"),
                "init",
                "-g",
                "RequirementConfirmationAgent",
                "--doc-root",
                str(doc_root),
                "--version",
                "V_1.0",
                "--target-id",
                "V_1.0_0029",
                "--requirement-id",
                "PRD0013",
                "--title",
                "商品实例流向与区域销售追溯需求",
                "--primary-module",
                "PRD",
                "--feature",
                "PRD0013-F01|流向历史|PRD|-|记录历史",
                "--feature",
                "PRD0013-F02|区域限制|PRD|ORD|限制范围",
                "--json",
            ]
            completed = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(0, completed.returncode, completed.stderr)
            payload = json.loads(completed.stdout)
            path = Path(payload["created"])
            self.assertTrue(path.is_file())
            self.assertEqual(
                doc_root / "version" / "V_1.0" / "doc" / "V_1.0_0029" / "requirement.md",
                path,
            )
            text = path.read_text(encoding="utf-8")
            self.assertIn("### 6.1 PRD0013-F01 流向历史", text)
            self.assertIn("### 6.2 PRD0013-F02 区域限制", text)


    def test_analysis_rejects_uncovered_business_and_cross_feature_rules(self) -> None:
        text = self.make_document(1)
        text = text.replace("BR-PRD0013-001、BR-PRD0013-002、CR-PRD0013-001", "BR-PRD0013-001")
        result = self.validate_text(text)
        self.assertFalse(result.valid)
        self.assertIn("business rule is not covered by traceability: BR-PRD0013-002", result.errors)
        self.assertIn("cross-feature rule is not covered by traceability: CR-PRD0013-001", result.errors)

    def test_analysis_rejects_unknown_trace_references(self) -> None:
        text = self.make_document(1)
        text = text.replace("BR-PRD0013-001、BR-PRD0013-002、CR-PRD0013-001", "BR-PRD0013-001、BR-PRD0013-999、CR-PRD0013-001")
        text = text.replace("AC-PRD0013-001 | 待补充", "AC-PRD0013-999 | 待补充")
        result = self.validate_text(text)
        self.assertFalse(result.valid)
        self.assertIn("traceability references unknown rule: TR-PRD0013-001->BR-PRD0013-999", result.errors)
        self.assertIn("traceability references unknown acceptance criterion: TR-PRD0013-001->AC-PRD0013-999", result.errors)

    def test_sync_traceability_creates_machine_records(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            requirement_path = root / "version" / "V_1.0" / "doc" / "V_1.0_0029" / "requirement.md"
            requirement_path.parent.mkdir(parents=True)
            requirement_path.write_text(self.make_document(2), encoding="utf-8")
            task_dir = root / "version" / "V_1.0" / "task" / "V_1.0_0029"
            task_dir.mkdir(parents=True)
            command = [
                sys.executable,
                str(requirement_doc.ROOT / "scripts" / "requirement_doc.py"),
                "sync-traceability",
                "-g",
                "RequirementAnalysisAgent",
                "--file",
                str(requirement_path),
                "--task-dir",
                str(task_dir),
                "--json",
            ]
            completed = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(0, completed.returncode, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(["TR-PRD0013-001", "TR-PRD0013-002"], payload["created"])
            trace_path = task_dir / "traceability.json"
            document = trace_path.read_text(encoding="utf-8")
            traces = json.loads(document)["items"]
            self.assertIn("\"items\"", document)
            self.assertEqual(2, len(traces))
            first = traces[0]
            self.assertEqual(["AC-PRD0013-001"], first["acceptance_criteria"])
            self.assertIn("version/V_1.0/doc/V_1.0_0029/requirement.md#BR-PRD0013-002", first["requirement_refs"])
            self.assertIn("version/V_1.0/doc/V_1.0_0029/requirement.md#CR-PRD0013-001", first["requirement_refs"])

    def test_sync_preserves_downstream_refs_and_marks_changed_source_stale(self) -> None:
        text = self.make_document(1)
        sources = requirement_doc.parse_trace_sources(text)
        source_path = "version/V_1.0/doc/V_1.0_0029/requirement.md"
        existing = [requirement_doc.trace_item_from_source(
            sources[0], source_path=source_path, feature_name="功能1"
        )]
        existing[0]["status"] = "COVERED"
        existing[0]["business_model_refs"] = ["business_model.yaml#INV-001"]
        existing[0]["design_refs"] = ["design.md#DES-001"]
        existing[0]["verification_evidence_ids"] = ["EVD-000001"]
        changed_source = requirement_doc.TraceSource(
            trace_id=sources[0].trace_id,
            feature_id=sources[0].feature_id,
            rule_ids=sources[0].rule_ids,
            acceptance_ids=("AC-PRD0013-001", "AC-PRD0013-002"),
            impact_refs=(), model_refs=(), design_refs=(), test_case_ids=(), status="PENDING",
        )
        merged, summary = requirement_doc.merge_traceability(
            existing,
            [changed_source],
            source_path=source_path,
            names={"PRD0013-F01": "功能1"},
            requirement_id="PRD0013",
        )
        self.assertEqual(["business_model.yaml#INV-001"], merged[0]["business_model_refs"])
        self.assertEqual(["design.md#DES-001"], merged[0]["design_refs"])
        self.assertEqual(["EVD-000001"], merged[0]["verification_evidence_ids"])
        self.assertEqual("STALE", merged[0]["status"])
        self.assertEqual(["TR-PRD0013-001"], summary["stale"])

    def test_sync_detects_body_change_without_changing_ids(self) -> None:
        text = self.make_document(1)
        old_source = requirement_doc.parse_trace_sources(text)[0]
        existing = requirement_doc.trace_item_from_source(old_source, source_path="requirement.md", feature_name="功能1")
        existing["status"] = "COVERED"
        existing["business_model_refs"] = ["model.yaml#/revision"]
        changed = text.replace("功能1说明", "功能1说明：失败必须回滚")
        merged, summary = requirement_doc.merge_traceability(
            [existing], requirement_doc.parse_trace_sources(changed),
            source_path="requirement.md", names={old_source.feature_id: "功能1"}, requirement_id="PRD0013",
        )
        self.assertEqual("STALE", merged[0]["status"])
        self.assertEqual([old_source.trace_id], summary["stale"])
        self.assertNotEqual(old_source.source_digest, merged[0]["requirement_source_digest"])
        again, summary = requirement_doc.merge_traceability(
            merged, requirement_doc.parse_trace_sources(changed),
            source_path="requirement.md", names={old_source.feature_id: "功能1"}, requirement_id="PRD0013",
        )
        self.assertEqual(merged, again)
        self.assertEqual([old_source.trace_id], summary["unchanged"])

    def test_sync_detects_cross_module_features_without_guessing_impact_refs(self) -> None:
        sources = requirement_doc.parse_trace_sources(self.make_document(2))
        first = requirement_doc.trace_item_from_source(sources[0], source_path="requirement.md", feature_name="功能1")
        second = requirement_doc.trace_item_from_source(sources[1], source_path="requirement.md", feature_name="功能2")
        self.assertFalse(first["cross_module_implementation_required"])
        self.assertTrue(second["cross_module_implementation_required"])
        self.assertTrue(second["impact_required"])
        self.assertEqual([], second["dependency_impact_refs"])

    def test_sync_is_idempotent_when_requirement_source_is_unchanged(self) -> None:
        text = self.make_document(1)
        sources = requirement_doc.parse_trace_sources(text)
        source_path = "version/V_1.0/doc/V_1.0_0029/requirement.md"
        first, _ = requirement_doc.merge_traceability(
            [], sources, source_path=source_path,
            names=requirement_doc.feature_names(text), requirement_id="PRD0013",
        )
        second, summary = requirement_doc.merge_traceability(
            first, sources, source_path=source_path,
            names=requirement_doc.feature_names(text), requirement_id="PRD0013",
        )
        self.assertEqual(first, second)
        self.assertEqual(["TR-PRD0013-001"], summary["unchanged"])
        self.assertEqual([], summary["updated"])


    def test_sync_list_writes_version_and_requirement_link_idempotently(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            requirement_path = root / "version" / "V_1.0" / "doc" / "V_1.0_0029" / "requirement.md"
            requirement_path.parent.mkdir(parents=True)
            requirement_path.write_text(self.make_document(1), encoding="utf-8")
            command = [
                sys.executable,
                str(requirement_doc.ROOT / "scripts" / "requirement_doc.py"),
                "sync-list",
                "-g",
                "RequirementConfirmationAgent",
                "--file",
                str(requirement_path),
                "--description",
                "商品实例流向追溯",
                "--status",
                "需求确认完成",
                "--json",
            ]
            first = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(0, first.returncode, first.stderr)
            payload = json.loads(first.stdout)
            self.assertEqual("V_1.0", payload["version"])
            self.assertEqual("doc/V_1.0_0029/requirement.md", payload["documentLink"])
            list_path = root / "version" / "V_1.0" / "requirement_list.md"
            text = list_path.read_text(encoding="utf-8")
            self.assertIn(
                "| PRD0013 | 商品实例流向与区域销售追溯需求 | V_1.0 | [requirement.md](doc/V_1.0_0029/requirement.md) | 商品实例流向追溯 | - | 需求确认完成 |",
                text,
            )

            command[command.index("商品实例流向追溯")] = "更新后的需求摘要"
            command[command.index("需求确认完成")] = "需求分析中"
            second = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(0, second.returncode, second.stderr)
            updated = list_path.read_text(encoding="utf-8")
            self.assertEqual(1, updated.count("| PRD0013 |"))
            self.assertIn("更新后的需求摘要", updated)
            self.assertIn("需求分析中", updated)

    def test_sync_list_rejects_unauthorized_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            requirement_path = root / "version" / "V_1.0" / "doc" / "V_1.0_0029" / "requirement.md"
            requirement_path.parent.mkdir(parents=True)
            requirement_path.write_text(self.make_document(1), encoding="utf-8")
            completed = subprocess.run([
                sys.executable,
                str(requirement_doc.ROOT / "scripts" / "requirement_doc.py"),
                "sync-list",
                "-g",
                "DevelopAgent",
                "--file",
                str(requirement_path),
                "--description",
                "需求摘要",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(2, completed.returncode)
            self.assertIn("not authorized", completed.stderr)

    def test_unauthorized_agent_cannot_sync_traceability(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            requirement_path = root / "version" / "V_1.0" / "doc" / "V_1.0_0029" / "requirement.md"
            requirement_path.parent.mkdir(parents=True)
            requirement_path.write_text(self.make_document(1), encoding="utf-8")
            task_dir = root / "version" / "V_1.0" / "task" / "V_1.0_0029"
            task_dir.mkdir(parents=True)
            command = [
                sys.executable,
                str(requirement_doc.ROOT / "scripts" / "requirement_doc.py"),
                "sync-traceability",
                "-g",
                "DevelopAgent",
                "--file",
                str(requirement_path),
                "--task-dir",
                str(task_dir),
            ]
            completed = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(2, completed.returncode)
            self.assertIn("not authorized", completed.stderr)

    def test_finalize_requirement_analysis_initializes_traceability_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            requirement_path = root / "version" / "V_1.0" / "doc" / "V_1.0_0029" / "requirement.md"
            requirement_path.parent.mkdir(parents=True)
            requirement_path.write_text(self.make_document(2), encoding="utf-8")
            task_dir = root / "version" / "V_1.0" / "task" / "V_1.0_0029"
            task_dir.mkdir(parents=True)
            completed = subprocess.run([
                sys.executable,
                str(requirement_doc.ROOT / "scripts" / "requirement_doc.py"),
                "finalize-requirement-analysis",
                "-g",
                "RequirementAnalysisAgent",
                "--file",
                str(requirement_path),
                "--task-dir",
                str(task_dir),
                "--json",
            ], text=True, capture_output=True, check=False)
            self.assertEqual(0, completed.returncode, completed.stderr)
            payload = json.loads(completed.stdout.split("{", 1)[1].join(["{"]) if False else completed.stdout[completed.stdout.find("{"):])
            self.assertEqual("PASSED", payload["status"])
            self.assertEqual(2, payload["traceCount"])
            trace_path = task_dir / "traceability.json"
            self.assertTrue(trace_path.is_file())
            traces = json.loads(trace_path.read_text(encoding="utf-8"))["items"]
            self.assertEqual(2, len(traces))


    def test_unauthorized_agent_cannot_initialize_requirement_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            command = [
                sys.executable,
                str(requirement_doc.ROOT / "scripts" / "requirement_doc.py"),
                "init",
                "-g",
                "DevelopAgent",
                "--doc-root",
                str(Path(temp) / "project_doc"),
                "--version",
                "V_1.0",
                "--target-id",
                "V_1.0_0029",
                "--requirement-id",
                "PRD0013",
                "--title",
                "需求",
                "--primary-module",
                "PRD",
            ]
            completed = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(2, completed.returncode)
            self.assertIn("not authorized", completed.stderr)


if __name__ == "__main__":
    unittest.main()
