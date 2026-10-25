"""Compile structured acceptance assertions and verify deterministic business outcomes."""
from __future__ import annotations
from common_utils import task_write_locked

import argparse
import hashlib
import json
import operator
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

import evidence
import review_store
import agent_context
import storage
from common_utils import atomic_write_text, now

SCHEMA_VERSION = 1
ASSERTION_TYPES = {
    "TEST_CASE", "COMMAND_EXIT", "JSON_PATH", "SQL_VALUE", "METRIC_THRESHOLD",
    "FILE_PATTERN", "FILE_DIGEST", "SET_EQUALS", "MANUAL_REVIEW",
}
STATUSES = {"PENDING", "VERIFIED", "FAILED", "BLOCKED", "UNRESOLVED", "NOT_APPLICABLE"}
OPERATORS = {
    "EQUALS": operator.eq,
    "NOT_EQUALS": operator.ne,
    "LESS_THAN": operator.lt,
    "LESS_THAN_OR_EQUAL": operator.le,
    "GREATER_THAN": operator.gt,
    "GREATER_THAN_OR_EQUAL": operator.ge,
    "CONTAINS": lambda actual, expected: expected in actual,
    "NOT_CONTAINS": lambda actual, expected: expected not in actual,
}

ASSERTION_EVIDENCE_TYPES: dict[str, set[str]] = {
    "TEST_CASE": {"test_ref"},
    "COMMAND_EXIT": {"command_ref"},
    "JSON_PATH": {"test_ref", "log_ref", "runtime_ref", "metric_ref", "document_ref"},
    "SQL_VALUE": {"test_ref", "log_ref", "runtime_ref"},
    "METRIC_THRESHOLD": {"metric_ref"},
    "FILE_PATTERN": {"code_ref", "diff_ref", "document_ref", "config_ref", "log_ref", "runtime_ref", "test_ref"},
    "FILE_DIGEST": {"code_ref", "diff_ref", "document_ref", "config_ref", "log_ref", "runtime_ref", "test_ref"},
    "SET_EQUALS": {"test_ref", "log_ref", "runtime_ref", "metric_ref"},
    "MANUAL_REVIEW": set(evidence.EVIDENCE_TYPES),
}


def matching_manual_review_assertions(
    document: dict[str, Any],
    *,
    phase: str,
    revision: str,
    reviewer: str = "",
    profile_id: str = "",
    acceptance_id: str = "",
) -> list[dict[str, Any]]:
    """Return exact current-revision Review assertions, never stale predecessors."""
    result: list[dict[str, Any]] = []
    for item in document.get("assertions", []):
        if not isinstance(item, dict) or item.get("type") != "MANUAL_REVIEW":
            continue
        parameters = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
        if item.get("phase") != phase or item.get("revision") != revision:
            continue
        if reviewer and parameters.get("reviewer_agent") != reviewer:
            continue
        if profile_id and parameters.get("profile_id") != profile_id:
            continue
        if acceptance_id and item.get("acceptance_id") != acceptance_id:
            continue
        result.append(item)
    return result


def missing_manual_reviewers(
    task_dir: Path,
    *,
    phase: str,
    revision: str,
    reviewers: Iterable[str],
) -> list[str]:
    """Return required Reviewers without an exact pending/current assertion."""
    document = load_document(task_dir)
    missing: list[str] = []
    for reviewer in reviewers:
        profile_id = f"{phase}:{reviewer}"
        matches = matching_manual_review_assertions(
            document,
            phase=phase,
            revision=revision,
            reviewer=reviewer,
            profile_id=profile_id,
        )
        if not matches:
            missing.append(reviewer)
    return missing


