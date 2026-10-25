import json
import tempfile
import unittest
from pathlib import Path

import long_task
import work_record


class WorkRecordV5Test(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "work_record.md"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def event(self) -> dict:
        return {
            "event_id": "EVENT-ATTEMPT-TASK-001-I001-A001-PASSED",
            "timestamp": "2026-07-25T10:00:00+08:00",
            "event_type": "TASK_ATTEMPT_COMPLETED",
            "status": "PASSED",
            "agent": "DevelopAgent",
            "source": "long_task.py finish-attempt",
            "version": "V_TEST",
            "target_id": "TARGET-001",
            "scope": "实现测试任务",
            "phase": "development",
            "task_type": "development",
            "execution_mode": "standard / sequential",
            "task_id": "TASK-001",
            "attempt_id": "ATTEMPT-TASK-001-I001-A001",
            "iteration_id": "ITER-TARGET-001-DEVELOPMENT-001",
            "iteration_no": 1,
            "input_revision": "TDD-R01@input",
            "output_revision": "DEV-R01@output",
            "evidence_ids": ["EVD-000001"],
            "summary": "实现完成",
            "state_change": "TASK-001: RUNNING → PASSED",
            "sql_change_summary": "无",
            "validation_summary": "单元测试 3/3 通过",
            "modified_files_summary": ["src/main.py"],
            "next_action": "进入代码 Review",
            "next_agent": "ProjectManagerAgent",
        }

    def validate(self):
        return work_record.validate_document(
            self.path,
            allowed_fields=long_task.WORK_EVENT_FIELDS,
            known_agents=long_task.KNOWN_AGENTS,
            statuses=long_task.WORK_EVENT_STATUSES,
        )

    def test_markdown_is_human_readable_and_machine_validated(self) -> None:
        self.path.write_text(work_record.render_document("V_TEST", [self.event()]), encoding="utf-8")
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("<!-- managed-by: common-develop/work-record-v5 -->", text)
        self.assertIn("### 基本信息", text)
        self.assertIn("| 执行 Agent | DevelopAgent |", text)
        self.assertIn("work-record-meta:", text)
        errors, events = self.validate()
        self.assertEqual([], errors)
        self.assertEqual("TASK-001", events[0]["task_id"])
        self.assertEqual("v5-markdown", work_record.format_name(text))

    def test_append_rejects_duplicate_event(self) -> None:
        self.path.write_text(work_record.render_document("V_TEST", []), encoding="utf-8")
        updated, events = work_record.append_event(self.path, self.event(), version="V_TEST")
        self.path.write_text(updated, encoding="utf-8")
        self.assertEqual(1, len(events))
        self.assertEqual([], self.validate()[0])
        with self.assertRaisesRegex(ValueError, "duplicate work event id"):
            work_record.append_event(self.path, self.event(), version="V_TEST")

    def test_visible_body_tampering_is_rejected(self) -> None:
        text = work_record.render_document("V_TEST", [self.event()])
        self.path.write_text(text.replace("实现完成", "伪造完成", 1), encoding="utf-8")
        errors, _ = self.validate()
        self.assertTrue(any("digest" in error or "canonical" in error for error in errors))
    def test_noncanonical_formats_fail_closed_without_mutation(self) -> None:
        samples = {
            "work_record.jsonl": work_record.canonical_line(self.event(), version="V_TEST") + "\n",
            "noncanonical_work_record.md": "# V_TEST 工作记录\n\n<!-- managed-by: other-format -->\n",
            "unstructured_record.md": "# V_TEST 工作记录\n\n## WR-INVALID\n",
        }
        for name, text in samples.items():
            path = Path(self.temp.name) / name; path.write_text(text, encoding="utf-8"); before=path.read_bytes()
            with self.assertRaisesRegex(ValueError, "work record does not use the current format"):
                work_record.read_events(path)
            self.assertEqual(before, path.read_bytes())


if __name__ == "__main__":
    unittest.main()
