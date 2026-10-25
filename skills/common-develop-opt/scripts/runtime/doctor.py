"""Project/version health checks and explicitly registered safe repairs for wk."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path
from typing import Any

from document_core import agent_context
from runtime import current_manifest
from runtime import long_task
from runtime import request_intake
from document_core import storage
from runtime import work_mode
from runtime import work_mode_events
from document_core import simple_yaml
import merge_docs
from runtime import project_constraints
from runtime import task_layout
from runtime.error_catalog import SkillError
from runtime._long_task.phase_policy import actual_work_mode_state


# Doctor may only execute repairs explicitly listed here.  The registry is
# intentionally data-only: callers cannot turn an arbitrary repairCommand
# string into a shell command.  Additions require a named callable and a
# corresponding contract/test update.
SAFE_REPAIRS: dict[str, dict[str, Any]] = {
    "sync-work-mode": {
        "id": "sync-work-mode",
        "commandId": "long_task.sync-work-mode",
        "description": "将任务 runtime 模式重新绑定到版本 work.md",
        "idempotent": True,
        "requiresAgent": "ProjectManagerAgent",
        "forbiddenSideEffects": [
            "phase_transition",
            "retry_policy_change",
            "archive",
            "git_push",
        ],
    },
    "task-layout-sync": {
        "id": "task-layout-sync",
        "commandId": "task_layout.sync",
        "description": "重新生成 current_manifest.json 投影",
        "idempotent": True,
        "requiresAgent": "ProjectManagerAgent",
        "forbiddenSideEffects": [
            "phase_transition",
            "retry_policy_change",
            "archive",
            "git_push",
        ],
    },
}


class DoctorFixError(SkillError):
    """Raised when the explicit doctor repair boundary is violated."""

    def __init__(self, message: str, *, code: str = "DOCTOR_FIX_INVALID", repair_command: str | None = None) -> None:
        super().__init__(code, message, repair_command)


def _repair_id_for_check(check_id: str) -> str | None:
    if check_id.startswith("task-mode-sync:"):
        return "sync-work-mode"
    if check_id.startswith("current-manifest:"):
        return "task-layout-sync"
    return None


def _repair_record(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize a finding repair and mark whether doctor may execute it."""
    repair_command = str(item.get("repairCommand") or "")
    repair_id = _repair_id_for_check(str(item.get("id") or ""))
    definition = SAFE_REPAIRS.get(repair_id or "")
    value: dict[str, Any] = {
        "command": repair_command,
        "safe": bool(definition),
        "idempotent": bool(definition and definition.get("idempotent")),
        "reason": str(item.get("message") or ""),
        "check": item.get("id"),
        "registered": bool(definition),
    }
    if definition:
        value.update({
            "id": definition["id"],
            "commandId": definition["commandId"],
            "description": definition["description"],
            "requiresAgent": definition["requiresAgent"],
        })
    else:
        value.update({
            "id": "unregistered",
            "blockedReason": "该建议修复不在 doctor 安全修复注册表中；只能人工执行",
        })
    if item.get("code"):
        value["code"] = item["code"]
    return value


def _safe_repairs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        item for item in payload.get("repairs", [])
        if isinstance(item, dict) and item.get("registered") is True and item.get("safe") is True
    ]


