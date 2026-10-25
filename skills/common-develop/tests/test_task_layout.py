"""Regression tests for compact generated task projections."""
from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path

import long_task
import task_layout


class TaskLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.doc_root = Path(self.temp.name) / "project_doc"
        args = argparse.Namespace(doc_root=str(self.doc_root), version="V_TEST", target="MOD0001", reuse=False)
        self.assertEqual(0, long_task.command_init(args))
        self.task_dir = self.doc_root / "version" / "V_TEST" / "task" / "MOD0001"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_sync_generates_one_ignored_compact_context(self) -> None:
        result = task_layout.build_projections(self.task_dir, use_cache=False)
        self.assertEqual("SYNCED", result["status"])
        context = json.loads((self.task_dir / "cache" / "current_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual("CURRENT_MANIFEST", context["kind"])
        self.assertEqual("DERIVED", context["authority"])
        self.assertEqual(["*", "!.gitignore"], (self.task_dir / "cache" / ".gitignore").read_text(encoding="utf-8").splitlines())
        self.assertFalse((self.task_dir / "manifest.json").exists())
        self.assertFalse((self.task_dir / "current" / "context.json").exists())
        self.assertFalse((self.task_dir / "views").exists())
        self.assertEqual([], task_layout.validate_projections(self.task_dir))

    def test_projection_is_marked_stale_after_authoritative_change(self) -> None:
        task_layout.build_projections(self.task_dir, use_cache=False)
        path = self.doc_root / "version" / "V_TEST" / "work.md"
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        errors = task_layout.validate_projections(self.task_dir)
        self.assertEqual([], errors)

    def test_projection_bytes_are_identical_on_cold_and_warm_runs(self) -> None:
        task_layout.build_projections(self.task_dir, use_cache=False)
        cold_context = (self.task_dir / "cache" / "current_manifest.json").read_bytes()
        task_layout.build_projections(self.task_dir, use_cache=True)
        self.assertEqual(cold_context, (self.task_dir / "cache" / "current_manifest.json").read_bytes())

    def test_agent_views_are_read_from_compact_context(self) -> None:
        task_layout.build_projections(self.task_dir, use_cache=False)
        view = task_layout.read_agent_view(self.task_dir, "RequirementConfirmationAgent")
        self.assertEqual("RequirementConfirmationAgent", view["agent"])
        self.assertEqual("DERIVED", view["authority"])


if __name__ == "__main__":
    unittest.main()
