"""Immutable constants for structured-document merge operations."""
from __future__ import annotations

from pathlib import Path

ASSET_ROOT = Path(__file__).resolve().parents[2] / "assets"
SCHEMA_ROOT = ASSET_ROOT / "structured-docs"
ARTIFACT_SCHEMA_FILES = {
    "business_model": "business-model.schema.json",
    "api": "api.schema.json",
    "db": "db.schema.json",
}
ARTIFACT_COLLECTIONS = {
    "business_model": (
        "terms", "scenarios", "entities", "valueObjects", "aggregates",
        "invariants", "stateMachines", "services", "policies", "events",
        "businessErrors", "traceability",
    ),
    "api": ("endpoints",),
    "db": ("tables",),
}
PROJECT_FACT_ARTIFACTS = ("business_model",)
VERSION_DELTA_ARTIFACTS = ("api", "db")
BUSINESS_PREFIX_COLLECTION = {
    "TERM-": "terms", "SCN-": "scenarios", "ENT-": "entities", "VO-": "valueObjects",
    "AGG-": "aggregates", "INV-": "invariants", "SM-": "stateMachines",
    "SVC-": "services", "POL-": "policies", "EVT-": "events", "ERR-": "businessErrors",
    "TR-": "traceability",
}
INITIAL_REVISIONS = {"business_model": "BM-R00@init", "api": "API-R00@init", "db": "DB-R00@init"}
ALLOWED_OPERATIONS = {"add", "update", "deprecate"}
GENERATED_MARKER = "<!-- generated-by: common-develop/merge_docs.py -->"
