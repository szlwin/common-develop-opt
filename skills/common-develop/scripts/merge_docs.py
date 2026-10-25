#!/usr/bin/env python3
"""Validate and archive version increments without materializing full API/DB documents."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from dataclasses import dataclass

import simple_yaml
from common_utils import atomic_write_text, now, sha256_file as _sha256_file
import agent_context
import contract_normalization
import document_facts
import document_portal
import artifact_relations
import business_flow
import render_relationships
import work_mode
import work_mode_events
import long_task
import doc_layout
from error_catalog import emit_cli_error

try:
    import jsonschema
except ImportError:  # pragma: no cover - validated by scripts/check_skill.py
    jsonschema = None


ASSET_ROOT = Path(__file__).resolve().parent.parent / "assets"
SCHEMA_ROOT = ASSET_ROOT / "structured-docs"
try:
    RUNTIME_CONTRACT = contract_normalization.load_runtime_contract(ASSET_ROOT / "runtime-contract.json")
except contract_normalization.ContractNormalizationError as exc:  # package corruption; checked again by check_skill.py
    raise RuntimeError(f"cannot load common-develop runtime contract: {exc}") from exc

from _merge_docs.constants import (
    ALLOWED_OPERATIONS, ARTIFACT_COLLECTIONS, ARTIFACT_SCHEMA_FILES,
    BUSINESS_PREFIX_COLLECTION, GENERATED_MARKER, INITIAL_REVISIONS,
    PROJECT_FACT_ARTIFACTS, SCHEMA_ROOT, VERSION_DELTA_ARTIFACTS,
)

_ARCHIVE_GATE_MAP = RUNTIME_CONTRACT.get("archiveGateMap", {})
_CANONICAL_PHASES = [
    phase.get("id")
    for phase in RUNTIME_CONTRACT.get("canonicalPhases", [])
    if phase.get("phaseKind", "CANONICAL") == "CANONICAL"
]
try:
    ARCHIVE_GATE_KEYS = tuple(_ARCHIVE_GATE_MAP[phase] for phase in _CANONICAL_PHASES)
except KeyError as exc:
    raise RuntimeError(f"runtime contract has no archive gate for canonical phase: {exc.args[0]}") from exc
ARCHIVE_CORE_GATES = tuple(gate for gate in ARCHIVE_GATE_KEYS if gate != "businessModel")


from _merge_docs.model import (
    MergeError, canonical_path, changes_root, design_index_payload,
    discover_changesets, empty_changeset_template, empty_document, empty_manifest, generated_root,
    load_manifest, markdown_path, public_snapshot_record, require_list, require_mapping, require_text,
    simulate, simulate_business_flows, simulate_design_snapshots, validate_json_schema,
)
from _merge_docs.rendering import (
    md_cell, render_business_model,
)
from _merge_docs.validation import (
    plan_comparable as _plan_comparable,
    validate_artifact_merges,
    validate_gate_outcome,
)
from _merge_docs.archive_validation import (
    standard_skip_archive_gates as _standard_skip_archive_gates,
    validate_archive_runtime_refs as _validate_archive_runtime_refs,
)
from _merge_docs.archive_plan import validate_merge_plan as _validate_merge_plan_impl
from _merge_docs.persistence import persist_apply as _persist_apply_impl


RENDERERS = {
    "business_model": render_business_model,
}


@dataclass
class PendingMerge:
    """All deterministic merge inputs shared by validate/preview/apply/archive."""

    state: dict[str, dict[str, Any]]
    records: list[dict[str, Any]]
    summaries: list[str]
    skipped: list[str]
    artifact_merges: list[dict[str, Any]]
    flow_simulations: list[business_flow.GroupSimulation]


def gather_pending(args: argparse.Namespace) -> PendingMerge:
    """Collect every pending merge source once for a command invocation."""
    state, records, summaries, skipped = simulate(args)
    artifact_merges = simulate_artifact_merges(Path(args.doc_root), args.version, args.module)
    flow_simulations = simulate_business_flows(args, artifact_merges)
    for item in flow_simulations:
        records.extend(item.records)
        summaries.extend(item.summaries)
        skipped.extend(item.skipped)
    return PendingMerge(
        state=state,
        records=records,
        summaries=summaries,
        skipped=skipped,
        artifact_merges=artifact_merges,
        flow_simulations=flow_simulations,
    )


def atomic_yaml(path: Path, document: dict[str, Any]) -> None:
    atomic_write_text(path, simple_yaml.dumps(document))


def markdown_target(path: Path) -> tuple[Path, str]:
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if GENERATED_MARKER not in existing:
            sibling = path.with_name(path.stem + ".generated.md")
            if sibling.exists() and GENERATED_MARKER not in sibling.read_text(encoding="utf-8"):
                raise MergeError(
                    f"cannot safely generate Markdown: both canonical and generated sibling are hand-written: "
                    f"{path}, {sibling}"
                )
            return sibling, "existing hand-written Markdown preserved; generated sibling created"
    return path, "generated"


def transactional_write(payloads: dict[Path, bytes]) -> None:
    """Replace a related file set as one best-effort transaction with rollback."""
    # macOS exposes the same temporary directory through both /var and
    # /private/var.  Collapse aliases before creating sibling .tmp files so a
    # logically identical target is replaced exactly once.
    payloads = {Path(path).resolve(): content for path, content in payloads.items()}
    backups: dict[Path, bytes | None] = {}
    temporary_paths: dict[Path, Path] = {}
    replaced: list[Path] = []
    try:
        for path, content in payloads.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            backups[path] = path.read_bytes() if path.exists() else None
            temporary = path.with_name(path.name + ".tmp")
            temporary.write_bytes(content)
            temporary_paths[path] = temporary
        for path, temporary in temporary_paths.items():
            os.replace(temporary, path)
            replaced.append(path)
    except OSError:
        for temporary in temporary_paths.values():
            if temporary.exists():
                temporary.unlink()
        for path in reversed(replaced):
            previous = backups[path]
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                rollback = path.with_name(path.name + ".rollback.tmp")
                rollback.write_bytes(previous)
                os.replace(rollback, path)
        raise


def merge_digest(
    state: dict[str, dict[str, Any]],
    records: list[dict[str, Any]],
    artifact_merges: list[dict[str, Any]] | None = None,
    work_mode_record: dict[str, Any] | None = None,
    business_flow_merges: list[dict[str, Any]] | None = None,
) -> str:
    # Local source paths are evidence locations, not semantic merge content. Excluding
    # them keeps the digest stable when the same reviewed increment moves directories.
    semantic_records = [
        {key: value for key, value in record.items() if key not in {"source", "appliedAt", "approval", "mergePlan"}}
        for record in records
    ]
    artifact_merges = artifact_merges or []
    business_flow_merges = business_flow_merges or []
    semantic_artifact_merges = [
        {
            key: value
            for key, value in record.items()
            if key not in {"sourceRef", "targetRef", "evidenceRefs", "reason"}
        }
        for record in artifact_merges
    ]
    payload = json.dumps(
        {
            "state": state,
            "records": semantic_records,
            "artifactMerges": semantic_artifact_merges,
            "businessFlowMerges": business_flow_merges,
            "workMode": work_mode_record or {},
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def simulate_artifact_merges(doc_root: Path, version: str, module: str) -> list[dict[str, Any]]:
    records = list(document_facts.simulate_merges(doc_root, version, module))
    version_doc_root = doc_root / "version" / version / "doc"
    for source in sorted(version_doc_root.glob("*/*_desc.md")):
        participant_module = source.parent.name
        if participant_module == module:
            continue
        records.extend(
            record
            for record in document_facts.simulate_merges(doc_root, version, participant_module)
            if record.get("artifactType") == "module_desc"
        )
    records.extend(simulate_design_snapshots(doc_root, version, module))
    return records


def public_artifact_record(record: dict[str, Any], evidence_ref: str | None = None) -> dict[str, Any]:
    if record.get("artifactType") in {"design_topic", "page_behavior"}:
        value = public_snapshot_record(record)
        value["evidenceRefs"] = [evidence_ref] if evidence_ref else list(record.get("evidenceRefs", []))
        return value
    return document_facts.public_record(record, evidence_ref)


def write_artifact_previews(root: Path, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    document_records = [
        record for record in records if record.get("artifactType") in {"requirement_list", "module_desc"}
    ]
    snapshot_records = [
        record for record in records if record.get("artifactType") in {"design_topic", "page_behavior"}
    ]
    public = document_facts.write_previews(root, document_records)
    for record in snapshot_records:
        preview = root / f"{record['stableId'].lower()}.snapshot.preview.md"
        preview.write_bytes(bytes(record["resultContent"]))
        public.append(public_artifact_record(record, str(preview)))
    return sorted(public, key=lambda item: (str(item.get("artifactType")), str(item.get("targetRef"))))


def expected_public_artifact_merges(
    root: Path, records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Recreate reviewed preview records without rewriting their evidence files."""
    public: list[dict[str, Any]] = []
    for record in records:
        if record.get("artifactType") in {"design_topic", "page_behavior"}:
            evidence = root / f"{record['stableId'].lower()}.snapshot.preview.md"
        else:
            evidence = root / str(record["previewName"])
        public.append(public_artifact_record(record, str(evidence)))
    return sorted(public, key=lambda item: (str(item.get("artifactType")), str(item.get("targetRef"))))


