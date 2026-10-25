from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from runtime._long_task import authorization, task_identity


class TaskIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.current = Path(
            "/Users/example/work/project_doc/version/V_TEST/task/FEAT001"
        )

    def test_portable_task_path_uses_project_doc_relative_suffix(self) -> None:
        self.assertEqual(
            "version/V_TEST/task/FEAT001",
            task_identity.portable_task_path(self.current),
        )

    def test_relocated_legacy_absolute_binding_remains_valid(self) -> None:
        payload = {
            "taskDir": "/mnt/data/import/project_doc/version/V_TEST/task/FEAT001",
            "taskId": "FEAT001",
            "changedPhase": "development",
            "fromPhase": "implementation_plan",
            "sourceRevision": "DEV-R01",
            "changedIterationId": "ITER-DEV-001",
            "changedRevision": "",
            "reason": "upstream changed",
            "agent": "ProjectManagerAgent",
            "mode": "reopen",
        }
        self.assertTrue(task_identity.binding_matches(payload, self.current))

        legacy_fields = dict(payload)
        expected = hashlib.sha256(
            json.dumps(
                legacy_fields,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(expected, authorization.reopen_boundary_fingerprint(payload))

    def test_binding_rejects_other_version_or_absolute_new_path(self) -> None:
        self.assertFalse(task_identity.binding_matches(
            {"taskDir": "/mnt/data/project_doc/version/V_OTHER/task/FEAT001"},
            self.current,
        ))
        self.assertFalse(task_identity.binding_matches(
            {"taskPath": "/version/V_TEST/task/FEAT001"},
            self.current,
        ))


if __name__ == "__main__":
    unittest.main()
