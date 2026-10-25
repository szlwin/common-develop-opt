#!/usr/bin/env python3
"""Verify one common-develop task and emit token-efficient gate summaries."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import acceptance
import agent_context
import evidence
import storage
import long_task
import review_store
import risk_detect
import doc_layout
from common_utils import atomic_write_text, sha256_file, sha256_text

SCHEMA_VERSION = 1
VALIDATOR_VERSION = str(agent_context.RUNTIME_CONTRACT.get("packageVersion") or "")
if not VALIDATOR_VERSION:
    raise RuntimeError("runtime contract packageVersion is required")
LEVELS = ("summary", "failures", "full")
PROFILES = (
    "task-health",
    "start-phase",
    "complete-phase",
    "advance-phase",
    "review-gate",
    "archive-gate",
    "next-phase",
)
OPEN_ISSUE_STATUSES = {"OPEN", "REOPENED", "IN_PROGRESS", "BLOCKED"}
EXCLUDED_TOP_LEVEL = {"cache", "current", "indexes", "objects", "views", "rendered"}


class TaskVerificationError(ValueError):
    """Raised when a task verification request is malformed."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    """Hash a file through the shared digest helper."""
    return sha256_file(path)


def _task_dir(doc_root: str, version: str, target: str, explicit: str) -> Path:
    if explicit:
        return Path(explicit).resolve()
    if not (doc_root and version and target):
        raise TaskVerificationError("provide --task-dir or --doc-root/--version/--target")
    return (Path(doc_root) / "version" / version / "task" / target).resolve()


def _authoritative_files(task_dir: Path, *, evidence_scope: str = "full") -> list[Path]:
    historical_only: set[Path] = set()
    if evidence_scope == "current":
        try:
            state = long_task.runtime_state(task_dir)
            current_revisions = {
                str(item.get("revision"))
                for item in (state.get("artifact_revisions") or {}).values()
                if isinstance(item, dict)
                and item.get("status") in {"IN_PROGRESS", "PASSED"}
                and str(item.get("revision") or "")
            }
            registry = evidence.load_registry(task_dir)
            current: set[Path] = set()
            historical: set[Path] = set()
            for entry in registry.get("evidences", []):
                if not isinstance(entry, dict):
                    continue
                target = current if str(entry.get("revision") or "") in current_revisions else historical
                for key in ("ref", "command_result_ref"):
                    ref = str(entry.get(key) or "")
                    if not ref:
                        continue
                    try:
                        target.add(evidence.resolve_task_ref(task_dir, ref).resolve())
                    except ValueError:
                        continue
            evidence_root = (task_dir / "evidence").resolve()
            registry_files = {
                evidence.registry_path(task_dir).resolve(),
                review_store.registry_path(task_dir).resolve(),
            }
            # Only dedicated payloads under task/evidence may be lazily omitted.
            # A canonical task/document file remains authoritative even when an
            # old Evidence entry happens to reference the same path.
            historical_only = {
                path for path in historical - current
                if path.is_relative_to(evidence_root) and path not in registry_files
            }
        except (OSError, ValueError):
            historical_only = set()
    files: list[Path] = []
    if task_dir.exists():
        for path in task_dir.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(task_dir)
            if relative.parts and relative.parts[0] in EXCLUDED_TOP_LEVEL:
                continue
            if path.name in {".DS_Store", "Thumbs.db"} or path.name.startswith("._"):
                continue
            if relative.as_posix() == "manifest.json":
                continue
            if path.resolve() in historical_only:
                continue
            files.append(path)
    version_root = long_task.version_root_from_task_dir(task_dir)
    for path in (version_root / "work.md", version_root / "work_record.md"):
        if path.is_file():
            files.append(path)
    return sorted(set(files), key=lambda item: str(item))


def input_fingerprint(task_dir: Path, *, evidence_scope: str = "full") -> tuple[str, dict[str, str]]:
    digests: dict[str, str] = {}
    base = task_dir.parent.parent.parent
    for path in _authoritative_files(task_dir, evidence_scope=evidence_scope):
        try:
            key = path.relative_to(base).as_posix()
        except ValueError:
            key = str(path)
        digests[key] = _sha256_file(path)
    layout_errors, layout_findings = doc_layout.task_layout_errors(task_dir)
    digests["__document_layout__"] = sha256_text(
        json.dumps({"errors": layout_errors, "findings": layout_findings}, ensure_ascii=False, sort_keys=True)
    )
    payload = json.dumps(digests, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256_text(payload), digests


def _read_state(task_dir: Path) -> tuple[list[str], dict[str, Any]]:
    try:
        state = long_task.runtime_state(task_dir)
    except (OSError, ValueError) as exc:
        return [str(exc)], {}
    if not isinstance(state, dict) or not state:
        return ["task_events.jsonl reducer produced no task state"], {}
    return [], state

def _read_traceability(task_dir: Path) -> tuple[list[str], list[dict[str, Any]]]:
    path = storage.path(task_dir, "traceability")
    if not path.is_file():
        return [f"missing required file: {path}"], []
    errors: list[str] = []
    value = storage.read_json(path, default={})
    value = value.get("items", []) if isinstance(value, dict) else value
    return errors, [item for item in value if isinstance(item, dict)]


def _read_issues(task_dir: Path) -> tuple[list[str], list[dict[str, Any]]]:
    try:
        return [], long_task.runtime_issues(task_dir)
    except (OSError, ValueError) as exc:
        return [str(exc)], []

def _next_phase(state: dict[str, Any]) -> tuple[str, str, bool, list[str]]:
    current = str(state.get("current_phase") or "")
    definitions = [
        item for item in agent_context.RUNTIME_CONTRACT.get("canonicalPhases", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    ids = [str(item["id"]) for item in definitions]
    artifacts = state.get("artifact_revisions") if isinstance(state.get("artifact_revisions"), dict) else {}
    reasons: list[str] = []
    if current not in ids:
        return "", "", False, [f"unknown current phase: {current}"]
    current_artifact = artifacts.get(current) if isinstance(artifacts, dict) else None
    if not isinstance(current_artifact, dict) or current_artifact.get("status") != "PASSED":
        reasons.append(f"current phase artifact is not PASSED: {current}")
    index = ids.index(current)
    if index + 1 >= len(ids):
        return "", "", not reasons, reasons
    next_definition = definitions[index + 1]
    return (
        str(next_definition.get("id") or ""),
        str(next_definition.get("ownerAgent") or ""),
        not reasons,
        reasons,
    )


def _check(check_id: str, errors: list[str], **details: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": check_id,
        "status": "FAILED" if errors else "PASSED",
        "errorCount": len(errors),
    }
    result.update(details)
    result["errors"] = list(errors)
    return result


def _diagnostic(error: str, task_dir: Path) -> dict[str, Any]:
    text = error.lower()
    code = "TASK_VALIDATION_FAILED"
    refs: list[dict[str, Any]] = []
    if "missing required file" in text:
        code = "MISSING_REQUIRED_FILE"
    elif "digest mismatch" in text:
        code = "DIGEST_MISMATCH"
        refs.append({"ref": "evidence/registry.jsonl"})
    elif "review" in text or "review_result_ref" in text:
        code = "REVIEW_GATE_FAILED"
        refs.append({"ref": "evidence/reviews.jsonl"})
    elif "trace" in text:
        code = "TRACEABILITY_INVALID"
        refs.append({"ref": "traceability.json"})
    elif "acceptance" in text or "assertion" in text:
        code = "ACCEPTANCE_INVALID"
        refs.append({"ref": "acceptance.json"})
    elif "risk" in text:
        code = "RISK_GATE_FAILED"
        refs.append({"ref": "risk.json"})
    elif "work_mode" in text or "work mode" in text:
        code = "WORK_MODE_OUT_OF_SYNC"
        refs.append({"ref": "task_events.jsonl"})
    elif "invalid_version_docs_directory" in text or "invalid_version_document_directory" in text:
        code = "INVALID_VERSION_DOCS_DIRECTORY"
        refs.append({"ref": "version/{VERSION}/doc"})
        refs.append({"ref": "docs"})
    elif "doc_root_points_to_version_directory" in text or "doc_root points to a version directory" in text:
        code = "DOC_ROOT_POINTS_TO_VERSION_DIRECTORY"
    elif "issue" in text:
        code = "OPEN_ISSUE_GATE_FAILED"
        refs.append({"ref": "task_events.jsonl"})
    if not refs:
        refs.append({"ref": "task_events.jsonl"})
    return {
        "code": code,
        "message": error,
        "recommendedReads": refs,
        "taskDir": str(task_dir),
    }


def _unique(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def run_verification(
    task_dir: Path, profile: str, *, evidence_scope: str = "full"
) -> dict[str, Any]:
    if profile not in PROFILES:
        raise TaskVerificationError(f"unknown profile: {profile}; allowed={list(PROFILES)}")
    if evidence_scope not in {"current", "full"}:
        raise TaskVerificationError("evidence_scope must be current or full")

    layout_errors, layout_findings = doc_layout.task_layout_errors(task_dir)
    state_errors, state = _read_state(task_dir)
    current_revisions = {
        str(item.get("revision"))
        for item in (state.get("artifact_revisions") or {}).values()
        if isinstance(item, dict)
        and item.get("status") in {"IN_PROGRESS", "PASSED"}
        and str(item.get("revision") or "")
    } if evidence_scope == "current" and isinstance(state, dict) else None
    evidence_errors, evidence_entries = evidence.validate_registry(
        task_dir, current_revisions=current_revisions
    )
    review_errors, review_records = review_store.validate_registry(task_dir)
    acceptance_errors, assertions = acceptance.validate_document(task_dir)
    risk_errors, risk_report = risk_detect.validate_report(task_dir)
    trace_errors, traceability = _read_traceability(task_dir)
    issue_errors, issues = _read_issues(task_dir)
    long_errors, long_warnings, validated_state, validated_issues = long_task.validate_task(
        task_dir,
        prevalidated={
            "layout": (layout_errors, layout_findings),
            "evidence": (evidence_errors, evidence_entries),
            "reviews": (review_errors, review_records),
            "acceptance": (acceptance_errors, assertions),
            "risk": (risk_errors, risk_report),
        },
    )
    if validated_state:
        state = validated_state
    if validated_issues:
        issues = validated_issues

    open_issues = [item for item in issues if str(item.get("status") or "") in OPEN_ISSUE_STATUSES]
    critical_issues = [
        item for item in open_issues
        if str(item.get("severity") or "").upper() in {"P0", "P1"}
    ]
    issue_gate_errors = [
        f"open critical issue: {item.get('id') or item.get('issue_id') or '<unknown>'}"
        for item in critical_issues
    ]

    next_phase, next_agent, can_advance, advance_reasons = _next_phase(state)
    phase_gate_errors = list(advance_reasons) if profile in {"advance-phase", "next-phase"} else []
    if profile in {"advance-phase", "next-phase"} and critical_issues:
        phase_gate_errors.extend(issue_gate_errors)
        can_advance = False

    checks = [
        _check("DOCUMENT_LAYOUT", layout_errors, findings=layout_findings),
        _check("TASK_STATE", state_errors, currentPhase=state.get("current_phase"), taskStatus=state.get("task_status")),
        _check("EVIDENCE_INTEGRITY", evidence_errors, count=len(evidence_entries)),
        _check("REVIEW_REGISTRY", review_errors, count=len(review_records)),
        _check(
            "ACCEPTANCE",
            acceptance_errors,
            count=len(assertions),
            verifiedCount=sum(1 for item in assertions.values() if item.get("status") == "VERIFIED"),
        ),
        _check("RISK_REPORT", risk_errors, findingCount=len(risk_report.get("findings", [])) if isinstance(risk_report, dict) else 0),
        _check("TRACEABILITY", trace_errors, count=len(traceability)),
        _check("OPEN_ISSUES", issue_errors + issue_gate_errors, openCount=len(open_issues), criticalCount=len(critical_issues)),
        _check("LONG_TASK", long_errors, warningCount=len(long_warnings)),
        _check("PHASE_GATE", phase_gate_errors, allowed=can_advance, nextPhase=next_phase, nextAgent=next_agent),
    ]

    primary_errors: list[str] = []
    primary_errors.extend(layout_errors)
    primary_errors.extend(state_errors)
    primary_errors.extend(evidence_errors)
    primary_errors.extend(review_errors)
    primary_errors.extend(
        error for error in acceptance_errors
        if not (evidence_errors and "evidence-index[" in error)
    )
    primary_errors.extend(risk_errors)
    primary_errors.extend(trace_errors)
    primary_errors.extend(issue_errors)
    primary_errors.extend(issue_gate_errors)
    primary_errors.extend(
        error for error in long_errors
        if error not in layout_errors
        and not (evidence_errors and "evidence-index[" in error)
        and not (review_errors and ("review" in error.lower() or "criteria_results" in error))
        and not (acceptance_errors and "acceptance_assertions." in error)
    )
    primary_errors.extend(phase_gate_errors)
    all_errors = _unique(primary_errors)
    input_digest, input_digests = input_fingerprint(task_dir, evidence_scope=evidence_scope)
    return {
        "schemaVersion": SCHEMA_VERSION,
        "validatorVersion": VALIDATOR_VERSION,
        "profile": profile,
        "evidenceScope": evidence_scope,
        "targetId": str(state.get("target_id") or task_dir.name),
        "version": str(state.get("version") or ""),
        "currentPhase": str(state.get("current_phase") or ""),
        "taskStatus": str(state.get("task_status") or ""),
        "overall": "FAILED" if all_errors else "PASSED",
        "inputDigest": input_digest,
        "inputDigests": input_digests,
        "verifiedAt": now(),
        "checks": checks,
        "warnings": _unique(long_warnings),
        "errors": all_errors,
        "diagnostics": [_diagnostic(error, task_dir) for error in all_errors],
        "next": {
            "allowed": can_advance and not all_errors,
            "phase": next_phase,
            "agent": next_agent,
            "blockingReasons": _unique(all_errors + phase_gate_errors),
        },
        "counts": {
            "evidence": len(evidence_entries),
            "reviews": len(review_records),
            "acceptanceAssertions": len(assertions),
            "traceability": len(traceability),
            "openIssues": len(open_issues),
            "criticalIssues": len(critical_issues),
        },
    }


def _cache_path(task_dir: Path, profile: str, input_digest: str, *, evidence_scope: str = "full") -> Path:
    cache_profile = profile if evidence_scope == "full" else f"{profile}-current"
    return task_dir / "cache" / "verification" / cache_profile / f"{input_digest}.json"


def _prune_profile_cache(current: Path) -> None:
    """Keep only the current digest-bound projection for one profile.

    Verification cache is derived and excluded from authority fingerprints.
    Retaining every historical digest made active task directories grow on
    every lifecycle event without improving the current read path.
    """
    for candidate in current.parent.glob("*.json"):
        if candidate != current:
            candidate.unlink(missing_ok=True)


def verify_task(
    task_dir: Path, profile: str = "task-health", *, use_cache: bool = True,
    evidence_scope: str = "full",
) -> tuple[dict[str, Any], bool]:
    input_digest, _ = input_fingerprint(task_dir, evidence_scope=evidence_scope)
    cache_path = _cache_path(task_dir, profile, input_digest, evidence_scope=evidence_scope)
    if use_cache and cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if (
                isinstance(cached, dict)
                and cached.get("validatorVersion") == VALIDATOR_VERSION
                and cached.get("inputDigest") == input_digest
                and cached.get("profile") == profile
                and cached.get("evidenceScope", "full") == evidence_scope
            ):
                return cached, True
        except (OSError, json.JSONDecodeError):
            pass
    result = run_verification(task_dir, profile, evidence_scope=evidence_scope)
    atomic_write_text(cache_path, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    _prune_profile_cache(cache_path)
    return result, False


def format_result(result: dict[str, Any], level: str, *, cache_hit: bool) -> dict[str, Any]:
    next_value = result.get("next") if isinstance(result.get("next"), dict) else {}
    base = {
        "schemaVersion": result.get("schemaVersion"),
        "validatorVersion": result.get("validatorVersion"),
        "profile": result.get("profile"),
        "evidenceScope": result.get("evidenceScope", "full"),
        "targetId": result.get("targetId"),
        "version": result.get("version"),
        "currentPhase": result.get("currentPhase"),
        "taskStatus": result.get("taskStatus"),
        "overall": result.get("overall"),
        "inputDigest": result.get("inputDigest"),
        "cacheHit": cache_hit,
        "counts": result.get("counts"),
        "next": {
            "allowed": next_value.get("allowed"),
            "phase": next_value.get("phase"),
            "agent": next_value.get("agent"),
            "blockingCount": len(next_value.get("blockingReasons") or []),
        },
    }
    if level == "summary":
        base["checks"] = [
            {
                "id": item.get("id"),
                "status": item.get("status"),
                "errorCount": item.get("errorCount", 0),
            }
            for item in result.get("checks", [])
        ]
        return base
    if level == "failures":
        diagnostics = list(result.get("diagnostics", []))
        base["failures"] = diagnostics[:20]
        base["omittedFailureCount"] = max(0, len(diagnostics) - 20)
        base["warnings"] = list(result.get("warnings", []))[:20]
        return base
    full = dict(result)
    full["cacheHit"] = cache_hit
    return full


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    verify = commands.add_parser("verify", help="verify a task and emit a compact result")
    agent_context.add_agent_argument(verify)
    verify.add_argument("--task-dir", default="")
    verify.add_argument("--doc-root", default="")
    verify.add_argument("--version", default="")
    verify.add_argument("--target", default="")
    verify.add_argument("--phase", default="", help="reserved for caller context; current phase remains authoritative")
    verify.add_argument("--profile", choices=PROFILES, default="task-health")
    verify.add_argument("--level", choices=LEVELS, default="summary")
    verify.add_argument("--no-cache", action="store_true")
    verify.add_argument("--json", action="store_true")
    return parser


def command_verify(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "task_verify", "verify", declared=True)
    task_dir = _task_dir(args.doc_root, args.version, args.target, args.task_dir)
    result, cache_hit = verify_task(task_dir, args.profile, use_cache=not args.no_cache)
    payload = format_result(result, args.level, cache_hit=cache_hit)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"{payload['overall']} {payload['targetId']} phase={payload['currentPhase']} cache={payload['cacheHit']}")
        if args.level != "summary":
            for item in payload.get("failures", []):
                print(f"ERROR {item['code']}: {item['message']}")
    return 1 if result.get("overall") == "FAILED" else 0


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "verify":
            return command_verify(args)
        return 2
    except (TaskVerificationError, agent_context.AgentAuthorizationError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
