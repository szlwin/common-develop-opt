"""Immutable constants for structured-document merge operations."""
from __future__ import annotations

from pathlib import Path
from resource_paths import asset_path

ASSET_ROOT = asset_path("assets/asset-catalog.json").parent
SCHEMA_ROOT = asset_path('assets/documents/structured-docs/business-model.schema.json').parent
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
from document_core.model_rendering import GENERATED_MARKER
