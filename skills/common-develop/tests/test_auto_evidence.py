from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import long_task
from _long_task import task_events
from error_catalog import LifecycleError


class AutoEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.doc_root = Path(self.tmp.name) / "project_doc"
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.command_init(argparse.Namespace(
                doc_root=str(self.doc_root), version="V_TEST", target="FEAT001", reuse=False,
                agent="ProjectManagerAgent",
            ))
        self.task_dir = self.doc_root / "version" / "V_TEST" / "task" / "FEAT001"
        self._prepare_development_task()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _prepare_development_task(self) -> None:
        state_document, state = long_task.read_task_state_cache(self.task_dir)
        for phase in ("implementation_plan", "test_design"):
            artifact = state["artifact_revisions"][phase]
            revision = f"{phase}-R01"
            artifact.update({"status": "PASSED", "revision": revision})
            review = state.get("collaboration_reviews", {}).get(phase)
            if not isinstance(review, dict):
                continue
            review["status"] = "PASSED"
            review["artifact_revision"] = revision
            review["current_iteration_id"] = artifact["iteration_id"]
            conclusions = {}
            for reviewer in sorted(long_task.expected_required_reviewers(phase, state, minimal=False)):
                criteria = []
                profile = long_task.review_profile(phase, reviewer)
                for index, criterion_id in enumerate(profile.get("criterionIds") or [], 1):
                    criterion = long_task.REVIEW_CRITERIA[criterion_id]
                    policy = criterion.get("evidencePolicy") or {}
                    kinds = list(policy.get("allOfTypes") or [])
                    any_of = list(policy.get("anyOfTypes") or [])
                    if any_of and not any(kind in kinds for kind in any_of):
                        kinds.append(any_of[0])
                    while len(kinds) < int(policy.get("minimumEvidenceCount") or 1):
                        kinds.append(any_of[0] if any_of else "document_ref")
                    evidence_refs = []
                    for evidence_index, kind in enumerate(kinds, 1):
                        ref = self.task_dir / "evidence" / "setup" / f"{phase}-{reviewer}-{index}-{evidence_index}.txt"
                        ref.parent.mkdir(parents=True, exist_ok=True)
                        ref.write_text("setup evidence", encoding="utf-8")
                        command_result_ref = ""
                        register_ref = ref
                        if kind == "command_ref":
                            output = ref.with_suffix(".log")
                            output.write_text("setup command passed", encoding="utf-8")
                            result = ref.with_suffix(".json")
                            result.write_text(json.dumps({
                                "schema_version": 1, "kind": "command_result",
                                "command": "python -m unittest", "exit_code": 0,
                                "executed_at": "2026-08-29T00:00:00Z", "revision": revision,
                                "output_ref": str(output.relative_to(self.task_dir)),
                                "output_digest": long_task.sha256_file(output),
                            }), encoding="utf-8")
                            register_ref = result
                            command_result_ref = str(result.relative_to(self.task_dir))
                        entry = long_task.evidence.register_evidence(
                            self.task_dir, agent=reviewer, evidence_type=kind,
                            ref=str(register_ref.relative_to(self.task_dir)), revision=revision, phase=phase,
                            scope_refs=[criterion_id], command_result_ref=command_result_ref,
                        )
                        evidence_refs.append({"evidence_id": entry["evidence_id"], "claim": "setup evidence"})
                    criteria.append({
                        "criterion_id": criterion_id, "status": "PASSED",
                        "evidence": evidence_refs,
                        "finding_ids": [], "reason": "",
                    })
                conclusions[reviewer] = {
                    "profile_id": f"{phase}:{reviewer}", "revision": revision,
                    "conclusion": "PASSED", "checked_scope": [f"{phase}:{revision}"],
                    "criteria_results": criteria, "finding_ids": [], "limitations": [],
                    "reviewed_at": "2026-08-29T00:00:00Z",
                }
            review["independent_conclusions"] = conclusions
        state["current_phase"] = "development"
        state["next_agent"] = "DevelopAgent"
        state["next_action"] = "implement"
        payloads, _, _ = long_task.append_task_events_payloads(
            self.task_dir, long_task.task_state_path(self.task_dir), state_document, state,
            [], synced_at="2026-08-29T00:00:00Z",
        )
        long_task.transactional_text_write(payloads)
        _, state = long_task.read_task_state_cache(self.task_dir)
        task = {
            "id": "DEV-1", "logical_task_id": "LOGICAL-DEV-1", "feature_id": "F01",
            "iteration_id": state["artifact_revisions"]["development"]["iteration_id"],
            "iteration_no": state["artifact_revisions"]["development"]["iteration_no"],
            "supersedes_iteration_id": "", "revision_reason": "initial iteration",
            "title": "DEV-1", "objective": "observable result", "phase": "development",
            "status": "READY", "depends_on": [], "owner_agent": "DevelopAgent",
            "reviewer_agents": [], "input_revisions": {
                phase: state["artifact_revisions"][phase]["revision"]
                for phase in long_task.phase_predecessors("development", False)
            },
            "allowed_files": [], "acceptance_trace_ids": [], "flow_refs": [], "flow_step_refs": [],
            "validation_commands": [], "expected_results": ["pass"],
            "stop_conditions": ["scope drift"], "risk_triggers": [], "attempts": 0,
            "max_attempts": 2, "output_revision": "", "validation_evidence_ids": [],
        }
        path = self.task_dir / "task_plan.json"
        plan = long_task.storage.read_json(path, default={})
        plan["tasks"] = [task]
        path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.task = task

    def _set_validation_command(self, command: str) -> None:
        plan_path = self.task_dir / "task_plan.json"
        plan = long_task.storage.read_json(plan_path, default={})
        plan["tasks"][0]["validation_commands"] = [command]
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _start(self) -> str:
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.command_start_attempt(argparse.Namespace(
                task_dir=str(self.task_dir), task_id="DEV-1", input_revision="",
                summary="run", next_action="", agent="DevelopAgent",
            ))
        return long_task.load_task_history(self.task_dir)["attempts"][-1]["attempt_id"]

    def _finish_args(self, attempt_id: str, command: str) -> argparse.Namespace:
        return argparse.Namespace(
            task_dir=str(self.task_dir), attempt_id=attempt_id, status="PASSED",
            output_revision="DEV-R01", failure_type="", failure_reason="", modified_file=[],
            command_ref=[], evidence_ref=[], auto_evidence=True, check=command,
            summary="done", next_action="", next_agent="ProjectManagerAgent", agent="DevelopAgent",
        )

    def test_parser_accepts_auto_evidence_flags(self) -> None:
        parser = long_task.build_parser()
        args = parser.parse_args([
            "finish-attempt", "-g", "DevelopAgent", "--task-dir", str(self.task_dir),
            "--attempt-id", "A", "--status", "PASSED", "--output-revision", "DEV-R01",
            "--auto-evidence", "--check", "python -m unittest",
        ])
        self.assertTrue(args.auto_evidence)
        self.assertEqual("python -m unittest", args.check)

    def test_auto_evidence_requires_exactly_declared_command(self) -> None:
        attempt_id = self._start()
        command = "python3 -c \"print('not declared')\""
        self._set_validation_command("python3 -c \"print('declared')\"")
        with mock.patch("_long_task.command_evidence.subprocess.run") as run:
            with self.assertRaisesRegex(LifecycleError, "LIFECYCLE_AUTO_EVIDENCE_COMMAND_NOT_DECLARED"):
                long_task.command_finish_attempt(self._finish_args(attempt_id, command))
            run.assert_not_called()

    def test_auto_evidence_success_registers_real_result_and_finishes(self) -> None:
        command = "python3 -c \"print('auto evidence ok')\""
        self._set_validation_command(command)
        attempt_id = self._start()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, long_task.command_finish_attempt(self._finish_args(attempt_id, command)))
        attempts = long_task.load_task_history(self.task_dir)["attempts"]
        self.assertEqual("PASSED", attempts[-1]["status"])
        self.assertEqual(1, len(attempts[-1]["command_evidence_ids"]))
        evidence_id = attempts[-1]["command_evidence_ids"][0]
        self.assertIn(evidence_id, attempts[-1]["evidence_ids"])
        entry = long_task.evidence.require_evidence(
            self.task_dir, evidence_id, expected_revision="DEV-R01", allowed_types={"command_ref"}
        )
        result = long_task.command_result_payload(self.task_dir, entry["command_result_ref"])
        self.assertEqual(0, result["exit_code"])
        self.assertEqual(command, result["command"])
        self.assertIn("auto evidence ok", result["output"])
        self.assertEqual("", result["stderr"])
        self.assertEqual("development", result["metadata"]["phase"])
        self.assertEqual("DEV-R01", result["revision"])
        self.assertEqual(attempt_id, result["metadata"]["attempt_id"])

    def test_failed_auto_check_records_stdout_and_stderr_but_keeps_attempt_running(self) -> None:
        command = "python3 -c \"import sys; print('auto evidence failed'); print('validation stderr', file=sys.stderr); sys.exit(3)\""
        self._set_validation_command(command)
        attempt_id = self._start()
        with self.assertRaisesRegex(LifecycleError, "LIFECYCLE_AUTO_EVIDENCE_COMMAND_FAILED"):
            long_task.command_finish_attempt(self._finish_args(attempt_id, command))
        attempt = long_task.load_task_history(self.task_dir)["attempts"][-1]
        self.assertEqual("RUNNING", attempt["status"])
        registry_lines = (self.task_dir / "evidence" / "registry.jsonl").read_text(encoding="utf-8").splitlines()
        registry = [json.loads(line) for line in registry_lines if line.strip()]
        auto_entries = [
            entry for entry in registry
            if (entry.get("metadata") or {}).get("collector") == "finish-attempt-auto-evidence-v1"
        ]
        self.assertEqual(1, len(auto_entries))
        result = long_task.command_result_payload(self.task_dir, auto_entries[0]["command_result_ref"])
        self.assertEqual(3, result["exit_code"])
        self.assertIn("auto evidence failed", result["output"])
        self.assertEqual("validation stderr\n", result["stderr"])
        self.assertEqual(
            hashlib.sha256(result["stderr"].encode("utf-8")).hexdigest(),
            result["stderr_digest"],
        )

    def test_timeout_auto_check_records_timeout_audit_and_keeps_attempt_running(self) -> None:
        command = "printf 'before-timeout\\n'; sleep 2; printf 'after-timeout\\n'"
        self._set_validation_command(command)
        attempt_id = self._start()
        args = self._finish_args(attempt_id, command)
        args.timeout = 1
        with self.assertRaisesRegex(LifecycleError, "LIFECYCLE_AUTO_EVIDENCE_COMMAND_TIMEOUT"):
            long_task.command_finish_attempt(args)

        attempt = long_task.load_task_history(self.task_dir)["attempts"][-1]
        self.assertEqual("RUNNING", attempt["status"])
        audit = attempt["last_command_audit"]
        self.assertEqual("COMMAND_TIMEOUT", audit["error_code"])
        self.assertEqual(124, audit["exit_code"])
        self.assertEqual(1, audit["timeout_seconds"])
        self.assertTrue(audit["timed_out"])
        self.assertGreaterEqual(audit["duration_ms"], 900)

        events = task_events.read_events(self.task_dir)
        self.assertEqual("ATTEMPT_COMMAND_RECORDED", events[-1]["type"])
        result = long_task.command_result_payload(self.task_dir, audit["command_result_ref"])
        self.assertEqual("COMMAND_TIMEOUT", result["error_code"])
        self.assertEqual(1, result["timeout_seconds"])
        self.assertGreaterEqual(result["duration_ms"], 900)
        self.assertEqual("before-timeout\n", result["output"])
        self.assertEqual(124, result["exit_code"])

    def test_large_auto_evidence_streams_are_readable_and_verified(self) -> None:
        command = "python3 -c \"print('x' * 20000); print('stderr' * 4000, file=__import__('sys').stderr)\""
        self._set_validation_command(command)
        attempt_id = self._start()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, long_task.command_finish_attempt(self._finish_args(attempt_id, command)))
        evidence_id = long_task.load_task_history(self.task_dir)["attempts"][-1]["command_evidence_ids"][0]
        entry = long_task.evidence.require_evidence(
            self.task_dir, evidence_id, expected_revision="DEV-R01", allowed_types={"command_ref"}
        )
        result = long_task.command_result_payload(self.task_dir, entry["command_result_ref"])
        self.assertIn("output_ref", result)
        self.assertIn("stderr_ref", result)
        output_path = self.task_dir / result["output_ref"]
        stderr_path = self.task_dir / result["stderr_ref"]
        self.assertGreater(output_path.stat().st_size, 16 * 1024)
        self.assertGreater(stderr_path.stat().st_size, 16 * 1024)
        self.assertEqual(long_task.sha256_file(output_path), result["output_digest"])
        self.assertEqual(long_task.sha256_file(stderr_path), result["stderr_digest"])

    def test_tampered_auto_evidence_stderr_is_rejected(self) -> None:
        command = "python3 -c \"import sys; print('ok'); print('stderr', file=sys.stderr)\""
        self._set_validation_command(command)
        attempt_id = self._start()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, long_task.command_finish_attempt(self._finish_args(attempt_id, command)))
        evidence_id = long_task.load_task_history(self.task_dir)["attempts"][-1]["command_evidence_ids"][0]
        entry = long_task.evidence.require_evidence(
            self.task_dir, evidence_id, expected_revision="DEV-R01", allowed_types={"command_ref"}
        )
        result_path = self.task_dir / entry["command_result_ref"]
        payload = json.loads(result_path.read_text(encoding="utf-8"))
        payload["stderr"] = "tampered\n"
        result_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "stderr_digest mismatch"):
            long_task.command_result_payload(self.task_dir, entry["command_result_ref"])

    def test_check_without_auto_evidence_is_rejected_before_execution(self) -> None:
        attempt_id = self._start()
        args = self._finish_args(attempt_id, "python -m unittest")
        args.auto_evidence = False
        with mock.patch("_long_task.command_evidence.subprocess.run") as run:
            with self.assertRaisesRegex(LifecycleError, "LIFECYCLE_AUTO_EVIDENCE_CHECK_WITHOUT_FLAG"):
                long_task.command_finish_attempt(args)
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
