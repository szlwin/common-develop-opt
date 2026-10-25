#!/usr/bin/env python3
"""Regression tests for compact Review Result events."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

import review_store


class ReviewStoreV4Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.task_dir = Path(self.temp.name) / "project_doc" / "version" / "V_TEST" / "task" / "TARGET"
        self.task_dir.mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def record(self, review_id: str = "REV-000001") -> dict:
        return {
            "schema_version": 1,
            "review_id": review_id,
            "assertion_id": "ASRT-DESIGN-001",
            "acceptance_id": "AC-DESIGN-001",
            "profile_id": "design:ArchitectureReviewAgent",
            "reviewer": "ArchitectureReviewAgent",
            "confirmed_by": "ArchitectureReviewAgent",
            "confirmation_mode": "WIZARD",
            "drafted_by_agent": "",
            "review_phase": "design",
            "review_revision": "DESIGN-R01@abc",
            "assertion_phase": "design",
            "assertion_revision": "DESIGN-R01@abc",
            "result": "PASSED",
            "conclusion_mode": "GENERATED",
            "conclusion": {
                "profile_id": "design:ArchitectureReviewAgent",
                "revision": "DESIGN-R01@abc",
                "conclusion": "PASSED",
                "criteria_results": [{
                    "criterion_id": "RC-ARCH-001",
                    "status": "PASSED",
                    "evidence": [],
                    "finding_ids": [],
                }],
                "finding_ids": [],
                "limitations": [],
                "reviewed_at": "2026-07-24T10:00:00+00:00",
            },
            "selected_evidence_ids": ["EVD-000001"],
            "issue_ids": [],
            "default_answer": "YES",
            "answer_exceptions": {},
            "summary": "Architecture review passed.",
            "notes": [],
            "confirmed_at": "2026-07-24T10:00:00+00:00",
        }

    def test_append_uses_one_jsonl_file_without_objects(self) -> None:
        review_store.append_record(self.task_dir, self.record())
        rows = review_store.load_records(self.task_dir)
        self.assertEqual(1, len(rows))
        self.assertEqual(1, rows[0]["schema_version"])
        self.assertIn("selected_evidence_ids", rows[0])
        self.assertFalse((self.task_dir / "evidence" / "review_objects").exists())
        self.assertEqual(self.record(), review_store.require_record(self.task_dir, "REV-000001"))

    def test_v4_registry_deduplicates_repeated_text_and_evidence_sets(self) -> None:
        record = self.record()
        record["conclusion"]["criteria_results"] = [
            {"criterion_id": f"RC-{number:03d}", "status": "PASSED",
             "evidence": [{"evidence_id": "EVD-000001", "claim": f"Review Result REV-000001 supports RC-{number:03d}"}],
             "finding_ids": [], "reason": "同一条较长的审查理由用于验证字典化存储。"}
            for number in range(1, 9)
        ]
        expanded_size = len(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
        review_store.append_record(self.task_dir, record)
        raw = json.loads(review_store.registry_path(self.task_dir).read_text(encoding="utf-8"))
        self.assertEqual(1, raw["schema_version"])
        self.assertIn("text_catalog", raw["conclusion"])
        self.assertIn("evidence_sets", raw["conclusion"])
        self.assertLess(review_store.registry_path(self.task_dir).stat().st_size, expanded_size)
        self.assertEqual(record, review_store.require_record(self.task_dir, "REV-000001"))

    def test_invalid_review_registry_fails_closed(self) -> None:
        path=review_store.registry_path(self.task_dir); path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({"schema_version":2,"review_id":"REV-INVALID"})+"\n",encoding="utf-8")
        with self.assertRaisesRegex(ValueError,"current registry format"):
            review_store.load_records(self.task_dir)

    def test_legacy_v4_registry_requires_explicit_migration(self) -> None:
        review_store.append_record(self.task_dir, self.record())
        path = review_store.registry_path(self.task_dir)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = 4
        path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(
            ValueError,
            "explicit audited migration.*legacy versions present=\\[4\\]",
        ):
            review_store.load_records(self.task_dir)

    def test_validation_cache_uses_registry_content_digest(self) -> None:
        review_store.append_record(self.task_dir, self.record())
        self.assertEqual([], review_store.validate_registry(self.task_dir)[0])
        path = review_store.registry_path(self.task_dir)
        original = path.read_bytes()
        stat = path.stat()
        changed = original.replace(b'"schema_version":1', b'"schema_version":2', 1)
        self.assertEqual(len(original), len(changed))
        path.write_bytes(changed)
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        errors, _ = review_store.validate_registry(self.task_dir)
        self.assertTrue(errors)

    def test_invalid_compact_record_fails_closed(self) -> None:
        review_store.append_record(self.task_dir, self.record())
        row = json.loads(review_store.registry_path(self.task_dir).read_text(encoding="utf-8").splitlines()[0])
        row.pop("confirmed_by")
        review_store.registry_path(self.task_dir).write_text(json.dumps(row) + "\n", encoding="utf-8")
        errors, _ = review_store.validate_registry(self.task_dir)
        self.assertTrue(any("missing fields" in item for item in errors))


if __name__ == "__main__":
    unittest.main()
