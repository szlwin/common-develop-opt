#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import auto_remediation
import execution_evidence
import project_environment
import wk
import work_mode


class LifecycleModeTests(unittest.TestCase):
    def _start_args(self, root: Path, *, review_only: bool = False) -> list[str]:
        args = [
            "start", "-g", "ProjectManagerAgent", "--doc-root", str(root), "--version", "V_TEST",
            "-f", "文案微调", "--classification", "simple-optimization", "--related-feature", "F01",
        ]
        for flag in (
            "business-logic", "data-model", "api-contract", "permission", "workflow",
            "persistence", "external-side-effect", "migration", "dependency",
        ):
            args += [f"--{flag}-impact", "NONE"]
        if review_only:
            args.append("-review-only")
        args.append("--apply")
        return args

    def _start(self, root: Path, *, review_only: bool = False) -> Path:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(0, wk.main(self._start_args(root, review_only=review_only)))
        payload = json.loads(out.getvalue()[out.getvalue().index("{"):])
        return Path(payload["taskDir"])

    def test_review_only_is_persisted_and_blocks_production(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir = self._start(Path(temp) / "project_doc", review_only=True)
            mode = work_mode.validate_work_file(task_dir.parent.parent / "work.md")
            self.assertTrue(mode.review_only)
            self.assertFalse(work_mode.mutation_decision(mode, "development", "productionCode")["allowed"])
            self.assertTrue(work_mode.mutation_decision(mode, "development", "projectDocs")["allowed"])

    def test_reconcile_preview_is_non_mutating(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir = self._start(Path(temp) / "project_doc")
            ledger = task_dir / "task_events.jsonl"
            before = ledger.read_bytes()
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(0, wk.main([
                    "reconcile", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir)
                ]))
            payload = json.loads(out.getvalue())
            self.assertEqual("PREVIEW", payload["status"])
            self.assertFalse(payload["apply"])
            self.assertEqual(before, ledger.read_bytes())

    def test_next_explain_returns_lifecycle_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir = self._start(Path(temp) / "project_doc")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(0, wk.main([
                    "next", "-g", "ProjectManagerAgent", "--task-dir", str(task_dir), "--explain"
                ]))
            payload = json.loads(out.getvalue())
            self.assertIn("decision", payload)
            self.assertEqual("development", payload["decision"]["currentPhase"])
            self.assertIn(payload["decision"]["classification"], {"EXECUTION_REQUIRED", "PUBLISH_READY"})

    def test_auto_preflight_enforces_review_only_mutation_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            task_dir = self._start(Path(temp) / "project_doc", review_only=True)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = auto_remediation.main([
                    "preflight", "-g", "ProjectManagerAgent",
                    "--authority-current", "true", "--test-oracle-authoritative", "true",
                    "--fixture-representative", "true", "--configuration-semantically-valid", "true",
                    "--environment-stable", "true", "--task-dir", str(task_dir), "--json",
                ])
            self.assertEqual(2, rc)
            payload = json.loads(out.getvalue())
            self.assertFalse(payload["productionMutationAllowed"])
            self.assertEqual("DENY", payload["mutationPolicy"]["decision"])

    def test_generic_execution_evidence_is_technology_neutral(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            task_dir = self._start(root / "project_doc")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = execution_evidence.main([
                    "run", "-g", "DevelopAgent", "--task-dir", str(task_dir),
                    "--phase", "development", "--revision", "DEV-R01@fixture",
                    "--command", "printf hello", "--cwd", str(root),
                    "--mutation-scope", "taskFacts", "--json",
                ])
            self.assertEqual(0, rc)
            payload = json.loads(out.getvalue())
            self.assertEqual("PASSED", payload["status"])
            command_result = json.loads((task_dir / payload["commandResultRef"]).read_text(encoding="utf-8"))
            self.assertEqual("command_result", command_result["kind"])
            self.assertEqual("printf hello", command_result["command"])
            self.assertEqual("hello", command_result["output"])


class EnvironmentContractTests(unittest.TestCase):
    def test_capabilities_are_read_from_existing_env_docs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "project_env.md").write_text(
                "# Environment\n\n```yaml common-develop-capabilities\ncapabilities:\n  build:\n    available: true\n    command: ./build.sh\n```\n",
                encoding="utf-8",
            )
            (root / "harness_env.md").write_text(
                "# Harness\n\n```json common-develop-capabilities\n{\"capabilities\":{\"integrationValidation\":{\"available\":false,\"reason\":\"not configured\"}}}\n```\n",
                encoding="utf-8",
            )
            payload = project_environment.project_capabilities(root)
            self.assertEqual("PASSED", payload["status"])
            self.assertIn("build", payload["availableCapabilities"])
            self.assertIn("integrationValidation", payload["unavailableCapabilities"])
            self.assertEqual("./build.sh", payload["capabilities"]["build"]["command"])


if __name__ == "__main__":
    unittest.main()
