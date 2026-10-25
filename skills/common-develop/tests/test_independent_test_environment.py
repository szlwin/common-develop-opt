#!/usr/bin/env python3
"""Tests for independent test database readiness."""

from __future__ import annotations

import argparse
import io
import json
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import agent_context
import independent_test_environment


class TestEnvironmentTests(unittest.TestCase):
    def test_missing_values_skip_without_failure(self) -> None:
        record = independent_test_environment.resolve_independent_database({})
        self.assertFalse(record["ready"])
        self.assertEqual("SKIPPED", record["status"])
        self.assertEqual(
            ["TEST_DB_URL", "TEST_DB_USER", "TEST_DB_PASSWORD"],
            record["missingEnvironmentGroups"],
        )
        self.assertFalse(record["fallbackAllowed"])
        self.assertFalse(record["secretValuesExposed"])

    def test_preferred_user_variable_enables_database_tests(self) -> None:
        record = independent_test_environment.resolve_independent_database({
            "TEST_DB_URL": "jdbc:test",
            "TEST_DB_USER": "tester",
            "TEST_DB_PASSWORD": "super-sensitive-password",
        })
        self.assertTrue(record["ready"])
        self.assertEqual("READY", record["status"])
        self.assertEqual("TEST_DB_USER", record["resolvedVariableNames"]["username"])
        self.assertNotIn("super-sensitive-password", json.dumps(record, ensure_ascii=False))

    def test_noncanonical_username_alias_is_rejected(self) -> None:
        record = independent_test_environment.resolve_independent_database({
            "TEST_DB_URL": "jdbc:test",
            "TEST_DB_" + "USERNAME": "tester",
            "TEST_DB_PASSWORD": "super-sensitive-password",
        })
        self.assertFalse(record["ready"])
        self.assertEqual("SKIPPED", record["status"])
        self.assertIn("TEST_DB_USER", record["missingEnvironmentGroups"])

    def test_empty_values_are_missing(self) -> None:
        record = independent_test_environment.resolve_independent_database({
            "TEST_DB_URL": " ",
            "TEST_DB_USER": "tester",
            "TEST_DB_PASSWORD": "",
        })
        self.assertEqual("SKIPPED", record["status"])
        self.assertIn("TEST_DB_URL", record["missingEnvironmentGroups"])
        self.assertIn("TEST_DB_PASSWORD", record["missingEnvironmentGroups"])

    def test_status_json_does_not_expose_values(self) -> None:
        args = argparse.Namespace(agent="TestAgent", json=True)
        output = io.StringIO()
        with patch.dict(
            "os.environ",
            {
                "TEST_DB_URL": "jdbc:classified",
                "TEST_DB_USER": "private-user",
                "TEST_DB_PASSWORD": "private-password",
            },
            clear=True,
        ), redirect_stdout(output):
            self.assertEqual(0, independent_test_environment.command_status(args))
        text = output.getvalue()
        payload = json.loads(text)
        self.assertTrue(payload["independentDatabase"]["ready"])
        self.assertNotIn("jdbc:classified", text)
        self.assertNotIn("private-user", text)
        self.assertNotIn("private-password", text)

    def test_status_authorizes_with_canonical_command_id(self) -> None:
        args = argparse.Namespace(agent="TestAgent", json=True)
        record = independent_test_environment.resolve_independent_database({})
        with patch.object(
            independent_test_environment.agent_context,
            "authorize_static",
        ) as authorize, patch.object(
            independent_test_environment,
            "resolve_independent_database",
            return_value=record,
        ), redirect_stdout(io.StringIO()):
            self.assertEqual(0, independent_test_environment.command_status(args))
        authorize.assert_called_once_with(
            "TestAgent",
            "independent_test_environment",
            "status",
        )

    def test_status_rejects_unauthorized_agent(self) -> None:
        args = argparse.Namespace(agent="DesignAgent", json=True)
        with self.assertRaises(agent_context.AgentAuthorizationError):
            independent_test_environment.command_status(args)


if __name__ == "__main__":
    unittest.main()
