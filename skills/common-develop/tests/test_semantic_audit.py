from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import semantic_audit


ROOT = Path(__file__).resolve().parent.parent


class SemanticAuditTest(unittest.TestCase):
    def test_package_passes_semantic_audit(self) -> None:
        result = semantic_audit.audit(ROOT)
        self.assertEqual("PASSED", result["status"], result["errors"])

    def test_duplicate_heading_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "sample.md"
            path.write_text("# A\n\n## Duplicate\n\nText\n\n## Duplicate\n", encoding="utf-8")
            original = semantic_audit.ROOT
            try:
                semantic_audit.ROOT = root
                errors = semantic_audit.duplicate_headings(path, path.read_text(encoding="utf-8"))
            finally:
                semantic_audit.ROOT = original
            self.assertTrue(any("duplicate heading" in item for item in errors))

    def test_contract_reserves_distinct_namespaces(self) -> None:
        contract = json.loads((ROOT / "assets" / "semantic-contract.json").read_text(encoding="utf-8"))
        prefixes = {item.get("prefix") for item in contract["idNamespaces"] if item.get("prefix")}
        self.assertTrue({"FLOW-", "STEP-", "CMI-", "CMSTEP-"}.issubset(prefixes))
        review = next(item for item in contract["idNamespaces"] if item.get("kind") == "reviewCriterion")
        self.assertEqual("^RC-[A-Z]+-[0-9]{3,}$", review["pattern"])
        self.assertEqual(len(prefixes), len(set(prefixes)))


if __name__ == "__main__":
    unittest.main()
