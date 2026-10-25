import json
import tempfile
import unittest
from pathlib import Path

from document_core.state_machine import StateMachineError, apply_event, recover


class B4StateMachineTests(unittest.TestCase):
    def event(self, typ, revision="R1"):
        return {"type": typ, "taskId": "T1", "phase": "development", "revision": revision}

    def test_normal_transition_and_idempotency(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            first = apply_event(root, {**self.event("START"), "eventId": "E1"})
            again = apply_event(root, {**self.event("START"), "eventId": "E1"})
            self.assertEqual("COMMITTED", first["status"])
            self.assertEqual("IDEMPOTENT", again["status"])
            self.assertEqual("IN_PROGRESS", json.loads((root / "state.json").read_text())["status"])

    def test_invalid_transition_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(StateMachineError):
                apply_event(Path(d), self.event("FINISH"))

    def test_recovery_rebuilds_state_from_journal(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            apply_event(root, {**self.event("START"), "eventId": "E1"})
            apply_event(root, {**self.event("FINISH"), "eventId": "E2"})
            (root / "state.json").unlink()
            result = recover(root)
            self.assertEqual("RECOVERED", result["status"])
            self.assertEqual("COMPLETED", result["state"]["status"])

    def test_crash_before_commit_writes_no_event(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with self.assertRaisesRegex(RuntimeError, "CRASH_INJECTED"):
                apply_event(root, self.event("START"), crash_after_prepare=True)
            self.assertFalse((root / "events.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
