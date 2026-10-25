#!/usr/bin/env python3
"""Tests for requirement-scoped test design documents."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import case_doc


class TestCaseDocumentTests(unittest.TestCase):
    def completed_document(self, requirement_id: str = "REQ-USER-001") -> str:
        text = case_doc.render_template(
            requirement_id,
            "TEST-REQ-USER-001-R01@demo",
            "REQ-USER-001-R01@demo",
        )
        replacements = {
            "{CASE_NAME}": "按用户名称查询",
            "{DESIGN_DOC_REF}": "../USER/USER_design.md",
            "{PAGE_DESIGN_REF}": "../USER/USER_page_design.md",
            "{TEST_OBJECTIVE}": "验证按用户名称查询的可观察行为",
            "{IN_SCOPE}": "用户查询页面和查询接口",
            "{OUT_OF_SCOPE}": "用户创建和删除",
            "{ENVIRONMENT_AND_ACCOUNT}": "隔离测试环境；普通查询账号",
            "{SHARED_PRECONDITIONS}": "已初始化名称为张三和李四的用户",
            "{FEATURE_ACCEPTANCE_REFS}": "REQ-USER-001-F01 / AC-USER-001",
            "{PAGE_ACTION_REFS}": "PAGE-USER-QUERY / PAGEACT-USER-QUERY",
            "{FLOW_STEP_REFS}": "FLOW-USER-QUERY / STEP-USER-QUERY",
            "{TEST_LEVEL}": "UI/接口集成测试",
            "{AUTOMATION_STATUS}": "AUTOMATED",
            "{REQUIREMENT_ACCEPTANCE_REFS}": "REQ-USER-001 / AC-USER-001",
            "{REQUIREMENT_ANCHOR}": "ac-user-001",
            "{DESIGN_CONTRACT_REFS}": "DESIGN-USER-QUERY / API-USER-QUERY",
            "{DESIGN_ANCHOR}": "code-blueprint",
            "{PAGE_ANCHOR}": "pageact-user-query",
            "{FLOW_DOC_REF_OR_REASONED_NA}": "[用户查询流程](../../../../docs/_flows/USER_flow.md#FLOW-USER-QUERY)",
            "{INPUT_NAME}": "用户名称",
            "{EXACT_INPUT_VALUE}": "张三",
            "{INPUT_METHOD}": "在用户查询页名称输入框输入后点击查询",
            "{INPUT_SOURCE_OR_CONSTRAINT}": "AC-USER-001；完全匹配",
            "{ACTION}": "输入张三并点击查询",
            "{PAGE_OR_API}": "PAGE-USER-QUERY / API-USER-QUERY",
            "{INTERMEDIATE_RESULT}": "查询请求携带 name=张三",
            "{OUTPUT_LOCATION}": "用户查询页面结果列表的名称列",
            "{EXACT_EXPECTED_OUTPUT}": "列表出现名称为张三的用户，且不出现李四",
            "{VERIFICATION_METHOD}": "逐行读取页面名称列，并核对接口响应 displayName",
            "{PASS_CRITERIA}": "至少一行等于张三且所有行均不等于李四",
            "{FORBIDDEN_SIDE_EFFECTS}": "不得修改用户资料或创建新用户",
            "{POSTCONDITION}": "用户数据保持初始化状态",
            "{CLEANUP_REQUIREMENT}": "执行需求级清理脚本",
            "{FIXTURE_REF_OR_REASONED_NA}": "../../test/REQ-USER-001/data/init.sql#CASE-REQ-USER-001-001",
            "{CLEANUP_REF_OR_REASONED_NA}": "../../test/REQ-USER-001/data/cleanup.sql#CASE-REQ-USER-001-001",
            "{AUTOMATION_TEST_REF_OR_REASONED_NA}": "tests/user_query.spec.ts#queries-by-exact-name",
            "{EXECUTION_COMMAND_OR_REASONED_NA}": "pnpm test user_query.spec.ts",
            "{AUTOMATION_STATUS_OR_REASON}": "AUTOMATED",
        }
        for source, target in replacements.items():
            text = text.replace(source, target)
        self.assertFalse(case_doc.PLACEHOLDER_RE.search(text), text)
        return text.replace("> 状态：草稿", "> 状态：测试设计完成").replace("- [ ]", "- [x]")

    def validate_text(self, text: str) -> case_doc.ValidationResult:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            path = root / "version" / "V_1.0" / "doc" / "REQ-USER-001" / "test_case.md"
            path.parent.mkdir(parents=True)
            (path.parent / "requirement.md").write_text("# REQ-USER-001\n", encoding="utf-8")
            path.write_text(text, encoding="utf-8")
            return case_doc.validate_document(path, "ready", root)

    def test_packaged_template_has_valid_structure(self) -> None:
        result = case_doc.validate_document(case_doc.TEMPLATE, "structure")
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(1, result.counts["cases"])

    def test_ready_document_requires_concrete_input_output_and_verification(self) -> None:
        result = self.validate_text(self.completed_document())
        self.assertTrue(result.valid, result.errors)

    def test_ready_rejects_vague_expected_output(self) -> None:
        text = self.completed_document().replace("列表出现名称为张三的用户，且不出现李四", "正确")
        result = self.validate_text(text)
        self.assertFalse(result.valid)
        self.assertTrue(any("预期输出" in error for error in result.errors), result.errors)

    def test_ready_requires_requirement_directory_and_sibling_requirement(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            path = root / "version" / "V_1.0" / "test_case.md"
            path.parent.mkdir(parents=True)
            path.write_text(self.completed_document(), encoding="utf-8")
            result = case_doc.validate_document(path, "ready", root)
        self.assertFalse(result.valid)
        self.assertTrue(any("version/{VERSION}/doc/{REQUIREMENT_ID}" in error for error in result.errors))

    def test_init_cli_creates_only_requirement_scoped_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            requirement = root / "version" / "V_1.0" / "doc" / "REQ-USER-001" / "requirement.md"
            requirement.parent.mkdir(parents=True)
            requirement.write_text("# REQ-USER-001\n", encoding="utf-8")
            command = [
                sys.executable, str(case_doc.ROOT / "scripts" / "case_doc.py"), "init",
                "-g", "TestDesignAgent", "--doc-root", str(root), "--version", "V_1.0",
                "--requirement-id", "REQ-USER-001", "--revision", "TEST-R01@demo",
                "--requirement-revision", "REQ-R01@demo", "--json",
            ]
            completed = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(0, completed.returncode, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["created"].endswith("version/V_1.0/doc/REQ-USER-001/test_case.md"))
            self.assertEqual("draft", payload["stage"])
            self.assertEqual("PASSED", payload["draftValidation"])
            self.assertGreater(payload["unresolvedPlaceholders"], 0)
            self.assertIn("same file", payload["next"])
            self.assertFalse((root / "version" / "V_1.0" / "test_case.md").exists())

    def test_init_rejects_invalid_existing_business_model_before_writing_case(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project_doc"
            requirement = root / "version" / "V_1.0" / "doc" / "REQ-USER-001" / "requirement.md"
            requirement.parent.mkdir(parents=True)
            requirement.write_text("# REQ-USER-001\n", encoding="utf-8")
            model = root / "version" / "V_1.0" / "doc" / "MODEL" / "MODEL_business_model.yaml"
            model.parent.mkdir(parents=True)
            model.write_text("schemaVersion: 1\nscenarios:\n  - given: [未加引号的中文数组]\n", encoding="utf-8")
            command = [
                sys.executable, str(case_doc.ROOT / "scripts" / "case_doc.py"), "init",
                "-g", "TestDesignAgent", "--doc-root", str(root), "--version", "V_1.0",
                "--requirement-id", "REQ-USER-001", "--revision", "TEST-R01@demo",
                "--requirement-revision", "REQ-R01@demo", "--json",
            ]
            completed = subprocess.run(command, text=True, capture_output=True, check=False)
            self.assertEqual(2, completed.returncode)
            self.assertIn("derived relation/portal refresh failed", completed.stderr)
            self.assertIn("inline collections must use JSON syntax", completed.stderr)
            self.assertFalse((requirement.parent / "test_case.md").exists())


if __name__ == "__main__":
    unittest.main()
