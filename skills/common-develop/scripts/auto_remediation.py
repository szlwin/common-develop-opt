#!/usr/bin/env python3
"""Deterministic policy helper for common-develop wk -auto remediation."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable

import agent_context
import contract_normalization
import work_mode
from _long_task import task_events
from _long_task.record_io import transactional_text_write


ROOT = Path(__file__).resolve().parent.parent
RUNTIME_PATH = ROOT / "assets" / "runtime-contract.json"
RUNTIME = contract_normalization.load_runtime_contract(RUNTIME_PATH)
AUTO = RUNTIME["commandInterface"]["workModes"]["auto"]
REMEDIATION = AUTO["remediationPolicy"]
ARCHITECTURE = AUTO["architectureBoundaryPolicy"]
PREFLIGHT = AUTO["semanticPreflightPolicy"]
FINAL_HEAD = AUTO["finalHeadGatePolicy"]


class AutoRemediationError(ValueError):
    """Raised when an auto-remediation decision input is invalid."""


def _bool_text(value: str) -> bool:
    normalized = str(value).strip().lower()
    if normalized in {"true", "yes", "1", "on"}:
        return True
    if normalized in {"false", "no", "0", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"expected true/false, got: {value}")


def policy_payload() -> dict[str, Any]:
    """Return the machine-owned wk -auto policy slices used by the AI."""
    return {
        "confirmationPolicy": AUTO["confirmationPolicy"],
        "remediationPolicy": REMEDIATION,
        "semanticPreflightPolicy": PREFLIGHT,
        "architectureBoundaryPolicy": ARCHITECTURE,
        "finalHeadGatePolicy": FINAL_HEAD,
        "machineSource": "assets/runtime-contract.json#/commandInterface/workModes/auto",
    }


def _stop(reason: str, *, signal: str, architecture_change: bool = False) -> dict[str, Any]:
    return {
        "signal": signal,
        "batchForward": "FROZEN",
        "autoRemediation": "STOPPED",
        "decision": "STOP",
        "stopReason": reason,
        "requiresNewExactRevision": False,
        "requiresP0": False,
        "requiresRelatedP1Review": False,
        "requiresArchitectureReview": bool(architecture_change),
    }


def classify(
    *,
    signal: str,
    deterministic: bool,
    safe: bool,
    ambiguous: bool,
    transient: bool,
    changes_code_config_or_test: bool,
    architecture_change: bool,
    same_signature_count: int,
    new_progress_evidence: bool,
) -> dict[str, Any]:
    """Resolve batch-forward versus remediation behavior for one failure signal."""
    if same_signature_count < 0:
        raise AutoRemediationError("same_signature_count must be non-negative")
    threshold = int(
        REMEDIATION["convergencePolicy"][
            "stopWhenSameFailureSignatureRepeatsWithoutProgress"
        ]
    )
    if ambiguous:
        reason = (
            "ambiguous_architecture_decision"
            if architecture_change
            else "ambiguous_business_decision"
        )
        return _stop(reason, signal=signal, architecture_change=architecture_change)
    if signal == "authority_stale" and not deterministic:
        return _stop("authority_conflict", signal=signal, architecture_change=architecture_change)
    if signal == "safety_block" and not safe:
        return _stop("safety_bypass_required", signal=signal, architecture_change=architecture_change)
    if same_signature_count >= threshold and not new_progress_evidence:
        return _stop("non_converging_remediation", signal=signal, architecture_change=architecture_change)
    if signal in {"scope_or_authorization_violation", "unsafe_or_irreversible_external_action"}:
        return _stop("unauthorized_external_action", signal=signal, architecture_change=architecture_change)

    auto_signals = {
        "ci_failure",
        "p0_failure",
        "p1_open",
        "open_p0_or_p1",
        "authority_stale",
        "safety_block",
        "test_oracle_defect",
        "test_fixture_defect",
        "configuration_semantic_defect",
        "runtime_integration_defect",
    }
    if signal not in auto_signals:
        return _stop("unsupported_or_unclassified_failure", signal=signal, architecture_change=architecture_change)

    same_revision_rerun = bool(transient and not changes_code_config_or_test)
    requires_new_revision = bool(changes_code_config_or_test and not same_revision_rerun)
    requires_p0 = signal in {
        "ci_failure", "p0_failure", "p1_open", "open_p0_or_p1",
        "test_oracle_defect", "test_fixture_defect", "configuration_semantic_defect",
        "runtime_integration_defect", "safety_block",
    } or requires_new_revision
    requires_p1 = signal in {"p1_open", "open_p0_or_p1"}
    authority_action = "RECONCILE" if signal == "authority_stale" and deterministic else "NONE"
    safety_action = "COMPLIANT_REPAIR_ONLY" if signal == "safety_block" else "NONE"
    return {
        "signal": signal,
        "batchForward": "FROZEN",
        "autoRemediation": "ACTIVE",
        "decision": "RERUN_SAME_REVISION" if same_revision_rerun else "AUTO_FIX",
        "stopReason": "",
        "authorityAction": authority_action,
        "safetyAction": safety_action,
        "requiresNewExactRevision": requires_new_revision,
        "requiresP0": bool(requires_p0),
        "requiresRelatedP1Review": bool(requires_p1),
        "requiresArchitectureReview": bool(architecture_change),
        "resumeBatchForwardWhen": [
            "current_gate_passed",
            "open_p0_p1_is_zero",
            "required_reviews_passed",
            "authority_current",
            "no_safety_block",
        ],
    }


def diagnose_log(text: str) -> dict[str, Any]:
    """Infer a conservative failure class from common build/test log signatures."""
    patterns: list[tuple[str, str]] = [
        (r"missing compiled access rule|policy.*not found|binding.*not found", "CONFIG_OR_POLICY_BINDING"),
        (r"ClassCastException", "RUNTIME_SHAPE_OR_IMPLEMENTATION"),
        (r"AssertionFailedError|expected:\s*<.*>\s*but was", "TEST_ORACLE_OR_IMPLEMENTATION"),
        (r"COMPILATION ERROR|cannot find symbol|Compilation failure", "BUILD_OR_COMPILER"),
        (r"Connection refused|timed out|Temporary failure|503 Service Unavailable", "INFRA_TRANSIENT_CANDIDATE"),
        (r"AccessDenied|permission denied|provenance|fail[- ]closed|Guard", "SAFETY_OR_AUTHORIZATION"),
        (r"SQLSTATE|SQLException|database.*failed|mysql", "DATABASE_OR_INTEGRATION"),
    ]
    matches = [name for pattern, name in patterns if re.search(pattern, text, re.IGNORECASE)]
    primary = matches[0] if matches else "UNCLASSIFIED"
    production_mutation_allowed = False
    return {
        "primaryClass": primary,
        "candidateClasses": matches or ["UNCLASSIFIED"],
        "productionMutationAllowed": production_mutation_allowed,
        "nextStep": "run preflight before production mutation",
    }


def production_preflight(checks: dict[str, bool]) -> dict[str, Any]:
    """Enforce the fixed evidence order before production implementation is changed."""
    order = list(REMEDIATION["productionMutationPrecheckOrder"])
    missing = [name for name in order if checks.get(name) is not True]
    return {
        "checkOrder": order,
        "results": {name: bool(checks.get(name)) for name in order},
        "productionMutationAllowed": not missing,
        "firstBlockingCheck": missing[0] if missing else "",
        "blockingChecks": missing,
    }


def architecture_decision(changes: list[str]) -> dict[str, Any]:
    """Decide whether the current DEV has a real architecture-boundary change."""
    trigger = set(ARCHITECTURE["runArOnlyWhen"])
    ordinary = set(ARCHITECTURE["skipArWhen"])
    supplied = [item.strip() for item in changes if item.strip()]
    unknown = sorted(set(supplied) - trigger - ordinary)
    hit = sorted(set(supplied) & trigger)
    skipped = sorted(set(supplied) & ordinary)
    return {
        "changes": supplied,
        "architectureBoundaryChanged": bool(hit),
        "requiresAr": bool(hit),
        "triggeredBy": hit,
        "ordinaryChanges": skipped,
        "unknownChanges": unknown,
        "decision": "BLOCKED_UNKNOWN_CHANGE" if unknown else ("RUN_AR" if hit else "SKIP_AR"),
    }


def convergence_decision(
    *,
    same_signature_count: int,
    previous_failure_eliminated: bool,
    failure_moved_deeper: bool,
    new_deterministic_evidence: bool,
) -> dict[str, Any]:
    """Stop only when the same failure repeats without any measurable progress."""
    threshold = int(
        REMEDIATION["convergencePolicy"][
            "stopWhenSameFailureSignatureRepeatsWithoutProgress"
        ]
    )
    progress = {
        "previous_failure_eliminated": previous_failure_eliminated,
        "failure_moved_deeper": failure_moved_deeper,
        "new_deterministic_evidence_obtained": new_deterministic_evidence,
    }
    any_progress = any(progress.values())
    stop = same_signature_count >= threshold and not any_progress
    return {
        "sameFailureSignatureCount": same_signature_count,
        "progress": progress,
        "continueRemediation": not stop,
        "decision": "STOP_NON_CONVERGING" if stop else "CONTINUE_REMEDIATION",
        "threshold": threshold,
    }


def _print(payload: dict[str, Any], json_output: bool) -> None:
    if json_output:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")




def _record_task_event(args: argparse.Namespace, event_type: str, payload: dict[str, Any]) -> None:
    task_dir_value = str(getattr(args, "task_dir", "") or "").strip()
    if not task_dir_value:
        return
    task_dir = Path(task_dir_value)
    from _long_task.state_access import runtime_state
    state = runtime_state(task_dir)
    if not isinstance(state, dict):
        raise AutoRemediationError("task-state must be an object")
    entity_id = str(getattr(args, "task_id", "") or getattr(args, "revision", "") or payload.get("signal") or event_type)
    event_payload = dict(payload)
    if getattr(args, "task_id", ""):
        event_payload["task_id"] = args.task_id
    if getattr(args, "revision", ""):
        event_payload["revision"] = args.revision
    evidence_refs = list(dict.fromkeys(getattr(args, "evidence_ref", []) or []))
    if evidence_refs:
        event_payload["evidence"] = evidence_refs
    ledger_text, _event, events = task_events.append_event_text(
        task_dir, event_type, entity_id, event_payload,
        source=f"auto_remediation.py {getattr(args, 'command', '')}",
    )
    reduced = task_events.reduce_events(events)
    projected = task_events.project_state(state, reduced)
    import current_manifest
    manifest = current_manifest.build_manifest(task_dir, state=projected)
    payloads = {
        task_events.event_path(task_dir): ledger_text,
        current_manifest.manifest_path(task_dir): json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
    }
    transactional_text_write(payloads)


def _add_task_event_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--task-dir", default="", help="optional task directory; when supplied, append the decision to task_events.jsonl")
    parser.add_argument("--task-id", default="")
    parser.add_argument("--revision", default="")
    parser.add_argument("--evidence-ref", action="append", default=[])


def command_policy(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "auto_remediation", "policy")
    _print(policy_payload(), args.json)
    return 0


def command_classify(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "auto_remediation", "classify")
    payload = classify(
        signal=args.signal,
        deterministic=args.deterministic,
        safe=args.safe,
        ambiguous=args.ambiguous,
        transient=args.transient,
        changes_code_config_or_test=args.changes_code_config_or_test,
        architecture_change=args.architecture_change,
        same_signature_count=args.same_signature_count,
        new_progress_evidence=args.new_progress_evidence,
    )
    _record_task_event(args, "AUTO_REMEDIATION_DECISION", payload)
    _print(payload, args.json)
    return 2 if payload["decision"] == "STOP" else 0


def command_diagnose_log(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "auto_remediation", "diagnose-log")
    if bool(args.text) == bool(args.log_file):
        raise AutoRemediationError("provide exactly one of --text or --log-file")
    text = args.text if args.text else Path(args.log_file).read_text(encoding="utf-8", errors="replace")
    _print(diagnose_log(text), args.json)
    return 0


def command_preflight(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "auto_remediation", "preflight")
    payload = production_preflight({
        "authority_current": args.authority_current,
        "test_oracle_authoritative": args.test_oracle_authoritative,
        "fixture_representative": args.fixture_representative,
        "configuration_semantically_valid": args.configuration_semantically_valid,
        "environment_stable": args.environment_stable,
    })
    task_dir_value = str(getattr(args, "task_dir", "") or "").strip()
    if task_dir_value:
        import long_task
        task_dir = Path(task_dir_value)
        state = long_task.runtime_state(task_dir)
        phase = str(state.get("current_phase") or "")
        mode = work_mode.validate_work_file(long_task.version_root_from_task_dir(task_dir) / "work.md")
        mutation = work_mode.mutation_decision(mode, phase, "productionCode")
        payload["mutationPolicy"] = mutation
        if not mutation["allowed"]:
            payload["productionMutationAllowed"] = False
            payload["firstBlockingCheck"] = payload.get("firstBlockingCheck") or "mutation_policy"
            payload["blockingChecks"] = list(dict.fromkeys([*(payload.get("blockingChecks") or []), "mutation_policy"]))
    _print(payload, args.json)
    return 0 if payload["productionMutationAllowed"] else 2


def command_architecture(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "auto_remediation", "architecture")
    payload = architecture_decision(args.change)
    _print(payload, args.json)
    return 2 if payload["decision"] == "BLOCKED_UNKNOWN_CHANGE" else 0


def command_convergence(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "auto_remediation", "convergence")
    payload = convergence_decision(
        same_signature_count=args.same_signature_count,
        previous_failure_eliminated=args.previous_failure_eliminated,
        failure_moved_deeper=args.failure_moved_deeper,
        new_deterministic_evidence=args.new_deterministic_evidence,
    )
    _record_task_event(args, "AUTO_REMEDIATION_CONVERGENCE", payload)
    _print(payload, args.json)
    return 2 if not payload["continueRemediation"] else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    policy = commands.add_parser("policy", help="show the machine-owned wk -auto remediation policy")
    agent_context.add_agent_argument(policy)
    policy.add_argument("--json", action="store_true")
    policy.set_defaults(func=command_policy)

    classify_parser = commands.add_parser("classify", help="resolve one failure into auto-fix/rerun/stop")
    agent_context.add_agent_argument(classify_parser)
    classify_parser.add_argument("--signal", required=True, choices=[
        "ci_failure", "p0_failure", "p1_open", "open_p0_or_p1", "authority_stale",
        "safety_block", "test_oracle_defect", "test_fixture_defect",
        "configuration_semantic_defect", "runtime_integration_defect",
        "scope_or_authorization_violation", "unsafe_or_irreversible_external_action",
    ])
    classify_parser.add_argument("--deterministic", type=_bool_text, default=True)
    classify_parser.add_argument("--safe", type=_bool_text, default=True)
    classify_parser.add_argument("--ambiguous", type=_bool_text, default=False)
    classify_parser.add_argument("--transient", type=_bool_text, default=False)
    classify_parser.add_argument("--changes-code-config-or-test", type=_bool_text, default=True)
    classify_parser.add_argument("--architecture-change", type=_bool_text, default=False)
    classify_parser.add_argument("--same-signature-count", type=int, default=0)
    classify_parser.add_argument("--new-progress-evidence", type=_bool_text, default=True)
    classify_parser.add_argument("--json", action="store_true")
    _add_task_event_args(classify_parser)
    classify_parser.set_defaults(func=command_classify)

    diagnose = commands.add_parser("diagnose-log", help="conservatively classify one CI/build/test log")
    agent_context.add_agent_argument(diagnose)
    diagnose.add_argument("--text", default="")
    diagnose.add_argument("--log-file", default="")
    diagnose.add_argument("--json", action="store_true")
    diagnose.set_defaults(func=command_diagnose_log)

    preflight = commands.add_parser("preflight", help="verify prerequisites before production mutation")
    agent_context.add_agent_argument(preflight)
    preflight.add_argument("--authority-current", type=_bool_text, required=True)
    preflight.add_argument("--test-oracle-authoritative", type=_bool_text, required=True)
    preflight.add_argument("--fixture-representative", type=_bool_text, required=True)
    preflight.add_argument("--configuration-semantically-valid", type=_bool_text, required=True)
    preflight.add_argument("--environment-stable", type=_bool_text, required=True)
    preflight.add_argument("--task-dir", default="", help="optional task; when supplied, enforce its machine mutation policy")
    preflight.add_argument("--json", action="store_true")
    preflight.set_defaults(func=command_preflight)

    architecture = commands.add_parser("architecture", help="decide whether this DEV requires wk -ar")
    agent_context.add_agent_argument(architecture)
    architecture.add_argument("--change", action="append", required=True)
    architecture.add_argument("--json", action="store_true")
    architecture.set_defaults(func=command_architecture)

    convergence = commands.add_parser("convergence", help="decide whether remediation is still making progress")
    agent_context.add_agent_argument(convergence)
    convergence.add_argument("--same-signature-count", type=int, required=True)
    convergence.add_argument("--previous-failure-eliminated", type=_bool_text, default=False)
    convergence.add_argument("--failure-moved-deeper", type=_bool_text, default=False)
    convergence.add_argument("--new-deterministic-evidence", type=_bool_text, default=False)
    convergence.add_argument("--json", action="store_true")
    _add_task_event_args(convergence)
    convergence.set_defaults(func=command_convergence)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except (AutoRemediationError, agent_context.AgentAuthorizationError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
