#!/usr/bin/env python3
"""Tests for documentation invariant contract projections."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import check_skill
from _check_skill import invariant_checks
from _check_skill.model import CheckResult


class DocumentInvariantContractTests(unittest.TestCase):
    def test_current_registry_contract_projections_pass(self) -> None:
        result = CheckResult()
        invariant_checks.check_doc_invariant_drift(result)
        self.assertEqual([], result.errors, "\n".join(result.errors))
        self.assertEqual(5, result.details["docInvariantDrift"]["contractChecks"])

    def _check_field(self, contract: object, field: dict | None) -> CheckResult:
        result = CheckResult()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract_path = root / "assets" / "contract.json"
            contract_path.parent.mkdir()
            contract_path.write_text(json.dumps(contract), encoding="utf-8")
            item = {"id": "TEST", "contractField": field}
            with patch.object(invariant_checks, "ROOT", root):
                invariant_checks._check_contract_field(item, result, {})
        return result

    def test_missing_pointer_is_rejected(self) -> None:
        result = self._check_field(
            {"policy": {"enabled": True}},
            {
                "contract": "assets/contract.json",
                "jsonPointer": "/policy/missing",
                "valueType": "boolean",
            },
        )
        self.assertTrue(any("cannot resolve" in error for error in result.errors))

    def test_expected_value_drift_is_rejected(self) -> None:
        result = self._check_field(
            {"policy": {"enabled": False}},
            {
                "contract": "assets/contract.json",
                "jsonPointer": "/policy/enabled",
                "expected": True,
                "valueType": "boolean",
            },
        )
        self.assertTrue(any("contractField mismatch" in error for error in result.errors))

    def test_value_type_mismatch_is_rejected(self) -> None:
        result = self._check_field(
            {"policy": {"enabled": "true"}},
            {
                "contract": "assets/contract.json",
                "jsonPointer": "/policy/enabled",
                "valueType": "boolean",
            },
        )
        self.assertTrue(any("type mismatch" in error for error in result.errors))

    def test_rfc6901_escaped_pointer_resolves(self) -> None:
        self.assertEqual(
            "value",
            invariant_checks.resolve_json_pointer(
                {"a/b": {"tilde~key": "value"}}, "/a~1b/tilde~0key"
            ),
        )

    def test_invalid_pointer_escape_is_rejected(self) -> None:
        result = self._check_field(
            {"a": 1},
            {"contract": "assets/contract.json", "jsonPointer": "/a~2b"},
        )
        self.assertTrue(any("cannot resolve" in error for error in result.errors))

    def test_null_contract_field_requires_reason(self) -> None:
        result = self._check_field({}, None)
        self.assertTrue(any("contractFieldReason" in error for error in result.errors))

    def test_missing_contract_file_is_rejected(self) -> None:
        result = self._check_field(
            {}, {"contract": "assets/missing.json", "jsonPointer": ""}
        )
        self.assertTrue(any("contract file is missing" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()