def historical_lightweight_state(doc_root: Path, version: str) -> dict[str, Any]:
    """Read version-level mode history from the canonical event ledger."""
    return work_mode_events.historical_lightweight_state(doc_root, version)


def current_work_mode_record(doc_root: Path, version: str, *, create_default: bool = False) -> dict[str, Any]:
    try:
        mode, path = work_mode.load_mode(doc_root, version, create_default=create_default)
    except work_mode.WorkModeError as exc:
        raise MergeError(str(exc)) from exc
    try:
        work_ref = path.relative_to(doc_root).as_posix()
    except ValueError:
        work_ref = str(path)
    historical = historical_lightweight_state(doc_root, version)
    record = mode.to_record()
    record.update(
        {
            "lightweightWaived": bool(historical["waived"]),
            "workRef": work_ref,
            "workDigest": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
    )
    if historical["waived"] and not mode.lightweight:
        record["skippedPhases"] = historical["skippedPhases"]
    return record


def gate_template(status: str = "PENDING", reason: str = "") -> dict[str, Any]:
    return {
        "status": status,
        "revision": "",
        "stageOutcomeRefs": [],
        "evidenceIds": [],
        "reviewerAgents": [],
        "reason": reason,
    }


def aggregate_plan_metadata(
    state: dict[str, dict[str, Any]], records: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any], list[str], list[str]]:
    raw_artifacts = {artifact for record in records for artifact in record.get("artifacts", [])}
    affected_artifacts = sorted({
        "business_flow" if str(artifact).startswith("business_flow__") else str(artifact)
        for artifact in raw_artifacts
    })
    base_revisions: dict[str, Any] = {}
    result_revisions: dict[str, Any] = {}
    for record in records:
        for artifact, revision in record.get("baseRevisions", {}).items():
            base_revisions.setdefault(artifact, revision)
        result_revisions.update(record.get("resultRevisions", {}))
    for artifact in affected_artifacts:
        if artifact in state:
            result_revisions[artifact] = state[artifact]["revision"]
    affected_trace_ids = sorted(
        {trace_id for record in records for trace_id in record.get("affectedTraceIds", [])}
    )
    return base_revisions, result_revisions, affected_artifacts, affected_trace_ids


