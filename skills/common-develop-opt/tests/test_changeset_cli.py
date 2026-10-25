"""Regression coverage for the script-only API/DB/impact changeset boundary."""
from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from document_core import merge_docs, simple_yaml  # noqa: E402


class ChangesetCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project_doc"
        self.source_dir = self.root / "version" / "V_TEST" / "requirement" / "CAT" / "changes"
        self.source_dir.mkdir(parents=True)
        self.document = {
            "schemaVersion": 1,
            "changeSet": {
                "id": "CS-V_TEST-CAT-001",
                "version": "V_TEST",
                "module": "CAT",
                "featureCode": "F-CAT-001",
                "reason": "test",
                "baseRevisions": {"api": "API-R00@init"},
                "resultRevisions": {"api": "API-R01@test"},
                "affectedTraceIds": [],
            },
            "operations": [{
                "artifact": "api",
                "operation": "add",
                "targetId": "API-CAT-001",
                "collection": "endpoints",
                "after": {},
                "reason": "test",
            }],
        }
        self.path = self.source_dir / "api.changeset.yaml"
        simple_yaml.dump(self.document, self.path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def args(self, **values):
        defaults = {
            "doc_root": str(self.root),
            "version": "V_TEST",
            "module": "CAT",
            "changeset": [str(self.path)],
            "artifact": "api",
            "target_id": "API-CAT-001",
            "change_set_id": "",
            "full": False,
            "json": True,
            "agent": "ProjectManagerAgent",
        }
        defaults.update(values)
        return argparse.Namespace(**defaults)

    def test_query_is_bounded_and_supports_full_target_selection(self):
        result = merge_docs._changeset_query_result(self.args())
        self.assertEqual("PASSED", result["status"])
        record = result["changesets"][0]
        self.assertEqual(1, record["operationCount"])
        self.assertEqual("API-CAT-001", record["operations"][0]["targetId"])
        self.assertNotIn("after", record["operations"][0])

        full = merge_docs._changeset_query_result(self.args(full=True))
        self.assertEqual({}, full["changesets"][0]["operations"][0]["after"])

    def test_query_validates_dependency_impact_changeset(self):
        impact = {
            "schemaVersion": 1,
            "artifact": "dependency_impact_changeset",
            "changeSet": {
                "id": "DICHG-V_TEST-CAT-001",
                "version": "V_TEST",
                "module": "CAT",
                "reason": "test",
                "baseRevision": "BM-CAT-R00",
                "resultRevision": "DSG-CAT-R01",
            },
            "operations": [{
                "operation": "add",
                "collection": "nodes",
                "targetId": "NODE-CAT-001",
                "after": {},
                "reason": "test",
            }],
        }
        impact_path = self.source_dir / "dependency-impact.changeset.yaml"
        simple_yaml.dump(impact, impact_path)
        result = merge_docs._changeset_query_result(self.args(
            changeset=[str(impact_path)],
            artifact="dependency-impact",
            target_id="NODE-CAT-001",
        ))
        record = result["changesets"][0]
        self.assertEqual("dependency-impact", record["artifact"])
        self.assertEqual("NODE-CAT-001", record["operations"][0]["targetId"])

    def test_write_requires_digest_and_writes_atomically(self):
        input_path = self.source_dir / "candidate.yaml"
        shutil.copyfile(self.path, input_path)
        digest = hashlib.sha256(self.path.read_bytes()).hexdigest()
        args = self.args(
            changeset=[str(self.path)],
            document_file=str(input_path),
            expected_sha256=digest,
            apply=True,
            agent="IncrementalArchiveAgent",
        )
        self.assertEqual(0, merge_docs.command_write(args))
        self.assertTrue(self.path.is_file())

        args.expected_sha256 = "0" * 64
        with self.assertRaises(ValueError):
            merge_docs.command_write(args)


if __name__ == "__main__":
    unittest.main()
