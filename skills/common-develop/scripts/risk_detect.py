"""Deterministically detect review risks from current long-task modified files."""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

from _long_task import task_events
import evidence
import agent_context
import contract_normalization
import storage
from common_utils import atomic_write_text, now

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = json.loads((ROOT / "assets" / "risk-detection-contract.json").read_text(encoding="utf-8"))
RUNTIME = contract_normalization.load_runtime_contract()
CATALOG = RUNTIME.get("riskReviewerCatalog", {})
SCHEMA_VERSION = 1
HIGH_CONFIDENCE_THRESHOLD = int(CONTRACT.get("highConfidenceThreshold", 7))

def report_path(task_dir: Path) -> Path:
    return storage.path(task_dir, "risk")


def default_report(task_dir: Path) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "target_id": task_dir.name,
        "version": evidence.version_root_from_task_dir(task_dir).name,
        "input_revision": "",
        "input_files": [],
        "input_digest": "",
        "status": "NOT_SCANNED",
        "scanned_by_agent": "",
        "scanned_at": "",
        "detections": [],
        "waivers": [],
    }


def ensure_report(task_dir: Path) -> Path:
    storage.assert_new_project(Path(task_dir), require_events=False)
    path = report_path(task_dir)
    if not path.exists():
        atomic_write_text(path, json.dumps(default_report(task_dir), ensure_ascii=False, indent=2) + "\n")
    return path


def load_report(task_dir: Path) -> dict[str, Any]:
    path = ensure_report(task_dir)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid risk detection report: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("risk detection report must be an object")
    return value


