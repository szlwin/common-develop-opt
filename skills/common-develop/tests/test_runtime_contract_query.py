#!/usr/bin/env python3
"""Tests for bounded Runtime Contract queries and wk command discovery."""
from __future__ import annotations

import contextlib
import io
import json
import unittest

import runtime_contract


class RuntimeContractQueryTests(unittest.TestCase):
    def test_command_catalog_discovers_all_wk_groups(self) -> None:
        catalog = runtime_contract.command_catalog()
        categories = {item["category"] for item in catalog}
        self.assertTrue({"request", "mode", "action", "orchestration", "selector"} <= categories)
        aliases = {flag for item in catalog for flag in item["flags"]}
        for flag in ("-b", "-f", "-mc", "-v", "-min", "-auto", "-ar", "-gc", "-gp", "-tp", "-l", "-d"):
            self.assertIn(flag, aliases)

    def test_command_query_returns_runtime_backed_metadata(self) -> None:
        item = runtime_contract.command_by_flag("-ar")
        self.assertEqual("architectureReview", item["id"])
        self.assertEqual("mode", item["category"])
        self.assertIn("runtime-contract.json#/commandInterface/workModes/architectureReview", item["policyRef"])

    def test_show_rejects_whole_document_and_allows_narrow_pointer(self) -> None:
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            self.assertEqual(1, runtime_contract.main(["show", "-g", "ProjectManagerAgent", "--pointer", "/", "--json"]))
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, runtime_contract.main([
                "show", "-g", "ProjectManagerAgent",
                "--pointer", "/commandInterface/workModes/architectureReview/flags", "--json",
            ]))
        payload = json.loads(output.getvalue())
        self.assertEqual(["-ar"], payload["value"])

    def test_permissions_query_is_compact(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, runtime_contract.main([
                "permissions", "-g", "ProjectManagerAgent", "--for-agent", "DevelopAgent", "--json",
            ]))
        payload = json.loads(output.getvalue())
        self.assertEqual("DevelopAgent", payload["agent"])
        self.assertIn("runtime_contract.show", payload["allowedReadOnlyCommands"])


if __name__ == "__main__":
    unittest.main()
