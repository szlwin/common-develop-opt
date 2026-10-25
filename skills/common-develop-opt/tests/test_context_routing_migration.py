"""Behavior checks for the context-routing implementation boundary."""
from __future__ import annotations

import contextlib
import io
import json
import unittest

from document_core import context_routing


class ContextRoutingMigrationTests(unittest.TestCase):
    def test_new_layer_exposes_all_query_commands(self) -> None:
        commands = set(context_routing.build_parser()._subparsers._group_actions[0].choices)
        self.assertEqual({"list", "show", "route", "condition", "resource", "resolve"}, commands)

    def test_route_payload_matches_contract_budget(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, context_routing.main([
                "route", "-g", "ProjectManagerAgent", "--task-type", "testing", "--json",
            ]))
        payload = json.loads(output.getvalue())
        self.assertEqual(2150, payload["baseRouteTokenBudget"])
        self.assertEqual(["execution-core", "testing-core", "test-evidence"], [item["id"] for item in payload["resources"]])


if __name__ == "__main__":
    unittest.main()
