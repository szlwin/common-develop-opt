"""Regression tests for token-efficient task verification."""
from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

import long_task
import task_verify
import evidence


class TaskVerifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.doc_root = Path(self.temp.name) / "project_doc"
        args = argparse.Namespace(doc_root=str(self.doc_root), version="V_TEST", target="MOD0001", reuse=False)
        self.assertEqual(0, long_task.command_init(args))
        self.task_dir = self.doc_root / "version" / "V_TEST" / "task" / "MOD0001"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_task_verify_reports_invalid_document_layout(self) -> None:
        forbidden = self.doc_root / "version" / "V_TEST" / "docs"
        forbidden.mkdir(parents=True)
        result = task_verify.run_verification(self.task_dir, "task-health")
        check = next(item for item in result["checks"] if item["id"] == "DOCUMENT_LAYOUT")
        self.assertEqual("FAILED", check["status"])
        self.assertEqual("INVALID_VERSION_DOCS_DIRECTORY", check["findings"][0]["code"])
        self.assertEqual("FAILED", result["overall"])

    def test_summary_is_small_and_does_not_expose_full_validation_payload(self) -> None:
        result, cache_hit = task_verify.verify_task(self.task_dir, "task-health", use_cache=True)
        self.assertFalse(cache_hit)
        summary = task_verify.format_result(result, "summary", cache_hit=cache_hit)
        self.assertEqual("PASSED", summary["overall"])
        self.assertNotIn("inputDigests", summary)
        self.assertNotIn("errors", summary)
        self.assertNotIn("blockingReasons", summary["next"])
        self.assertLess(len(json.dumps(summary, ensure_ascii=False)), 3000)

    def test_second_verification_uses_digest_bound_cache(self) -> None:
        first, first_hit = task_verify.verify_task(self.task_dir, "task-health", use_cache=True)
        second, second_hit = task_verify.verify_task(self.task_dir, "task-health", use_cache=True)
        self.assertFalse(first_hit)
        self.assertTrue(second_hit)
        self.assertEqual(first["inputDigest"], second["inputDigest"])

    def test_cache_invalidates_when_authoritative_input_changes(self) -> None:
        first, _ = task_verify.verify_task(self.task_dir, "task-health", use_cache=True)
        path = self.doc_root / "version" / "V_TEST" / "work.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        second, cache_hit = task_verify.verify_task(self.task_dir, "task-health", use_cache=True)
        self.assertFalse(cache_hit)
        self.assertNotEqual(first["inputDigest"], second["inputDigest"])
        self.assertEqual(
            [f"{second['inputDigest']}.json"],
            sorted(path.name for path in (self.task_dir / "cache" / "verification" / "task-health").glob("*.json")),
        )

    def test_failure_level_returns_bounded_diagnostics(self) -> None:
        document, state = long_task.read_task_state_cache(self.task_dir)
        state["current_phase"] = "unknown"
        payloads, _, _ = long_task.append_task_events_payloads(
            self.task_dir, long_task.task_state_path(self.task_dir), document, state, [], synced_at=long_task.now()
        )
        long_task.transactional_text_write(payloads)
        result, _ = task_verify.verify_task(self.task_dir, "task-health", use_cache=False)
        payload = task_verify.format_result(result, "failures", cache_hit=False)
        self.assertEqual("FAILED", payload["overall"])
        self.assertGreater(len(payload["failures"]), 0)
        self.assertLessEqual(len(payload["failures"]), 20)
        self.assertIn("recommendedReads", payload["failures"][0])

    def test_operating_system_metadata_does_not_change_input_digest(self) -> None:
        before, _ = task_verify.input_fingerprint(self.task_dir)
        (self.task_dir / ".DS_Store").write_bytes(b"finder metadata")
        (self.task_dir / "._task_state.md").write_bytes(b"apple double")
        after, _ = task_verify.input_fingerprint(self.task_dir)
        self.assertEqual(before, after)

    def test_generated_projection_directories_do_not_change_input_digest(self) -> None:
        before, _ = task_verify.input_fingerprint(self.task_dir)
        path = self.task_dir / "views" / "design" / "DesignAgent.json"
        path.parent.mkdir(parents=True)
        path.write_text('{"generated":true}\n', encoding="utf-8")
        after, _ = task_verify.input_fingerprint(self.task_dir)
        self.assertEqual(before, after)

    def test_current_scope_skips_missing_historical_evidence_but_full_audit_reports_it(self) -> None:
        source = self.task_dir / "evidence" / "historical.txt"
        source.write_text("historical", encoding="utf-8")
        evidence.register_evidence(
            self.task_dir, agent="RequirementConfirmationAgent", evidence_type="document_ref",
            ref="evidence/historical.txt", revision="REQCONF-R00@historical",
            phase="requirement_confirmation", scope_refs=["historical"],
        )
        source.unlink()
        current = task_verify.run_verification(self.task_dir, "task-health", evidence_scope="current")
        full = task_verify.run_verification(self.task_dir, "task-health")
        self.assertEqual("PASSED", current["overall"])
        self.assertEqual("current", current["evidenceScope"])
        self.assertEqual("FAILED", full["overall"])
        self.assertEqual("full", full["evidenceScope"])

    def test_current_scope_fingerprint_ignores_historical_evidence_bytes(self) -> None:
        source = self.task_dir / "evidence" / "historical.txt"
        source.write_text("historical", encoding="utf-8")
        evidence.register_evidence(
            self.task_dir, agent="RequirementConfirmationAgent", evidence_type="document_ref",
            ref="evidence/historical.txt", revision="REQCONF-R00@historical",
            phase="requirement_confirmation", scope_refs=["historical"],
        )
        current_before, _ = task_verify.input_fingerprint(self.task_dir, evidence_scope="current")
        full_before, _ = task_verify.input_fingerprint(self.task_dir, evidence_scope="full")
        source.write_text("tampered historical", encoding="utf-8")
        current_after, _ = task_verify.input_fingerprint(self.task_dir, evidence_scope="current")
        full_after, _ = task_verify.input_fingerprint(self.task_dir, evidence_scope="full")
        self.assertEqual(current_before, current_after)
        self.assertNotEqual(full_before, full_after)

    def test_historical_evidence_reference_cannot_hide_canonical_input(self) -> None:
        canonical = self.task_dir / "requirement.md"
        canonical.write_text("canonical requirement", encoding="utf-8")
        evidence.register_evidence(
            self.task_dir, agent="RequirementConfirmationAgent", evidence_type="document_ref",
            ref="requirement.md", revision="REQCONF-R00@historical",
            phase="requirement_confirmation", scope_refs=["historical"],
        )
        before, _ = task_verify.input_fingerprint(self.task_dir, evidence_scope="current")
        canonical.write_text("changed canonical requirement", encoding="utf-8")
        after, _ = task_verify.input_fingerprint(self.task_dir, evidence_scope="current")
        self.assertNotEqual(before, after)


if __name__ == "__main__":
    unittest.main()
