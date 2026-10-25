"""Document compatibility helpers; preview writes retain their original owner."""
from __future__ import annotations
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any
from document_core.tables import REFERENCE_PLACEHOLDERS, split_refs, table_after_marker, table_rows_in_section, _parse_table_lines

def write_preview_files(
    records: Iterable[Any],
    *,
    path_for: Callable[[Any], Path],
    content_for: Callable[[Any], bytes],
) -> None:
    """Write a collection of preview files with one deterministic filesystem loop."""
    for record in records:
        path = path_for(record)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content_for(record))
