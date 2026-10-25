from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import task_plan


class TaskPlanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.task_dir = Path(self.temp.name) / "P1-COMPILER-F01"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = task_plan.main(args)
        return code, stdout.getvalue(), stderr.getvalue()

    def init_plan(self, *, minimal: bool = False, architecture_review: bool = False) -> dict:
        args = [
            "init", "-g", "ImplementationPlanAgent",
            "--task-dir", str(self.task_dir),
            "--version", "V_1.0",
            "--target-id", "P1-COMPILER-F01",
            "--requirement-analysis-revision", "REQAN-R04@7421b050ed44",
            "--business-model-revision", "BM-R04@1b19a0ba26b6",
            "--design-revision", "DESIGN-R04@abc123abc123",
        ]
        if architecture_review:
            args.append("--architecture-review")
        if minimal:
            args.append("--minimal")
        else:
            args += ["--test-design-revision", "TESTDESIGN-R04@def456def456"]
        code, _, error = self.run_cli(*args)
        self.assertEqual(0, code, error)
        return json.loads((self.task_dir / task_plan.PLAN_FILENAME).read_text(encoding="utf-8"))

    def test_init_records_architecture_review_mode(self) -> None:
        plan = self.init_plan(architecture_review=True)
        self.assertEqual(1, plan["implementation_strategy_contract_version"])
        self.assertTrue(plan["work_mode"]["architecture_review"])
        self.assertFalse(plan["work_mode"]["minimal"])

    def valid_task(self, *, reviewers: list[str] | None = None) -> dict:
        return {
            "task_id": "TASK-P1-COMPILER-F01-DEV-001",
            "title": "实现统一文档输入契约",
            "goal": "将 XML 与 YAML 输入转换为同一种 CanonicalDocumentNode，并保留稳定 SourceRef。",
            "implementation": {
                "owner_module": "dec-core-compiler",
                "owner_component": "frontend",
                "steps": [
                    "定义 DocumentSource、CanonicalDocumentNode 和 SourceRef 接口",
                    "实现 XML 与 YAML 安全解析并输出统一节点结构",
                ],
                "affected_files": ["dec-core-compiler/src/main/java/**", "dec-core-compiler/src/test/java/**"],
                "forbidden_changes": ["不得让编译结果持有 DOM 或 YAML Node"],
                "implementation_decision_ref": "IMPL-DEC-COMPILER-CANONICAL-INPUT",
                "strategy": "MODIFY",
                "existing_code_refs": ["dec-core-compiler/src/main/java/Compiler.java#compile"],
                "shared_logic_disposition": "EXTRACT_EXISTING",
                "shared_logic_owner": "dec-core-compiler/CanonicalInputParser",
                "compatibility_verifications": ["运行 XML 旧调用回归与 XML/YAML 新路径对等测试"],
                "source_doc_refs": ["project_doc/version/V_1.0/doc/COMPILER/COMPILER_design.md#implementation-strategy-decisions"],
            },
            "acceptance_criteria": [
                {
                    "id": "AC-TASK-P1-COMPILER-001",
                    "assertion": "同义 XML 与 YAML 必须生成一致的 CanonicalDocumentNode，非法输入必须返回稳定 Diagnostic。",
                }
            ],
            "depends_on": [],
            "owner_agent": "DevelopAgent",
            "reviewer_agents": reviewers or list(task_plan.STANDARD_REVIEWERS),
            "validation_commands": ["./mvnw -pl dec-core-compiler test"],
            "expected_results": ["构建通过且 XML/YAML 对等契约测试全部通过"],
            "deliverables": ["统一前端接口", "XML/YAML 契约测试"],
            "stop_conditions": ["需要改变已冻结的 DESIGN-R04 语义"],
            "trace_ids": ["TR-P1-COMPILER-001"],
            "flow_refs": [],
            "flow_step_refs": [],
            "risk_triggers": [],
            "git_checkpoint_required": True,
        }

    def write_tasks(self, tasks: list[dict]) -> None:
        plan = json.loads((self.task_dir / task_plan.PLAN_FILENAME).read_text(encoding="utf-8"))
        plan["tasks"] = tasks
        (self.task_dir / task_plan.PLAN_FILENAME).write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def pass_all_reviews(self) -> None:
        while True:
            plan = task_plan.load_plan(self.task_dir)
            reviews = task_plan.load_reviews(self.task_dir)
            assignment = task_plan.next_assignment(plan, reviews)
            if not assignment:
                return
            reviewer, _ = assignment
            code, _, error = self.run_cli(
                "submit-review", "-g", reviewer,
                "--task-dir", str(self.task_dir),
                "--result", "PASSED",
                "--summary", f"{reviewer} 确认任务目标、实现方式和验收标准可执行",
            )
            self.assertEqual(0, code, error)

    def test_standard_plan_review_and_finalize(self) -> None:
        self.init_plan()
        self.write_tasks([self.valid_task()])
        code, output, error = self.run_cli("seal", "-g", "ImplementationPlanAgent", "--task-dir", str(self.task_dir))
        self.assertEqual(0, code, error)
        self.assertIn("PlanReviewAgent", output)
        self.pass_all_reviews()
        code, output, error = self.run_cli("finalize", "-g", "ProjectManagerAgent", "--task-dir", str(self.task_dir))
        self.assertEqual(0, code, error)
        self.assertEqual("PASSED", json.loads(output)["status"])
        plan = task_plan.load_plan(self.task_dir)
        self.assertRegex(plan["revision"], r"^TP-P1-COMPILER-F01-R01@[0-9a-f]{12}$")
        self.assertEqual("PASSED", plan["status"])

    def test_query_returns_bounded_index_and_selected_task_fields(self) -> None:
        self.init_plan()
        self.write_tasks([self.valid_task()])
        code, output, error = self.run_cli(
            "query", "-g", "ProjectManagerAgent", "--task-dir", str(self.task_dir)
        )
        self.assertEqual(0, code, error)
        index = json.loads(output)
        self.assertEqual("PASSED", index["status"])
        self.assertEqual(1, index["taskCount"])
        self.assertEqual("TASK-P1-COMPILER-F01-DEV-001", index["tasks"][0]["task_id"])
        self.assertNotIn("implementation", index["tasks"][0])
        code, output, error = self.run_cli(
            "query", "-g", "ProjectManagerAgent", "--task-dir", str(self.task_dir),
            "--task-id", "TASK-P1-COMPILER-F01-DEV-001", "--field", "implementation",
            "--field", "acceptance_criteria",
        )
        self.assertEqual(0, code, error)
        selected = json.loads(output)
        self.assertEqual(["implementation", "acceptance_criteria"], selected["selectedFields"])
        self.assertIn("implementation", selected["task"])
        self.assertIn("acceptance_criteria", selected["task"])

    def test_query_rejects_unbounded_output(self) -> None:
        self.init_plan()
        self.write_tasks([self.valid_task()])
        code, _, error = self.run_cli(
            "query", "-g", "ProjectManagerAgent", "--task-dir", str(self.task_dir),
            "--task-id", "TASK-P1-COMPILER-F01-DEV-001", "--field", "implementation",
            "--max-bytes", "1",
        )
        self.assertEqual(1, code)
        self.assertIn("above limit", error)

    def test_project_manager_cannot_author_or_revise_task_plan(self) -> None:
        code, _, error = self.run_cli(
            "init", "-g", "ProjectManagerAgent",
            "--task-dir", str(self.task_dir),
            "--version", "V_1.0",
            "--target-id", "P1-COMPILER-F01",
            "--requirement-analysis-revision", "REQAN-R04@7421b050ed44",
            "--business-model-revision", "BM-R04@1b19a0ba26b6",
            "--design-revision", "DESIGN-R04@abc123abc123",
            "--test-design-revision", "TESTDESIGN-R04@def456def456",
        )
        self.assertEqual(1, code)
        self.assertIn("not authorized", error)

    def test_review_finding_requires_revision_and_full_rereview(self) -> None:
        self.init_plan()
        self.write_tasks([self.valid_task()])
        self.assertEqual(0, self.run_cli("seal", "-g", "ImplementationPlanAgent", "--task-dir", str(self.task_dir))[0])
        finding_file = self.task_dir / "findings.json"
        finding_file.write_text(json.dumps([{
            "severity": "P1",
            "task_id": "TASK-P1-COMPILER-F01-DEV-001",
            "field": "implementation.steps",
            "description": "缺少 SourceRef 失败路径",
            "required_change": "增加 SourceRef 定位与稳定 Diagnostic 的实现步骤",
        }], ensure_ascii=False), encoding="utf-8")
        code, _, error = self.run_cli(
            "submit-review", "-g", "PlanReviewAgent", "--task-dir", str(self.task_dir),
            "--result", "NEEDS_CHANGES", "--findings-file", str(finding_file),
            "--summary", "任务需要补充失败路径",
        )
        self.assertEqual(0, code, error)
        code, _, _ = self.run_cli(
            "submit-review", "-g", "ArchitectureReviewAgent", "--task-dir", str(self.task_dir),
            "--result", "PASSED", "--summary", "不应执行",
        )
        self.assertEqual(1, code)
        code, _, error = self.run_cli(
            "revise", "-g", "ImplementationPlanAgent", "--task-dir", str(self.task_dir),
            "--reason", "未修改任务内容的空返修",
        )
        self.assertEqual(1, code)
        self.assertIn("actual task-plan content changes", error)
        plan = task_plan.load_plan(self.task_dir)
        plan["tasks"][0]["implementation"]["steps"].append("为解析失败建立带文件、行列和节点路径的稳定 SourceRef Diagnostic")
        (self.task_dir / task_plan.PLAN_FILENAME).write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        code, output, error = self.run_cli(
            "revise", "-g", "ImplementationPlanAgent", "--task-dir", str(self.task_dir),
            "--reason", "根据 PlanReviewAgent finding 补充失败路径",
        )
        self.assertEqual(0, code, error)
        payload = json.loads(output)
        self.assertRegex(payload["revision"], r"-R02@")
        self.assertEqual("PlanReviewAgent", payload["nextReviewer"])
        self.pass_all_reviews()
        self.assertEqual(0, self.run_cli("finalize", "-g", "ProjectManagerAgent", "--task-dir", str(self.task_dir))[0])
        reviews = task_plan.load_reviews(self.task_dir)
        revisions = {record["plan_revision"] for record in reviews}
        self.assertEqual(2, len(revisions))

    def test_missing_goal_implementation_or_acceptance_is_rejected(self) -> None:
        self.init_plan()
        task = self.valid_task()
        task["goal"] = "TODO"
        task["implementation"]["steps"] = []
        task["acceptance_criteria"] = []
        self.write_tasks([task])
        code, output, _ = self.run_cli("validate", "-g", "ImplementationPlanAgent", "--task-dir", str(self.task_dir))
        self.assertEqual(1, code)
        errors = json.loads(output)["errors"]
        self.assertTrue(any("goal" in error for error in errors))
        self.assertTrue(any("implementation.steps" in error for error in errors))
        self.assertTrue(any("acceptance_criteria" in error for error in errors))

    def test_current_plan_requires_implementation_decision_fields(self) -> None:
        self.init_plan()
        task = self.valid_task()
        task["implementation"].pop("implementation_decision_ref")
        task["implementation"]["strategy"] = "COPY_AND_TWEAK"
        self.write_tasks([task])
        code, output, _ = self.run_cli("validate", "-g", "ImplementationPlanAgent", "--task-dir", str(self.task_dir))
        self.assertEqual(1, code)
        errors = json.loads(output)["errors"]
        self.assertTrue(any("implementation_decision_ref" in error for error in errors))
        self.assertTrue(any("strategy must be one of" in error for error in errors))

    def test_minimal_mode_suppresses_test_design_reviewer(self) -> None:
        plan = self.init_plan(minimal=True)
        self.assertEqual(task_plan.MINIMAL_REVIEWERS, plan["required_reviewers"])
        self.write_tasks([self.valid_task(reviewers=list(task_plan.MINIMAL_REVIEWERS))])
        self.assertEqual(0, self.run_cli("seal", "-g", "ImplementationPlanAgent", "--task-dir", str(self.task_dir))[0])
        self.pass_all_reviews()
        reviewers = [record["reviewer_agent"] for record in task_plan.load_reviews(self.task_dir)]
        self.assertNotIn("TestDesignAgent", reviewers)

    def test_old_dependency_alias_and_p3_finding_are_rejected(self) -> None:
        self.init_plan()
        task = self.valid_task()
        task["dependencies"] = task.pop("depends_on")
        self.write_tasks([task])
        code, output, _ = self.run_cli("validate", "-g", "ImplementationPlanAgent", "--task-dir", str(self.task_dir))
        self.assertEqual(1, code)
        self.assertTrue(any("dependencies" in error for error in json.loads(output)["errors"]))

        task = self.valid_task()
        self.write_tasks([task])
        self.assertEqual(0, self.run_cli("seal", "-g", "ImplementationPlanAgent", "--task-dir", str(self.task_dir))[0])
        finding_file = self.task_dir / "p3-findings.json"
        finding_file.write_text(json.dumps([{
            "severity": "P3",
            "task_id": task["task_id"],
            "field": "goal",
            "description": "不应允许未登记严重级别",
            "required_change": "改用 P0/P1/P2",
        }], ensure_ascii=False), encoding="utf-8")
        code, _, error = self.run_cli(
            "submit-review", "-g", "PlanReviewAgent", "--task-dir", str(self.task_dir),
            "--result", "NEEDS_CHANGES", "--findings-file", str(finding_file),
            "--summary", "严重级别契约检查",
        )
        self.assertEqual(1, code)
        self.assertIn("severity is invalid", error)

    def test_additional_task_reviewer_gets_only_assigned_tasks(self) -> None:
        self.init_plan()
        first = self.valid_task(reviewers=[*task_plan.STANDARD_REVIEWERS, "SecurityReviewAgent"])
        second = self.valid_task()
        second["task_id"] = "TASK-P1-COMPILER-F01-DEV-002"
        second["title"] = "实现确定性 SourceGraph"
        second["goal"] = "发现全部受控输入文件并生成稳定、可追踪且无路径逃逸的 MixSourceGraph。"
        second["acceptance_criteria"][0]["id"] = "AC-TASK-P1-COMPILER-002"
        second["trace_ids"] = ["TR-P1-COMPILER-002"]
        second["depends_on"] = [first["task_id"]]
        self.write_tasks([first, second])
        self.assertEqual(0, self.run_cli("seal", "-g", "ImplementationPlanAgent", "--task-dir", str(self.task_dir))[0])
        self.pass_all_reviews()
        security = [record for record in task_plan.load_reviews(self.task_dir) if record["reviewer_agent"] == "SecurityReviewAgent"]
        self.assertEqual(1, len(security))
        self.assertEqual([first["task_id"]], security[0]["task_ids"])


if __name__ == "__main__":
    unittest.main()
