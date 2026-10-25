"""Validation helpers for merge plans and deterministic artifact merges."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from document_core import document_facts
from document_core._merge_docs.model import MergeError, require_list, require_mapping, require_text


def validate_gate_outcome(name: str, value: Any, allow_not_applicable: bool = False) -> dict[str, Any]:
    gate = require_mapping(value, f"merge plan archiveGate.gates.{name}")
    status = gate.get("status")
    if status == "NOT_APPLICABLE":
        if not allow_not_applicable:
            raise MergeError(f"merge plan archive gate cannot be NOT_APPLICABLE: {name}")
        require_text(gate.get("reason"), f"merge plan archiveGate.gates.{name}.reason")
        return gate
    if status != "PASSED":
        raise MergeError(f"merge plan archive gate is not PASSED: {name}={status}")
    require_text(gate.get("revision"), f"merge plan archiveGate.gates.{name}.revision")
    for key in ("stageOutcomeRefs", "evidenceIds", "reviewerAgents"):
        values = require_list(gate.get(key), f"merge plan archiveGate.gates.{name}.{key}")
        if not values or any(not isinstance(item, str) or not item.strip() for item in values):
            raise MergeError(f"merge plan archiveGate.gates.{name}.{key} must contain evidence")
    return gate


def validate_artifact_merges(value: Any, doc_root: Path) -> list[dict[str, Any]]:
    merges = require_list(value, "merge plan artifactMerges")
    seen: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(merges):
        item = require_mapping(raw, f"merge plan artifactMerges[{index}]")
        artifact_type = require_text(item.get("artifactType"), f"artifactMerges[{index}].artifactType")
        source_ref = require_text(item.get("sourceRef"), f"artifactMerges[{index}].sourceRef")
        target_ref = require_text(item.get("targetRef"), f"artifactMerges[{index}].targetRef")
        marker = (artifact_type, source_ref, target_ref)
        if marker in seen:
            raise MergeError(f"duplicate artifact merge: {artifact_type} {source_ref} -> {target_ref}")
        seen.add(marker)
        require_text(item.get("sourceRevision"), f"artifactMerges[{index}].sourceRevision")
        require_text(item.get("resultRevision"), f"artifactMerges[{index}].resultRevision")
        if item.get("status") != "READY":
            raise MergeError(f"artifactMerges[{index}] must be deterministic READY")
        for key in ("sourceDigest", "targetDigest", "resultDigest"):
            digest = require_text(item.get(key), f"artifactMerges[{index}].{key}")
            if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise MergeError(f"artifactMerges[{index}].{key} must be a lowercase sha256")
        evidence = require_list(item.get("evidenceRefs"), f"artifactMerges[{index}].evidenceRefs")
        if not evidence or any(not isinstance(ref, str) or not ref.strip() for ref in evidence):
            raise MergeError(f"artifactMerges[{index}] READY without generated Evidence reference")
        if artifact_type in {"requirement_list", "module_desc", "union_language"}:
            try:
                document_facts.verify_ready_record(item, doc_root)
            except document_facts.DocumentFactError as exc:
                raise MergeError(str(exc)) from exc
        else:
            source_path = doc_root / source_ref
            target_path = doc_root / target_ref
            if not source_path.is_file() or hashlib.sha256(source_path.read_bytes()).hexdigest() != item["sourceDigest"]:
                raise MergeError(f"artifactMerges[{index}].sourceDigest does not match {source_path}")
            target_digest = hashlib.sha256(target_path.read_bytes() if target_path.is_file() else b"").hexdigest()
            if target_digest != item["targetDigest"]:
                raise MergeError(f"artifactMerges[{index}].targetDigest does not match {target_path}")
    return merges


def plan_comparable(plan: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in plan.items() if key not in {"generatedAt", "archiveGate", "conflictDecisions", "resultDigest"}}
