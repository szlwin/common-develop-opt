"""Validate archive gates against long-task runtime evidence."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import work_mode
from _merge_docs.model import MergeError, require_list, require_mapping, require_text


def standard_skip_archive_reason(reasons: set[str]) -> str:
    """Render the deterministic archive reason for task-bound standard waivers."""
    return "标准模式用户已显式授权跳过：" + "；".join(sorted(reasons))


def build_archive_runtime_index(
    doc_root: Path,
    version: str,
    *,
    long_task_runtime: Any,
) -> dict[str, Any]:
    """Materialize the validated task/runtime evidence used by archive gates."""
    task_root = doc_root / "version" / version / "task"
    index: dict[str, Any] = {"outcomes": {}, "tasks": []}
    if not task_root.is_dir():
        return index
    for task_dir in sorted(path for path in task_root.iterdir() if path.is_dir()):
        required_runtime_files = {
            "task_events.jsonl", "traceability.json", "acceptance.json",
            "risk.json", "evidence/registry.jsonl",
        }
        missing_runtime_files = sorted(
            name for name in required_runtime_files if not (task_dir / name).is_file()
        )
        if missing_runtime_files:
            raise MergeError(
                "long task runtime is incomplete and cannot provide archive evidence: "
                f"{task_dir.name}: missing {missing_runtime_files}"
            )
        try:
            candidate_state = long_task_runtime.runtime_state(task_dir)
        except (OSError, ValueError) as exc:
            raise MergeError(
                "long task event reducer is unreadable and cannot provide archive evidence: "
                f"{task_dir.name}: {exc}"
            ) from exc
        if (
            not isinstance(candidate_state, dict)
            or candidate_state.get("schema_version") != long_task_runtime.LONG_TASK_SCHEMA_VERSION
        ):
            raise MergeError(
                "long task schema is missing or unsupported and cannot provide archive evidence: "
                f"{task_dir.name}"
            )
        validation_errors, _, _, _ = long_task_runtime.validate_task(task_dir)
        if validation_errors:
            raise MergeError(
                f"long task is invalid and cannot provide archive evidence: {task_dir.name}: "
                + "; ".join(validation_errors)
            )
        evidence_errors, evidence_entries = long_task_runtime.evidence.validate_registry(task_dir)
        if evidence_errors:
            raise MergeError(
                f"long task evidence registry is invalid: {task_dir.name}: "
                + "; ".join(evidence_errors)
            )
        acceptance_errors, acceptance_assertions = long_task_runtime.acceptance.validate_document(task_dir)
        if acceptance_errors:
            raise MergeError(
                f"long task acceptance assertions are invalid: {task_dir.name}: "
                + "; ".join(acceptance_errors)
            )
        risk_errors, risk_report = long_task_runtime.risk_detect.validate_report(task_dir)
        if risk_errors:
            raise MergeError(
                f"long task risk detection is invalid: {task_dir.name}: "
                + "; ".join(risk_errors)
            )
        task_record: dict[str, Any] = {
            "task_dir": task_dir,
            "state": candidate_state,
            "issues": [],
            "evidence": evidence_entries,
            "acceptance": acceptance_assertions,
            "risk": risk_report,
            "trace_ids": set(),
            "module_bindings": set(),
            "feature_ids": set(),
        }
        for plan_name in ("task_plan.json", "development_task_plan.json"):
            try:
                task_plan = json.loads((task_dir / plan_name).read_text(encoding="utf-8"))
                plan_items = task_plan.get("tasks", []) if isinstance(task_plan, dict) else []
                for item in plan_items:
                    if not isinstance(item, dict):
                        continue
                    implementation = item.get("implementation") if isinstance(item.get("implementation"), dict) else {}
                    owner_module = str(
                        implementation.get("owner_module")
                        or item.get("owner_module")
                        or item.get("ownerModule")
                        or ""
                    ).strip()
                    if owner_module:
                        task_record["module_bindings"].add(owner_module.upper())
                    feature_id = str(item.get("feature_id") or item.get("featureId") or "").strip()
                    if feature_id:
                        task_record["feature_ids"].add(feature_id.upper())
            except (OSError, ValueError, TypeError):
                pass
        try:
            trace_document = json.loads((task_dir / "traceability.json").read_text(encoding="utf-8"))
            trace_items = trace_document.get("items", []) if isinstance(trace_document, dict) else []
            task_record["trace_ids"] = {
                str(item.get("id")) for item in trace_items
                if isinstance(item, dict) and str(item.get("id") or "")
            }
        except (OSError, ValueError, TypeError):
            task_record["trace_ids"] = set()
        try:
            task_record["issues"] = long_task_runtime.runtime_issues(task_dir)
        except (OSError, ValueError):
            pass
        try:
            outcomes = long_task_runtime.load_task_history(task_dir).get("outcomes", [])
        except (OSError, ValueError):
            outcomes = []
        if isinstance(outcomes, list):
            for outcome in outcomes:
                if not isinstance(outcome, dict):
                    continue
                outcome_id = outcome.get("id")
                if not isinstance(outcome_id, str) or not outcome_id:
                    continue
                if outcome_id in index["outcomes"]:
                    raise MergeError(f"duplicate StageOutcome id across tasks: {outcome_id}")
                index["outcomes"][outcome_id] = {"task": task_record, "outcome": outcome}
        index["tasks"].append(task_record)
    return index


def standard_skip_archive_gates(
    doc_root: Path,
    version: str,
    *,
    archive_gate_map: dict[str, str],
    long_task_runtime: Any,
    module: str = "",
    affected_trace_ids: set[str] | None = None,
    feature_codes: set[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Project validated task-level standard waivers into archive gate templates."""
    runtime = build_archive_runtime_index(
        doc_root, version, long_task_runtime=long_task_runtime
    )
    projected: dict[str, dict[str, Any]] = {}
    scope_traces = {str(value) for value in (affected_trace_ids or set()) if str(value)}
    for phase in long_task_runtime.STANDARD_USER_SKIPPABLE_PHASES:
        gate_name = archive_gate_map.get(phase)
        if not gate_name:
            raise MergeError(f"standard skip phase has no archive gate mapping: {phase}")
        outcome_refs: list[str] = []
        reasons: set[str] = set()
        for record in runtime["tasks"]:
            if not _task_in_archive_scope(
                record, module=module, affected_trace_ids=scope_traces,
                feature_codes=feature_codes or set(),
            ):
                continue
            override = long_task_runtime.standard_skip_override(record.get("state", {}), phase)
            if not isinstance(override, dict):
                continue
            matches = [
                outcome_id
                for outcome_id, outcome_record in runtime["outcomes"].items()
                if outcome_record["task"] is record
                and outcome_record["outcome"].get("phase") == phase
                and outcome_record["outcome"].get("iteration_id") == override.get("iteration_id")
                and outcome_record["outcome"].get("output_revision") == override.get("revision")
                and outcome_record["outcome"].get("status") == "SKIPPED"
                and outcome_record["outcome"].get("is_current") is True
            ]
            if len(matches) != 1:
                raise MergeError(
                    f"standard waiver for {record['task_dir'].name}/{phase} must bind exactly one "
                    "current SKIPPED StageOutcome"
                )
            outcome_refs.extend(matches)
            reasons.add(str(override.get("reason") or ""))
        if outcome_refs:
            projected[gate_name] = {
                "status": "NOT_APPLICABLE",
                "revision": "",
                "stageOutcomeRefs": sorted(outcome_refs),
                "evidenceIds": [],
                "reviewerAgents": [],
                "reason": standard_skip_archive_reason(reasons),
            }
    return projected


