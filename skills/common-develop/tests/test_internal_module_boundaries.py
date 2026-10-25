from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from _check_skill import structure_checks
from _check_skill.model import CheckResult


class InternalModuleBoundaryTests(unittest.TestCase):
    def _root(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        (root / "scripts" / "_long_task").mkdir(parents=True)
        (root / "scripts" / "_check_skill").mkdir(parents=True)
        (root / "scripts" / "long_task.py").write_text(
            'from _long_task.api import *\n', encoding="utf-8"
        )
        (root / "scripts" / "check_skill.py").write_text(
            'from _check_skill.orchestrator import *\n', encoding="utf-8"
        )
        return temporary, root

    def test_rejects_internal_reverse_facade_import(self) -> None:
        temporary, root = self._root()
        self.addCleanup(temporary.cleanup)
        (root / "scripts" / "_long_task" / "bad.py").write_text(
            "import long_task\n", encoding="utf-8"
        )
        result = CheckResult()
        with mock.patch.object(structure_checks, "ROOT", root):
            structure_checks.check_internal_module_boundaries(result)
        self.assertTrue(any("must not import public facade long_task" in error for error in result.errors))

    def test_rejects_executable_internal_module(self) -> None:
        temporary, root = self._root()
        self.addCleanup(temporary.cleanup)
        (root / "scripts" / "_check_skill" / "bad.py").write_text(
            'if __name__ == "__main__":\n    raise SystemExit(0)\n', encoding="utf-8"
        )
        result = CheckResult()
        with mock.patch.object(structure_checks, "ROOT", root):
            structure_checks.check_internal_module_boundaries(result)
        self.assertTrue(any("internal modules must be import-only" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()