def _review_assertion_id(phase: str, revision: str, reviewer: str, acceptance_id: str) -> str:
    key = json.dumps(
        [phase, revision, reviewer, acceptance_id],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "ASRT-REVIEW-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24].upper()


def review_binding_id(phase: str, revision: str, reviewer: str) -> str:
    key = json.dumps([phase, revision, reviewer], ensure_ascii=False, separators=(",", ":"))
    return "REVIEW-BINDING-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24].upper()


@task_write_locked
def ensure_review_assertions(
    task_dir: Path,
    *,
    phase: str,
    revision: str,
    reviewers: Iterable[str],
    acceptance_ids: Iterable[str],
    source_refs: Iterable[str],
) -> dict[str, Any]:
    """Idempotently register exact Review assertions in canonical acceptance.json."""
    import review_contract

    reviewer_values = list(dict.fromkeys(str(value) for value in reviewers if str(value)))
    acceptance_values = list(dict.fromkeys(str(value) for value in acceptance_ids if str(value)))
    source_values = list(dict.fromkeys(str(value) for value in source_refs if str(value)))
    if not reviewer_values:
        raise ValueError(f"{phase}@{revision} has no active Reviewers")
    if not acceptance_values:
        raise ValueError(f"{phase}@{revision} has no Acceptance IDs mapped from current task Trace")
    if not source_values:
        raise ValueError(f"{phase}@{revision} has no stable Review source refs")

    contract = review_contract.load_contract()
    document = load_document(task_dir)
    created: list[dict[str, Any]] = []
    existing: list[dict[str, Any]] = []
    for reviewer in reviewer_values:
        profile_id = f"{phase}:{reviewer}"
        binding_id = review_binding_id(phase, revision, reviewer)
        review_contract.resolve_profile(contract, phase, reviewer)
        for acceptance_id in acceptance_values:
            matches = matching_manual_review_assertions(
                document,
                phase=phase,
                revision=revision,
                reviewer=reviewer,
                profile_id=profile_id,
                acceptance_id=acceptance_id,
            )
            if matches:
                existing.extend(matches)
                continue
            assertion_id = _review_assertion_id(phase, revision, reviewer, acceptance_id)
            if any(
                isinstance(item, dict) and item.get("assertion_id") == assertion_id
                for item in document.get("assertions", [])
            ):
                raise ValueError(f"deterministic Review assertion ID collision: {assertion_id}")
            item = {
                "assertion_id": assertion_id,
                "acceptance_id": acceptance_id,
                "statement": (
                    f"{reviewer} independently verifies {phase}@{revision} against "
                    f"the complete {profile_id} Review profile."
                ),
                "type": "MANUAL_REVIEW",
                "phase": phase,
                "revision": revision,
                "blocking": True,
                "status": "PENDING",
                "parameters": {
                    "reviewer_agent": reviewer,
                    "review_phase": phase,
                    "scope": f"{phase}@{revision}",
                    "profile_id": profile_id,
                    "review_binding_id": binding_id,
                },
                "source_refs": source_values,
                "evidence_ids": [],
                "verified_by_agent": "",
                "verified_at": "",
                "reason": "",
            }
            document.setdefault("assertions", []).append(item)
            created.append(item)
    errors, _ = validate_document(task_dir, document)
    if errors:
        raise ValueError("invalid generated Review assertions: " + "; ".join(errors))
    if created:
        save_document(task_dir, document)
    return {
        "status": "CREATED" if created else "ALREADY_PRESENT",
        "phase": phase,
        "revision": revision,
        "createdAssertionIds": [item["assertion_id"] for item in created],
        "existingAssertionIds": list(dict.fromkeys(item["assertion_id"] for item in existing)),
        "reviewerAgents": reviewer_values,
        "acceptanceIds": acceptance_values,
    }

def assertions_path(task_dir: Path) -> Path:
    return storage.path(task_dir, "acceptance")


def default_document(task_dir: Path, target_id: str = "", version: str = "") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "target_id": target_id or task_dir.name,
        "version": version or evidence.version_root_from_task_dir(task_dir).name,
        "assertions": [],
    }


def ensure_document(task_dir: Path, target_id: str = "", version: str = "") -> Path:
    storage.assert_new_project(Path(task_dir), require_events=False)
    path = assertions_path(task_dir)
    if not path.exists():
        atomic_write_text(path, json.dumps(default_document(task_dir, target_id, version), ensure_ascii=False, indent=2) + "\n")
    return path


def load_document(task_dir: Path) -> dict[str, Any]:
    path = ensure_document(task_dir)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid acceptance assertions: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("acceptance assertions must be an object")
    return value


