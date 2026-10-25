#!/usr/bin/env python3
"""Tests for bounded Context Routing queries."""
from __future__ import annotations

import contextlib
import io
import json
import unittest

import context_routing


class ContextRoutingQueryTests(unittest.TestCase):
    def test_route_returns_only_exact_base_resources(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, context_routing.main([
                "route", "-g", "ProjectManagerAgent", "--task-type", "testing", "--json",
            ]))
        payload = json.loads(output.getvalue())
        self.assertEqual("testing", payload["taskType"])
        self.assertEqual(2150, payload["baseRouteTokenBudget"])
        self.assertEqual(["execution-core", "testing-core", "test-evidence"], [item["id"] for item in payload["resources"]])

    def test_resource_and_condition_queries_are_narrow(self) -> None:
        resource = io.StringIO()
        with contextlib.redirect_stdout(resource):
            self.assertEqual(0, context_routing.main([
                "resource", "-g", "ProjectManagerAgent", "--id", "testing-core", "--json",
            ]))
        self.assertEqual("testing-core", json.loads(resource.getvalue())["id"])
        contract = context_routing.load_contract()
        condition_id = contract["promptConditions"][0]["id"]
        condition = io.StringIO()
        with contextlib.redirect_stdout(condition):
            self.assertEqual(0, context_routing.main([
                "condition", "-g", "ProjectManagerAgent", "--id", condition_id, "--json",
            ]))
        self.assertEqual(condition_id, json.loads(condition.getvalue())["id"])

    def test_show_rejects_whole_document(self) -> None:
        error = io.StringIO()
        with contextlib.redirect_stderr(error):
            self.assertEqual(1, context_routing.main([
                "show", "-g", "ProjectManagerAgent", "--pointer", "/", "--json",
            ]))
        self.assertIn("whole-document", error.getvalue())


if __name__ == "__main__":
    unittest.main()
