"""Task validation helpers for traceability, task-plan, attempt, and issue domains."""
from __future__ import annotations

from _long_task.validation.task_support import *
from common_utils import sha256_file
from datetime import datetime


def revalidation_superseded_by_reopen(record: dict, history: dict) -> bool:
    """Historical proofs keep their bindings, not ownership of a newer file."""
    try:
        confirmed = datetime.fromisoformat(str(record.get("created_at", "")))
        for event in history.get("stale_events", []):
            if event.get("executed_by_agent") != PROJECT_MANAGER_AGENT:
                continue
            if event.get("from_phase") not in {"requirement_confirmation", "requirement_analysis"}:
                continue
            if datetime.fromisoformat(str(event.get("created_at", ""))) > confirmed:
                return True
    except (ValueError, TypeError):
        return False
    return False


_TRACE_STRING_LIST_FIELDS = (
    "requirement_refs",
    "design_refs",
    "test_case_ids",
    "contract_refs",
    "implementation_refs",
    "verified_by_agents",
    "acceptance_criteria",
    "business_flow_refs",
    "business_model_refs",
    "plan_task_ids",
    "verification_evidence_ids",
    "dependency_impact_refs",
    "cross_module_implementation_refs",
)
_TRACE_BOOLEAN_FIELDS = (
    "impact_required",
    "business_flow_required",
    "cross_module_implementation_required",
)


def _preserved_by_amendment_chain(
    task: dict[str, object],
    phase: str,
    artifact: dict[str, object],
    history: dict[str, object],
) -> bool:
    """Accept a passed task carried through every amendment since its revision."""
    if task.get("status") not in {"PASSED", "NOT_APPLICABLE"}:
        return False
    task_id = str(task.get("id") or "")
    revision = str(task.get("output_revision") or "")
    target_revision = str(artifact.get("revision") or "")
    if not task_id or not revision or not target_revision:
        return False
    advanced = False
    for amendment in history.get("amendment_events", []):
        if not isinstance(amendment, dict) or amendment.get("phase") != phase:
            continue
        if str(amendment.get("base_revision") or "") != revision:
            continue
        if task_id in set(amendment.get("affected_task_ids") or []):
            return False
        revision = str(amendment.get("revision") or "")
        advanced = True
        if revision == target_revision:
            return True
    return advanced and revision == target_revision


def _validate_traceability_entries(ctx, legacy: dict[str, object]) -> None:
    artifacts = ctx.value("artifacts")
    current_artifact_revisions = ctx.value("current_artifact_revisions")
    errors = ctx.value("errors")
    evidence_entries = ctx.value("evidence_entries")
    task_dir = ctx.value("task_dir")
    trace_ids = ctx.value("trace_ids")
    traceability = ctx.value("traceability")
    testing_artifact = artifacts.get("testing") if isinstance(artifacts, dict) else None
    require_current_evidence = (
        isinstance(testing_artifact, dict) and testing_artifact.get("status") == "PASSED"
    )

    for index, trace in enumerate(traceability):
        prefix = f"traceability[{index}]"
        legacy.update(index=index, prefix=prefix, trace=trace)
        if not isinstance(trace, dict):
            errors.append(f"{prefix} must be an object")
            continue
        validate_allowed_fields(trace, TRACEABILITY_FIELDS, prefix, errors)
        trace_id = str(trace.get("id") or "").strip()
        if not trace_id:
            errors.append(f"{prefix} missing id")
        elif trace_id in trace_ids:
            errors.append(f"duplicate traceability id: {trace_id}")
        trace_ids.add(trace_id)
        if not is_nonempty_string(trace.get("description")):
            errors.append(f"{prefix} missing description")
        if trace.get("status") not in TRACE_STATUSES:
            errors.append(f"{prefix} invalid status: {trace.get('status')}")
        for key in _TRACE_STRING_LIST_FIELDS:
            legacy["key"] = key
            if not validate_string_list(trace.get(key, [])):
                errors.append(f"{prefix} {key} must be a string array")
        for key in _TRACE_BOOLEAN_FIELDS:
            legacy["key"] = key
            if not isinstance(trace.get(key, False), bool):
                errors.append(f"{prefix} {key} must be a boolean")
        source_digest = trace.get("requirement_source_digest")
        if source_digest is not None and not re.fullmatch(r"[a-f0-9]{64}", str(source_digest)):
            errors.append(f"{prefix} requirement_source_digest must be a SHA-256 digest")
        invalid_flow_refs = [
            item
            for item in trace.get("business_flow_refs") or []
            if not re.fullmatch("FLOW-[A-Z0-9_-]+", str(item))
        ]
        if invalid_flow_refs:
            errors.append(f"{prefix} invalid business_flow_refs: {invalid_flow_refs}")
        for evidence_id in trace.get("verification_evidence_ids") or []:
            legacy["evidence_id"] = evidence_id
            try:
                entry = evidence.require_evidence(
                    task_dir, evidence_id, validated_entries=evidence_entries
                )
                legacy["entry"] = entry
                if require_current_evidence and entry.get("revision") not in current_artifact_revisions:
                    errors.append(
                        f"{prefix} verification evidence revision is stale: "
                        f"{evidence_id}@{entry.get('revision')}"
                    )
                if trace_id and trace_id not in set(entry.get("scope_refs") or []):
                    scope_tokens = {
                        token.strip()
                        for raw_scope in (entry.get("scope_refs") or [])
                        if isinstance(raw_scope, str)
                        for token in re.split(r"[,;]", raw_scope)
                        if token.strip()
                    }
                    if trace_id in scope_tokens:
                        continue
                    errors.append(
                        f"{prefix} verification evidence {evidence_id} does not cover trace {trace_id}"
                    )
            except ValueError as exc:
                errors.append(f"{prefix} {exc}")

    _validate_traceability_revalidations(ctx, traceability, trace_ids)


