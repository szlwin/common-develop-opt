from __future__ import annotations

import argparse
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import merge_docs  # binds the split merge model to the public runtime contract
import simple_yaml
from _merge_docs.model import (
    MergeError,
    dependency_impact_path,
    empty_dependency_impact,
    simulate_dependency_impact,
)


class DependencyImpactArchiveTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project_doc"
        self.version = "V_1.0"
        self.module = "ORD"
        self.change_root = merge_docs.changes_root(self.root, self.version, self.module)
        self.change_root.mkdir(parents=True)
        (self.root / "docs" / "_relations").mkdir(parents=True)
        self.change = self.change_root / "order.dependency-impact.changeset.yaml"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def args(self) -> argparse.Namespace:
        return argparse.Namespace(
            doc_root=str(self.root), version=self.version, module=self.module,
            changeset=[str(self.change)],
        )

    def document(self, operation: str = "add", base: str = "DI-R00@init") -> dict:
        value = {
            "schemaVersion": 1,
            "artifact": "dependency_impact_changeset",
            "changeSet": {
                "id": "DICHG-V_1.0-ORD-MODULE",
                "version": self.version,
                "module": self.module,
                "reason": "登记订单模块影响",
                "baseRevision": base,
                "resultRevision": "DI-R01@v1" if base == "DI-R00@init" else "DI-R02@v1",
            },
            "operations": [{
                "operation": operation,
                "collection": "nodes",
                "targetId": "MODULE-ORD",
                "reason": "维护模块生命周期状态",
            }],
        }
        if operation in {"add", "update"}:
            value["operations"][0]["after"] = {
                "id": "MODULE-ORD", "type": "MODULE", "name": "订单模块",
                "module": "ORD", "refs": ["docs/ORD/ORD_desc.md"],
            }
        return value

    def test_add_is_deterministic_and_manifest_id_makes_it_idempotent(self) -> None:
        simple_yaml.dump(self.document(), self.change)
        state, records, _, _ = simulate_dependency_impact(self.args(), set())
        self.assertEqual("DI-R01@v1", state["revision"])
        self.assertEqual("MODULE-ORD", state["nodes"][0]["id"])
        target = dependency_impact_path(self.root)
        simple_yaml.dump(state, target)
        _, repeated, _, skipped = simulate_dependency_impact(
            self.args(), {"DICHG-V_1.0-ORD-MODULE"}
        )
        self.assertEqual([], repeated)
        self.assertEqual(["DICHG-V_1.0-ORD-MODULE"], skipped)
        self.assertEqual("DEPENDENCY_IMPACT_CHANGESET", records[0]["kind"])

    def test_stale_base_revision_is_rejected(self) -> None:
        current = empty_dependency_impact(self.root)
        current["revision"] = "DI-R05@current"
        simple_yaml.dump(current, dependency_impact_path(self.root))
        simple_yaml.dump(self.document(base="DI-R00@init"), self.change)
        with self.assertRaisesRegex(MergeError, "stale base revision"):
            simulate_dependency_impact(self.args(), set())

    def test_deprecate_preserves_historical_object(self) -> None:
        current = empty_dependency_impact(self.root)
        current["revision"] = "DI-R01@v1"
        current["nodes"] = [{
            "id": "MODULE-ORD", "type": "MODULE", "name": "订单模块",
            "module": "ORD", "refs": ["docs/ORD/ORD_desc.md"], "status": "ACTIVE",
        }]
        simple_yaml.dump(current, dependency_impact_path(self.root))
        document = self.document(operation="deprecate", base="DI-R01@v1")
        document["changeSet"]["id"] = "DICHG-V_1.0-ORD-DEPRECATE"
        simple_yaml.dump(document, self.change)
        state, _, _, _ = simulate_dependency_impact(self.args(), set())
        self.assertEqual(1, len(state["nodes"]))
        self.assertEqual("DEPRECATED", state["nodes"][0]["status"])


if __name__ == "__main__":
    unittest.main()