@task_write_locked
def save_document(task_dir: Path, value: dict[str, Any]) -> None:
    path = ensure_document(task_dir)
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def validate_document(task_dir: Path, value: Optional[dict[str, Any]] = None) -> tuple[list[str], dict[str, dict[str, Any]]]:
    errors: list[str] = []
    try:
        document = value if value is not None else load_document(task_dir)
    except ValueError as exc:
        return [str(exc)], {}
    if document.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"acceptance assertions schema_version must be {SCHEMA_VERSION}")
    if not isinstance(document.get("target_id"), str) or not document.get("target_id"):
        errors.append("acceptance assertions missing target_id")
    if not isinstance(document.get("version"), str) or not document.get("version"):
        errors.append("acceptance assertions missing version")
    assertions = document.get("assertions")
    if not isinstance(assertions, list):
        errors.append("acceptance assertions must be an array")
        return errors, {}
    allowed_fields = {
        "assertion_id", "acceptance_id", "statement", "type", "phase", "revision", "blocking",
        "status", "parameters", "source_refs", "evidence_ids", "verified_by_agent", "verified_at", "reason",
    }
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(assertions):
        prefix = f"acceptance-assertions[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be an object")
            continue
        unknown = sorted(set(item) - allowed_fields)
        if unknown:
            errors.append(f"{prefix} contains unknown fields: {unknown}")
        assertion_id = item.get("assertion_id")
        if not isinstance(assertion_id, str) or not assertion_id:
            errors.append(f"{prefix} missing assertion_id")
            continue
        if assertion_id in result:
            errors.append(f"duplicate assertion_id: {assertion_id}")
        result[assertion_id] = item
        for key in ("acceptance_id", "statement", "phase", "revision"):
            if not isinstance(item.get(key), str) or not item.get(key):
                errors.append(f"{prefix} missing {key}")
        if item.get("type") not in ASSERTION_TYPES:
            errors.append(f"{prefix} invalid type: {item.get('type')}")
        if item.get("phase") not in evidence.KNOWN_PHASES:
            errors.append(f"{prefix} unknown phase: {item.get('phase')}")
        if item.get("status") not in STATUSES:
            errors.append(f"{prefix} invalid status: {item.get('status')}")
        if not isinstance(item.get("blocking"), bool):
            errors.append(f"{prefix} blocking must be a boolean")
        if not isinstance(item.get("parameters"), dict):
            errors.append(f"{prefix} parameters must be an object")
        if not isinstance(item.get("source_refs"), list) or any(
            not isinstance(v, str) or not v for v in item.get("source_refs", [])
        ):
            errors.append(f"{prefix} source_refs must be a string array")
        if not isinstance(item.get("evidence_ids"), list) or any(not isinstance(v, str) or not v for v in item.get("evidence_ids", [])):
            errors.append(f"{prefix} evidence_ids must be a string array")
        for key in ("verified_by_agent", "verified_at", "reason"):
            if not isinstance(item.get(key, ""), str):
                errors.append(f"{prefix} {key} must be a string")
        if item.get("verified_by_agent") and item.get("verified_by_agent") not in evidence.KNOWN_AGENTS:
            errors.append(f"{prefix} unknown verified_by_agent: {item.get('verified_by_agent')}")
        validate_parameters(item, prefix, errors)
        if item.get("status") == "VERIFIED":
            if not item.get("verified_by_agent") or not item.get("verified_at"):
                errors.append(f"{prefix} VERIFIED requires verified_by_agent and verified_at")
            if not item.get("evidence_ids"):
                errors.append(f"{prefix} VERIFIED requires evidence_ids")
            expected_revision = str(item.get("revision") or "")
            expected_phase = str(item.get("phase") or "")
            if item.get("type") == "MANUAL_REVIEW":
                parameters = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
                review_phase = str(parameters.get("review_phase") or expected_phase)
                try:
                    state = load_task_state(task_dir)
                    artifacts = state.get("artifact_revisions") if isinstance(state.get("artifact_revisions"), dict) else {}
                    artifact = artifacts.get(review_phase) if isinstance(artifacts, dict) else None
                    review_revision = artifact.get("revision") if isinstance(artifact, dict) else ""
                    if review_revision:
                        expected_revision = str(review_revision)
                        expected_phase = review_phase
                except ValueError as exc:
                    errors.append(f"{prefix} {exc}")
            for evidence_id in item.get("evidence_ids") or []:
                try:
                    entry = evidence.require_evidence(
                        task_dir, evidence_id,
                        expected_revision=expected_revision,
                        expected_phase=expected_phase,
                    )
                    validate_assertion_evidence_entry(item, entry)
                except ValueError as exc:
                    errors.append(f"{prefix} {exc}")
        if item.get("status") in {"FAILED", "BLOCKED", "UNRESOLVED", "NOT_APPLICABLE"} and not item.get("reason"):
            errors.append(f"{prefix} {item.get('status')} requires reason")
    return errors, result


def nonempty_string(parameters: dict[str, Any], key: str, prefix: str, errors: list[str]) -> None:
    if not isinstance(parameters.get(key), str) or not parameters.get(key):
        errors.append(f"{prefix}.parameters missing {key}")