def archive_gate_template(
    affected_artifacts: list[str],
    work_mode_record: dict[str, Any],
    standard_skip_gates: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    gates = {gate: gate_template() for gate in ARCHIVE_GATE_KEYS}
    if "business_model" not in affected_artifacts:
        gates["businessModel"] = gate_template("NOT_APPLICABLE", "本次 merge plan 不包含 business_model 增量。")
    if work_mode_record["minimal"]:
        for gate in work_mode.minimal_archive_gate_keys():
            gates[gate] = gate_template(
                work_mode.MINIMAL_ARCHIVE_GATE_STATUS,
                work_mode.MINIMAL_SKIP_REASON,
            )
    if work_mode_record.get("lightweight") or work_mode_record.get("lightweightWaived"):
        for gate in work_mode.lightweight_archive_gate_keys():
            gates[gate] = gate_template(
                work_mode.LIGHTWEIGHT_ARCHIVE_GATE_STATUS,
                work_mode.LIGHTWEIGHT_SKIP_REASON,
            )
    gates.update(copy.deepcopy(standard_skip_gates or {}))
    return {
        "status": "DRAFT",
        "gates": gates,
        "openIssueIds": [],
        "authorizedBy": "",
        "authorizedAt": "",
        "decisionRef": "",
        "userConfirmed": False,
        "notes": (
            "用于 wk -d 将当前版本已确认事实归档；API/DB 只保留版本增量引用，不生成项目级全量文档；不是发布审批。"
            + (" 当前版本使用 wk -auto，仍需 Agent 完成门禁和授权，但无需用户确认。" if work_mode_record["auto"] else "")
        ),
    }


def build_merge_plan(
    args: argparse.Namespace,
    state: dict[str, dict[str, Any]],
    records: list[dict[str, Any]],
    public_artifact_merges: list[dict[str, Any]],
    business_flow_merges: list[dict[str, Any]],
    *,
    generated_at: str | None = None,
    archive_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    work_mode_record = current_work_mode_record(Path(args.doc_root), args.version, create_default=True)
    base_revisions, result_revisions, affected_artifacts, affected_trace_ids = aggregate_plan_metadata(state, records)
    standard_skip_gates = _standard_skip_archive_gates(
        Path(args.doc_root), args.version,
        archive_gate_map=_ARCHIVE_GATE_MAP,
        long_task_runtime=long_task,
        module=str(args.module or ""),
        affected_trace_ids=set(affected_trace_ids),
        feature_codes={
            str(record.get("featureCode")) for record in records
            if str(record.get("featureCode") or "")
        },
    )
    project_fact_artifacts = set(affected_artifacts) & set(PROJECT_FACT_ARTIFACTS)
    if "business_flow" in affected_artifacts:
        project_fact_artifacts.add("business_flow")
    if "dependency_impact" in affected_artifacts:
        project_fact_artifacts.add("dependency_impact")
    plan = {
        "schemaVersion": 1,
        "mergeMode": "VERSION_DELTA_ARCHIVE",
        "version": args.version,
        "module": args.module,
        "changeSetIds": [record["id"] for record in records],
        "baseRevisions": base_revisions,
        "resultRevisions": result_revisions,
        "structuredArtifacts": affected_artifacts,
        "projectFactArtifacts": sorted(project_fact_artifacts),
        "versionDeltaArtifacts": sorted(set(affected_artifacts) & set(VERSION_DELTA_ARTIFACTS)),
        "documentArtifacts": sorted({item["artifactType"] for item in public_artifact_merges}),
        "artifactMerges": public_artifact_merges,
        "conflictDecisions": [],
        "businessFlowMerges": business_flow_merges,
        "affectedTraceIds": affected_trace_ids,
        "workMode": work_mode_record,
        "resultDigest": merge_digest(
            state, records, public_artifact_merges, work_mode_record, business_flow_merges
        ),
        "archiveGate": archive_gate or archive_gate_template(
            affected_artifacts, work_mode_record, standard_skip_gates
        ),
        "generatedAt": generated_at or now(),
    }
    validate_json_schema(plan, "merge-plan.schema.json", "merge plan")
    return plan


def write_preview(
    args: argparse.Namespace,
    state: dict[str, dict[str, Any]],
    records: list[dict[str, Any]],
    summaries: list[str],
    skipped: list[str],
    artifact_merges: list[dict[str, Any]],
    flow_simulations: list[business_flow.GroupSimulation],
) -> dict[str, Any]:
    root = generated_root(Path(args.doc_root), args.version, args.module)
    root.mkdir(parents=True, exist_ok=True)
    for artifact in PROJECT_FACT_ARTIFACTS:
        document = state[artifact]
        suffix = "business_model"
        atomic_yaml(root / f"{args.module}_{suffix}_preview.yaml", document)
        (root / f"{args.module}_{suffix}_preview.md").write_text(RENDERERS[artifact](document), encoding="utf-8")
    for artifact in VERSION_DELTA_ARTIFACTS:
        delta = version_delta_preview(Path(args.doc_root), args.version, args.module, records, artifact)
        if delta["changes"]:
            atomic_yaml(root / f"{args.module}_{artifact}_delta_preview.yaml", delta)
            (root / f"{args.module}_{artifact}_delta_preview.md").write_text(
                render_version_delta(delta), encoding="utf-8"
            )
    if "dependency_impact" in state:
        dependency_document = state["dependency_impact"]
        atomic_yaml(root / f"{args.module}_dependency_impact_preview.yaml", dependency_document)
        (root / f"{args.module}_dependency_impact_preview.md").write_text(
            render_relationships.render(dependency_document), encoding="utf-8"
        )
    public_artifact_merges = write_artifact_previews(root, artifact_merges)
    try:
        business_flow_merges = business_flow.write_previews(
            Path(args.doc_root), args.version, flow_simulations
        )
    except business_flow.BusinessFlowError as exc:
        raise MergeError(str(exc)) from exc
    plan = build_merge_plan(args, state, records, public_artifact_merges, business_flow_merges)
    atomic_yaml(root / "merge_plan.yaml", plan)
    report = [
        "# 结构化文档增量合并预览",
        "",
        f"- 版本：{args.version}",
        f"- 模块：{args.module}",
        f"- 当前 Agent：{agent_context.selected_agent(args)}",
        f"- 基线 revisions：{plan['baseRevisions']}",
        f"- 结果 revisions：{plan['resultRevisions']}",
        f"- 工作模式：model_code={str(plan['workMode']['modelCode']).lower()} page_design={str(plan['workMode']['pageDesign']).lower()} minimal={str(plan['workMode']['minimal']).lower()} auto={str(plan['workMode']['auto']).lower()} architecture_review={str(plan['workMode']['architectureReview']).lower()} git_checkpoint={str(plan['workMode']['gitCheckpoint']).lower()} git_push={str(plan['workMode']['gitPush']).lower()} lightweight={str(plan['workMode'].get('lightweight', False)).lower()}",
        "- 归档门禁：请在同一 merge_plan.yaml 的 archiveGate 中补充证据和授权结论",
        "",
        "## 变更",
        "",
    ]
    report.extend(f"- {item}" for item in summaries)
    if business_flow_merges:
        report.extend(["", "## 业务流程事实合并", ""] + [
            f"- {item['flowGroup']}：{item['baseRevision']} -> {item['resultRevision']}"
            for item in business_flow_merges
        ])
    if public_artifact_merges:
        report.extend(["", "## Markdown 事实合并", ""] + [
            f"- {item['artifactType']}：{item['sourceRef']} -> {item['targetRef']}"
            for item in public_artifact_merges
        ])
    if skipped:
        report.extend(["", "## 已应用并跳过", ""] + [f"- {item}" for item in skipped])
    (root / "merge_report.md").write_text("\n".join(report).rstrip() + "\n", encoding="utf-8")
    return plan


def version_delta_preview(
    doc_root: Path,
    version: str,
    module: str,
    records: list[dict[str, Any]],
    artifact: str,
) -> dict[str, Any]:
    """Build a version-local API/DB delta projection from canonical changesets."""
    changes: list[dict[str, Any]] = []
    for record in records:
        if artifact not in set(record.get("versionDeltaArtifacts") or []):
            continue
        source_ref = str(record.get("sourceRef") or record.get("source") or "")
        source_path = Path(source_ref)
        if not source_path.is_absolute():
            source_path = doc_root / source_path
        document = simple_yaml.load(source_path)
        operations = [
            copy.deepcopy(item)
            for item in document.get("operations", [])
            if isinstance(item, dict) and item.get("artifact") == artifact
        ]
        if operations:
            changes.append(
                {
                    "changeSetId": record["id"],
                    "sourceRef": source_ref,
                    "sourceDigest": record.get("sourceDigest", ""),
                    "baseRevision": record.get("baseRevisions", {}).get(artifact, ""),
                    "resultRevision": record.get("resultRevisions", {}).get(artifact, ""),
                    "operations": operations,
                }
            )
    result = {
        "schemaVersion": 1,
        "artifact": f"{artifact}_delta",
        "version": version,
        "module": module,
        "authority": "VERSION_CHANGESET",
        "currentImplementationAuthority": "CODE_OPENAPI_DDL_MIGRATION",
        "changes": changes,
    }
    validate_json_schema(result, "version-delta.schema.json", f"{artifact} version delta")
    return result


def render_version_delta(document: dict[str, Any]) -> str:
    artifact = str(document["artifact"]).removesuffix("_delta").upper()
    lines = [
        GENERATED_MARKER,
        f"# {document['module']} {artifact} 版本增量预览",
        "",
        f"- 版本：{document['version']}",
        "- 当前实现权威：代码、OpenAPI、DDL、Migration 或项目声明的等价来源",
        "- 本文件仅展示当前版本增量，不构成项目级全量契约。",
        "",
        "| Changeset | 操作 | 目标 | 原因 | 来源 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for change in document["changes"]:
        for operation in change["operations"]:
            lines.append(
                "| " + " | ".join(
                    md_cell(str(value))
                    for value in (
                        change["changeSetId"],
                        operation.get("operation", ""),
                        operation.get("targetId", ""),
                        operation.get("reason", ""),
                        change["sourceRef"],
                    )
                ) + " |"
            )
    return "\n".join(lines).rstrip() + "\n"


def command_init(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "merge_docs", "init")
    doc_root = Path(args.doc_root)
    created: list[Path] = []
    for artifact in PROJECT_FACT_ARTIFACTS:
        path = canonical_path(doc_root, args.module, artifact)
        if not path.exists():
            atomic_yaml(path, empty_document(args.module, artifact))
            created.append(path)
    changes_dir = changes_root(doc_root, args.version, args.module)
    changes_dir.mkdir(parents=True, exist_ok=True)
    template = changes_dir / "changeset.template.yaml"
    if not template.exists():
        atomic_yaml(template, empty_changeset_template(args.version, args.module))
        created.append(template)
    dependency_template = changes_dir / "dependency-impact.changeset.template.yaml"
    if not dependency_template.exists():
        atomic_yaml(
            dependency_template,
            {
                "schemaVersion": 1,
                "artifact": "dependency_impact_changeset",
                "changeSet": {
                    "id": f"DICHG-{args.version}-{args.module}-FEATURE",
                    "version": args.version,
                    "module": args.module,
                    "reason": "说明本版本为何新增、调整或废弃跨模块关系",
                    "baseRevision": "DI-R00@init",
                    "resultRevision": f"DI-R01@{args.version}",
                },
                "operations": [
                    {
                        "operation": "add",
                        "collection": "nodes",
                        "targetId": f"MODULE-{args.module}",
                        "reason": "登记参与本次跨模块实现的模块",
                        "after": {
                            "id": f"MODULE-{args.module}",
                            "type": "MODULE",
                            "name": args.module,
                            "module": args.module,
                            "refs": [f"docs/{args.module}/{args.module}_desc.md"],
                        },
                    }
                ],
            },
        )
        created.append(dependency_template)
    generated_root(doc_root, args.version, args.module).mkdir(parents=True, exist_ok=True)
    created.extend(document_facts.ensure_templates(doc_root, args.version, args.module))
    work_file = work_mode.work_path(doc_root, args.version)
    if not work_file.exists():
        work_mode.ensure_work_file(doc_root, args.version)
        created.append(work_file)
    work_record_file = doc_root / 'version' / args.version / 'work_record.md'
    if not work_record_file.exists():
        long_task.ensure_work_record(doc_root / 'version' / args.version, args.version)
        created.append(work_record_file)
    manifest = doc_root / "archive_manifest.yaml"
    if not manifest.exists():
        atomic_yaml(manifest, empty_manifest())
        created.append(manifest)
    created.extend(path for path in document_portal.write_portals(doc_root, versions=[args.version]) if path not in created)
    for path in created:
        print(f"CREATED {path}")
    if not created:
        print("No files created; structured document baseline already exists.")
    return 0


def command_validate(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "merge_docs", "validate")
    pending = gather_pending(args)
    print(
        f"Validated {len(pending.records)} changeset(s), {len(pending.artifact_merges)} deterministic artifact merge(s), "
        f"and {len(pending.flow_simulations)} business flow group(s)."
    )
    for summary in pending.summaries:
        print(summary)
    for summary in pending.skipped:
        print(summary)
    return 0


def command_preview(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "merge_docs", "preview")
    pending = gather_pending(args)
    if not pending.records and not pending.artifact_merges:
        print("增量已合并，无需重复执行")
        return 0
    write_preview(args, pending.state, pending.records, pending.summaries, pending.skipped, pending.artifact_merges, pending.flow_simulations)
    print("Preview written. Complete generated/merge_plan.yaml archiveGate before archive.")
    return 0


def persist_apply(
    args: argparse.Namespace,
    state: dict[str, dict[str, Any]],
    records: list[dict[str, Any]],
    summaries: list[str],
    artifact_merges: list[dict[str, Any]],
    flow_simulations: list[business_flow.GroupSimulation],
    archive_plan: dict[str, Any] | None = None,
) -> int:
    """Compatibility facade for the extracted merge persistence pipeline."""
    return _persist_apply_impl(
        args,
        state,
        records,
        summaries,
        artifact_merges,
        flow_simulations,
        archive_plan=archive_plan,
        renderers=RENDERERS,
        markdown_target=markdown_target,
        transactional_write=transactional_write,
        public_artifact_record=public_artifact_record,
    )


def command_apply(args: argparse.Namespace) -> int:
    pending = gather_pending(args)
    if not pending.records and not pending.artifact_merges:
        print("增量已合并，无需重复执行")
        return 0
    write_preview(args, pending.state, pending.records, pending.summaries, pending.skipped, pending.artifact_merges, pending.flow_simulations)
    return persist_apply(args, pending.state, pending.records, pending.summaries, pending.artifact_merges, pending.flow_simulations)


def command_apply_cli(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "merge_docs", "apply")
    if not getattr(args, "allow_low_level_apply", False):
        raise MergeError(
            "apply writes structured document changes directly, including project facts and delta archive records; use archive for wk -d gated version-delta archival "
            "or pass --allow-low-level-apply explicitly"
        )
    return command_apply(args)


def merge_plan_path(args: argparse.Namespace) -> Path:
    return generated_root(Path(args.doc_root), args.version, args.module) / "merge_plan.yaml"

















def validate_merge_plan(
    args: argparse.Namespace,
    state: dict[str, dict[str, Any]],
    records: list[dict[str, Any]],
    expected_artifact_merges: list[dict[str, Any]],
    expected_business_flow_merges: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compatibility wrapper around the dependency-injected archive validator."""
    return _validate_merge_plan_impl(
        args, state, records, expected_artifact_merges, expected_business_flow_merges,
        merge_plan_path=merge_plan_path,
        require_mapping=require_mapping, require_list=require_list, require_text=require_text,
        load_yaml=simple_yaml.load, validate_json_schema=validate_json_schema,
        current_work_mode_record=current_work_mode_record, build_merge_plan=build_merge_plan,
        plan_comparable=_plan_comparable, validate_artifact_merges=validate_artifact_merges,
        validate_gate_outcome=validate_gate_outcome,
        minimal_archive_gate_keys=work_mode.minimal_archive_gate_keys,
        lightweight_archive_gate_keys=work_mode.lightweight_archive_gate_keys,
        validate_archive_runtime_refs=_validate_archive_runtime_refs,
        runtime_contract=RUNTIME_CONTRACT, archive_gate_map=_ARCHIVE_GATE_MAP,
        long_task_runtime=long_task,
    )


def command_archive(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "merge_docs", "archive")
    pending = gather_pending(args)
    if not pending.records and not pending.artifact_merges:
        if getattr(args, "audit_bundle", False):
            from _merge_docs.archive_output import export_existing_audit
            payloads = export_existing_audit(Path(args.doc_root), args, load_manifest(Path(args.doc_root)))
            transactional_write(payloads)
        print("增量已合并，无需重复执行")
        return 0
    root = generated_root(Path(args.doc_root), args.version, args.module)
    expected_artifact_merges = expected_public_artifact_merges(root, pending.artifact_merges)
    expected_business_flow_merges = business_flow.simulation_plan_records(
        Path(args.doc_root), args.version, pending.flow_simulations
    )
    plan = validate_merge_plan(
        args, pending.state, pending.records, expected_artifact_merges, expected_business_flow_merges
    )
    for record in pending.records:
        record["mergeDigest"] = plan["resultDigest"]
        record["mergePlan"] = {
            "file": str(merge_plan_path(args)),
            "gates": plan["archiveGate"]["gates"],
            "authorizedBy": plan["archiveGate"]["authorizedBy"],
            "authorizedAt": plan["archiveGate"]["authorizedAt"],
            "decisionRef": plan["archiveGate"].get("decisionRef", ""),
            "userConfirmed": plan["archiveGate"].get("userConfirmed", False),
            "baseRevisions": plan["baseRevisions"],
            "resultRevisions": plan["resultRevisions"],
            "structuredArtifacts": plan["structuredArtifacts"],
            "artifactMerges": plan["artifactMerges"],
            "conflictDecisions": plan["conflictDecisions"],
            "affectedTraceIds": plan["affectedTraceIds"],
            "workMode": plan["workMode"],
        }
    return persist_apply(
        args, pending.state, pending.records, pending.summaries, pending.artifact_merges,
        pending.flow_simulations, archive_plan=plan
    )

def command_status(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "merge_docs", "status")
    manifest = load_manifest(Path(args.doc_root))
    records = [
        item
        for item in manifest.get("appliedChangeSets", [])
        if item.get("module") == args.module and (not args.version or item.get("version") == args.version)
    ]
    if not records:
        print("No applied changesets.")
        return 0
    for item in records:
        print(f"{item.get('id')} {item.get('version')} {item.get('module')} {item.get('appliedAt', '')}")
    return 0


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    help_text = {
        "init": "initialize project facts and a version changeset draft; API/DB stay version-local deltas",
        "validate": "validate baseline, changesets, revisions, stable IDs, and cross-document references",
        "preview": "simulate the increment and write the single merge plan plus preview documents",
        "apply": "low-level apply without archive gates; disabled unless an explicit override is supplied",
        "archive": "archive a READY increment; API/DB are referenced as version deltas only",
        "status": "show current revisions and already applied changesets",
    }
    for name in ("init", "validate", "preview", "apply", "archive", "status"):
        command = sub.add_parser(name, help=help_text[name], description=help_text[name])
        agent_context.add_agent_argument(command)
        command.add_argument("--doc-root", default="project_doc", help="root of project research/development facts")
        command.add_argument("--module", required=True, help="module code")
        command.add_argument("--version", required=name != "status", help="source version containing increments")
        if name in {"validate", "preview", "apply", "archive"}:
            command.add_argument("--changeset", action="append", help="explicit changeset path; repeatable")
        if name == "archive":
            command.add_argument(
                "--audit-bundle", action="store_true",
                help="also write a machine-readable audit bundle; knowledge archive output remains unchanged",
            )
            command.add_argument(
                "--require-user-confirmation",
                action="store_true",
                help="require merge_plan.archiveGate.userConfirmed=true before applying the increment",
            )
        if name == "apply":
            command.add_argument(
                "--allow-low-level-apply",
                action="store_true",
                help="explicitly run the low-level apply command for controlled diagnostics",
            )
    for command_parser in sub.choices.values():
        command_parser.add_argument("--json", action="store_true", help="emit machine-readable result or error")

    sub.choices["init"].set_defaults(handler=command_init)
    sub.choices["validate"].set_defaults(handler=command_validate)
    sub.choices["preview"].set_defaults(handler=command_preview)
    sub.choices["apply"].set_defaults(handler=command_apply_cli)
    sub.choices["archive"].set_defaults(handler=command_archive)
    sub.choices["status"].set_defaults(handler=command_status)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        doc_layout.validate_cli_namespace(args)
        return int(args.handler(args))
    except (agent_context.AgentAuthorizationError, MergeError, doc_layout.DocumentLayoutError, simple_yaml.YamlError, OSError, ValueError) as exc:
        emit_cli_error(exc, json_output=bool(getattr(args, "json", False)), stream=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