def _task_in_archive_scope(
    record: dict[str, Any], *, module: str, affected_trace_ids: set[str],
    feature_codes: set[str] | None = None,
) -> bool:
    """Keep task-bound waivers local to the current changeset/module archive."""
    trace_ids = record.get("trace_ids") if isinstance(record.get("trace_ids"), set) else set()
    if affected_trace_ids:
        return bool(trace_ids & affected_trace_ids)
    module_token = str(module or "").strip().upper()
    if not module_token:
        return False
    module_bindings = {
        str(value).strip().upper()
        for value in (record.get("module_bindings") or set())
        if str(value).strip()
    }
    if module_bindings:
        return module_token in module_bindings
    normalized_features = {str(value).strip().upper() for value in (feature_codes or set()) if str(value).strip()}
    feature_ids = {
        str(value).strip().upper()
        for value in (record.get("feature_ids") or set())
        if str(value).strip()
    }
    if feature_ids:
        return any(
            feature_id == code or feature_id.startswith(f"{code}-F")
            for feature_id in feature_ids for code in normalized_features
        )
    target_id = str((record.get("state") or {}).get("target_id") or record.get("task_dir", "")).upper()
    if target_id in normalized_features:
        return True
    return re.match(rf"^{re.escape(module_token)}(?:$|[-_])", target_id) is not None


