"""Thin execution packages over reducer decisions and revision-bound context."""
from __future__ import annotations
from pathlib import Path
from typing import Any, Callable
import json

from runtime import context_resolver
from document_core import agent_context
from runtime import current_manifest
from runtime import runtime_contract
from runtime import work_mode
from runtime import request_intake
from runtime._long_task.state_access import runtime_state, runtime_tasks, runtime_issues
from .command_plan import build_action_plan, required_inputs
from .result_projection import project
from .context_cache import load as cache_load, save as cache_save, source_digests
from .risk import assess
from .command_paths import bind_commands


def stable_context(task_dir: Path, state: dict[str, Any], agent: str) -> dict[str, Any]:
    contract = runtime_contract.load_contract()
    phase = str(state.get("current_phase") or "")
    definition = next((p for p in contract["canonicalPhases"] if p["id"] == phase), {})
    routing = context_resolver.load_routing()
    known = {t for route in routing["routes"] for t in route["taskTypes"]}
    route_type = phase if phase in known else "orchestration"
    parsed = work_mode.validate_work_file(task_dir.parent.parent / "work.md")
    mode = context_resolver.ResolvedMode(**{key: getattr(parsed, key) for key in
        ("model_code", "page_design", "minimal", "auto", "architecture_review", "git_checkpoint", "git_push", "lightweight")})
    intake_path = task_dir / "request_intake.json"
    intake = request_intake.load(intake_path) if intake_path.is_file() else {}
    tasks = runtime_tasks(task_dir)
    descriptions = [intake.get("originalInput", "")]
    descriptions.extend(t.get(key, "") for t in tasks if t.get("phase") == phase
                        for key in ("title", "description", "objective"))
    prompt = "\n".join(text for text in descriptions if isinstance(text, str) and text)
    # Routing includes mode gates; skipped stages fall back to the orchestration route.
    try:
        route = context_resolver.resolve_context(route_type, prompt=prompt, mode=mode, routing=routing)
    except context_resolver.ContextResolutionError:
        route = context_resolver.resolve_context("orchestration", prompt=prompt, mode=mode, routing=routing)
    artifact = (state.get("artifact_revisions") or {}).get(phase) or {}
    allowed = []
    for name in ("begin", "next", "progress", "finish", "record-result"):
        permission = agent_context.command_permission("wk", name)
        if permission.get("mode") == "STATIC" and agent not in permission.get("allowedAgents", []):
            continue
        if name == "record-result" and agent != definition.get("ownerAgent"):
            continue
        allowed.append("wk." + name)
    return {"contractQueries": {"phase": definition}, "contextRouting": route,
            "allowedCommands": allowed,
            "stableIdIndex": {"taskIds": [t["id"] for t in tasks],
                "traceIds": sorted({a for t in tasks for a in t.get("acceptance_trace_ids", [])}),
                "acceptanceIds": current_manifest.traceability_summary(task_dir).get("acceptanceIds") or []},
            "revisionSummary": {"phase": phase, "revision": artifact.get("revision") or "",
                "iterationId": artifact.get("iteration_id") or "", "agent": agent}}


def execution_package(task_dir: Path, *, agent: str, phase: str,
                      status_reader: Callable[[Path], dict[str, Any]],
                      next_reader: Callable[..., dict[str, Any]], write_cache: bool = False,
                      risk_facts: dict[str, Any] | None = None) -> dict[str, Any]:
    state = runtime_state(task_dir)
    current_phase = str(state.get("current_phase") or "")
    artifact = (state.get("artifact_revisions") or {}).get(current_phase) or {}
    revision = str(artifact.get("revision") or "")
    # Per-agent cache avoids returning another owner's allowed context.
    profile = "CONTEXT-" + agent
    cached = cache_load(task_dir, revision, profile)
    if cached:
        context = cached["payload"]
        cache_status = "HIT"
    else:
        before = source_digests(task_dir)
        context = stable_context(task_dir, state, agent)
        cache_status = "MISS" if revision else "UNBOUND"
        if write_cache and revision and before == source_digests(task_dir):
            try:
                cache_save(task_dir, revision, profile, context)
            except OSError:
                # A disposable cache cannot undo a successful canonical action.
                cache_status = "WRITE_UNAVAILABLE"
    status = status_reader(task_dir)
    decision = next_reader(task_dir, status, agent, explain=True, impact_plan="")
    result = project(decision, action=phase)
    result["actionPlan"] = decision.get("actionPlan") or build_action_plan(decision, task_dir=str(task_dir))
    result["requiredInputs"] = required_inputs((decision.get("nextCommand") or {}).get("command"))
    result.update(phase=current_phase, revision=revision,
                  owner=result.get("requiredAgent") or status.get("nextAgent"),
                  context=context, cache={"status": cache_status, "revision": revision},
                  readOnly=not write_cache)
    intake_path = task_dir / "request_intake.json"
    intake = request_intake.load(intake_path) if intake_path.is_file() else {}
    mode = work_mode.validate_work_file(task_dir.parent.parent / "work.md")
    observations = dict(risk_facts or {})
    if observations and (not revision or observations.get("revision") != revision):
        # Stale observations may escalate but can never prove a fast path.
        observations = {"riskLevel": "HIGH"} if assess(observations)["riskLevel"] == "HIGH" else {}
    result.update(assess({"observations": observations, "intake": intake, "tasks": runtime_tasks(task_dir), "issues": runtime_issues(task_dir),
                         "openCriticalIssueIds": status.get("openCriticalIssueIds") or [],
                         "explicitMode": "MINIMAL" if mode.minimal or mode.lightweight else "STANDARD"}))
    return bind_commands(result)
