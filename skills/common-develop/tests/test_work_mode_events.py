#!/usr/bin/env python3
"""Regression tests for version-level work-mode audit events."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import merge_docs
import work_mode
import work_mode_events


class WorkModeEventsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project_doc"
        self.version = "V_TEST"
        self.work = work_mode.ensure_work_file(self.root, self.version)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _apply(self, mode: work_mode.WorkMode, *, sync_tasks: bool = False) -> None:
        work_mode.apply_mode_transaction(
            self.root,
            self.version,
            self.work,
            mode,
            sync_tasks=sync_tasks,
            agent="ProjectManagerAgent",
        )

    def test_first_mode_change_is_recorded_from_default_mode(self) -> None:
        self._apply(work_mode.WorkMode(lightweight=True))

        events = work_mode_events.read_events(self.root, self.version)
        self.assertEqual(1, len(events))
        self.assertEqual("WORK_MODE_CHANGED", events[0]["type"])
        self.assertEqual(work_mode.WorkMode().to_record(), events[0]["before"])
        self.assertTrue(events[0]["after"]["lightweight"])
        self.assertEqual("", events[0]["previousDigest"])
        self.assertEqual(events[0]["digest"], work_mode_events.last_mode(self.root, self.version) and events[0]["digest"])

    def test_reapplying_same_mode_is_idempotent(self) -> None:
        mode = work_mode.WorkMode(lightweight=True)
        self._apply(mode)
        self._apply(mode)

        self.assertEqual(1, len(work_mode_events.read_events(self.root, self.version)))

    def test_lightweight_round_trip_records_each_effective_change(self) -> None:
        self._apply(work_mode.WorkMode(lightweight=True))
        self._apply(work_mode.WorkMode())
        self._apply(work_mode.WorkMode(lightweight=True))

        events = work_mode_events.read_events(self.root, self.version)
        self.assertEqual(3, len(events))
        self.assertEqual(
            [False, True, False],
            [event["before"]["lightweight"] for event in events],
        )
        self.assertEqual(
            [True, False, True],
            [event["after"]["lightweight"] for event in events],
        )
        self.assertEqual(events[0]["digest"], events[1]["previousDigest"])
        self.assertEqual(events[1]["digest"], events[2]["previousDigest"])

    def test_corrupt_event_chain_fails_closed(self) -> None:
        self._apply(work_mode.WorkMode(lightweight=True))
        path = work_mode_events.event_path(self.root, self.version)
        event = json.loads(path.read_text(encoding="utf-8"))
        event["after"]["lightweight"] = False
        path.write_text(json.dumps(event) + "\n", encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "work mode event chain is invalid"):
            work_mode_events.read_events(self.root, self.version)
        with self.assertRaisesRegex(ValueError, "work mode event chain is invalid"):
            merge_docs.historical_lightweight_state(self.root, self.version)

    def test_transaction_rolls_back_event_file_when_task_sync_fails(self) -> None:
        self._apply(work_mode.WorkMode(lightweight=True))
        path = work_mode_events.event_path(self.root, self.version)
        original_events = path.read_bytes()
        original_work = self.work.read_bytes()

        with mock.patch.object(
            work_mode,
            "sync_existing_tasks",
            side_effect=work_mode.WorkModeError("同步失败", code="WORKMODE_TASK_RUNTIME_INVALID"),
        ):
            with self.assertRaises(work_mode.WorkModeError):
                self._apply(work_mode.WorkMode(), sync_tasks=True)

        self.assertEqual(original_events, path.read_bytes())
        self.assertEqual(original_work, self.work.read_bytes())

    def test_no_sync_option_does_not_suppress_event_recording(self) -> None:
        self._apply(work_mode.WorkMode(auto=True), sync_tasks=False)

        events = work_mode_events.read_events(self.root, self.version)
        self.assertEqual(1, len(events))
        self.assertTrue(events[0]["after"]["auto"])

    def test_historical_state_uses_only_version_events(self) -> None:
        self._apply(work_mode.WorkMode(lightweight=True))
        task_root = self.root / "version" / self.version / "task"
        task_root.mkdir(parents=True, exist_ok=True)
        (task_root / "UNUSED_REDUCER").mkdir()

        with mock.patch.object(merge_docs.long_task, "runtime_state", side_effect=AssertionError("legacy reducer used")):
            historical = merge_docs.historical_lightweight_state(self.root, self.version)
        self.assertTrue(historical["waived"])
        self.assertIn("test_design", historical["skippedPhases"])

        event_path = work_mode_events.event_path(self.root, self.version)
        event_path.unlink()
        with mock.patch.object(merge_docs.long_task, "runtime_state", side_effect=AssertionError("legacy reducer used")) as runtime_state:
            historical = merge_docs.historical_lightweight_state(self.root, self.version)
        self.assertFalse(historical["waived"])
        self.assertEqual([], historical["skippedPhases"])
        runtime_state.assert_not_called()


if __name__ == "__main__":
    unittest.main()

class WorkModeEventContractTests(unittest.TestCase):
    def test_event_contains_version_source_and_chain_contract_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            event = work_mode_events.append_change(
                root, "V_TEST", {}, {"lightweight": True}, agent="ProjectManagerAgent", source="work_mode.apply"
            )
            self.assertIsNotNone(event)
            assert event is not None
            self.assertEqual("V_TEST", event["version"])
            self.assertEqual("work_mode.apply", event["source"])
            self.assertEqual(work_mode_events.EVENT_SCHEMA_VERSION, event["schemaVersion"])
            self.assertEqual(event, work_mode_events.read_events(root, "V_TEST")[0])

    def test_event_with_wrong_version_or_source_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            event = work_mode_events.append_change(root, "V_TEST", {}, {"auto": True}, agent="ProjectManagerAgent")
            assert event is not None
            path = work_mode_events.event_path(root, "V_TEST")
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["version"] = "V_OTHER"
            payload["digest"] = work_mode_events._digest(payload)
            path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "version is invalid"):
                work_mode_events.read_events(root, "V_TEST")

class WorkModeEventFieldValidationTests(unittest.TestCase):
    def _event_path_with_payload(self, field: str, value: object) -> tuple[tempfile.TemporaryDirectory, Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        event = work_mode_events.append_change(
            root, "V_TEST", {}, {"auto": True}, agent="ProjectManagerAgent"
        )
        assert event is not None
        path = work_mode_events.event_path(root, "V_TEST")
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload[field] = value
        if field != "digest":
            payload["digest"] = work_mode_events._digest(payload)
        path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
        return temp, path

    def test_required_scalar_fields_are_non_empty_strings(self) -> None:
        invalid_values = {
            "eventId": "",
            "timestamp": None,
            "agent": 123,
            "digest": "",
            "previousDigest": None,
        }
        expected_fragments = {
            "eventId": "id is invalid",
            "timestamp": "timestamp is invalid",
            "agent": "agent is invalid",
            "digest": "digest is invalid",
            "previousDigest": "previousDigest is invalid",
        }
        for field, value in invalid_values.items():
            with self.subTest(field=field):
                temp, path = self._event_path_with_payload(field, value)
                try:
                    with self.assertRaisesRegex(ValueError, expected_fragments[field]):
                        work_mode_events.read_events(path.parents[2], "V_TEST")
                finally:
                    temp.cleanup()

    def test_append_rejects_blank_agent_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaisesRegex(ValueError, "agent must be a non-empty string"):
                work_mode_events.append_change(
                    root, "V_TEST", {}, {"auto": True}, agent="   "
                )
            self.assertFalse(work_mode_events.event_path(root, "V_TEST").exists())