def validate_operator(parameters: dict[str, Any], prefix: str, errors: list[str], *, allowed: set[str] | None = None) -> None:
    operator_name = str(parameters.get("operator") or "EQUALS")
    valid = allowed or set(OPERATORS)
    if operator_name not in valid:
        errors.append(f"{prefix}.parameters invalid operator: {operator_name}")


def validate_parameters(item: dict[str, Any], prefix: str, errors: list[str]) -> None:
    assertion_type = item.get("type")
    parameters = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
    if assertion_type == "TEST_CASE":
        nonempty_string(parameters, "test_case_id", prefix, errors)
        nonempty_string(parameters, "expected_status", prefix, errors)
    elif assertion_type == "COMMAND_EXIT":
        if "expected" in parameters and not isinstance(parameters.get("expected"), int):
            errors.append(f"{prefix}.parameters expected must be an integer")
        validate_operator(parameters, prefix, errors, allowed={"EQUALS", "NOT_EQUALS"})
    elif assertion_type == "JSON_PATH":
        nonempty_string(parameters, "path", prefix, errors)
        if not str(parameters.get("path") or "").startswith("$."):
            errors.append(f"{prefix}.parameters path must use $.a.b notation")
        if "expected" not in parameters:
            errors.append(f"{prefix}.parameters missing expected")
        validate_operator(parameters, prefix, errors)
    elif assertion_type == "SQL_VALUE":
        nonempty_string(parameters, "query_ref", prefix, errors)
        if "expected" not in parameters:
            errors.append(f"{prefix}.parameters missing expected")
        validate_operator(parameters, prefix, errors)
    elif assertion_type == "METRIC_THRESHOLD":
        nonempty_string(parameters, "metric", prefix, errors)
        if "expected" not in parameters:
            errors.append(f"{prefix}.parameters missing expected")
        validate_operator(
            parameters, prefix, errors,
            allowed={"LESS_THAN", "LESS_THAN_OR_EQUAL", "GREATER_THAN", "GREATER_THAN_OR_EQUAL", "EQUALS"},
        )
    elif assertion_type == "FILE_PATTERN":
        nonempty_string(parameters, "pattern", prefix, errors)
        validate_operator(parameters, prefix, errors, allowed={"CONTAINS", "NOT_CONTAINS"})
    elif assertion_type == "FILE_DIGEST":
        digest = parameters.get("expected_digest")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            errors.append(f"{prefix}.parameters expected_digest must be a SHA-256 hex digest")
    elif assertion_type == "SET_EQUALS":
        if not isinstance(parameters.get("expected"), list):
            errors.append(f"{prefix}.parameters expected must be an array")
        nonempty_string(parameters, "path", prefix, errors)
    elif assertion_type == "MANUAL_REVIEW":
        reviewer = parameters.get("reviewer_agent")
        if reviewer not in evidence.KNOWN_AGENTS:
            errors.append(f"{prefix}.parameters unknown reviewer_agent: {reviewer}")
        review_phase = parameters.get("review_phase", item.get("phase"))
        if review_phase not in evidence.KNOWN_PHASES:
            errors.append(f"{prefix}.parameters unknown review_phase: {review_phase}")


@task_write_locked
def add_assertion(
    task_dir: Path,
    *,
    assertion_id: str,
    acceptance_id: str,
    statement: str,
    assertion_type: str,
    phase: str,
    revision: str,
    blocking: bool,
    parameters: dict[str, Any],
    source_refs: Optional[list[str]] = None,
) -> dict[str, Any]:
    if assertion_type not in ASSERTION_TYPES:
        raise ValueError(f"unknown assertion type: {assertion_type}")
    document = load_document(task_dir)
    if any(isinstance(item, dict) and item.get("assertion_id") == assertion_id for item in document.get("assertions", [])):
        raise ValueError(f"duplicate assertion_id: {assertion_id}")
    item = {
        "assertion_id": assertion_id,
        "acceptance_id": acceptance_id,
        "statement": statement,
        "type": assertion_type,
        "phase": phase,
        "revision": revision,
        "blocking": blocking,
        "status": "PENDING",
        "parameters": parameters,
        "source_refs": list(dict.fromkeys(source_refs or [f"acceptance:{acceptance_id}"])),
        "evidence_ids": [],
        "verified_by_agent": "",
        "verified_at": "",
        "reason": "",
    }
    validation_errors: list[str] = []
    if not isinstance(item["source_refs"], list) or not item["source_refs"] or not all(
        isinstance(value, str) and value.strip() for value in item["source_refs"]
    ):
        validation_errors.append("assertion.source_refs must contain at least one non-empty source reference")
    if phase not in evidence.KNOWN_PHASES:
        validation_errors.append(f"assertion.phase is not canonical: {phase}")
    if not isinstance(revision, str) or not revision.strip():
        validation_errors.append("assertion.revision is required")
    validate_parameters(item, "assertion", validation_errors)
    if validation_errors:
        raise ValueError("; ".join(validation_errors))
    document.setdefault("assertions", []).append(item)
    save_document(task_dir, document)
    return item