def _validate_traceability_revalidations(ctx, traceability, trace_ids: set[str]) -> None:
    """Verify every lifecycle revalidation against the canonical trace file."""
    errors = ctx.value("errors")
    history = ctx.value("history")
    task_dir = ctx.value("task_dir")
    trace_path = ctx.value("trace_path")
    current = {
        str(item.get("id")): item for item in traceability
        if isinstance(item, dict) and item.get("id")
    }
    try:
        trace_digest = sha256_file(trace_path)
    except OSError as exc:
        errors.append(f"traceability revalidation cannot read canonical file: {exc}")
        return
    records = history.get("traceability_revalidations", [])
    if not isinstance(records, list):
        errors.append("traceability_revalidations must be an array")
        return
    last_record_index = len(records) - 1
    for index, record in enumerate(records):
        prefix = f"traceability_revalidations[{index}]"
        if not isinstance(record, dict):
            errors.append(f"{prefix} must be an object")
            continue
        required = TRACEABILITY_REVALIDATION_FIELDS
        unknown = sorted(set(record) - required)
        if unknown:
            errors.append(f"{prefix} contains unknown fields: {unknown}")
        missing = sorted(key for key in required if key not in record)
        if missing:
            errors.append(f"{prefix} missing fields: {missing}")
            continue
        trace_id = str(record.get("trace_id") or "")
        item = current.get(trace_id)
        if trace_id not in trace_ids or not isinstance(item, dict):
            errors.append(f"{prefix} references unknown traceability id: {trace_id}")
            continue
        if record.get("from_status") not in {"STALE", "GAP"}:
            errors.append(f"{prefix}.from_status must be STALE or GAP")
        superseded = revalidation_superseded_by_reopen(record, history)
        if record.get("to_status") != "COVERED" or (not superseded and item.get("status") != "COVERED"):
            errors.append(f"{prefix} does not match a COVERED canonical trace")
        phase = str(record.get("phase") or "")
        revision = str(record.get("revision") or "")
        if phase not in PHASES:
            errors.append(f"{prefix}.phase is invalid: {phase}")
        if not is_nonempty_string(revision):
            errors.append(f"{prefix}.revision is required")
        evidence_ids = record.get("evidence_ids")
        acceptance_ids = record.get("acceptance_ids")
        if not validate_nonempty_string_list(evidence_ids) or len(set(evidence_ids)) != len(evidence_ids):
            errors.append(f"{prefix}.evidence_ids must be a unique non-empty string array")
            evidence_ids = []
        if not validate_nonempty_string_list(acceptance_ids) or len(set(acceptance_ids)) != len(acceptance_ids):
            errors.append(f"{prefix}.acceptance_ids must be a unique non-empty string array")
            acceptance_ids = []
        if not superseded and set(acceptance_ids) != set(item.get("acceptance_criteria") or []):
            errors.append(f"{prefix}.acceptance_ids must equal the trace acceptance criteria")
        if not superseded and set(evidence_ids) - set(item.get("verification_evidence_ids") or []):
            errors.append(f"{prefix}.evidence_ids are not recorded on the canonical trace")
        # A revalidation digest describes the canonical file written by that
        # event. Later revalidations of other traces legitimately change the
        # file again, so only the last event can be compared with the current
        # file. Every historical record is still checked for its bindings
        # below; without a full snapshot there is no prior file to replay.
        if not superseded and index == last_record_index and record.get("traceability_digest") != trace_digest:
            errors.append(f"{prefix}.traceability_digest does not match canonical traceability.json")
        if not is_nonempty_string(record.get("review_result_ref")):
            errors.append(f"{prefix}.review_result_ref is required")
        if not is_nonempty_string(record.get("reason")):
            errors.append(f"{prefix}.reason is required")
        if record.get("agent") != PROJECT_MANAGER_AGENT:
            errors.append(f"{prefix}.agent must be {PROJECT_MANAGER_AGENT}")
        try:
            review = review_store.require_record(task_dir, str(record.get("review_result_ref") or ""))
            if review.get("result") != "PASSED" or review.get("review_phase") != phase or review.get("review_revision") != revision:
                errors.append(f"{prefix}.review_result_ref is not a PASSED review for {phase}@{revision}")
            if not set(evidence_ids).issubset(set(review.get("selected_evidence_ids") or review.get("evidence_ids") or [])):
                errors.append(f"{prefix}.review_result_ref does not cite all Evidence IDs")
        except ValueError as exc:
            errors.append(f"{prefix} {exc}")
        for evidence_id in evidence_ids:
            try:
                entry = evidence.require_evidence(
                    task_dir, evidence_id, expected_revision=revision, expected_phase=phase,
                    validated_entries=ctx.value("evidence_entries"),
                )
                scopes = entry.get("scope_refs") or []
                tokens = {token.strip() for raw in scopes if isinstance(raw, str) for token in re.split(r"[,;]", raw) if token.strip()}
                if trace_id not in tokens and trace_id not in scopes:
                    errors.append(f"{prefix} Evidence {evidence_id} does not cover {trace_id}")
            except ValueError as exc:
                errors.append(f"{prefix} {exc}")


