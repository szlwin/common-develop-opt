#!/usr/bin/env python3
"""Regression tests for the canonical DOC_ROOT layout contract."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import agent_context
import doc_layout


class DocumentLayoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.doc_root = Path(self.temp.name) / "project_doc"
        self.version_root = self.doc_root / "version" / "V_1.0"
        (self.version_root / "doc").mkdir(parents=True)
        (self.doc_root / "docs").mkdir(parents=True)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_doc_root_rejects_version_directory(self) -> None:
        with self.assertRaisesRegex(doc_layout.DocumentLayoutError, "DOC_ROOT points to a version directory"):
            doc_layout.validate_doc_root(self.version_root, "V_1.0")

    def test_version_docs_directory_is_forbidden(self) -> None:
        (self.version_root / "docs").mkdir()
        findings = doc_layout.layout_findings(self.doc_root, "V_1.0")
        self.assertEqual("INVALID_VERSION_DOCS_DIRECTORY", findings[0]["code"])
        with self.assertRaisesRegex(doc_layout.DocumentLayoutError, "INVALID_VERSION_DOCS_DIRECTORY"):
            doc_layout.validate_doc_root(self.doc_root, "V_1.0")

    def test_cli_uses_current_agent_selector(self) -> None:
        self.assertTrue(agent_context.parser_has_agent_selector(doc_layout.build_parser()))

    def test_task_plan_rejects_version_docs_path(self) -> None:
        errors: list[str] = []
        doc_layout.validate_task_plan_paths(
            {"allowed_files": ["project_doc/version/V_1.0/docs/_flows/COMPILER_flow.yaml"]},
            "task-plan[0]",
            errors,
            "V_1.0",
        )
        self.assertEqual(1, len(errors))
        self.assertIn("forbidden version document path", errors[0])


if __name__ == "__main__":
    unittest.main()
