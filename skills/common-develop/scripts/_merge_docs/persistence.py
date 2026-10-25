"""Persistence orchestration for deterministic merge application.

This module owns the write-side of merge application.  It deliberately depends
on callbacks for facade-local helpers (rendering and transactional writes) so
that it can be imported independently of ``merge_docs.py``.
"""
from __future__ import annotations

import copy
import hashlib
import json
from argparse import Namespace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import agent_context
import artifact_relations
import business_flow
import document_facts
import document_portal
import render_relationships
import simple_yaml

from common_utils import now
from _merge_docs.model import (
    MergeError,
    canonical_path,
    dependency_impact_path,
    design_index_payload,
    load_manifest,
    markdown_path,
)
from _merge_docs.constants import PROJECT_FACT_ARTIFACTS


def persist_apply(
    args: Namespace,
    state: dict[str, dict[str, Any]],
    records: list[dict[str, Any]],
    summaries: list[str],
    artifact_merges: list[dict[str, Any]],
    flow_simulations: Sequence[business_flow.GroupSimulation],
    archive_plan: dict[str, Any] | None = None,
    *,
    renderers: Mapping[str, Callable[[dict[str, Any]], str]],
    markdown_target: Callable[[Path], tuple[Path, str]],
    transactional_write: Callable[[dict[Path, bytes]], None],
    public_artifact_record: Callable[[dict[str, Any], str | None], dict[str, Any]],
) -> int:
    """Apply a validated merge and atomically persist all derived artifacts.

    The function contains no CLI discovery or preview logic.  Its explicit
    callback boundary keeps the persistence module reusable and prevents a
    dependency cycle back into the ``merge_docs`` facade.
    """
    doc_root = Path(args.doc_root)
    manifest = load_manifest(doc_root)
    applied_by = agent_context.selected_agent(args)
    for record in records:
        record["appliedAt"] = now()
        record["appliedByAgent"] = applied_by
        manifest.setdefault("appliedChangeSets", []).append(record)

    _append_artifact_merge_record(
        manifest,
        args,
        artifact_merges,
        archive_plan=archive_plan,
        applied_by=applied_by,
        public_artifact_record=public_artifact_record,
    )

    payloads: dict[Path, bytes] = {}
    markdown_notes: list[tuple[Path, str]] = []
    _build_project_fact_payloads(
        payloads,
        markdown_notes,
        doc_root,
        args,
        state,
        renderers=renderers,
        markdown_target=markdown_target,
    )
    _add_artifact_payloads(payloads, doc_root, args, artifact_merges)
    _add_requirement_index_payload(payloads, doc_root)
    try:
        payloads.update(business_flow.payloads(doc_root, flow_simulations, payloads))
    except business_flow.BusinessFlowError as exc:
        raise MergeError(str(exc)) from exc
    _add_dependency_payloads(payloads, doc_root, state)
    if archive_plan is not None:
        from _merge_docs.archive_output import build_archive_output
        archive, archive_payloads = build_archive_output(
            doc_root, args, archive_plan, records, payloads
        )
        manifest.setdefault("archives", []).append(archive)
        payloads.update(archive_payloads)
    payloads[doc_root / "archive_manifest.yaml"] = simple_yaml.dumps(manifest).encode("utf-8")
    payloads.update(artifact_relations.build_payloads(doc_root, pending=payloads))
    payloads.update(document_portal.build_payloads(doc_root, versions=[args.version], pending=payloads))

    transactional_write(payloads)
    for target, note in markdown_notes:
        print(f"MARKDOWN {target} ({note})")
    print(
        f"APPLIED changesets={len(records)} operations={len(summaries)} "
        f"artifacts={len(artifact_merges)} businessFlowGroups={len(flow_simulations)}"
    )
    return 0