def _validate_task_projection(ctx, legacy: dict[str, object]) -> None:
    artifacts = ctx.value("artifacts")
    errors = ctx.value("errors")
    history = ctx.value("history")
    minimal_mode = ctx.value("minimal_mode")
    phases = ctx.value("phases")
    state = ctx.value("state")
    traceability = ctx.value("traceability")
    warnings = ctx.value("warnings")

    trace_map = {
        str(item.get("id")): item
        for item in traceability
        if isinstance(item, dict) and item.get("id")
    }
    tasks = task_events.materialize_tasks(ctx.value("tasks"), history)
    legacy["tasks"] = tasks
    task_ids = validate_task_plan(tasks, phases, trace_map, errors, minimal=minimal_mode)

    current_phase = state.get("current_phase")
    if current_phase in phases:
        for predecessor in phase_predecessors(str(current_phase), minimal_mode):
            predecessor_artifact = artifacts.get(predecessor)
            legacy.update(
                predecessor=predecessor,
                predecessor_artifact=predecessor_artifact,
            )
            predecessor_status = (
                predecessor_artifact.get("status")
                if isinstance(predecessor_artifact, dict)
                else None
            )
            skipped = phase_skip_allowed(predecessor, minimal_mode, state)
            if predecessor_status != "PASSED" and not (
                skipped and predecessor_status == "NOT_APPLICABLE"
            ):
                errors.append(
                    f"current_phase {current_phase} cannot be active because predecessor "
                    f"{predecessor} is {predecessor_status}"
                )

    for index, task in enumerate(tasks):
        legacy.update(index=index, task=task)
        if not isinstance(task, dict):
            continue
        phase = task.get("phase")
        artifact = artifacts.get(phase) if isinstance(phase, str) else None
        legacy.update(artifact=artifact, phase=phase)
        if isinstance(artifact, dict) and (
            task.get("iteration_id") != artifact.get("iteration_id")
            or task.get("iteration_no") != artifact.get("iteration_no")
        ):
            preserved_by_amendment = _preserved_by_amendment_chain(
                task, str(phase), artifact, history
            )
            if not preserved_by_amendment:
                errors.append(
                    f"task-plan[{index}] iteration does not match current phase artifact iteration"
                )
        if task.get("status") == "NOT_APPLICABLE" and not phase_skip_allowed(
            str(phase), minimal_mode, state
        ):
            errors.append(
                f"task-plan[{index}] cannot be NOT_APPLICABLE outside an active mode skip or standard user waiver"
            )

    active_task_ids = state.get("active_task_ids")
    if not validate_string_list(active_task_ids):
        errors.append("active_task_ids must be a string array")
    else:
        for task_id in active_task_ids:
            if task_id not in task_ids:
                errors.append(f"active_task_ids references unknown task: {task_id}")

    failed_attempts = state.get("failed_attempts", {})
    if not isinstance(failed_attempts, dict):
        errors.append("failed_attempts must be an object when present")
    else:
        for task_id, count in failed_attempts.items():
            legacy["count"] = count
            if task_id not in task_ids:
                warnings.append(f"failed_attempts references unknown task: {task_id}")
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                errors.append(f"failed_attempts.{task_id} must be a non-negative integer")