def json_path_get(value: Any, path: str) -> Any:
    if not path.startswith("$."):
        raise ValueError("JSON_PATH supports only $.a.b notation")
    current = value
    for part in path[2:].split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise ValueError(f"JSON path does not resolve: {path}")
    return current


def compare(actual: Any, expected: Any, op_name: str) -> bool:
    function = OPERATORS.get(op_name)
    if function is None:
        raise ValueError(f"unsupported operator: {op_name}")
    try:
        return bool(function(actual, expected))
    except TypeError as exc:
        raise ValueError(f"operator {op_name} cannot compare {actual!r} and {expected!r}") from exc


def load_evidence_json(task_dir: Path, entry: dict[str, Any]) -> Any:
    path = evidence.resolve_task_ref(task_dir, str(entry.get("ref") or ""))
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"evidence is not valid JSON: {entry.get('evidence_id')}: {exc}") from exc


def validate_assertion_evidence_entry(item: dict[str, Any], entry: dict[str, Any]) -> None:
    assertion_type = str(item.get("type") or "")
    entry_types = evidence.evidence_types(entry)
    allowed_types = ASSERTION_EVIDENCE_TYPES.get(assertion_type, set())
    if allowed_types and not (entry_types & allowed_types):
        if len(entry_types) == 1:
            actual = f"evidence type {next(iter(entry_types))}"
        else:
            actual = f"evidence types {sorted(entry_types)}"
        raise ValueError(
            f"{assertion_type} cannot use {actual}; allowed types: {sorted(allowed_types)}"
        )
    parameters = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
    scopes = set(entry.get("scope_refs") or [])
    if assertion_type == "TEST_CASE":
        required = str(parameters.get("test_case_id") or "")
        if required and required not in scopes:
            raise ValueError(f"TEST_CASE evidence scope does not contain {required}")
    elif assertion_type == "SQL_VALUE":
        required = str(parameters.get("query_ref") or "")
        if required and required not in scopes:
            raise ValueError(f"SQL_VALUE evidence scope does not contain query_ref {required}")
    elif assertion_type == "METRIC_THRESHOLD":
        required = str(parameters.get("metric") or "")
        if required and required not in scopes:
            raise ValueError(f"METRIC_THRESHOLD evidence scope does not contain metric {required}")
    elif assertion_type == "COMMAND_EXIT" and not str(entry.get("command_result_ref") or ""):
        raise ValueError("COMMAND_EXIT evidence must bind command_result_ref")


def load_task_state(task_dir: Path) -> dict[str, Any]:
    """Build MANUAL_REVIEW state from the shared reducer projection.

    The import is intentionally local because ``state_access`` imports this
    module for acceptance validation.  Keeping one state reader prevents this
    path from drifting away from cross-session/runtime queries.
    """
    from _long_task.state_access import runtime_state

    state = runtime_state(task_dir)
    if not state:
        raise ValueError("MANUAL_REVIEW requires reducer-owned task state")
    return state


def require_manual_review_conclusion(task_dir: Path, item: dict[str, Any], reviewer: str) -> None:
    state = load_task_state(task_dir)
    parameters = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
    review_phase = str(parameters.get("review_phase") or item.get("phase") or "")
    artifacts = state.get("artifact_revisions") if isinstance(state.get("artifact_revisions"), dict) else {}
    artifact = artifacts.get(review_phase) if isinstance(artifacts, dict) else None
    current_revision = artifact.get("revision") if isinstance(artifact, dict) else None
    reviews = state.get("collaboration_reviews") if isinstance(state.get("collaboration_reviews"), dict) else {}
    review = reviews.get(review_phase) if isinstance(reviews, dict) else None
    conclusions = review.get("independent_conclusions") if isinstance(review, dict) else None
    compact = conclusions.get(reviewer) if isinstance(conclusions, dict) else None
    try:
        conclusion = review_store.materialize_conclusion(task_dir, compact) if isinstance(compact, dict) else None
    except ValueError:
        conclusion = None
    if (
        not isinstance(conclusion, dict)
        or conclusion.get("conclusion") != "PASSED"
        or conclusion.get("revision") != current_revision
    ):
        raise ValueError(
            f"MANUAL_REVIEW requires current PASSED conclusion from {reviewer} "
            f"for {review_phase}@{current_revision}"
        )


