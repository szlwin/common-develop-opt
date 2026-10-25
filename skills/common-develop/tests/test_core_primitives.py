#!/usr/bin/env python3
"""Tests for fifth-batch shared document and persistence primitives."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from _core.document_utils import (
    split_refs,
    table_after_marker,
    table_rows_in_section,
    write_preview_files,
)
from _core.persistence import load_merge_save
from _core.validation import make_require_helpers


class CoreDocumentUtilsTests(unittest.TestCase):
    def test_split_refs_uses_canonical_placeholders_and_separators(self) -> None:
        for value in (None, "", "-", "待补充", "无", "不适用", "N/A", "n/a", "CURRENT", "{VERSION}"):
            self.assertEqual([], split_refs(value))
        self.assertEqual(
            ["REQ-1", "REQ-2", "REQ-3", "REQ-4", "REQ-5", "REQ-6"],
            split_refs("REQ-1、REQ-2，REQ-3,REQ-4；REQ-5\nREQ-6"),
        )

    def test_table_after_marker_reads_first_contiguous_table(self) -> None:
        text = """before
<!-- sample -->

| ID | Name |
| --- | :---: |
| A | Alpha |

not part of table
| B | Beta |
"""
        self.assertEqual(
            (["ID", "Name"], [["A", "Alpha"]]),
            table_after_marker(text, "sample"),
        )

    def test_table_rows_in_section_preserves_section_reader_boundary(self) -> None:
        calls = []

        def reader(text: str, heading: str, next_headings: tuple[str, ...]) -> str:
            calls.append((text, heading, next_headings))
            return "| ID | Name |\n| --- | --- |\n| A | Alpha |\n| malformed |\n"

        rows = table_rows_in_section("document", "## Table", ("## Next",), reader)
        self.assertEqual([{"ID": "A", "Name": "Alpha"}], rows)
        self.assertEqual([("document", "## Table", ("## Next",))], calls)

    def test_write_preview_files_creates_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            records = [(Path("nested/a.bin"), b"A"), (Path("b.bin"), b"B")]
            write_preview_files(
                records,
                path_for=lambda record: root / record[0],
                content_for=lambda record: record[1],
            )
            self.assertEqual(b"A", (root / "nested/a.bin").read_bytes())
            self.assertEqual(b"B", (root / "b.bin").read_bytes())


class CoreValidationTests(unittest.TestCase):
    def test_require_helpers_preserve_domain_exception_and_wording(self) -> None:
        class DomainError(ValueError):
            pass

        require_mapping, require_list, require_text = make_require_helpers(DomainError)
        with self.assertRaisesRegex(DomainError, "value must be a mapping"):
            require_mapping([], "value")
        with self.assertRaisesRegex(DomainError, "value must be a list"):
            require_list({}, "value")
        with self.assertRaisesRegex(DomainError, "value must be a non-empty string"):
            require_text(" ", "value")
        self.assertEqual("text", require_text(" text ", "value"))

    def test_require_helpers_support_exact_alternate_vocabulary(self) -> None:
        require_mapping, require_list, require_text = make_require_helpers(
            ValueError,
            mapping_requirement="an object",
            list_requirement="an array",
            text_requirement="non-empty text",
        )
        with self.assertRaisesRegex(ValueError, "value must be an object"):
            require_mapping([], "value")
        with self.assertRaisesRegex(ValueError, "value must be an array"):
            require_list({}, "value")
        with self.assertRaisesRegex(ValueError, "value must be non-empty text"):
            require_text(None, "value")


class CorePersistenceTests(unittest.TestCase):
    def test_load_merge_save_persists_once_and_returns_merge_result(self) -> None:
        stored = {"items": ["A"]}
        saves = []

        def load() -> dict[str, list[str]]:
            return {"items": list(stored["items"])}

        def merge(value: dict[str, list[str]]) -> str:
            value["items"].append("B")
            return "updated"

        result = load_merge_save(load, lambda value: saves.append(value), merge)
        self.assertEqual("updated", result)
        self.assertEqual([{"items": ["A", "B"]}], saves)

    def test_load_merge_save_does_not_persist_failed_merge(self) -> None:
        saves = []

        def fail(_value: dict[str, object]) -> None:
            raise ValueError("invalid")

        with self.assertRaisesRegex(ValueError, "invalid"):
            load_merge_save(lambda: {}, lambda value: saves.append(value), fail)
        self.assertEqual([], saves)


if __name__ == "__main__":
    unittest.main()