def _append_artifact_merge_record(
    manifest: dict[str, Any],
    args: Namespace,
    artifact_merges: Sequence[dict[str, Any]],
    *,
    archive_plan: dict[str, Any] | None,
    applied_by: str,
    public_artifact_record: Callable[[dict[str, Any], str | None], dict[str, Any]],
) -> None:
    if not artifact_merges:
        return
    artifact_record_payload = [public_artifact_record(item, None) for item in artifact_merges]
    artifact_record_digest = hashlib.sha256(
        json.dumps(artifact_record_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    artifact_record_id = f"ARTMERGE-{args.version}-{args.module}-{artifact_record_digest[:12]}"
    existing_ids = {item.get("id") for item in manifest.get("appliedChangeSets", [])}
    if artifact_record_id in existing_ids:
        return
    artifact_manifest_record: dict[str, Any] = {
        "id": artifact_record_id,
        "kind": "DETERMINISTIC_ARTIFACT_MERGE",
        "version": args.version,
        "module": args.module,
        "artifactMerges": artifact_record_payload,
        "appliedAt": now(),
        "appliedByAgent": applied_by,
    }
    if archive_plan is not None:
        artifact_manifest_record["mergeDigest"] = archive_plan["resultDigest"]
        artifact_manifest_record["conflictDecisions"] = copy.deepcopy(
            archive_plan.get("conflictDecisions", [])
        )
    manifest.setdefault("appliedChangeSets", []).append(artifact_manifest_record)


def _build_project_fact_payloads(
    payloads: dict[Path, bytes],
    markdown_notes: list[tuple[Path, str]],
    doc_root: Path,
    args: Namespace,
    state: Mapping[str, dict[str, Any]],
    *,
    renderers: Mapping[str, Callable[[dict[str, Any]], str]],
    markdown_target: Callable[[Path], tuple[Path, str]],
) -> None:
    for artifact in PROJECT_FACT_ARTIFACTS:
        document = state[artifact]
        yaml_path = canonical_path(doc_root, args.module, artifact)
        payloads[yaml_path] = simple_yaml.dumps(document).encode("utf-8")
        target, note = markdown_target(markdown_path(doc_root, args.module, artifact))
        payloads[target] = renderers[artifact](document).encode("utf-8")
        markdown_notes.append((target, note))


def _add_artifact_payloads(
    payloads: dict[Path, bytes],
    doc_root: Path,
    args: Namespace,
    artifact_merges: Sequence[dict[str, Any]],
) -> None:
    for artifact_merge in artifact_merges:
        payloads[Path(artifact_merge["targetPath"])] = bytes(artifact_merge["resultContent"])
    snapshot_merges = [
        item for item in artifact_merges
        if item.get("artifactType") in {"design_topic", "page_behavior"}
    ]
    if snapshot_merges:
        registry_path, index_content = design_index_payload(doc_root, args.module, snapshot_merges)
        payloads[registry_path] = index_content


def _add_requirement_index_payload(payloads: dict[Path, bytes], doc_root: Path) -> None:
    requirement_index = doc_root / "docs" / "requirement_list.md"
    requirement_target = requirement_index.resolve()
    if (
        all(Path(path).resolve() != requirement_target for path in payloads)
        and not requirement_index.is_file()
    ):
        payloads[requirement_index] = document_facts.requirement_list_template(
            "CURRENT", project=True
        ).encode("utf-8")


def _add_dependency_payloads(
    payloads: dict[Path, bytes],
    doc_root: Path,
    state: Mapping[str, dict[str, Any]],
) -> None:
    if "dependency_impact" not in state:
        return
    dependency_document = state["dependency_impact"]
    dependency_yaml = dependency_impact_path(doc_root)
    payloads[dependency_yaml] = simple_yaml.dumps(dependency_document).encode("utf-8")
    dependency_markdown = dependency_yaml.with_name("dependency_impact.md")
    payloads[dependency_markdown] = render_relationships.render(
        dependency_document, project_navigation=True
    ).encode("utf-8")