def reviewer_passed_for_gate(
    task_dir: Path,
    state: dict[str, Any],
    phase: str,
    revision: str,
    reviewer: str,
    *,
    review_store: Any,
) -> bool:
    """Return whether the reviewer has a materialized PASSED conclusion."""
    reviews = state.get("collaboration_reviews", {})
    review = reviews.get(phase, {}) if isinstance(reviews, dict) else {}
    if not isinstance(review, dict) or review.get("artifact_revision") != revision:
        return False
    conclusions = review.get("independent_conclusions", {})
    compact = conclusions.get(reviewer) if isinstance(conclusions, dict) else None
    try:
        conclusion = review_store.materialize_conclusion(task_dir, compact) if isinstance(compact, dict) else None
    except ValueError:
        return False
    return (
        isinstance(conclusion, dict)
        and conclusion.get("revision") == revision
        and conclusion.get("conclusion") == "PASSED"
    )


def _validate_gate_evidence(
    gate_name: str,
    gate: dict[str, Any],
    resolved: list[dict[str, Any]],
    revision: str,
) -> None:
    evidence_ids = require_list(
        gate.get("evidenceIds"),
        f"merge plan archiveGate.gates.{gate_name}.evidenceIds",
    )
    bound_evidence = {
        evidence_ref
        for record in resolved
        for evidence_ref in (record["outcome"].get("evidence_ids") or [])
        if isinstance(evidence_ref, str)
    }
    for evidence_ref in evidence_ids:
        matching_records = [
            record for record in resolved
            if evidence_ref in record["task"].get("evidence", {})
        ]
        if not matching_records:
            raise MergeError(f"archive gate {gate_name} evidence ID does not exist: {evidence_ref}")
        if evidence_ref not in bound_evidence:
            raise MergeError(
                f"archive gate {gate_name} evidence is not bound to the referenced StageOutcome: "
                f"{evidence_ref}"
            )
        for record in matching_records:
            entry = record["task"]["evidence"][evidence_ref]
            if entry.get("status") != "ACTIVE":
                raise MergeError(f"archive gate {gate_name} evidence is not ACTIVE: {evidence_ref}")
            if entry.get("revision") != revision:
                raise MergeError(f"archive gate {gate_name} evidence revision differs: {evidence_ref}")


def _validate_gate_reviewers(
    gate_name: str,
    gate: dict[str, Any],
    resolved: list[dict[str, Any]],
    phase: str,
    revision: str,
    *,
    runtime_contract: dict[str, Any],
    long_task_runtime: Any,
    minimal: bool,
    lightweight: bool,
) -> None:
    reviewers = require_list(
        gate.get("reviewerAgents"),
        f"merge plan archiveGate.gates.{gate_name}.reviewerAgents",
    )
    phase_definition = next(
        (item for item in runtime_contract.get("canonicalPhases", []) if item.get("id") == phase),
        {},
    )
    mandatory_reviewers = set(phase_definition.get("requiredReviewerAgents", []))
    if minimal:
        mandatory_reviewers -= set(work_mode.MINIMAL_SUPPRESSED_REVIEWERS.get(phase, []))
    if lightweight:
        mandatory_reviewers -= set(work_mode.LIGHTWEIGHT_SUPPRESSED_REVIEWERS.get(phase, []))
    missing_reviewers = mandatory_reviewers - set(reviewers)
    if missing_reviewers:
        raise MergeError(
            f"archive gate {gate_name} missing mandatory reviewers: {sorted(missing_reviewers)}"
        )
    if not reviewers and phase_definition.get("ownerAgent"):
        reviewers = [phase_definition["ownerAgent"]]
    known_agents = set(runtime_contract.get("agents", []))
    for reviewer in reviewers:
        if reviewer not in known_agents:
            raise MergeError(f"archive gate {gate_name} reviewer is not a declared Agent: {reviewer}")
        matched = any(
            reviewer_passed_for_gate(
                record["task"].get("task_dir"),
                record["task"].get("state", {}),
                phase,
                revision,
                reviewer,
                review_store=long_task_runtime.review_store,
            )
            or record["outcome"].get("produced_by_agent") == reviewer
            for record in resolved
        )
        if not matched:
            raise MergeError(
                f"archive gate {gate_name} reviewer has no PASSED conclusion for {revision}: {reviewer}"
            )


