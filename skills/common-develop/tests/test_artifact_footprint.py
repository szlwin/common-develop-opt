"""Tests for artifact footprint classification and advisory budgets."""
from __future__ import annotations

import unittest

import _bootstrap
import artifact_footprint


class ArtifactFootprintTests(unittest.TestCase):
    def test_git_refs_and_ignored_cache_marker_stay_small(self) -> None:
        report = artifact_footprint.analyze([
            "src/main/A.java",
            "project_doc/version/V/task/T/cache/.gitignore",
            "project_doc/version/V/task/T/evidence/index.json",
        ])
        self.assertEqual("PASSED", report["status"])
        self.assertEqual(2, report["counts"]["canonical_task"])

    def test_current_manifest_projection_is_counted(self) -> None:
        report = artifact_footprint.analyze([
            "project_doc/version/V/task/T/cache/current_manifest.json",
        ])
        self.assertEqual("WARNING", report["status"])
        self.assertEqual(1, report["counts"]["derived_projection"])
        self.assertIn("DERIVED_PROJECTION_BUDGET", {item["code"] for item in report["warnings"]})

    def test_current_projection_and_handoff_budgets_are_reported(self) -> None:
        paths = [
            "project_doc/version/V/task/T/cache/current_manifest.json",
            "project_doc/version/V/task/T/cache/current_impact.json",
            "project_doc/version/V/task/T/handoff/one.md",
        ]
        report = artifact_footprint.analyze(paths)
        codes = {item["code"] for item in report["warnings"]}
        self.assertIn("DERIVED_PROJECTION_BUDGET", codes)
        self.assertIn("HISTORICAL_HANDOFF_REVIEW", codes)


if __name__ == "__main__":
    unittest.main()
