from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from document_core import state_projection


class StateProjectionR2Tests(unittest.TestCase):
    def test_empty_review_and_evidence_queries_are_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory) / "version" / "V_1" / "task" / "T-001"
            task_dir.mkdir(parents=True)
            before = sorted(task_dir.rglob("*"))
            review = state_projection.review_query(task_dir)
            evidence = state_projection.evidence_query(task_dir)
            self.assertEqual("PASSED", review["status"])
            self.assertEqual([], evidence)
            self.assertEqual(before, sorted(task_dir.rglob("*")))

    def test_projection_exports_are_callable(self) -> None:
        self.assertTrue(callable(state_projection.status))
        self.assertTrue(callable(state_projection.next_action))
        self.assertTrue(callable(state_projection.context))


if __name__ == "__main__":
    unittest.main()
