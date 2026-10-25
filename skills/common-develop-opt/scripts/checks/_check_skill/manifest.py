"""Canonical package manifest discovery, diffing, and validation."""
from __future__ import annotations

import hashlib 
import re 
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]

def is_transient_file(path: Path) -> bool:
    return (
        any(part in {"__pycache__", ".pytest_cache", ".git"} for part in path.parts)
        or path.suffix in {".pyc", ".pyo", ".tmp", ".bak"}
        or path.name in {".coverage", "coverage.xml", ".DS_Store", "Thumbs.db"}
        or path.name.startswith("._")
    )


def manifest_files() -> list[Path]:
    return sorted((
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and path.name != "FILE_MANIFEST.sha256"
        and not is_transient_file(path)
    ), key=lambda path: path.relative_to(ROOT).as_posix())


def manifest_content() -> str:
    lines = []
    for path in manifest_files():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  ./{path.relative_to(ROOT).as_posix()}")
    return "\n".join(lines) + "\n"


def manifest_entry_map() -> dict[str, str]:
    """Return the canonical runtime manifest as path -> sha256."""

    return {
        f"./{path.relative_to(ROOT).as_posix()}": hashlib.sha256(path.read_bytes()).hexdigest()
        for path in manifest_files()
    }


def parse_manifest_entries(text: str) -> tuple[dict[str, str], list[str]]:
    """Parse manifest text and report malformed or duplicate entries."""

    entries: dict[str, str] = {}
    invalid: list[str] = []
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.strip()
        if not line:
            continue
        match = re.fullmatch(r"([a-f0-9]{64})\s{2}(\./[^\r\n]+)", line)
        if not match:
            invalid.append(f"line {line_number}: {raw_line}")
            continue
        digest, relative_path = match.groups()
        if relative_path in entries:
            invalid.append(f"line {line_number}: duplicate {relative_path}")
            continue
        entries[relative_path] = digest
    return entries, invalid


def manifest_diff(actual_text: str, expected_entries: dict[str, str] | None = None) -> dict[str, Any]:
    """Return a stable, machine-readable manifest difference report."""

    expected = expected_entries if expected_entries is not None else manifest_entry_map()
    actual, invalid = parse_manifest_entries(actual_text)
    common = set(actual) & set(expected)
    changed = sorted(path for path in common if actual[path] != expected[path])
    missing = sorted(set(actual) - set(expected))
    unexpected = sorted(set(expected) - set(actual))
    canonical = "\n".join(f"{expected[path]}  {path}" for path in sorted(expected)) + "\n"
    return {
        "changedFiles": changed,
        "missingFiles": missing,
        "unexpectedFiles": unexpected,
        "invalidEntries": invalid,
        "canonicalFormatMismatch": actual_text != canonical,
    }


def write_manifest() -> None:
    from checks.generate_resource_index import write_index
    write_index(ROOT)
    (ROOT / "FILE_MANIFEST.sha256").write_text(manifest_content(), encoding="utf-8")


def check_manifest(result: Any) -> None:
    path = ROOT / "FILE_MANIFEST.sha256"
    if not path.is_file():
        result.errors.append("missing FILE_MANIFEST.sha256")
        return
    expected = manifest_content()
    actual = path.read_text(encoding="utf-8")
    diff = manifest_diff(actual)
    result.details["manifest"] = diff
    transient_entries = [line for line in actual.splitlines() if any(token in line for token in ("__pycache__", ".pytest_cache", ".pyc", ".pyo", ".tmp", ".bak", ".coverage", "coverage.xml", ".DS_Store", "Thumbs.db"))]
    if transient_entries:
        result.errors.append(f"FILE_MANIFEST.sha256 contains cache or temporary entries: {transient_entries}")
    transient_files = sorted(
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if (
            path.is_file()
            and is_transient_file(path)
            and ".git" not in path.relative_to(ROOT).parts
        )
    )
    if transient_files:
        # Persistent Skill workspaces may intentionally retain pre-existing
        # transient files.  They remain excluded from FILE_MANIFEST and are
        # surfaced as a warning instead of forcing destructive cleanup.
        result.warnings.append(f"runtime package contains unmanifested cache or temporary files: {transient_files}")
    if actual != expected:
        summary = ", ".join(
            f"{key}={value}"
            for key, value in diff.items()
            if key != "canonicalFormatMismatch" and value
        )
        if not summary:
            summary = "canonical ordering or formatting differs"
        result.errors.append(
            "FILE_MANIFEST.sha256 is stale: "
            f"{summary}; run scripts/check_skill.py --write-manifest"
        )
    else:
        result.ok("File manifest matches runtime package")

__all__ = [
    "is_transient_file", "manifest_files", "manifest_content",
    "manifest_entry_map", "parse_manifest_entries", "manifest_diff",
    "write_manifest", "check_manifest",
]
