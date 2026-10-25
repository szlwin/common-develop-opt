"""Technology-neutral revision provenance for incremental validity.

Provenance is a derived projection.  It never replaces the canonical artifact
revision, Review Result, Evidence, or task event authorities.
"""
from __future__ import annotations

from typing import Any, Iterable

CHANGE_CLASSES = (
    "PROCESS_ONLY",
    "DOCUMENTATION_ONLY",
    "TEST_ONLY",
    "CONFIG",
    "PRODUCTION",
    "SEMANTIC_AUTHORITY",
    "COMPOSITE",
    "UNKNOWN",
)
TRI_STATE = ("TRUE", "FALSE", "UNKNOWN")

_DEFAULTS = {
    "PROCESS_ONLY": ("FALSE", "FALSE"),
    "TEST_ONLY": ("FALSE", "FALSE"),
    "PRODUCTION": ("FALSE", "TRUE"),
    "SEMANTIC_AUTHORITY": ("TRUE", "FALSE"),
    "DOCUMENTATION_ONLY": ("UNKNOWN", "FALSE"),
    "CONFIG": ("UNKNOWN", "FALSE"),
    "COMPOSITE": ("UNKNOWN", "UNKNOWN"),
    "UNKNOWN": ("UNKNOWN", "UNKNOWN"),
}


def unique_values(values: Iterable[str] | None) -> list[str]:
    return list(dict.fromkeys(str(item).strip() for item in (values or []) if str(item).strip()))


def _tri(value: str | bool | None, default: str) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    text = str(value or "").strip().upper()
    if not text:
        return default
    if text in {"TRUE", "YES", "1"}:
        return "TRUE"
    if text in {"FALSE", "NO", "0"}:
        return "FALSE"
    if text == "UNKNOWN":
        return "UNKNOWN"
    raise ValueError(f"invalid tri-state value: {value}")


def build(
    *,
    base_revision: str,
    revision: str,
    change_class: str,
    changed_scopes: Iterable[str] | None = None,
    changed_paths: Iterable[str] | None = None,
    semantic_artifact_changed: str | bool | None = None,
    production_changed: str | bool | None = None,
) -> dict[str, Any]:
    normalized_class = str(change_class or "UNKNOWN").strip().upper()
    if normalized_class not in CHANGE_CLASSES:
        raise ValueError(f"invalid change class: {change_class}")
    base = str(base_revision or "").strip()
    current = str(revision or "").strip()
    if not current:
        raise ValueError("revision is required")
    semantic_default, production_default = _DEFAULTS[normalized_class]
    semantic = _tri(semantic_artifact_changed, semantic_default)
    production = _tri(production_changed, production_default)
    warnings: list[str] = []
    if base and base == current and normalized_class != "PROCESS_ONLY":
        warnings.append("baseRevision equals revision for a non-process-only change; verify the exact revision inputs")
    if normalized_class == "PROCESS_ONLY" and (semantic != "FALSE" or production != "FALSE"):
        raise ValueError("PROCESS_ONLY requires semanticArtifactChanged=FALSE and productionChanged=FALSE")
    return {
        "schemaVersion": 1,
        "authority": "DERIVED",
        "baseRevision": base,
        "revision": current,
        "changeClass": normalized_class,
        "changedScopes": unique_values(changed_scopes),
        "changedPaths": unique_values(changed_paths),
        "semanticArtifactChanged": semantic,
        "productionChanged": production,
        "processOnly": normalized_class == "PROCESS_ONLY" and semantic == "FALSE" and production == "FALSE",
        "warnings": warnings,
    }