def _validate_attempt_projection(ctx, legacy: dict[str, object]) -> None:
    validate_attempt_history(
        ctx.value("task_dir"),
        ctx.value("attempts"),
        ctx.value("work_events"),
        list(legacy["tasks"]),
        ctx.value("state"),
        ctx.value("phases"),
        ctx.value("errors"),
        ctx.value("warnings"),
    )


def _validate_issue_entry(
    issue: dict[str, object],
    *,
    prefix: str,
    phases: list[str],
    trace_ids: set[str],
    seen_issue_ids: set[str],
    derived_open: set[str],
    errors: list[str],
    legacy: dict[str, object],
) -> None:
    validate_allowed_fields(issue, REVIEW_ISSUE_FIELDS, prefix, errors)
    issue_id = str(issue.get("id") or "").strip()
    if not issue_id:
        errors.append(f"{prefix} missing id")
    elif issue_id in seen_issue_ids:
        errors.append(f"duplicate issue id: {issue_id}")
    seen_issue_ids.add(issue_id)
    if issue.get("status") not in ISSUE_STATUSES:
        errors.append(f"{prefix} invalid status: {issue.get('status')}")
    if issue.get("status") in OPEN_ISSUE_STATUSES and issue_id:
        derived_open.add(issue_id)
    if issue.get("severity") not in {"P0", "P1", "P2"}:
        errors.append(f"{prefix} invalid severity: {issue.get('severity')}")
    if issue.get("phase") not in phases:
        errors.append(f"{prefix} invalid phase: {issue.get('phase')}")
    for key in ("raised_by_agent", "owner_agent", "description"):
        legacy["key"] = key
        if not is_nonempty_string(issue.get(key)):
            errors.append(f"{prefix} missing {key}")
    if KNOWN_AGENTS:
        for key in ("raised_by_agent", "owner_agent"):
            legacy["key"] = key
            value = issue.get(key)
            legacy["value"] = value
            if is_nonempty_string(value) and value not in KNOWN_AGENTS:
                errors.append(f"{prefix} unknown {key}: {value}")
    if issue.get("issue_type") not in ISSUE_TYPES:
        errors.append(f"{prefix} invalid issue_type: {issue.get('issue_type')}")
    for key in ("artifact_revision", "question"):
        legacy["key"] = key
        if not is_nonempty_string(issue.get(key)):
            errors.append(f"{prefix} missing {key}")
    question_to = issue.get("question_to", [])
    if not validate_nonempty_string_list(question_to):
        errors.append(f"{prefix} question_to must be a non-empty string array")
    elif KNOWN_AGENTS:
        for agent in question_to:
            if agent not in KNOWN_AGENTS:
                errors.append(f"{prefix} question_to references unknown Agent: {agent}")
    if not validate_string_list(issue.get("affected_trace_ids", [])):
        errors.append(f"{prefix} affected_trace_ids must be a string array")
    if not isinstance(issue.get("responses", []), list):
        errors.append(f"{prefix} responses must be an array")
    for key in ("axis", "title", "impact", "recommendation"):
        legacy["key"] = key
        if not is_nonempty_string(issue.get(key)):
            errors.append(f"{prefix} missing {key}")
    confidence = issue.get("confidence")
    if not isinstance(confidence, int) or isinstance(confidence, bool) or not 1 <= confidence <= 10:
        errors.append(f"{prefix} confidence must be an integer from 1 to 10")
    motivating_evidence = issue.get("motivating_evidence")
    if not validate_nonempty_string_list(motivating_evidence):
        errors.append(f"{prefix} motivating_evidence must be a non-empty string array")
    if (
        issue.get("severity") in {"P0", "P1"}
        and isinstance(confidence, int)
        and confidence >= 7
        and not motivating_evidence
    ):
        errors.append(f"{prefix} blocking high-confidence issue lacks evidence")
    if issue.get("status") == "CLOSED":
        for key in ("resolution_revision", "verified_by_agent", "verified_at"):
            legacy["key"] = key
            if not is_nonempty_string(issue.get(key)):
                errors.append(f"{prefix} CLOSED issue missing {key}")
        if issue.get("verified_by_agent") == issue.get("owner_agent"):
            errors.append(f"{prefix} owner cannot self-close the issue")
    if issue.get("status") == "DEFERRED" and not is_nonempty_string(issue.get("defer_reason")):
        errors.append(f"{prefix} DEFERRED issue missing defer_reason")
    for trace_id in issue.get("affected_trace_ids") or []:
        if trace_id not in trace_ids:
            errors.append(f"{prefix} references unknown traceability id: {trace_id}")


