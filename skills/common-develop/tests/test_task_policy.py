#!/usr/bin/env python3
"""Regression tests for governed runtime max_attempts policies."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import long_task
from _long_task import task_events
from error_catalog import LifecycleError


class TaskPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.doc_root = Path(self.temp.name) / "project_doc"
        args = argparse.Namespace(
            doc_root=str(self.doc_root),
            version="V_TEST",
            target="MOD-POLICY",
            reuse=False,
            request_intake="",
            agent="ProjectManagerAgent",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, long_task.command_init(args))
        self.task_dir = self.doc_root / "version" / "V_TEST" / "task" / "MOD-POLICY"
        self.task_id = "TASK-001"
        self.iteration_id = "ITER-TASK-001"
        self.static_task = {
            "id": self.task_id,
            "title": "policy test",
            "objective": "test runtime policy",
            "phase": "development",
            "depends_on": [],
            "owner_agent": "DevelopAgent",
            "reviewer_agents": [],
            "input_revisions": {},
            "allowed_files": [],
            "acceptance_trace_ids": [],
            "flow_step_refs": [],
            "flow_refs": [],
            "validation_commands": [],
            "expected_results": [],
            "stop_conditions": [],
            "max_attempts": 3,
            "logical_task_id": "LOGICAL-001",
            "feature_id": "FEATURE-001",
            "iteration_id": self.iteration_id,
            "iteration_no": 1,
            "supersedes_iteration_id": "",
            "revision_reason": "initial",
            "risk_triggers": [],
        }
        (self.task_dir / "task_plan.json").write_text(
            json.dumps({"tasks": [self.static_task]}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_policy(self, *, value: int, agent: str = "ProjectManagerAgent", reason: str = "test policy") -> tuple[int, str]:
        args = argparse.Namespace(
            task_dir=str(self.task_dir),
            task_id=self.task_id,
            max_attempts=value,
            reason=reason,
            agent=agent,
            json=False,
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = long_task.command_set_task_policy(args)
        return code, output.getvalue()

    def write_events(self, specs: list[dict]) -> None:
        text, _created, _events = task_events.append_many_text(self.task_dir, specs)
        task_events.event_path(self.task_dir).write_text(text, encoding="utf-8")

    def materialized_task(self) -> dict:
        history = task_events.reduce_events(task_events.read_events(self.task_dir))
        return task_events.materialize_tasks([self.static_task], history)[0]

    def test_set_policy_is_audited_and_materialized(self) -> None:
        code, output = self.run_policy(value=5, reason="increase retry budget")
        self.assertEqual(0, code)
        payload = json.loads(output)
        self.assertEqual({"before": 3, "after": 5, "changed": True}, {key: payload[key] for key in ("before", "after", "changed")})
        event = task_events.read_events(self.task_dir)[-1]
        self.assertEqual("TASK_POLICY_SET", event["type"])
        self.assertEqual(
            {"task_id": self.task_id, "iteration_id": self.iteration_id, "before": 3, "after": 5,
             "reason": "increase retry budget", "agent": "ProjectManagerAgent"},
            event["payload"]["policy"],
        )
        task = self.materialized_task()
        self.assertEqual(3, task["declared_max_attempts"])
        self.assertEqual(5, task["effective_max_attempts"])
        self.assertEqual(5, task["max_attempts"])
        self.assertEqual("runtime_policy", task["max_attempts_source"])

    def test_same_value_is_idempotent_and_update_records_before_value(self) -> None:
        self.run_policy(value=5)
        count = len(task_events.read_events(self.task_dir))
        code, output = self.run_policy(value=5)
        self.assertEqual(0, code)
        self.assertFalse(json.loads(output)["changed"])
        self.assertEqual(count, len(task_events.read_events(self.task_dir)))
        self.run_policy(value=7, reason="one more retry")
        policy = task_events.reduce_events(task_events.read_events(self.task_dir))["task_policies"][self.task_id][self.iteration_id]
        self.assertEqual(5, policy["before"])
        self.assertEqual(7, policy["after"])

    def test_clear_policy_restores_static_plan(self) -> None:
        self.run_policy(value=5)
        code, output = self.run_policy(value=0, reason="restore plan default")
        self.assertEqual(0, code)
        self.assertEqual("static_plan", json.loads(output)["source"])
        task = self.materialized_task()
        self.assertEqual(3, task["max_attempts"])
        self.assertEqual(3, task["effective_max_attempts"])
        self.assertEqual("static_plan", task["max_attempts_source"])

    def test_clear_without_override_is_idempotent(self) -> None:
        before = len(task_events.read_events(self.task_dir))
        code, output = self.run_policy(value=0, reason="no override to clear")
        self.assertEqual(0, code)
        payload = json.loads(output)
        self.assertFalse(payload["changed"])
        self.assertEqual(3, payload["before"])
        self.assertEqual(3, payload["effectiveMaxAttempts"])
        self.assertEqual(before, len(task_events.read_events(self.task_dir)))

    def test_clear_tombstone_does_not_break_followup_static_value(self) -> None:
        self.run_policy(value=5)
        self.run_policy(value=0, reason="restore plan default")
        code, output = self.run_policy(value=3, reason="keep plan default")
        self.assertEqual(0, code)
        self.assertFalse(json.loads(output)["changed"])
        policies = task_events.reduce_events(task_events.read_events(self.task_dir))["task_policies"]
        self.assertEqual(0, policies[self.task_id][self.iteration_id]["after"])

    def test_clear_cannot_restore_static_limit_at_current_attempt_count(self) -> None:
        self.run_policy(value=5)
        self.write_events([
            {
                "type": "ATTEMPT_STARTED",
                "entity_id": "ATTEMPT-001",
                "timestamp": "2026-08-29T00:00:00Z",
                "source": "test",
                "payload": {"attempt": {
                    "attempt_id": "ATTEMPT-001", "task_id": self.task_id,
                    "iteration_id": self.iteration_id, "iteration_no": 1,
                    "attempt_no": 1, "status": "RUNNING",
                }},
            },
            {
                "type": "ATTEMPT_STARTED",
                "entity_id": "ATTEMPT-002",
                "timestamp": "2026-08-29T00:01:00Z",
                "source": "test",
                "payload": {"attempt": {
                    "attempt_id": "ATTEMPT-002", "task_id": self.task_id,
                    "iteration_id": self.iteration_id, "iteration_no": 1,
                    "attempt_no": 2, "status": "RUNNING",
                }},
            },
            {
                "type": "ATTEMPT_STARTED",
                "entity_id": "ATTEMPT-003",
                "timestamp": "2026-08-29T00:02:00Z",
                "source": "test",
                "payload": {"attempt": {
                    "attempt_id": "ATTEMPT-003", "task_id": self.task_id,
                    "iteration_id": self.iteration_id, "iteration_no": 1,
                    "attempt_no": 3, "status": "RUNNING",
                }},
            },
        ])
        with self.assertRaises(LifecycleError) as caught:
            self.run_policy(value=0, reason="restore plan default")
        self.assertEqual("LIFECYCLE_POLICY_INVALID", caught.exception.code)
        self.assertEqual(5, len(task_events.read_events(self.task_dir)))

    def test_policy_cannot_be_lowered_to_current_attempt_count(self) -> None:
        self.write_events([
            {
                "type": "ATTEMPT_STARTED",
                "entity_id": "ATTEMPT-001",
                "timestamp": "2026-08-29T00:00:00Z",
                "source": "test",
                "payload": {"attempt": {
                    "attempt_id": "ATTEMPT-001", "task_id": self.task_id,
                    "iteration_id": self.iteration_id, "iteration_no": 1,
                    "attempt_no": 1, "status": "RUNNING",
                }},
            },
        ])
        with self.assertRaises(LifecycleError) as caught:
            self.run_policy(value=1)
        self.assertEqual("LIFECYCLE_POLICY_INVALID", caught.exception.code)
        self.assertIn("当前 iteration", caught.exception.message)
        self.assertEqual(2, len(task_events.read_events(self.task_dir)))

    def test_non_project_manager_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.run_policy(value=5, agent="DevelopAgent")
        self.assertEqual(1, len(task_events.read_events(self.task_dir)))

    def test_policy_is_iteration_scoped(self) -> None:
        self.run_policy(value=5)
        self.write_events([
            {
                "type": "TASK_RUNTIME_SET",
                "entity_id": self.task_id,
                "timestamp": "2026-08-29T00:00:00Z",
                "source": "test reopen",
                "payload": {"runtime": {
                    "iteration_id": "ITER-TASK-002", "iteration_no": 2,
                    "status": "PENDING", "attempts": 0,
                }},
            },
        ])
        task = self.materialized_task()
        self.assertEqual("ITER-TASK-002", task["iteration_id"])
        self.assertEqual(3, task["max_attempts"])
        self.assertIsNone(task["policy_override"])

    def test_parser_and_help_expose_command(self) -> None:
        parser = long_task.build_parser()
        args = parser.parse_args([
            "set-task-policy", "--task-dir", str(self.task_dir), "--task-id", self.task_id,
            "--max-attempts", "5", "--reason", "test",
        ])
        self.assertEqual("set-task-policy", args.command)
        self.assertEqual(5, args.max_attempts)
        self.assertIn("set-task-policy", long_task.COMMAND_HELP_METADATA)


if __name__ == "__main__":
    unittest.main()
