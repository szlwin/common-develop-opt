#!/usr/bin/env python3
"""Regression tests for the deterministic YAML subset."""

from __future__ import annotations

import unittest

import simple_yaml


class SimpleYamlTest(unittest.TestCase):
    def test_round_trip_nested_document(self) -> None:
        source = {
            "name": "demo",
            "enabled": True,
            "items": [{"id": "A", "url": "https://example.test/a:b#c"}],
            "empty": [],
        }
        self.assertEqual(simple_yaml.loads(simple_yaml.dumps(source)), source)

    def test_root_indentation_is_rejected(self) -> None:
        with self.assertRaisesRegex(simple_yaml.YamlError, "root must start"):
            simple_yaml.loads("  key: value\n")

    def test_duplicate_key_is_rejected(self) -> None:
        with self.assertRaisesRegex(simple_yaml.YamlError, "duplicate key"):
            simple_yaml.loads("key: one\nkey: two\n")

    def test_inline_comment_is_rejected_instead_of_becoming_data(self) -> None:
        with self.assertRaisesRegex(simple_yaml.YamlError, "inline comments"):
            simple_yaml.loads("key: value # ambiguous\n")

    def test_hash_inside_plain_url_without_whitespace_is_data(self) -> None:
        self.assertEqual(simple_yaml.loads("url: https://example.test/a#section\n")["url"], "https://example.test/a#section")


    def test_root_inline_collections_round_trip(self) -> None:
        self.assertEqual([], simple_yaml.loads(simple_yaml.dumps([])))
        self.assertEqual({}, simple_yaml.loads(simple_yaml.dumps({})))

    def test_nested_indentation_must_advance_exactly_two_spaces(self) -> None:
        with self.assertRaisesRegex(simple_yaml.YamlError, "indent exactly two spaces"):
            simple_yaml.loads("root:\n    child: value\n")
        with self.assertRaisesRegex(simple_yaml.YamlError, "list item content must indent exactly two spaces"):
            simple_yaml.loads("items:\n  -\n      id: A\n")

    def test_non_string_keys_and_non_finite_floats_are_rejected(self) -> None:
        with self.assertRaisesRegex(simple_yaml.YamlError, "mapping keys must be strings"):
            simple_yaml.dumps({1: "value"})
        with self.assertRaisesRegex(simple_yaml.YamlError, "non-finite floats"):
            simple_yaml.dumps({"value": float("nan")})

    def test_tabs_and_advanced_yaml_features_are_rejected(self) -> None:
        with self.assertRaises(simple_yaml.YamlError):
            simple_yaml.loads("key:\tvalue\n")
        with self.assertRaises(simple_yaml.YamlError):
            simple_yaml.loads("key: &anchor\n")


if __name__ == "__main__":
    unittest.main()
