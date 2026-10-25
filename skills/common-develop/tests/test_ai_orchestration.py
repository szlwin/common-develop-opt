"""Execution-path regressions for the thin AI orchestration layer."""
import argparse
import contextlib
import copy
import io
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import acceptance
import evidence
import long_task
import wk
import context_resolver
import agent_context
import request_intake
import test_auto_evidence as fixtures
import test_wk_ux as ux_fixtures
from _core import wk_commands
from _wk.command_plan import build_action_plan
from _wk.risk import assess
from _wk.context_cache import save, load, cache_path
from _wk import context_cache, orchestration, result_recorder
from _wk.result_projection import project
from _wk.command_paths import bind_commands


class AIProjectionTests(unittest.TestCase):
    def test_command_binding_preserves_project_validation_command_facts(self):
        command = "python3 scripts/check.py --project-test"
        value = {"validationResults": [{"command": command}],
                 "actionPlan": [{"command": "python3 scripts/wk.py help"}]}
        bound = bind_commands(value)
        self.assertEqual(command, bound["validationResults"][0]["command"])
        self.assertTrue(Path(shlex.split(bound["actionPlan"][0]["command"])[1]).is_absolute())

    def test_action_plan_excludes_every_placeholder(self):
        for phase in ("development", "testing", "requirement_analysis"):
            self.assertEqual([], build_action_plan({"recommendedCommand": "wk x --revision <REVISION>", "currentPhase": phase}))
            plan = build_action_plan({"recommendedCommand": "wk status --task-dir /tmp/t", "currentPhase": phase})
            self.assertEqual(1, len(plan))
            self.assertEqual("wk status --task-dir /tmp/t", plan[0]["command"])

    def test_nested_blocker_and_user_decision(self):
        value = project({"decision": {"classification": "BLOCKED", "reasonCodes": ["STALE"]},
            "nextCommand": {"command": "wk next", "requiresConfirmation": True,
                "executionContext": "ISOLATED_REVIEW_SESSION_REQUIRED"}}, action="begin")
        self.assertEqual("BLOCKED", value["status"])
        self.assertEqual("STALE", value["reasonCode"])
        self.assertTrue(value["requiresUserDecision"])
        self.assertEqual("ISOLATED_REVIEW_SESSION_REQUIRED", value["executionContext"])
        self.assertEqual("wk next", value["repairPlan"][0]["command"])
        self.assertEqual("BLOCKED", value["lifecycleDecision"]["classification"])
        self.assertEqual("BLOCKED", project(value, action="progress")["lifecycleDecision"]["classification"])

    def test_risk_never_downgrades_structured_high_or_critical_issue(self):
        for facts in ({"riskLevel": "HIGH"}, {"riskLevel": "LOW", "issues": [{"severity": "P1", "status": "OPEN"}]},
                      {"intake": {"riskLevel": "HIGH"}}, {"changedScopes": ["permission"]},
                      {"impactAssessment": {"dataModel": "LOW"}}):
            self.assertEqual("HIGH", assess(facts)["riskLevel"])
        self.assertEqual("MEDIUM", assess({"description": "single safe module with no security issue"})["riskLevel"])

    def test_low_requires_proven_scope_and_respects_standard_mode(self):
        facts = {"changedModules": ["one"], **{key: False for key in
            ("schema", "api_contract", "data_model", "permission", "transaction", "security", "migration")}}
        self.assertEqual("FAST", assess(facts)["governanceProfile"])
        self.assertEqual("STANDARD", assess({**facts, "explicitMode": "STANDARD"})["governanceProfile"])
        self.assertEqual("HIGH", assess({**facts, "changedModules": ["a", "b"]})["riskLevel"])

    def test_malformed_canonical_risk_facts_are_conservative(self):
        for status in ([], {}, None, True):
            with self.subTest(status=status):
                self.assertEqual("HIGH", assess({"severity": "P1", "status": status})["riskLevel"])
        low = {"changedModules": ["one"], **{key: False for key in
            ("schema", "api_contract", "data_model", "permission", "transaction", "security", "migration")}}
        for invalid in ({"status": []}, {"riskLevel": {}}, {"permission": []},
                        {"nested": {"changedModules": [False]}}):
            with self.subTest(invalid=invalid):
                self.assertNotEqual("LOW", assess({**low, **invalid})["riskLevel"])

    def test_cache_sources_and_revision_are_checked_and_gate_conclusions_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            task = Path(root) / "version" / "V" / "task" / "T"
            task.mkdir(parents=True)
            for source in (task / "task_events.jsonl", task / "task_plan.json", task.parent.parent / "work.md"):
                source.write_text("original")
            for source in (task / "task_events.jsonl", task / "task_plan.json", task.parent.parent / "work.md"):
                save(task, "R1", "STANDARD", {"revisionSummary": {"revision": "R1"}})
                self.assertIsNotNone(load(task, "R1", "STANDARD"))
                source.write_text(source.read_text() + "changed")
                self.assertIsNone(load(task, "R1", "STANDARD"))
            with self.assertRaises(ValueError):
                save(task, "R1", "STANDARD", {"decision": "PASSED"})
            self.assertTrue(cache_path(task, "../../escape", "../../escape").is_relative_to(task / "cache" / "context"))
            self.assertIsNone(load(task, "R2", "STANDARD"))

    def test_source_map_change_invalidates_context_cache(self):
        with tempfile.TemporaryDirectory() as root:
            task = Path(root) / "version" / "V" / "task" / "T"
            task.mkdir(parents=True)
            for source in (task / "task_events.jsonl", task / "task_plan.json", task.parent.parent / "work.md"):
                source.write_text("original")
            package = Path(root) / "package"
            source_map = package / "ai" / "source-map.json"
            source_map.parent.mkdir(parents=True)
            source_map.write_text('{"authority": "original"}')
            with mock.patch.object(context_cache, "ROOT", package):
                payload = {"revisionSummary": {"revision": "R1"}}
                save(task, "R1", "STANDARD", payload)
                self.assertIsNotNone(load(task, "R1", "STANDARD"))
                source_map.write_text('{"authority": "changed"}')
                self.assertIsNone(load(task, "R1", "STANDARD"))
                save(task, "R1", "STANDARD", payload)
                self.assertIsNotNone(load(task, "R1", "STANDARD"))
                source_map.unlink()
                self.assertIsNone(load(task, "R1", "STANDARD"))
                save(task, "R1", "STANDARD", payload)
                self.assertIsNotNone(load(task, "R1", "STANDARD"))
                source_map.write_text('{}')
                self.assertIsNone(load(task, "R1", "STANDARD"))


class AIRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AutoEvidenceTests()
        self.fixture.setUp()
        self.task = self.fixture.task_dir
        self.command = 'python3 -c "print(42)"'
        self.fixture._set_validation_command(self.command)

    def tearDown(self):
        self.fixture.tearDown()

    def cli(self, *args, agent="DevelopAgent"):
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = wk.main([*args, "-g", agent, "--task-dir", str(self.task), "--json"])
        return code, json.loads(stdout.getvalue() or stderr.getvalue())

    def execute(self, revision="DEV-R01", exit_code=0):
        self.attempt = self.fixture._start()
        from _long_task.command_evidence import run_and_register
        result = run_and_register(self.task, agent="DevelopAgent", command=self.command, revision=revision,
            phase="development", cwd=self.task, mutation_scope="taskFacts", timeout=10,
            scope_refs=["DEV-1"], attempt_id=self.attempt, declared_commands=[self.command])
        self.result = self.task / result["commandResultRef"]
        return result

    def record(self, **overrides):
        options = dict(agent="DevelopAgent", attempt_id=self.attempt, command_id=self.result.stem,
            result_file=self.result, revision="DEV-R01", phase="development", scope_refs=[])
        options.update(overrides)
        return result_recorder.record(self.task, **options)

    def snapshot(self):
        return {p.relative_to(self.task): p.read_bytes() for p in self.task.rglob("*")
                if p.is_file() and p.suffix != ".lock"}

    def test_begin_preview_is_readonly_and_apply_starts_then_resumes_pending(self):
        plan = self.task / "task_plan.json"
        value = json.loads(plan.read_text()); value["tasks"][0]["status"] = "PENDING"; plan.write_text(json.dumps(value))
        before = self.snapshot()
        code, preview = self.cli("begin")
        self.assertEqual(0, code)
        self.assertTrue(preview["actionPlan"])
        self.assertIn("start-attempt", preview["actionPlan"][0]["command"])
        self.assertEqual(before, self.snapshot())
        code, started = self.cli("begin", "--apply")
        self.assertEqual(0, code)
        self.assertFalse(started["resumed"])
        code, resumed = self.cli("begin", "--apply")
        self.assertEqual(0, code)
        self.assertTrue(resumed["resumed"])
        self.assertEqual(1, len(long_task.load_task_history(self.task)["attempts"]))

    def test_begin_wrong_owner_does_not_start_attempt(self):
        before = self.snapshot()
        code, result = self.cli("begin", "--apply", agent="ProjectManagerAgent")
        self.assertEqual("BLOCKED", result["status"])
        self.assertNotEqual(0, code)
        self.assertEqual(before, self.snapshot())

    def test_record_real_result_binds_attempt_is_idempotent_and_returns_gate(self):
        self.execute()
        first = self.record()
        seq = long_task.load_task_history(self.task)["last_seq"]
        second = self.record()
        self.assertEqual(first["evidence"]["evidence_id"], second["evidence"]["evidence_id"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(seq, long_task.load_task_history(self.task)["last_seq"])
        self.assertEqual("BLOCKED", first["gate"]["classification"])
        attempt = long_task.load_task_history(self.task)["attempts"][-1]
        self.assertEqual("RUNNING", attempt["status"])
        self.assertEqual([first["evidence"]["evidence_id"]], attempt["command_evidence_ids"])

    def test_record_rejects_wrong_owner_phase_revision_command_and_attempt(self):
        self.execute()
        for options in ({"agent": "ProjectManagerAgent"}, {"phase": "testing"}, {"revision": "DEV-R02"},
                        {"command_id": "invented"}, {"attempt_id": "historical"}):
            before = self.snapshot()
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.record(**options)
            self.assertEqual(before, self.snapshot())
        value = json.loads(self.result.read_text()); value["command"] += " && true"; self.result.write_text(json.dumps(value))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "RESULT_COMMAND_NOT_DECLARED"):
            self.record()
        self.assertEqual(before, self.snapshot())

    def test_historical_passed_attempt_is_never_updated(self):
        self.execute()
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.command_finish_attempt(self.fixture._finish_args(self.attempt, self.command))
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "RESULT_ATTEMPT_NOT_RUNNING"):
            self.record()
        self.assertEqual(before, self.snapshot())

    def test_current_iteration_is_required_before_any_write(self):
        self.execute()
        tasks = long_task.runtime_tasks(self.task)
        tasks[0]["iteration_id"] = "ITER-OTHER"
        before = self.snapshot()
        with mock.patch.object(result_recorder, "runtime_tasks", return_value=tasks):
            with self.assertRaisesRegex(ValueError, "RESULT_ITERATION_STALE"):
                self.record()
        self.assertEqual(before, self.snapshot())

    def test_assertion_binds_real_evidence_verifies_and_retries_without_duplicate(self):
        self.execute()
        plan = self.task / "task_plan.json"
        value = json.loads(plan.read_text()); value["tasks"][0]["acceptance_trace_ids"] = ["TRACE-1"]; plan.write_text(json.dumps(value))
        (self.task / "traceability.json").write_text(json.dumps({"items": [{"id": "TRACE-1", "acceptance_criteria": ["AC-1"]}]}))
        first = self.record(acceptance_id="AC-1")
        second = self.record(acceptance_id="AC-1")
        self.assertEqual("VERIFIED", first["acceptance"]["status"])
        self.assertEqual([first["evidence"]["evidence_id"]], first["acceptance"]["evidence_ids"])
        self.assertEqual(first["acceptance"]["assertion_id"], second["acceptance"]["assertion_id"])
        self.assertEqual(1, len(acceptance.load_document(self.task)["assertions"]))

    def test_real_multifile_changes_roll_back_including_new_registry_and_assertions(self):
        self.execute()
        plan = self.task / "task_plan.json"
        value = json.loads(plan.read_text()); value["tasks"][0]["acceptance_trace_ids"] = ["TRACE-1"]; plan.write_text(json.dumps(value))
        (self.task / "traceability.json").write_text(json.dumps({"items": [{"id": "TRACE-1", "acceptance_criteria": ["AC-1"]}]}))
        # No registry existed when this transaction started: restore must remove it.
        (self.task / "evidence" / "registry.jsonl").unlink()
        (self.task / "acceptance.json").unlink(missing_ok=True)
        before = self.snapshot()
        real_write = result_recorder.transactional_text_write
        def fail_after_commit(payloads):
            real_write(payloads)
            self.assertNotEqual(before, self.snapshot())
            raise OSError("injected multi-file failure")
        with mock.patch.object(result_recorder, "transactional_text_write", side_effect=fail_after_commit):
            with self.assertRaisesRegex(OSError, "multi-file failure"):
                self.record(acceptance_id="AC-1")
        self.assertEqual(before, self.snapshot())

    def test_writer_lock_prevents_result_and_evidence_concurrent_writes(self):
        self.execute()
        code = 'import fcntl,sys; f=open(sys.argv[1],"a+b"); fcntl.flock(f,fcntl.LOCK_EX); print("LOCKED",flush=True); sys.stdin.read()'
        child = subprocess.Popen([sys.executable, "-c", code, str(self.task / "cache" / "amend-phase.lock")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual("LOCKED", child.stdout.readline().strip())
            before = self.snapshot()
            with self.assertRaisesRegex(ValueError, "holds the task lock"):
                self.record()
            with self.assertRaisesRegex(ValueError, "holds the task lock"):
                evidence.register_evidence(self.task, agent="DevelopAgent", evidence_type="command_ref",
                    ref=str(self.result), revision="DEV-R01", phase="development")
            self.assertEqual(before, self.snapshot())
        finally:
            child.communicate("", timeout=10)

    def test_finish_executes_declared_validation_and_keeps_gate_blocked(self):
        self.fixture._start()
        code, result = self.cli("finish", "--apply", "--revision", "DEV-R01")
        self.assertEqual(2, code)
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("PASSED", result["validationResults"][0]["status"])
        self.assertEqual("RECORDED", result["validationResults"][0]["registration"]["status"])
        self.assertEqual("PASSED", result["verification"]["overall"])

    def test_finish_completed_is_done_but_closure_ready_is_partial(self):
        for classification, status in (("COMPLETED", "DONE"), ("CLOSURE_ONLY", "PARTIAL"), ("BLOCKED", "BLOCKED")):
            with mock.patch.object(wk, "_safe_next_payload", return_value={"decision": {"classification": classification}}), \
                 mock.patch.object(wk_commands.long_task, "runtime_tasks", return_value=[]), \
                 mock.patch("task_verify.run_verification", return_value={"overall": "PASSED"}):
                _, result = self.cli("finish", "--apply")
                self.assertEqual(status, result["status"])

    def test_cache_hit_reuses_context_but_reads_gate_fresh(self):
        document, state = long_task.read_task_state_cache(self.task)
        state["artifact_revisions"]["development"]["revision"] = "DEV-R01"
        payloads, _, _ = long_task.append_task_events_payloads(self.task, long_task.task_state_path(self.task),
            document, state, [], synced_at="2026-09-06T00:00:00Z")
        long_task.transactional_text_write(payloads)
        options = dict(agent="DevelopAgent", phase="begin", status_reader=lambda p: {"currentPhase": "development"},
            next_reader=lambda *args, **kwargs: {"decision": {"classification": "REVIEW_REQUIRED"},
                "nextCommand": {"executionContext": "ISOLATED_REVIEW_SESSION_REQUIRED"}})
        first = orchestration.execution_package(self.task, **options, write_cache=True)
        with mock.patch.object(orchestration, "stable_context", side_effect=AssertionError("cache miss recomputed context")):
            second = orchestration.execution_package(self.task, **options)
        self.assertEqual("HIT", second["cache"]["status"])
        self.assertEqual(first["context"], second["context"])
        self.assertEqual("ISOLATED_REVIEW_SESSION_REQUIRED", second["executionContext"])
        cached = load(self.task, "DEV-R01", "CONTEXT-DevelopAgent")
        self.assertNotIn("decision", cached["payload"])
        # A fresh process also consumes the same cache.
        script = 'import sys;from pathlib import Path;from _wk.context_cache import load;assert load(Path(sys.argv[1]),"DEV-R01","CONTEXT-DevelopAgent")'
        subprocess.run([sys.executable, "-c", script, str(self.task)], check=True)

    def test_response_schemas_validate_real_begin_and_record_envelopes(self):
        import jsonschema
        schema = json.loads((Path(__file__).parents[1] / "assets/capability-result.schema.json").read_text())
        _, result = self.cli("begin")
        jsonschema.Draft202012Validator(schema).validate(result)
        self.execute()
        jsonschema.Draft202012Validator(schema).validate(self.record())

    def test_invalid_risk_input_cannot_start_attempt(self):
        before = self.snapshot()
        code, result = self.cli("begin", "--apply", "--risk-facts", "[]")
        self.assertNotEqual(0, code)
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual(before, self.snapshot())

    def test_nested_invalid_risk_input_blocks_all_apply_commands_before_writes(self):
        too_deep = {}
        for _ in range(66):
            too_deep = {"nested": too_deep}
        inputs = ["[]", "{", '{"value": NaN}', json.dumps(too_deep),
                  *[json.dumps(facts) for facts in (
                      {"severity": "P1", "status": []},
                      {"issues": [{"severity": "P1", "status": {}}]},
                      {"riskLevel": ["HIGH"]}, {"riskLevel": "UNKNOWN"},
                      {"revision": []}, {"changedModules": ["one", {}]},
                      {"nested": {"security": []}}, {"riskTriggers": "permission"})]]
        for command in ("begin", "progress", "finish"):
            for facts in inputs:
                with self.subTest(command=command, facts=facts):
                    before = self.snapshot()
                    with mock.patch.object(wk, "command_step") as step, \
                         mock.patch.object(wk_commands.execution_evidence, "run_and_register") as run:
                        code, result = self.cli(command, "--apply", "--risk-facts", facts,
                            agent="ProjectManagerAgent" if command == "progress" else "DevelopAgent")
                        step.assert_not_called()
                        run.assert_not_called()
                    self.assertNotEqual(0, code)
                    self.assertEqual("BLOCKED", result["status"])
                    self.assertEqual("RISK_FACTS_INVALID", result["reasonCode"])
                    self.assertEqual(before, self.snapshot())

    def test_finish_runs_relative_validation_in_target_project_from_foreign_cwd(self):
        project = self.fixture.doc_root.parent.resolve()
        command = 'python3 check_target.py'
        (project / "check_target.py").write_text('from pathlib import Path\nassert Path("target.txt").read_text() == "target"\nprint("target validated")\n')
        (project / "target.txt").write_text("target")
        self.fixture._set_validation_command(command)
        self.fixture._start()
        with tempfile.TemporaryDirectory() as other:
            (Path(other) / "check_target.py").write_text('raise RuntimeError("wrong project")\n')
            proc = subprocess.run([sys.executable, str(Path(wk.__file__).resolve()), "finish", "--apply",
                "-g", "DevelopAgent", "--task-dir", str(self.task), "--revision", "DEV-R01", "--json"],
                cwd=other, capture_output=True, text=True)
        result = json.loads(proc.stdout or proc.stderr)
        self.assertEqual(str(project), result["validationCwd"])
        outcome = result["validationResults"][0]
        self.assertEqual("PASSED", outcome["status"])
        recorded = json.loads((self.task / outcome["commandResultRef"]).read_text())
        self.assertEqual(str(project), recorded["metadata"]["cwd"])
        self.assertIn("target validated", recorded["output"])

    def test_finish_cwd_override_is_project_relative_and_rejects_escape_before_writes(self):
        project = self.fixture.doc_root.parent.resolve()
        nested = project / "module"
        nested.mkdir()
        (nested / "check.py").write_text('print("module validated")\n')
        self.fixture._set_validation_command("python3 check.py")
        self.fixture._start()
        _, result = self.cli("finish", "--apply", "--revision", "DEV-R01", "--cwd", "module")
        self.assertEqual("PASSED", result["validationResults"][0]["status"])
        self.assertEqual(str(nested), result["validationCwd"])
        with tempfile.TemporaryDirectory() as other:
            (project / "external").symlink_to(other, target_is_directory=True)
            (project / "loop").symlink_to("loop", target_is_directory=True)
            for path in (other, "..", "external", "loop", "missing", "module/check.py"):
                with self.subTest(path=path):
                    before = self.snapshot()
                    with mock.patch.object(wk_commands.execution_evidence, "run_and_register") as run:
                        code, result = self.cli("finish", "--apply", "--revision", "DEV-R01", "--cwd", path)
                        run.assert_not_called()
                    self.assertNotEqual(0, code)
                    self.assertEqual("FINISH_CWD_INVALID", result["reasonCode"])
                    self.assertEqual(before, self.snapshot())

    def test_finish_preview_exposes_resolved_cwd_without_writes(self):
        before = self.snapshot()
        _, result = self.cli("finish")
        self.assertEqual(str(self.fixture.doc_root.parent.resolve()), result["validationCwd"])
        self.assertFalse(result["validationExecuted"])
        self.assertEqual(before, self.snapshot())

    def test_begin_with_malformed_historical_risk_returns_committed_attempt(self):
        with mock.patch.object(orchestration, "runtime_issues", return_value=[{"severity": "P1", "status": []}]):
            code, result = self.cli("begin", "--apply")
        self.assertEqual(0, code)
        self.assertEqual("HIGH", result["riskLevel"])
        self.assertFalse(result["resumed"])
        self.assertEqual(1, len(long_task.load_task_history(self.task)["attempts"]))

    def test_begin_invalid_request_intake_does_not_start_attempt(self):
        intake = self.task / "request_intake.json"
        intake.write_text("{broken", encoding="utf-8")
        before = self.snapshot()
        code, result = self.cli("begin", "--apply")
        self.assertEqual(2, code)
        self.assertEqual("REQUEST_INTAKE_INVALID", result["reasonCode"])
        self.assertEqual(before, self.snapshot())

    def test_all_compound_error_commands_are_bound_to_installed_scripts(self):
        samples = [
            {"recommendedCommand": "python3 scripts/long_task.py validate --task-dir /tmp/t"},
            {"repairCommand": "python3 scripts/wk.py step --preview --task-dir /tmp/t"},
            {"nextCommand": {"command": "python3 scripts/request_intake.py validate --task-dir /tmp/t"}},
            {"actionPlan": [{"command": "python3 scripts/long_task.py advance-phase --task-dir /tmp/t"}]},
            {"repairPlan": [{"command": "python3 scripts/wk.py reconcile --task-dir /tmp/t"}]},
            {"failedStep": {"repairCommand": "python3 scripts/wk.py step --preview --task-dir /tmp/t"}},
        ]
        for value in samples:
            bound = bind_commands(value)
            commands = []
            def collect(item):
                if isinstance(item, dict):
                    for key, child in item.items():
                        if key.endswith("Command") or key == "command" or key == "repairCommand":
                            commands.append(child)
                        collect(child)
                elif isinstance(item, list):
                    for child in item: collect(child)
            collect(bound)
            self.assertTrue(commands)
            for command in commands:
                self.assertNotIn("python3 scripts/", command)

    def test_missing_finish_revision_is_structured_and_does_not_execute(self):
        self.fixture._start()
        with mock.patch.object(wk_commands.execution_evidence, "run_and_register") as run:
            code, result = self.cli("finish", "--apply")
            run.assert_not_called()
        self.assertEqual("BLOCKED", result["status"])
        self.assertEqual("FINISH_REVISION_REQUIRED", result["reasonCode"])
        self.assertIn("revision", result["requiredInputs"])
        self.assertIn("repairPlan", result)

    def test_record_rejects_current_artifact_revision_mismatch(self):
        self.execute()
        doc, state = long_task.read_task_state_cache(self.task)
        state["artifact_revisions"]["development"]["revision"] = "DEV-R02"
        payloads, _, _ = long_task.append_task_events_payloads(self.task, long_task.task_state_path(self.task),
            doc, state, [], synced_at="2026-09-06T00:00:00Z")
        long_task.transactional_text_write(payloads)
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, "CURRENT_REVISION_EVIDENCE_STALE"):
            self.record()
        self.assertEqual(before, self.snapshot())

    def test_rollback_snapshot_is_taken_after_lock_acquisition(self):
        self.execute()
        registry = self.task / "evidence/registry.jsonl"
        original = registry.read_bytes()
        other_commit = original + b"\n"
        real_lock = result_recorder.task_writer_lock
        @contextlib.contextmanager
        def lock_after_other_writer(task_dir):
            registry.write_bytes(other_commit)
            with real_lock(task_dir) as lock:
                yield lock
        with mock.patch.object(result_recorder, "task_writer_lock", side_effect=lock_after_other_writer), \
             mock.patch.object(result_recorder, "transactional_text_write", side_effect=OSError("injected")):
            with self.assertRaisesRegex(OSError, "injected"):
                self.record()
        self.assertEqual(other_commit, registry.read_bytes())

    def test_progress_uses_next_digest_and_executes_only_one_atomic_action(self):
        helper = ux_fixtures.WKUXTests()
        with tempfile.TemporaryDirectory() as root:
            task, _ = helper._start_task(Path(root))
            helper._prepare_passed_phase_fixture(task)
            args = ["-g", "ProjectManagerAgent", "--task-dir", str(task), "--json"]
            code, preview = helper._json_command(["progress", *args])
            self.assertIn("planDigest", preview)
            code, result = helper._json_command(["progress", "--apply", "--plan-digest", preview["planDigest"], *args])
            self.assertEqual(0, code)
            self.assertEqual(1, len(result["appliedResult"]["completedSteps"]))
            self.assertNotEqual(preview["planDigest"], result["planDigest"])
            self.assertEqual("requirement_analysis", long_task.runtime_state(task)["current_phase"])

    def test_progress_permissions_match_step_and_owner_plan_names_coordinator(self):
        self.assertEqual(agent_context.command_permission("wk", "step"),
                         agent_context.command_permission("wk", "progress"))
        before = self.snapshot()
        code, denied = self.cli("progress", "--apply", "--plan-digest", "invalid")
        self.assertNotEqual(0, code)
        self.assertEqual("BLOCKED", denied["status"])
        self.assertEqual(before, self.snapshot())
        _, owner = self.cli("begin")
        self.assertNotIn("wk.progress", owner["context"]["allowedCommands"])
        helper = ux_fixtures.WKUXTests()
        with tempfile.TemporaryDirectory() as root:
            task, _ = helper._start_task(Path(root))
            helper._prepare_passed_phase_fixture(task)
            code, plan = helper._json_command(["begin", "-g", "RequirementConfirmationAgent",
                "--task-dir", str(task), "--json"])
            self.assertEqual("ProjectManagerAgent", plan["progressAgent"])
            self.assertEqual("ProjectManagerAgent", plan["actionPlan"][0]["requiredAgent"])
            proc = subprocess.run(shlex.split(plan["progressCommand"]), cwd=self.fixture.doc_root.parent,
                                  capture_output=True, text=True)
            self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
            applied = json.loads(proc.stdout)
            self.assertTrue(applied["mutationsApplied"])
            self.assertEqual(1, len(applied["appliedResult"]["completedSteps"]))

    def test_begin_matches_prompt_routes_and_invalidates_cache_when_request_changes(self):
        document, state = long_task.read_task_state_cache(self.task)
        state["artifact_revisions"]["development"]["revision"] = "DEV-R01"
        payloads, _, _ = long_task.append_task_events_payloads(self.task, long_task.task_state_path(self.task),
            document, state, [], synced_at="2026-09-06T00:00:00Z")
        long_task.transactional_text_write(payloads)
        intake = self.task / "request_intake.json"
        options = dict(agent="DevelopAgent", phase="begin", status_reader=lambda p: {},
                       next_reader=lambda *a, **kw: {}, write_cache=True)
        valid_intake = request_intake.classify_feature("示例功能", agent="ProjectManagerAgent",
            classification="new-requirement")
        for prompt in ("后端数据库事务", "前端 Vue", "后端 数据库事务 跨模块 集成测试 失败重试 Review"):
            current_intake = dict(valid_intake, originalInput=prompt)
            intake.write_text(json.dumps(current_intake))
            expected = context_resolver.resolve_context("development", prompt=prompt)
            self.assertTrue(expected["matchedConditionIds"])
            first = orchestration.execution_package(self.task, **options)
            self.assertEqual("MISS", first["cache"]["status"])
            route = first["context"]["contextRouting"]
            self.assertEqual(expected["matchedConditionIds"], route["matchedConditionIds"])
            self.assertEqual(expected["status"], route["status"])
            self.assertEqual(expected["insufficientMandatoryResources"], route["insufficientMandatoryResources"])
            self.assertEqual([r["id"] for r in expected["resources"]], [r["id"] for r in route["resources"]])
            second = orchestration.execution_package(self.task, **options)
            self.assertEqual("HIT", second["cache"]["status"])
            self.assertEqual(route, second["context"]["contextRouting"])

        intake.unlink()
        plan_path = self.task / "task_plan.json"
        plan = json.loads(plan_path.read_text())
        plan["tasks"][0]["title"] = "前端 Vue"
        plan_path.write_text(json.dumps(plan))
        routed = orchestration.execution_package(self.task, **options)
        self.assertEqual("MISS", routed["cache"]["status"])
        expected = context_resolver.resolve_context("development", prompt="前端 Vue")
        self.assertEqual(expected["matchedConditionIds"], routed["context"]["contextRouting"]["matchedConditionIds"])

    def test_installed_action_command_executes_from_foreign_directory(self):
        with tempfile.TemporaryDirectory(prefix="installed skill ") as root:
            install = Path(root) / "common develop"
            shutil.copytree(Path(wk.__file__).parents[1], install,
                ignore=shutil.ignore_patterns("__pycache__", "tests", "*.pyc"))
            project = Path(root) / "project with spaces"
            shutil.copytree(self.fixture.doc_root, project / "project_doc")
            self.task = project / "project_doc" / "version" / "V_TEST" / "task" / self.task.name
            proc = subprocess.run([sys.executable, str(install / "scripts/wk.py"), "begin", "-g", "DevelopAgent",
                "--task-dir", os.path.relpath(self.task, project), "--json"],
                cwd=project, capture_output=True, text=True)
            self.assertEqual(0, proc.returncode, proc.stderr + proc.stdout)
            plan = json.loads(proc.stdout)
            command = plan["actionPlan"][0]["command"]
            self.assertEqual(str((install / "scripts/long_task.py").resolve()), shlex.split(command)[1])
            executed = subprocess.run(command, shell=True, executable="/bin/bash", cwd=root,
                                      capture_output=True, text=True)
            self.assertEqual(0, executed.returncode, executed.stderr + executed.stdout)
            self.assertEqual(1, len(long_task.load_task_history(self.task)["attempts"]))


class AIQuickStartTests(unittest.TestCase):
    def test_readme_preview_confirm_apply_begin_commands_execute(self):
        repository = Path(__file__).resolve().parents[3]
        readme = (repository / "README.zh-CN.md").read_text()
        example = readme.split("### 首次使用的最小示例", 1)[1].split("### 2.", 1)[0]
        blocks = re.findall(r"```bash\n(.*?)```", example, re.S)
        self.assertEqual(2, len(blocks))
        with tempfile.TemporaryDirectory(prefix="readme project ") as root:
            setup = f"WK_SCRIPT={shlex.quote(str(repository / 'skills/common-develop/scripts/wk.py'))}"
            preview_script = blocks[0].replace('WK_SCRIPT="$PWD/skills/common-develop/scripts/wk.py"', setup)
            preview = subprocess.run(["/bin/bash", "-c", preview_script], cwd=root, capture_output=True, text=True)
            self.assertEqual(2, preview.returncode, preview.stderr)
            self.assertEqual("NEEDS_CONFIRMATION", json.loads(preview.stdout)["classification"])
            self.assertFalse((Path(root) / "project_doc").exists())
            # The second block models an explicit user classification, not automatic consent.
            created = subprocess.run(["/bin/bash", "-e", "-c", setup + "\n" + blocks[1]],
                                     cwd=root, capture_output=True, text=True)
            self.assertEqual(0, created.returncode, created.stderr + created.stdout)
            decoder = json.JSONDecoder()
            first, end = decoder.raw_decode(created.stdout.lstrip())
            last = json.loads(created.stdout.lstrip()[end:])
            self.assertEqual("PREVIEW", first["status"])
            self.assertEqual("AI_EXECUTION_PACKAGE", last["kind"])
            self.assertEqual(1, len(list(Path(root).glob("project_doc/version/*/task/*/task.json"))))


if __name__ == "__main__":
    unittest.main()