def verify_assertion(task_dir: Path, item: dict[str, Any], agent: str) -> tuple[str, str]:
    if agent not in evidence.KNOWN_AGENTS:
        raise ValueError(f"unknown Agent: {agent}")
    assertion_type = item.get("type")
    params = item.get("parameters") if isinstance(item.get("parameters"), dict) else {}
    revision = str(item.get("revision") or "")
    evidence_ids = list(item.get("evidence_ids") or [])
    entries = [
        evidence.require_evidence(
            task_dir, evidence_id, expected_revision=revision,
            expected_phase=str(item.get("phase") or ""),
        )
        for evidence_id in evidence_ids
    ]
    for entry in entries:
        validate_assertion_evidence_entry(item, entry)
    actual: Any = None
    expected = params.get("expected")
    op_name = str(params.get("operator") or "EQUALS")
    if assertion_type == "MANUAL_REVIEW":
        return "BLOCKED", "MANUAL_REVIEW must be completed by long-task Review conclusion"
    if assertion_type == "COMMAND_EXIT":
        if len(entries) != 1:
            raise ValueError("COMMAND_EXIT requires exactly one evidence_id")
        command_ref = str(entries[0].get("command_result_ref") or "")
        if not command_ref:
            raise ValueError("COMMAND_EXIT evidence must bind command_result_ref")
        result = json.loads(evidence.resolve_task_ref(task_dir, command_ref).read_text(encoding="utf-8"))
        actual = result.get("exit_code")
        expected = params.get("expected", 0)
    elif assertion_type == "TEST_CASE":
        if len(entries) != 1:
            raise ValueError("TEST_CASE requires exactly one evidence_id")
        payload = load_evidence_json(task_dir, entries[0])
        actual = payload.get("status") if isinstance(payload, dict) else None
        expected = params.get("expected_status", "PASSED")
    elif assertion_type in {"JSON_PATH", "SQL_VALUE", "METRIC_THRESHOLD", "SET_EQUALS"}:
        if len(entries) != 1:
            raise ValueError(f"{assertion_type} requires exactly one evidence_id")
        payload = load_evidence_json(task_dir, entries[0])
        if assertion_type == "JSON_PATH":
            actual = json_path_get(payload, str(params.get("path") or ""))
        elif assertion_type == "SQL_VALUE":
            actual = payload.get("value") if isinstance(payload, dict) else None
        elif assertion_type == "METRIC_THRESHOLD":
            metric = str(params.get("metric") or "value")
            actual = payload.get(metric) if isinstance(payload, dict) else None
        else:
            actual = json_path_get(payload, str(params.get("path") or ""))
            expected = params.get("expected")
            return ("VERIFIED", "") if set(actual or []) == set(expected or []) else (
                "FAILED", f"actual set {actual!r} differs from expected {expected!r}"
            )
    elif assertion_type == "FILE_PATTERN":
        if len(entries) != 1:
            raise ValueError("FILE_PATTERN requires exactly one evidence_id")
        pattern = str(params.get("pattern") or "")
        actual = evidence.resolve_task_ref(task_dir, str(entries[0].get("ref") or "")).read_text(encoding="utf-8")
        expected = pattern
    elif assertion_type == "FILE_DIGEST":
        if len(entries) != 1:
            raise ValueError("FILE_DIGEST requires exactly one evidence_id")
        actual = evidence.sha256_file(evidence.resolve_task_ref(task_dir, str(entries[0].get("ref") or "")))
        expected = params.get("expected_digest")
    else:
        raise ValueError(f"unsupported assertion type: {assertion_type}")
    return ("VERIFIED", "") if compare(actual, expected, op_name) else (
        "FAILED", f"actual {actual!r} does not satisfy {op_name} expected {expected!r}"
    )