def validate_archive_runtime_refs(
    args: argparse.Namespace,
    archive_gate: dict[str, Any],
    gates: dict[str, Any],
    *,
    runtime_contract: dict[str, Any],
    archive_gate_map: dict[str, str],
    long_task_runtime: Any,
    minimal: bool = False,
    lightweight: bool = False,
    module: str = "",
    affected_trace_ids: set[str] | None = None,
    feature_codes: set[str] | None = None,
) -> set[str]:
    """Validate archive gate references against current task runtime facts."""
    runtime = build_archive_runtime_index(
        Path(args.doc_root), args.version, long_task_runtime=long_task_runtime
    )
    known_agents = set(runtime_contract.get("agents", []))
    authorized_by = require_text(
        archive_gate.get("authorizedBy"), "merge plan archiveGate.authorizedBy"
    )
    if authorized_by not in known_agents:
        raise MergeError(
            f"merge plan archiveGate.authorizedBy is not a declared Agent: {authorized_by}"
        )
    project_managers = set(
        (runtime_contract.get("projectManagement") or {}).get("allowedAgents", [])
    )
    if authorized_by not in project_managers:
        raise MergeError(
            "merge plan archiveGate.authorizedBy must be an authorized project manager: "
            f"{authorized_by}"
        )
    decision_ref = require_text(
        archive_gate.get("decisionRef"), "merge plan archiveGate.decisionRef"
    )
    if not re.fullmatch(r"(?:DEC|IMPL-DEC)-[A-Z0-9_-]+", decision_ref):
        raise MergeError(
            "merge plan archiveGate.decisionRef must reference a current DEC-* or "
            f"IMPL-DEC-* design decision: {decision_ref}"
        )

    gate_to_phase = {gate_name: phase for phase, gate_name in archive_gate_map.items()}
    scope_traces = {str(value) for value in (affected_trace_ids or set()) if str(value)}
    effective_standard_waivers: dict[str, tuple[list[str], str]] = {}
    for waiver_phase in long_task_runtime.STANDARD_USER_SKIPPABLE_PHASES:
        expected_refs: list[str] = []
        reasons: set[str] = set()
        for record in runtime["tasks"]:
            if not _task_in_archive_scope(
                record, module=module, affected_trace_ids=scope_traces,
                feature_codes=feature_codes or set(),
            ):
                continue
            override = long_task_runtime.standard_skip_override(record.get("state", {}), waiver_phase)
            if not isinstance(override, dict):
                continue
            matches = [
                outcome_id for outcome_id, outcome_record in runtime["outcomes"].items()
                if outcome_record["task"] is record
                and outcome_record["outcome"].get("phase") == waiver_phase
                and outcome_record["outcome"].get("iteration_id") == override.get("iteration_id")
                and outcome_record["outcome"].get("output_revision") == override.get("revision")
                and outcome_record["outcome"].get("status") == "SKIPPED"
                and outcome_record["outcome"].get("is_current") is True
            ]
            if len(matches) != 1:
                raise MergeError(
                    f"standard waiver for {record['task_dir'].name}/{waiver_phase} must bind exactly one current SKIPPED StageOutcome"
                )
            expected_refs.extend(matches)
            reasons.add(str(override.get("reason") or ""))
        if expected_refs:
            effective_standard_waivers[archive_gate_map[waiver_phase]] = (
                sorted(expected_refs), standard_skip_archive_reason(reasons)
            )
    standard_waiver_gates: set[str] = set()
    for gate_name, raw_gate in gates.items():
        gate = require_mapping(raw_gate, f"merge plan archiveGate.gates.{gate_name}")
        phase = gate_to_phase.get(gate_name)
        effective_waiver = effective_standard_waivers.get(gate_name)
        if effective_waiver and gate.get("status") != "NOT_APPLICABLE":
            raise MergeError(
                f"archive gate {gate_name} must remain NOT_APPLICABLE while a current standard user waiver is active"
            )
        if gate.get("status") == "NOT_APPLICABLE":
            mode_skip = bool(
                (minimal and phase in set(work_mode.MINIMAL_PHASES))
                or (lightweight and phase in set(work_mode.LIGHTWEIGHT_PHASES))
            )
            if mode_skip or phase not in set(long_task_runtime.STANDARD_USER_SKIPPABLE_PHASES):
                continue
            outcome_refs = require_list(
                gate.get("stageOutcomeRefs"),
                f"merge plan archiveGate.gates.{gate_name}.stageOutcomeRefs",
            )
            if not outcome_refs:
                raise MergeError(
                    f"archive gate {gate_name} standard waiver requires a current SKIPPED StageOutcome"
                )
            expected_refs: list[str] = []
            reasons: set[str] = set()
            for record in runtime["tasks"]:
                if not _task_in_archive_scope(
                    record, module=module, affected_trace_ids=scope_traces,
                    feature_codes=feature_codes or set(),
                ):
                    continue
                override = long_task_runtime.standard_skip_override(record.get("state", {}), phase)
                if not isinstance(override, dict):
                    continue
                matches = [
                    outcome_id
                    for outcome_id, outcome_record in runtime["outcomes"].items()
                    if outcome_record["task"] is record
                    and outcome_record["outcome"].get("phase") == phase
                    and outcome_record["outcome"].get("iteration_id") == override.get("iteration_id")
                    and outcome_record["outcome"].get("output_revision") == override.get("revision")
                    and outcome_record["outcome"].get("status") == "SKIPPED"
                    and outcome_record["outcome"].get("is_current") is True
                ]
                if len(matches) != 1:
                    raise MergeError(
                        f"standard waiver for {record['task_dir'].name}/{phase} must bind exactly one "
                        "current SKIPPED StageOutcome"
                    )
                expected_refs.extend(matches)
                reasons.add(str(override.get("reason") or ""))
            if sorted(outcome_refs) != sorted(expected_refs) or not expected_refs:
                raise MergeError(
                    f"archive gate {gate_name} StageOutcome refs do not match current standard waivers"
                )
            if gate.get("reason") != standard_skip_archive_reason(reasons):
                raise MergeError(
                    f"archive gate {gate_name} reason does not match current standard waivers"
                )
            if require_list(gate.get("evidenceIds"), f"merge plan archiveGate.gates.{gate_name}.evidenceIds"):
                raise MergeError(f"archive gate {gate_name} standard waiver must not claim pass evidence")
            if require_list(gate.get("reviewerAgents"), f"merge plan archiveGate.gates.{gate_name}.reviewerAgents"):
                raise MergeError(f"archive gate {gate_name} standard waiver must not claim passed reviewers")
            standard_waiver_gates.add(gate_name)
            continue
        phase = gate_to_phase.get(gate_name)
        if not phase:
            raise MergeError(f"merge plan archive gate has no canonical phase mapping: {gate_name}")
        revision = require_text(
            gate.get("revision"), f"merge plan archiveGate.gates.{gate_name}.revision"
        )
        outcome_refs = require_list(
            gate.get("stageOutcomeRefs"),
            f"merge plan archiveGate.gates.{gate_name}.stageOutcomeRefs",
        )
        resolved: list[dict[str, Any]] = []
        for outcome_ref in outcome_refs:
            record = runtime["outcomes"].get(outcome_ref)
            if record is None:
                raise MergeError(
                    f"archive gate {gate_name} StageOutcome does not exist: {outcome_ref}"
                )
            if not _task_in_archive_scope(
                record["task"], module=module, affected_trace_ids=scope_traces,
                feature_codes=feature_codes or set(),
            ):
                raise MergeError(
                    f"archive gate {gate_name} StageOutcome is outside the current archive scope: {outcome_ref}"
                )
            outcome = record["outcome"]
            if outcome.get("phase") != phase:
                raise MergeError(
                    f"archive gate {gate_name} StageOutcome phase mismatch: {outcome_ref}"
                )
            if outcome.get("status") != "PASSED" or outcome.get("is_current") is not True:
                raise MergeError(
                    f"archive gate {gate_name} StageOutcome must be current PASSED: {outcome_ref}"
                )
            if outcome.get("output_revision") != revision:
                raise MergeError(
                    f"archive gate {gate_name} revision differs from StageOutcome: {outcome_ref}"
                )
            task = record["task"]
            for issue in task.get("issues", []):
                if (
                    isinstance(issue, dict)
                    and issue.get("severity") in {"P0", "P1"}
                    and issue.get("status") in long_task_runtime.OPEN_ISSUE_STATUSES
                ):
                    raise MergeError(
                        f"archive gate {gate_name} task has open critical issue: {issue.get('id', '')}"
                    )
            resolved.append(record)

        _validate_gate_evidence(gate_name, gate, resolved, revision)
        _validate_gate_reviewers(
            gate_name,
            gate,
            resolved,
            phase,
            revision,
            runtime_contract=runtime_contract,
            long_task_runtime=long_task_runtime,
            minimal=minimal,
            lightweight=lightweight,
        )
    return standard_waiver_gates
