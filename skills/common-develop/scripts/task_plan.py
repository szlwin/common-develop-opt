#!/usr/bin/env python3
"""Create, review, revise, and freeze large-requirement development task plans."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import agent_context
from common_utils import atomic_write_text, canonical_json
from contract_query_utils import DEFAULT_MAX_SELECTION_BYTES, bounded_selection
from _core import implementation_strategy

SCHEMA_VERSION = 1
PLAN_FILENAME = "development_task_plan.json"
REVIEW_FILENAME = "evidence/reviews.jsonl"
PLAN_STATUSES = {"PENDING", "IN_REVIEW", "REWORK", "BLOCKED", "PASSED"}
REVIEW_RESULTS = {"PASSED", "NEEDS_CHANGES", "BLOCKED"}
SEVERITIES = {"P0", "P1", "P2"}
PLACEHOLDER_RE = re.compile(r"\b(?:TBD|TODO|LATER)\b|适当|相关测试|类似处理|后续补充", re.IGNORECASE)
PLAN_REVISION_RE = re.compile(r"^TP-[A-Z0-9_.-]+-R(\d{2,})@([0-9a-f]{12})$")
TASK_ID_RE = re.compile(r"^TASK-[A-Z0-9_.-]+$")
REVIEW_ID_RE = re.compile(r"^TPR-[0-9]{6}$")
FINDING_ID_RE = re.compile(r"^TPF-[0-9]{6}-[0-9]{2}$")
STANDARD_REVIEWERS = ["PlanReviewAgent", "ArchitectureReviewAgent", "TestDesignAgent", "DevelopAgent"]
MINIMAL_REVIEWERS = ["PlanReviewAgent", "ArchitectureReviewAgent", "DevelopAgent"]
TASK_QUERY_FIELDS = {
    "task_id", "title", "goal", "implementation", "acceptance_criteria", "depends_on",
    "owner_agent", "reviewer_agents", "validation_commands", "expected_results", "deliverables",
    "stop_conditions", "trace_ids", "flow_refs", "flow_step_refs", "risk_triggers",
    "git_checkpoint_required",
}
DEFAULT_TASK_QUERY_FIELDS = (
    "task_id", "title", "goal", "depends_on", "owner_agent", "reviewer_agents",
    "validation_commands", "expected_results", "trace_ids",
)


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def plan_path(task_dir: Path) -> Path:
    return Path(task_dir) / PLAN_FILENAME


def review_path(task_dir: Path) -> Path:
    return Path(task_dir) / REVIEW_FILENAME





def load_plan(task_dir: Path) -> dict[str, Any]:
    path = plan_path(task_dir)
    if not path.is_file():
        raise ValueError(f"missing {PLAN_FILENAME}; run task_plan.py init first")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {PLAN_FILENAME}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{PLAN_FILENAME} root must be an object")
    return value


def load_reviews(task_dir: Path) -> list[dict[str, Any]]:
    path = review_path(task_dir)
    if not path.exists():
        return []
    result: list[dict[str, Any]] = []
    for line_no, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid {REVIEW_FILENAME} line {line_no}: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{REVIEW_FILENAME} line {line_no} must be an object")
        if value.get("kind") == "PLAN_REVIEW":
            result.append({key: item for key, item in value.items() if key != "kind"})
    return result


def save_plan(task_dir: Path, plan: dict[str, Any], reviews: list[dict[str, Any]]) -> None:
    atomic_write_text(plan_path(task_dir), json.dumps(plan, ensure_ascii=False, indent=2) + "\n")


def append_review(task_dir: Path, record: dict[str, Any]) -> None:
    path = review_path(task_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(record)
    payload["kind"] = "PLAN_REVIEW"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(canonical_json(payload) + "\n")


def plan_content(plan: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "schema_version", "plan_id", "target_id", "version", "input_revisions",
        "work_mode", "execution_mode", "generated_by_agent", "required_reviewers", "tasks",
        "implementation_strategy_contract_version",
    )
    return {key: copy.deepcopy(plan.get(key)) for key in keys}


def compute_digest(plan: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(plan_content(plan)).encode("utf-8")).hexdigest()[:12]


def build_revision(plan: dict[str, Any], revision_no: int) -> str:
    return f"{plan['plan_id']}-R{revision_no:02d}@{compute_digest(plan)}"


def required_review_assignments(plan: dict[str, Any]) -> list[tuple[str, list[str]]]:
    task_ids = [str(task.get("task_id") or "") for task in plan.get("tasks") or [] if isinstance(task, dict)]
    order: list[str] = []
    assignment: dict[str, list[str]] = {}
    for reviewer in plan.get("required_reviewers") or []:
        if reviewer not in order:
            order.append(reviewer)
        assignment[reviewer] = list(task_ids)
    for task in plan.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("task_id") or "")
        for reviewer in task.get("reviewer_agents") or []:
            if reviewer not in order:
                order.append(reviewer)
            assignment.setdefault(reviewer, [])
            if task_id and task_id not in assignment[reviewer]:
                assignment[reviewer].append(task_id)
    return [(reviewer, assignment.get(reviewer, [])) for reviewer in order]


def current_revision_reviews(plan: dict[str, Any], reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    revision = str(plan.get("revision") or "")
    return [record for record in reviews if record.get("plan_revision") == revision]


def latest_by_reviewer(plan: dict[str, Any], reviews: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for record in current_revision_reviews(plan, reviews):
        reviewer = str(record.get("reviewer_agent") or "")
        if reviewer:
            result[reviewer] = record
    return result


def next_assignment(plan: dict[str, Any], reviews: list[dict[str, Any]]) -> tuple[str, list[str]] | None:
    latest = latest_by_reviewer(plan, reviews)
    for reviewer, task_ids in required_review_assignments(plan):
        record = latest.get(reviewer)
        if not record:
            return reviewer, task_ids
        if record.get("result") != "PASSED":
            return None
    return None


def _require_string(value: Any, label: str, errors: list[str], *, min_length: int = 1) -> str:
    if not isinstance(value, str) or len(value.strip()) < min_length:
        errors.append(f"{label} must be a non-empty string")
        return ""
    if PLACEHOLDER_RE.search(value):
        errors.append(f"{label} contains placeholder or vague wording")
    return value.strip()


def _require_string_list(value: Any, label: str, errors: list[str], *, min_items: int = 1) -> list[str]:
    if not isinstance(value, list) or len(value) < min_items:
        errors.append(f"{label} must contain at least {min_items} item(s)")
        return []
    result: list[str] = []
    for index, item in enumerate(value):
        text = _require_string(item, f"{label}[{index}]", errors)
        if text:
            result.append(text)
    if len(result) != len(set(result)):
        errors.append(f"{label} must not contain duplicates")
    return result


def validate_plan(plan: dict[str, Any], *, require_revision: bool = False) -> list[str]:
    errors: list[str] = []
    allowed_top = {
        "schema_version", "plan_id", "target_id", "version", "status", "revision",
        "revision_no", "supersedes_revision", "revision_reason", "input_revisions",
        "work_mode", "execution_mode", "generated_by_agent", "required_reviewers",
        "review_round", "created_at", "updated_at", "finalized_at", "tasks",
        "implementation_strategy_contract_version",
    }
    unknown = sorted(set(plan) - allowed_top)
    if unknown:
        errors.append(f"plan contains unknown fields: {unknown}")
    if plan.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    strategy_contract_version = plan.get("implementation_strategy_contract_version")
    strategy_contract_enabled = strategy_contract_version == implementation_strategy.CONTRACT_VERSION
    if strategy_contract_version not in (None, implementation_strategy.CONTRACT_VERSION):
        errors.append(
            "implementation_strategy_contract_version must be "
            f"{implementation_strategy.CONTRACT_VERSION} when present"
        )
    plan_id = _require_string(plan.get("plan_id"), "plan_id", errors)
    target_id = _require_string(plan.get("target_id"), "target_id", errors)
    _require_string(plan.get("version"), "version", errors)
    status = str(plan.get("status") or "")
    if status not in PLAN_STATUSES:
        errors.append(f"status must be one of {sorted(PLAN_STATUSES)}")
    if plan_id and target_id and plan_id != f"TP-{target_id}":
        errors.append("plan_id must equal TP-{target_id}")
    if plan.get("execution_mode") != "SEQUENTIAL":
        errors.append("execution_mode must be SEQUENTIAL")
    if plan.get("generated_by_agent") != "ImplementationPlanAgent":
        errors.append("generated_by_agent must be ImplementationPlanAgent")
    work_mode = plan.get("work_mode")
    if not isinstance(work_mode, dict) or not isinstance(work_mode.get("minimal"), bool):
        errors.append("work_mode.minimal must be boolean")
        minimal = False
        architecture_review = False
    else:
        minimal = bool(work_mode.get("minimal"))
        if not isinstance(work_mode.get("architecture_review"), bool):
            errors.append("work_mode.architecture_review must be boolean")
            architecture_review = False
        else:
            architecture_review = bool(work_mode.get("architecture_review"))
    expected_reviewers = MINIMAL_REVIEWERS if minimal else STANDARD_REVIEWERS
    reviewers = _require_string_list(plan.get("required_reviewers"), "required_reviewers", errors)
    if reviewers[: len(expected_reviewers)] != expected_reviewers:
        errors.append(f"required_reviewers must start with {expected_reviewers}")
    known_agents = agent_context.known_agents()
    for reviewer in reviewers:
        if reviewer not in known_agents:
            errors.append(f"required_reviewers contains unknown Agent: {reviewer}")
    inputs = plan.get("input_revisions")
    if not isinstance(inputs, dict):
        errors.append("input_revisions must be an object")
    else:
        for key in ("requirement_analysis", "business_model", "design"):
            _require_string(inputs.get(key), f"input_revisions.{key}", errors)
        if minimal:
            if inputs.get("test_design") not in (None, "", "无"):
                errors.append("minimal mode input_revisions.test_design must be empty or 无")
        else:
            _require_string(inputs.get("test_design"), "input_revisions.test_design", errors)
    tasks = plan.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        errors.append("tasks must contain at least one development task")
        tasks = []
    task_ids: list[str] = []
    dependency_map: dict[str, list[str]] = {}
    trace_seen: set[str] = set()
    for index, task in enumerate(tasks):
        prefix = f"tasks[{index}]"
        if not isinstance(task, dict):
            errors.append(f"{prefix} must be an object")
            continue
        allowed_task = {
            "task_id", "title", "goal", "implementation", "acceptance_criteria",
            "depends_on", "owner_agent", "reviewer_agents", "validation_commands",
            "expected_results", "deliverables", "stop_conditions", "trace_ids",
            "flow_refs", "flow_step_refs", "risk_triggers", "git_checkpoint_required",
        }
        unknown_task = sorted(set(task) - allowed_task)
        if unknown_task:
            errors.append(f"{prefix} contains unknown fields: {unknown_task}")
        task_id = _require_string(task.get("task_id"), f"{prefix}.task_id", errors)
        if task_id and not TASK_ID_RE.fullmatch(task_id):
            errors.append(f"{prefix}.task_id is invalid: {task_id}")
        task_ids.append(task_id)
        _require_string(task.get("title"), f"{prefix}.title", errors, min_length=4)
        _require_string(task.get("goal"), f"{prefix}.goal", errors, min_length=12)
        if task.get("owner_agent") != "DevelopAgent":
            errors.append(f"{prefix}.owner_agent must be DevelopAgent")
        task_reviewers = _require_string_list(task.get("reviewer_agents"), f"{prefix}.reviewer_agents", errors)
        for reviewer in task_reviewers:
            if reviewer not in known_agents:
                errors.append(f"{prefix}.reviewer_agents contains unknown Agent: {reviewer}")
        for reviewer in expected_reviewers:
            if reviewer not in task_reviewers:
                errors.append(f"{prefix}.reviewer_agents missing required {reviewer}")
        implementation = task.get("implementation")
        if not isinstance(implementation, dict):
            errors.append(f"{prefix}.implementation must be an object")
        else:
            allowed_impl = {
                "owner_module", "owner_component", "steps", "affected_files", "forbidden_changes",
                "implementation_decision_ref", "strategy", "existing_code_refs",
                "shared_logic_disposition", "shared_logic_owner",
                "compatibility_verifications", "source_doc_refs",
            }
            unknown_impl = sorted(set(implementation) - allowed_impl)
            if unknown_impl:
                errors.append(f"{prefix}.implementation contains unknown fields: {unknown_impl}")
            _require_string(implementation.get("owner_module"), f"{prefix}.implementation.owner_module", errors)
            _require_string(implementation.get("owner_component"), f"{prefix}.implementation.owner_component", errors)
            _require_string_list(implementation.get("steps"), f"{prefix}.implementation.steps", errors, min_items=2)
            _require_string_list(implementation.get("affected_files"), f"{prefix}.implementation.affected_files", errors)
            _require_string_list(implementation.get("forbidden_changes"), f"{prefix}.implementation.forbidden_changes", errors)
            if strategy_contract_enabled:
                decision_ref = _require_string(
                    implementation.get("implementation_decision_ref"),
                    f"{prefix}.implementation.implementation_decision_ref",
                    errors,
                )
                if decision_ref and not implementation_strategy.DECISION_ID_RE.search(decision_ref):
                    errors.append(f"{prefix}.implementation.implementation_decision_ref must reference IMPL-DEC-*")
                strategy = _require_string(implementation.get("strategy"), f"{prefix}.implementation.strategy", errors)
                if strategy and strategy not in implementation_strategy.DECISION_ORDER:
                    errors.append(
                        f"{prefix}.implementation.strategy must be one of {list(implementation_strategy.DECISION_ORDER)}"
                    )
                _require_string_list(
                    implementation.get("existing_code_refs"),
                    f"{prefix}.implementation.existing_code_refs",
                    errors,
                )
                disposition = _require_string(
                    implementation.get("shared_logic_disposition"),
                    f"{prefix}.implementation.shared_logic_disposition",
                    errors,
                )
                if disposition and disposition not in implementation_strategy.SHARED_LOGIC_DISPOSITIONS:
                    errors.append(
                        f"{prefix}.implementation.shared_logic_disposition must be one of "
                        f"{sorted(implementation_strategy.SHARED_LOGIC_DISPOSITIONS)}"
                    )
                _require_string(implementation.get("shared_logic_owner"), f"{prefix}.implementation.shared_logic_owner", errors)
                _require_string_list(
                    implementation.get("compatibility_verifications"),
                    f"{prefix}.implementation.compatibility_verifications",
                    errors,
                )
                source_refs = _require_string_list(
                    implementation.get("source_doc_refs"),
                    f"{prefix}.implementation.source_doc_refs",
                    errors,
                )
                if source_refs and not any(
                    re.search(r"(?:_design|design)\.md#implementation-strategy-decisions\b", ref, re.I)
                    for ref in source_refs
                ):
                    errors.append(
                        f"{prefix}.implementation.source_doc_refs must link a design document's implementation-strategy-decisions anchor"
                    )
        acceptance = task.get("acceptance_criteria")
        if not isinstance(acceptance, list) or not acceptance:
            errors.append(f"{prefix}.acceptance_criteria must contain at least one item")
        else:
            acceptance_ids: list[str] = []
            for criterion_index, criterion in enumerate(acceptance):
                criterion_prefix = f"{prefix}.acceptance_criteria[{criterion_index}]"
                if not isinstance(criterion, dict):
                    errors.append(f"{criterion_prefix} must be an object")
                    continue
                if set(criterion) - {"id", "assertion"}:
                    errors.append(f"{criterion_prefix} contains unknown fields: {sorted(set(criterion)-{'id','assertion'})}")
                criterion_id = _require_string(criterion.get("id"), f"{criterion_prefix}.id", errors)
                assertion = _require_string(criterion.get("assertion"), f"{criterion_prefix}.assertion", errors, min_length=12)
                if criterion_id:
                    acceptance_ids.append(criterion_id)
                if assertion and not re.search(r"通过|失败|拒绝|生成|保持|不存在|一致|精确|不得|能够|必须", assertion):
                    errors.append(f"{criterion_prefix}.assertion must state an observable or decidable result")
            if len(acceptance_ids) != len(set(acceptance_ids)):
                errors.append(f"{prefix}.acceptance_criteria ids must be unique")
        deps = task.get("depends_on")
        if not isinstance(deps, list) or any(not isinstance(item, str) for item in deps):
            errors.append(f"{prefix}.depends_on must be a string array")
            deps = []
        dependency_map[task_id] = list(deps)
        _require_string_list(task.get("validation_commands"), f"{prefix}.validation_commands", errors)
        _require_string_list(task.get("expected_results"), f"{prefix}.expected_results", errors)
        _require_string_list(task.get("deliverables"), f"{prefix}.deliverables", errors)
        _require_string_list(task.get("stop_conditions"), f"{prefix}.stop_conditions", errors)
        trace_ids = _require_string_list(task.get("trace_ids"), f"{prefix}.trace_ids", errors)
        trace_seen.update(trace_ids)
        if task.get("git_checkpoint_required") is not True:
            errors.append(f"{prefix}.git_checkpoint_required must be true")
    if len(task_ids) != len(set(task_ids)):
        errors.append("task_id values must be unique")
    task_set = set(task_ids)
    for task_id, deps in dependency_map.items():
        for dep in deps:
            if dep not in task_set:
                errors.append(f"task {task_id} depends on unknown task {dep}")
            if dep == task_id:
                errors.append(f"task {task_id} must not depend on itself")
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(task_id: str) -> None:
        if task_id in visited:
            return
        if task_id in visiting:
            errors.append(f"task dependency cycle detected at {task_id}")
            return
        visiting.add(task_id)
        for dep in dependency_map.get(task_id, []):
            if dep in task_set:
                visit(dep)
        visiting.remove(task_id)
        visited.add(task_id)
    for task_id in task_ids:
        visit(task_id)
    revision = str(plan.get("revision") or "")
    revision_no = plan.get("revision_no")
    if require_revision or revision:
        match = PLAN_REVISION_RE.fullmatch(revision)
        if not match:
            errors.append("revision must match TP-{TARGET}-RNN@{12hex}")
        else:
            if not isinstance(revision_no, int) or revision_no < 1 or int(match.group(1)) != revision_no:
                errors.append("revision_no must match revision")
            if match.group(2) != compute_digest(plan):
                errors.append("revision digest does not match current plan content; run revise or seal")
    return errors


def validate_review_record(record: dict[str, Any], *, index: int) -> list[str]:
    prefix = f"review[{index}]"
    errors: list[str] = []
    allowed = {
        "schema_version", "review_id", "plan_revision", "review_round", "reviewer_agent",
        "task_ids", "result", "findings", "summary", "created_at",
    }
    unknown = sorted(set(record) - allowed)
    if unknown:
        errors.append(f"{prefix} contains unknown fields: {unknown}")
    if record.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"{prefix}.schema_version must be {SCHEMA_VERSION}")
    if not REVIEW_ID_RE.fullmatch(str(record.get("review_id") or "")):
        errors.append(f"{prefix}.review_id is invalid")
    if not PLAN_REVISION_RE.fullmatch(str(record.get("plan_revision") or "")):
        errors.append(f"{prefix}.plan_revision is invalid")
    if record.get("result") not in REVIEW_RESULTS:
        errors.append(f"{prefix}.result is invalid")
    if not isinstance(record.get("task_ids"), list) or not record.get("task_ids"):
        errors.append(f"{prefix}.task_ids must be non-empty")
    findings = record.get("findings")
    if not isinstance(findings, list):
        errors.append(f"{prefix}.findings must be an array")
        findings = []
    if record.get("result") == "PASSED" and findings:
        errors.append(f"{prefix} PASSED must not contain findings")
    if record.get("result") != "PASSED" and not findings:
        errors.append(f"{prefix} {record.get('result')} requires findings")
    for finding_index, finding in enumerate(findings):
        finding_prefix = f"{prefix}.findings[{finding_index}]"
        if not isinstance(finding, dict):
            errors.append(f"{finding_prefix} must be an object")
            continue
        if set(finding) - {"finding_id", "severity", "task_id", "field", "description", "required_change"}:
            errors.append(f"{finding_prefix} contains unknown fields")
        if not FINDING_ID_RE.fullmatch(str(finding.get("finding_id") or "")):
            errors.append(f"{finding_prefix}.finding_id is invalid")
        if finding.get("severity") not in SEVERITIES:
            errors.append(f"{finding_prefix}.severity is invalid")
        for key in ("task_id", "field", "description", "required_change"):
            _require_string(finding.get(key), f"{finding_prefix}.{key}", errors)
    return errors


def validate_all(task_dir: Path, *, require_revision: bool = False) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    plan = load_plan(task_dir)
    reviews = load_reviews(task_dir)
    errors = validate_plan(plan, require_revision=require_revision)
    for index, record in enumerate(reviews):
        errors.extend(validate_review_record(record, index=index))
    return plan, reviews, errors




def authorize(agent: str, command: str) -> None:
    permission = agent_context.command_permission("task_plan", command)
    mode = permission.get("mode")
    if mode == "STATIC":
        agent_context.authorize_static(agent, "task_plan", command)
    elif mode == "DECLARED_AGENT":
        agent_context.authorize_declared(agent, "task_plan", command)
    else:
        raise RuntimeError(f"unsupported task_plan permission mode for {command}: {mode}")


def command_init(args: argparse.Namespace) -> int:
    agent = agent_context.selected_agent(args)
    authorize(agent, "init")
    task_dir = Path(args.task_dir)
    if plan_path(task_dir).exists() and not args.force:
        try:
            existing = load_plan(task_dir)
        except ValueError:
            existing = {}
        if existing.get("tasks") not in ([], None) or set(existing) - {"schema_version", "tasks"}:
            raise ValueError(f"{PLAN_FILENAME} already exists; use --force only for an empty/unapproved draft")
    minimal = bool(args.minimal)
    architecture_review = bool(getattr(args, "architecture_review", False))
    reviewers = list(MINIMAL_REVIEWERS if minimal else STANDARD_REVIEWERS)
    plan = {
        "schema_version": SCHEMA_VERSION,
        "implementation_strategy_contract_version": implementation_strategy.CONTRACT_VERSION,
        "plan_id": f"TP-{args.target_id}",
        "target_id": args.target_id,
        "version": args.version,
        "status": "PENDING",
        "revision": "",
        "revision_no": 0,
        "supersedes_revision": "",
        "revision_reason": "",
        "input_revisions": {
            "requirement_analysis": args.requirement_analysis_revision,
            "business_model": args.business_model_revision,
            "design": args.design_revision,
            "test_design": "无" if minimal else args.test_design_revision,
        },
        "work_mode": {"minimal": minimal, "architecture_review": architecture_review},
        "execution_mode": "SEQUENTIAL",
        "generated_by_agent": "ImplementationPlanAgent",
        "required_reviewers": reviewers,
        "review_round": 0,
        "created_at": now(),
        "updated_at": now(),
        "finalized_at": "",
        "tasks": [],
    }
    task_dir.mkdir(parents=True, exist_ok=True)
    review_file = review_path(task_dir)
    if not review_file.exists():
        atomic_write_text(review_file, "")
    save_plan(task_dir, plan, [])
    print(json.dumps({"status": "PENDING", "plan": str(plan_path(task_dir)), "nextAction": "author concrete tasks, then run seal"}, ensure_ascii=False))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    agent = agent_context.selected_agent(args)
    authorize(agent, "validate")
    plan, reviews, errors = validate_all(Path(args.task_dir), require_revision=args.require_revision)
    payload = {
        "status": "PASSED" if not errors else "FAILED",
        "planRevision": plan.get("revision"),
        "planStatus": plan.get("status"),
        "taskCount": len(plan.get("tasks") or []),
        "reviewCount": len(reviews),
        "errors": errors,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


def command_seal(args: argparse.Namespace) -> int:
    agent = agent_context.selected_agent(args)
    authorize(agent, "seal")
    task_dir = Path(args.task_dir)
    plan, reviews, errors = validate_all(task_dir, require_revision=False)
    if plan.get("implementation_strategy_contract_version") != implementation_strategy.CONTRACT_VERSION:
        raise ValueError("task plan is missing implementation_strategy_contract_version; add current IMPL-DEC references before seal")
    if plan.get("status") != "PENDING" or plan.get("revision"):
        raise ValueError("seal requires an unversioned PENDING plan")
    if errors:
        raise ValueError("cannot seal invalid plan: " + "; ".join(errors))
    plan["revision_no"] = 1
    plan["revision"] = build_revision(plan, 1)
    plan["review_round"] = 1
    plan["status"] = "IN_REVIEW"
    plan["updated_at"] = now()
    save_plan(task_dir, plan, reviews)
    assignment = next_assignment(plan, reviews)
    print(json.dumps({"status": plan["status"], "revision": plan["revision"], "nextReviewer": assignment[0] if assignment else ""}, ensure_ascii=False))
    return 0


def next_review_id(reviews: list[dict[str, Any]]) -> str:
    highest = 0
    for record in reviews:
        value = str(record.get("review_id") or "")
        if REVIEW_ID_RE.fullmatch(value):
            highest = max(highest, int(value[4:]))
    return f"TPR-{highest + 1:06d}"


def command_submit_review(args: argparse.Namespace) -> int:
    agent = agent_context.selected_agent(args)
    authorize(agent, "submit-review")
    task_dir = Path(args.task_dir)
    plan, reviews, errors = validate_all(task_dir, require_revision=True)
    if errors:
        raise ValueError("cannot review invalid plan: " + "; ".join(errors))
    if plan.get("status") != "IN_REVIEW":
        raise ValueError("submit-review requires plan status IN_REVIEW")
    assignment = next_assignment(plan, reviews)
    if not assignment:
        raise ValueError("no pending Reviewer; finalize the plan")
    reviewer, assigned_task_ids = assignment
    if agent != reviewer:
        raise agent_context.AgentAuthorizationError(f"next Reviewer is {reviewer}, not {agent}")
    findings_input = json.loads(Path(args.findings_file).read_text(encoding="utf-8")) if args.findings_file else []
    if not isinstance(findings_input, list):
        raise ValueError("findings file must contain a JSON array")
    review_id = next_review_id(reviews)
    findings: list[dict[str, Any]] = []
    for index, raw in enumerate(findings_input, 1):
        if not isinstance(raw, dict):
            raise ValueError(f"finding {index} must be an object")
        task_id = str(raw.get("task_id") or "")
        if task_id not in assigned_task_ids:
            raise ValueError(f"finding {index} task_id is outside Reviewer assignment: {task_id}")
        findings.append({
            "finding_id": f"TPF-{int(review_id[4:]):06d}-{index:02d}",
            "severity": str(raw.get("severity") or "P1"),
            "task_id": task_id,
            "field": str(raw.get("field") or ""),
            "description": str(raw.get("description") or ""),
            "required_change": str(raw.get("required_change") or ""),
        })
    result = args.result
    record = {
        "schema_version": SCHEMA_VERSION,
        "review_id": review_id,
        "plan_revision": plan["revision"],
        "review_round": plan["review_round"],
        "reviewer_agent": agent,
        "task_ids": assigned_task_ids,
        "result": result,
        "findings": findings,
        "summary": args.summary,
        "created_at": now(),
    }
    review_errors = validate_review_record(record, index=len(reviews))
    if review_errors:
        raise ValueError("invalid review: " + "; ".join(review_errors))
    append_review(task_dir, record)
    reviews.append(record)
    if result == "NEEDS_CHANGES":
        plan["status"] = "REWORK"
    elif result == "BLOCKED":
        plan["status"] = "BLOCKED"
    plan["updated_at"] = now()
    save_plan(task_dir, plan, reviews)
    next_item = next_assignment(plan, reviews) if plan["status"] == "IN_REVIEW" else None
    print(json.dumps({"reviewId": review_id, "result": result, "planStatus": plan["status"], "nextReviewer": next_item[0] if next_item else ""}, ensure_ascii=False))
    return 0


def command_revise(args: argparse.Namespace) -> int:
    agent = agent_context.selected_agent(args)
    authorize(agent, "revise")
    task_dir = Path(args.task_dir)
    plan = load_plan(task_dir)
    reviews = load_reviews(task_dir)
    if plan.get("status") not in {"REWORK", "BLOCKED"}:
        raise ValueError("revise requires REWORK or BLOCKED status")
    current = current_revision_reviews(plan, reviews)
    if not any(record.get("result") in {"NEEDS_CHANGES", "BLOCKED"} for record in current):
        raise ValueError("revise requires a current-revision Review finding")
    errors = validate_plan(plan, require_revision=False)
    errors = [error for error in errors if not error.startswith("revision digest does not match")]
    if errors:
        raise ValueError("cannot revise invalid task content: " + "; ".join(errors))
    old_revision = str(plan.get("revision") or "")
    old_match = PLAN_REVISION_RE.fullmatch(old_revision)
    if old_match and old_match.group(2) == compute_digest(plan):
        raise ValueError("revise requires actual task-plan content changes that address the current Review findings")
    revision_no = int(plan.get("revision_no") or 0) + 1
    plan["supersedes_revision"] = old_revision
    plan["revision_no"] = revision_no
    plan["revision_reason"] = args.reason
    plan["revision"] = build_revision(plan, revision_no)
    plan["review_round"] = int(plan.get("review_round") or 0) + 1
    plan["status"] = "IN_REVIEW"
    plan["updated_at"] = now()
    save_plan(task_dir, plan, reviews)
    assignment = next_assignment(plan, reviews)
    print(json.dumps({"status": plan["status"], "revision": plan["revision"], "supersedes": old_revision, "nextReviewer": assignment[0] if assignment else ""}, ensure_ascii=False))
    return 0


def command_finalize(args: argparse.Namespace) -> int:
    agent = agent_context.selected_agent(args)
    authorize(agent, "finalize")
    task_dir = Path(args.task_dir)
    plan, reviews, errors = validate_all(task_dir, require_revision=True)
    if errors:
        raise ValueError("cannot finalize invalid plan: " + "; ".join(errors))
    if plan.get("status") != "IN_REVIEW":
        raise ValueError("finalize requires plan status IN_REVIEW")
    assignment = next_assignment(plan, reviews)
    if assignment:
        raise ValueError(f"pending Reviewer: {assignment[0]}")
    latest = latest_by_reviewer(plan, reviews)
    for reviewer, task_ids in required_review_assignments(plan):
        record = latest.get(reviewer)
        if not record or record.get("result") != "PASSED" or record.get("task_ids") != task_ids:
            raise ValueError(f"Reviewer {reviewer} has not PASSED the exact current task assignment")
    plan["status"] = "PASSED"
    plan["finalized_at"] = now()
    plan["updated_at"] = plan["finalized_at"]
    save_plan(task_dir, plan, reviews)
    print(json.dumps({"status": "PASSED", "revision": plan["revision"], "taskCount": len(plan.get("tasks") or []), "reviewRound": plan["review_round"]}, ensure_ascii=False))
    return 0


def command_status(args: argparse.Namespace) -> int:
    agent = agent_context.selected_agent(args)
    authorize(agent, "status")
    task_dir = Path(args.task_dir)
    plan, reviews, errors = validate_all(task_dir, require_revision=bool(load_plan(task_dir).get("revision")))
    assignment = next_assignment(plan, reviews) if not errors and plan.get("status") == "IN_REVIEW" else None
    payload = {
        "status": "INVALID" if errors else plan.get("status"),
        "revision": plan.get("revision"),
        "taskCount": len(plan.get("tasks") or []),
        "reviewRound": plan.get("review_round"),
        "nextReviewer": assignment[0] if assignment else "",
        "nextTaskIds": assignment[1] if assignment else [],
        "errors": errors,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


def _task_query_summary(task: dict[str, Any]) -> dict[str, Any]:
    return {
        "task_id": task.get("task_id"),
        "title": task.get("title"),
        "depends_on": task.get("depends_on") or [],
        "owner_agent": task.get("owner_agent"),
        "reviewer_agents": task.get("reviewer_agents") or [],
    }


def command_query(args: argparse.Namespace) -> int:
    """Return a bounded plan index or one selected task without exposing the raw plan."""
    agent = agent_context.selected_agent(args)
    authorize(agent, "query")
    task_dir = Path(args.task_dir)
    plan = load_plan(task_dir)
    reviews = load_reviews(task_dir)
    errors = validate_plan(plan, require_revision=bool(plan.get("revision")))
    for index, record in enumerate(reviews):
        errors.extend(validate_review_record(record, index=index))
    payload: dict[str, Any] = {
        "status": "INVALID" if errors else "PASSED",
        "source": str(plan_path(task_dir)),
        "revision": plan.get("revision"),
        "digest": compute_digest(plan),
        "planId": plan.get("plan_id"),
        "targetId": plan.get("target_id"),
        "version": plan.get("version"),
        "planStatus": plan.get("status"),
        "reviewRound": plan.get("review_round"),
        "taskCount": len(plan.get("tasks") or []),
        "query": {
            "taskId": args.task_id or "",
            "fields": list(args.field) if args.field else list(DEFAULT_TASK_QUERY_FIELDS),
            "maxBytes": args.max_bytes,
        },
        "errors": errors,
    }
    if args.task_id:
        task = next(
            (item for item in plan.get("tasks") or []
             if isinstance(item, dict) and item.get("task_id") == args.task_id),
            None,
        )
        if task is None:
            payload["status"] = "NOT_FOUND"
            payload["errors"] = [f"task_id not found: {args.task_id}"]
            bounded_selection(payload, max_bytes=args.max_bytes)
            print(json.dumps(payload, ensure_ascii=False, indent=2))
            return 1
        fields = list(args.field) if args.field else list(DEFAULT_TASK_QUERY_FIELDS)
        payload["task"] = {field: task.get(field) for field in fields if field in task}
        payload["selectedFields"] = fields
    else:
        if args.field:
            raise ValueError("--field requires --task-id; query the task index without field selection")
        payload["tasks"] = [_task_query_summary(item) for item in plan.get("tasks") or [] if isinstance(item, dict)]
        payload["selectedFields"] = ["task_id", "title", "depends_on", "owner_agent", "reviewer_agents"]
    bounded_selection(payload, max_bytes=args.max_bytes)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="initialize wk -tp task-plan files")
    agent_context.add_agent_argument(init)
    init.add_argument("--task-dir", required=True)
    init.add_argument("--version", required=True)
    init.add_argument("--target-id", required=True)
    init.add_argument("--requirement-analysis-revision", required=True)
    init.add_argument("--business-model-revision", required=True)
    init.add_argument("--design-revision", required=True)
    init.add_argument("--test-design-revision", default="")
    init.add_argument("--minimal", action="store_true")
    init.add_argument("--architecture-review", action="store_true", help="plan wk -ar skeleton and concrete implementation iterations")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=command_init)

    validate = subparsers.add_parser("validate", help="validate task plan and Review history")
    agent_context.add_agent_argument(validate)
    validate.add_argument("--task-dir", required=True)
    validate.add_argument("--require-revision", action="store_true")
    validate.set_defaults(func=command_validate)

    seal = subparsers.add_parser("seal", help="freeze the first task-plan revision and begin serial Review")
    agent_context.add_agent_argument(seal)
    seal.add_argument("--task-dir", required=True)
    seal.set_defaults(func=command_seal)

    submit = subparsers.add_parser("submit-review", help="submit the next serial task-plan Review")
    agent_context.add_agent_argument(submit)
    submit.add_argument("--task-dir", required=True)
    submit.add_argument("--result", required=True, choices=sorted(REVIEW_RESULTS))
    submit.add_argument("--findings-file", default="")
    submit.add_argument("--summary", required=True)
    submit.set_defaults(func=command_submit_review)

    revise = subparsers.add_parser("revise", help="create a new task-plan revision after Review findings")
    agent_context.add_agent_argument(revise)
    revise.add_argument("--task-dir", required=True)
    revise.add_argument("--reason", required=True)
    revise.set_defaults(func=command_revise)

    finalize = subparsers.add_parser("finalize", help="mark the current task-plan revision PASSED after all Reviews")
    agent_context.add_agent_argument(finalize)
    finalize.add_argument("--task-dir", required=True)
    finalize.set_defaults(func=command_finalize)

    status = subparsers.add_parser("status", help="show current task-plan and Review state")
    agent_context.add_agent_argument(status)
    status.add_argument("--task-dir", required=True)
    status.set_defaults(func=command_status)

    query = subparsers.add_parser("query", help="query a bounded plan index or selected task fields")
    agent_context.add_agent_argument(query)
    query.add_argument("--task-dir", required=True)
    query.add_argument("--task-id", default="")
    query.add_argument("--field", action="append", choices=sorted(TASK_QUERY_FIELDS), default=[])
    query.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_SELECTION_BYTES)
    query.set_defaults(func=command_query)
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except (agent_context.AgentAuthorizationError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