def command_add(args: argparse.Namespace) -> int:
    agent_context.authorize_static(args.agent, "acceptance", "add")
    try:
        parameters = json.loads(args.parameters)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--parameters must be JSON: {exc}") from exc
    item = add_assertion(
        Path(args.task_dir), assertion_id=args.assertion_id, acceptance_id=args.acceptance_id,
        statement=args.statement, assertion_type=args.type, phase=args.phase, revision=args.revision,
        blocking=args.blocking, parameters=parameters,
        source_refs=args.source_ref,
    )
    print(json.dumps(item, ensure_ascii=False, indent=2))
    return 0


def _current_review_registration_context(
    task_dir: Path,
    *,
    requested_phase: str = "",
    requested_revision: str = "",
) -> dict[str, Any]:
    """Derive Review registration inputs from reducer and current task Trace."""
    from _long_task.commands.queries import active_phase_reviewers, current_phase_tasks, load_task_runtime_documents

    (_state_document, state), (_plan_document, tasks), (_attempts_document, attempts) = (
        load_task_runtime_documents(task_dir)
    )
    phase = requested_phase or str(state.get("current_phase") or "")
    if phase not in evidence.KNOWN_PHASES:
        raise ValueError(f"unknown current Review phase: {phase}")
    artifact = ((state.get("artifact_revisions") or {}).get(phase) or {})
    iteration_id = str(artifact.get("iteration_id") or "")
    phase_tasks = current_phase_tasks(tasks, phase, iteration_id)
    revision = requested_revision or str(artifact.get("revision") or "")
    if not revision:
        candidates = [
            str(item.get("output_revision") or "")
            for item in attempts
            if isinstance(item, dict)
            and item.get("phase") == phase
            and item.get("iteration_id") == iteration_id
            and item.get("status") == "PASSED"
            and item.get("output_revision")
        ]
        unique_candidates = list(dict.fromkeys(candidates))
        if len(unique_candidates) != 1:
            raise ValueError(
                f"{phase} requires one current PASSED candidate revision before Review assertion registration"
            )
        revision = unique_candidates[0]
    review = ((state.get("collaboration_reviews") or {}).get(phase) or {})
    reviewers = active_phase_reviewers(task_dir, state, phase, review)
    trace_ids = list(dict.fromkeys(
        str(trace_id)
        for task in phase_tasks
        for trace_id in (task.get("acceptance_trace_ids") or [])
        if str(trace_id)
    ))
    trace_document = storage.read_json(storage.path(task_dir, "traceability"), default={})
    trace_items = trace_document.get("items", []) if isinstance(trace_document, dict) else []
    relevant_traces = [
        item for item in trace_items
        if isinstance(item, dict)
        and item.get("id") in trace_ids
        and item.get("status") != "NOT_APPLICABLE"
    ]
    acceptance_ids = list(dict.fromkeys(
        str(acceptance_id)
        for item in relevant_traces
        for acceptance_id in (item.get("acceptance_criteria") or [])
        if str(acceptance_id)
    ))
    expected_result_refs = [
        f"{task.get('id')}#expected_results/{index}"
        for task in phase_tasks
        for index, statement in enumerate(task.get("expected_results") or [])
        if isinstance(statement, str) and statement.strip()
    ]
    return {
        "phase": phase,
        "revision": revision,
        "reviewers": reviewers,
        "acceptance_ids": acceptance_ids,
        "source_refs": list(dict.fromkeys([*trace_ids, *expected_result_refs])),
    }


