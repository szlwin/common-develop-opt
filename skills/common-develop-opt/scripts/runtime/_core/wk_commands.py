"""Command implementations for the unified ``wk`` command-line entry point.

The public facade in :mod:`scripts.wk` retains compatibility callbacks and parser
construction; command orchestration lives here so the facade stays thin.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
import hashlib
import shutil
import shlex
import sys
from pathlib import Path
from typing import Any, Iterable

from document_core import agent_context
from runtime import long_task
from runtime import request_intake
from runtime import runtime_contract
from runtime import work_mode
from runtime import project_environment
from document_core import impact_analysis
from runtime import delivery_readiness
from checks import cli_help
from runtime import current_manifest
from runtime import authority_consistency
from runtime import doctor
from runtime import doc_layout
from runtime import governance_profile
from document_core import acceptance
from document_core import evidence
from runtime import execution_evidence
from runtime.error_catalog import SkillError, emit_cli_error, error_record
import merge_docs
from runtime._core import capability_packs
from runtime._core import wk_cli
from runtime._core import step_plan
from runtime._long_task import authorization, lifecycle_decision, reason_codes, revision_provenance, task_events
from runtime._long_task.commands.queries import read_static_task_plan
from runtime._long_task.amendment_validation import task_writer_lock
from runtime._long_task.phase_policy import phase_skip_allowed
from runtime._long_task.record_io import transactional_text_write
from runtime._long_task import state_access
from document_core.common_utils import attach_rollback_details, atomic_write_text, now, restore_files
from runtime._wk.orchestration import execution_package
from runtime._wk.risk import assess, observation_error
from runtime._wk.command_plan import build_action_plan, required_inputs
from runtime._wk.command_paths import bind_command, bind_commands
from runtime._wk.result_projection import project as project_result, classification as result_classification
from runtime._long_task.commands.queries import build_task_context
from runtime._wk.result_recorder import record as record_result


def _compact_summary(payload: Any) -> str:
    """Render the small, actionable default view for human CLI users."""
    if not isinstance(payload, dict):
        return str(payload)
    if payload.get("action") in {"begin", "next", "progress", "finish"} or payload.get("status") == "RECORDED":
        if payload.get("status") == "RECORDED":
            return "执行结果已登记；下一步仍按当前阶段条件推进。"
        message = payload.get("message") or ("当前阶段仍有条件未满足。" if payload.get("status") == "BLOCKED" else "当前阶段的执行计划已准备好。")
        owner = payload.get("owner") or payload.get("requiredAgent")
        return str(message) + (f" 下一步由 {owner} 处理。" if owner else "")
    lines: list[str] = []
    status = payload.get("status") or payload.get("state")
    if status:
        lines.append(f"状态: {status}")
    for key, label in (
        ("targetId", "目标"),
        ("taskDir", "任务目录"),
        ("currentPhase", "当前阶段"),
        ("currentRound", "当前轮次"),
        ("currentGate", "当前门禁"),
        ("nextAgent", "下一 Agent"),
        ("nextAction", "下一动作"),
    ):
        value = payload.get(key)
        if value not in (None, "", []):
            lines.append(f"{label}: {value}")
    blockers: list[str] = []
    for key in ("errors", "blockers", "reasonCodes"):
        value = payload.get(key)
        if isinstance(value, list):
            blockers.extend(str(item.get("message") if isinstance(item, dict) else item) for item in value if item)
        elif value:
            blockers.append(str(value))
    for key in ("reason", "warning", "error"):
        if payload.get(key):
            blockers.append(str(payload[key]))
    if blockers:
        lines.append(f"阻断/说明: {blockers[0]}")
    next_command = payload.get("nextCommand") or payload.get("recommendedCommand")
    if isinstance(next_command, dict):
        next_command = next_command.get("command") or next_command.get("value")
    if next_command:
        lines.append(f"下一步: {next_command}")
    if not lines:
        for key in ("count", "written", "readOnly"):
            if key in payload:
                lines.append(f"{key}: {payload[key]}")
    return "\n".join(lines) or "完成"


def emit_result(payload: Any, args: argparse.Namespace, *, exit_code: int = 0) -> int:
    """Emit complete JSON only on ``--json``; otherwise show an actionable summary."""
    payload = bind_commands(payload)
    if isinstance(payload, dict) and payload.get("status") == "BLOCKED" and payload.get("action") in {"begin", "next", "progress", "finish"}:
        payload = project_result(payload, action=payload["action"])
    if bool(getattr(args, "_legacy_output", False)) or not hasattr(args, "json") or bool(getattr(args, "json", False)):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(_compact_summary(payload))
    return exit_code


class WKError(SkillError):
    """CLI-domain error that preserves stable domain codes at the boundary."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "CLI_ERROR",
        repair_command: str | None = None,
    ) -> None:
        super().__init__(code, message, bind_command(repair_command) if repair_command else None)


COMMAND_HELP_METADATA = {
    "start": {"mutation": True, "previewSupported": True, "effect": "PROJECT_DOCUMENT_WRITE_WITH_APPLY"},
    "status": {"mutation": False, "previewSupported": False, "effect": "READ_ONLY"},
    "doctor": {"mutation": True, "previewSupported": True, "effect": "READ_ONLY_UNLESS_APPLY"},
    "next": {"mutation": False, "previewSupported": False, "effect": "READ_ONLY"},
    "step": {"mutation": True, "previewSupported": True, "effect": "LIFECYCLE_ORCHESTRATION_WITH_APPLY"},
    "reconcile": {"mutation": True, "previewSupported": True, "effect": "PROCESS_REPAIR_WITH_APPLY"},
    "close": {"mutation": True, "previewSupported": True, "effect": "LIFECYCLE_EVENT_WRITE_WITH_APPLY"},
    "confirm": {"mutation": True, "previewSupported": True, "effect": "AUTHORIZATION_BATCH_EVENT_WRITE_WITH_APPLY"},
    "impact": {"mutation": True, "previewSupported": False, "effect": "OPTIONAL_DERIVED_OUTPUT_WRITE"},
    "delivery": {"mutation": True, "previewSupported": False, "effect": "OPTIONAL_DERIVED_OUTPUT_WRITE"},
    "env": {"mutation": False, "previewSupported": False, "effect": "READ_ONLY"},
    "context": {"mutation": False, "previewSupported": False, "effect": "READ_ONLY"},
    "packs": {"mutation": False, "previewSupported": False, "effect": "READ_ONLY"},
    "help": {"mutation": False, "previewSupported": False, "effect": "READ_ONLY"},
}


def load_json_input(path: str | Path, *, context: str, required: bool = True) -> Any:
    """Load one CLI JSON input with stable, machine-readable boundary errors."""
    if not str(path or "").strip():
        if required:
            raise WKError(f"{context} requires a JSON input file", code="INPUT_REQUIRED")
        return {}
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise WKError(f"{context} input file not found: {source}", code="INPUT_NOT_FOUND") from exc
    except PermissionError as exc:
        raise WKError(f"{context} input file is not readable: {source}", code="INPUT_PERMISSION_DENIED") from exc
    except OSError as exc:
        raise WKError(f"{context} input file cannot be read: {source}: {exc}", code="INPUT_READ_FAILED") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise WKError(f"{context} input is not valid JSON: {source}: {exc.msg}", code="INPUT_INVALID_JSON") from exc


