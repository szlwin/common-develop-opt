"""Read-only consistency and content bindings for declared Review inputs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from document_core import business_flow
from document_core import requirement_doc
from document_core import simple_yaml
from document_core.common_utils import sha256_file
from document_core.reference_resolution import resolve_reference


def flow_inputs(doc_root: Path, version: str, flow_ids: set[str]) -> dict[str, str]:
    if not flow_ids:
        return {}
    from document_core._merge_docs.model import load_manifest

    manifest = load_manifest(doc_root)
    applied = {str(item["id"]) for item in manifest["appliedChangeSets"]}
    files: set[Path] = set()
    manifest_path = doc_root / "archive_manifest.yaml"
    if manifest_path.exists():
        files.add(manifest_path)
    canonical_documents = {
        path: simple_yaml.load(path)
        for path in sorted((doc_root / "docs" / "_flows").glob("*_flow.yaml"))
    }
    known_groups = {
        document["flowGroup"]["code"]
        for document in canonical_documents.values()
        if any(flow["id"] in flow_ids for flow in document.get("flows", []))
    }
    modules: dict[str, set[str]] = {}
    changes_root = doc_root / "version" / version / "module" / "_flows"
    for path in sorted(changes_root.glob("*/changes/*.yaml")):
        if path.name.endswith(".template.yaml"):
            continue
        raw = path.read_text(encoding="utf-8")
        try:
            document = simple_yaml.loads(raw)
        except simple_yaml.YamlError as exc:
            if path.parent.parent.name in known_groups or any(flow_id in raw for flow_id in flow_ids):
                raise ValueError(f"INVALID_FLOW_CHANGESET: {path}: {exc}") from exc
            # An invalid draft in an unrelated group is not an input. Relevant
            # groups are fully parsed again during replay, including siblings.
            continue
        if not isinstance(document, dict) or document.get("artifact") != "business_flow_changeset":
            continue
        info = document.get("changeSet")
        operations = document.get("operations", [])
        if not isinstance(info, dict) or not isinstance(operations, list):
            raise ValueError(f"INVALID_FLOW_CHANGESET: {path}")
        targets = {op.get("targetId") for op in operations if isinstance(op, dict)}
        if targets & flow_ids:
            info = business_flow.validate_changeset(document, version, info.get("module"), path.parent.parent.name)
            if info["id"] not in applied:
                modules.setdefault(info["module"], set()).add(info["flowGroup"])
    found: set[str] = set()
    for module, groups in modules.items():
        for simulation in business_flow.simulate(doc_root, version, module, applied, groups=groups, require_links=False):
            root = business_flow.preview_root(doc_root, version, simulation.group)
            yaml_path = root / f"{simulation.group}_flow.preview.yaml"
            md_path = root / f"{simulation.group}_flow.preview.md"
            if not yaml_path.is_file() or simple_yaml.load(yaml_path) != simulation.document:
                raise ValueError(f"STALE_FLOW_PREVIEW: {yaml_path}; regenerate with merge_docs.py preview")
            expected_md = business_flow.render_markdown(simulation.document, doc_root, md_path)
            if not md_path.is_file() or md_path.read_text(encoding="utf-8") != expected_md:
                raise ValueError(f"STALE_FLOW_PREVIEW: {md_path}; regenerate with merge_docs.py preview")
            files.update({yaml_path, md_path})
            files.update(Path(record["source"]) for record in simulation.records)
            canonical = business_flow.canonical_yaml_path(doc_root, simulation.group)
            if canonical.is_file():
                files.add(canonical)
            found.update(flow["id"] for flow in simulation.document["flows"])
    for path, document in canonical_documents.items():
        ids = {flow["id"] for flow in document.get("flows", [])}
        if ids & (flow_ids - found):
            files.add(path)
            found.update(ids)
    if flow_ids - found:
        raise ValueError(f"MISSING_FLOW_INPUT: {sorted(flow_ids - found)}")
    return {path.relative_to(doc_root).as_posix(): sha256_file(path) for path in sorted(files)}


def collect_review_inputs(task_dir: Path, phase: str, revision: str,
                          evidence_entries: list[dict[str, Any]] | tuple = ()) -> dict[str, str]:
    """Validate existing declared inputs; never require future-stage artifacts."""
    task_dir = task_dir.resolve()
    doc_root = task_dir.parents[3]
    project_root = doc_root.parent
    version = task_dir.parent.parent.name
    canonical_dir = task_dir.parent.parent / "requirement" / task_dir.name
    requirement = canonical_dir / "requirement.md"
    files = {path for path in canonical_dir.glob("*") if path.is_file() and path.suffix in {".md", ".yaml", ".json"}}
    trace_path = task_dir / "traceability.json"
    if trace_path.is_file():
        files.add(trace_path)
    bindings: dict[str, str] = {}
    if requirement.is_file() and phase != "requirement_confirmation":
        traces = json.loads(trace_path.read_text(encoding="utf-8"))["items"] if trace_path.exists() else []
        sources = {source.trace_id: source for source in requirement_doc.parse_trace_sources(requirement.read_text(encoding="utf-8"))}
        flow_ids: set[str] = set()
        for trace in traces:
            source = sources.get(trace["id"])
            if source is None or trace.get("status") == "NOT_APPLICABLE":
                continue
            digest = trace.get("requirement_source_digest")
            if digest and digest != source.source_digest:
                raise ValueError(f"STALE_REQUIREMENT_TRACE: {trace['id']}; run requirement_doc.py sync-traceability")
            if source.cross_module_required and (
                not trace.get("impact_required") or not trace.get("cross_module_implementation_required")
            ):
                raise ValueError(f"MISSING_CROSS_MODULE_IMPACT: {trace['id']}; synchronize module impact flags")
            for flag, field in (("impact_required", "dependency_impact_refs"),
                                ("cross_module_implementation_required", "cross_module_implementation_refs")):
                if trace.get(flag) and not trace.get(field):
                    raise ValueError(f"MISSING_IMPACT_REFERENCE: {trace['id']}.{field}")
            for field in ("dependency_impact_refs", "cross_module_implementation_refs", "business_model_refs", "design_refs"):
                for ref in trace.get(field, []):
                    resolved = resolve_reference(ref, doc_root, label=f"{trace['id']}.{field}")
                    files.add(resolved.path)
            flow_ids.update(trace.get("business_flow_refs", []))
        bindings.update({f"{doc_root.name}/{key}": value for key, value in flow_inputs(doc_root, version, flow_ids).items()})
    for entry in evidence_entries:
        if entry.get("revision") != revision or entry.get("capture_mode") != "SNAPSHOT" or not entry.get("source_ref"):
            continue
        source = (task_dir / entry["source_ref"].split("#", 1)[0]).resolve()
        if not source.is_relative_to(project_root):
            raise ValueError(f"REVIEW_SOURCE_OUTSIDE_PROJECT: {entry['evidence_id']}")
        if not source.is_file() or sha256_file(source) != entry.get("digest"):
            raise ValueError(f"STALE_REVIEW_SOURCE: {entry['evidence_id']}; capture evidence for the current content/revision")
        files.add(source)
    for path in sorted(files):
        if not path.resolve().is_relative_to(project_root):
            raise ValueError(f"REVIEW_SOURCE_OUTSIDE_PROJECT: {path}")
        bindings[path.relative_to(project_root).as_posix()] = sha256_file(path)
    return bindings