def command_ensure_review(args: argparse.Namespace) -> int:
    agent_context.authorize_static(args.agent, "acceptance", "ensure-review")
    task_dir = Path(args.task_dir)
    context = _current_review_registration_context(
        task_dir,
        requested_phase=str(args.phase or ""),
        requested_revision=str(args.revision or ""),
    )
    result = ensure_review_assertions(
        task_dir,
        phase=context["phase"],
        revision=context["revision"],
        reviewers=context["reviewers"],
        acceptance_ids=context["acceptance_ids"],
        source_refs=context["source_refs"],
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


@task_write_locked
def command_verify(args: argparse.Namespace) -> int:
    agent_context.authorize_declared(args.agent, "acceptance", "verify")
    task_dir = Path(args.task_dir)
    document = load_document(task_dir)
    matched = None
    for item in document.get("assertions", []):
        if isinstance(item, dict) and item.get("assertion_id") == args.assertion_id:
            matched = item
            break
    if matched is None:
        raise ValueError(f"unknown assertion_id: {args.assertion_id}")
    if args.evidence_id:
        matched["evidence_ids"] = list(dict.fromkeys(args.evidence_id))
    status, reason = verify_assertion(task_dir, matched, args.agent)
    matched["status"] = status
    matched["verified_by_agent"] = args.agent
    matched["verified_at"] = now()
    matched["reason"] = reason
    save_document(task_dir, document)
    print(json.dumps(matched, ensure_ascii=False, indent=2))
    return 0 if status == "VERIFIED" else 1


@task_write_locked
def command_manual(args: argparse.Namespace) -> int:
    agent_context.authorize_declared(args.agent, "acceptance", "manual")
    task_dir = Path(args.task_dir)
    document = load_document(task_dir)
    matched = next((
        item for item in document.get("assertions", [])
        if isinstance(item, dict) and item.get("assertion_id") == args.assertion_id
    ), None)
    if not isinstance(matched, dict):
        raise ValueError(f"unknown assertion_id: {args.assertion_id}")
    if matched.get("type") != "MANUAL_REVIEW":
        raise ValueError("manual command only applies to MANUAL_REVIEW assertions")
    reviewer = str((matched.get("parameters") or {}).get("reviewer_agent") or "")
    if reviewer and reviewer != args.agent:
        raise ValueError(f"MANUAL_REVIEW requires reviewer {reviewer}, not {args.agent}")
    if args.agent not in evidence.KNOWN_AGENTS:
        raise ValueError(f"unknown Agent: {args.agent}")
    require_manual_review_conclusion(task_dir, matched, args.agent)
    if not args.evidence_id:
        raise ValueError("MANUAL_REVIEW requires at least one evidence_id")
    for evidence_id in args.evidence_id:
        evidence.require_evidence(task_dir, evidence_id, expected_revision=str(matched.get("revision") or ""))
    matched["status"] = "VERIFIED"
    matched["evidence_ids"] = list(dict.fromkeys(args.evidence_id))
    matched["verified_by_agent"] = args.agent
    matched["verified_at"] = now()
    matched["reason"] = args.reason or "manual review verified"
    save_document(task_dir, document)
    print(json.dumps(matched, ensure_ascii=False, indent=2))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    agent_context.authorize_declared(args.agent, "acceptance", "validate")
    errors, assertions = validate_document(Path(args.task_dir))
    print(json.dumps({"status": "PASSED" if not errors else "FAILED", "errors": errors, "count": len(assertions)}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="compile one natural-language acceptance statement into a structured assertion")
    agent_context.add_agent_argument(add)
    add.add_argument("--task-dir", required=True)
    add.add_argument("--assertion-id", required=True)
    add.add_argument("--acceptance-id", required=True)
    add.add_argument("--statement", required=True)
    add.add_argument("--type", required=True, choices=sorted(ASSERTION_TYPES))
    add.add_argument("--phase", required=True)
    add.add_argument("--revision", required=True)
    add.add_argument("--blocking", action=argparse.BooleanOptionalAction, default=True)
    add.add_argument("--parameters", required=True, help="JSON object")
    add.add_argument("--source-ref", action="append", default=[], help="stable source such as TASK-ID#expected_results/0")
    add.set_defaults(func=command_add)
    ensure_review = sub.add_parser(
        "ensure-review",
        help="idempotently register current-revision MANUAL_REVIEW assertions",
    )
    agent_context.add_agent_argument(ensure_review)
    ensure_review.add_argument("--task-dir", required=True)
    ensure_review.add_argument("--phase", default="", help="defaults to reducer current phase")
    ensure_review.add_argument(
        "--revision",
        default="",
        help="defaults to current artifact or the unique PASSED candidate revision",
    )
    ensure_review.set_defaults(func=command_ensure_review)
    verify = sub.add_parser("verify")
    agent_context.add_agent_argument(verify)
    verify.add_argument("--task-dir", required=True)
    verify.add_argument("--assertion-id", required=True)
    verify.add_argument("--evidence-id", action="append", default=[])
    verify.set_defaults(func=command_verify)
    manual = sub.add_parser("manual", help="record a structured manual Review result")
    agent_context.add_agent_argument(manual)
    manual.add_argument("--task-dir", required=True)
    manual.add_argument("--assertion-id", required=True)
    manual.add_argument("--evidence-id", action="append", default=[])
    manual.add_argument("--reason", default="")
    manual.set_defaults(func=command_manual)
    validate = sub.add_parser("validate")
    agent_context.add_agent_argument(validate)
    validate.add_argument("--task-dir", required=True)
    validate.set_defaults(func=command_validate)
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except (OSError, ValueError, agent_context.AgentAuthorizationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
