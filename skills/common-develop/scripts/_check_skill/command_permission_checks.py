"""Static checks for CLI command surfaces and permission assignments."""
from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path
from typing import Any

from _check_skill.context import ROOT
from _check_skill.model import CheckResult


SURFACE_POLICY_KEY = "scriptCommandSurfaces"
MUTABILITY_VALUES = {"READ_ONLY", "MUTATING"}
PROFILE_MUTABILITY_VALUES = MUTABILITY_VALUES | {"MIXED"}


def _static_strings(node: ast.AST, env: dict[str, list[str]]) -> list[str] | None:
    """Resolve the small set of literal forms used by the CLI parsers."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        values: list[str] = []
        for item in node.elts:
            resolved = _static_strings(item, env)
            if resolved is None:
                return None
            values.extend(resolved)
        return values
    if isinstance(node, ast.Name):
        return env.get(node.id)
    return None


class _ParserCommandVisitor(ast.NodeVisitor):
    """Collect argparse subcommands without importing or executing a module."""

    def __init__(self) -> None:
        self.commands: set[str] = set()
        self._env: dict[str, list[str]] = {}

    def visit_Assign(self, node: ast.Assign) -> None:  # noqa: N802
        values = _static_strings(node.value, self._env)
        if values is not None:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self._env[target.id] = values
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:  # noqa: N802
        values = _static_strings(node.iter, self._env)
        if values is None or not isinstance(node.target, ast.Name):
            self.generic_visit(node)
            return
        previous = self._env.get(node.target.id)
        for value in values:
            self._env[node.target.id] = [value]
            for statement in node.body:
                self.visit(statement)
        if previous is None:
            self._env.pop(node.target.id, None)
        else:
            self._env[node.target.id] = previous
        for statement in node.orelse:
            self.visit(statement)

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        if isinstance(node.func, ast.Attribute) and node.func.attr == "add_parser" and node.args:
            values = _static_strings(node.args[0], self._env)
            if values is not None:
                self.commands.update(values)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "add_argument" and node.args:
            first = _static_strings(node.args[0], self._env)
            if first == ["command"]:
                for keyword in node.keywords:
                    if keyword.arg == "choices":
                        values = _static_strings(keyword.value, self._env)
                        if values is not None:
                            self.commands.update(values)
        self.generic_visit(node)


def _script_name(path: Path) -> str:
    """Return the contract-facing script name for a repository path."""
    try:
        relative = path.relative_to(ROOT).as_posix()
    except ValueError:
        relative = path.as_posix()
    if relative == "scripts/_long_task/cli.py":
        return "long_task"
    return path.stem


def _public_script_paths() -> list[Path]:
    paths = sorted(ROOT.joinpath("scripts").glob("*.py"))
    long_task_cli = ROOT / "scripts" / "_long_task" / "cli.py"
    if long_task_cli.is_file():
        paths.append(long_task_cli)
    return paths


def _delegated_parser_paths(path: Path, tree: ast.AST) -> list[Path]:
    """Find parser builders delegated by a thin public facade.

    Public CLIs may keep their implementation in an internal module so the
    facade remains small. Static surface discovery follows explicit
    ``from ... import ...`` aliases used in ``alias.build_parser(...)``; it
    never imports or executes the target module.
    """
    imported: dict[str, Path] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            base = path.parent
            if node.module.startswith("."):
                continue
            module_path = base
            for part in node.module.split("."):
                module_path /= part
            for item in node.names:
                if item.name == "*":
                    continue
                alias = item.asname or item.name
                candidate = module_path / f"{item.name}.py"
                if candidate.is_file():
                    imported[alias] = candidate
        elif isinstance(node, ast.Import):
            for item in node.names:
                alias = item.asname or item.name.split(".")[-1]
                candidate = path.parent
                for part in item.name.split("."):
                    candidate /= part
                candidate = candidate.with_suffix(".py")
                if candidate.is_file():
                    imported[alias] = candidate
    delegated: list[Path] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "build_parser" or not isinstance(node.func.value, ast.Name):
            continue
        target = imported.get(node.func.value.id)
        if target is not None and target not in delegated:
            delegated.append(target)
    return delegated


def _parse_commands(path: Path, seen: set[Path] | None = None) -> set[str]:
    seen = set() if seen is None else seen
    if path in seen:
        return set()
    seen.add(path)
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return set()
    visitor = _ParserCommandVisitor()
    visitor.visit(tree)
    for delegated in _delegated_parser_paths(path, tree):
        visitor.commands.update(_parse_commands(delegated, seen))
    return visitor.commands


def _load_policy(contract: dict[str, Any], result: CheckResult) -> dict[str, Any]:
    policy = contract.get(SURFACE_POLICY_KEY)
    if not isinstance(policy, dict):
        result.errors.append(
            f"assets/runtime-contract.json: {SURFACE_POLICY_KEY} must be an object"
        )
        return {}
    for key, default in (("excluded", {}), ("aliases", {}), ("nonParserCommands", {}), ("mutability", {})):
        if key not in policy:
            policy[key] = default
    if not isinstance(policy["excluded"], dict):
        result.errors.append(f"{SURFACE_POLICY_KEY}.excluded must be an object")
        policy["excluded"] = {}
    if not isinstance(policy["aliases"], dict):
        result.errors.append(f"{SURFACE_POLICY_KEY}.aliases must be an object")
        policy["aliases"] = {}
    if not isinstance(policy["nonParserCommands"], dict):
        result.errors.append(f"{SURFACE_POLICY_KEY}.nonParserCommands must be an object")
        policy["nonParserCommands"] = {}
    if not isinstance(policy["mutability"], dict):
        result.errors.append(f"{SURFACE_POLICY_KEY}.mutability must be an object")
        policy["mutability"] = {}
    return policy


def _assignment_map(contract: dict[str, Any], result: CheckResult) -> dict[str, list[str]]:
    assignments = contract.get("scriptPermissionAssignments")
    profiles = contract.get("scriptPermissionProfiles")
    if not isinstance(assignments, dict):
        result.errors.append("scriptPermissionAssignments must be an object")
        return {}
    if not isinstance(profiles, dict):
        result.errors.append("scriptPermissionProfiles must be an object")
        profiles = {}
    command_profiles: dict[str, list[str]] = defaultdict(list)
    for profile_id, commands in assignments.items():
        if profile_id not in profiles:
            result.errors.append(f"command permission assignment references unknown profile: {profile_id}")
        if not isinstance(commands, list):
            result.errors.append(f"command permission assignment {profile_id} must be a list")
            continue
        for command in commands:
            if not isinstance(command, str) or not command.strip():
                result.errors.append(f"command permission assignment {profile_id} contains an invalid command")
                continue
            command_profiles[command].append(profile_id)
    for command, profile_ids in sorted(command_profiles.items()):
        if len(profile_ids) > 1:
            result.errors.append(
                f"command permission assignment is duplicated: {command} in {sorted(profile_ids)}"
            )
    return dict(command_profiles)


def _validate_mutability_policy(
    policy: dict[str, Any],
    canonical_commands: set[str],
    aliases: dict[str, Any],
    command_profiles: dict[str, list[str]],
    profiles: dict[str, Any],
    result: CheckResult,
) -> dict[str, str]:
    """Validate command mutation declarations against permission profiles.

    The command map is deliberately explicit: parser discovery establishes the
    public surface, while this map is the stable semantic declaration consumed
    by permission checks and downstream tooling.  A profile may be ``MIXED``
    when the existing agent/mode grouping intentionally contains both kinds;
    pure profiles are checked in both directions and fail closed.
    """
    raw_mutability = policy.get("mutability", {})
    mutability: dict[str, str] = {}
    for command, value in raw_mutability.items():
        if not isinstance(command, str) or not command.strip():
            result.errors.append(f"{SURFACE_POLICY_KEY}.mutability keys must be non-empty strings")
            continue
        if command in aliases:
            result.errors.append(
                f"{SURFACE_POLICY_KEY}.mutability must omit aliases (they inherit their target): {command}"
            )
        if command not in canonical_commands:
            result.errors.append(f"{SURFACE_POLICY_KEY}.mutability references unknown command: {command}")
        if value not in MUTABILITY_VALUES:
            result.errors.append(
                f"{SURFACE_POLICY_KEY}.mutability.{command} must be READ_ONLY or MUTATING"
            )
            continue
        mutability[command] = value

    missing = sorted(canonical_commands - set(mutability))
    if missing:
        result.errors.append(f"public CLI commands missing mutation declarations: {missing}")

    for command, profile_ids in sorted(command_profiles.items()):
        canonical = aliases.get(command, command)
        command_mutability = mutability.get(canonical)
        if command_mutability is None:
            continue
        for profile_id in profile_ids:
            profile = profiles.get(profile_id)
            if not isinstance(profile, dict):
                continue
            profile_mutability = profile.get("mutation")
            if profile_mutability not in PROFILE_MUTABILITY_VALUES:
                result.errors.append(
                    f"permission profile {profile_id} must declare mutation as READ_ONLY, MUTATING, or MIXED"
                )
                continue
            if profile_mutability != "MIXED" and profile_mutability != command_mutability:
                result.errors.append(
                    f"permission mutation mismatch: {command} is {command_mutability} "
                    f"but profile {profile_id} is {profile_mutability}"
                )

    return mutability


def _validate_surface_policy(
    policy: dict[str, Any],
    parser_commands: dict[str, set[str]],
    result: CheckResult,
) -> tuple[dict[str, str], dict[str, set[str]]]:
    excluded = policy.get("excluded", {})
    aliases = policy.get("aliases", {})
    configured_non_parser = policy.get("nonParserCommands", {})
    excluded_paths: dict[str, str] = {}
    for raw_path, declaration in excluded.items():
        if not isinstance(raw_path, str) or not raw_path:
            result.errors.append("scriptCommandSurfaces.excluded paths must be non-empty strings")
            continue
        path = ROOT / raw_path
        if not path.is_file():
            result.errors.append(f"script command surface exclusion path is missing: {raw_path}")
        if not isinstance(declaration, str) or not declaration.strip():
            result.errors.append(f"script command surface exclusion needs a reason: {raw_path}")
        excluded_paths[raw_path] = declaration

    non_parser: dict[str, set[str]] = {}
    for script, commands in configured_non_parser.items():
        if not isinstance(script, str) or not script:
            result.errors.append("scriptCommandSurfaces.nonParserCommands keys must be non-empty strings")
            continue
        if not isinstance(commands, list) or any(not isinstance(command, str) or not command for command in commands):
            result.errors.append(f"scriptCommandSurfaces.nonParserCommands.{script} must be a string list")
            continue
        non_parser[script] = set(commands)
        if script not in parser_commands and not any(_script_name(path) == script for path in _public_script_paths()):
            result.errors.append(f"non-parser command surface has no script: {script}")

    for alias, target in aliases.items():
        if not isinstance(alias, str) or not isinstance(target, str) or not alias or not target:
            result.errors.append("scriptCommandSurfaces.aliases must map non-empty strings")
    return excluded_paths, non_parser


def check_command_permission_consistency(contract: dict[str, Any], result: CheckResult) -> None:
    """Ensure every public argparse command has exactly one declared permission."""
    initial_errors = len(result.errors)
    policy = _load_policy(contract, result)
    command_profiles = _assignment_map(contract, result)
    profiles = contract.get("scriptPermissionProfiles")
    if not isinstance(profiles, dict):
        profiles = {}

    # Parse only files that actually expose argparse subcommands.  The scripts
    # directory also contains implementation modules, and treating every .py
    # file as a public CLI would turn internal helpers into false omissions.
    parser_commands: dict[str, set[str]] = {}
    for path in _public_script_paths():
        commands = _parse_commands(path)
        if commands:
            script = _script_name(path)
            parser_commands[script] = commands

    excluded_paths, non_parser = _validate_surface_policy(policy, parser_commands, result)
    excluded_scripts = {
        _script_name(ROOT / raw_path)
        for raw_path in excluded_paths
    }
    for script in excluded_scripts:
        parser_commands.pop(script, None)

    surface_commands: set[str] = set()
    for script, commands in parser_commands.items():
        surface_commands.update(f"{script}.{command}" for command in commands)
    surface_commands.update(f"{script}.{command}" for script, commands in non_parser.items() for command in commands)

    aliases = policy.get("aliases", {})
    alias_keys = set(aliases)
    canonical_commands = surface_commands - alias_keys
    mutability = _validate_mutability_policy(
        policy, canonical_commands, aliases, command_profiles, profiles, result
    )
    missing = sorted(command for command in canonical_commands if command not in command_profiles)
    if missing:
        result.errors.append(f"public CLI commands missing permission assignments: {missing}")

    known_commands = surface_commands
    stale = sorted(command for command in command_profiles if command not in known_commands)
    if stale:
        result.errors.append(f"permission assignments reference unknown CLI commands: {stale}")

    for alias, target in sorted(aliases.items()):
        if alias not in surface_commands:
            result.errors.append(f"command permission alias is not a discovered command: {alias}")
        if target not in canonical_commands:
            result.errors.append(f"command permission alias target is not canonical: {alias} -> {target}")
        if alias in command_profiles:
            result.errors.append(f"command permission alias must not have a direct assignment: {alias}")
        elif target not in command_profiles:
            result.errors.append(f"command permission alias target is missing an assignment: {alias} -> {target}")

    for raw_path, reason in policy.get("excluded", {}).items():
        script = _script_name(ROOT / raw_path)
        if script in parser_commands:
            result.errors.append(f"excluded command surface still discovered as public: {raw_path}")

    result.details["commandPermissionConsistency"] = {
        "publicScripts": sorted(parser_commands),
        "parserCommands": {script: sorted(commands) for script, commands in sorted(parser_commands.items())},
        "nonParserCommands": {script: sorted(commands) for script, commands in sorted(non_parser.items())},
        "aliases": dict(sorted(aliases.items())),
        "mutability": dict(sorted(mutability.items())),
        "profileMutability": {
            profile_id: profile.get("mutation")
            for profile_id, profile in sorted(profiles.items())
            if isinstance(profile, dict)
        },
        "assignedCommands": len(command_profiles),
        "surfaceCommands": len(surface_commands),
        "missingAssignments": missing,
        "staleAssignments": stale,
        "excludedSurfaces": dict(sorted(excluded_paths.items())),
    }
    if len(result.errors) == initial_errors:
        result.ok("CLI command surfaces have complete, unique permission assignments")


__all__ = ["check_command_permission_consistency"]
