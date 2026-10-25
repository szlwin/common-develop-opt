"""Behavior checks for the artifact-footprint implementation boundary."""
from __future__ import annotations

import unittest
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from document_core import artifact_footprint


class ArtifactFootprintMigrationTests(unittest.TestCase):
    def test_declared_cli_runs_from_foreign_cwd_without_pythonpath(self) -> None:
        script = Path(artifact_footprint.__file__).resolve()
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        with tempfile.TemporaryDirectory() as directory:
            run = subprocess.run(
                [sys.executable, "-B", str(script), "--path", "src/main.py", "--json"],
                cwd=directory, env=env, text=True, capture_output=True, timeout=30,
            )
        self.assertEqual(0, run.returncode, run.stderr)
        self.assertEqual(1, json.loads(run.stdout)["counts"]["product_or_test"])

    def test_new_layer_exposes_classifier_api(self) -> None:
        self.assertTrue(callable(artifact_footprint.analyze))
        self.assertTrue(callable(artifact_footprint.category))
        self.assertTrue(callable(artifact_footprint.normalize))

    def test_new_layer_preserves_category_and_budget_warning(self) -> None:
        result = artifact_footprint.analyze([
            "project_doc/version/V/task/T/current_manifest.json",
            "project_doc/version/V/task/T/current_impact.json",
            "project_doc/version/V/task/T/handoff/20260911.md",
        ])
        self.assertEqual("WARNING", result["status"])
        self.assertEqual(2, result["counts"]["derived_projection"])
        self.assertEqual(1, result["counts"]["historical_handoff"])


if __name__ == "__main__":
    unittest.main()