def require_json_object(value: Any, *, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WKError(f"{context} input must be a JSON object", code="INPUT_SCHEMA_INVALID")
    return value


def _current_manifest_ref(
    task_dir: Path,
    *,
    state: dict[str, Any] | None = None,
    issues: list[dict[str, Any]] | None = None,
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    """Expose the compact reducer projection without making read commands write."""
    existing = current_manifest.read_manifest(task_dir)
    try:
        manifest = current_manifest.build_manifest(
            task_dir,
            state=state,
            issues=issues,
            validation_errors=errors,
            validation_warnings=warnings,
        )
        fresh = bool(existing and existing.get("sourceInputDigest") == manifest.get("sourceInputDigest"))
    except (OSError, ValueError):
        manifest = existing
        fresh = False
    return {
        "currentManifestFresh": fresh,
        "currentManifestAuthority": False,
        "currentManifest": manifest,
        "currentManifestQueryCommand": (
            f"python3 scripts/task_layout.py view --task-dir {shlex.quote(str(task_dir.resolve()))}"
        ),
        "currentManifestSyncCommand": (
            f"python3 scripts/task_layout.py sync -g ProjectManagerAgent --task-dir {shlex.quote(str(task_dir.resolve()))} --json"
        ),
    }


def _resolved_task_work_mode(task_dir: Path) -> dict[str, Any]:
    """Return the current work mode plus machine-owned policies without guessing."""
    actual = long_task.actual_work_mode_state(task_dir)
    mode = work_mode.WorkMode(
        model_code=bool(actual.get("model_code")),
        page_design=bool(actual.get("page_design")),
        minimal=bool(actual.get("minimal")),
        auto=bool(actual.get("auto")),
        architecture_review=bool(actual.get("architecture_review")),
        git_checkpoint=bool(actual.get("git_checkpoint")),
        git_push=bool(actual.get("git_push")),
        review_only=bool(actual.get("review_only")),
        lightweight=bool(actual.get("lightweight")),
    )
    return work_mode.resolved_mode_policy(mode)


def _open_critical_issue_ids(issues: list[dict[str, Any]]) -> list[str]:
    """Return open P0/P1 IDs used to freeze batch-forward in wk -auto."""
    result: list[str] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        if issue.get("status") not in long_task.OPEN_ISSUE_STATUSES:
            continue
        if issue.get("severity") not in {"P0", "P1"}:
            continue
        issue_id = str(issue.get("id") or "").strip()
        if issue_id:
            result.append(issue_id)
    return result


def _mode_from_args(args: argparse.Namespace, current: work_mode.WorkMode | None = None) -> work_mode.WorkMode:
    """Resolve the same incremental mode patch used by work_mode.py."""
    return work_mode.mode_from_args(args, current)


def _classify(args: argparse.Namespace, agent: str) -> dict[str, Any]:
    if args.bug is not None:
        return request_intake.classify_bug(args.bug, agent=agent, reason=args.reason, evidence_refs=args.evidence_ref)
    impacts = {
        "businessLogic": args.business_logic_impact,
        "dataModel": args.data_model_impact,
        "apiContract": args.api_contract_impact,
        "permission": args.permission_impact,
        "workflow": args.workflow_impact,
        "persistence": args.persistence_impact,
        "externalSideEffect": args.external_side_effect_impact,
        "migration": args.migration_impact,
        "dependency": args.dependency_impact,
    }
    return request_intake.classify_feature(
        args.feature, agent=agent, classification=args.classification,
        related_requirement=args.related_requirement, related_feature=args.related_feature,
        impact_assessment=impacts, reason=args.reason, evidence_refs=args.evidence_ref,
    )


def _load_constraints_argument(value: str) -> dict[str, Any] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    path = Path(raw)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WKError(f"cannot read governance constraints {path}: {exc}", code="GOVERNANCE_CONSTRAINTS_INVALID") from exc
    if not isinstance(payload, dict):
        raise WKError("governance constraints must be a JSON object", code="GOVERNANCE_CONSTRAINTS_INVALID")
    return payload


def _persistable_governance_recommendation(governance: dict[str, Any]) -> dict[str, Any]:
    # The recommendation is a request fact, not a machine-local path record.
    result = copy.deepcopy(governance) if isinstance(governance, dict) else {}
    result.pop("projectRoot", None)
    return result


def command_start(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "wk", "start")
    intake = _classify(args, agent)
    doc_root = Path(args.doc_root).resolve()
    version = str(args.version)
    task_dir = doc_root / "version" / version / "task" / intake["targetId"]
    work_path = work_mode.work_path(doc_root, version)
    current_mode = work_mode.validate_work_file(work_path) if work_path.is_file() else work_mode.WorkMode()
    selected_mode = _mode_from_args(args, current_mode)
    impact_assessment = {
        "businessLogic": args.business_logic_impact,
        "dataModel": args.data_model_impact,
        "apiContract": args.api_contract_impact,
        "permission": args.permission_impact,
        "workflow": args.workflow_impact,
        "persistence": args.persistence_impact,
        "externalSideEffect": args.external_side_effect_impact,
        "migration": args.migration_impact,
        "dependency": args.dependency_impact,
    }
    constraints_payload = _load_constraints_argument(getattr(args, "constraints", ""))
    governance_internal = governance_profile._recommend_internal(
        doc_root,
        impact_assessment=impact_assessment,
        classification=intake["classification"],
        changed_scopes=getattr(args, "changed_scope", []),
        graph_path=Path(args.graph) if getattr(args, "graph", "") else None,
        changed_refs=getattr(args, "changed_ref", []),
        constraints_payload=constraints_payload,
        current_profile=getattr(args, "current_profile", ""),
        exploration=bool(getattr(args, "exploration", False)),
        override_reason=getattr(args, "override_reason", ""),
    )
    # request_intake keeps the complete calculation snapshot for auditability;
    # the user-facing preview is a strict public projection.
    governance_snapshot = _persistable_governance_recommendation(governance_internal)
    governance = governance_profile.public_governance_profile(governance_internal)
    intake["governanceRecommendation"] = governance_snapshot
    next_command = _structured_next_command({
        "recommendedCommand": f"python3 scripts/wk.py begin -g {agent} --task-dir {shlex.quote(str(task_dir))}",
        "requiredAgent": agent,
        "reasonCodes": ["WK_START_CREATED"],
        "allowedCommands": ["wk.begin"],
        "readOnly": True,
    })
    decision_guide = governance_profile.build_decision_guide(
        governance_internal,
        next_command=next_command,
        current_facts=(
            f"已完成请求分类：{intake['targetId']} / {intake['classification']}；"
            f"入口阶段：{intake['entryPhase']}。"
        ),
        actual_risk=(
            f"治理画像为 {governance['profile']}；将保留需求级门禁，"
            "推荐不会自动改变工作模式。"
        ),
        selected_mode=json.dumps(selected_mode.to_record(), ensure_ascii=False, sort_keys=True),
    )
    try:
        work_mode.validate_workflow_mode_compatibility(intake["workflowProfile"], selected_mode)
    except work_mode.WorkModeError as exc:
        raise WKError(
            exc.message,
            code=exc.code,
            repair_command=exc.repair_command,
        ) from exc
    preview = {
        "status": "BLOCKED" if intake["workflowProfile"] == "BLOCKED" else "PREVIEW",
        "apply": bool(args.apply),
        "taskDir": str(task_dir),
        "targetId": intake["targetId"],
        "classification": intake["classification"],
        "workflowProfile": intake["workflowProfile"],
        "entryPhase": intake["entryPhase"],
        "workMode": selected_mode.to_record(),
        "governanceProfile": governance,
        "decisionGuide": decision_guide,
    }
    if not args.apply:
        return emit_result({**preview, "requestIntake": intake}, args, exit_code=2 if intake["workflowProfile"] == "BLOCKED" else 0)
    if intake["workflowProfile"] == "BLOCKED":
        raise WKError("request intake is BLOCKED; provide project facts and an explicit classification before --apply")
    if task_dir.exists() and any(task_dir.iterdir()):
        raise WKError(f"task directory already exists and is not empty: {task_dir}")

    original_work = work_path.read_bytes() if work_path.exists() else None
    created_task = False
    intake_temp = doc_root / "version" / version / ".request_intake_staging" / f"{intake['targetId']}.json"
    try:
        # apply_mode_transaction owns work.md creation and event recording.
        # Do not seed with selected_mode first, otherwise a brand-new version
        # would appear unchanged and lose its first WORK_MODE_CHANGED event.
        work_mode.apply_mode_transaction(doc_root, version, work_path, selected_mode, sync_tasks=bool(getattr(args, "sync_existing_tasks", True)), agent=agent)
        request_intake.write(intake_temp, intake)
        with contextlib.redirect_stdout(io.StringIO()):
            long_task.command_init(argparse.Namespace(
                doc_root=str(doc_root), version=version, target=intake["targetId"], reuse=False,
                request_intake=str(intake_temp), agent=agent,
            ))
        created_task = True
        errors, warnings, state, _ = long_task.validate_task(task_dir)
        if errors:
            raise WKError("initialized task failed validation: " + "; ".join(errors))
        payload = {
            "status": "CREATED", "taskDir": str(task_dir), "targetId": intake["targetId"],
            "classification": intake["classification"], "workflowProfile": intake["workflowProfile"],
            "currentPhase": state.get("current_phase"), "warnings": warnings,
            "governanceProfile": governance, "decisionGuide": decision_guide,
            "nextCommand": next_command,
            "recommendedCommand": f"python3 scripts/wk.py begin -g {agent} --task-dir {shlex.quote(str(task_dir))}",
        }
        return emit_result(payload, args)
    except Exception as original_error:
        rollback_errors: list[str] = []
        recovered: list[str] = []
        try:
            if task_dir.exists() and (created_task or any(task_dir.iterdir())):
                failed_root = doc_root / "version" / version / "start_failure_backup" / f"WK-START-FAILED-{intake['targetId']}-{now().replace(':','').replace('+','_')}"
                failed_root.parent.mkdir(parents=True, exist_ok=True)
                if not failed_root.exists():
                    task_dir.rename(failed_root)
                recovered.append(str(failed_root))
        except OSError as exc:
            rollback_errors.append(f"preserve failed task: {exc}")
        try:
            if original_work is None:
                # Preserve a failed new work file instead of deleting it.
                if work_path.exists():
                    backup = doc_root / "version" / version / "start_failure_backup" / "WK-START-FAILED-work.md"
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    if not backup.exists():
                        shutil.copy2(work_path, backup)
                    recovered.append(str(backup))
            else:
                details = restore_files({work_path: original_work})
                recovered.extend(details.get("recoveredPaths", []))
                rollback_errors.extend(details.get("rollbackErrors", []))
        except OSError as exc:
            rollback_errors.append(f"restore work file: {exc}")
        attach_rollback_details(original_error, {
            "rollbackStatus": "COMPLETE" if not rollback_errors else ("PARTIAL" if recovered else "FAILED"),
            "recoveredPaths": recovered,
            "rollbackErrors": rollback_errors,
        })
        raise
    finally:
        intake_temp.unlink(missing_ok=True)


def _quick_health_checks(task_dir: Path, state: dict[str, Any], errors: list[str]) -> dict[str, Any]:
    """Run a bounded, read-only health projection for daily status checks."""
    checks: list[dict[str, Any]] = []

    def check(check_id: str, status: str, message: str) -> None:
        checks.append({"id": check_id, "status": status, "message": message})

    version_root = task_dir.parent.parent
    doc_root = version_root.parent.parent
    version = version_root.name
    work_file = work_mode.work_path(doc_root, version)
    if not work_file.is_file():
        check("work-file", "DRIFT", f"缺少 work.md：{work_file}")
    else:
        try:
            work_mode.validate_work_file(work_file)
            check("work-file", "PASSED", "work.md 可解析")
        except (OSError, ValueError, work_mode.WorkModeError) as exc:
            check("work-file", "INVALID", f"work.md 无效：{exc}")

    ledger = task_events.event_path(task_dir)
    if not ledger.is_file():
        check("task-events", "INVALID", "缺少 task_events.jsonl")
    else:
        try:
            events = task_events.read_events(task_dir)
            check("task-events", "PASSED", f"事件账本有效（{len(events)} 条）")
        except (OSError, ValueError) as exc:
            check("task-events", "INVALID", f"事件账本无效：{exc}")

    try:
        reduced = task_events.reduce_events(task_events.read_events(task_dir))
        task_events.project_state({}, reduced, synced_at="")
        check("runtime-reducer", "PASSED", "runtime reducer 可生成当前状态")
    except (OSError, ValueError) as exc:
        check("runtime-reducer", "INVALID", f"runtime reducer 失败：{exc}")

    intake_path = task_dir / "request_intake.json"
    if not intake_path.is_file():
        check("request-intake", "DRIFT", "缺少 request_intake.json")
    else:
        try:
            request_intake.load(intake_path)
            check("request-intake", "PASSED", "request_intake.json 可解析")
        except (OSError, ValueError) as exc:
            check("request-intake", "INVALID", f"request_intake.json 无效：{exc}")

    try:
        consistency = authority_consistency.check(task_dir)
    except (OSError, ValueError) as exc:
        consistency = {"status": "INVALID", "error": str(exc)}
    check(
        "authority-consistency",
        "PASSED" if consistency.get("status") == "PASSED" else ("INVALID" if consistency.get("status") == "INVALID" else "DRIFT"),
        "摘要与 runtime authority 一致" if consistency.get("status") == "PASSED" else f"authority 一致性检查失败：{consistency.get('error') or '摘要可能与 runtime authority 漂移'}",
    )
    if errors:
        check("task-validation", "INVALID", "；".join(errors[:3]))
    else:
        check("task-validation", "PASSED", "任务基础校验通过")
    statuses = {str(item.get("status")) for item in checks}
    overall = "INVALID" if "INVALID" in statuses else ("DRIFT" if "DRIFT" in statuses else "PASSED")
    return {"status": overall, "quickChecks": checks, "readOnly": True}


def _structured_next_command(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize next-step metadata while retaining the legacy command string."""
    raw = result.get("recommendedCommand")
    if isinstance(raw, dict):
        command = str(raw.get("command") or "")
    else:
        command = str(raw or "")
    reason_codes = [str(item) for item in (result.get("reasonCodes") or []) if str(item)]
    reason = str(result.get("reason") or "；".join(reason_codes) or result.get("currentGate") or "")
    required_agent = str(result.get("requiredAgent") or "")
    allowed = result.get("allowedCommands") if isinstance(result.get("allowedCommands"), list) else []
    readonly_prefixes = ("wk.status", "wk.next", "wk.doctor", "wk.env", "wk.context", "wk.packs", "wk.help", "long_task.validate", "long_task.task-context", "request_intake.validate")
    read_only = bool(result.get("readOnly")) if "readOnly" in result else bool(allowed) and all(str(item).startswith(readonly_prefixes) for item in allowed)
    decision = result.get("decision") if isinstance(result.get("decision"), dict) else {}
    classification = str(result.get("classification") or decision.get("classification") or "")
    review_required = classification in {"REVIEW_REQUIRED", "REVALIDATION_REQUIRED"}
    return {
        "command": bind_command(command),
        "reason": reason,
        "requiredAgent": required_agent,
        "requiresConfirmation": bool(result.get("requiresConfirmation") or decision.get("requiresConfirmation")),
        "readOnly": read_only,
        "executionContext": "ISOLATED_REVIEW_SESSION_REQUIRED" if review_required else "CURRENT_SESSION_ALLOWED",
        "ownerContextReuseAllowed": not review_required,
    }


def _safe_next_payload(
    task_dir: Path,
    payload: dict[str, Any],
    agent: str,
    *,
    explain: bool = False,
    impact_plan: str = "",
) -> dict[str, Any]:
    """Build a stable read-only next-step projection even when inputs are broken."""
    task_dir = task_dir.resolve()
    try:
        result = _next_payload(
            task_dir,
            payload,
            agent,
            explain=True,
            impact_plan=impact_plan,
        )
    except Exception as exc:  # status/next are diagnostics and must not crash on bad state
        result = {
            "currentGate": "TASK_VALIDATION",
            "allowedCommands": ["long_task.validate"],
            "recommendedCommand": (
                f"python3 scripts/long_task.py validate -g {agent} "
                f"--task-dir {shlex.quote(str(task_dir))} --json"
            ),
            "requiredAgent": agent,
            "reasonCodes": ["NEXT_PROJECTION_UNAVAILABLE"],
            "errors": [f"无法生成下一步建议：{exc}"],
        }
    result["recommendedCommand"] = (
        result.get("recommendedCommand", "") if not isinstance(result.get("recommendedCommand"), dict)
        else result["recommendedCommand"].get("command", "")
    )
    result["nextCommand"] = _structured_next_command(result)
    result["requiredInputs"] = required_inputs(result.get("recommendedCommand"))
    result["actionPlan"] = build_action_plan(result, task_dir=str(task_dir))
    inner = result.get("decision") if isinstance(result.get("decision"), dict) else None
    if inner is None and not result.get("errors"):
        inner = lifecycle_decision.evaluate(task_dir, long_task, validation=getattr(payload, "validation", None))
    if inner is not None:
        preview = step_plan.build_preview(task_dir, runtime=long_task, decision=inner, validation=getattr(payload, "validation", None))
        result["planDigest"] = preview["planDigest"]
        result["applySteps"] = preview["applySteps"][:1]
        result["revision"] = preview["currentRevision"]
        result["phase"] = preview["currentPhase"]
        if preview["applySteps"] and preview["status"] == "READY":
            action = preview["applySteps"][0]
            coordinator = "ProjectManagerAgent"
            atomic_agent = _step_agent_for_action(action, coordinator, inner)
            atomic_args = _step_atomic_args(action, task_dir, atomic_agent, inner)
            command = _step_repair_command(action, task_dir, atomic_agent)
            if action == "long_task.publish-artifact":
                command = command.replace("{ATTEMPT_ID}", shlex.quote(atomic_args.attempt_id))
            result["actionPlan"] = [{"action": action, "command": command, "reason": result.get("reason") or action,
                "requiredAgent": atomic_agent, "atomic": True, "executionContext": result["nextCommand"]["executionContext"]}]
            result["progressAgent"] = coordinator
            result["progressCommand"] = (f"python3 scripts/wk.py progress -g {coordinator} "
                f"--task-dir {shlex.quote(str(task_dir))} --apply --plan-digest {preview['planDigest']} --json")
    if not explain:
        result.pop("decision", None)
    result["kind"] = "AI_EXECUTION_PACKAGE"
    return bind_commands(project_result(result, action="next"))


class _StatusSnapshot(dict):
    """Share one fresh validation within a CLI call without exposing it in JSON."""
    def __init__(self, payload: dict[str, Any], validation: tuple):
        super().__init__(payload)
        self.validation = validation


def _status_payload(task_dir: Path, *, full: bool = False, include_next: bool = True) -> dict[str, Any]:
    try:
        errors, warnings, state, issues = long_task.validate_task(task_dir)
    except Exception as exc:  # keep read-only status structured for malformed task input
        errors, warnings, state, issues = [f"任务校验失败：{exc}"], [], {}, []
    intake_context: dict[str, Any] = {}
    intake_path = task_dir / "request_intake.json"
    if intake_path.is_file():
        try:
            document = request_intake.load(intake_path)
            intake_context = {
                "classification": document["classification"],
                "workflowProfile": document["workflowProfile"],
                "classificationRevision": document["classificationRevision"],
            }
        except request_intake.RequestIntakeError as exc:
            intake_context = {"status": "INVALID", "error": str(exc)}
    critical_issue_ids = _open_critical_issue_ids(issues)
    payload = _StatusSnapshot({
        "status": "INVALID" if errors else str(state.get("task_status") or "UNKNOWN"),
        "taskDir": str(task_dir), "targetId": state.get("target_id"),
        "currentPhase": state.get("current_phase"), "currentRound": state.get("current_round"),
        "nextAgent": state.get("next_agent"), "nextAction": state.get("next_action"),
        "requestIntake": intake_context, "openIssueCount": sum(
            1 for issue in issues if isinstance(issue, dict) and issue.get("status") in long_task.OPEN_ISSUE_STATUSES
        ),
        "openCriticalIssueIds": critical_issue_ids,
        "errors": errors, "warnings": warnings,
    }, (errors, warnings, state, issues))
    try:
        payload["authorityConsistency"] = authority_consistency.check(task_dir)
    except (OSError, ValueError) as exc:
        # Keep status useful even when the authority ledger is missing or corrupt;
        # the bounded health projection reports the precise read-only failure.
        payload["authorityConsistency"] = {
            "status": "INVALID",
            "error": str(exc),
        }
    try:
        resolved_mode = _resolved_task_work_mode(task_dir)
        payload["workMode"] = resolved_mode
        if resolved_mode.get("auto") is True:
            payload["autoExecution"] = {
                "enabled": True,
                "batchForward": "FROZEN" if critical_issue_ids else "ELIGIBLE",
                "remediationActive": bool(critical_issue_ids),
                "remediationPolicyRef": "assets/runtime/runtime-contract.json#/commandInterface/workModes/auto/remediationPolicy",
                "remediationStopWhen": resolved_mode.get("confirmationPolicy", {}).get("remediationStopWhen", []),
            }
    except (OSError, ValueError, work_mode.WorkModeError) as exc:
        record = error_record(exc, default_code="WORKMODE_INVALID")
        work_mode_error = {
            "status": "INVALID",
            "errorCode": record.get("code", "WORKMODE_INVALID"),
            "message": record.get("message", str(exc)),
        }
        payload["workMode"] = work_mode_error
        payload["errors"].append(f"{work_mode_error['errorCode']}: {work_mode_error['message']}")
        payload["status"] = "INVALID"
    if full:
        payload["taskState"] = state
    try:
        payload.update(_current_manifest_ref(
            task_dir, state=state, issues=issues, errors=errors, warnings=warnings,
        ))
    except Exception as exc:
        payload.update({
            "currentManifestFresh": False,
            "currentManifestAuthority": False,
            "currentManifest": None,
            "currentManifestQueryCommand": (
                f"python3 scripts/task_layout.py view --task-dir {shlex.quote(str(task_dir.resolve()))}"
            ),
            "currentManifestSyncCommand": (
                f"python3 scripts/task_layout.py sync -g ProjectManagerAgent "
                f"--task-dir {shlex.quote(str(task_dir.resolve()))} --json"
            ),
        })
        errors.append(f"无法读取派生 manifest：{exc}")
        # The manifest is a derived projection, but an unavailable projection
        # still makes the status diagnostic invalid rather than silently
        # reporting the earlier task status as healthy.
        payload["status"] = "INVALID"
    try:
        payload["health"] = _quick_health_checks(task_dir, state, errors)
    except Exception as exc:
        payload["health"] = {
            "status": "INVALID",
            "quickChecks": [{"id": "status-projection", "status": "INVALID", "message": str(exc)}],
            "readOnly": True,
        }
    if include_next:
        next_result = _safe_next_payload(
            task_dir,
            payload,
            str(state.get("next_agent") or "ProjectManagerAgent"),
        )
        payload["nextCommand"] = next_result.get("nextCommand", {})
    return payload


def _status_summary_payload(task_dir: Path) -> dict[str, Any]:
    """Return the authoritative lifecycle position without running diagnostic projections.

    This is intentionally narrower than :func:`_status_payload`: users and
    coordinators can identify the current phase in one cheap reducer read, then
    opt into the normal/full status only when drift or gate diagnosis is needed.
    """
    try:
        state = long_task.runtime_state(task_dir)
        if not isinstance(state, dict) or not state:
            raise ValueError("task event reducer produced no state")
        issues = long_task.runtime_issues(task_dir)
    except (OSError, ValueError) as exc:
        return {
            "status": "INVALID",
            "taskDir": str(task_dir),
            "errors": [str(exc)],
            "source": "task_events.jsonl reducer",
            "diagnosticsRun": False,
        }
    open_issues = [
        item for item in issues
        if isinstance(item, dict) and item.get("status") in long_task.OPEN_ISSUE_STATUSES
    ]
    return {
        "status": str(state.get("task_status") or "UNKNOWN"),
        "taskDir": str(task_dir),
        "targetId": state.get("target_id"),
        "currentPhase": state.get("current_phase"),
        "currentRound": state.get("current_round"),
        "currentAgent": state.get("current_agent"),
        "nextAgent": state.get("next_agent"),
        "nextAction": state.get("next_action"),
        "activeTaskIds": list(state.get("active_task_ids") or []),
        "currentAttemptId": state.get("current_attempt_id") or "",
        "openIssueCount": len(open_issues),
        "openCriticalIssueIds": _open_critical_issue_ids(open_issues),
        "source": "task_events.jsonl reducer",
        "diagnosticsRun": False,
        "detailCommand": f"python3 scripts/wk.py status -g ProjectManagerAgent --task-dir {shlex.quote(str(task_dir.resolve()))} --json",
    }


def command_status(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "wk", "status")
    task_dir = Path(args.task_dir)
    payload = (
        _status_summary_payload(task_dir)
        if bool(getattr(args, "summary", False))
        else _status_payload(task_dir, full=bool(args.full))
    )
    return emit_result(payload, args, exit_code=1 if payload["status"] == "INVALID" else 0)


def command_doctor(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "wk", "doctor")
    if bool(getattr(args, "apply", False)) and not bool(getattr(args, "fix", False)):
        raise WKError(
            "doctor --apply 必须同时指定 --fix；默认 doctor 保持只读",
            code="DOCTOR_FIX_REQUIRES_FIX",
        )
    if bool(getattr(args, "fix", False)):
        payload = doctor.fix(
            Path(args.doc_root),
            str(args.version),
            agent=agent,
            apply=bool(getattr(args, "apply", False)),
        )
    else:
        payload = doctor.inspect(Path(args.doc_root), str(args.version), agent=agent)
    exit_code = 0 if payload.get("status") in {"PASSED", "FIXED"} else (
        0 if payload.get("status") == "PREVIEW" and payload.get("diagnosticStatus") == "PASSED" else 1
    )
    if getattr(args, "json", False):
        return emit_result(payload, args, exit_code=exit_code)
    print(doctor.render(payload))
    return exit_code


def _recommended_from_decision(decision: dict[str, Any], task_dir: Path, agent: str) -> dict[str, Any]:
    classification = str(decision.get("classification") or "")
    required = str(decision.get("requiredAgent") or agent)
    candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
    # Impact analysis can tighten a lifecycle decision to BLOCKED. Its
    # recovery command must stay in the same vocabulary as legalActions;
    # task-context is not a legal action for an impact-analysis blocker.
    if (
        classification == "BLOCKED"
        and decision.get("currentState") == reason_codes.IMPACT_ANALYSIS_BLOCKED
    ):
        required = "ProjectManagerAgent"
        impact = decision.get("impactPlan") if isinstance(decision.get("impactPlan"), dict) else {}
        revision = str(impact.get("revision") or "{REVISION}")
        change_class = str(impact.get("changeClass") or "{CHANGE_CLASS}")
        command = (
            f"python3 scripts/wk.py impact -g {required} --task-dir {shlex.quote(str(task_dir))} "
            f"--phase {decision.get('currentPhase') or '{PHASE}'} "
            f"--revision {revision} --change-class {change_class}"
        )
        allowed = list(decision.get("legalActions") or ["wk.impact", "wk.env"])
    elif classification == "PUBLISH_READY":
        command = (
            f"python3 scripts/long_task.py publish-artifact -g {required} --task-dir {shlex.quote(str(task_dir))} "
            f"--attempt-id {candidate.get('attemptId') or '{ATTEMPT_ID}'}"
        )
        allowed = ["long_task.publish-artifact"]
    elif classification == "ACCEPTANCE_REGISTRATION_REQUIRED":
        revision = str(candidate.get("revision") or "{REVISION}")
        command = (
            f"python3 {acceptance.__file__} ensure-review -g ProjectManagerAgent "
            f"--task-dir {shlex.quote(str(task_dir))} "
            f"--phase {decision.get('currentPhase') or '{PHASE}'} --revision {shlex.quote(revision)}"
        )
        allowed = ["acceptance.ensure-review"]
        required = "ProjectManagerAgent"
    elif classification == "CLOSURE_ONLY":
        command = f"python3 scripts/wk.py close -g ProjectManagerAgent --task-dir {shlex.quote(str(task_dir))}"
        allowed = ["wk.close", "long_task.finalize-phase"]
        required = "ProjectManagerAgent"
    elif classification == "ADVANCE_READY":
        command = f"python3 scripts/long_task.py advance-phase -g ProjectManagerAgent --task-dir {shlex.quote(str(task_dir))}"
        allowed = ["long_task.advance-phase"]
        required = "ProjectManagerAgent"
    elif classification == "RECONCILIATION_REQUIRED":
        command = f"python3 scripts/wk.py reconcile -g {required} --task-dir {shlex.quote(str(task_dir))}"
        allowed = ["wk.reconcile"]
    elif classification in {"REVIEW_REQUIRED", "REVALIDATION_REQUIRED"}:
        command = f"python3 scripts/long_task.py task-context -g {required} --task-dir {shlex.quote(str(task_dir))} --task-id {{TASK_ID}}"
        allowed = list(decision.get("legalActions") or ["manual_review.prepare", "manual_review.submit"] )
    elif classification in {"COMPLETED"}:
        command = ""
        allowed = []
    else:
        command = f"python3 scripts/long_task.py task-context -g {required} --task-dir {shlex.quote(str(task_dir))} --task-id {{TASK_ID}}"
        allowed = list(decision.get("legalActions") or ["long_task.task-context", "long_task.start-attempt"] )
    if "{TASK_ID}" in command:
        current_tasks = [t for t in long_task.runtime_tasks(task_dir)
                         if t.get("phase") == decision.get("currentPhase")]
        selected = next((t for t in current_tasks if t.get("status") == "RUNNING"), None)
        if selected is None:
            selected = next((t for t in current_tasks if t.get("status") in {"PENDING", "READY", "REWORK", "FAILED"}), None)
        if classification == "EXECUTION_REQUIRED" and selected:
            required = str(selected.get("owner_agent") or required)
            command = (f"python3 scripts/long_task.py start-attempt -g {shlex.quote(required)} "
                       f"--task-dir {shlex.quote(str(task_dir))} --task-id {shlex.quote(selected['id'])}")
        elif selected:
            command = command.replace("{TASK_ID}", shlex.quote(selected["id"]))
        else:
            command = command.replace(" --task-id {TASK_ID}", "")
    result = {
        "currentGate": decision.get("currentPhase") or decision.get("currentState"),
        "classification": classification,
        "allowedCommands": allowed,
        "recommendedCommand": command,
        "requiredAgent": required,
        "reasonCodes": list(decision.get("reasonCodes") or []),
        "reason": str(decision.get("preferredAction") or decision.get("currentState") or "下一步可执行动作"),
    }
    result["nextCommand"] = _structured_next_command(result)
    return result


def _decision_with_impact(decision: dict[str, Any], impact_plan_path: str) -> dict[str, Any]:
    if not impact_plan_path:
        return decision
    plan = impact_analysis.load_plan(Path(impact_plan_path))
    decision_task = str(Path(str(decision.get("taskDir") or "")).resolve()) if decision.get("taskDir") else ""
    plan_task = str(Path(str(plan.get("taskDir") or "")).resolve()) if plan.get("taskDir") else ""
    if decision_task and plan_task and decision_task != plan_task:
        raise WKError("impact plan belongs to a different task directory")
    if str(plan.get("phase") or "") != str(decision.get("currentPhase") or ""):
        raise WKError("impact plan phase does not match the current lifecycle phase")
    return impact_analysis.tighten_lifecycle_decision(decision, plan)


def _blocked_impact_plan(
    args: argparse.Namespace,
    *,
    task_dir: Path,
    phase: str,
    base_revision: str,
    output_path: Path,
    blocker: str,
) -> dict[str, Any]:
    """Build a current, schema-shaped failure projection that supersedes stale cache."""
    try:
        provenance = revision_provenance.build(
            base_revision=base_revision,
            revision=args.revision,
            change_class=args.change_class,
            changed_scopes=args.changed_scope,
            changed_paths=args.changed_path,
            semantic_artifact_changed=args.semantic_artifact_changed,
            production_changed=args.production_changed,
        )
    except ValueError as exc:
        # The failure path itself must be total. Invalid combinations such as
        # PROCESS_ONLY + semanticArtifactChanged=TRUE must still replace the
        # current projection instead of re-raising while building the blocker.
        requested_class = str(args.change_class or "UNKNOWN").strip().upper()
        change_class = (
            requested_class
            if requested_class in revision_provenance.CHANGE_CLASSES
            else "UNKNOWN"
        )

        def safe_tri(value: Any) -> str:
            if isinstance(value, bool):
                return "TRUE" if value else "FALSE"
            normalized = str(value or "").strip().upper()
            return normalized if normalized in revision_provenance.TRI_STATE else "UNKNOWN"

        provenance = {
            "schemaVersion": 1,
            "authority": "DERIVED",
            "baseRevision": str(base_revision or ""),
            "revision": str(args.revision or ""),
            "changeClass": change_class,
            "changedScopes": revision_provenance.unique_values(args.changed_scope),
            "changedPaths": revision_provenance.unique_values(args.changed_path),
            "semanticArtifactChanged": safe_tri(args.semantic_artifact_changed),
            "productionChanged": safe_tri(args.production_changed),
            "processOnly": False,
            "warnings": [f"invalid impact-analysis input: {exc}"],
        }
    process_only = bool(provenance.get("processOnly"))
    return {
        "schemaVersion": 1,
        "authority": "DERIVED",
        "kind": "incremental_revalidation_plan",
        "taskDir": str(task_dir),
        "phase": phase,
        "provenance": provenance,
        "graphImpact": None,
        "requiredRiskTemplates": [],
        "affectedScope": {"taskIds": [], "traceIds": [], "acceptanceIds": []},
        "adapter": {
            "used": False,
            "confidence": "NONE",
            "affectedScopes": [],
            "affectedArtifacts": [],
            "notes": [],
        },
        "fallbackPolicy": (
            "PROCESS_ONLY_NO_SEMANTIC_REVALIDATION"
            if process_only else "CONSERVATIVE_FULL_PROFILE"
        ),
        "minimumRevalidation": {
            "reviews": [],
            "validationCapabilities": [],
            "acceptanceDisposition": "UNCHANGED" if process_only else "RECHECK_AFFECTED_ASSERTIONS",
        },
        "unaffected": {"reviewCriteria": []},
        "reusePolicy": {
            "reviewResultReuseAcrossExactRevision": False,
            "criterionEvidenceIdReuseAcrossExactRevision": False,
            "unaffectedEvidenceSourceReuse": "CONDITIONAL_RECAPTURE_OR_REBIND",
            "conditions": [
                "source content/digest and criterion scope remain unchanged",
                "the source is recaptured or deterministically rebound as current-revision Evidence before it supports the criterion",
                "current Evidence remains ACTIVE and satisfies the criterion Evidence Policy",
                "Reviewer still emits a new independent Review Result when the exact review revision changes",
            ],
        },
        "revalidationRequired": False,
        "reasonCodes": [reason_codes.IMPACT_ANALYSIS_BLOCKED],
        "capabilityResolution": {"status": "BLOCKED", "required": [], "missing": []},
        "warnings": list(provenance.get("warnings") or []),
        "blockers": [blocker],
        "status": "BLOCKED",
        "derivedOutputPath": str(output_path),
    }


def _emit_blocked_impact(
    args: argparse.Namespace,
    *,
    task_dir: Path,
    phase: str,
    base_revision: str,
    default_output_path: Path,
    output_path: Path,
    output_path_checked: bool,
    output_is_default: bool,
    blocker: str,
    write_custom: bool,
) -> int:
    """Invalidate the implicit projection and emit a total failure result."""
    blocked_default = _blocked_impact_plan(
        args,
        task_dir=task_dir,
        phase=phase,
        base_revision=base_revision,
        output_path=default_output_path,
        blocker=blocker,
    )
    try:
        atomic_write_text(
            default_output_path,
            json.dumps(blocked_default, ensure_ascii=False, indent=2) + "\n",
        )
        current_manifest.sync_manifest(task_dir)
    except (OSError, RuntimeError, ValueError) as exc:
        blocked_default["blockers"] = list(blocked_default.get("blockers") or []) + [
            f"failed to invalidate default impact cache: {exc}"
        ]
    blocked_output = blocked_default
    if write_custom and output_path_checked and not output_is_default:
        blocked_output = dict(blocked_default)
        blocked_output["derivedOutputPath"] = str(output_path)
        try:
            atomic_write_text(
                output_path,
                json.dumps(blocked_output, ensure_ascii=False, indent=2) + "\n",
            )
        except (OSError, RuntimeError, ValueError) as exc:
            blocked_output["blockers"] = list(blocked_output.get("blockers") or []) + [
                f"impact output write failed: {exc}"
            ]
    return emit_result({
        **blocked_output,
        "fallbackCommand": "long_task.py reopen-phase",
    }, args, exit_code=2)


def command_impact(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "wk", "impact")
    task_dir = Path(args.task_dir)
    state = long_task.runtime_state(task_dir)
    phase = str(args.phase or state.get("current_phase") or "")
    if not phase:
        raise WKError("impact analysis requires a current or explicit phase")
    artifact = ((state.get("artifact_revisions") or {}).get(phase) or {})
    base_revision = str(args.base_revision or artifact.get("revision") or "")
    output_path = Path(args.output) if args.output else task_dir / "cache" / "current_impact.json"
    default_output_path = task_dir / "cache" / "current_impact.json"
    output_is_default = False
    output_path_checked = False
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_is_default = output_path.resolve() == default_output_path.resolve()
        output_path_checked = True
        if not str(args.revision or "").strip():
            raise ValueError("revision is required")
        if not str(args.change_class or "").strip():
            raise ValueError("change class is required")
        adapter = impact_analysis.load_adapter_result(Path(args.adapter_result)) if args.adapter_result else None
        payload = impact_analysis.build_plan(
            task_dir=task_dir, phase=phase, base_revision=base_revision, revision=args.revision,
            change_class=args.change_class, changed_scopes=args.changed_scope, changed_paths=args.changed_path,
            semantic_artifact_changed=args.semantic_artifact_changed, production_changed=args.production_changed,
            profile_ids=args.profile, required_capabilities=args.required_capability, adapter_result=adapter,
            project_root=Path(args.project_root) if args.project_root else None, version=str(args.version or ""),
            graph_path=Path(args.graph) if args.graph else None, changed_refs=args.changed_ref,
            affected_task_ids=args.affected_task_id,
            affected_trace_ids=args.affected_trace_id,
            affected_acceptance_ids=args.affected_acceptance_id,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        return _emit_blocked_impact(
            args,
            task_dir=task_dir,
            phase=phase,
            base_revision=base_revision,
            default_output_path=default_output_path,
            output_path=output_path,
            output_path_checked=output_path_checked,
            output_is_default=output_is_default,
            blocker=str(exc),
            write_custom=True,
        )
    payload["derivedOutputPath"] = str(output_path)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    default_payload = payload if output_is_default else dict(payload)
    default_payload["derivedOutputPath"] = str(default_output_path)
    try:
        atomic_write_text(
            default_output_path,
            json.dumps(default_payload, ensure_ascii=False, indent=2) + "\n",
        )
        current_manifest.sync_manifest(task_dir)
        if not output_is_default:
            atomic_write_text(output_path, text + "\n")
    except (OSError, RuntimeError, ValueError) as exc:
        return _emit_blocked_impact(
            args,
            task_dir=task_dir,
            phase=phase,
            base_revision=base_revision,
            default_output_path=default_output_path,
            output_path=output_path,
            output_path_checked=output_path_checked,
            output_is_default=output_is_default,
            blocker=f"impact output write failed: {exc}",
            write_custom=False,
        )
    print(text)
    return 2 if payload.get("status") == "BLOCKED" else 0


def command_reconcile(args: argparse.Namespace) -> int:
    task_dir = Path(args.task_dir)
    state = long_task.runtime_state(task_dir)
    phase = str(state.get("current_phase") or "")
    agent = agent_context.selected_agent(args)
    agent_context.authorize_task_context(
        agent, "wk", "reconcile", phase=phase, next_agent=str(state.get("next_agent") or ""),
        project_manager_agent=str(state.get("project_manager_agent") or ""),
    )
    decision = _decision_with_impact(lifecycle_decision.evaluate(task_dir, long_task), str(getattr(args, "impact_plan", "") or ""))
    result: dict[str, Any] = {"status": "PREVIEW", "apply": bool(args.apply), "decision": decision}
    if args.apply:
        if decision.get("classification") == "PUBLISH_READY":
            candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
            attempt_id = str(candidate.get("attemptId") or "")
            if not attempt_id:
                raise WKError("reconcile has no deterministic PASSED attempt to publish")
            sink = io.StringIO()
            with contextlib.redirect_stdout(sink):
                long_task.command_publish_artifact(argparse.Namespace(
                    task_dir=str(task_dir), attempt_id=attempt_id, evidence_id=[], open_risk=[], agent=agent,
                ))
            result["status"] = "APPLIED"
            result["appliedAction"] = "publish-artifact"
            result["decision"] = _decision_with_impact(lifecycle_decision.evaluate(task_dir, long_task), str(getattr(args, "impact_plan", "") or ""))
        else:
            result["status"] = "NO_SAFE_MUTATION"
            result["reasonCodes"] = ["NO_DETERMINISTIC_APPLY"]
    return emit_result(result, args)


def _decision_with_delivery(task_dir: Path, decision: dict[str, Any], results_path: str = "") -> dict[str, Any]:
    phase = str((decision.get("phase") or decision.get("currentPhase") or long_task.runtime_state(task_dir).get("current_phase") or ""))
    if phase != "completion_verification":
        return decision
    try:
        doc_root = task_dir.parents[3]
        project_root = doc_root.parent
        version = task_dir.parents[1].name
    except IndexError:
        return decision
    plan = delivery_readiness.plan(project_root, version=version)
    results = json.loads(Path(results_path).read_text(encoding="utf-8")) if results_path else {}
    evaluation = delivery_readiness.evaluate(plan, results)
    result = dict(decision)
    result["deliveryReadiness"] = evaluation
    if evaluation.get("status") == "BLOCKED":
        result.update({
            "classification": "BLOCKED",
            "currentState": "DELIVERY_READINESS_BLOCKED",
            "preferredAction": "verify-delivery-readiness",
            "legalActions": ["wk.delivery", "execution_evidence.run"],
        })
        result["blockers"] = list(dict.fromkeys(list(result.get("blockers") or []) + list(evaluation.get("blockers") or [])))
    return result


def command_close(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "wk", "close")
    task_dir = Path(args.task_dir)
    decision = _decision_with_impact(lifecycle_decision.evaluate(task_dir, long_task), str(getattr(args, "impact_plan", "") or ""))
    decision = _decision_with_delivery(task_dir, decision, str(getattr(args, "delivery_results", "") or ""))
    result: dict[str, Any] = {"status": "PREVIEW", "apply": bool(args.apply), "decision": decision}
    if decision.get("classification") != "CLOSURE_ONLY":
        result["status"] = "BLOCKED"
        return emit_result(result, args, exit_code=2)
    mode = work_mode.validate_work_file(long_task.version_root_from_task_dir(task_dir) / "work.md")
    mutation = work_mode.mutation_decision(mode, str(decision.get("currentPhase") or ""), "taskFacts")
    result["mutationPolicy"] = mutation
    if not mutation["allowed"]:
        raise WKError("mutation policy blocks taskFacts required for phase closure")
    if args.apply:
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink):
            long_task.command_finalize_phase(argparse.Namespace(task_dir=str(task_dir), agent=agent))
        result["status"] = "APPLIED"
        result["finalizeResult"] = json.loads(sink.getvalue()) if sink.getvalue().strip().startswith("{") else sink.getvalue().strip()
        result["decision"] = _decision_with_impact(lifecycle_decision.evaluate(task_dir, long_task), str(getattr(args, "impact_plan", "") or ""))
    return emit_result(result, args)


def command_document_archive(args: argparse.Namespace) -> int:
    """Finalize lightweight-mode documents from confirmed facts and implementation evidence."""
    agent = agent_context.authorize_command(args, "wk", "document-archive")
    work_path = work_mode.work_path(Path(args.doc_root), str(args.version))
    mode = work_mode.validate_work_file(work_path)
    historical = merge_docs.historical_lightweight_state(Path(args.doc_root), str(args.version))
    if not mode.lightweight and not historical["waived"]:
        raise WKError("wk -wd requires active or previously closed lightweight mode; enable it with work_mode.py apply -ws first")
    preview_args = argparse.Namespace(
        doc_root=args.doc_root, module=args.module, version=args.version,
        changeset=args.changeset, agent=agent,
    )
    plan_path = merge_docs.generated_root(Path(args.doc_root), args.version, args.module) / "merge_plan.yaml"
    if not args.apply:
        merge_docs.command_preview(preview_args)
    payload: dict[str, Any] = {
        "status": "PREVIEW",
        "mode": "LIGHTWEIGHT" if mode.lightweight else "LIGHTWEIGHT_CLOSED",
        "mergePlan": str(plan_path),
    }
    if args.apply:
        archive_args = argparse.Namespace(
            doc_root=args.doc_root, module=args.module, version=args.version,
            changeset=args.changeset, agent="IncrementalArchiveAgent", require_user_confirmation=False,
            audit_bundle=getattr(args, "audit_bundle", False),
        )
        payload["archiveStatus"] = merge_docs.command_archive(archive_args)
        payload["status"] = "APPLIED"
    return emit_result(payload, args)
    return 0




def command_delivery(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "wk", "delivery")
    if args.action == "plan":
        payload = delivery_readiness.plan(Path(args.project_root), version=str(args.version or ""))
        if args.output:
            atomic_write_text(Path(args.output), json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    else:
        plan_value = require_json_object(load_json_input(args.plan, context="delivery --plan"), context="delivery --plan")
        results = {}
        if args.results:
            results = require_json_object(load_json_input(args.results, context="delivery --results"), context="delivery --results")
        payload = delivery_readiness.evaluate(plan_value, results)
    return emit_result(payload, args, exit_code=2 if payload.get("status") == "BLOCKED" else 0)

def command_env(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "wk", "env")
    if bool(getattr(args, "check", False)):
        payload = project_environment.environment_health(Path(args.project_root), version=str(args.version or ""))
        if args.impact_adapter_result:
            adapter_check: dict[str, Any] = {"path": str(args.impact_adapter_result), "status": "PASSED"}
            try:
                adapter = impact_analysis.load_adapter_result(Path(args.impact_adapter_result))
                adapter_check["confidence"] = adapter.get("confidence") if adapter else "UNKNOWN"
            except impact_analysis.ImpactAnalysisError as exc:
                adapter_check.update({"status": "FAILED", "error": str(exc)})
                payload["errors"].append(str(exc))
                payload["status"] = "FAILED"
            payload["impactAdapter"] = adapter_check
        return emit_result(payload, args, exit_code=2 if payload.get("status") != "PASSED" else 0)
    payload = project_environment.project_capabilities(Path(args.project_root), version=str(args.version or ""))
    return emit_result(payload, args, exit_code=2 if payload.get("status") == "BLOCKED" else 0)


def _next_payload(
    task_dir: Path,
    payload: dict[str, Any],
    agent: str,
    *,
    explain: bool = False,
    impact_plan: str = "",
) -> dict[str, Any]:
    """Build the single read-only next-step projection used by ``next`` and ``status``."""
    auto_execution = payload.get("autoExecution") if isinstance(payload.get("autoExecution"), dict) else {}
    critical_issue_ids = payload.get("openCriticalIssueIds") or []
    if auto_execution.get("enabled") is True and critical_issue_ids:
        result = {
            "currentGate": "AUTO_REMEDIATION",
            "allowedCommands": ["auto_remediation.classify", "long_task.task-context"],
            "recommendedCommand": (
                f"python3 scripts/auto_remediation.py classify -g {agent} "
                f"--signal open_p0_or_p1 --changes-code-config-or-test true --task-dir {shlex.quote(str(task_dir))} --json"
            ),
            "requiredAgent": agent,
            "reasonCodes": ["AUTO_BATCH_FORWARD_FROZEN", "AUTO_REMEDIATION_REQUIRED"],
            "openCriticalIssueIds": critical_issue_ids,
        }
    elif payload.get("status") == "INVALID":
        result = {
            "currentGate": "TASK_VALIDATION", "allowedCommands": ["long_task.validate"],
            "recommendedCommand": f"python3 scripts/long_task.py validate -g {agent} --task-dir {shlex.quote(str(task_dir))} --json",
            "requiredAgent": agent, "reasonCodes": ["TASK_INVALID"], "errors": payload.get("errors") or [],
        }
    elif payload.get("status") == "BLOCKED":
        result = {
            "status": "BLOCKED", "currentGate": "REQUEST_INTAKE", "allowedCommands": ["request_intake.reclassify", "request_intake.classify"],
            "recommendedCommand": f"python3 scripts/request_intake.py reclassify -g ProjectManagerAgent --task-dir {shlex.quote(str(task_dir))} --classification requirement-change --reason {{REASON}}",
            "requiredAgent": "ProjectManagerAgent", "reasonCodes": ["REQUEST_OR_GATE_BLOCKED"],
        }
    else:
        state = long_task.runtime_state(task_dir)
        phase = str(state.get("current_phase") or "")
        ar = state.get("architecture_review") if isinstance(state.get("architecture_review"), dict) else {}
        if phase == "development" and ar.get("step") == "SKELETON_REVIEW_PASSED":
            result = {
                "currentGate": phase, "allowedCommands": ["long_task.advance-development-step"],
                "recommendedCommand": f"python3 scripts/long_task.py advance-development-step -g ProjectManagerAgent --task-dir {shlex.quote(str(task_dir))}",
                "requiredAgent": "ProjectManagerAgent", "reasonCodes": ["WK_AR_SKELETON_REVIEW_PASSED"],
            }
        else:
            decision = lifecycle_decision.evaluate(task_dir, long_task, validation=getattr(payload, "validation", None))
            effective_impact_path = str(impact_plan or "")
            if not effective_impact_path:
                derived = (payload.get("currentManifest") or {}).get("derivedImpact") if isinstance(payload.get("currentManifest"), dict) else {}
                if isinstance(derived, dict) and derived.get("fresh") and derived.get("path"):
                    effective_impact_path = str(derived.get("path"))
            if effective_impact_path:
                decision = _decision_with_impact(decision, effective_impact_path)
            result = _recommended_from_decision(decision, task_dir, agent)
            if explain:
                result["decision"] = decision
    # Keep the legacy field while making nextCommand the canonical consumer field.
    result["recommendedCommand"] = (
        result.get("recommendedCommand", "") if not isinstance(result.get("recommendedCommand"), dict)
        else result["recommendedCommand"].get("command", "")
    )
    result["nextCommand"] = _structured_next_command(result)
    return result


def command_next(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "wk", "next")
    task_dir = Path(args.task_dir)
    payload = _status_payload(task_dir, include_next=False)
    result = _safe_next_payload(
        task_dir, payload, agent,
        explain=bool(getattr(args, "explain", False)),
        impact_plan=str(getattr(args, "impact_plan", "") or ""),
    )
    return emit_result(result, args)


def _authorize_orchestration(args: argparse.Namespace, action: str) -> str:
    agent = agent_context.selected_agent(args)
    if agent_context.command_permission("wk", action).get("mode") != "TASK_CONTEXT":
        return agent_context.authorize_command(args, "wk", action)
    state = long_task.runtime_state(Path(args.task_dir))
    agent_context.authorize_task_context(agent, "wk", action,
        phase=str(state.get("current_phase") or ""), next_agent=str(state.get("next_agent") or ""),
        project_manager_agent=str(state.get("project_manager_agent") or ""))
    return agent


def _risk_observations(args: argparse.Namespace) -> dict[str, Any]:
    raw = str(getattr(args, "risk_facts", "") or "")
    def invalid_constant(value: str) -> None:
        raise ValueError(f"invalid JSON constant: {value}")
    try:
        value = json.loads(raw, parse_constant=invalid_constant) if raw else {}
    except (ValueError, RecursionError) as exc:
        raise WKError(f"--risk-facts 必须为有效 JSON: {exc}", code="RISK_FACTS_INVALID") from exc
    error = observation_error(value)
    if error:
        raise WKError(f"--risk-facts: {error}", code="RISK_FACTS_INVALID")
    return value


def _package(args: argparse.Namespace, action: str, *, write_cache: bool = False) -> dict[str, Any]:
    agent = _authorize_orchestration(args, action)
    return execution_package(Path(args.task_dir).resolve(), agent=agent, phase=action,
        status_reader=lambda path: _status_payload(path, include_next=False), next_reader=_safe_next_payload,
        write_cache=write_cache, risk_facts=_risk_observations(args))


def _ai_execution_package(args: argparse.Namespace, *, phase: str) -> int:
    result = _package(args, phase)
    return emit_result(result, args, exit_code=2 if result.get("status") == "BLOCKED" else 0)


def _capture_command(command: Any, args: argparse.Namespace) -> tuple[int, Any]:
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        code = command(args)
    output = sink.getvalue().strip()
    return int(code), json.loads(output) if output else None


def command_begin(args: argparse.Namespace) -> int:
    agent = _authorize_orchestration(args, "begin")
    _risk_observations(args)
    intake_path = Path(args.task_dir) / "request_intake.json"
    if intake_path.is_file():
        try:
            request_intake.load(intake_path)
        except request_intake.RequestIntakeError as exc:
            return emit_result({"kind": "AI_EXECUTION_PACKAGE", "action": "begin", "status": "BLOCKED",
                "reasonCode": "REQUEST_INTAKE_INVALID", "message": str(exc),
                "requiredAgent": agent, "requiredInputs": ["request_intake.json"],
                "blockingReasons": [str(exc)], "actionPlan": [],
                "executionContext": "CURRENT_SESSION_ALLOWED", "requiresUserDecision": False}, args, exit_code=2)
    if not args.apply:
        return _ai_execution_package(args, phase="begin")
    task_dir = Path(args.task_dir)
    with task_writer_lock(task_dir):
        tasks = long_task.runtime_tasks(task_dir)
        state = long_task.runtime_state(task_dir)
        selected_id = str(getattr(args, "task_id", "") or "")
        candidates = [t for t in tasks if t.get("phase") == state.get("current_phase")
                      and (not selected_id or t.get("id") == selected_id)
                      and t.get("status") in {"RUNNING", "PENDING", "READY", "REWORK", "FAILED"}]
        owned = [t for t in candidates if t.get("owner_agent") == agent]
        selected = next((t for t in owned if t.get("status") == "RUNNING"), None)
        if selected is None and owned:
            selected = owned[0]
        if not selected:
            result = _package(args, "begin")
            result.update(status="BLOCKED", reasonCode="BEGIN_TASK_MISSING",
                message="当前 Agent 没有可启动或恢复的阶段任务，请按 owner/actionPlan 继续。")
            return emit_result(result, args, exit_code=2)
        context = build_task_context(task_dir, selected["id"])
        attempt = context.get("currentAttempt")
        resumed = bool(attempt)
        if not attempt:
            ns = argparse.Namespace(task_dir=str(task_dir), task_id=selected["id"], agent=agent,
                input_revision="", summary="AI orchestration begin", next_action="continue with wk progress")
            code, attempt = _capture_command(long_task.command_start_attempt, ns)
            if code:
                return emit_result({"status": "BLOCKED", "result": attempt}, args, exit_code=code)
        result = _package(args, "begin", write_cache=True)
        result.update(attempt=attempt, taskId=selected["id"], resumed=resumed)
        # ACTIVE_ATTEMPT blocks phase advancement, not owner execution/resumption.
        if result.get("reasonCodes") == [reason_codes.ACTIVE_ATTEMPT]:
            result.update(status="READY", message="继续当前任务，完成实现和声明的验证后登记结果。")
        return emit_result(result, args, exit_code=2 if result.get("status") == "BLOCKED" else 0)


def command_progress(args: argparse.Namespace) -> int:
    agent = _authorize_orchestration(args, "progress")
    _risk_observations(args)
    if not args.apply:
        return _ai_execution_package(args, phase="progress")
    if not args.plan_digest:
        raise WKError("progress --apply requires planDigest from begin, next or progress", code="PROGRESS_PLAN_DIGEST_REQUIRED")
    ns = argparse.Namespace(task_dir=args.task_dir, agent=agent, apply=True, preview=False,
        plan_digest=args.plan_digest, json=True, impact_plan="", _single_action=True)
    with task_writer_lock(Path(args.task_dir)):
        code, applied = _capture_command(command_step, ns)
        result = _package(args, "progress", write_cache=True)
    result["appliedResult"] = applied
    result["mutationsApplied"] = bool(applied and applied.get("mutationsApplied"))
    if code:
        result.update(status="PARTIAL" if result["mutationsApplied"] else "BLOCKED",
            reasonCode="PROGRESS_ACTION_INCOMPLETE", message="原子动作未完成，请读取当前修复计划。")
    return emit_result(result, args, exit_code=code)


def _finish_validation_cwd(args: argparse.Namespace) -> Path:
    """Bind relative validation commands to the project containing the task."""
    try:
        doc_root, _ = doc_layout.doc_root_from_task_dir(args.task_dir)
        project_root = doc_root.parent.resolve()
        requested = Path(getattr(args, "cwd", "") or ".").expanduser()
        cwd = (project_root / requested).resolve(strict=True)
        cwd.relative_to(project_root)
        if not cwd.is_dir():
            raise ValueError("validation cwd must be a directory")
        return cwd
    except (ValueError, OSError, RuntimeError) as exc:
        raise WKError("验证目录必须存在且位于任务所属项目内；--cwd 相对于项目根目录。",
                      code="FINISH_CWD_INVALID") from exc


def command_finish(args: argparse.Namespace) -> int:
    _risk_observations(args)
    validation_cwd = _finish_validation_cwd(args)
    if not args.apply:
        result = _package(args, "finish")
        result["status"] = ("BLOCKED" if result.get("status") == "BLOCKED" else
                            "DONE" if result_classification(result) == "COMPLETED" else "PARTIAL")
        result["validationExecuted"] = False
        result["validationCwd"] = str(validation_cwd)
        return emit_result(result, args, exit_code=2 if result["status"] == "BLOCKED" else 0)
    with task_writer_lock(Path(args.task_dir)):
        return _command_finish_locked(args)


def _command_finish_locked(args: argparse.Namespace) -> int:
    agent = _authorize_orchestration(args, "finish")
    _risk_observations(args)
    validation_cwd = _finish_validation_cwd(args)
    from runtime import task_verify
    task_dir = Path(args.task_dir)
    state = long_task.runtime_state(task_dir)
    phase = str(state.get("current_phase") or "")
    tasks = [t for t in long_task.runtime_tasks(task_dir) if t.get("phase") == phase
             and (not getattr(args, "task_id", "") or t.get("id") == args.task_id)]
    if getattr(args, "task_id", "") and not tasks:
        raise WKError("当前阶段不存在指定 task ID", code="FINISH_TASK_NOT_FOUND")
    results = []
    for task in tasks:
        commands = list(task.get("validation_commands") or [])
        if not commands:
            continue
        context = build_task_context(task_dir, task["id"])
        attempt = context.get("currentAttempt") or {}
        revision = str(getattr(args, "revision", "") or task.get("output_revision") or
                       ((state.get("artifact_revisions") or {}).get(phase) or {}).get("revision") or "")
        if not revision:
            results.append({"status": "BLOCKED", "reasonCode": "FINISH_REVISION_REQUIRED",
                "taskId": task["id"], "message": "验证前需要当前候选 revision"})
            continue
        canonical_revision = task.get("output_revision") or ((state.get("artifact_revisions") or {}).get(phase) or {}).get("revision")
        if canonical_revision and revision != canonical_revision:
            results.append({"status": "BLOCKED", "reasonCode": "CURRENT_REVISION_EVIDENCE_STALE", "taskId": task["id"]})
            continue
        if task.get("owner_agent") != agent:
            results.append({"status": "BLOCKED", "reasonCode": "FINISH_OWNER_REQUIRED", "taskId": task["id"]})
            continue
        try:
            for evidence_id in attempt.get("command_evidence_ids") or []:
                evidence.require_evidence(task_dir, evidence_id, expected_revision=revision, expected_phase=phase)
        except ValueError as exc:
            results.append({"status": "BLOCKED", "reasonCode": "CURRENT_REVISION_EVIDENCE_STALE", "error": error_record(exc)})
            continue
        for command in commands:
            try:
                outcome = execution_evidence.run_and_register(task_dir, agent=agent, command=command,
                    revision=revision, phase=phase, cwd=validation_cwd, mutation_scope="taskFacts", timeout=300,
                    scope_refs=[task["id"], command], attempt_id=str(attempt.get("attempt_id") or ""),
                    declared_commands=commands)
                if attempt:
                    path = task_dir / outcome["commandResultRef"]
                    outcome["registration"] = record_result(task_dir, agent=agent,
                        attempt_id=attempt["attempt_id"], command_id=path.stem, result_file=path,
                        revision=revision, phase=phase, scope_refs=[task["id"], command])
                results.append(outcome)
            except (ValueError, OSError) as exc:
                results.append({"status": "BLOCKED", "command": command, "error": error_record(exc)})
    verification = task_verify.run_verification(task_dir, "task-health", evidence_scope="current")
    decision = _package(args, "finish", write_cache=True)
    failed = any(item.get("status") != "PASSED" for item in results) or verification.get("overall") != "PASSED"
    cls = result_classification(decision)
    status = "BLOCKED" if failed or cls == "BLOCKED" else ("DONE" if cls == "COMPLETED" else "PARTIAL")
    result = project_result(decision, action="finish")
    if failed:
        first_failure = next((r for r in results if r.get("status") != "PASSED"), {})
        result["reasonCode"] = first_failure.get("reasonCode") or first_failure.get("errorCode") or "FINISH_VALIDATION_FAILED"
        if result["reasonCode"] == "FINISH_REVISION_REQUIRED":
            result["requiredInputs"] = list(dict.fromkeys([*(result.get("requiredInputs") or []), "revision"]))
    result.update(kind="AI_EXECUTION_PACKAGE", status=status, validationResults=results, verification=verification,
        validationCwd=str(validation_cwd),
        message={"DONE": "当前任务已通过完成验证。", "PARTIAL": "验证已汇总，仍需完成剩余阶段动作。",
                 "BLOCKED": "当前任务尚有未满足的验证或阶段条件。"}[status])
    return emit_result(result, args, exit_code=2 if status == "BLOCKED" else 0)


def command_record_result(args: argparse.Namespace) -> int:
    """Register one command result under the task writer lock; lifecycle remains canonical."""
    agent = _authorize_orchestration(args, "record-result")
    task_dir = Path(args.task_dir)
    result_path = Path(args.result_file).resolve()
    if not result_path.is_file():
        raise WKError(f"result file does not exist: {result_path}", code="RESULT_FILE_MISSING")
    return emit_result(record_result(task_dir, agent=agent, attempt_id=args.attempt_id, command_id=args.command_id, result_file=result_path, revision=args.revision, phase=args.phase, scope_refs=list(args.scope_ref), acceptance_id=str(getattr(args, "acceptance_id", "") or "")), args)



def _step_agent_for_action(action: str, agent: str, decision: dict[str, Any]) -> str:
    """Resolve the internal Agent for one restricted atomic action.

    ``wk.step`` itself is a ProjectManager command, but artifact publication is
    an owner-scoped lifecycle operation.  Resolve that one identity from the
    fresh canonical phase definition; never accept an alternate Agent from the
    caller or blindly impersonate ``requiredAgent`` for every action.
    """
    if action != "long_task.publish-artifact":
        return agent
    phase = str(decision.get("currentPhase") or "")
    if not phase:
        raise WKError(
            "当前发布计划缺少 canonical phase，不能解析阶段 Owner",
            code="STEP_OWNER_RESOLUTION_FAILED",
            repair_command="请重新执行 wk step --preview 并检查当前任务状态",
        )
    try:
        definition = agent_context.phase_definition(phase)
    except (KeyError, ValueError) as exc:
        raise WKError(
            f"当前发布计划包含未知 canonical phase：{phase}",
            code="STEP_OWNER_RESOLUTION_FAILED",
            repair_command="请重新执行 wk step --preview 并检查当前任务状态",
        ) from exc
    owner = definition.get("ownerAgent") if isinstance(definition, dict) else None
    if not isinstance(owner, str) or not owner:
        raise WKError(
            f"canonical phase {phase} 未声明 artifact Owner",
            code="STEP_OWNER_RESOLUTION_FAILED",
            repair_command="请重新执行 wk step --preview 并检查 Runtime Contract",
        )
    planned_owner = str(decision.get("requiredAgent") or "")
    if planned_owner and planned_owner != owner:
        raise WKError(
            f"发布计划的 requiredAgent 与 canonical phase Owner 不一致：{planned_owner} != {owner}",
            code="STEP_OWNER_RESOLUTION_FAILED",
            repair_command="请重新执行 wk step --preview；若仍失败请运行 wk doctor",
        )
    return owner


def _step_atomic_args(action: str, task_dir: Path, agent: str, decision: dict[str, Any]) -> argparse.Namespace:
    """Build arguments only for the closed-world UX-6 atomic actions."""
    common = {"task_dir": str(task_dir), "agent": agent}
    if action == "acceptance.ensure-review":
        candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
        return argparse.Namespace(
            **common,
            phase=str(decision.get("currentPhase") or ""),
            revision=str(candidate.get("revision") or ""),
        )
    if action == "long_task.publish-artifact":
        candidate = decision.get("candidate") if isinstance(decision.get("candidate"), dict) else {}
        attempt_id = str(candidate.get("attemptId") or "")
        if not attempt_id:
            raise WKError(
                "当前计划缺少可确定的 PASSED attempt，不能自动发布",
                code="STEP_INPUT_REQUIRED",
                repair_command=f"python3 scripts/long_task.py task-context -g {agent} --task-dir {shlex.quote(str(task_dir))} --task-id {{TASK_ID}}",
            )
        return argparse.Namespace(**common, attempt_id=attempt_id, evidence_id=[], open_risk=[])
    if action == "long_task.finalize-phase":
        return argparse.Namespace(**common)
    if action == "long_task.advance-phase":
        return argparse.Namespace(**common)
    if action == "wk.reconcile":
        return argparse.Namespace(**common, apply=True, impact_plan="")
    raise WKError("计划动作不在 UX-6 受限白名单中", code="STEP_ACTION_NOT_ALLOWED")


def _step_run_atomic(action: str, atomic_args: argparse.Namespace) -> tuple[int, Any]:
    """Invoke an existing command, preserving its own authorization and gates."""
    commands = {
        "acceptance.ensure-review": acceptance.command_ensure_review,
        "long_task.publish-artifact": long_task.command_publish_artifact,
        "long_task.finalize-phase": long_task.command_finalize_phase,
        "long_task.advance-phase": long_task.command_advance_phase,
        "wk.reconcile": command_reconcile,
    }
    command = commands.get(action)
    if command is None:
        raise WKError("计划动作不在 UX-6 受限白名单中", code="STEP_ACTION_NOT_ALLOWED")
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink):
        rc = int(command(atomic_args))
    text = sink.getvalue().strip()
    if not text:
        return rc, None
    try:
        return rc, json.loads(text)
    except json.JSONDecodeError:
        return rc, text


def _step_repair_command(action: str, task_dir: Path, agent: str) -> str:
    if action == "wk.reconcile":
        return f"python3 scripts/wk.py reconcile -g {agent} --task-dir {shlex.quote(str(task_dir))} --apply"
    if action == "acceptance.ensure-review":
        return f"python3 {acceptance.__file__} ensure-review -g ProjectManagerAgent --task-dir {shlex.quote(str(task_dir))}"
    if action == "long_task.publish-artifact":
        return f"python3 scripts/long_task.py publish-artifact -g {agent} --task-dir {shlex.quote(str(task_dir))} --attempt-id {{ATTEMPT_ID}}"
    return f"python3 scripts/long_task.py {action.rsplit('.', 1)[-1]} -g {agent} --task-dir {shlex.quote(str(task_dir))}"


def _step_resume_token(plan_digest: str, failed_index: int) -> str:
    value = f"{plan_digest}:{failed_index}".encode("utf-8")
    return "sha256:" + hashlib.sha256(value).hexdigest()


def command_step(args: argparse.Namespace) -> int:
    """Preview or apply a closed-world sequence of existing atomic commands."""
    agent = agent_context.authorize_command(args, "wk", "step")
    task_dir = Path(args.task_dir).resolve()
    is_apply = bool(getattr(args, "apply", False))
    if not is_apply and not bool(getattr(args, "preview", False)):
        raise WKError(
            "必须指定 --preview 或 --apply",
            code="STEP_MODE_REQUIRED",
        )
    if is_apply and not str(getattr(args, "plan_digest", "") or "").strip():
        # Keep the old marker in the detail for callers upgrading from UX-5;
        # the machine code is the new, actionable UX-6 contract.
        raise WKError(
            "--apply 必须提供来自 --preview 的 --plan-digest（STEP_PREVIEW_REQUIRED：请先执行 preview）",
            code="STEP_PLAN_DIGEST_REQUIRED",
            repair_command=f"python3 scripts/wk.py step -g {agent} --task-dir {shlex.quote(str(task_dir))} --preview --json",
        )

    validation = long_task.validate_task(task_dir)
    decision = lifecycle_decision.evaluate(task_dir, long_task, validation=validation)
    payload = step_plan.build_preview(
        task_dir, runtime=long_task, validation=validation, decision=decision,
    )
    payload["agent"] = agent
    if not is_apply:
        return emit_result(payload, args, exit_code=0 if payload["status"] == "READY" else 1)

    supplied_digest = str(args.plan_digest).strip()
    if supplied_digest != str(payload.get("planDigest") or ""):
        raise WKError(
            "计划已过期，当前事实与 --plan-digest 不一致；请重新 preview",
            code="STEP_PLAN_STALE",
            repair_command=f"python3 scripts/wk.py step -g {agent} --task-dir {shlex.quote(str(task_dir))} --preview --json",
        )
    if payload.get("status") != "READY":
        payload.update({
            "status": "NO_SAFE_MUTATION",
            "readOnly": False,
            "mutationsApplied": False,
            "authorizationRecorded": False,
            "reason": "当前生命周期计划被阻断，未执行任何动作",
        })
        return emit_result(payload, args, exit_code=2)

    planned = step_plan.apply_sequence(payload)
    if getattr(args, "_single_action", False):
        planned = planned[:1]
    if not planned:
        payload.update({
            "status": "NO_SAFE_MUTATION",
            "readOnly": False,
            "mutationsApplied": False,
            "authorizationRecorded": False,
            "reason": "当前计划没有可自动执行的受限原子动作；需要人工输入、Review、Evidence 或确认",
            "nextCommand": _recommended_from_decision(decision, task_dir, agent).get("nextCommand", {}),
        })
        return emit_result(payload, args, exit_code=2)

    completed: list[dict[str, Any]] = []
    for index, step in enumerate(planned):
        action = str(step.get("permissionCommandId") or "")
        if action not in step_plan.APPLYABLE_ACTIONS or not step.get("autoApplyAllowed"):
            error = {"code": "STEP_ACTION_NOT_ALLOWED", "message": "动作不在受限编排白名单中"}
            result = {
                "status": "PARTIAL", "taskDir": str(task_dir.resolve()),
                "planVersion": payload.get("planVersion"), "planDigest": supplied_digest,
                "completedSteps": completed, "failedStep": {**step, "status": "FAILED", "error": error},
                "repairCommand": _step_repair_command(action, task_dir, agent),
                "resumeToken": _step_resume_token(supplied_digest, index),
                "mutationsApplied": bool(completed), "authorizationRecorded": False,
            }
            return emit_result(result, args, exit_code=2)
        step_agent = agent
        try:
            step_agent = _step_agent_for_action(action, agent, decision)
            atomic_args = _step_atomic_args(action, task_dir, step_agent, decision)
            rc, atomic_result = _step_run_atomic(action, atomic_args)
            if rc != 0:
                raise WKError(
                    f"原子动作返回非零状态：{action} rc={rc}",
                    code="STEP_ATOMIC_COMMAND_FAILED",
                    repair_command=_step_repair_command(action, task_dir, step_agent),
                )
            if isinstance(atomic_result, dict) and atomic_result.get("status") in {
                "NO_SAFE_MUTATION", "BLOCKED", "FAILED", "PREVIEW"
            }:
                raise WKError(
                    f"原子动作未产生允许的应用结果：{action} status={atomic_result.get('status')}",
                    code="STEP_ATOMIC_COMMAND_FAILED",
                    repair_command=_step_repair_command(action, task_dir, step_agent),
                )
            completed.append({**step, "status": "PASSED", "result": atomic_result})
            # The next atomic command is allowed only if the existing command
            # succeeded.  Its own gate re-reads the authoritative reducer.
        except (SkillError, ValueError, OSError) as exc:
            record = error_record(exc, default_code="STEP_ATOMIC_COMMAND_FAILED")
            failed = {**step, "status": "FAILED", "error": record}
            result = {
                "status": "PARTIAL", "taskDir": str(task_dir.resolve()),
                "planVersion": payload.get("planVersion"), "planDigest": supplied_digest,
                "completedSteps": completed, "failedStep": failed,
                "repairCommand": record.get("repairCommand") or _step_repair_command(action, task_dir, step_agent),
                "resumeToken": _step_resume_token(supplied_digest, index),
                "mutationsApplied": bool(completed), "authorizationRecorded": False,
            }
            return emit_result(result, args, exit_code=2)

    refreshed_validation = long_task.validate_task(task_dir)
    refreshed_decision = lifecycle_decision.evaluate(task_dir, long_task, validation=refreshed_validation)
    result = {
        "status": "APPLIED",
        "taskDir": str(task_dir.resolve()),
        "planVersion": payload.get("planVersion"),
        "planDigest": supplied_digest,
        "completedSteps": completed,
        "steps": completed,
        "mutationsApplied": True,
        "authorizationRecorded": False,
        "decision": refreshed_decision,
        "nextCommand": _recommended_from_decision(refreshed_decision, task_dir, agent).get("nextCommand", {}),
    }
    return emit_result(result, args)

def _confirmation_phase(value: str) -> str:
    """Resolve a canonical phase or its archive-gate camelCase alias."""
    candidate = str(value or "").strip()
    if not candidate:
        raise WKError("--until must not be empty", code="CONFIRM_PHASE_INVALID")
    if candidate in long_task.PHASES:
        return candidate
    gate_map = agent_context.RUNTIME_CONTRACT.get("archiveGateMap", {})
    if isinstance(gate_map, dict):
        for phase, gate in gate_map.items():
            if candidate == gate and phase in long_task.PHASES:
                return str(phase)
    raise WKError(f"unknown phase or archive gate: {candidate}", code="CONFIRM_PHASE_INVALID")


def _authorization_event_spec(
    task_dir: Path,
    from_phase: str,
    until_phase: str,
    reason: str,
    agent: str,
    actions: list[dict[str, Any]],
    *,
    state: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    context = authorization.phase_context(state, from_phase)
    fingerprint_input = {
        "taskPath": authorization.task_identity.portable_task_path(task_dir),
        "fromPhase": from_phase,
        "untilPhase": until_phase,
        "taskId": context["taskId"],
        "iterationId": context["iterationId"],
        "revision": context["revision"],
        "reason": reason,
        "agent": agent,
        "atomicActions": actions,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fingerprint_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return ({
        "type": "AUTHORIZATION_BATCH_CONFIRMED",
        "entity_id": str(task_dir.name),
        "timestamp": now(),
        "source": "wk.confirm",
        "payload": {
            "taskPath": authorization.task_identity.portable_task_path(task_dir),
            "fromPhase": from_phase,
            "untilPhase": until_phase,
            "taskId": context["taskId"],
            "iterationId": context["iterationId"],
            "revision": context["revision"],
            "reason": reason,
            "agent": agent,
            "mode": "apply",
            "atomicActions": actions,
            "requestFingerprint": fingerprint,
        },
    }, fingerprint)


def _reopen_authorization_event_spec(
    task_dir: Path,
    *,
    changed_phase: str,
    from_phase: str,
    source_revision: str,
    reason: str,
    agent: str,
    state: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    changed = ((state.get("artifact_revisions") or {}).get(changed_phase) or {})
    payload = {
        "taskPath": authorization.task_identity.portable_task_path(task_dir),
        "taskId": str(state.get("target_id") or task_dir.name),
        "changedPhase": changed_phase,
        "fromPhase": from_phase,
        "sourceRevision": source_revision,
        "changedIterationId": str(changed.get("iteration_id") or ""),
        "changedRevision": str(changed.get("revision") or ""),
        "reason": reason,
        "agent": agent,
        "mode": "reopen",
        "atomicActions": [{"fromPhase": from_phase, "changedPhase": changed_phase}],
    }
    fingerprint = authorization.reopen_boundary_fingerprint(payload)
    payload["requestFingerprint"] = fingerprint
    return ({
        "type": "AUTHORIZATION_BATCH_CONFIRMED",
        "entity_id": str(task_dir.name),
        "timestamp": now(),
        "source": "wk.confirm",
        "payload": payload,
    }, fingerprint)


def _standard_skip_authorization_event_spec(
    task_dir: Path,
    *,
    phase: str,
    reason: str,
    agent: str,
    state: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    artifact = ((state.get("artifact_revisions") or {}).get(phase) or {})
    payload = {
        "taskPath": authorization.task_identity.portable_task_path(task_dir),
        "taskId": str(state.get("target_id") or task_dir.name),
        "phase": phase,
        "iterationId": str(artifact.get("iteration_id") or ""),
        "iterationNo": artifact.get("iteration_no"),
        "revision": str(artifact.get("revision") or ""),
        "reason": reason,
        "agent": agent,
        "mode": "standard-skip",
        "atomicActions": [{"phase": phase, "action": "SKIP"}],
    }
    fingerprint = authorization.standard_skip_fingerprint(payload)
    payload["requestFingerprint"] = fingerprint
    return ({
        "type": "AUTHORIZATION_BATCH_CONFIRMED",
        "entity_id": str(task_dir.name),
        "timestamp": now(),
        "source": "wk.confirm",
        "payload": payload,
    }, fingerprint)


def _command_confirm_unlocked(args: argparse.Namespace) -> int:
    """Preview or record a bounded authorization batch without executing gates."""
    agent = agent_context.authorize_command(args, "wk", "confirm")
    task_dir = Path(args.task_dir)
    reason = str(args.reason or "").strip()
    if not reason:
        raise WKError("--reason must not be empty", code="CONFIRM_REASON_REQUIRED")
    state = long_task.runtime_state(task_dir)
    current = str(state.get("current_phase") or "")
    if current not in long_task.PHASES:
        raise WKError(f"current phase is invalid: {current}", code="CONFIRM_PHASE_INVALID")
    current_index = long_task.PHASES.index(current)
    skip_phase = str(getattr(args, "skip_phase", "") or "").strip()
    if skip_phase:
        mode = state.get("work_mode") if isinstance(state.get("work_mode"), dict) else {}
        if mode.get("minimal") or mode.get("lightweight"):
            raise WKError("--skip-phase confirmation is only valid in standard mode", code="CONFIRM_SKIP_MODE_INVALID")
        if skip_phase not in long_task.STANDARD_USER_SKIPPABLE_PHASES:
            raise WKError("--skip-phase is not allowed by standardUserSkipPolicy", code="CONFIRM_PHASE_INVALID")
        artifact = ((state.get("artifact_revisions") or {}).get(skip_phase) or {})
        if artifact.get("status") not in {"PENDING", "STALE", "IN_PROGRESS", "BLOCKED"}:
            raise WKError(
                f"cannot confirm a skip for {skip_phase} from status {artifact.get('status')}",
                code="CONFIRM_SKIP_STATUS_INVALID",
            )
        event_spec, fingerprint = _standard_skip_authorization_event_spec(
            task_dir, phase=skip_phase, reason=reason, agent=agent, state=state,
        )
        existing_fingerprints = {
            str((event.get("payload") or {}).get("requestFingerprint") or "")
            for event in task_events.read_events(task_dir)
            if event.get("type") == "AUTHORIZATION_BATCH_CONFIRMED"
            and isinstance(event.get("payload"), dict)
        }
        result = {
            "status": "NO_OP" if fingerprint in existing_fingerprints else (
                "CONFIRMED" if bool(getattr(args, "apply", False)) else "PREVIEW"
            ),
            "taskDir": str(task_dir), "authorizationMode": "standard-skip",
            "phase": skip_phase, "reason": reason,
            "eventType": "AUTHORIZATION_BATCH_CONFIRMED", "requestFingerprint": fingerprint,
            "alreadyConfirmed": fingerprint in existing_fingerprints, "written": False,
        }
        if fingerprint in existing_fingerprints or not bool(getattr(args, "apply", False)):
            return emit_result(result, args)
        state_document, state = state_access.read_task_state_cache(task_dir)
        payloads, _projected, _reduced = state_access.append_task_events_payloads(
            task_dir, state_access.task_state_path(task_dir), state_document, state,
            [event_spec], synced_at=now(),
        )
        transactional_text_write(payloads)
        result["written"] = True
        return emit_result(result, args)
    reopen_from = str(getattr(args, "reopen_from", "") or "").strip()
    if reopen_from:
        changed_phase = str(getattr(args, "changed_phase", "") or current).strip()
        if changed_phase not in long_task.PHASES or reopen_from not in long_task.PHASES:
            raise WKError("reopen boundary phases must be canonical", code="CONFIRM_PHASE_INVALID")
        if long_task.PHASES.index(changed_phase) > current_index:
            raise WKError("--changed-phase cannot be later than the current phase", code="CONFIRM_CHANGED_PHASE_FUTURE")
        if long_task.PHASES.index(reopen_from) >= long_task.PHASES.index(changed_phase):
            raise WKError("--reopen-from must be upstream of --changed-phase", code="CONFIRM_REOPEN_ORDER")
        source_revision = str(getattr(args, "source_revision", "") or "").strip()
        if not source_revision:
            raise WKError("--source-revision is required with --reopen-from", code="CONFIRM_SOURCE_REVISION_REQUIRED")
        event_spec, fingerprint = _reopen_authorization_event_spec(
            task_dir, changed_phase=changed_phase, from_phase=reopen_from,
            source_revision=source_revision, reason=reason, agent=agent, state=state,
        )
        existing_fingerprints = {
            str((event.get("payload") or {}).get("requestFingerprint") or "")
            for event in task_events.read_events(task_dir)
            if event.get("type") == "AUTHORIZATION_BATCH_CONFIRMED"
            and isinstance(event.get("payload"), dict)
        }
        result = {
            "status": "NO_OP" if fingerprint in existing_fingerprints else ("CONFIRMED" if bool(getattr(args, "apply", False)) else "PREVIEW"),
            "taskDir": str(task_dir), "authorizationMode": "reopen",
            "changedPhase": changed_phase, "fromPhase": reopen_from,
            "sourceRevision": source_revision, "reason": reason,
            "eventType": "AUTHORIZATION_BATCH_CONFIRMED", "requestFingerprint": fingerprint,
            "alreadyConfirmed": fingerprint in existing_fingerprints, "written": False,
        }
        if fingerprint in existing_fingerprints or not bool(getattr(args, "apply", False)):
            return emit_result(result, args)
        state_document, state = state_access.read_task_state_cache(task_dir)
        payloads, _projected, _reduced = state_access.append_task_events_payloads(
            task_dir, state_access.task_state_path(task_dir), state_document, state,
            [event_spec], synced_at=now(),
        )
        transactional_text_write(payloads)
        result["written"] = True
        return emit_result(result, args)

    target = _confirmation_phase(args.until)
    target_index = long_task.PHASES.index(target)
    if target_index < current_index:
        raise WKError(
            f"--until {target} is earlier than current phase {current}",
            code="CONFIRM_PHASE_BACKWARD",
        )

    decision = lifecycle_decision.evaluate(task_dir, long_task)
    first = _recommended_from_decision(decision, task_dir, agent)
    minimal = bool((state.get("work_mode") or {}).get("minimal"))
    current_skipped = phase_skip_allowed(current, minimal, state)
    if current_skipped:
        actions: list[dict[str, Any]] = [{
            "phase": current,
            "command": "",
            "requiredAgent": str(long_task.PHASE_DEFINITIONS.get(current, {}).get("ownerAgent") or agent),
            "status": "NOT_APPLICABLE",
            "reasonCodes": ["PHASE_SKIPPED_BY_ACTIVE_WORKFLOW"],
        }]
    else:
        actions = [{
            "phase": current,
            "command": (first.get("nextCommand", {}).get("command", "")
                        if isinstance(first.get("nextCommand"), dict)
                        else first.get("nextCommand") or first.get("recommendedCommand") or ""),
            "requiredAgent": first.get("requiredAgent") or agent,
            "status": "CURRENT_DECISION" if decision.get("classification") not in {"BLOCKED", "INVALID"} else "REQUIRES_GATE",
            "reasonCodes": list(decision.get("reasonCodes") or []),
        }]
    for phase in long_task.PHASES[current_index + 1: target_index + 1]:
        owner = str(long_task.PHASE_DEFINITIONS.get(phase, {}).get("ownerAgent") or "ProjectManagerAgent")
        skipped = phase_skip_allowed(phase, minimal, state)
        actions.append({
            "phase": phase,
            "command": "" if skipped else f"python3 scripts/long_task.py advance-phase -g ProjectManagerAgent --task-dir {shlex.quote(str(task_dir.resolve()))}",
            "requiredAgent": owner,
            "status": "NOT_APPLICABLE" if skipped else "REQUIRES_GATE",
            "reasonCodes": (["PHASE_SKIPPED_BY_ACTIVE_WORKFLOW"] if skipped
                            else ["BATCH_AUTHORIZATION_DOES_NOT_BYPASS_GATE"]),
        })
    event_spec, fingerprint = _authorization_event_spec(
        task_dir, current, target, reason, agent, actions, state=state
    )
    existing_fingerprints = set()
    try:
        for event in task_events.read_events(task_dir):
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            value = payload.get("requestFingerprint")
            if event.get("type") == "AUTHORIZATION_BATCH_CONFIRMED" and isinstance(value, str):
                existing_fingerprints.add(value)
    except (OSError, ValueError) as exc:
        raise WKError(f"cannot read task event ledger: {exc}", code="CONFIRM_LEDGER_INVALID") from exc

    if fingerprint in existing_fingerprints:
        result = {
            "status": "NO_OP", "taskDir": str(task_dir), "fromPhase": current,
            "untilPhase": target, "reason": reason, "atomicActions": actions,
            "eventType": "AUTHORIZATION_BATCH_CONFIRMED", "requestFingerprint": fingerprint,
            "alreadyConfirmed": True, "written": False,
        }
        return emit_result(result, args)
    result = {
        "status": "CONFIRMED" if bool(getattr(args, "apply", False)) else "PREVIEW",
        "taskDir": str(task_dir), "fromPhase": current, "untilPhase": target,
        "reason": reason, "atomicActions": actions,
        "eventType": "AUTHORIZATION_BATCH_CONFIRMED", "requestFingerprint": fingerprint,
        "alreadyConfirmed": False, "written": False,
    }
    if not bool(getattr(args, "apply", False)):
        return emit_result(result, args)
    state_document, state = state_access.read_task_state_cache(task_dir)
    payloads, _projected, _reduced = state_access.append_task_events_payloads(
        task_dir,
        state_access.task_state_path(task_dir),
        state_document,
        state,
        [event_spec],
        synced_at=now(),
    )
    transactional_text_write(payloads)
    result["written"] = True
    return emit_result(result, args)


def command_confirm(args: argparse.Namespace) -> int:
    """Run mutating confirmation under the shared task lifecycle writer lock."""
    if not bool(getattr(args, "apply", False)):
        return _command_confirm_unlocked(args)
    with task_writer_lock(Path(args.task_dir)):
        return _command_confirm_unlocked(args)


def command_context(args: argparse.Namespace) -> int:
    """Emit reducer-derived task context without reading task-state cache."""
    agent = agent_context.authorize_command(args, "wk", "context")
    task_dir = Path(args.task_dir)
    if args.trace_id or args.acceptance_id:
        payload = current_manifest.query_traceability(
            task_dir, trace_id=str(args.trace_id or ""), acceptance_id=str(args.acceptance_id or "")
        )
        payload["source"] = "traceability.json canonical JSON"
        return emit_result(payload, args) if not payload.get("notFound") and payload.get("status") == "PASSED" else 1
    context = long_task.build_task_context(task_dir, task_id=str(args.task_id or ""))
    context["source"] = "task_events.jsonl reducer"
    state = long_task.runtime_state(task_dir)
    context.update(_current_manifest_ref(task_dir, state=state))
    return emit_result(context, args)


CATEGORY_ORDER = ["request", "mode", "action", "orchestration", "selector"]


def command_help(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "wk", "help", declared=True)
    if args.script:
        payload = cli_help.catalog_for_script(args.script, str(args.script_command or ""))
        return emit_result(payload, args)
    if args.script_command:
        raise WKError("--command requires --script")
    entries = runtime_contract.command_catalog()
    if args.flag:
        entries = [runtime_contract.command_by_flag(args.flag)]
    elif args.category:
        entries = [item for item in entries if item.get("category") == args.category]
    payload = {
        "count": len(entries),
        "commands": entries,
        "machineSource": "assets/runtime/runtime-contract.json#/commandInterface",
        "detailQuery": "python3 scripts/runtime_contract.py command -g {CURRENT_AGENT} --flag={FLAG} --json",
        "capabilityPackQuery": "python3 scripts/wk.py packs -g {CURRENT_AGENT} --task-type {TASK_TYPE} --phase {PHASE} --json",
    }
    if args.json:
        return emit_result(payload, args)
    grouped: dict[str, list[dict[str, Any]]] = {category: [] for category in CATEGORY_ORDER}
    for item in entries:
        grouped.setdefault(str(item.get("category") or "other"), []).append(item)
    for category in CATEGORY_ORDER + [key for key in grouped if key not in CATEGORY_ORDER]:
        items = grouped.get(category) or []
        if not items:
            continue
        print(category.upper())
        for item in items:
            aliases = ", ".join(item.get("flags") or [])
            print(f"  {aliases}: {item.get('description', '')}")
    return 0


def command_packs(args: argparse.Namespace) -> int:
    """Resolve internal capability packs without creating another Skill entry."""
    agent = agent_context.authorize_command(args, "wk", "packs", declared=True)
    payload = capability_packs.resolve(args.task_type, args.phase)
    if args.json:
        return emit_result(payload, args)
    else:
        for item in payload["packs"]:
            print(f"{item['id']}: {item['mode']} ({', '.join(item['reasonCodes'])})")
    return 0
