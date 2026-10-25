"""Archive merge-plan validation orchestration."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from document_core._merge_docs.model import MergeError


def validate_merge_plan(
    args: Any,
    state: dict[str, dict[str, Any]],
    records: list[dict[str, Any]],
    expected_artifact_merges: list[dict[str, Any]],
    expected_business_flow_merges: list[dict[str, Any]],
    *,
    merge_plan_path: Callable[[Any], Path],
    require_mapping: Callable[[Any, str], dict[str, Any]],
    require_list: Callable[[Any, str], list[Any]],
    require_text: Callable[[Any, str], str],
    load_yaml: Callable[[Path], Any],
    validate_json_schema: Callable[[dict[str, Any], str, str], None],
    current_work_mode_record: Callable[..., dict[str, Any]],
    build_merge_plan: Callable[..., dict[str, Any]],
    plan_comparable: Callable[[dict[str, Any]], dict[str, Any]],
    validate_artifact_merges: Callable[[Any, Path], list[dict[str, Any]]],
    validate_gate_outcome: Callable[..., dict[str, Any]],
    minimal_archive_gate_keys: Callable[[], list[str]],
    lightweight_archive_gate_keys: Callable[[], list[str]],
    validate_archive_runtime_refs: Callable[..., None],
    runtime_contract: dict[str, Any],
    archive_gate_map: dict[str, str],
    long_task_runtime: Any,
) -> dict[str, Any]:
    """Validate a reviewed plan against current facts without importing the CLI facade."""
    path = merge_plan_path(args)
    if not path.is_file():
        raise MergeError(f"missing merge plan: {path}; run preview, complete archiveGate, then retry")
    plan = require_mapping(load_yaml(path), str(path))
    validate_json_schema(plan, "merge-plan.schema.json", "merge plan")
    if plan.get("version") != args.version or plan.get("module") != args.module:
        raise MergeError("merge plan version/module does not match current scope")
    current_mode = current_work_mode_record(Path(args.doc_root), args.version)
    if current_mode != plan.get("workMode"):
        raise MergeError("work.md mode or content changed after preview; regenerate preview")
    expected = build_merge_plan(
        args, state, records, expected_artifact_merges, expected_business_flow_merges,
        generated_at=str(plan.get("generatedAt", "")), archive_gate=plan.get("archiveGate"),
    )
    if plan.get("resultDigest") != expected.get("resultDigest"):
        raise MergeError("merge plan digest is stale; regenerate preview and re-review")
    if plan_comparable(plan) != plan_comparable(expected):
        raise MergeError(
            "merge plan content is stale or was modified outside archiveGate/conflictDecisions; "
            "regenerate preview and re-review"
        )
    if plan.get("businessFlowMerges") != expected_business_flow_merges:
        raise MergeError("merge plan businessFlowMerges differ from current flow facts; regenerate preview")
    artifact_merges = validate_artifact_merges(plan.get("artifactMerges"), Path(args.doc_root))
    if artifact_merges != expected_artifact_merges:
        raise MergeError("merge plan artifactMerges differ from current deterministic artifact facts")
    conflict_decisions = require_list(plan.get("conflictDecisions"), "merge plan conflictDecisions")
    affected_stable_ids = {
        str(stable_id)
        for item in expected_artifact_merges
        for stable_id in item.get("affectedStableIds", [])
    }
    for index, decision in enumerate(conflict_decisions):
        resolution = str(decision.get("decision") or "")
        stable_id = str(decision.get("stableId") or "")
        if resolution in {"REBASE_REQUIRED", "REJECT_ARCHIVE"}:
            raise MergeError(f"conflictDecisions[{index}] remains blocking: {stable_id} {resolution}")
        if resolution == "SUPERSEDE" and stable_id not in affected_stable_ids:
            raise MergeError(
                f"conflictDecisions[{index}] SUPERSEDE is not represented by current artifactMerges: {stable_id}"
            )
    archive_gate = require_mapping(plan.get("archiveGate"), "merge plan archiveGate")
    if archive_gate.get("status") != "READY":
        raise MergeError("merge plan archiveGate.status must be READY")
    open_issue_ids = require_list(archive_gate.get("openIssueIds", []), "merge plan archiveGate.openIssueIds")
    if open_issue_ids:
        raise MergeError("merge plan archiveGate has open issues; close them before wk -d")
    gates = require_mapping(archive_gate.get("gates"), "merge plan archiveGate.gates")
    minimal_gates = set(minimal_archive_gate_keys()) if plan["workMode"].get("minimal") is True else set()
    lightweight_history = bool(plan["workMode"].get("lightweightWaived"))
    lightweight_gates = (
        set(lightweight_archive_gate_keys())
        if plan["workMode"].get("lightweight") is True or lightweight_history else set()
    )
    require_text(archive_gate.get("authorizedBy"), "merge plan archiveGate.authorizedBy")
    authorized_at = require_text(archive_gate.get("authorizedAt"), "merge plan archiveGate.authorizedAt")
    try:
        authorized_time = datetime.fromisoformat(authorized_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MergeError("merge plan archiveGate.authorizedAt must be ISO-8601") from exc
    if authorized_time.tzinfo is None:
        raise MergeError("merge plan archiveGate.authorizedAt must include a timezone")
    require_text(archive_gate.get("decisionRef"), "merge plan archiveGate.decisionRef")
    standard_waiver_gates = validate_archive_runtime_refs(
        args, archive_gate, gates,
        runtime_contract=runtime_contract,
        archive_gate_map=archive_gate_map,
        long_task_runtime=long_task_runtime,
        minimal=bool(plan.get("workMode", {}).get("minimal")),
        lightweight=bool(plan.get("workMode", {}).get("lightweight")) or lightweight_history,
        module=str(args.module or ""),
        affected_trace_ids={
            str(value) for value in (plan.get("affectedTraceIds") or []) if str(value)
        },
        feature_codes={
            str(record.get("featureCode")) for record in records
            if str(record.get("featureCode") or "")
        },
    )
    archive_core_gates = tuple(gate for gate in archive_gate_map.values() if gate != "businessModel")
    for gate in archive_core_gates:
        validate_gate_outcome(
            gate, gates.get(gate),
            allow_not_applicable=(
                gate in minimal_gates
                or gate in lightweight_gates
                or gate in standard_waiver_gates
            ),
        )
    validate_gate_outcome(
        "businessModel", gates.get("businessModel"),
        allow_not_applicable="business_model" not in plan["structuredArtifacts"],
    )
    if plan["workMode"].get("auto") is True and getattr(args, "require_user_confirmation", False):
        raise MergeError("wk -auto does not allow --require-user-confirmation")
    if (
        plan["workMode"].get("auto") is not True
        and getattr(args, "require_user_confirmation", False)
        and archive_gate.get("userConfirmed") is not True
    ):
        raise MergeError("merge plan archiveGate requires userConfirmed=true for --require-user-confirmation")
    return plan
