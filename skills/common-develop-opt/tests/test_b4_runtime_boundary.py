import tempfile
import unittest
from pathlib import Path

from document_core import runtime


class B4RuntimeBoundaryTests(unittest.TestCase):
    def test_read_projections_have_one_canonical_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertEqual([], runtime.evidence_query(root))
            self.assertEqual("PASSED", runtime.review_query(root)["status"])


if __name__ == "__main__":
    unittest.main()
