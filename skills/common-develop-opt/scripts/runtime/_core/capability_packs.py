"""Load, validate, and resolve common-develop internal capability packs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from resource_paths import asset_path, formalize
from typing import Any


ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = asset_path('assets/development/capability-pack-contract.json')
RESULT_SCHEMA_PATH = asset_path('assets/development/capability-result.schema.json')
PACK_IDS = ("document-facts", "task-runtime", "review-verification")
VALID_MODES = {"READ_ONLY", "PRODUCE", "ORCHESTRATE", "GATE"}
MODE_PRIORITY = {"READ_ONLY": 1, "PRODUCE": 2, "GATE": 2, "ORCHESTRATE": 3}


class CapabilityPackError(ValueError):
    """Raised when the internal capability-pack contract is invalid."""


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CapabilityPackError(f"cannot load capability-pack asset {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CapabilityPackError(f"capability-pack asset must be an object: {path}")
    return value


def _ref_path(value: str) -> Path:
    return formalize(ROOT / value.split("#", 1)[0])


def command_surface(contract: dict[str, Any]) -> dict[str, Any]:
    """Materialize v1 decisions from the same declarations used for CLI discovery."""
    surface = contract.get("commandSurface")
    if not isinstance(surface, dict) or surface.get("version") != 1:
        raise CapabilityPackError("commandSurface.version must be 1")
    entries: dict[str, dict[str, Any]] = {}

    def add(script_id: str, decision: str, **fields: Any) -> None:
        if not isinstance(script_id, str) or not script_id.isidentifier():
            raise CapabilityPackError(f"invalid command surface script ID: {script_id}")
        if script_id in entries:
            raise CapabilityPackError(f"conflicting command surface decision: {script_id}")
        entries[script_id] = {"script": script_id, "v1Decision": decision, **fields}

    entry = contract.get("entryLayer")
    packs = contract.get("packs")
    if not isinstance(entry, dict):
        raise CapabilityPackError("entryLayer must be an object")
    if not isinstance(packs, list):
        raise CapabilityPackError("packs must be an array")
    owners = [("entryLayer", entry), *[(f"packs/{index}", pack) for index, pack in enumerate(packs)]]
    for owner, definition in owners:
        if not isinstance(definition, dict):
            raise CapabilityPackError(f"{owner} must be an object")
        if owner != "entryLayer" and "supportCliEntrypoints" in definition:
            raise CapabilityPackError(f"{owner}.supportCliEntrypoints is only supported on entryLayer")
        fields = ("cliEntrypoints", "supportCliEntrypoints") if owner == "entryLayer" else ("cliEntrypoints",)
        for field in fields:
            paths = definition.get(field)
            if not isinstance(paths, list):
                raise CapabilityPackError(f"{owner}.{field} must be an array")
            for path in paths:
                if not isinstance(path, str) or not path.startswith("scripts/") or not path.endswith(".py") or ".." in Path(path).parts:
                    raise CapabilityPackError(f"invalid command surface CLI path: {path}")
                add(Path(path).stem, "KEEP", path=path, sourcePointer=f"/{owner}/{field}")
    for field, decision in (("internalScriptIds", "INTERNAL"), ("mergedScriptIds", "MERGE")):
        values = surface.get(field)
        if not isinstance(values, list):
            raise CapabilityPackError(f"commandSurface.{field} must be an array")
        for script_id in values:
            add(script_id, decision, sourcePointer=f"/commandSurface/{field}")
    if len(entries) != surface.get("candidateCount"):
        raise CapabilityPackError("commandSurface.candidateCount does not match classified scripts")
    ordered = [entries[key] for key in sorted(entries)]
    projection = {
        "version": surface["version"],
        "counts": {decision: sum(item["v1Decision"] == decision for item in ordered) for decision in ("KEEP", "INTERNAL", "MERGE")},
        "entries": ordered,
    }
    canonical = json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        **projection,
        "source": "assets/development/capability-pack-contract.json",
        "digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def command_surface_inventory(contract: dict[str, Any]) -> dict[str, Any]:
    """Export the current decision inventory without repository-local dependencies."""
    return {
        "schemaVersion": 2,
        "kind": "V1_COMMAND_SURFACE_INVENTORY",
        "authority": "DERIVED",
        "regenerateCommand": "python3 scripts/cli_help.py list --inventory --json",
        **command_surface(contract),
    }


def validate_contract(contract: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    try:
        surface = command_surface(contract)
        if contract["commandSurface"].get("digest") != surface["digest"]:
            errors.append("commandSurface.digest does not match classified scripts")
    except CapabilityPackError as exc:
        errors.append(str(exc))
    if contract.get("schemaVersion") != 2:
        errors.append("schemaVersion must be 2")
    if contract.get("kind") != "INTERNAL_CAPABILITY_PACKS":
        errors.append("kind must be INTERNAL_CAPABILITY_PACKS")
    if contract.get("singleAuthoritySkill") != "common-develop":
        errors.append("singleAuthoritySkill must be common-develop")
    if contract.get("releaseUnit") != "ATOMIC":
        errors.append("releaseUnit must be ATOMIC")

    entry = contract.get("entryLayer")
    if not isinstance(entry, dict) or entry.get("lifecycleEventMutation") != "FORBIDDEN":
        errors.append("entryLayer must forbid lifecycle event mutation")
        entry = {}
    if entry.get("skillEntrypoint") != "SKILL.md":
        errors.append("entryLayer.skillEntrypoint must be SKILL.md")

    declared_paths: set[str] = set()

    def validate_interfaces(owner: str, definition: dict[str, Any]) -> None:
        cli_paths = definition.get("cliEntrypoints")
        module_paths = definition.get("moduleInterfaces")
        if not isinstance(cli_paths, list):
            errors.append(f"{owner}.cliEntrypoints must be an array")
            cli_paths = []
        if not isinstance(module_paths, list):
            errors.append(f"{owner}.moduleInterfaces must be an array")
            module_paths = []
        overlap = set(cli_paths) & set(module_paths)
        if overlap:
            errors.append(f"{owner} cannot classify paths as both CLI and module interface: {sorted(overlap)}")
        for kind, paths in (("CLI", cli_paths), ("module interface", module_paths)):
            for path in paths:
                if not isinstance(path, str) or not path or not formalize(ROOT / path).is_file():
                    errors.append(f"{owner} references missing {kind} path: {path}")
                    continue
                if not path.startswith("scripts/") or not path.endswith(".py"):
                    errors.append(f"{owner} {kind} must be a scripts/*.py path: {path}")
                if path in declared_paths:
                    errors.append(f"entrypoint path is declared more than once: {path}")
                declared_paths.add(path)

    if isinstance(entry, dict):
        validate_interfaces("entryLayer", entry)
        support_cli_paths = entry.get("supportCliEntrypoints")
        if not isinstance(support_cli_paths, list):
            errors.append("entryLayer.supportCliEntrypoints must be an array")
        else:
            for path in support_cli_paths:
                if not isinstance(path, str) or not path or not formalize(ROOT / path).is_file():
                    errors.append(f"entryLayer references missing support CLI path: {path}")
                    continue
                if not path.startswith("scripts/") or not path.endswith(".py"):
                    errors.append(f"entryLayer support CLI must be a scripts/*.py path: {path}")
                if path in declared_paths:
                    errors.append(f"entrypoint path is declared more than once: {path}")
                declared_paths.add(path)

    shared = contract.get("sharedKernel")
    if not isinstance(shared, dict):
        errors.append("sharedKernel must be an object")
        shared = {}
    for ref in list(shared.get("authorityRefs") or []) + list(shared.get("implementationPaths") or []):
        if not isinstance(ref, str) or not ref or not _ref_path(ref).exists():
            errors.append(f"sharedKernel references missing path: {ref}")
    result_contracts = shared.get("interfaceContracts")
    expected_results = {"artifactRef", "documentValidationResult", "reviewGateResult", "lifecycleDecision"}
    if not isinstance(result_contracts, dict) or set(result_contracts) != expected_results:
        errors.append("sharedKernel.interfaceContracts must declare the four bounded cross-pack results")
    else:
        for ref in result_contracts.values():
            if not isinstance(ref, str) or not _ref_path(ref).is_file():
                errors.append(f"interface contract references missing schema: {ref}")

    packs = contract.get("packs")
    if not isinstance(packs, list):
        errors.append("packs must be an array")
        packs = []
    ids = [item.get("id") for item in packs if isinstance(item, dict)]
    if tuple(ids) != PACK_IDS:
        errors.append(f"packs must be exactly {list(PACK_IDS)} in canonical order")
    known_targets = {"shared-kernel", *PACK_IDS}
    lifecycle_writers: list[str] = []
    for pack in packs:
        if not isinstance(pack, dict):
            errors.append("each pack must be an object")
            continue
        pack_id = str(pack.get("id") or "")
        for forbidden_key in ("version", "packageVersion", "releaseStage", "baselineTag"):
            if forbidden_key in pack:
                errors.append(f"{pack_id} must not declare independent {forbidden_key}")
        validate_interfaces(pack_id, pack)
        for path in list(pack.get("implementationPaths") or []):
            if not isinstance(path, str) or not path or not formalize(ROOT / path).exists():
                errors.append(f"{pack_id} references missing path: {path}")
        dependencies = pack.get("dependencies")
        if not isinstance(dependencies, list):
            errors.append(f"{pack_id} dependencies must be an array")
        else:
            for dependency in dependencies:
                if not isinstance(dependency, dict) or dependency.get("target") not in known_targets:
                    errors.append(f"{pack_id} has invalid dependency: {dependency}")
        mutation = pack.get("mutationAuthorities")
        if not isinstance(mutation, dict):
            errors.append(f"{pack_id} mutationAuthorities must be an object")
            continue
        if mutation.get("lifecycleEvents") == "SOLE_WRITER":
            lifecycle_writers.append(pack_id)
        elif mutation.get("lifecycleEvents") != "FORBIDDEN":
            errors.append(f"{pack_id} lifecycleEvents must be SOLE_WRITER or FORBIDDEN")
    if lifecycle_writers != ["task-runtime"]:
        errors.append("task-runtime must be the only lifecycle event writer")
    boundary_rules = contract.get("boundaryRules")
    if not isinstance(boundary_rules, list) or len(boundary_rules) < 5 or any(not isinstance(item, str) or not item for item in boundary_rules):
        errors.append("boundaryRules must declare the single-authority cross-pack invariants")

    known_pack_ids = set(PACK_IDS)
    for label in ("defaultActivation",):
        values = contract.get(label)
        if not isinstance(values, list) or not values:
            errors.append(f"{label} must be a non-empty array")
            continue
        for value in values:
            if not isinstance(value, dict) or value.get("packId") not in known_pack_ids or value.get("mode") not in VALID_MODES:
                errors.append(f"{label} has invalid activation: {value}")
    rules = contract.get("routingRules")
    if not isinstance(rules, list) or not rules:
        errors.append("routingRules must be a non-empty array")
    else:
        rule_ids: set[str] = set()
        for rule in rules:
            if not isinstance(rule, dict) or not isinstance(rule.get("id"), str):
                errors.append(f"invalid routing rule: {rule}")
                continue
            if rule["id"] in rule_ids:
                errors.append(f"duplicate routing rule id: {rule['id']}")
            rule_ids.add(rule["id"])
            match = rule.get("match")
            if not isinstance(match, dict) or not any(match.get(key) for key in ("taskTypes", "phases")):
                errors.append(f"routing rule has no taskTypes or phases: {rule['id']}")
            seen: set[str] = set()
            for activation in rule.get("activate") or []:
                if not isinstance(activation, dict) or activation.get("packId") not in known_pack_ids or activation.get("mode") not in VALID_MODES:
                    errors.append(f"routing rule has invalid activation: {rule['id']}->{activation}")
                    continue
                if activation["packId"] in seen:
                    errors.append(f"routing rule activates one pack twice: {rule['id']}->{activation['packId']}")
                seen.add(activation["packId"])

    try:
        schema = _read_object(RESULT_SCHEMA_PATH)
        definitions = schema.get("$defs")
        if not isinstance(definitions, dict) or not expected_results.issubset(definitions):
            errors.append("capability-result.schema.json is missing required $defs")
    except CapabilityPackError as exc:
        errors.append(str(exc))
    return errors


def load_contract() -> dict[str, Any]:
    contract = _read_object(CONTRACT_PATH)
    errors = validate_contract(contract)
    if errors:
        raise CapabilityPackError("; ".join(errors))
    return contract


def pack_by_id(pack_id: str, contract: dict[str, Any] | None = None) -> dict[str, Any]:
    value = load_contract() if contract is None else contract
    for pack in value.get("packs") or []:
        if isinstance(pack, dict) and pack.get("id") == pack_id:
            return pack
    raise CapabilityPackError(f"unknown capability pack: {pack_id}")


def resolve(
    task_type: str, phase: str = "", contract: dict[str, Any] | None = None, *, compact: bool = False,
) -> dict[str, Any]:
    value = load_contract() if contract is None else contract
    selected: dict[str, dict[str, Any]] = {}

    def activate(pack_id: str, mode: str, reason: str) -> None:
        current = selected.get(pack_id)
        if current is None:
            selected[pack_id] = {"id": pack_id, "mode": mode, "reasonCodes": [reason]}
            return
        if MODE_PRIORITY[mode] > MODE_PRIORITY[current["mode"]]:
            current["mode"] = mode
        if reason not in current["reasonCodes"]:
            current["reasonCodes"].append(reason)

    for activation in value.get("defaultActivation") or []:
        activate(str(activation["packId"]), str(activation["mode"]), "DEFAULT_ACTIVATION")
    for rule in value.get("routingRules") or []:
        match = rule.get("match") or {}
        task_match = bool(task_type) and task_type in set(match.get("taskTypes") or [])
        phase_match = bool(phase) and phase in set(match.get("phases") or [])
        if not (task_match or phase_match):
            continue
        for activation in rule.get("activate") or []:
            activate(str(activation["packId"]), str(activation["mode"]), str(rule["id"]))

    order = {pack_id: index for index, pack_id in enumerate(PACK_IDS)}
    packs = []
    for item in sorted(selected.values(), key=lambda entry: order[entry["id"]]):
        definition = pack_by_id(item["id"], value)
        detail = {} if compact else {
            "name": definition.get("name"),
            "purpose": definition.get("purpose"),
            "cliEntrypoints": definition.get("cliEntrypoints"),
            "moduleInterfaces": definition.get("moduleInterfaces"),
        }
        packs.append({**item, **detail})
    result = {
        "schemaVersion": 1,
        "singleAuthoritySkill": value["singleAuthoritySkill"],
        "releaseUnit": value["releaseUnit"],
        "packs": packs,
    }
    if not compact:
        result.update({
            "taskType": task_type,
            "phase": phase,
            "boundaryRef": "assets/development/capability-pack-contract.json#/boundaryRules",
            "queryCommand": "python3 scripts/wk.py packs -g {CURRENT_AGENT} --task-type {TASK_TYPE} --phase {PHASE} --json",
        })
    return result