def _validate_issue_projection(ctx, legacy: dict[str, object]) -> None:
    errors = ctx.value("errors")
    phases = ctx.value("phases")
    state = ctx.value("state")
    trace_ids = ctx.value("trace_ids")
    seen_issue_ids: set[str] = set()
    derived_open: set[str] = set()
    for index, issue in enumerate(ctx.value("issues")):
        prefix = f"review-issues[{index}]"
        legacy.update(index=index, issue=issue, prefix=prefix)
        if not isinstance(issue, dict):
            errors.append(f"{prefix} must be an object")
            continue
        _validate_issue_entry(
            issue,
            prefix=prefix,
            phases=phases,
            trace_ids=trace_ids,
            seen_issue_ids=seen_issue_ids,
            derived_open=derived_open,
            errors=errors,
            legacy=legacy,
        )
    legacy["derived_open"] = derived_open

    recorded_open_raw = state.get("open_issue_ids")
    if not validate_string_list(recorded_open_raw):
        errors.append("open_issue_ids must be an array of non-empty strings")
        recorded_open: set[str] = set()
    else:
        recorded_open = set(recorded_open_raw)
        if len(recorded_open) != len(recorded_open_raw):
            errors.append("open_issue_ids must not contain duplicates")
    if recorded_open != derived_open:
        errors.append(
            f"open_issue_ids mismatch: state={sorted(recorded_open)}, ledger={sorted(derived_open)}"
        )


def _validate_collaboration_review_projection(ctx, legacy: dict[str, object]) -> None:
    errors = ctx.value("errors")
    collaboration_reviews = ctx.value("state").get("collaboration_reviews")
    if not isinstance(collaboration_reviews, dict):
        errors.append("collaboration_reviews must be an object")
        collaboration_reviews = {}
    extra_reviews = sorted(
        set(collaboration_reviews) - set(ctx.value("collaboration_reviewers"))
    )
    if extra_reviews:
        errors.append(f"collaboration_reviews contains unknown phases: {extra_reviews}")
    legacy["collaboration_reviews"] = collaboration_reviews


def _validate_task_section_2(ctx) -> None:
    """Validate traceability/task/issue projections and bind named legacy outputs."""
    legacy: dict[str, object] = {
        "artifact": None,
        "collaboration_reviews": None,
        "count": None,
        "derived_open": None,
        "entry": None,
        "evidence_id": None,
        "index": None,
        "issue": None,
        "key": ctx.value("key"),
        "phase": ctx.value("phase"),
        "predecessor": None,
        "predecessor_artifact": None,
        "prefix": None,
        "task": None,
        "tasks": ctx.value("tasks"),
        "trace": None,
        "value": ctx.value("value"),
    }
    _validate_traceability_entries(ctx, legacy)
    _validate_task_projection(ctx, legacy)
    _validate_attempt_projection(ctx, legacy)
    _validate_issue_projection(ctx, legacy)
    _validate_collaboration_review_projection(ctx, legacy)
    ctx.bind(**legacy)
