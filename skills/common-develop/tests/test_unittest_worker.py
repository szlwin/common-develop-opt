#!/usr/bin/env python3
"""Tests for the deterministic unittest subprocess worker."""
from __future__ import annotations

import contextlib
import io
import subprocess
import time
import unittest

from internal import unittest_worker


class _PassingCase(unittest.TestCase):
    def case_ok(self) -> None:
        self.assertTrue(True)


class _FailingCase(unittest.TestCase):
    def case_fail(self) -> None:
        self.fail("expected")


class UnittestWorkerTests(unittest.TestCase):
    def test_run_targets_returns_zero_for_success(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(0, unittest_worker.run_targets([f"{__name__}._PassingCase.case_ok"], quiet=True))

    def test_run_targets_returns_one_for_failure(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(1, unittest_worker.run_targets([f"{__name__}._FailingCase.case_fail"], quiet=True))

    def test_empty_targets_are_rejected(self) -> None:
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(2, unittest_worker.run_targets([], quiet=True))

    def test_terminate_descendants_stops_owned_helper(self) -> None:
        child = subprocess.Popen(["sleep", "30"])
        try:
            for _ in range(20):
                if child.pid in unittest_worker.descendant_pids(__import__("os").getpid()):
                    break
                time.sleep(0.01)
            unittest_worker.terminate_descendants()
            child.wait(timeout=2)
            self.assertIsNotNone(child.returncode)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()


if __name__ == "__main__":
    unittest.main()
