from __future__ import annotations

import argparse
import contextlib
import fcntl
import io
import json
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import acceptance
import evidence
import long_task
import review_store
import storage
from _long_task import task_events


class TraceabilityRevalidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.doc_root = Path(self.temp.name) / "project_doc"
        init = argparse.Namespace(
            doc_root=str(self.doc_root), version="V_TEST", target="TRACE-TARGET",
            reuse=False, request_intake="", agent="ProjectManagerAgent",
        )
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, long_task.command_init(init))
        self.task_dir = self.doc_root / "version" / "V_TEST" / "task" / "TRACE-TARGET"
        self.phase = "requirement_confirmation"
        self.revision = "REQCONF-R01@trace-proof"
        self.trace_id = "TR-TRACE-TARGET-001"
        self.evidence_id = self._prepare_facts()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _prepare_facts(self) -> str:
        state = long_task.runtime_state(self.task_dir)
        state["artifact_revisions"][self.phase].update({"revision": self.revision, "status": "PASSED"})
        state["collaboration_reviews"][self.phase].update({"artifact_revision": self.revision, "status": "PASSED"})
        document, _ = long_task.read_task_state_cache(self.task_dir)
        payloads, _, _ = long_task.append_task_events_payloads(
            self.task_dir, long_task.task_state_path(self.task_dir), document, state, [],
            synced_at="2026-09-04T00:00:00+00:00",
        )
        long_task.transactional_text_write(payloads)

        trace = {
            "id": self.trace_id, "description": "trace proof", "status": "STALE",
            "acceptance_criteria": ["AC-TRACE-TARGET-001"],
            "requirement_refs": ["requirement.md#rule-001"], "impact_required": False,
            "dependency_impact_refs": [], "business_flow_required": False,
            "business_flow_refs": [], "cross_module_implementation_required": False,
            "cross_module_implementation_refs": [], "business_model_refs": [],
            "design_refs": [], "test_case_ids": [], "plan_task_ids": [],
            "contract_refs": [], "implementation_refs": [],
            "verification_evidence_ids": [], "verified_by_agents": [], "notes": "",
        }
        storage.write_json(storage.path(self.task_dir, "traceability"), {"schema_version": 1, "target_id": self.task_dir.name, "items": [trace]})
        proof = self.task_dir / "evidence" / "trace-proof.txt"
        proof.write_text("current trace proof", encoding="utf-8")
        entry = evidence.register_evidence(
            self.task_dir, agent="RequirementConfirmationAgent", evidence_type="document_ref",
            ref=str(proof.relative_to(self.task_dir)), revision=self.revision,
            phase=self.phase, scope_refs=[self.trace_id],
        )
        assertion = {
            "assertion_id": "ASRT-TRACE-TARGET-001", "acceptance_id": "AC-TRACE-TARGET-001",
            "statement": "trace is revalidated", "type": "MANUAL_REVIEW", "phase": self.phase,
            "revision": self.revision, "blocking": True, "status": "VERIFIED",
            "parameters": {"reviewer_agent": "RequirementAnalysisAgent", "review_profile_id": "requirement_confirmation:RequirementAnalysisAgent"},
            "source_refs": ["traceability:TR-TRACE-TARGET-001"], "evidence_ids": [entry["evidence_id"]],
            "verified_by_agent": "RequirementAnalysisAgent", "verified_at": "2026-09-04T00:00:00+00:00", "reason": "",
        }
        acceptance.save_document(self.task_dir, {
            "schema_version": 1, "target_id": self.task_dir.name, "version": "V_TEST", "assertions": [assertion],
        })
        review_store.append_record(self.task_dir, {
            "schema_version": 1, "review_id": "REV-000001", "assertion_id": assertion["assertion_id"],
            "acceptance_id": assertion["acceptance_id"], "profile_id": "requirement_confirmation:RequirementAnalysisAgent",
            "reviewer": "RequirementAnalysisAgent", "confirmed_by": "RequirementAnalysisAgent",
            "confirmation_mode": "MARKDOWN", "drafted_by_agent": "", "review_phase": self.phase,
            "review_revision": self.revision, "assertion_phase": self.phase, "assertion_revision": self.revision,
            "result": "PASSED", "conclusion_mode": "GENERATED", "conclusion": {"conclusion": "PASSED"},
            "selected_evidence_ids": [entry["evidence_id"]], "issue_ids": [], "default_answer": "YES",
            "answer_exceptions": {}, "summary": "trace review passed", "notes": [],
            "confirmed_at": "2026-09-04T00:00:00+00:00",
        })
        return entry["evidence_id"]

    def _args(self, **overrides):
        values = {
            "task_dir": str(self.task_dir), "trace_id": self.trace_id, "phase": self.phase,
            "revision": self.revision, "evidence_id": [self.evidence_id],
            "review_result_ref": "REV-000001", "reason": "旧版同步导致的下游事实失效，重新核验。",
            "agent": "ProjectManagerAgent", "json": True,
        }
        values.update(overrides)
        return argparse.Namespace(**values)

    def test_revalidation_updates_canonical_trace_and_event_atomically(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, long_task.command_revalidate_traceability(self._args()))
        result = json.loads(output.getvalue())
        self.assertEqual("REVALIDATED", result["status"])
        value = storage.read_json(storage.path(self.task_dir, "traceability"), default={})
        self.assertEqual("COVERED", value["items"][0]["status"])
        self.assertEqual(result["traceabilityDigest"], task_events.reduce_events(task_events.read_events(self.task_dir))["traceability_revalidations"][0]["traceability_digest"])
        manifest = storage.read_json(task_events.state_cache_path(self.task_dir), default={})
        self.assertEqual(result["traceabilityDigest"], manifest["traceabilitySummary"]["sourceDigest"])

    def test_revalidation_only_accepts_stale_or_gap(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.command_revalidate_traceability(self._args())
        with self.assertRaises(ValueError):
            long_task.command_revalidate_traceability(self._args())

    def test_revalidation_ignores_missing_historical_evidence_source(self) -> None:
        old_source = self.task_dir / "evidence" / "historical-trace-proof.txt"
        old_source.write_text("historical trace proof", encoding="utf-8")
        evidence.register_evidence(
            self.task_dir,
            agent="RequirementConfirmationAgent",
            evidence_type="document_ref",
            ref=str(old_source.relative_to(self.task_dir)),
            revision="REQCONF-R00@historical",
            phase=self.phase,
            scope_refs=[self.trace_id],
        )
        old_source.unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            result = long_task.command_revalidate_traceability(self._args())
        self.assertEqual(0, result)

    def test_revalidation_rejects_non_project_manager_and_wrong_review_binding(self) -> None:
        with self.assertRaises(ValueError):
            long_task.command_revalidate_traceability(self._args(agent="RequirementAnalysisAgent"))
        with self.assertRaises(ValueError):
            long_task.command_revalidate_traceability(self._args(review_result_ref="REV-999999"))

    def test_revalidation_uses_task_writer_lock(self) -> None:
        lock_path = self.task_dir / "cache" / "amend-phase.lock"
        with lock_path.open("a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaisesRegex(ValueError, "another amend-phase writer"):
                long_task.command_revalidate_traceability(self._args())
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def test_validation_detects_canonical_trace_tampering_after_revalidation(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.command_revalidate_traceability(self._args())
        path = storage.path(self.task_dir, "traceability")
        value = storage.read_json(path, default={})
        value["items"][0]["description"] = "tampered"
        storage.write_json(path, value)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertTrue(any("traceability_digest" in error for error in errors), errors)

    def test_historical_revalidation_allows_trace_rework_after_recorded_reopen(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()):
            with mock.patch("_long_task.commands.traceability.now", return_value="2026-09-04T00:00:00+00:00"):
                long_task.command_revalidate_traceability(self._args())
            long_task.command_reopen_phase(argparse.Namespace(
                task_dir=str(self.task_dir), agent="ProjectManagerAgent",
                from_phase="requirement_analysis", changed_phase="requirement_analysis",
                source_revision="REQAN-R01", reason="Repair analysis references", round=None,
                json=True,
            ))
        path = storage.path(self.task_dir, "traceability")
        value = storage.read_json(path, default={})
        value["items"][0]["status"] = "STALE"
        value["items"][0]["description"] = "Reworked analysis references"
        storage.write_json(path, value)
        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertFalse([error for error in errors if "traceability_revalidations" in error], errors)
        self.assertFalse([error for error in errors if "requirement_confirmation PASSED with GAP/STALE" in error], errors)

    def test_validation_accepts_multiple_revalidations_with_only_latest_digest(self) -> None:
        path = storage.path(self.task_dir, "traceability")
        document = storage.read_json(path, default={})
        second = dict(document["items"][0])
        second["id"] = "TR-TRACE-TARGET-002"
        second["description"] = "second trace proof"
        document["items"].append(second)
        storage.write_json(path, document)

        # The first operation records the digest of the one-trace document.
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.command_revalidate_traceability(self._args())

        # Make the second trace stale again and give it the same proof
        # fixture, now explicitly scoped to the second trace as well.
        document = storage.read_json(path, default={})
        document["items"][1]["status"] = "STALE"
        document["items"][1]["verification_evidence_ids"] = []
        document["items"][1]["verified_by_agents"] = []
        storage.write_json(path, document)
        registry = evidence.load_registry(self.task_dir)
        if registry.get("evidences"):
            registry["evidences"][0]["scope_refs"] = [self.trace_id, "TR-TRACE-TARGET-002"]
            evidence.save_registry(self.task_dir, registry)
        second_args = self._args(trace_id="TR-TRACE-TARGET-002")
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.command_revalidate_traceability(second_args)

        errors, _, _, _ = long_task.validate_task(self.task_dir)
        self.assertFalse(
            [error for error in errors if "traceability_digest" in error],
            errors,
        )


if __name__ == "__main__":
    unittest.main()