def _invoke_safe_repair(repair: dict[str, Any], *, agent: str) -> dict[str, Any]:
    """Invoke one fixed repair callable; never evaluate the displayed command."""
    repair_id = str(repair.get("id") or "")
    definition = SAFE_REPAIRS.get(repair_id)
    if not definition:
        raise DoctorFixError(
            f"拒绝执行未注册的 doctor 修复：{repair_id or 'unknown'}",
            code="DOCTOR_REPAIR_NOT_REGISTERED",
        )
    required = str(definition.get("requiresAgent") or "")
    if agent != required:
        raise DoctorFixError(
            f"Agent {agent} 无权执行 doctor 修复 {repair_id}；需要 {required}",
            code="DOCTOR_FIX_AGENT_FORBIDDEN",
        )

    check_id = str(repair.get("check") or "")
    if repair_id == "sync-work-mode":
        task_dir = Path(str(repair.get("taskDir") or ""))
        if not task_dir.is_dir() or not check_id.startswith("task-mode-sync:"):
            raise DoctorFixError("sync-work-mode 修复缺少经过 doctor 校验的任务目录", code="DOCTOR_REPAIR_INPUT_INVALID")
        command_args = argparse.Namespace(task_dir=str(task_dir), agent=agent)
        # The existing command owns all lifecycle validation and transaction
        # semantics.  Capture its legacy stdout so doctor keeps one output.
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            long_task.command_sync_work_mode(command_args)
        raw = output.getvalue().strip()
        result: Any = raw
        if raw:
            try:
                result = json.loads(raw)
            except json.JSONDecodeError:
                result = raw
    elif repair_id == "task-layout-sync":
        task_dir = Path(str(repair.get("taskDir") or ""))
        if not task_dir.is_dir() or not check_id.startswith("current-manifest:"):
            raise DoctorFixError("task-layout-sync 修复缺少经过 doctor 校验的任务目录", code="DOCTOR_REPAIR_INPUT_INVALID")
        # task_layout.sync is the existing implementation.  Keep its
        # declared command authorization explicit even though we call the
        # shared function directly to avoid nested CLI output.
        agent_context.authorize_declared(agent, "task_layout", "sync")
        result = task_layout.sync(task_dir)
    else:  # defensive branch for future registry edits
        raise DoctorFixError(f"doctor 修复 {repair_id} 没有实现", code="DOCTOR_REPAIR_NOT_IMPLEMENTED")
    return result if isinstance(result, dict) else {"output": result}


def _error_code(exc: BaseException, default: str = "DOCTOR_CHECK_FAILED") -> str:
    code = getattr(exc, "code", None)
    return str(code) if code else default


