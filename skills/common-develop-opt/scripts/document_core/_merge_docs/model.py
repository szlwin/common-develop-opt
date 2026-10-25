"""Structured-document validation, simulation, and merge operations."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from document_core import business_flow
import render_relationships
from document_core import simple_yaml
from document_core import page_documents
from document_core import reference_resolution
from runtime.error_catalog import ArchiveError
from runtime._core.validation import make_require_helpers

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None

from document_core._merge_docs.constants import (
    ALLOWED_OPERATIONS, ARTIFACT_COLLECTIONS, ARTIFACT_SCHEMA_FILES,
    BUSINESS_PREFIX_COLLECTION, INITIAL_REVISIONS, PROJECT_FACT_ARTIFACTS,
    SCHEMA_ROOT, VERSION_DELTA_ARTIFACTS,
)

class MergeError(ArchiveError):
    """Structured archive/merge validation error."""
    pass


def schema_validator(schema_name: str) -> Any:
    """Build each immutable package schema validator once per process."""

    if jsonschema is None:
        raise MergeError("jsonschema is required; run scripts/check_skill.py for environment diagnostics")
    schema = json.loads((SCHEMA_ROOT / schema_name).read_text(encoding="utf-8"))
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    return validator_class(schema)


def validate_json_schema(document: Any, schema_name: str, label: str) -> None:
    if jsonschema is None:
        raise MergeError("jsonschema is required; run scripts/check_skill.py for environment diagnostics")
    try:
        schema_validator(schema_name).validate(document)
    except jsonschema.ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path) or "<root>"
        raise MergeError(f"{label} schema violation at {location}: {exc.message}") from exc


def canonical_path(doc_root: Path, module: str, artifact: str) -> Path:
    suffix = "business_model" if artifact == "business_model" else artifact
    return doc_root / "docs" / "module" / module / f"{module}_{suffix}.yaml"


def markdown_path(doc_root: Path, module: str, artifact: str) -> Path:
    suffix = "business_model" if artifact == "business_model" else artifact
    return doc_root / "docs" / "module" / module / f"{module}_{suffix}.md"


def generated_root(doc_root: Path, version: str, module: str) -> Path:
    return doc_root / "version" / version / "module" / module / "generated"


def changes_root(doc_root: Path, version: str, module: str) -> Path:
    return doc_root / "version" / version / "module" / module / "changes"


def empty_document(module: str, artifact: str) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schemaVersion": 1,
        "artifact": artifact,
        "module": {"code": module, "name": module},
        "revision": INITIAL_REVISIONS[artifact],
        "baseRevision": "",
    }
    for collection in ARTIFACT_COLLECTIONS[artifact]:
        base[collection] = []
    return base


def empty_manifest() -> dict[str, Any]:
    return {"schemaVersion": 1, "appliedChangeSets": []}


def empty_changeset_template(version: str, module: str) -> dict[str, Any]:
    """Return a non-active draft that users copy and complete before validation."""
    return {
        "schemaVersion": 1,
        "changeSet": {
            "id": f"CHG-{version}-{module}-FEATURE",
            "version": version,
            "module": module,
            "featureCode": "FEATURE",
            "reason": "replace with the confirmed increment reason",
            "baseRevisions": {},
            "resultRevisions": {},
            "affectedTraceIds": [],
        },
        "operations": [],
    }


require_mapping, require_list, require_text = make_require_helpers(MergeError)


def item_key(item: dict[str, Any]) -> tuple[str, str] | None:
    for key in ("id", "traceId", "name"):
        value = item.get(key)
        if isinstance(value, str) and value:
            return key, value
    return None


def validate_unique_items(items: list[Any], label: str) -> None:
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(items):
        item = require_mapping(raw, f"{label}[{index}]")
        key = item_key(item)
        if key:
            if key in seen:
                raise MergeError(f"duplicate stable key in {label}: {key[1]}")
            seen.add(key)
        for child_key in (
            "attributes",
            "behaviors",
            "members",
            "transitions",
            "columns",
            "indexes",
            "foreignKeys",
            "fields",
        ):
            children = item.get(child_key)
            if isinstance(children, list) and children and all(isinstance(child, dict) for child in children):
                validate_unique_items(children, f"{label}.{key[1] if key else index}.{child_key}")


def validate_document(document: dict[str, Any], module: str, artifact: str) -> None:
    validate_json_schema(document, ARTIFACT_SCHEMA_FILES[artifact], artifact)
    if document.get("schemaVersion") != 1:
        raise MergeError(f"{artifact}: schemaVersion must be 1")
    if document.get("artifact") != artifact:
        raise MergeError(f"{artifact}: artifact must equal '{artifact}'")
    module_info = require_mapping(document.get("module"), f"{artifact}.module")
    if module_info.get("code") != module:
        raise MergeError(f"{artifact}: module.code must equal '{module}'")
    require_text(module_info.get("name"), f"{artifact}.module.name")
    require_text(document.get("revision"), f"{artifact}.revision")
    if not isinstance(document.get("baseRevision", ""), str):
        raise MergeError(f"{artifact}.baseRevision must be a string")
    for collection in ARTIFACT_COLLECTIONS[artifact]:
        items = require_list(document.get(collection), f"{artifact}.{collection}")
        validate_unique_items(items, f"{artifact}.{collection}")

    if artifact == "business_model":
        prefixes = {
            "terms": "TERM-",
            "scenarios": "SCN-",
            "entities": "ENT-",
            "valueObjects": "VO-",
            "aggregates": "AGG-",
            "invariants": "INV-",
            "stateMachines": "SM-",
            "businessErrors": "ERR-",
        }
        for collection, prefix in prefixes.items():
            for item in document[collection]:
                current = require_text(item.get("id"), f"business_model.{collection}.id")
                if not current.startswith(prefix):
                    raise MergeError(f"business_model.{collection} id must start with '{prefix}': {current}")
        for item in document["traceability"]:
            require_text(item.get("traceId"), "business_model.traceability.traceId")
    elif artifact == "api":
        for endpoint in document["endpoints"]:
            current = require_text(endpoint.get("id"), "api.endpoints.id")
            if not current.startswith("API-"):
                raise MergeError(f"api endpoint id must start with 'API-': {current}")
    elif artifact == "db":
        for table in document["tables"]:
            current = require_text(table.get("id"), "db.tables.id")
            if not current.startswith("DB-"):
                raise MergeError(f"db table id must start with 'DB-': {current}")


def resolve_collection(artifact: str, operation: dict[str, Any]) -> str:
    explicit = operation.get("collection")
    if explicit is not None:
        if explicit not in ARTIFACT_COLLECTIONS[artifact]:
            raise MergeError(f"unsupported collection for {artifact}: {explicit}")
        return str(explicit)
    if artifact == "api":
        return "endpoints"
    if artifact == "db":
        return "tables"
    target = require_text(operation.get("targetId"), "operation.targetId")
    for prefix, collection in BUSINESS_PREFIX_COLLECTION.items():
        if target.startswith(prefix):
            return collection
    raise MergeError(f"business_model operation requires collection for targetId: {target}")


def validate_changeset(document: dict[str, Any], version: str, module: str) -> dict[str, Any]:
    validate_json_schema(document, "changeset.schema.json", "changeset")
    if document.get("schemaVersion") != 1:
        raise MergeError("changeset schemaVersion must be 1")
    info = require_mapping(document.get("changeSet"), "changeSet")
    require_text(info.get("id"), "changeSet.id")
    if info.get("version") != version:
        raise MergeError(f"changeSet.version must equal '{version}'")
    if info.get("module") != module:
        raise MergeError(f"changeSet.module must equal '{module}'")
    require_text(info.get("featureCode"), "changeSet.featureCode")
    require_text(info.get("reason"), "changeSet.reason")
    bases = require_mapping(info.get("baseRevisions"), "changeSet.baseRevisions")
    results = require_mapping(info.get("resultRevisions"), "changeSet.resultRevisions")
    traces = require_list(info.get("affectedTraceIds"), "changeSet.affectedTraceIds")
    if any(not isinstance(item, str) or not item.strip() for item in traces):
        raise MergeError("changeSet.affectedTraceIds must contain non-empty strings")

    operations = require_list(document.get("operations"), "operations")
    if not operations:
        raise MergeError("changeset operations must not be empty")
    seen_targets: set[tuple[str, str, str]] = set()
    changed_artifacts: set[str] = set()
    for index, raw in enumerate(operations):
        operation = require_mapping(raw, f"operations[{index}]")
        artifact = operation.get("artifact")
        action = operation.get("operation")
        target = require_text(operation.get("targetId"), f"operations[{index}].targetId")
        require_text(operation.get("reason"), f"operations[{index}].reason")
        if artifact not in ARTIFACT_COLLECTIONS:
            raise MergeError(f"operations[{index}].artifact is unsupported: {artifact}")
        if action not in ALLOWED_OPERATIONS:
            raise MergeError(f"operations[{index}].operation is unsupported: {action}")
        collection = resolve_collection(str(artifact), operation)
        if action in {"add", "update"}:
            after = require_mapping(operation.get("after"), f"operations[{index}].after")
            identifier_key = "traceId" if collection == "traceability" else "id"
            supplied_identifier = after.get(identifier_key)
            if supplied_identifier is not None and supplied_identifier != target:
                raise MergeError(
                    f"operations[{index}].after.{identifier_key} must match targetId '{target}'"
                )
        marker = (str(artifact), collection, target)
        if marker in seen_targets:
            raise MergeError(f"duplicate operation target in one changeset: {artifact}/{collection}/{target}")
        seen_targets.add(marker)
        changed_artifacts.add(str(artifact))
    revision_artifacts = set(bases) | set(results)
    unsupported = sorted(revision_artifacts - set(ARTIFACT_COLLECTIONS))
    if unsupported:
        raise MergeError(f"unsupported revision artifacts: {', '.join(unsupported)}")
    if set(bases) != changed_artifacts or set(results) != changed_artifacts:
        raise MergeError(
            "changeSet baseRevisions/resultRevisions keys must exactly match operation artifacts: "
            f"expected={sorted(changed_artifacts)} bases={sorted(bases)} results={sorted(results)}"
        )
    for artifact in changed_artifacts:
        require_text(bases.get(artifact), f"changeSet.baseRevisions.{artifact}")
        require_text(results.get(artifact), f"changeSet.resultRevisions.{artifact}")
        if bases[artifact] == results[artifact]:
            raise MergeError(f"result revision must differ from base revision: {artifact}")
    return info


def merge_value(current: Any, changes: Any) -> Any:
    if isinstance(current, dict) and isinstance(changes, dict):
        result = copy.deepcopy(current)
        for key, value in changes.items():
            result[key] = merge_value(result[key], value) if key in result else copy.deepcopy(value)
        return result
    if isinstance(current, list) and isinstance(changes, list):
        combined = current + changes
        keys = [item_key(item) if isinstance(item, dict) else None for item in combined]
        if combined and all(key is not None for key in keys):
            result = copy.deepcopy(current)
            positions = {item_key(item): idx for idx, item in enumerate(result)}
            for item in changes:
                key = item_key(item)
                if key in positions:
                    result[positions[key]] = merge_value(result[positions[key]], item)
                else:
                    positions[key] = len(result)
                    result.append(copy.deepcopy(item))
            return result
        return copy.deepcopy(changes)
    return copy.deepcopy(changes)


def object_identifier(collection: str, item: dict[str, Any]) -> str:
    key = "traceId" if collection == "traceability" else "id"
    return require_text(item.get(key), f"{collection}.{key}")


def apply_changeset(
    state: dict[str, dict[str, Any]], document: dict[str, Any], version: str, module: str
) -> tuple[dict[str, Any], set[str], list[str]]:
    info = validate_changeset(document, version, module)
    bases = info["baseRevisions"]
    results = info["resultRevisions"]
    changed_artifacts = {str(operation["artifact"]) for operation in document["operations"]}
    for artifact in changed_artifacts:
        actual = state[artifact]["revision"]
        if actual != bases[artifact]:
            raise MergeError(f"stale base revision for {artifact}: expected {bases[artifact]}, actual {actual}")

    summaries: list[str] = []
    for operation in document["operations"]:
        artifact = str(operation["artifact"])
        action = str(operation["operation"])
        target = str(operation["targetId"])
        collection = resolve_collection(artifact, operation)
        items = state[artifact][collection]
        indexed = {object_identifier(collection, item): item for item in items}
        existing = indexed.get(target)
        if action == "add":
            if existing is not None:
                raise MergeError(f"add target already exists: {artifact}/{collection}/{target}")
            value = copy.deepcopy(operation["after"])
            key = "traceId" if collection == "traceability" else "id"
            value.setdefault(key, target)
            items.append(value)
        elif action == "update":
            if existing is None:
                raise MergeError(f"update target does not exist: {artifact}/{collection}/{target}")
            replacement = merge_value(existing, operation["after"])
            identifier_key = "traceId" if collection == "traceability" else "id"
            if replacement.get(identifier_key) != target:
                raise MergeError(
                    f"update cannot change stable identifier: {artifact}/{collection}/{target}"
                )
            items[items.index(existing)] = replacement
        else:
            if existing is None:
                raise MergeError(f"deprecate target does not exist: {artifact}/{collection}/{target}")
            if artifact in {"api", "db"}:
                existing["status"] = "deprecated"
            else:
                items.remove(existing)
        summaries.append(f"{action} {artifact}.{collection} {target}: {operation['reason']}")

    for artifact in changed_artifacts:
        state[artifact]["baseRevision"] = bases[artifact]
        state[artifact]["revision"] = results[artifact]
    for artifact in changed_artifacts:
        validate_document(state[artifact], module, artifact)
    return info, changed_artifacts, summaries


def table_catalog(db_document: dict[str, Any]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for table in db_document["tables"]:
        name = require_text(table.get("name"), f"db table {table.get('id')}.name")
        result[name] = {
            require_text(column.get("name"), f"db table {name} column.name")
            for column in table.get("columns", [])
        }
    return result


def validate_cross_references(state: dict[str, dict[str, Any]]) -> None:
    catalog = table_catalog(state["db"])
    for table in state["db"]["tables"]:
        table_name = table["name"]
        for foreign_key in table.get("foreignKeys", []):
            source_columns = require_list(foreign_key.get("columns"), f"foreign key {foreign_key.get('id')}.columns")
            target_table = require_text(foreign_key.get("targetTable"), f"foreign key {foreign_key.get('id')}.targetTable")
            target_columns = require_list(
                foreign_key.get("targetColumns"), f"foreign key {foreign_key.get('id')}.targetColumns"
            )
            if target_table not in catalog:
                raise MergeError(f"foreign key {foreign_key.get('id')} references missing table {target_table}")
            if not source_columns or len(source_columns) != len(target_columns):
                raise MergeError(
                    f"foreign key {foreign_key.get('id')} columns and targetColumns must be non-empty and equal length"
                )
            for column in source_columns:
                if column not in catalog[table_name]:
                    raise MergeError(
                        f"foreign key {foreign_key.get('id')} references missing DB field {table_name}.{column}"
                    )
            for column in target_columns:
                if column not in catalog[target_table]:
                    raise MergeError(
                        f"foreign key {foreign_key.get('id')} references missing DB field {target_table}.{column}"
                    )

    business = state["business_model"]
    known_ids = {
        item["id"]
        for collection in (
            "terms",
            "scenarios",
            "entities",
            "valueObjects",
            "aggregates",
            "invariants",
            "stateMachines",
            "services",
            "policies",
            "events",
            "businessErrors",
        )
        for item in business[collection]
        if "id" in item
    }
    entity_ids = {item["id"] for item in business["entities"] + business["valueObjects"]}
    invariant_ids = {item["id"] for item in business["invariants"]}
    for aggregate in business["aggregates"]:
        root = aggregate.get("root")
        if root and root not in entity_ids:
            raise MergeError(f"aggregate {aggregate['id']} references missing root {root}")
        for member in aggregate.get("members", []):
            if member not in entity_ids:
                raise MergeError(f"aggregate {aggregate['id']} references missing member {member}")
        for invariant in aggregate.get("invariantIds", []):
            if invariant not in invariant_ids:
                raise MergeError(f"aggregate {aggregate['id']} references missing invariant {invariant}")
    trace_ids = {trace["traceId"] for trace in business["traceability"]}
    business_error_ids = {item["id"] for item in business["businessErrors"]}
    for trace in business["traceability"]:
        for reference in trace.get("modelRefs", []):
            if reference not in known_ids:
                raise MergeError(f"traceability {trace['traceId']} references missing model item {reference}")
    for collection in (
        "scenarios", "entities", "valueObjects", "aggregates", "invariants",
        "stateMachines", "services", "policies", "events", "businessErrors"
    ):
        for item in business[collection]:
            for trace_id in item.get("traceIds", []):
                if trace_id not in trace_ids:
                    raise MergeError(f"business_model {item.get('id')} references missing traceId {trace_id}")
    for endpoint in state["api"]["endpoints"]:
        for trace_id in endpoint.get("traceIds", []):
            if trace_id not in trace_ids:
                raise MergeError(f"api endpoint {endpoint['id']} references missing traceId {trace_id}")
        for error_id in endpoint.get("errors", []):
            if error_id.startswith("ERR-") and error_id not in business_error_ids:
                raise MergeError(f"api endpoint {endpoint['id']} references missing business error {error_id}")
    for table in state["db"]["tables"]:
        for trace_id in table.get("traceIds", []):
            if trace_id not in trace_ids:
                raise MergeError(f"db table {table['id']} references missing traceId {trace_id}")


def empty_dependency_impact(doc_root: Path) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "artifact": "dependency_impact",
        "scope": {"project": doc_root.parent.name or "PROJECT", "version": "CURRENT"},
        "revision": "DI-R00@init",
        "baseRevision": "",
        "nodes": [],
        "relationships": [],
        "impactPolicies": [],
        "crossModuleImplementations": [],
    }


def dependency_impact_path(doc_root: Path) -> Path:
    return doc_root / "docs" / "_relations" / "dependency_impact.yaml"


def _load_dependency_impact(doc_root: Path) -> dict[str, Any]:
    path = dependency_impact_path(doc_root)
    document = require_mapping(simple_yaml.load(path), str(path)) if path.is_file() else empty_dependency_impact(doc_root)
    validate_json_schema(document, "dependency-impact.schema.json", "dependency impact")
    try:
        render_relationships.validate_artifact(document)
    except render_relationships.RelationshipError as exc:
        raise MergeError(str(exc)) from exc
    return document


def discover_dependency_changesets(args: argparse.Namespace) -> list[Path]:
    explicit = getattr(args, "changeset", None)
    if explicit:
        result: list[Path] = []
        for value in explicit:
            path = Path(value)
            document = simple_yaml.load(path)
            if isinstance(document, dict) and document.get("artifact") == "dependency_impact_changeset":
                result.append(path)
        return result
    root = changes_root(Path(args.doc_root), args.version, args.module)
    return sorted(
        path for path in root.glob("*.dependency-impact.changeset.y*ml")
        if not path.name.endswith((".template.yaml", ".template.yml"))
    )


def _validate_dependency_changeset(
    document: dict[str, Any], version: str, module: str
) -> dict[str, Any]:
    validate_json_schema(
        document, "dependency-impact.changeset.schema.json", "dependency impact changeset"
    )
    info = require_mapping(document.get("changeSet"), "dependency impact changeSet")
    if info.get("version") != version or info.get("module") != module:
        raise MergeError("dependency impact changeset version/module does not match current scope")
    if info.get("baseRevision") == info.get("resultRevision"):
        raise MergeError("dependency impact result revision must differ from base revision")
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(require_list(document.get("operations"), "dependency impact operations")):
        operation = require_mapping(raw, f"dependency impact operations[{index}]")
        marker = (str(operation.get("collection")), str(operation.get("targetId")))
        if marker in seen:
            raise MergeError(f"duplicate dependency impact operation target: {marker}")
        seen.add(marker)
        if operation.get("operation") in {"add", "update"}:
            after = require_mapping(operation.get("after"), f"dependency impact operations[{index}].after")
            if after.get("id") not in {None, operation.get("targetId")}:
                raise MergeError(
                    f"dependency impact operations[{index}].after.id must match targetId"
                )
    return info


def simulate_dependency_impact(
    args: argparse.Namespace, applied_ids: set[str]
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str], list[str]]:
    doc_root = Path(args.doc_root)
    state = _load_dependency_impact(doc_root)
    records: list[dict[str, Any]] = []
    summaries: list[str] = []
    skipped: list[str] = []
    for path in discover_dependency_changesets(args):
        document = require_mapping(simple_yaml.load(path), str(path))
        info = _validate_dependency_changeset(document, args.version, args.module)
        change_id = str(info["id"])
        if change_id in applied_ids:
            skipped.append(change_id)
            continue
        if state.get("revision") != info["baseRevision"]:
            raise MergeError(
                "stale base revision for dependency_impact: "
                f"expected {info['baseRevision']}, actual {state.get('revision')}"
            )
        for operation in document["operations"]:
            collection = str(operation["collection"])
            target_id = str(operation["targetId"])
            action = str(operation["operation"])
            values = require_list(state.get(collection), f"dependency impact.{collection}")
            matches = [item for item in values if isinstance(item, dict) and item.get("id") == target_id]
            existing = matches[0] if matches else None
            if action == "add":
                if existing is not None:
                    raise MergeError(f"dependency impact add target already exists: {collection}/{target_id}")
                value = copy.deepcopy(operation["after"])
                value.setdefault("id", target_id)
                value.setdefault("status", "ACTIVE")
                values.append(value)
            elif action == "update":
                if existing is None:
                    raise MergeError(f"dependency impact update target does not exist: {collection}/{target_id}")
                value = merge_value(existing, operation["after"])
                if value.get("id") != target_id:
                    raise MergeError(f"dependency impact update cannot change stable ID: {target_id}")
                values[values.index(existing)] = value
            else:
                if existing is None:
                    raise MergeError(f"dependency impact deprecate target does not exist: {collection}/{target_id}")
                existing["status"] = "DEPRECATED"
            summaries.append(f"{action} dependency_impact.{collection} {target_id}: {operation['reason']}")
        state["baseRevision"] = info["baseRevision"]
        state["revision"] = info["resultRevision"]
        state["scope"]["version"] = args.version
        validate_json_schema(state, "dependency-impact.schema.json", "dependency impact")
        try:
            render_relationships.validate_artifact(state)
        except render_relationships.RelationshipError as exc:
            raise MergeError(str(exc)) from exc
        try:
            source_ref = path.relative_to(doc_root).as_posix()
        except ValueError:
            source_ref = str(path.resolve())
        records.append({
            "id": change_id,
            "kind": "DEPENDENCY_IMPACT_CHANGESET",
            "version": args.version,
            "module": args.module,
            "reason": info["reason"],
            "artifacts": ["dependency_impact"],
            "source": source_ref,
            "sourceRef": source_ref,
            "sourceDigest": hashlib.sha256(path.read_bytes()).hexdigest(),
            "archiveMode": "PROJECT_FACT_MERGE",
            "projectFactArtifacts": ["dependency_impact"],
            "versionDeltaArtifacts": [],
            "baseRevisions": {"dependency_impact": info["baseRevision"]},
            "resultRevisions": {"dependency_impact": info["resultRevision"]},
            "affectedTraceIds": [],
            "affectedStableIds": sorted(str(item["targetId"]) for item in document["operations"]),
        })
    return state, records, summaries, skipped


_DESIGN_ID_RE = re.compile(r"\bDESIGN-[A-Z0-9_]+-[A-Z0-9_-]+\b")
_PAGE_ID_RE = re.compile(r"\bPAGE-[A-Z0-9_]+-[A-Z0-9_-]+\b")
_STABLE_ID_RE = re.compile(
    r"\b(?:REQ-[A-Z0-9_-]+|[A-Z][A-Z0-9_-]*-F[0-9]+|PAGE(?:ACT)?-[A-Z0-9_-]+|"
    r"DESIGN-[A-Z0-9_-]+|FLOW-[A-Z0-9_-]+|(?:AGG|INV|SM|EVT)-[A-Z0-9_-]+|"
    r"CMI-[A-Z0-9_-]+|(?:AC|BR|CR|TR|TC|CASE)-[A-Z0-9_-]+)\b"
)


def _markdown_meta(text: str, label: str) -> str:
    quote = re.search(rf"^>\s*{re.escape(label)}：`([^`]*)`", text, re.M | re.I)
    if quote:
        return quote.group(1).strip()
    table = re.search(rf"^\|\s*{re.escape(label)}\s*\|\s*([^|]+)\|", text, re.M | re.I)
    return table.group(1).strip().strip("`") if table else ""


def _normalized_base_revision(value: str) -> str:
    return "" if not value or value.startswith(("无", "N/A", "首次")) else value


def _rewrite_snapshot_navigation(
    text: str, *, module: str, version: str, target: Path, doc_root: Path
) -> str:
    project_home = os.path.relpath(doc_root / "docs" / "README.md", target.parent).replace(os.sep, "/")
    version_summary = os.path.relpath(
        doc_root / "version" / version / "version_summary.md", target.parent
    ).replace(os.sep, "/")
    requirement_list = os.path.relpath(
        doc_root / "version" / version / "requirement" / "requirement_list.md", target.parent
    ).replace(os.sep, "/")
    module_desc = os.path.relpath(
        doc_root / "docs" / "module" / module / f"{module}_desc.md", target.parent
    ).replace(os.sep, "/")
    navigation = (
        f"> 文档导航：[项目文档首页]({project_home}) · [来源版本摘要]({version_summary}) · "
        f"[来源版本需求列表]({requirement_list}) · [模块说明]({module_desc})"
    )
    if re.search(r"^>\s*文档导航：.*$", text, re.M):
        text = re.sub(r"^>\s*文档导航：.*$", navigation, text, count=1, flags=re.M)
    else:
        lines = text.splitlines()
        lines[1:1] = ["", navigation]
        text = "\n".join(lines)
    return text.rstrip() + "\n"


def _snapshot_record(
    doc_root: Path,
    version: str,
    module: str,
    source: Path,
    artifact_type: str,
    page_document: page_documents.PageDocument | None = None,
) -> dict[str, Any] | None:
    source_text = source.read_text(encoding="utf-8")
    snapshot_text = source_text
    if artifact_type == "design_topic":
        stable_id = _markdown_meta(source_text, "Design topic")
        if not _DESIGN_ID_RE.fullmatch(stable_id):
            raise MergeError(f"design source must declare one stable Design topic ID: {source}")
        revision = _markdown_meta(source_text, "Design revision")
        base_revision = _normalized_base_revision(_markdown_meta(source_text, "Base revision"))
        topic = stable_id.removeprefix(f"DESIGN-{module}-")
        if topic == stable_id:
            raise MergeError(f"design topic does not belong to module {module}: {stable_id}")
        target = doc_root / "docs" / "module" / module / "design" / f"{topic.lower().replace('_', '-')}.md"
    else:
        page_ids = sorted(set(_PAGE_ID_RE.findall(source_text)))
        if not page_ids:
            return None
        if page_document is None:
            raise MergeError(f"page snapshot is missing canonical page identity: {source}")
        stable_id = page_document.stable_id
        revision = _markdown_meta(source_text, "Design revision")
        base_revision = _normalized_base_revision(_markdown_meta(source_text, "Base revision"))
        target_name = f"{module}_page_design.md" if page_document.legacy else source.name
        target = doc_root / "docs" / "module" / module / "design" / "pages" / target_name
    if not revision:
        raise MergeError(f"snapshot source is missing Design revision: {source}")
    target_text = target.read_text(encoding="utf-8") if target.is_file() else ""
    target_revision = _markdown_meta(target_text, "Design revision") if target_text else ""
    if artifact_type == "design_topic" and target_text:
        target_stable_id = _markdown_meta(target_text, "Design topic")
        if target_stable_id != stable_id:
            raise MergeError(
                f"design topic target collision: {stable_id} maps to {target}, "
                f"already owned by {target_stable_id or '<missing>'}"
            )
    if artifact_type == "page_behavior" and target_text and not (page_document and page_document.legacy):
        target_page_ids = sorted(set(_PAGE_ID_RE.findall(target_text)))
        target_stable_id = _markdown_meta(target_text, "主页面编号")
        if target_stable_id and target_stable_id != stable_id:
            raise MergeError(
                f"page target collision: {stable_id} maps to {target}, "
                f"already owned by {target_stable_id}"
            )
        if not target_stable_id and stable_id not in target_page_ids:
            raise MergeError(
                f"page target collision: {stable_id} maps to {target}, existing identity is ambiguous"
            )
    if artifact_type == "page_behavior":
        link_errors = reference_resolution.validate_markdown_links(source, source_text, doc_root)
        if link_errors:
            raise MergeError("; ".join(link_errors))
        snapshot_text = reference_resolution.rebase_markdown_links(
            source_text,
            source,
            target,
            doc_root,
        )
    result_text = _rewrite_snapshot_navigation(
        snapshot_text, module=module, version=version, target=target, doc_root=doc_root
    )
    if target_text:
        if revision == target_revision:
            if result_text.encode("utf-8") == target_text.encode("utf-8"):
                return None
            raise MergeError(f"snapshot content changed without a new revision: {stable_id} {revision}")
        if base_revision != target_revision:
            raise MergeError(
                f"stale base revision for {stable_id}: expected {base_revision or '<empty>'}, "
                f"actual {target_revision}"
            )
    elif base_revision and base_revision not in {"DESIGN-R00@init", "PAGE-R00@init"}:
        raise MergeError(
            f"new snapshot {stable_id} must use an empty or initial base revision, got {base_revision}"
        )
    source_bytes = source_text.encode("utf-8")
    target_bytes = target_text.encode("utf-8")
    result_bytes = result_text.encode("utf-8")
    record = {
        "artifactType": artifact_type,
        "stableId": stable_id,
        "module": module,
        "sourceRef": source.relative_to(doc_root).as_posix(),
        "targetRef": target.relative_to(doc_root).as_posix(),
        "baseRevision": base_revision,
        "sourceRevision": revision,
        "resultRevision": revision,
        "sourceDigest": hashlib.sha256(source_bytes).hexdigest(),
        "targetDigest": hashlib.sha256(target_bytes).hexdigest(),
        "resultDigest": hashlib.sha256(result_bytes).hexdigest(),
        "affectedStableIds": sorted(set(_STABLE_ID_RE.findall(source_text)) | {stable_id}),
        "status": "READY",
    }
    validate_json_schema(record, "design-topic.schema.json", f"{artifact_type} snapshot")
    record.update({"sourcePath": source, "targetPath": target, "resultContent": result_bytes})
    return record


def simulate_design_snapshots(
    doc_root: Path, version: str, module: str
) -> list[dict[str, Any]]:
    source_root = doc_root / "version" / version / "module" / module
    sources = [source_root / f"{module}_design.md"]
    if (source_root / "design").is_dir():
        sources.extend(sorted((source_root / "design").glob("*.md")))
    try:
        pages = page_documents.discover_module_page_documents(source_root, module)
        page_errors = page_documents.validate_page_document_links(doc_root, pages)
        if page_errors:
            raise MergeError("; ".join(page_errors))
    except page_documents.PageDocumentError as exc:
        raise MergeError(str(exc)) from exc
    page_sources = {page.path.resolve(): page for page in pages}
    sources.extend(page.path for page in pages)
    records: list[dict[str, Any]] = []
    stable_ids: set[str] = set()
    target_paths: set[Path] = set()
    for source in sources:
        if not source.is_file():
            continue
        page_document = page_sources.get(source.resolve())
        artifact_type = "page_behavior" if page_document is not None else "design_topic"
        record = _snapshot_record(
            doc_root,
            version,
            module,
            source,
            artifact_type,
            page_document=page_document,
        )
        if record is None:
            continue
        if record["stableId"] in stable_ids:
            raise MergeError(f"multiple version sources modify the same stable snapshot: {record['stableId']}")
        target_path = Path(record["targetPath"]).resolve()
        if target_path in target_paths:
            raise MergeError(f"multiple stable snapshots map to the same target: {record['targetRef']}")
        stable_ids.add(str(record["stableId"]))
        target_paths.add(target_path)
        records.append(record)
    return records


def public_snapshot_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: record[key]
        for key in (
            "artifactType", "stableId", "module", "sourceRef", "targetRef",
            "baseRevision", "sourceRevision", "resultRevision", "sourceDigest",
            "targetDigest", "resultDigest", "affectedStableIds", "status",
        )
    }


def design_index_payload(
    doc_root: Path, module: str, pending_records: list[dict[str, Any]]
) -> tuple[Path, bytes]:
    index = doc_root / "docs" / "module" / module / f"{module}_design.md"
    design_root = doc_root / "docs" / "module" / module / "design"
    targets: dict[Path, tuple[Path, str]] = {}
    if design_root.is_dir():
        targets.update({
            path.resolve(): (path, "页面行为" if "pages" in path.parts else "设计主题")
            for path in design_root.rglob("*.md") if path.is_file()
        })
    for record in pending_records:
        path = Path(record["targetPath"])
        targets[path.resolve()] = (
            path, "页面行为" if record["artifactType"] == "page_behavior" else "设计主题"
        )
    lines = [
        "<!-- generated-by: common-develop/merge_docs.py design-index -->",
        f"# {module} 当前设计",
        "",
        f"> 导航：[项目文档首页](../README.md) · [模块说明]({module}_desc.md)",
        "",
        "| 类型 | 稳定主题/页面 | 当前文档 | Revision |",
        "| --- | --- | --- | --- |",
    ]
    by_target = {str(Path(record["targetPath"]).resolve()): record for record in pending_records}
    for resolved, (path, label) in sorted(targets.items(), key=lambda item: item[1][0].as_posix()):
        record = by_target.get(str(resolved))
        text = (
            bytes(record["resultContent"]).decode("utf-8")
            if record else path.read_text(encoding="utf-8")
        )
        match = _DESIGN_ID_RE.search(text) or _PAGE_ID_RE.search(text)
        stable = str(record.get("stableId")) if record else (match.group(0) if match else path.stem)
        revision = str(record.get("resultRevision")) if record else (_markdown_meta(text, "Design revision") or "-")
        link = os.path.relpath(path, index.parent).replace(os.sep, "/")
        lines.append(f"| {label} | `{stable}` | [打开]({link}) | `{revision}` |")
    if not targets:
        lines.append("| - | - | - | - |")
    return index, ("\n".join(lines).rstrip() + "\n").encode("utf-8")


def load_manifest(doc_root: Path) -> dict[str, Any]:
    path = doc_root / "archive_manifest.yaml"
    manifest = require_mapping(simple_yaml.load(path), str(path)) if path.is_file() else empty_manifest()
    validate_json_schema(manifest, "archive-manifest.schema.json", "archive manifest")
    if manifest.get("schemaVersion") != 1:
        raise MergeError("archive_manifest.yaml schemaVersion must be 1")
    records = require_list(manifest.get("appliedChangeSets"), "archive_manifest.appliedChangeSets")
    seen: set[str] = set()
    for index, raw in enumerate(records):
        record = require_mapping(raw, f"archive_manifest.appliedChangeSets[{index}]")
        change_set_id = require_text(record.get("id"), f"archive_manifest.appliedChangeSets[{index}].id")
        if change_set_id in seen:
            raise MergeError(f"duplicate changeSet id in archive manifest: {change_set_id}")
        seen.add(change_set_id)
    return manifest


def _record_source_path(doc_root: Path, record: dict[str, Any]) -> Path:
    source = record.get("sourceRef") or record.get("source")
    path = Path(require_text(source, f"changeSet {record.get('id', '')}.sourceRef"))
    return path if path.is_absolute() else doc_root / path


def _filtered_delta_changeset(
    document: dict[str, Any], artifacts: set[str]
) -> dict[str, Any] | None:
    operations = [
        copy.deepcopy(item)
        for item in require_list(document.get("operations"), "operations")
        if item.get("artifact") in artifacts
    ]
    if not operations:
        return None
    selected = {str(item["artifact"]) for item in operations}
    result = copy.deepcopy(document)
    result["operations"] = operations
    info = require_mapping(result.get("changeSet"), "changeSet")
    info["baseRevisions"] = {
        key: value for key, value in require_mapping(info.get("baseRevisions"), "changeSet.baseRevisions").items()
        if key in selected
    }
    info["resultRevisions"] = {
        key: value for key, value in require_mapping(info.get("resultRevisions"), "changeSet.resultRevisions").items()
        if key in selected
    }
    return result


def _replay_version_deltas(
    doc_root: Path,
    module: str,
    state: dict[str, dict[str, Any]],
    manifest: dict[str, Any],
) -> None:
    for raw in manifest.get("appliedChangeSets", []):
        record = require_mapping(raw, "archive_manifest.appliedChangeSets[]")
        if record.get("module") != module:
            continue
        affected = set(record.get("artifacts") or []) & set(VERSION_DELTA_ARTIFACTS)
        if not affected:
            continue
        if not affected:
            continue
        source_path = _record_source_path(doc_root, record)
        if not source_path.is_file():
            raise MergeError(f"archived API/DB delta source does not exist: {source_path}")
        expected_digest = record.get("sourceDigest")
        if expected_digest:
            actual_digest = hashlib.sha256(source_path.read_bytes()).hexdigest()
            if actual_digest != expected_digest:
                raise MergeError(f"archived API/DB delta source digest mismatch: {source_path}")
        document = require_mapping(simple_yaml.load(source_path), str(source_path))
        filtered = _filtered_delta_changeset(document, affected)
        if filtered is not None:
            apply_changeset(state, filtered, str(record.get("version") or ""), module)


def load_state(
    doc_root: Path, module: str, manifest: dict[str, Any] | None = None
) -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    for artifact in PROJECT_FACT_ARTIFACTS:
        path = canonical_path(doc_root, module, artifact)
        if not path.is_file():
            raise MergeError(f"missing canonical YAML: {path}; run init first")
        document = require_mapping(simple_yaml.load(path), str(path))
        validate_document(document, module, artifact)
        state[artifact] = document
    for artifact in VERSION_DELTA_ARTIFACTS:
        state[artifact] = empty_document(module, artifact)
    _replay_version_deltas(
        doc_root,
        module,
        state,
        manifest or load_manifest(doc_root),
    )
    return state


def discover_changesets(args: argparse.Namespace) -> list[Path]:
    explicit = getattr(args, "changeset", None)
    if explicit:
        result: list[Path] = []
        for value in explicit:
            path = Path(value)
            document = simple_yaml.load(path)
            if not isinstance(document, dict) or document.get("artifact") != "dependency_impact_changeset":
                result.append(path)
        return result
    return sorted(
        path
        for path in changes_root(Path(args.doc_root), args.version, args.module).glob("*.yaml")
        if not path.name.endswith(".template.yaml")
        and not path.name.endswith(".dependency-impact.changeset.yaml")
    )


def simulate(args: argparse.Namespace) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]], list[str], list[str]]:
    doc_root = Path(args.doc_root)
    manifest = load_manifest(doc_root)
    state = load_state(doc_root, args.module, manifest)
    applied = {item.get("id") for item in manifest.get("appliedChangeSets", [])}
    records: list[dict[str, Any]] = []
    summaries: list[str] = []
    skipped: list[str] = []
    paths = discover_changesets(args)
    seen_change_set_ids: set[str] = set()
    for path in paths:
        document = require_mapping(simple_yaml.load(path), str(path))
        info = validate_changeset(document, args.version, args.module)
        has_api_db_delta = any(
            operation.get("artifact") in set(VERSION_DELTA_ARTIFACTS)
            for operation in document.get("operations", [])
            if isinstance(operation, dict)
        )
        if has_api_db_delta:
            expected_root = changes_root(doc_root, args.version, args.module).resolve()
            try:
                path.resolve().relative_to(expected_root)
            except ValueError as exc:
                raise MergeError(
                    f"API/DB delta changeset must stay under the current version changes directory: {expected_root}"
                ) from exc
        if info["id"] in seen_change_set_ids:
            raise MergeError(f"duplicate changeSet id in current input: {info['id']}")
        seen_change_set_ids.add(info["id"])
        if info["id"] in applied:
            skipped.append(info["id"])
            continue
        info, changed, current_summaries = apply_changeset(state, document, args.version, args.module)
        validate_cross_references(state)
        known_trace_ids = {
            item["traceId"] for item in state["business_model"]["traceability"] if "traceId" in item
        }
        missing_affected = sorted(set(info["affectedTraceIds"]) - known_trace_ids)
        if missing_affected:
            raise MergeError(
                "changeSet.affectedTraceIds reference missing traceability items: "
                + ", ".join(missing_affected)
            )
        try:
            source_ref = path.relative_to(doc_root).as_posix()
        except ValueError:
            source_ref = str(path.resolve())
        records.append(
            {
                "id": info["id"],
                "version": args.version,
                "module": args.module,
                "featureCode": info["featureCode"],
                "reason": info["reason"],
                "artifacts": sorted(changed),
                "source": source_ref,
                "sourceRef": source_ref,
                "sourceDigest": hashlib.sha256(path.read_bytes()).hexdigest(),
                "archiveMode": "VERSION_DELTA_ARCHIVE" if changed & set(VERSION_DELTA_ARTIFACTS) else "PROJECT_FACT_MERGE",
                "projectFactArtifacts": sorted(changed & set(PROJECT_FACT_ARTIFACTS)),
                "versionDeltaArtifacts": sorted(changed & set(VERSION_DELTA_ARTIFACTS)),
                "baseRevisions": {artifact: info["baseRevisions"][artifact] for artifact in sorted(changed)},
                "resultRevisions": {artifact: info["resultRevisions"][artifact] for artifact in sorted(changed)},
                "affectedTraceIds": sorted(set(info["affectedTraceIds"])),
            }
        )
        summaries.extend(current_summaries)
    dependency_history = [
        item for item in manifest.get("appliedChangeSets", [])
        if "dependency_impact" in set(item.get("artifacts") or [])
    ]
    if dependency_history:
        canonical_dependency = dependency_impact_path(doc_root)
        if not canonical_dependency.is_file():
            raise MergeError(
                "archive manifest records dependency_impact but the canonical project fact is missing"
            )
        canonical_revision = require_mapping(
            simple_yaml.load(canonical_dependency), str(canonical_dependency)
        ).get("revision")
        expected_revision = dependency_history[-1].get("resultRevisions", {}).get(
            "dependency_impact"
        )
        if expected_revision and canonical_revision != expected_revision:
            raise MergeError(
                "dependency_impact canonical revision differs from archive manifest: "
                f"expected {expected_revision}, actual {canonical_revision}"
            )
    dependency_state, dependency_records, dependency_summaries, dependency_skipped = (
        simulate_dependency_impact(args, {str(value) for value in applied})
    )
    if dependency_records:
        state["dependency_impact"] = dependency_state
        records.extend(dependency_records)
        summaries.extend(dependency_summaries)
    skipped.extend(dependency_skipped)
    return state, records, summaries, skipped


def simulate_business_flows(
    args: argparse.Namespace, artifact_merges: list[dict[str, Any]]
) -> list[business_flow.GroupSimulation]:
    manifest = load_manifest(Path(args.doc_root))
    applied = {str(item.get("id")) for item in manifest.get("appliedChangeSets", [])}
    pending_paths = {
        Path(item["targetPath"]): bytes(item["resultContent"])
        for item in artifact_merges
    }
    try:
        return business_flow.simulate(
            Path(args.doc_root), args.version, args.module, applied, pending_paths=pending_paths
        )
    except business_flow.BusinessFlowError as exc:
        raise MergeError(str(exc)) from exc

__all__ = [
    "MergeError", "schema_validator", "validate_json_schema",
    "canonical_path", "markdown_path", "generated_root", "changes_root",
    "empty_document", "empty_manifest", "empty_changeset_template",
    "require_mapping", "require_list", "require_text", "item_key",
    "validate_unique_items", "validate_document", "resolve_collection",
    "validate_changeset", "merge_value", "object_identifier",
    "apply_changeset", "table_catalog", "validate_cross_references",
    "empty_dependency_impact", "dependency_impact_path", "discover_dependency_changesets",
    "simulate_dependency_impact", "simulate_design_snapshots", "public_snapshot_record",
    "design_index_payload",
    "load_manifest", "load_state", "discover_changesets", "simulate",
    "simulate_business_flows",
]
