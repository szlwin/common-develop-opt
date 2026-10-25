from __future__ import annotations

import argparse
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import current_manifest
import long_task
import task_layout
import wk
import work_mode
from _long_task import task_events


class CurrentManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.doc_root = Path(self.temp.name) / "project_doc"
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.command_init(argparse.Namespace(
                doc_root=str(self.doc_root), version="V_TEST", target="TASK-MANIFEST",
                reuse=False, request_intake="", agent="ProjectManagerAgent",
            ))
        self.task_dir = self.doc_root / "version" / "V_TEST" / "task" / "TASK-MANIFEST"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_manifest_is_reducer_derived_and_compact(self) -> None:
        value = current_manifest.build_manifest(self.task_dir)
        self.assertEqual("CURRENT_MANIFEST", value["kind"])
        self.assertEqual("DERIVED", value["authority"])
        self.assertEqual("EVENT_LEDGER_V3", value["state"]["storageMode"])
        self.assertEqual("requirement_confirmation", value["state"]["currentPhase"])
        self.assertTrue(value["recoveryPolicy"]["historyRequiresExplicitRead"])

    def test_stale_cache_does_not_change_manifest_source(self) -> None:
        first = current_manifest.build_manifest(self.task_dir)
        current_manifest.manifest_path(self.task_dir).write_text(
            json.dumps({"kind": "CURRENT_MANIFEST", "state": {"currentPhase": "testing"}}),
            encoding="utf-8",
        )
        second = current_manifest.build_manifest(self.task_dir)
        self.assertEqual(first["sourceInputDigest"], second["sourceInputDigest"])
        self.assertEqual("requirement_confirmation", second["state"]["currentPhase"])

    def test_lightweight_close_is_non_retroactive_in_manifest(self) -> None:
        work_path = self.doc_root / "version" / "V_TEST" / "work.md"
        work_mode.apply_mode(work_path, work_mode.WorkMode(lightweight=True))
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.command_sync_work_mode(argparse.Namespace(
                task_dir=str(self.task_dir), agent="ProjectManagerAgent",
            ))
        work_mode.apply_mode(work_path, work_mode.WorkMode())
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.command_sync_work_mode(argparse.Namespace(
                task_dir=str(self.task_dir), agent="ProjectManagerAgent",
            ))
        value = current_manifest.build_manifest(self.task_dir)
        self.assertEqual("lightweight_closed", value["workPolicy"]["effectiveMode"])
        self.assertFalse(value["workPolicy"]["retroactiveReviewTestRequired"])
        self.assertTrue(value["workPolicy"]["explicitReopenRequired"])
        self.assertIn("testing", value["workPolicy"]["skippedPhases"])
        self.assertFalse((self.task_dir / "cache" / "current_context.md").exists())
        self.assertTrue(any(item.get("type") == "WORK_POLICY_SET" for item in task_events.read_events(self.task_dir)))

    def test_standard_user_skip_is_visible_and_requires_explicit_reopen(self) -> None:
        reason = "user explicitly skipped independent Review"
        confirmation_output = io.StringIO()
        with contextlib.redirect_stdout(confirmation_output):
            self.assertEqual(0, wk.command_confirm(argparse.Namespace(
                task_dir=str(self.task_dir), until="", reopen_from="", skip_phase="code_review",
                changed_phase="", source_revision="", reason=reason,
                agent="ProjectManagerAgent", preview=False, apply=True, json=True,
            )))
        confirmation_ref = json.loads(confirmation_output.getvalue())["requestFingerprint"]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, long_task.command_skip_phase(argparse.Namespace(
                task_dir=str(self.task_dir), phase="code_review", reason=reason,
                confirmation_ref=confirmation_ref, agent="ProjectManagerAgent", json=True,
            )))
        policy = current_manifest.build_manifest(self.task_dir)["workPolicy"]
        self.assertEqual("standard", policy["effectiveMode"])
        self.assertIn("code_review", policy["skippedPhases"])
        self.assertTrue(policy["explicitReopenRequired"])

    def test_wk_status_and_context_embed_manifest_without_writing(self) -> None:
        for command in ("status", "context"):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, wk.main([command, "-g", "ProjectManagerAgent", "--task-dir", str(self.task_dir)]))
            payload = json.loads(output.getvalue())
            self.assertEqual("CURRENT_MANIFEST", payload["currentManifest"]["kind"])
            self.assertTrue((self.task_dir / "cache" / "current_manifest.json").exists())
            self.assertTrue(payload["currentManifestFresh"])

    def test_impact_plan_is_persisted_as_derived_cache(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, wk.main([
                "impact", "-g", "ProjectManagerAgent", "--task-dir", str(self.task_dir),
                "--revision", "R1", "--change-class", "DOCUMENTATION_ONLY",
            ]))
        payload = json.loads(output.getvalue())
        impact_path = self.task_dir / "cache" / "current_impact.json"
        self.assertEqual(str(impact_path), payload["derivedOutputPath"])
        self.assertTrue(impact_path.is_file())
        manifest = current_manifest.build_manifest(self.task_dir)
        self.assertTrue(manifest["derivedImpact"]["present"])
        self.assertTrue(manifest["derivedImpact"]["fresh"])

    def test_wk_impact_output_matches_bounded_plan_schema(self) -> None:
        adapter_path = Path(self.temp.name) / "impact-adapter.json"
        adapter_path.write_text(json.dumps({
            "schemaVersion": 1,
            "confidence": "HIGH",
            "affectedProfiles": ["requirement_confirmation:RequirementAnalysisAgent"],
        }), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, wk.main([
                "impact", "-g", "ProjectManagerAgent", "--task-dir", str(self.task_dir),
                "--phase", "requirement_confirmation", "--base-revision", "REQ-R01@base",
                "--revision", "REQ-R02@next", "--change-class", "DOCUMENTATION_ONLY",
                "--profile", "requirement_confirmation:RequirementAnalysisAgent",
                "--adapter-result", str(adapter_path), "--affected-task-id", "TASK-A",
            ]))
        payload = json.loads(output.getvalue())
        plan = json.loads((self.task_dir / "cache" / "current_impact.json").read_text(encoding="utf-8"))
        self.assertEqual(str(self.task_dir / "cache" / "current_impact.json"), payload["derivedOutputPath"])
        from _long_task.amendment_validation import validate_bounded_impact_plan
        self.assertEqual([], validate_bounded_impact_plan(plan, change_class="DOCUMENTATION_ONLY"))

    def test_traceability_summary_is_canonical_and_manifest_tracks_digest(self) -> None:
        trace = self.task_dir / "traceability.json"
        trace.write_text(json.dumps({"items": [
            {"id": "TR-001", "status": "COVERED", "acceptance_criteria": ["AC-001"], "verification_evidence_ids": ["EVD-1"]},
            {"id": "TR-002", "status": "GAP", "acceptance_criteria": ["AC-002"]},
        ]}), encoding="utf-8")
        first = current_manifest.build_manifest(self.task_dir)
        summary = first["traceabilitySummary"]
        self.assertEqual("CANONICAL", summary["authority"])
        self.assertEqual(2, summary["total"])
        self.assertEqual(["TR-002"], summary["gapIds"])
        self.assertEqual(["AC-001", "AC-002"], summary["acceptanceIds"])
        trace.write_text(trace.read_text(encoding="utf-8").replace("TR-002", "TR-003"), encoding="utf-8")
        second = current_manifest.build_manifest(self.task_dir)
        self.assertNotEqual(first["sourceInputDigest"], second["sourceInputDigest"])

    def test_traceability_query_selects_only_requested_ids(self) -> None:
        (self.task_dir / "traceability.json").write_text(json.dumps({"items": [
            {"id": "TR-001", "status": "COVERED", "acceptance_criteria": ["AC-001"]},
            {"id": "TR-002", "status": "GAP", "acceptance_criteria": ["AC-002"]},
        ]}), encoding="utf-8")
        by_trace = current_manifest.query_traceability(self.task_dir, trace_id="TR-002")
        self.assertEqual(["TR-002"], [item["id"] for item in by_trace["items"]])
        by_acceptance = current_manifest.query_traceability(self.task_dir, acceptance_id="AC-001")
        self.assertEqual(["TR-001"], [item["id"] for item in by_acceptance["items"]])

    def test_task_layout_cli_syncs_declared_projection(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, task_layout.main([
                "-g", "ProjectManagerAgent", "sync", "--task-dir", str(self.task_dir),
            ]))
        payload = json.loads(output.getvalue())
        self.assertEqual("SYNCED", payload["status"])
        self.assertTrue((self.task_dir / "cache" / "current_manifest.json").is_file())


if __name__ == "__main__":
    unittest.main()