def save_report(task_dir: Path, report: dict[str, Any]) -> None:
    path = ensure_report(task_dir)
    atomic_write_text(path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")


def input_digest(task_dir: Path, refs: list[str]) -> str:
    digest = hashlib.sha256()
    for ref in sorted(dict.fromkeys(refs)):
        path = evidence.resolve_task_ref(task_dir, ref)
        digest.update(ref.encode("utf-8"))
        digest.update(b"\0")
        digest.update(evidence.sha256_file(path).encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def stable_detection_id(rule_id: str, source_ref: str) -> str:
    key = f"{rule_id}\0{source_ref}".encode("utf-8")
    return "DET-" + hashlib.sha256(key).hexdigest()[:12].upper()


def load_traceability(task_dir: Path) -> list[dict[str, Any]]:
    path = storage.path(task_dir, "traceability")
    if not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid traceability document: {path}: {exc}") from exc
    if isinstance(value, dict):
        value = value.get("items", [])
    return value if isinstance(value, list) else []


def traceability_detections(task_dir: Path, *, agent: str, revision: str) -> list[dict[str, Any]]:
    detections: list[dict[str, Any]] = []
    traceability = load_traceability(task_dir)
    rules = CONTRACT.get("traceabilityRules", [])
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        flag = str(rule.get("flag") or "")
        matching_ids = [
            str(item.get("id") or "")
            for item in traceability
            if isinstance(item, dict) and item.get("status") != "NOT_APPLICABLE" and item.get(flag) is True
        ]
        if not matching_ids:
            continue
        risk_type = str(rule.get("riskType") or "")
        if risk_type not in CATALOG:
            raise ValueError(f"traceability risk rule maps unknown risk type: {risk_type}")
        evidence_entry = evidence.register_evidence(
            task_dir,
            agent=agent,
            evidence_type="document_ref",
            ref="traceability.json",
            revision=revision,
            phase="development",
            scope_refs=[str(rule.get("id") or ""), risk_type, *matching_ids],
            metadata={"risk_rule": rule.get("id"), "traceability_ids": matching_ids},
        )
        rule_id = str(rule.get("id") or "")
        detections.append({
            "detection_id": stable_detection_id(rule_id, "traceability.json"),
            "rule_id": rule_id,
            "risk_type": risk_type,
            "confidence": int(rule.get("confidence", 0)),
            "required_reviewer": CATALOG[risk_type]["reviewerAgent"],
            "evidence_ids": [evidence_entry["evidence_id"]],
            "status": "DETECTED",
            "reason": f"traceability {flag}=true: {', '.join(matching_ids)}",
            "source": "DETECTOR",
        })
    return detections


def match_rule(ref: str, text: str, rule: dict[str, Any]) -> list[str]:
    lowered_ref = ref.lower()
    lowered_text = text.lower()
    matched: list[str] = []
    for pattern in rule.get("filePatterns", []):
        normalized = str(pattern).lower()
        if fnmatch.fnmatch(lowered_ref, normalized) or fnmatch.fnmatch(Path(lowered_ref).name, normalized):
            matched.append(f"file:{pattern}")
    for pattern in rule.get("contentPatterns", []):
        if str(pattern).lower() in lowered_text:
            matched.append(f"content:{pattern}")
    return matched


def current_development_files(task_dir: Path) -> tuple[str, list[str]]:
    from _long_task.state_access import runtime_state
    history = task_events.reduce_events(task_events.read_events(task_dir))
    state = runtime_state(task_dir)
    if not state:
        raise ValueError("long-task reducer produced no task state")
    attempts = history.get("attempts", [])
    artifact = (state.get("artifact_revisions") or {}).get("development") or {}
    revision = str(artifact.get("revision") or "")
    iteration_id = str(artifact.get("iteration_id") or "")
    refs: list[str] = []
    for attempt in attempts if isinstance(attempts, list) else []:
        if not isinstance(attempt, dict):
            continue
        if (
            attempt.get("phase") == "development"
            and attempt.get("status") == "PASSED"
            and attempt.get("iteration_id") == iteration_id
            and attempt.get("output_revision") == revision
        ):
            refs.extend(str(value) for value in attempt.get("modified_files", []) if isinstance(value, str) and value)
    return revision, list(dict.fromkeys(refs))


def scan(task_dir: Path, *, agent: str, revision: str, refs: list[str]) -> dict[str, Any]:
    if agent not in evidence.KNOWN_AGENTS:
        raise ValueError(f"unknown Agent: {agent}")
    if not revision:
        raise ValueError("risk scan requires current development revision")
    normalized_refs = list(dict.fromkeys(refs))
    report = load_report(task_dir)
    retained_manual = [
        item for item in report.get("detections", [])
        if isinstance(item, dict)
        and item.get("source") == "AGENT"
        and report.get("input_revision") == revision
    ]
    detections: list[dict[str, Any]] = []
    for ref in normalized_refs:
        path = evidence.resolve_task_ref(task_dir, ref)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ValueError(f"cannot read modified file for risk scan: {ref}: {exc}") from exc
        for rule in CONTRACT.get("rules", []):
            if not isinstance(rule, dict):
                continue
            matches = match_rule(ref, text, rule)
            if not matches:
                continue
            risk_type = str(rule.get("riskType") or "")
            if risk_type not in CATALOG:
                raise ValueError(f"risk rule maps unknown risk type: {risk_type}")
            ev = evidence.register_evidence(
                task_dir,
                agent=agent,
                evidence_type="code_ref",
                ref=ref,
                revision=revision,
                phase="development",
                scope_refs=[str(rule.get("id") or ""), risk_type, *matches],
                metadata={"risk_rule": rule.get("id"), "matches": matches},
            )
            rule_id = str(rule.get("id") or "")
            detections.append(
                {
                    "detection_id": stable_detection_id(rule_id, ref),
                    "rule_id": rule_id,
                    "risk_type": risk_type,
                    "confidence": int(rule.get("confidence", 0)),
                    "required_reviewer": CATALOG[risk_type]["reviewerAgent"],
                    "evidence_ids": [ev["evidence_id"]],
                    "status": "DETECTED",
                    "reason": ", ".join(matches),
                    "source": "DETECTOR",
                }
            )
    detections.extend(traceability_detections(task_dir, agent=agent, revision=revision))
    detections.extend(retained_manual)
    detections_by_id = {
        str(item.get("detection_id")): item for item in detections if isinstance(item, dict)
    }
    detections = [detections_by_id[key] for key in sorted(detections_by_id)]
    current_ids = set(detections_by_id)
    retained_waivers = [
        item for item in report.get("waivers", [])
        if isinstance(item, dict) and str(item.get("detection_id") or "") in current_ids
    ]
    waived_ids = {str(item.get("detection_id") or "") for item in retained_waivers}
    for detection in detections:
        if str(detection.get("detection_id") or "") in waived_ids:
            detection["status"] = "WAIVED"
    report.update(
        {
            "schema_version": SCHEMA_VERSION,
            "target_id": task_dir.name,
            "version": evidence.version_root_from_task_dir(task_dir).name,
            "input_revision": revision,
            "input_files": normalized_refs,
            "input_digest": input_digest(task_dir, normalized_refs),
            "status": "SCANNED",
            "scanned_by_agent": agent,
            "scanned_at": now(),
            "detections": detections,
            "waivers": retained_waivers,
        }
    )
    save_report(task_dir, report)
    return report


def add_manual_detection(
    task_dir: Path,
    *,
    agent: str,
    risk_type: str,
    confidence: int,
    reason: str,
    evidence_ids: list[str],
) -> dict[str, Any]:
    if agent not in evidence.KNOWN_AGENTS:
        raise ValueError(f"unknown Agent: {agent}")
    if risk_type not in CATALOG:
        raise ValueError(f"unknown risk type: {risk_type}")
    if not 1 <= confidence <= 10:
        raise ValueError("confidence must be from 1 to 10")
    if not reason:
        raise ValueError("manual risk reason is required")
    report = load_report(task_dir)
    if report.get("status") != "SCANNED" or not report.get("input_revision"):
        raise ValueError("manual risk requires a current SCANNED report")
    revision = str(report.get("input_revision"))
    for evidence_id in evidence_ids:
        evidence.require_evidence(
            task_dir, evidence_id,
            expected_revision=revision,
            expected_phase="development",
        )
    if not evidence_ids:
        raise ValueError("manual risk requires evidence_ids")
    source_key = f"{risk_type}:{reason}:{','.join(sorted(evidence_ids))}"
    item = {
        "detection_id": stable_detection_id(f"MANUAL-{risk_type}", source_key),
        "rule_id": f"MANUAL-{risk_type}",
        "risk_type": risk_type,
        "confidence": confidence,
        "required_reviewer": CATALOG[risk_type]["reviewerAgent"],
        "evidence_ids": list(dict.fromkeys(evidence_ids)),
        "status": "DETECTED",
        "reason": reason,
        "source": "AGENT",
    }
    detections = [
        value for value in report.get("detections", [])
        if isinstance(value, dict) and value.get("detection_id") != item["detection_id"]
    ]
    detections.append(item)
    report["detections"] = sorted(detections, key=lambda value: str(value.get("detection_id")))
    save_report(task_dir, report)
    return item


def waive_detection(
    task_dir: Path,
    *,
    detection_id: str,
    reason: str,
    approved_by: list[str],
    evidence_ids: list[str],
) -> dict[str, Any]:
    report = load_report(task_dir)
    detection = next((
        item for item in report.get("detections", [])
        if isinstance(item, dict) and item.get("detection_id") == detection_id
    ), None)
    if not isinstance(detection, dict):
        raise ValueError(f"unknown detection_id: {detection_id}")
    if not reason or not evidence_ids:
        raise ValueError("risk waiver requires reason and evidence_ids")
    revision = str(report.get("input_revision") or "")
    approval_entries: list[dict[str, Any]] = []
    for evidence_id in evidence_ids:
        approval_entries.append(evidence.require_evidence(
            task_dir, evidence_id,
            expected_revision=revision,
            expected_phase="development",
            allowed_types={"decision_ref"},
        ))
    approvals = list(dict.fromkeys(approved_by))
    required_agents = {str(detection.get("required_reviewer") or ""), "ProjectManagerAgent"}
    if int(detection.get("confidence") or 0) >= HIGH_CONFIDENCE_THRESHOLD and not required_agents.issubset(approvals):
        raise ValueError(f"high-confidence waiver requires approvals {sorted(required_agents)}")
    if any(agent not in evidence.KNOWN_AGENTS for agent in approvals):
        raise ValueError("risk waiver contains unknown approving Agent")
    evidenced_approvers = {
        str(entry.get("generated_by_agent") or "")
        for entry in approval_entries
        if detection_id in set(entry.get("scope_refs") or [])
    }
    missing_approval_evidence = set(approvals) - evidenced_approvers
    if missing_approval_evidence:
        raise ValueError(
            f"risk waiver lacks decision evidence from approvers: {sorted(missing_approval_evidence)}"
        )
    waiver = {
        "detection_id": detection_id,
        "reason": reason,
        "approved_by": approvals,
        "evidence_ids": list(dict.fromkeys(evidence_ids)),
        "approved_at": now(),
    }
    report["waivers"] = [
        item for item in report.get("waivers", [])
        if isinstance(item, dict) and item.get("detection_id") != detection_id
    ] + [waiver]
    detection["status"] = "WAIVED"
    save_report(task_dir, report)
    return waiver


def validate_report(
    task_dir: Path,
    value: Optional[dict[str, Any]] = None,
    *,
    verify_input_digest: bool = True,
) -> tuple[list[str], dict[str, Any]]:
    errors: list[str] = []
    try:
        report = value if value is not None else load_report(task_dir)
    except ValueError as exc:
        return [str(exc)], {}
    required = {
        "schema_version", "target_id", "version", "input_revision", "input_files", "input_digest",
        "status", "scanned_by_agent", "scanned_at", "detections", "waivers",
    }
    unknown = sorted(set(report) - required)
    if unknown:
        errors.append(f"risk detection contains unknown fields: {unknown}")
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"risk detection schema_version must be {SCHEMA_VERSION}")
    if report.get("status") not in {"NOT_SCANNED", "SCANNED"}:
        errors.append(f"risk detection invalid status: {report.get('status')}")
    for key in ("target_id", "version"):
        if not isinstance(report.get(key), str) or not report.get(key):
            errors.append(f"risk detection missing {key}")
    for key in ("input_files", "detections", "waivers"):
        if not isinstance(report.get(key), list):
            errors.append(f"risk detection {key} must be an array")
    if report.get("status") == "SCANNED":
        for key in ("input_revision", "input_digest", "scanned_by_agent", "scanned_at"):
            if not isinstance(report.get(key), str) or not report.get(key):
                errors.append(f"risk detection SCANNED missing {key}")
        if report.get("scanned_by_agent") not in evidence.KNOWN_AGENTS:
            errors.append(f"risk detection unknown scanned_by_agent: {report.get('scanned_by_agent')}")
        if verify_input_digest:
            try:
                if input_digest(task_dir, list(report.get("input_files") or [])) != report.get("input_digest"):
                    errors.append("risk detection input_digest is stale")
            except ValueError as exc:
                errors.append(str(exc))
    detection_ids: set[str] = set()
    for index, detection in enumerate(report.get("detections") or []):
        prefix = f"risk-detection[{index}]"
        if not isinstance(detection, dict):
            errors.append(f"{prefix} must be an object")
            continue
        allowed = {
            "detection_id", "rule_id", "risk_type", "confidence", "required_reviewer", "evidence_ids",
            "status", "reason", "source",
        }
        extra = sorted(set(detection) - allowed)
        if extra:
            errors.append(f"{prefix} contains unknown fields: {extra}")
        detection_id = detection.get("detection_id")
        if not isinstance(detection_id, str) or not detection_id:
            errors.append(f"{prefix} missing detection_id")
        elif detection_id in detection_ids:
            errors.append(f"duplicate detection_id: {detection_id}")
        detection_ids.add(str(detection_id))
        risk_type = detection.get("risk_type")
        if risk_type not in CATALOG:
            errors.append(f"{prefix} unknown risk_type: {risk_type}")
        elif detection.get("required_reviewer") != CATALOG[risk_type]["reviewerAgent"]:
            errors.append(f"{prefix} required_reviewer differs from runtime catalog")
        if not isinstance(detection.get("confidence"), int) or isinstance(detection.get("confidence"), bool):
            errors.append(f"{prefix} confidence must be an integer")
        if detection.get("status") not in {"DETECTED", "WAIVED"}:
            errors.append(f"{prefix} invalid status: {detection.get('status')}")
        if detection.get("source") not in {"DETECTOR", "AGENT"}:
            errors.append(f"{prefix} invalid source: {detection.get('source')}")
        if not isinstance(detection.get("detection_id"), str) or not str(detection.get("detection_id")).startswith("DET-"):
            errors.append(f"{prefix} invalid detection_id: {detection.get('detection_id')}")
        if not isinstance(detection.get("evidence_ids"), list) or not detection.get("evidence_ids"):
            errors.append(f"{prefix} evidence_ids must be non-empty")
        for evidence_id in detection.get("evidence_ids") or []:
            try:
                evidence.require_evidence(
                    task_dir, evidence_id,
                    expected_revision=str(report.get("input_revision") or ""),
                    expected_phase="development",
                )
            except ValueError as exc:
                errors.append(f"{prefix} {exc}")
    waiver_map: dict[str, dict[str, Any]] = {}
    for index, waiver in enumerate(report.get("waivers") or []):
        prefix = f"risk-waivers[{index}]"
        if not isinstance(waiver, dict):
            errors.append(f"{prefix} must be an object")
            continue
        required_waiver = {"detection_id", "reason", "approved_by", "evidence_ids", "approved_at"}
        extra = sorted(set(waiver) - required_waiver)
        if extra:
            errors.append(f"{prefix} contains unknown fields: {extra}")
        detection_id = str(waiver.get("detection_id") or "")
        if detection_id not in detection_ids:
            errors.append(f"{prefix} unknown detection_id: {detection_id}")
        waiver_map[detection_id] = waiver
        approved_by = waiver.get("approved_by")
        if not isinstance(approved_by, list) or any(agent not in evidence.KNOWN_AGENTS for agent in approved_by):
            errors.append(f"{prefix} approved_by must contain declared Agents")
        if not isinstance(waiver.get("reason"), str) or not waiver.get("reason"):
            errors.append(f"{prefix} missing reason")
        if not isinstance(waiver.get("approved_at"), str) or not waiver.get("approved_at"):
            errors.append(f"{prefix} missing approved_at")
        if not isinstance(waiver.get("evidence_ids"), list) or not waiver.get("evidence_ids"):
            errors.append(f"{prefix} evidence_ids must be non-empty")
        else:
            approval_entries: list[dict[str, Any]] = []
            for evidence_id in waiver.get("evidence_ids") or []:
                try:
                    approval_entries.append(evidence.require_evidence(
                        task_dir, evidence_id,
                        expected_revision=str(report.get("input_revision") or ""),
                        expected_phase="development",
                        allowed_types={"decision_ref"},
                    ))
                except ValueError as exc:
                    errors.append(f"{prefix} {exc}")
            approved_agents = set(approved_by or []) if isinstance(approved_by, list) else set()
            evidenced_approvers = {
                str(entry.get("generated_by_agent") or "")
                for entry in approval_entries
                if detection_id in set(entry.get("scope_refs") or [])
            }
            missing_approval_evidence = approved_agents - evidenced_approvers
            if missing_approval_evidence:
                errors.append(
                    f"{prefix} lacks decision evidence from approvers: "
                    f"{sorted(missing_approval_evidence)}"
                )
    for detection in report.get("detections") or []:
        if not isinstance(detection, dict):
            continue
        if int(detection.get("confidence") or 0) < HIGH_CONFIDENCE_THRESHOLD:
            continue
        waiver = waiver_map.get(str(detection.get("detection_id") or ""))
        if detection.get("status") == "WAIVED" and not waiver:
            errors.append(f"WAIVED detection lacks waiver: {detection.get('detection_id')}")
        if detection.get("status") == "DETECTED" and waiver:
            errors.append(f"DETECTED detection has waiver but status was not updated: {detection.get('detection_id')}")
        if waiver:
            required_agents = {str(detection.get("required_reviewer") or ""), "ProjectManagerAgent"}
            if not required_agents.issubset(set(waiver.get("approved_by") or [])):
                errors.append(
                    f"high-confidence waiver {detection.get('detection_id')} requires approvals {sorted(required_agents)}"
                )
    return errors, report


def active_risk_types(report: dict[str, Any]) -> set[str]:
    waived = {
        str(item.get("detection_id"))
        for item in report.get("waivers", [])
        if isinstance(item, dict)
    }
    return {
        str(item.get("risk_type"))
        for item in report.get("detections", [])
        if isinstance(item, dict)
        and int(item.get("confidence") or 0) >= HIGH_CONFIDENCE_THRESHOLD
        and item.get("status") == "DETECTED"
        and str(item.get("detection_id")) not in waived
    }


def command_scan(args: argparse.Namespace) -> int:
    agent_context.authorize_static(args.agent, "risk_detect", "scan")
    task_dir = Path(args.task_dir)
    revision = args.revision
    refs = list(args.file or [])
    if not revision or not refs:
        inferred_revision, inferred_refs = current_development_files(task_dir)
        revision = revision or inferred_revision
        refs = refs or inferred_refs
    report = scan(task_dir, agent=args.agent, revision=revision, refs=refs)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    agent_context.authorize_declared(args.agent, "risk_detect", "validate")
    errors, report = validate_report(Path(args.task_dir))
    print(json.dumps({"status": "PASSED" if not errors else "FAILED", "errors": errors, "active_risks": sorted(active_risk_types(report))}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


def command_add(args: argparse.Namespace) -> int:
    agent_context.authorize_declared(args.agent, "risk_detect", "add")
    item = add_manual_detection(
        Path(args.task_dir), agent=args.agent, risk_type=args.risk_type,
        confidence=args.confidence, reason=args.reason, evidence_ids=args.evidence_id,
    )
    print(json.dumps(item, ensure_ascii=False, indent=2))
    return 0


def command_waive(args: argparse.Namespace) -> int:
    agent_context.authorize_static(args.agent, "risk_detect", "waive")
    item = waive_detection(
        Path(args.task_dir), detection_id=args.detection_id, reason=args.reason,
        approved_by=args.approved_by, evidence_ids=args.evidence_id,
    )
    print(json.dumps(item, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    scan_parser = sub.add_parser("scan")
    agent_context.add_agent_argument(scan_parser)
    scan_parser.add_argument("--task-dir", required=True)
    scan_parser.add_argument("--revision", default="")
    scan_parser.add_argument("--file", action="append", default=[])
    scan_parser.set_defaults(func=command_scan)
    validate_parser = sub.add_parser("validate")
    agent_context.add_agent_argument(validate_parser)
    validate_parser.add_argument("--task-dir", required=True)
    validate_parser.set_defaults(func=command_validate)
    add_parser = sub.add_parser("add", help="add an Agent-detected semantic risk to the current scan")
    agent_context.add_agent_argument(add_parser)
    add_parser.add_argument("--task-dir", required=True)
    add_parser.add_argument("--risk-type", required=True, choices=sorted(CATALOG))
    add_parser.add_argument("--confidence", required=True, type=int)
    add_parser.add_argument("--reason", required=True)
    add_parser.add_argument("--evidence-id", action="append", default=[])
    add_parser.set_defaults(func=command_add)
    waive_parser = sub.add_parser("waive", help="waive a detected risk with registered evidence and approvals")
    agent_context.add_agent_argument(waive_parser)
    waive_parser.add_argument("--task-dir", required=True)
    waive_parser.add_argument("--detection-id", required=True)
    waive_parser.add_argument("--reason", required=True)
    waive_parser.add_argument("--approved-by", action="append", default=[])
    waive_parser.add_argument("--evidence-id", action="append", default=[])
    waive_parser.set_defaults(func=command_waive)
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
