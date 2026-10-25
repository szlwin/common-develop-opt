#!/usr/bin/env python3
"""Shared current-Agent selection and authorization for common-develop scripts."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Iterable

import contract_normalization


ROOT = Path(__file__).resolve().parent.parent
RUNTIME_CONTRACT_PATH = ROOT / "assets" / "runtime-contract.json"

try:
    RUNTIME_CONTRACT: dict[str, Any] = contract_normalization.load_runtime_contract(RUNTIME_CONTRACT_PATH)
except contract_normalization.ContractNormalizationError as exc:  # package corruption; checked by check_skill.py
    raise RuntimeError(f"cannot load common-develop runtime contract: {exc}") from exc


class AgentAuthorizationError(ValueError):
    """Raised when the selected Agent is unknown or not authorized for an operation."""


def known_agents() -> set[str]:
    values = RUNTIME_CONTRACT.get("agents", [])
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise RuntimeError("runtime contract agents must be a string array")
    return set(values)


def default_agent() -> str:
    selector = RUNTIME_CONTRACT.get("commandInterface", {}).get("currentAgent", {})
    value = selector.get("default")
    if not isinstance(value, str) or value not in known_agents():
        raise RuntimeError("runtime contract current Agent default is invalid")
    return value



def project_manager_agents() -> set[str]:
    values = RUNTIME_CONTRACT.get("projectManagement", {}).get("allowedAgents", [])
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise RuntimeError("runtime contract projectManagement.allowedAgents must be a string array")
    result = set(values)
    if not result or not result.issubset(known_agents()):
        raise RuntimeError("runtime contract projectManagement.allowedAgents is invalid")
    return result

def add_agent_argument(parser: argparse.ArgumentParser) -> None:
    """Add the canonical current-Agent selector to one CLI parser."""

    parser.add_argument(
        "-g",
        "--agent",
        default=default_agent(),
        choices=sorted(known_agents()),
        help="current common-develop Agent; defaults to ProjectManagerAgent",
    )


def selected_agent(args: argparse.Namespace) -> str:
    value = getattr(args, "agent", None) or default_agent()
    if value not in known_agents():
        raise AgentAuthorizationError(f"unknown current Agent: {value}")
    return value


def command_permission(script: str, command: str) -> dict[str, Any]:
    permissions = RUNTIME_CONTRACT.get("scriptPermissions", {})
    key = f"{script}.{command}"
    definition = permissions.get(key)
    if not isinstance(definition, dict):
        raise RuntimeError(f"runtime contract has no script permission: {key}")
    return definition


def static_allowed_agents(script: str, command: str) -> set[str]:
    definition = command_permission(script, command)
    values = definition.get("allowedAgents")
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        return set()
    return set(values)


def authorize_static(agent: str, script: str, command: str) -> None:
    definition = command_permission(script, command)
    mode = definition.get("mode")
    if mode != "STATIC":
        raise RuntimeError(f"script permission {script}.{command} is not STATIC")
    allowed = static_allowed_agents(script, command)
    if agent not in allowed:
        raise AgentAuthorizationError(
            f"Agent {agent} is not authorized for {script}.{command}; allowed={sorted(allowed)}"
        )




def authorize_declared(agent: str, script: str, command: str) -> None:
    """Authorize any Agent declared by the current runtime contract for a read-only command."""

    definition = command_permission(script, command)
    if definition.get("mode") != "DECLARED_AGENT":
        raise RuntimeError(f"script permission {script}.{command} is not DECLARED_AGENT")
    if agent not in known_agents():
        raise AgentAuthorizationError(f"unknown current Agent: {agent}")

def authorize_command(
    args: argparse.Namespace,
    script: str,
    command: str,
    *,
    declared: bool = False,
) -> str:
    """Select and authorize the current Agent for one CLI command.

    This is the shared, explicit authorization preamble used by command
    handlers.  Keeping the script and command identifiers at each call site
    preserves static command discovery while avoiding duplicated selection and
    authorization boilerplate.
    """

    agent = selected_agent(args)
    if declared:
        authorize_declared(agent, script, command)
    else:
        authorize_static(agent, script, command)
    return agent

def phase_definition(phase: str) -> dict[str, Any]:
    for definition in RUNTIME_CONTRACT.get("canonicalPhases", []):
        if isinstance(definition, dict) and definition.get("id") == phase:
            return definition
    raise AgentAuthorizationError(f"unknown canonical phase: {phase}")


def phase_agents(
    phase: str,
    *,
    conditional_reviewers: Iterable[str] = (),
    additional_reviewers: Iterable[str] = (),
    project_manager_agent: str = "",
) -> set[str]:
    definition = phase_definition(phase)
    result: set[str] = set()
    owner = definition.get("ownerAgent")
    if isinstance(owner, str):
        result.add(owner)
    reviewers = definition.get("requiredReviewerAgents", [])
    if isinstance(reviewers, list):
        result.update(value for value in reviewers if isinstance(value, str))
    declared_conditional = definition.get("conditionalReviewerAgents", {})
    declared_conditional_agents = set(declared_conditional) if isinstance(declared_conditional, dict) else set()
    result.update(
        value for value in conditional_reviewers
        if value in declared_conditional_agents and value in known_agents()
    )
    result.update(value for value in additional_reviewers if value in known_agents())
    if project_manager_agent in project_manager_agents():
        result.add(project_manager_agent)
    return result


def authorize_task_context(
    agent: str,
    script: str,
    command: str,
    *,
    phase: str,
    next_agent: str = "",
    conditional_reviewers: Iterable[str] = (),
    additional_reviewers: Iterable[str] = (),
    project_manager_agent: str = "",
) -> None:
    definition = command_permission(script, command)
    if definition.get("mode") != "TASK_CONTEXT":
        raise RuntimeError(f"script permission {script}.{command} is not TASK_CONTEXT")
    allowed = phase_agents(
        phase,
        conditional_reviewers=conditional_reviewers,
        additional_reviewers=additional_reviewers,
        project_manager_agent=project_manager_agent,
    )
    if next_agent in known_agents():
        allowed.add(next_agent)
    if agent not in allowed:
        raise AgentAuthorizationError(
            f"Agent {agent} is not authorized for {script}.{command} in phase {phase}; "
            f"allowed={sorted(allowed)}"
        )


def parser_has_agent_selector(parser: argparse.ArgumentParser) -> bool:
    """Return true when a parser and every subcommand expose -g/--agent."""

    def has_local_selector(current: argparse.ArgumentParser) -> bool:
        return any(
            action.dest == "agent" and "-g" in action.option_strings and "--agent" in action.option_strings
            for action in current._actions
        )

    subcommands: list[argparse.ArgumentParser] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            subcommands.extend(action.choices.values())
    if not subcommands:
        return has_local_selector(parser)
    return all(has_local_selector(subcommand) for subcommand in subcommands)
