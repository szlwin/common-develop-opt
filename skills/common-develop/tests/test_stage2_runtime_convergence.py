#!/usr/bin/env python3
"""Focused regressions for Stage 2 lifecycle, permission, and runtime convergence."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import long_task
import request_intake
import review_session
import wk
from _long_task import record_io
from common_utils import atomic_write_bytes
from error_catalog import RequestIntakeError


class Stage2RuntimeConvergenceTests(unittest.TestCase):
    def make_task(self, root: Path) -> Path:
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.command_init(argparse.Namespace(
                doc_root=str(root), version="V_TEST", target="TARGET", reuse=False,
                agent="ProjectManagerAgent",
            ))
        return root / "version" / "V_TEST" / "task" / "TARGET"

    def test_delivery_input_errors_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            missing = root / "missing.json"
            args = wk.build_parser().parse_args([
                "delivery", "-g", "ProjectManagerAgent", "--action", "verify",
                "--plan", str(missing),
            ])
            with self.assertRaises(wk.WKError) as caught:
                wk.command_delivery(args)
            self.assertEqual("INPUT_NOT_FOUND", caught.exception.code)

            invalid = root / "invalid.json"
            invalid.write_text("{not-json", encoding="utf-8")
            args.plan = str(invalid)
            with self.assertRaises(wk.WKError) as caught:
                wk.command_delivery(args)
            self.assertEqual("INPUT_INVALID_JSON", caught.exception.code)

            scalar = root / "scalar.json"
            scalar.write_text("[]", encoding="utf-8")
            args.plan = str(scalar)
            with self.assertRaises(wk.WKError) as caught:
                wk.command_delivery(args)
            self.assertEqual("INPUT_SCHEMA_INVALID", caught.exception.code)

    def test_delivery_results_input_errors_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = root / "plan.json"
            plan.write_text("{}", encoding="utf-8")
            args = wk.build_parser().parse_args([
                "delivery", "-g", "ProjectManagerAgent", "--action", "verify",
                "--plan", str(plan), "--results", str(root / "missing-results.json"),
            ])
            with self.assertRaises(wk.WKError) as caught:
                wk.command_delivery(args)
            self.assertEqual("INPUT_NOT_FOUND", caught.exception.code)

    def test_jsonschema_dependency_missing_fails_closed(self) -> None:
        document = {"schemaVersion": 1}
        real_import = __import__

        def reject_jsonschema(name, *args, **kwargs):
            if name == "jsonschema":
                raise ImportError("simulated missing dependency")
            return real_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=reject_jsonschema):
            with self.assertRaises(RequestIntakeError) as caught:
                request_intake.validate_document(document)
        self.assertEqual("JSONSCHEMA_DEPENDENCY_MISSING", caught.exception.code)

    def test_status_surfaces_corrupt_work_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir = self.make_task(Path(temp) / "project_doc")
            work_path = task_dir.parents[1] / "work.md"
            work_path.write_text("not a valid work-mode document\n", encoding="utf-8")
            payload = wk._status_payload(task_dir)
            self.assertEqual("INVALID", payload["status"])
            self.assertEqual("INVALID", payload["workMode"]["status"])
            self.assertTrue(payload["workMode"]["errorCode"])
            self.assertTrue(payload["errors"])

    def test_state_cache_returns_isolated_reducer_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir = self.make_task(Path(temp) / "project_doc")
            _document, first = long_task.read_task_state_cache(task_dir)
            first["work_mode"]["auto"] = True
            first["artifact_revisions"]["design"]["revision"] = "MUTATED"
            second = long_task.runtime_state(task_dir)
            self.assertFalse(second["work_mode"]["auto"])
            self.assertNotEqual("MUTATED", second["artifact_revisions"]["design"]["revision"])

    def test_atomic_write_bytes_replaces_complete_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "nested" / "record.json"
            atomic_write_bytes(target, b'{"status":"complete"}\n')
            self.assertEqual(b'{"status":"complete"}\n', target.read_bytes())
            self.assertFalse(any(target.parent.glob(f".{target.name}.*.tmp")))

    def test_transaction_keeps_original_error_and_structured_rollback_details(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first.json"
            second = Path(temp) / "second.json"
            first.write_text("old-first", encoding="utf-8")
            second.write_text("old-second", encoding="utf-8")
            original = OSError("simulated replacement failure")
            calls = 0
            real_replace = record_io.os.replace

            def replace(source, target):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise original
                return real_replace(source, target)

            with mock.patch.object(record_io.os, "replace", side_effect=replace):
                with self.assertRaises(OSError) as caught:
                    record_io.transactional_text_write({first: "new-first", second: "new-second"})
            self.assertIs(original, caught.exception)
            self.assertEqual("COMPLETE", caught.exception.rollback_details["rollbackStatus"])
            self.assertEqual("old-first", first.read_text(encoding="utf-8"))
            self.assertEqual("old-second", second.read_text(encoding="utf-8"))

    def test_review_session_is_explicitly_advisory(self) -> None:
        session = review_session.plan_session(
            phase="code_review", revision="R-STAGE2", explicit_risks=[],
            risk_report=None, impact_plan=None,
        )
        self.assertTrue(session["advisory"])
        self.assertEqual("NOT_REQUIRED", session["lifecycleGate"])
        self.assertEqual([], review_session.validate_session(session))

    def test_minimal_mode_suppresses_declared_reviewers(self) -> None:
        standard = review_session.plan_session(
            phase="requirement_analysis", revision="R-STAGE2", explicit_risks=[],
            risk_report=None, impact_plan=None, mode="standard",
        )
        minimal = review_session.plan_session(
            phase="requirement_analysis", revision="R-STAGE2", explicit_risks=[],
            risk_report=None, impact_plan=None, mode="minimal",
        )
        standard_reviewers = {item["reviewerAgent"] for item in standard["axes"]}
        minimal_reviewers = {item["reviewerAgent"] for item in minimal["axes"]}
        self.assertTrue(standard_reviewers - minimal_reviewers)
        self.assertEqual([], review_session.validate_session(minimal))

    def test_query_commands_are_read_only_in_runtime_contract(self) -> None:
        import agent_context
        mutability = agent_context.RUNTIME_CONTRACT["scriptCommandSurfaces"]["mutability"]
        for command in (
            "evidence.query", "evidence.show", "evidence.validate", "evidence.status",
            "long_task.task-context", "project_environment.status",
        ):
            self.assertEqual("READ_ONLY", mutability[command])


if __name__ == "__main__":
    unittest.main()