def _finding(
    check_id: str,
    status: str,
    message: str,
    *,
    code: str | None = None,
    repair_command: str = "",
    details: Any = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {"id": check_id, "status": status, "message": message}
    if code:
        value["code"] = code
    if repair_command:
        value["repairCommand"] = repair_command
    if details is not None:
        value["details"] = details
    return value


def _check(path: Path, check_id: str, message: str, repair_command: str = "") -> dict[str, Any]:
    if path.is_file() or path.is_dir():
        return _finding(check_id, "PASSED", message)
    return _finding(
        check_id,
        "FAILED",
        f"missing: {path}",
        code="DOCTOR_REQUIRED_PATH_MISSING",
        repair_command=repair_command,
    )


def _task_mode(mode: dict[str, Any]) -> work_mode.WorkMode:
    """Convert the reducer's snake-case mode snapshot to the public model."""
    return work_mode.WorkMode(
        model_code=bool(mode.get("model_code")),
        page_design=bool(mode.get("page_design")),
        minimal=bool(mode.get("minimal")),
        auto=bool(mode.get("auto")),
        architecture_review=bool(mode.get("architecture_review")),
        git_checkpoint=bool(mode.get("git_checkpoint")),
        git_push=bool(mode.get("git_push")),
        review_only=bool(mode.get("review_only")),
        lightweight=bool(mode.get("lightweight")),
    )


def _archive_plan_findings(version_root: Path, version: str) -> list[dict[str, Any]]:
    """Validate each generated merge plan without treating archive_manifest as a plan."""
    generated = version_root / "module"
    plans = sorted(generated.glob("*/generated/merge_plan.yaml")) if generated.is_dir() else []
    if not plans:
        return [_finding("archive-plan", "SKIPPED", "未发现 generated/merge_plan.yaml；当前版本尚未生成归档预览", code="DOCTOR_ARCHIVE_PLAN_NOT_PRESENT")]
    result: list[dict[str, Any]] = []
    for path in plans:
        check_id = f"archive-plan:{path.parent.parent.name}"
        try:
            plan = simple_yaml.load(path)
            if not isinstance(plan, dict):
                raise ValueError("merge plan must be a mapping")
            if str(plan.get("version") or version) != str(version):
                raise ValueError("merge plan version differs from requested version")
            gate = plan.get("archiveGate")
            if not isinstance(gate, dict):
                result.append(_finding(check_id, "DRIFT", f"归档计划缺少 archiveGate：{path}", code="DOCTOR_ARCHIVE_PLAN_GATE_MISSING"))
            else:
                status = str(gate.get("status") or gate.get("decision") or "PENDING")
                result.append(_finding(check_id, "PASSED", f"归档计划有效：{path}（archiveGate={status}）", details={"path": str(path), "archiveGateStatus": status}))
        except simple_yaml.YamlError as exc:
            result.append(_finding(check_id, "FAILED", f"归档计划 YAML 无效：{path}：{exc}", code="DOCTOR_ARCHIVE_PLAN_INVALID"))
        except (OSError, ValueError, TypeError) as exc:
            result.append(_finding(check_id, "FAILED", f"归档计划无效：{path}：{exc}", code="DOCTOR_ARCHIVE_PLAN_INVALID"))
    return result


def inspect(doc_root: Path, version: str, *, agent: str = "ProjectManagerAgent") -> dict[str, Any]:
    """Inspect canonical state without calling any mutating command."""
    doc_root = Path(doc_root)
    version = str(version)
    version_root = doc_root / "version" / version
    findings: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    work_path = work_mode.work_path(doc_root, version)
    findings.append(_check(doc_root, "project-root", "项目文档根目录存在"))
    findings.append(_check(version_root, "version-root", "版本目录存在"))
    findings.append(_check(work_path, "work-file", "work.md 存在", f"python3 scripts/work_mode.py init -g {agent} --doc-root {doc_root} --version {version}"))

    current_mode: work_mode.WorkMode | None = None
    if work_path.is_file():
        try:
            current_mode = work_mode.validate_work_file(work_path)
            findings.append(_finding("work-mode", "PASSED", "work.md 标记、摘要、分工表和模式策略一致"))
        except (OSError, ValueError) as exc:
            findings.append(_finding("work-mode", "FAILED", str(exc), code=_error_code(exc, "WORKMODE_VALIDATION_FAILED"), repair_command=f"python3 scripts/work_mode.py validate -g {agent} --doc-root {doc_root} --version {version}"))
    else:
        findings.append(_finding("work-mode", "SKIPPED", "缺少 work.md，无法校验工作模式", code="DOCTOR_DEPENDENCY_MISSING"))

    task_root = version_root / "task"
    tasks = sorted(path for path in task_root.iterdir() if path.is_dir()) if task_root.is_dir() else []
    findings.append(_finding("task-index", "PASSED" if task_root.is_dir() else "SKIPPED", f"发现 {len(tasks)} 个任务目录" if task_root.is_dir() else f"缺少任务目录：{task_root}"))
    for task_dir in tasks:
        task_id = task_dir.name
        errors: list[str] = []
        warnings: list[str] = []
        state: dict[str, Any] = {}
        issues: list[dict[str, Any]] = []
        try:
            errors, warnings, state, issues = long_task.validate_task(task_dir)
        except (OSError, ValueError, storage.StorageError) as exc:
            errors = [str(exc)]
        if errors:
            findings.append(_finding(f"task-runtime:{task_id}", "FAILED", "; ".join(errors), code="DOCTOR_TASK_RUNTIME_INVALID", repair_command=f"python3 scripts/long_task.py validate -g {agent} --task-dir {task_dir}"))
        else:
            findings.append(_finding(f"task-runtime:{task_id}", "PASSED", "任务事件账本和运行时整体校验通过"))
        if warnings:
            findings.append(_finding(f"task-warnings:{task_id}", "WARNING", "; ".join(warnings), code="DOCTOR_TASK_WARNING"))

        # Keep these as separate findings so a doctor consumer can identify the
        # exact repair domain instead of parsing one aggregated validator string.
        schema_errors = [item for item in errors if "schema_version" in item or "unsupported_schema" in item]
        findings.append(_finding(f"task-schema:{task_id}", "FAILED" if schema_errors else "PASSED", "; ".join(schema_errors) if schema_errors else "任务 schema 版本有效", code="DOCTOR_TASK_SCHEMA_INVALID" if schema_errors else None))
        phase = state.get("current_phase") if isinstance(state, dict) else None
        valid_phases = set(getattr(long_task, "PHASE_DEFINITIONS", {}))
        phase_errors = [item for item in errors if "current_phase" in item or "predecessor" in item]
        phase_ok = bool(phase in valid_phases) and not phase_errors
        findings.append(_finding(f"task-current-phase:{task_id}", "PASSED" if phase_ok else "FAILED", f"当前阶段合法：{phase}" if phase_ok else "; ".join(phase_errors) or f"当前阶段无效：{phase}", code=None if phase_ok else "DOCTOR_TASK_CURRENT_PHASE_INVALID"))

        intake_path = task_dir / "request_intake.json"
        intake_document: dict[str, Any] | None = None
        if intake_path.is_file():
            try:
                intake_document = request_intake.load(intake_path)
                findings.append(_finding(f"request-intake:{task_id}", "PASSED", "request_intake.json 有效"))
            except (OSError, ValueError) as exc:
                findings.append(_finding(f"request-intake:{task_id}", "FAILED", str(exc), code="DOCTOR_REQUEST_INTAKE_INVALID"))
        else:
            findings.append(_finding(f"request-intake:{task_id}", "FAILED", f"缺少：{intake_path}", code="DOCTOR_REQUEST_INTAKE_MISSING"))

        mode_sync_ok = True
        actual: dict[str, Any] = {}
        try:
            actual = actual_work_mode_state(task_dir)
            recorded = state.get("work_mode") if isinstance(state.get("work_mode"), dict) else {}
            mode_keys = ("ref", "digest", "model_code", "page_design", "minimal", "auto", "architecture_review", "git_checkpoint", "git_push", "review_only", "lightweight")
            drift = [key for key in mode_keys if recorded.get(key) != actual.get(key)]
            mode_sync_ok = not drift
            findings.append(_finding(f"task-mode-sync:{task_id}", "PASSED" if mode_sync_ok else "DRIFT", "work.md 当前模式与 task runtime 一致" if mode_sync_ok else f"work.md 与 task runtime 模式不一致：{', '.join(drift)}", code=None if mode_sync_ok else "DOCTOR_TASK_MODE_DRIFT", repair_command=f"python3 scripts/long_task.py sync-work-mode -g {agent} --task-dir {task_dir}" if not mode_sync_ok else ""))
        except (OSError, ValueError) as exc:
            findings.append(_finding(f"task-mode-sync:{task_id}", "SKIPPED", f"无法比较任务模式：{exc}", code="DOCTOR_DEPENDENCY_MISSING"))

        if intake_document is not None and actual:
            profile = str(intake_document.get("workflowProfile") or "")
            try:
                work_mode.validate_workflow_mode_compatibility(profile, _task_mode(actual))
                findings.append(_finding(f"mode-profile-compatibility:{task_id}", "PASSED", f"模式与 workflow profile 兼容：{profile or '未声明'}"))
            except ValueError as exc:
                findings.append(_finding(f"mode-profile-compatibility:{task_id}", "FAILED", str(exc), code=_error_code(exc, "WORKMODE_PROFILE_INCOMPATIBLE")))
        else:
            findings.append(_finding(f"mode-profile-compatibility:{task_id}", "SKIPPED", "缺少 request intake 或当前模式，无法校验 profile 兼容性", code="DOCTOR_DEPENDENCY_MISSING"))

        manifest_path = current_manifest.manifest_path(task_dir)
        existing = current_manifest.read_manifest(task_dir)
        if existing:
            try:
                built = current_manifest.build_manifest(task_dir, state=state, issues=issues, validation_errors=errors, validation_warnings=warnings)
                fresh = existing.get("sourceInputDigest") == built.get("sourceInputDigest")
            except (OSError, ValueError):
                fresh = False
            findings.append(_finding(f"current-manifest:{task_id}", "PASSED" if fresh else "DRIFT", "current manifest 是最新" if fresh else f"current manifest 已过期：{manifest_path}", code=None if fresh else "DOCTOR_CURRENT_MANIFEST_STALE", repair_command=f"python3 scripts/task_layout.py sync -g {agent} --task-dir {task_dir} --json" if not fresh else ""))
        else:
            findings.append(_finding(f"current-manifest:{task_id}", "DRIFT", f"缺少 current manifest：{manifest_path}", code="DOCTOR_CURRENT_MANIFEST_MISSING", repair_command=f"python3 scripts/task_layout.py sync -g {agent} --task-dir {task_dir} --json"))

    # Archive-plan is based on generated merge plans from the storage contract;
    # archive_manifest is checked separately as the applied-history index. A
    # missing manifest is expected until an archive plan has been generated.
    archive_plan_findings = _archive_plan_findings(version_root, version)
    findings.extend(archive_plan_findings)
    archive_manifest = doc_root / str(storage.CONTRACT.get("versionFiles", {}).get("archiveManifest", "archive_manifest.yaml"))
    archive_plan_exists = any(
        item.get("id", "").startswith("archive-plan:")
        for item in archive_plan_findings
    )
    if archive_manifest.is_file():
        try:
            merge_docs.load_manifest(doc_root)
        except (OSError, ValueError, TypeError) as exc:
            findings.append(_finding(
                "archive-manifest",
                "FAILED",
                f"archive_manifest.yaml 无效：{archive_manifest}：{exc}",
                code="DOCTOR_ARCHIVE_MANIFEST_INVALID",
            ))
        else:
            findings.append(_finding("archive-manifest", "PASSED", "archive_manifest.yaml 存在且有效"))
    elif archive_plan_exists:
        findings.append(_finding(
            "archive-manifest",
            "DRIFT",
            f"归档计划已生成但缺少 archive_manifest.yaml：{archive_manifest}",
            code="DOCTOR_ARCHIVE_MANIFEST_MISSING",
            repair_command=f"python3 scripts/merge_docs.py init -g {agent} --doc-root {doc_root} --version {version} --module <MODULE>",
        ))
    else:
        findings.append(_finding(
            "archive-manifest",
            "SKIPPED",
            "尚未生成归档计划，archive_manifest.yaml 暂不适用",
            code="DOCTOR_ARCHIVE_MANIFEST_NOT_APPLICABLE",
        ))
    # Project constraints are optional, but when declared they are part of the
    # read-only health projection. Validation never writes or mutates the source.
    try:
        constraint_result = project_constraints.validate_project_constraints(doc_root, version)
    except (OSError, ValueError) as exc:
        constraint_result = {
            "status": "FAILED", "constraints": [], "checks": [], "errors": [str(exc)], "warnings": [],
        }
    constraint_status = str(constraint_result.get("status") or "FAILED")
    if constraint_status == "NOT_DECLARED":
        finding_status = "SKIPPED"
        finding_code = "DOCTOR_PROJECT_CONSTRAINTS_NOT_DECLARED"
    elif constraint_status == "PASSED":
        finding_status = "PASSED"
        finding_code = None
    elif constraint_status == "PARTIAL":
        finding_status = "WARNING"
        finding_code = "DOCTOR_PROJECT_CONSTRAINTS_PARTIAL"
    else:
        finding_status = "FAILED"
        finding_code = "DOCTOR_PROJECT_CONSTRAINTS_INVALID"
    constraint_messages = list(constraint_result.get("errors") or []) + list(constraint_result.get("warnings") or [])
    findings.append(_finding(
        "project-constraints", finding_status,
        "项目约束声明有效" if finding_status == "PASSED" else (
            "未声明 common-develop-constraints block" if finding_status == "SKIPPED" else "; ".join(constraint_messages) or "项目约束声明需要处理"
        ),
        code=finding_code,
        details={"status": constraint_status, "constraints": constraint_result.get("constraints", []), "checks": constraint_result.get("checks", [])},
    ))

    event_ledger = work_mode_events.event_path(doc_root, version)
    if event_ledger.exists():
        try:
            events = work_mode_events.read_events(doc_root, version)
            findings.append(_finding("work-mode-events", "PASSED", f"版本级工作模式事件链有效（{len(events)} 条）"))
        except (OSError, ValueError) as exc:
            findings.append(_finding("work-mode-events", "FAILED", str(exc), code="WORKMODE_EVENT_LEDGER_INVALID"))
    else:
        findings.append(_finding("work-mode-events", "WARNING", "尚未记录版本级工作模式变更", code="WORKMODE_EVENT_LEDGER_MISSING"))

    for item in findings:
        if item["status"] in {"FAILED", "DRIFT"} and item.get("repairCommand"):
            repair = _repair_record(item)
            # The task directory is derived from the finding ID and canonical
            # version root, never from user-provided repair text.
            if repair["id"] in {"sync-work-mode", "task-layout-sync"}:
                task_id = str(item["id"]).split(":", 1)[1]
                repair["taskDir"] = str(version_root / "task" / task_id)
            repairs.append(repair)
    failed = any(item["status"] in {"FAILED", "DRIFT"} for item in findings)
    return {"status": "DRIFT" if failed else "PASSED", "docRoot": str(doc_root), "version": version, "agent": agent, "readOnly": True, "checks": findings, "findings": [item for item in findings if item["status"] in {"FAILED", "DRIFT", "WARNING"}], "repairs": repairs}


def _repair_key(repair: dict[str, Any]) -> tuple[str, str, str]:
    """Return a stable identity for one concrete finding repair."""
    return (
        str(repair.get("id") or ""),
        str(repair.get("taskDir") or ""),
        str(repair.get("check") or ""),
    )


def _merge_repair_plan(
    plan: dict[tuple[str, str, str], dict[str, Any]],
    repairs: list[dict[str, Any]],
    *,
    status: str,
) -> None:
    """Keep the latest concrete repair metadata without duplicating findings."""
    for repair in repairs:
        if not isinstance(repair, dict):
            continue
        item = dict(repair)
        # Unregistered suggestions are diagnostic only and must remain blocked
        # in both preview and apply plans, regardless of their displayed shell
        # command.  Only registry-backed repairs may enter READY/PENDING.
        item["status"] = status if item.get("registered") is True and item.get("safe") is True else "BLOCKED"
        plan[_repair_key(item)] = item


def fix(
    doc_root: Path,
    version: str,
    *,
    agent: str = "ProjectManagerAgent",
    apply: bool = False,
) -> dict[str, Any]:
    """Preview or apply only registered, idempotent doctor repairs.

    ``inspect`` remains the sole diagnostic implementation.  Applying a
    repair invokes the existing Python command implementation through a
    fixed registry entry, never the displayed shell string.  After every
    attempt the canonical state is inspected again; newly exposed safe
    repairs may be queued, with a bounded pass count for fail-closed safety.
    """
    initial = inspect(doc_root, version, agent=agent)
    plan: dict[tuple[str, str, str], dict[str, Any]] = {}
    _merge_repair_plan(
        plan,
        [item for item in initial.get("repairs", []) if isinstance(item, dict)],
        status="READY" if not apply else "PENDING",
    )

    if not apply:
        planned = list(plan.values())
        return {
            **initial,
            "status": "PREVIEW",
            "diagnosticStatus": initial["status"],
            "readOnly": True,
            "fixRequested": True,
            "applyRequested": False,
            "appliedRepairs": [],
            "failedRepairs": [],
            "blockedRepairs": [item for item in planned if item["status"] == "BLOCKED"],
            "repairs": planned,
        }

    if agent != "ProjectManagerAgent":
        raise DoctorFixError(
            f"Agent {agent} 无权执行 doctor --fix --apply；仅 ProjectManagerAgent 可执行安全修复",
            code="DOCTOR_FIX_AGENT_FORBIDDEN",
        )

    applied: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    attempted: set[tuple[str, str, str]] = set()
    queue: list[dict[str, Any]] = [item for item in _safe_repairs(initial)]
    max_attempts = max(1, len(queue) + 16)

    # Process the initial findings and any safe finding revealed by a prior
    # repair.  The bound protects the command if a broken repair oscillates.
    while queue and len(attempted) < max_attempts:
        repair = queue.pop(0)
        key = _repair_key(repair)
        if key in attempted:
            continue
        attempted.add(key)
        try:
            result = _invoke_safe_repair(repair, agent=agent)
            applied_item = {**repair, "status": "APPLIED", "result": result}
            applied.append(applied_item)
            plan[key] = applied_item
        except (DoctorFixError, OSError, ValueError, RuntimeError) as exc:
            failed_item = {
                **repair,
                "status": "FAILED",
                "error": {
                    "code": getattr(exc, "code", "DOCTOR_REPAIR_FAILED"),
                    "message": str(exc),
                },
            }
            failures.append(failed_item)
            plan[key] = failed_item

        refreshed = inspect(doc_root, version, agent=agent)
        refreshed_repairs = [item for item in refreshed.get("repairs", []) if isinstance(item, dict)]
        for item in refreshed_repairs:
            item_key = _repair_key(item)
            if item_key in attempted:
                # Preserve APPLIED/FAILED outcome while retaining the latest
                # repair metadata and post-check status.
                previous = plan.get(item_key, item)
                previous["postCheckStatus"] = refreshed["status"]
                plan[item_key] = previous
            elif item.get("registered") is True and item.get("safe") is True:
                item["status"] = "PENDING"
                plan[item_key] = item
                queue.append(item)
            else:
                item["status"] = "BLOCKED"
                plan[item_key] = item

    final = inspect(doc_root, version, agent=agent)
    final_repairs = [item for item in final.get("repairs", []) if isinstance(item, dict)]
    for item in final_repairs:
        key = _repair_key(item)
        if key not in plan:
            item["status"] = "BLOCKED" if not item.get("registered") else "PENDING"
            plan[key] = item

    planned = list(plan.values())
    blocked = [item for item in planned if item.get("status") == "BLOCKED"]
    # A repair that remains unresolved is not a successful fix.  This keeps
    # FIXED reserved for a clean post-condition rather than a successful call.
    if final["status"] == "PASSED":
        final_status = "FIXED"
    elif applied:
        final_status = "PARTIAL"
    else:
        final_status = "FAILED"
    return {
        **final,
        "status": final_status,
        "diagnosticStatus": final["status"],
        "readOnly": False,
        "fixRequested": True,
        "applyRequested": True,
        "appliedRepairs": applied,
        "failedRepairs": failures,
        "blockedRepairs": blocked,
        "repairs": planned,
    }


def render(payload: dict[str, Any]) -> str:
    lines = [f"{payload['status']} {payload['docRoot']} version={payload['version']} readOnly={str(payload.get('readOnly', True)).lower()}"]
    if payload.get("fixRequested"):
        lines.append(f"fixRequested=true applyRequested={str(bool(payload.get('applyRequested'))).lower()}")
    for item in payload.get("checks", []):
        code = f" {item['code']}" if item.get("code") else ""
        lines.append(f"[{item['status']}] {item['id']}{code}: {item['message']}")
        if item.get("repairCommand"):
            lines.append(f"  repair: {item['repairCommand']}")
    for key in ("appliedRepairs", "failedRepairs", "blockedRepairs"):
        for item in payload.get(key, []):
            lines.append(f"[{item.get('status', key.upper())}] repair {item.get('id', 'unknown')}: {item.get('reason') or item.get('error', {}).get('message', '')}")
    return "\n".join(lines)


__all__ = ["SAFE_REPAIRS", "DoctorFixError", "inspect", "fix", "render"]
