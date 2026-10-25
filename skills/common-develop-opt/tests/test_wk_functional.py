"""Command x path-class functional matrix for the canonical ``wk`` entry.

Scenarios are mined from the compatibility suite (``test_wk.py``,
``test_wk_ux.py``, ``test_long_task.py``) and re-expressed against the new
dispatcher entry (``scripts/wk.py`` -> ``wk_dispatcher`` -> handler registry)
so behavior coverage survives the later compat deletion. Path classes:

- normal: the command completes with exit 0 and a machine-readable payload;
- reject: fail-closed input or authorization errors (argparse SystemExit(2),
  boundary exit 1, or gate exit 2 with structured guidance);
- idempotent: repeated invocation returns an equivalent result;
- recovery: read commands keep working after derived state is deleted.

Deep write-path scenarios are covered by ``test_wk_lifecycle_matrix`` and
``test_document_archive_matrix``. Archive fixtures include generated portals,
revision-bound Evidence and real archive validation and persistence.
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import wk  # noqa: E402  (canonical entry under test)

AGENT = "ProjectManagerAgent"
OWNER = "DevelopAgent"
COMMANDS = (
    "start", "status", "doctor", "next", "begin", "progress", "finish",
    "record-result", "step", "confirm", "reconcile", "close",
    "document-archive", "impact", "delivery", "env", "context", "packs", "help",
)


def run_wk(args):
    """Invoke ``wk.main`` programmatically; return (code, payload, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = wk.main([*args, "--json"])
    text = out.getvalue()
    payload = None
    start = text.find("{")
    if start >= 0:
        try:
            payload = json.loads(text[start:])
        except json.JSONDecodeError:
            payload = None
    return code, payload, text, err.getvalue()


class MatrixFixture(unittest.TestCase):
    """Shared fixture: one applied task in a temporary doc root."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "project_doc"
        code, payload, _, _ = self.create_task()
        self.assertEqual(0, code, payload)
        self.task_dir = Path(payload["taskDir"])
        self.addCleanup(self._tmp.cleanup)

    @staticmethod
    def intake_args(root) -> list[str]:
        args = [
            "start", "-g", AGENT, "--doc-root", str(root), "--version", "V_TEST",
            "-f", "优化页面标题颜色", "--classification", "simple-optimization",
            "--related-feature", "PRD0013-F01",
        ]
        for flag in (
            "business-logic", "data-model", "api-contract", "permission",
            "workflow", "persistence", "external-side-effect", "migration",
            "dependency",
        ):
            args.extend([f"--{flag}-impact", "NONE"])
        return args

    def create_task(self, root=None):
        return run_wk([*self.intake_args(root or self.root), "--apply"])


class NormalPathTests(MatrixFixture):
    def test_start_preview_does_not_create_directories(self) -> None:
        with tempfile.TemporaryDirectory() as fresh:
            code, payload, _, _ = run_wk(self.intake_args(Path(fresh) / "pd"))
            self.assertEqual(0, code)
            self.assertFalse(payload.get("apply"))
            self.assertFalse((Path(fresh) / "pd").exists())

    def test_start_apply_creates_task_with_intake_record(self) -> None:
        self.assertTrue((self.task_dir / "request_intake.json").is_file())
        self.assertIn("task", self.task_dir.parts)

    def test_status_full_reports_current_phase(self) -> None:
        code, payload, _, _ = run_wk(["status", "--task-dir", str(self.task_dir), "--full"])
        self.assertEqual(0, code)
        self.assertIn(payload["currentPhase"], ("requirement_analysis", "development"))

    def test_status_summary_returns_bounded_projection(self) -> None:
        code, payload, _, _ = run_wk(["status", "--task-dir", str(self.task_dir), "--summary"])
        self.assertEqual(0, code)
        self.assertNotIn("requestIntake", payload)

    def test_next_returns_recommended_command_and_gate(self) -> None:
        code, payload, _, _ = run_wk(["next", "--task-dir", str(self.task_dir)])
        self.assertEqual(0, code)
        self.assertIn("recommendedCommand", payload)
        self.assertTrue(payload["currentGate"])

    def test_step_preview_returns_plan_projection(self) -> None:
        code, payload, _, _ = run_wk(["step", "--task-dir", str(self.task_dir), "--preview"])
        self.assertEqual(0, code)
        self.assertTrue(payload.get("currentPhase"))

    def test_context_returns_task_projection(self) -> None:
        code, payload, _, _ = run_wk(["context", "--task-dir", str(self.task_dir)])
        self.assertEqual(0, code)
        # The task projection stays None until the first attempt exists; the
        # projection keys themselves must always be present and populated.
        self.assertIn("task", payload)
        self.assertTrue(payload.get("currentPhase") or payload.get("nextAction"))

    def test_help_lists_contract_commands(self) -> None:
        code, payload, _, _ = run_wk(["help"])
        self.assertEqual(0, code)
        self.assertGreaterEqual(payload["count"], 19)

    def test_help_flag_query_resolves_mode_flag(self) -> None:
        code, payload, _, _ = run_wk(["help", "--flag=-ar"])
        self.assertEqual(0, code)
        self.assertEqual(1, payload["count"])
        self.assertIn("-ar", payload["commands"][0]["flags"])

    def test_help_category_query_lists_request_flags(self) -> None:
        code, payload, _, _ = run_wk(["help", "--category", "mode"])
        self.assertEqual(0, code)
        self.assertTrue(all(item["category"] == "mode" for item in payload["commands"]))

    def test_doctor_reports_fresh_project(self) -> None:
        code, payload, _, _ = run_wk(["doctor", "--doc-root", str(self.root), "--version", "V_TEST"])
        self.assertEqual(0, code)
        self.assertIn(payload["status"], ("OK", "PASSED", "READY"))

    def test_packs_resolves_internal_modules(self) -> None:
        code, payload, _, _ = run_wk(["packs", "--task-type", "standard"])
        self.assertEqual(0, code)
        self.assertTrue(payload)

    def test_every_command_exposes_help(self) -> None:
        for command in COMMANDS:
            with self.subTest(command=command):
                out = io.StringIO()
                with self.assertRaises(SystemExit) as caught, contextlib.redirect_stdout(out):
                    wk.main([command, "--help"])
                self.assertEqual(0, caught.exception.code)
                self.assertIn(command, out.getvalue())


class RejectPathTests(MatrixFixture):
    def test_start_requires_bug_or_feature(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            wk.main(["start", "-g", AGENT, "--doc-root", str(self.root), "--version", "V_X"])
        self.assertEqual(2, caught.exception.code)

    def test_vague_request_is_blocked_needs_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as fresh:
            root = Path(fresh) / "pd"
            args = ["start", "-g", AGENT, "--doc-root", str(root), "--version", "V_X", "-f", "做点改进"]
            code, payload, _, _ = run_wk(args)
            self.assertEqual(2, code)
            self.assertEqual("BLOCKED", payload.get("workflowProfile") or payload.get("status"))
            self.assertFalse(root.exists())

    def test_simple_optimization_rejects_minimal_mode(self) -> None:
        argv = [
            "start", "-g", AGENT, "--doc-root", str(self.root), "--version", "V_X",
            "-f", "优化页面标题颜色", "--classification", "simple-optimization",
            "--related-feature", "PRD0013-F01", "-min",
        ]
        code, _, _, err = run_wk(argv)
        self.assertEqual(1, code)
        self.assertTrue(err)

    def test_unknown_command_rejected(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            wk.main(["explode", "-g", AGENT])
        self.assertEqual(2, caught.exception.code)

    def test_unknown_agent_rejected(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            wk.main(["status", "-g", "NobodyAgent", "--task-dir", str(self.task_dir)])
        self.assertEqual(2, caught.exception.code)

    def test_begin_reports_required_agent_and_legal_actions(self) -> None:
        code, payload, _, _ = run_wk(["begin", "-g", OWNER, "--task-dir", str(self.task_dir), "--apply"])
        self.assertEqual(2, code)
        # A blocked begin must always answer with structured guidance: the
        # owning agent plus a machine-usable next action, whichever projection
        # shape the current task state produces.
        self.assertTrue(payload)
        self.assertTrue(payload.get("requiredAgent"))
        self.assertTrue(
            payload.get("legalActions")
            or payload.get("preferredAction")
            or payload.get("nextCommand")
            or payload.get("decision")
        )

    def test_record_result_requires_attempt_and_command(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            wk.main(["record-result", "-g", OWNER, "--task-dir", str(self.task_dir)])
        self.assertEqual(2, caught.exception.code)

    def test_confirm_requires_reason(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            wk.main(["confirm", "-g", AGENT, "--task-dir", str(self.task_dir), "--skip-phase", "testing"])
        self.assertEqual(2, caught.exception.code)

    def test_step_apply_requires_matching_digest(self) -> None:
        code, _, _, _ = run_wk(["step", "--task-dir", str(self.task_dir), "--apply"])
        self.assertNotEqual(0, code)

    def test_missing_task_dir_fails_closed(self) -> None:
        code, _, _, _ = run_wk(["status", "--task-dir", "/nonexistent/task-dir"])
        self.assertEqual(1, code)


class IdempotencyTests(MatrixFixture):
    def test_help_output_is_stable_across_invocations(self) -> None:
        first = run_wk(["help"])[2]
        second = run_wk(["help"])[2]
        self.assertEqual(first, second)

    def test_status_projection_is_stable_across_invocations(self) -> None:
        first = run_wk(["status", "--task-dir", str(self.task_dir)])[1]
        second = run_wk(["status", "--task-dir", str(self.task_dir)])[1]
        self.assertEqual(first, second)

    def test_begin_gate_response_is_stable(self) -> None:
        argv = ["begin", "-g", OWNER, "--task-dir", str(self.task_dir), "--apply"]
        first = run_wk(argv)[1]
        second = run_wk(argv)[1]
        self.assertEqual(first, second)

    def test_repeat_start_apply_is_fail_closed_and_never_duplicates(self) -> None:
        # The deterministic content-addressed task id makes a repeated apply a
        # guard hit: it must fail closed instead of duplicating or overwriting.
        code, payload, _, err = self.create_task()
        self.assertEqual(1, code)
        self.assertIn("already exists", err)
        tasks = list((self.root / "version" / "V_TEST" / "task").iterdir())
        self.assertEqual(1, len(tasks))


class RecoveryTests(MatrixFixture):
    def _delete_derived_state(self) -> None:
        (self.task_dir / "cache" / "current_manifest.json").unlink(missing_ok=True)

    def test_status_survives_state_cache_deletion(self) -> None:
        self._delete_derived_state()
        code, payload, _, _ = run_wk(["status", "--task-dir", str(self.task_dir), "--full"])
        self.assertEqual(0, code)
        self.assertNotEqual("INVALID", payload.get("status"))

    def test_next_survives_state_cache_deletion(self) -> None:
        self._delete_derived_state()
        code, payload, _, _ = run_wk(["next", "--task-dir", str(self.task_dir)])
        self.assertEqual(0, code)
        self.assertTrue(payload.get("currentGate"))

    def test_context_rebuilds_projection_after_cache_loss(self) -> None:
        self._delete_derived_state()
        code, payload, _, _ = run_wk(["context", "--task-dir", str(self.task_dir)])
        self.assertEqual(0, code)
        self.assertIn("task", payload)
        self.assertTrue(payload.get("currentPhase") or payload.get("nextAction"))


if __name__ == "__main__":
    unittest.main()
