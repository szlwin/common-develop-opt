#!/usr/bin/env python3
"""Tests for project Hard/Soft/Assumption constraint declarations."""
from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

import project_constraints


class ProjectConstraintsTests(unittest.TestCase):
    def write_env(self, root: Path, constraints: object) -> None:
        (root / "project_env.md").write_text(
            "# Project environment\n\n"
            "```json common-develop-constraints\n"
            + json.dumps({"constraints": constraints}, ensure_ascii=False)
            + "\n```\n",
            encoding="utf-8",
        )

    def test_missing_block_is_not_declared(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "project_env.md").write_text("# No constraints\n", encoding="utf-8")
            payload = project_constraints.validate_project_constraints(root)
        self.assertEqual("NOT_DECLARED", payload["status"])
        self.assertEqual("NOT_DECLARED", payload["checks"][0]["status"])

    def test_valid_hard_soft_and_assumption_constraints(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "policy.md").write_text("policy\n", encoding="utf-8")
            self.write_env(root, [
                {
                    "id": "DATA-RETENTION",
                    "type": "HARD",
                    "statement": "保留策略必须经过测试",
                    "owner": "ProjectManagerAgent",
                    "source": "policy.md",
                    "gate": "testing",
                    "status": "VALIDATED",
                },
                {
                    "id": "REVIEW-DEPTH",
                    "type": "SOFT",
                    "statement": "高风险需求优先保留完整审查",
                    "owner": "ProjectManagerAgent",
                    "source": "policy.md",
                    "decision": "按需求影响面决定",
                },
                {
                    "id": "LEGACY-API",
                    "type": "ASSUMPTION",
                    "statement": "旧 API 在目标环境可用",
                    "owner": "ProjectManagerAgent",
                    "source": "policy.md",
                    "validation": "运行兼容性测试",
                    "validationRef": "policy.md#policy",
                    "status": "UNVALIDATED",
                },
            ])
            payload = project_constraints.validate_project_constraints(root)
        self.assertEqual("PARTIAL", payload["status"])
        self.assertEqual("PARTIAL", payload["completionStatus"])
        self.assertEqual(3, len(payload["constraints"]))
        self.assertEqual(
            ["COMPLETE", "COMPLETE", "PARTIAL"],
            [item["completionStatus"] for item in payload["constraints"]],
        )
        self.assertTrue(any("assumption is unvalidated" in item for item in payload["warnings"]))

    def test_hard_constraint_requires_known_gate_or_adapter(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "policy.md").write_text("policy\n", encoding="utf-8")
            self.write_env(root, [{
                "id": "UNKNOWN-GATE",
                "type": "HARD",
                "statement": "必须经过未知门禁",
                "owner": "ProjectManagerAgent",
                "source": "policy.md",
                "gate": "not-a-runtime-gate",
            }])
            payload = project_constraints.validate_project_constraints(root)
        self.assertEqual("FAILED", payload["status"])
        self.assertEqual("BLOCKED", payload["completionStatus"])
        self.assertEqual("BLOCKED", payload["constraints"][0]["completionStatus"])
        self.assertTrue(any("gate is not declared" in item for item in payload["errors"]))

    def test_missing_source_is_invalid_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_env(root, [{
                "id": "NO-SOURCE",
                "type": "SOFT",
                "statement": "必须保留约束来源",
                "owner": "ProjectManagerAgent",
                "decision": "按风险覆盖",
            }])
            payload = project_constraints.validate_project_constraints(root)
        self.assertEqual("FAILED", payload["status"])
        self.assertEqual("INVALID", payload["completionStatus"])
        self.assertEqual("INVALID", payload["constraints"][0]["completionStatus"])
        self.assertTrue(any("source is required" in item for item in payload["errors"]))

    def test_invalid_record_takes_precedence_over_unresolved_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_env(root, [{
                "id": "INVALID-AND-BLOCKED",
                "type": "UNKNOWN",
                "statement": "声明类型错误",
                "owner": "ProjectManagerAgent",
                "source": "missing.md",
            }])
            payload = project_constraints.validate_project_constraints(root)
        self.assertEqual("INVALID", payload["completionStatus"])
        self.assertEqual("INVALID", payload["constraints"][0]["completionStatus"])

    def test_missing_source_blocks_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_env(root, [{
                "id": "MISSING-SOURCE",
                "type": "HARD",
                "statement": "必须有可追溯的约束来源",
                "owner": "ProjectManagerAgent",
                "source": "does-not-exist.md",
                "gate": "testing",
                "status": "VALIDATED",
            }])
            payload = project_constraints.validate_project_constraints(root)
        self.assertEqual("FAILED", payload["status"])
        self.assertEqual("BLOCKED", payload["completionStatus"])
        self.assertEqual("BLOCKED", payload["constraints"][0]["completionStatus"])
        self.assertTrue(any("source does not exist" in item for item in payload["errors"]))

    def test_unknown_type_is_invalid_and_cannot_claim_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_env(root, [{
                "id": "UNKNOWN-TYPE",
                "type": "UNKNOWN",
                "statement": "未知类型不能直接完成",
                "owner": "ProjectManagerAgent",
                "completionStatus": "COMPLETE",
            }])
            payload = project_constraints.validate_project_constraints(root)
        self.assertEqual("FAILED", payload["status"])
        self.assertEqual("INVALID", payload["completionStatus"])
        self.assertEqual("INVALID", payload["constraints"][0]["completionStatus"])
        self.assertNotEqual("COMPLETE", payload["constraints"][0]["completionStatus"])

    def test_invalid_status_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_env(root, [{
                "id": "BAD-STATUS",
                "type": "SOFT",
                "statement": "状态必须来自允许集合",
                "owner": "ProjectManagerAgent",
                "decision": "保留完整审查",
                "status": "UNKNOWN",
            }])
            payload = project_constraints.validate_project_constraints(root)
        self.assertEqual("FAILED", payload["status"])
        self.assertEqual("INVALID", payload["completionStatus"])
        self.assertEqual("INVALID", payload["constraints"][0]["completionStatus"])

    def test_valid_constraints_are_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "policy.md").write_text("policy\n", encoding="utf-8")
            self.write_env(root, [
                {
                    "id": "HARD-OK",
                    "type": "HARD",
                    "statement": "必须经过测试门禁",
                    "owner": "ProjectManagerAgent",
                    "source": "policy.md",
                    "gate": "testing",
                    "status": "VALIDATED",
                },
                {
                    "id": "SOFT-OK",
                    "type": "SOFT",
                    "statement": "默认保留完整审查",
                    "owner": "ProjectManagerAgent",
                    "source": "policy.md",
                    "decision": "按风险覆盖",
                },
                {
                    "id": "ASSUMPTION-OK",
                    "type": "ASSUMPTION",
                    "statement": "目标环境支持旧 API",
                    "owner": "ProjectManagerAgent",
                    "source": "policy.md",
                    "validation": "运行兼容性测试",
                    "validationRef": "policy.md#policy",
                    "status": "VALIDATED",
                },
            ])
            payload = project_constraints.validate_project_constraints(root)
        self.assertEqual("PASSED", payload["status"])
        self.assertEqual("COMPLETE", payload["completionStatus"])
        self.assertTrue(all(item["completionStatus"] == "COMPLETE" for item in payload["constraints"]))

    def test_adapter_can_explain_project_specific_hard_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "harness_env.md").write_text(
                "# Harness environment\n\n"
                "```json common-develop-capabilities\n"
                + json.dumps({"capabilities": {"erp.production_data_review": {"available": True}}})
                + "\n```\n",
                encoding="utf-8",
            )
            self.write_env(root, [{
                "id": "ERP-DATA-SAFETY",
                "type": "HARD",
                "statement": "生产数据操作必须经过业务适配器",
                "owner": "ProjectManagerAgent",
                "source": "project_env.md",
                "gate": "erp.production_data_review",
                "adapter": "project_env#erp.production_data_review",
            }])
            payload = project_constraints.validate_project_constraints(root)
        self.assertEqual("PASSED", payload["status"])

    def test_schema_field_requirements_and_identity_are_checked(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.write_env(root, [
                {"id": "DUPLICATE", "type": "SOFT", "statement": "a", "owner": "A"},
                {"id": "DUPLICATE", "type": "ASSUMPTION", "statement": "b", "owner": "B"},
                {"id": "BAD-TYPE", "type": "OTHER", "statement": "c", "owner": "C"},
                {"id": "NO-VALIDATION", "type": "ASSUMPTION", "statement": "d", "owner": "D"},
            ])
            payload = project_constraints.validate_project_constraints(root)
        self.assertEqual("FAILED", payload["status"])
        self.assertTrue(any("duplicate constraint id" in item for item in payload["errors"]))
        self.assertTrue(any("type must be" in item for item in payload["errors"]))
        self.assertTrue(any("requires validation" in item for item in payload["errors"]))

    def test_cli_parser_exposes_validate_and_status(self) -> None:
        commands = next(action for action in project_constraints.build_parser()._actions if isinstance(action, argparse._SubParsersAction))
        self.assertEqual({"validate", "status"}, set(commands.choices))

    def test_unknown_field_is_rejected_by_json_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "policy.md").write_text("policy\n", encoding="utf-8")
            self.write_env(root, [{
                "id": "STRICT-FIELD", "type": "SOFT", "statement": "strict",
                "owner": "ProjectManagerAgent", "source": "policy.md",
                "decision": "keep review", "sttaus": "VALIDATED",
            }])
            payload = project_constraints.validate_project_constraints(root)
        self.assertEqual("FAILED", payload["status"])
        self.assertEqual("INVALID", payload["completionStatus"])
        self.assertTrue(any("sttaus" in error and "schema" in error for error in payload["errors"]))


if __name__ == "__main__":
    unittest.main()
