from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from document_core.review_session_write_pilot import write_plan


class R3WritePilotTests(unittest.TestCase):
    def test_review_session_plan_commits_atomically_with_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "review_session.json"
            receipt = write_plan(output, phase="development", revision="a" * 64)
            self.assertEqual("PILOT_COMMITTED", receipt["status"])
            self.assertEqual(receipt["sha256"], __import__("hashlib").sha256(output.read_bytes()).hexdigest())
            self.assertIn('"kind": "review_session"', output.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
