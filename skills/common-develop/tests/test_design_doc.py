#!/usr/bin/env python3
"""Regression tests for implementation-ready canonical design documents."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import design_doc


class DesignDocumentTests(unittest.TestCase):
    FILL = "本次设计复用既有业务入口并收敛数据职责，主流程先校验当前事实再完成原子保存；失败时回滚业务写入并保留可诊断错误。"

    def completed_document(self) -> str:
        text = design_doc.render_template(
            title="会员偏好设置",
            module_code="PREF",
            requirement_ref="requirement.md#TR-DEMO-001",
            revision="DESIGN-R01@demo",
            base_revision="",
        )
        text = text.replace("{ADD_MODIFY_REMOVE_REUSE_UNCHANGED}", "MODIFY")
        text = text.replace("{ADD_MODIFY_REMOVE}", "MODIFY")
        text = text.replace("{SELECT_INSERT_UPDATE_DELETE}", "SELECT/UPDATE")
        text = text.replace("{COVERED_GAP}", "COVERED")
        text = text.replace(
            "{DB_DELTA_AND_IMPLEMENTATION_REF}",
            "changes/PREF001.yaml#DB-PREF-SETTING；db/migration/V001__pref.sql",
        )
        text = text.replace(
            "{API_DELTA_AND_IMPLEMENTATION_REF}",
            "changes/PREF001.yaml#API-PREF-SAVE；src/pref/PreferenceController.java#save",
        )
        text = text.replace("{FILE_CLASS_CONFIG}", "src/pref/PreferenceService.java")
        text = text.replace("{CURRENT_EVIDENCE}", "src/pref/PreferenceService.java#save")
        text = text.replace("{CURRENT_BEHAVIOR_EVIDENCE}", "src/pref/PreferenceService.java#save")
        text = text.replace("{IMPLEMENTATION_DECISION_ID}", "IMPL-DEC-PREF-SAVE")
        text = text.replace("{IMPLEMENTATION_OBJECT}", "PreferenceService.save")
        text = text.replace("{IMPLEMENTATION_STRATEGY}", "MODIFY")
        text = text.replace("{EXISTING_CANDIDATE_EVIDENCE}", "src/pref/PreferenceService.java#save")
        text = text.replace("{REUSE_REJECTION_OR_NA}", "现有签名不能接收新的偏好版本")
        text = text.replace("{EXTEND_REJECTION_OR_NA}", "旧保存语义必须同步调整而非并行扩展")
        text = text.replace("{MODIFY_REJECTION_OR_NA}", "不适用（理由：已选择 MODIFY）")
        text = text.replace("{SHARED_LOGIC_DISPOSITION}", "REUSE_SHARED")
        text = text.replace("{SHARED_LOGIC_OWNER}", "PreferenceDomainService")
        text = text.replace("{COMPATIBILITY_CALLERS_VERIFICATION}", "调用方迁移清单已确认，并由 PreferenceServiceTest 回归验证")
        text = text.replace("{BLUEPRINT_REF}", "[BP-PREF-SAVE](#code-blueprint)")
        text = text.replace("{BLUEPRINT_ID}", "BP-PREF-SAVE")
        text = text.replace("{IMPLEMENTATION_DECISION_REF}", "[IMPL-DEC-PREF-SAVE](#implementation-strategy-decisions)")
        text = text.replace("{BLUEPRINT_SHARED_LOGIC_OWNER}", "PreferenceDomainService")
        text = text.replace(
            "{SOURCE_DOCUMENT_MARKDOWN_LINKS}",
            "[AC-PREF-001](../requirement.md#AC-PREF-001)；[实现决策](#implementation-strategy-decisions)",
        )
        text = design_doc.PLACEHOLDER_RE.sub(self.FILL, text)
        return text.replace("> 状态：草稿", "> 状态：设计完成").replace("- [ ]", "- [x]")

    def validate_text(self, text: str, stage: str) -> design_doc.ValidationResult:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "design.md"
            path.write_text(text, encoding="utf-8")
            return design_doc.validate_design(path, stage)

    def test_packaged_template_has_all_implementation_matrices(self) -> None:
        result = design_doc.validate_design(design_doc.TEMPLATE, "structure")
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(len(design_doc.MATRIX_CONTRACTS), result.counts["matrices"])
        self.assertEqual(len(design_doc.NARRATIVE_CONTRACTS), result.counts["narratives"])
        self.assertGreater(result.counts["unresolvedPlaceholders"], 0)

    def test_template_keeps_readable_story_before_implementation_matrices(self) -> None:
        text = design_doc.TEMPLATE.read_text(encoding="utf-8")
        first_part = text.index("## 第一部分：设计正文")
        second_part = text.index("## 第二部分：开发实施明细")
        appendix = text.index("## 附录：追踪、决策与未决项")
        self.assertLess(first_part, second_part)
        self.assertLess(second_part, appendix)
        for marker in design_doc.MATRIX_CONTRACTS:
            self.assertGreater(text.index(f"<!-- {marker} -->"), second_part)
        self.assertIn("#### 1.1 改造前后对照", text)
        self.assertIn("#### 1.3 三分钟阅读路径", text)

    def test_ready_rejects_placeholders_and_unchecked_gates(self) -> None:
        result = design_doc.validate_design(design_doc.TEMPLATE, "ready")
        self.assertFalse(result.valid)
        self.assertTrue(any("unresolved design placeholders" in error for error in result.errors))
        self.assertTrue(any("readiness checklist" in error for error in result.errors))
        self.assertTrue(any("status from 草稿" in error for error in result.errors))

    def test_completed_document_is_development_ready(self) -> None:
        result = self.validate_text(self.completed_document(), "ready")
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(0, result.counts["uncheckedReadinessGates"])
        self.assertEqual(1, result.counts["implementationStrategyContractVersion"])
        self.assertEqual(1, result.counts["implementationDecisions"])

    def test_ready_rejects_create_without_ordered_rejection_reasons(self) -> None:
        text = self.completed_document().replace(
            "| IMPL-DEC-PREF-SAVE | PreferenceService.save | MODIFY |",
            "| IMPL-DEC-PREF-SAVE | PreferenceService.save | CREATE |",
        )
        text = text.replace("现有签名不能接收新的偏好版本", "无", 1)
        result = self.validate_text(text, "ready")
        self.assertFalse(result.valid)
        self.assertTrue(any("CREATE requires reasoned rejection" in error for error in result.errors))

    def test_ready_rejects_unknown_decision_reference_from_blueprint(self) -> None:
        text = self.completed_document().replace(
            "[IMPL-DEC-PREF-SAVE](#implementation-strategy-decisions)",
            "[IMPL-DEC-PREF-UNKNOWN](#implementation-strategy-decisions)",
        )
        result = self.validate_text(text, "ready")
        self.assertFalse(result.valid)
        self.assertTrue(any("unknown implementation decisions" in error for error in result.errors))

    def test_incomplete_design_is_rejected_at_structure_stage(self) -> None:
        text = self.completed_document()
        text = re.sub(r"^> Implementation strategy contract：`1`\s*$\n?", "", text, flags=re.M)
        start = text.index("#### 9.3 实现策略决策")
        end = text.index("### 10. 表、字段与数据读写明细")
        text = text[:start] + text[end:]
        row = next(line for line in text.splitlines() if line.startswith("| BP-PREF-SAVE |"))
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        behavior_row = "| " + " | ".join([cells[1], cells[2], cells[3], cells[4], cells[5], cells[7], cells[8], cells[11], cells[12]]) + " |"
        marker = "<!-- DESIGN-CODE-BLUEPRINT -->"
        block_start = text.index(marker)
        block_end = text.index("\n\n尚不存在的文件", block_start)
        behavior_block = (
            marker + "\n"
            "| 顺序 | 仓库/模块 | 文件、类或配置 | 变更类型 | 方法/符号 | 具体改动 | 作用 | 依赖 | 对应测试 |\n"
            "|---:|---|---|---|---|---|---|---|---|\n"
            + behavior_row
        )
        text = text[:block_start] + behavior_block + text[block_end:]
        structure = self.validate_text(text, "structure")
        self.assertFalse(structure.valid)
        self.assertTrue(any("implementation strategy" in error for error in structure.errors))
        ready = self.validate_text(text, "ready")
        self.assertFalse(ready.valid)

    def test_ready_rejects_matrix_complete_but_unreadable_short_narrative(self) -> None:
        text = self.completed_document().replace(self.FILL, "已调整。", 1)
        result = self.validate_text(text, "ready")
        self.assertFalse(result.valid)
        self.assertTrue(any("DESIGN-NARRATIVE-SUMMARY must explain" in error for error in result.errors))

    def test_ready_rejects_missing_field_update_contract(self) -> None:
        text = self.completed_document().replace(" | 更新条件 |", " |")
        result = self.validate_text(text, "ready")
        self.assertFalse(result.valid)
        self.assertTrue(any("DESIGN-FIELD-CHANGES missing columns" in error for error in result.errors))

    def test_ready_requires_reason_for_not_applicable(self) -> None:
        text = self.completed_document().replace(
            f"| {self.FILL} | MODIFY | {self.FILL} |",
            f"| 不适用 | MODIFY | {self.FILL} |",
            1,
        )
        result = self.validate_text(text, "ready")
        self.assertFalse(result.valid)
        self.assertTrue(any("not-applicable without a reason" in error for error in result.errors))

    def test_init_cli_creates_draft_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "design.md"
            command = [
                sys.executable,
                str(design_doc.ROOT / "scripts" / "design_doc.py"),
                "init",
                "-g",
                "DesignAgent",
                "--output",
                str(output),
                "--title",
                "会员偏好设置",
                "--module-code",
                "PREF",
                "--requirement-ref",
                "requirement.md#TR-DEMO-001",
                "--revision",
                "DESIGN-R01@demo",
                "--json",
            ]
            completed = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual(output.as_posix(), json.loads(completed.stdout)["created"])
            self.assertIn("# 会员偏好设置 技术设计", output.read_text(encoding="utf-8"))
            second = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(2, second.returncode)


if __name__ == "__main__":
    unittest.main()
