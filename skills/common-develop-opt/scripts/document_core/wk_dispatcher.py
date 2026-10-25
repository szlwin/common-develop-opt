"""Canonical ``wk`` parser and dispatcher independent of compat modules."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from argparse import ArgumentParser, Namespace
from typing import Callable, Iterable

COMMANDS = ("start", "status", "doctor", "next", "begin", "progress", "finish", "record-result", "step", "confirm", "reconcile", "close", "document-archive", "impact", "delivery", "env", "context", "packs", "help")
def _contract_agents() -> tuple[str, ...]:
    path = Path(__file__).resolve().parents[2] / "assets" / "runtime" / "runtime-contract.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        agents = value.get("agents", [])
        if isinstance(agents, list) and all(isinstance(item, str) for item in agents):
            return tuple(agents)
    except (OSError, json.JSONDecodeError):
        pass
    return ("ProjectManagerAgent",)


AGENTS = _contract_agents()
SKIPPABLE_PHASES = ("code_review", "testing")
CHANGE_CLASSES = ("PROCESS_ONLY", "DOCUMENTATION_ONLY", "TEST_ONLY", "CONFIG", "PRODUCTION", "SEMANTIC_AUTHORITY", "COMPOSITE", "UNKNOWN")
TRI_STATE = ("TRUE", "FALSE", "UNKNOWN")
CATEGORY_ORDER = ["request", "mode", "action", "orchestration", "selector"]


class DispatcherError(ValueError):
    """Raised when no canonical handler is registered for a parsed command."""


def _agent(parser: ArgumentParser) -> None:
    parser.add_argument("-g", "--agent", default="ProjectManagerAgent", choices=sorted(AGENTS))


def _intake(parser: ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("-b", "--bug")
    group.add_argument("-f", "--feature")
    parser.add_argument("--classification", choices=["new-requirement", "requirement-change", "simple-optimization", "needs-confirmation"], default="")
    parser.add_argument("--related-requirement", default=""); parser.add_argument("--related-feature", default=""); parser.add_argument("--reason", default=""); parser.add_argument("--evidence-ref", action="append", default=[])
    for flag, dest in (("business-logic", "business_logic"), ("data-model", "data_model"), ("api-contract", "api_contract"), ("permission", "permission"), ("workflow", "workflow"), ("persistence", "persistence"), ("external-side-effect", "external_side_effect"), ("migration", "migration"), ("dependency", "dependency")):
        parser.add_argument(f"--{flag}-impact", dest=f"{dest}_impact", choices=["NONE", "CHANGED", "UNKNOWN"], default="UNKNOWN")


def _common(parser: ArgumentParser, task_dir: bool = False) -> None:
    _agent(parser)
    if task_dir: parser.add_argument("--task-dir", required=True)


def build_parser(
    handlers: dict[str, Callable[[Namespace], int]] | None = None,
    *,
    handler_attribute: str = "_handler",
    description: str | None = None,
) -> ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__ if description is None else description); sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start"); _agent(start); start.add_argument("--doc-root", required=True); start.add_argument("--version", required=True); _intake(start)
    for flag in ("changed-scope", "changed-ref"):
        start.add_argument(f"--{flag}", action="append", default=[])
    for flag in ("graph", "constraints", "current-profile", "override-reason"):
        start.add_argument(f"--{flag}", default="")
    for positive, negative, dest in (("-mc", "--no-mc", "model_code"), ("-v", "--no-v", "page_design"), ("-min", "--no-min", "minimal"), ("-auto", "--no-auto", "auto"), ("-ar", "--no-ar", "architecture_review"), ("-gc", "--no-gc", "git_checkpoint"), ("-gp", "--no-gp", "git_push"), ("-review-only", "--no-review-only", "review_only")):
        group = start.add_mutually_exclusive_group()
        group.add_argument(positive, dest=dest, action="store_const", const=True, default=None)
        group.add_argument(negative, dest=dest, action="store_const", const=False)
    lightweight = start.add_mutually_exclusive_group()
    lightweight.add_argument("-ws", dest="lightweight", action="store_const", const=True, default=None)
    lightweight.add_argument("-wc", "--no-ws", dest="lightweight", action="store_const", const=False)
    sync = start.add_mutually_exclusive_group()
    sync.add_argument("--sync-existing-tasks", dest="sync_existing_tasks", action="store_true", default=True)
    sync.add_argument("--no-sync-existing-tasks", dest="sync_existing_tasks", action="store_false")
    start.add_argument("--exploration", action="store_true"); start.add_argument("--apply", action="store_true"); start.add_argument("--json", action="store_true")
    status = sub.add_parser("status"); _common(status, True); group = status.add_mutually_exclusive_group(); group.add_argument("--summary", action="store_true"); group.add_argument("--full", action="store_true"); status.add_argument("--json", action="store_true")
    doctor = sub.add_parser("doctor"); _common(doctor); doctor.add_argument("--doc-root", required=True); doctor.add_argument("--version", required=True); doctor.add_argument("--json", action="store_true"); doctor.add_argument("--fix", action="store_true"); doctor.add_argument("--apply", action="store_true")
    nxt = sub.add_parser("next"); _common(nxt, True); nxt.add_argument("--explain", action="store_true"); nxt.add_argument("--impact-plan", default=""); nxt.add_argument("--json", action="store_true")
    begin = sub.add_parser("begin"); _common(begin, True); begin.add_argument("--task-id", default=""); begin.add_argument("--apply", action="store_true"); begin.add_argument("--risk-facts", default=""); begin.add_argument("--json", action="store_true")
    progress = sub.add_parser("progress"); _common(progress, True); progress.add_argument("--apply", action="store_true"); progress.add_argument("--plan-digest", default=""); progress.add_argument("--risk-facts", default=""); progress.add_argument("--json", action="store_true")
    finish = sub.add_parser("finish"); _common(finish, True); finish.add_argument("--task-id", default=""); finish.add_argument("--revision", default=""); finish.add_argument("--cwd", default=""); finish.add_argument("--apply", action="store_true"); finish.add_argument("--risk-facts", default=""); finish.add_argument("--json", action="store_true")
    record = sub.add_parser("record-result"); _common(record, True); record.add_argument("--attempt-id", required=True); record.add_argument("--command-id", required=True); record.add_argument("--result-file", required=True); record.add_argument("--revision", required=True); record.add_argument("--phase", required=True); record.add_argument("--scope-ref", action="append", default=[]); record.add_argument("--acceptance-id", default=""); record.add_argument("--json", action="store_true")
    step = sub.add_parser("step"); _common(step, True); mode = step.add_mutually_exclusive_group(); mode.add_argument("--preview", action="store_true"); mode.add_argument("--apply", action="store_true"); step.add_argument("--plan-digest", default=""); step.add_argument("--json", action="store_true")
    confirm = sub.add_parser("confirm"); _common(confirm, True); target = confirm.add_mutually_exclusive_group(required=True); target.add_argument("--until", default=""); target.add_argument("--reopen-from", default=""); target.add_argument("--skip-phase", default="", choices=SKIPPABLE_PHASES); confirm.add_argument("--changed-phase", default=""); confirm.add_argument("--source-revision", default=""); confirm.add_argument("--reason", required=True); cmode = confirm.add_mutually_exclusive_group(); cmode.add_argument("--preview", action="store_true"); cmode.add_argument("--apply", action="store_true"); confirm.add_argument("--json", action="store_true")
    reconcile = sub.add_parser("reconcile"); _common(reconcile, True); reconcile.add_argument("--apply", action="store_true"); reconcile.add_argument("--json", action="store_true"); reconcile.add_argument("--impact-plan", default="")
    close = sub.add_parser("close"); _common(close, True); close.add_argument("--apply", action="store_true"); close.add_argument("--json", action="store_true"); close.add_argument("--impact-plan", default=""); close.add_argument("--delivery-results", default="")
    archive = sub.add_parser("document-archive"); _common(archive); archive.add_argument("-wd", action="store_true"); archive.add_argument("--doc-root", default="project_doc"); archive.add_argument("--version", required=True); archive.add_argument("--module", required=True); archive.add_argument("--changeset", action="append"); archive.add_argument("--apply", action="store_true"); archive.add_argument("--audit-bundle", action="store_true"); archive.add_argument("--json", action="store_true")
    impact = sub.add_parser("impact"); _common(impact, True)
    for flag in ("phase", "base-revision", "revision", "change-class", "adapter-result", "graph", "project-root", "version", "output"): impact.add_argument(f"--{flag}", default="")
    for flag in ("changed-scope", "changed-path", "profile", "required-capability", "changed-ref", "affected-task-id", "affected-trace-id", "affected-acceptance-id"): impact.add_argument(f"--{flag}", action="append", default=[])
    impact.add_argument("--semantic-artifact-changed", default=None); impact.add_argument("--production-changed", default=None); impact.add_argument("--json", action="store_true")
    delivery = sub.add_parser("delivery"); _common(delivery); delivery.add_argument("--action", choices=["plan", "verify"], required=True); delivery.add_argument("--project-root", default="."); delivery.add_argument("--version", default=""); delivery.add_argument("--plan", default=""); delivery.add_argument("--results", default=""); delivery.add_argument("--output", default=""); delivery.add_argument("--json", action="store_true")
    env = sub.add_parser("env"); _common(env); env.add_argument("--project-root", required=True); env.add_argument("--version", default=""); env.add_argument("--check", action="store_true"); env.add_argument("--impact-adapter-result", default=""); env.add_argument("--json", action="store_true")
    context = sub.add_parser("context"); _common(context, True); context.add_argument("--task-id", default=""); query = context.add_mutually_exclusive_group(); query.add_argument("--trace-id", default=""); query.add_argument("--acceptance-id", default=""); context.add_argument("--json", action="store_true")
    packs = sub.add_parser("packs"); _common(packs); packs.add_argument("--task-type", required=True); packs.add_argument("--phase", default=""); packs.add_argument("--json", action="store_true")
    help_parser = sub.add_parser("help"); _common(help_parser); selector = help_parser.add_mutually_exclusive_group(); selector.add_argument("--flag", default=""); selector.add_argument("--category", choices=CATEGORY_ORDER, default=""); selector.add_argument("--script", default=""); help_parser.add_argument("--command", dest="script_command", default=""); help_parser.add_argument("--json", action="store_true")
    handlers = handlers or {}
    for command, item in sub.choices.items():
        item.set_defaults(**{handler_attribute: handlers.get(command)})
    return parser


def dispatch(argv: Iterable[str], handlers: dict[str, Callable[[Namespace], int]]) -> int:
    args = build_parser(handlers).parse_args(list(argv)); handler = getattr(args, "_handler", None)
    if handler is None: raise DispatcherError(f"canonical handler not registered: {args.command}")
    return int(handler(args))


__all__ = ["AGENTS", "CATEGORY_ORDER", "COMMANDS", "CHANGE_CLASSES", "DispatcherError", "TRI_STATE", "build_parser", "dispatch"]
