import json
import tempfile
import unittest
from pathlib import Path

from document_core.state_machine import StateMachineError, append_task_event, replay_task_events


class B4RealTaskEventsTests(unittest.TestCase):
    def test_append_replay_and_missing_snapshot_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / "cache").mkdir()
            append_task_event(root, {"task_id": "T1", "phase": "development", "revision": "R1", "event_type": "TASK_STARTED", "state_patch": {"status": "IN_PROGRESS"}})
            append_task_event(root, {"task_id": "T1", "phase": "development", "revision": "R1", "event_type": "TASK_FINISHED", "state_patch": {"status": "COMPLETED"}})
            (root / "cache" / "canonical_state.json").unlink()
            self.assertEqual("COMPLETED", replay_task_events(root)["status"])

    def test_duplicate_event_is_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / "cache").mkdir()
            event = {"event_id": "E1", "task_id": "T1", "phase": "development", "revision": "R1", "event_type": "TASK_STARTED"}
            append_task_event(root, event); result = append_task_event(root, event)
            self.assertEqual("IDEMPOTENT", result["status"])

    def test_broken_sequence_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); (root / "cache").mkdir()
            (root / "task_events.jsonl").write_text(json.dumps({"seq": 2}) + "\n")
            with self.assertRaises(StateMachineError): replay_task_events(root)
