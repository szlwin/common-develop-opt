"""Real event/Evidence scenarios through the installed canonical dispatcher."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from attempt_fixture import AttemptFixture
from test_wk_functional import run_wk
from runtime import long_task
from runtime._long_task import task_events
from runtime._long_task.command_evidence import run_and_register


class LifecycleMatrixTests(AttemptFixture):
    def setUp(self):
        super().setUp()
        self.command = 'python3 -c "print(42)"'
        self._set_validation_command(self.command)

    def cli(self, command, *args, agent="DevelopAgent"):
        code, payload, out, err = run_wk([
            command, "-g", agent, "--task-dir", str(self.task_dir), *args,
        ])
        return code, payload or json.loads(err or out)

    def global_cli(self, command, *args, agent="ProjectManagerAgent"):
        code, payload, out, err = run_wk([command, "-g", agent, *args])
        return code, payload or json.loads(err or out)

    def snapshot(self):
        return {str(p.relative_to(self.doc_root)): p.read_bytes()
                for p in self.doc_root.rglob("*") if p.is_file() and p.suffix != ".lock"}

    def recover(self):
        for name in ("current_manifest.json", "ai_context.json"):
            (self.task_dir / "cache" / name).unlink(missing_ok=True)

    def execute(self):
        self.attempt = self._start()
        result = run_and_register(
            self.task_dir, agent="DevelopAgent", command=self.command, revision="DEV-R01",
            phase="development", cwd=self.task_dir, mutation_scope="taskFacts", timeout=10,
            scope_refs=["DEV-1"], attempt_id=self.attempt, declared_commands=[self.command],
        )
        self.result = self.task_dir / result["commandResultRef"]

    def record(self, **changes):
        options = {"attempt-id": self.attempt, "command-id": self.result.stem,
                   "result-file": str(self.result), "revision": "DEV-R01", "phase": "development"}
        agent = changes.pop("agent", "DevelopAgent")
        options.update(changes)
        return self.cli("record-result", *[part for k, v in options.items() for part in ("--" + k, v)], agent=agent)

    def pass_attempt(self):
        attempt = self._start()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, long_task.command_finish_attempt(self._finish_args(attempt, self.command)))
        return attempt

    def test_begin_start_resume_and_recover_one_attempt(self):
        before = self.snapshot()
        code, preview = self.cli("begin")
        self.assertEqual(0, code, preview)
        self.assertEqual(before, self.snapshot())
        code, started = self.cli("begin", "--apply")
        self.assertEqual(0, code, started)
        self.assertFalse(started["resumed"])
        self.recover()
        code, resumed = self.cli("begin", "--apply")
        self.assertEqual(0, code, resumed)
        self.assertTrue(resumed["resumed"])
        self.assertEqual(1, len(long_task.load_task_history(self.task_dir)["attempts"]))

    def test_begin_wrong_owner_and_corrupt_intake_do_not_write(self):
        before = self.snapshot()
        self.assertNotEqual(0, self.cli("begin", "--apply", agent="ProjectManagerAgent")[0])
        self.assertEqual(before, self.snapshot())
        (self.task_dir / "request_intake.json").write_text('{broken', encoding="utf-8")
        before = self.snapshot()
        code, result = self.cli("begin", "--apply")
        self.assertEqual(2, code)
        self.assertEqual("REQUEST_INTAKE_INVALID", result["reasonCode"])
        self.assertEqual(before, self.snapshot())

    def test_record_result_writes_once_and_recovers(self):
        self.execute()
        code, first = self.record()
        self.assertEqual(0, code, first)
        self.assertEqual("RECORDED", first["status"])
        ledger = task_events.event_path(self.task_dir).read_bytes()
        self.recover()
        code, second = self.record()
        self.assertEqual(0, code, second)
        self.assertTrue(second["idempotent"])
        self.assertEqual(first["evidence"]["evidence_id"], second["evidence"]["evidence_id"])
        self.assertEqual(ledger, task_events.event_path(self.task_dir).read_bytes())
        attempt = long_task.load_task_history(self.task_dir)["attempts"][-1]
        self.assertEqual("RUNNING", attempt["status"])
        self.assertEqual([first["evidence"]["evidence_id"]], attempt["command_evidence_ids"])

    def test_record_result_rejects_wrong_identity_revision_and_input_drift(self):
        self.execute()
        for change in ({"agent": "ProjectManagerAgent"}, {"phase": "testing"},
                       {"revision": "DEV-R02"}, {"command-id": "invented"},
                       {"attempt-id": "historical"}):
            with self.subTest(change=change):
                before = self.snapshot()
                self.assertNotEqual(0, self.record(**change)[0])
                self.assertEqual(before, self.snapshot())
        value = json.loads(self.result.read_text())
        value["command"] += " && true"
        self.result.write_text(json.dumps(value), encoding="utf-8")
        before = self.snapshot()
        self.assertNotEqual(0, self.record()[0])
        self.assertEqual(before, self.snapshot())

    def test_finish_executes_real_validation_without_closing_running_attempt(self):
        self._start()
        for recover in (False, True):
            if recover:
                self.recover()
            code, result = self.cli("finish", "--apply", "--revision", "DEV-R01")
            self.assertEqual(2, code, result)
            self.assertEqual("BLOCKED", result["status"])
            self.assertEqual("PASSED", result["validationResults"][0]["status"])
            self.assertEqual("RECORDED", result["validationResults"][0]["registration"]["status"])
            self.assertEqual("PASSED", result["verification"]["overall"])
            self.assertEqual("RUNNING", long_task.load_task_history(self.task_dir)["attempts"][-1]["status"])

    def test_finish_rejects_owner_missing_revision_and_cwd_escape(self):
        self._start()
        for args, agent, reason in (
            ([], "DevelopAgent", "FINISH_REVISION_REQUIRED"),
            (["--revision", "DEV-R01"], "ProjectManagerAgent", "FINISH_OWNER_REQUIRED"),
            (["--revision", "DEV-R01", "--cwd", ".."], "DevelopAgent", "FINISH_CWD_INVALID"),
        ):
            with self.subTest(reason=reason):
                ledger = task_events.event_path(self.task_dir).read_bytes()
                code, result = self.cli("finish", "--apply", *args, agent=agent)
                self.assertNotEqual(0, code)
                self.assertIn(reason, json.dumps(result))
                self.assertEqual(ledger, task_events.event_path(self.task_dir).read_bytes())
                self.assertFalse(list((self.task_dir / "evidence" / "runs").rglob("*.json")))

    def test_reconcile_publishes_passed_attempt_once_and_recovers(self):
        self.pass_attempt()
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.acceptance.command_ensure_review(__import__("argparse").Namespace(
                task_dir=str(self.task_dir), phase="development", revision="", agent="ProjectManagerAgent"))
        code, result = self.cli("reconcile", "--apply")
        self.assertEqual(0, code, result)
        self.assertEqual("APPLIED", result["status"], result)
        self.assertEqual("publish-artifact", result["appliedAction"])
        ledger = task_events.event_path(self.task_dir).read_bytes()
        self.recover()
        code, repeated = self.cli("reconcile", "--apply")
        self.assertEqual(0, code, repeated)
        self.assertEqual("NO_SAFE_MUTATION", repeated["status"])
        self.assertEqual(ledger, task_events.event_path(self.task_dir).read_bytes())

    def test_progress_rejects_missing_stale_digest_and_wrong_owner(self):
        for args, agent in (([], "ProjectManagerAgent"), (["--plan-digest", "stale"], "ProjectManagerAgent"),
                            (["--plan-digest", "stale"], "DevelopAgent")):
            with self.subTest(args=args, agent=agent):
                before = self.snapshot()
                self.assertNotEqual(0, self.cli("progress", "--apply", *args, agent=agent)[0])
                self.assertEqual(before, self.snapshot())

    def test_confirm_preview_and_idempotent_skip(self):
        code, preview = self.cli("confirm", "--skip-phase", "testing", "--reason", "matrix", agent="ProjectManagerAgent")
        self.assertEqual(0, code, preview)
        self.assertEqual("PREVIEW", preview["status"])
        code, applied = self.cli("confirm", "--skip-phase", "testing", "--reason", "matrix", "--apply", agent="ProjectManagerAgent")
        self.assertEqual(0, code, applied)
        self.assertIn(applied["status"], {"CONFIRMED", "NO_OP"})
        code, repeated = self.cli("confirm", "--skip-phase", "testing", "--reason", "matrix", "--apply", agent="ProjectManagerAgent")
        self.assertEqual(0, code, repeated)
        self.assertEqual("NO_OP", repeated["status"])

    def test_impact_delivery_env_and_close_boundaries(self):
        code, impact = self.cli("impact", "--revision", "DEV-R01", "--change-class", "PATCH", "--changed-path", "src/app.py", agent="ProjectManagerAgent")
        self.assertIn(code, {0, 2}, impact)
        self.assertIn(impact.get("status"), {"PASSED", "BLOCKED"})
        code, delivery = self.global_cli("delivery", "--action", "plan", "--project-root", str(self.doc_root.parent), agent="ProjectManagerAgent")
        self.assertIn(code, {0, 2}, delivery)
        self.assertIn(delivery.get("status"), {"PASSED", "BLOCKED"})
        code, env = self.global_cli("env", "--project-root", str(self.doc_root.parent), "--check", agent="ProjectManagerAgent")
        self.assertIn(code, {0, 2}, env)
        self.assertIn(env.get("status"), {"PASSED", "FAILED", "BLOCKED"})
        code, closed = self.cli("close", agent="ProjectManagerAgent")
        self.assertEqual(2, code, closed)
        self.assertEqual("BLOCKED", closed.get("status"))

    def test_document_archive_rejects_without_lightweight_mode(self):
        code, result = self.global_cli("document-archive", "--doc-root", str(self.doc_root), "--version", "V_TEST", "--module", "demo", agent="ProjectManagerAgent")
        self.assertNotEqual(0, code)
        self.assertIn("lightweight", json.dumps(result).lower())


if __name__ == "__main__":
    unittest.main()
