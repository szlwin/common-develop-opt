"""Release checks for the internal runtime."""
from __future__ import annotations
from checks._check_skill.layout import checked_path, read_check_text
import argparse 
import ast 
import hashlib 
import importlib.util 
import json 
import py_compile 
import re 
import sys 
import tempfile 
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

from document_core import simple_yaml
from document_core import agent_context
from checks import semantic_audit
import reference_reader
from runtime import context_resolver
from runtime import contract_normalization
from runtime import design_doc
from runtime import case_doc
from checks import cli_help
import merge_docs
from runtime import long_task as long_task_runtime
from runtime._core import capability_packs, implementation_strategy
from checks._check_skill.context import *
from checks._check_skill.model import CheckResult
from checks._check_skill.io import load_json, load_yaml
from checks._check_skill.module_loader import *
from checks._check_skill.routing import *
from checks._check_skill.manifest import *

try:
    import jsonschema 
except ImportError:  # pragma: no cover
    jsonschema = None

from checks._check_skill.model import CheckResult
from checks._check_skill.routing import load_materialized_routing
def check_capability_pack_contract(contract: dict[str, Any], result: CheckResult) -> None:
    """Enforce one Skill authority with three internal, non-versioned capability packs."""
    before = len(result.errors)
    try:
        pack_contract = capability_packs.load_contract()
    except capability_packs.CapabilityPackError as exc:
        result.errors.append(f"capability-pack contract invalid: {exc}")
        return

    result_schema = load_json(ASSETS / 'development/capability-result.schema.json', result)
    if isinstance(result_schema, dict) and jsonschema is not None:
        try:
            jsonschema.Draft202012Validator.check_schema(result_schema)
        except jsonschema.SchemaError as exc:
            result.errors.append(f"capability-result.schema.json is invalid: {exc.message}")

    source_map = load_json(ROOT / "ai" / "source-map.json", result)
    if isinstance(source_map, dict):
        authoritative = source_map.get("authoritativeSources") or {}
        expected = {
            "capabilityPacks": "assets/development/capability-pack-contract.json",
            "capabilityPackResults": "assets/development/capability-result.schema.json",
            "capabilityPackQuery": "scripts/wk.py",
            "v1CommandSurface": "assets/development/capability-pack-contract.json#/commandSurface",
            "v1CommandSurfaceExport": "scripts/cli_help.py list --inventory --json",
            "cliHelp": "scripts/cli_help.py",
            "apiDbVersionDelta": "scripts/merge_docs.py query --version {VERSION} --module {MODULE} --artifact api|db",
            "apiDbCurrentImplementation": "project-declared code/OpenAPI/DDL/Migration/Schema sources",
            "apiDbDeltaProjectionSchema": "assets/documents/structured-docs/version-delta.schema.json",
            "apiDbDeltaArchive": "scripts/merge_docs.py",
            "moduleEnums": "docs/module/{MODULE}/{MODULE}_desc.md#枚举",
            "versionModuleEnums": "version/{VERSION}/module/{MODULE}/{MODULE}_desc.md#枚举",
            "projectUnionLanguage": "docs/union/union_language.md",
            "versionUnionLanguageDelta": "version/{VERSION}/union/union_language_changes.md",
            "artifactRelationSchema": "assets/documents/artifact-relation.schema.json",
            "artifactRelationBuilder": "scripts/artifact_relations.py",
            "dependencyImpactChangesetSchema": "assets/documents/structured-docs/dependency-impact.changeset.schema.json",
            "changesetReader": "scripts/merge_docs.py query",
            "changesetWriter": "scripts/merge_docs.py write",
            "designTopicArchiveSchema": "assets/documents/structured-docs/design-topic.schema.json",
            "archiveManifestSchema": "assets/documents/structured-docs/archive-manifest.schema.json",
            "currentManifest": "version/{VERSION}/task/{TARGET_ID}/cache/current_manifest.json",
            "currentImpact": "version/{VERSION}/task/{TARGET_ID}/cache/current_impact.json",
            "storageContract": "assets/storage/storage-contract.json",
        }
        for key, value in expected.items():
            if authoritative.get(key) != value:
                result.errors.append(f"ai/source-map.json capability-pack pointer is invalid: {key}")

    declared_commands = {
        "capability_packs.list", "capability_packs.show", "capability_packs.resolve",
        "capability_packs.validate", "wk.packs", "cli_help.list", "cli_help.show",
        "cli_help.validate", "long_task.help",
    }
    # v2 has two authoritative permission sources: capability-pack query
    # permissions live in the capability-pack contract, while the unified
    # ``wk``/help commands are materialized from Runtime Contract profiles.
    # Do not require the capability queries to be duplicated in the expanded
    # Runtime Contract view.
    capability_permissions = pack_contract.get("scriptPermissions")
    runtime_permissions = contract.get("scriptPermissions") if isinstance(contract, dict) else None
    missing_permissions = sorted(
        command
        for command in declared_commands
        if (
            (capability_permissions if command.startswith("capability_packs.") else runtime_permissions) or {}
        ).get(command) != {"mode": "DECLARED_AGENT"}
    )
    if missing_permissions:
        result.errors.append(f"capability-pack queries missing DECLARED_AGENT permission: {missing_permissions}")

    declared_clis = set(cli_help.declared_cli_paths(pack_contract))
    query_policy = contract.get("contractQueryPolicy") or {}
    if query_policy.get("v1CommandSurface") != "assets/development/capability-pack-contract.json#/commandSurface":
        result.errors.append("Runtime Contract must reference the canonical v1 command surface")
    if query_policy.get("capabilityPackQueryCommand") != "python3 scripts/wk.py packs":
        result.errors.append("capability pack queries must use the declared wk packs entrypoint")
    surface = capability_packs.command_surface(pack_contract)
    result.details["v1CommandSurface"] = surface["counts"]
    result.details["v1CommandSurfaceDigest"] = surface["digest"]
    generated_entries = load_json(ROOT / 'ai/v1-cli-entrypoints.json', result)
    expected_entries = {"schemaVersion": 1, "entrypoints": cli_help.declared_cli_entries(pack_contract),
                        "commandSurface": surface}
    if generated_entries != expected_entries:
        result.errors.append(
            'V1_CLI_ENTRYPOINTS_STALE: regenerate ai/v1-cli-entrypoints.json with cli_help.py list --json'
        )
    reference_catalog = load_json(ROOT / "references/reference-catalog.json", result)
    if isinstance(reference_catalog, dict):
        if reference_catalog.get("commandSurfaceSource") != "assets/development/capability-pack-contract.json#/commandSurface":
            result.errors.append("Reference catalog must reference the canonical v1 command surface")
    for module_path in ('scripts/document_core/document_facts.py', 'scripts/runtime/work_record.py'):
        if module_path in declared_clis:
            result.errors.append(f"module interface must not be exposed as a CLI entrypoint: {module_path}")
    if "scripts/cli_help.py" not in declared_clis:
        result.errors.append("entry layer must expose scripts/cli_help.py for machine-readable command discovery")

    orchestration = (((contract or {}).get("commandInterface") or {}).get("orchestration") or {})
    if not isinstance(orchestration.get("packs"), dict) or orchestration["packs"].get("command") != "packs":
        result.errors.append("Runtime Contract must expose wk packs through the unified orchestration entry")

    release = load_json(ASSETS / 'package/release-contract.json', result)
    if isinstance(release, dict):
        required_flags = {
            "single-skill-internal-capability-packs", "machine-readable-cli-help",
            "declared-cli-module-separation", "cli-help-quality-gate",
            "version-local-api-db-deltas", "no-full-api-db-archive",
            "reducer-driven-task-human-summary", "non-destructive-document-preview",
            "api-db-read-only-baseline", "document-archive-gate",
            "requirement-scoped-test-design", "explicit-test-input-output-verification",
            "test-design-script-traceability", "source-linked-code-comments", "page-design-demo",
        }
        missing_flags = sorted(required_flags - set(release.get("featureFlags") or []))
        if missing_flags:
            result.errors.append(f"release contract missing capability/CLI feature flags: {missing_flags}")

    skill_text = read_check_text(ROOT / "SKILL.md", encoding="utf-8")
    for token in ("document-facts", "task-runtime", "review-verification", "assets/development/capability-pack-contract.json"):
        if token not in skill_text:
            result.errors.append(f"SKILL.md missing internal capability-pack boundary: {token}")

    design = capability_packs.resolve("design", "design", pack_contract)
    if [item["id"] for item in design["packs"]] != list(capability_packs.PACK_IDS):
        result.errors.append("design context must compose all three internal capability packs")
    if len(result.errors) == before:
        result.ok("One Skill authority, three internal capability packs, bounded results, and single lifecycle writer agree")


def check_command_surface_inventory(path: Path, result: CheckResult) -> None:
    """Check a repository-supplied inventory; installed packages need no opt directory."""
    try:
        expected = capability_packs.command_surface_inventory(capability_packs.load_contract())
        actual = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        result.errors.append(f"COMMAND_SURFACE_INVENTORY_INVALID: {exc}")
        return
    if not isinstance(actual, dict):
        result.errors.append("COMMAND_SURFACE_INVENTORY_INVALID: inventory must be an object")
        return
    mismatches = sorted(key for key, value in expected.items() if actual.get(key) != value)
    unexpected = sorted(set(actual) - set(expected))
    if mismatches or unexpected:
        result.errors.append(
            f"COMMAND_SURFACE_INVENTORY_DRIFT: fields={mismatches}, unexpected={unexpected}"
        )
        return
    result.details["commandSurfaceInventory"] = {
        "status": "PASSED", "counts": expected["counts"], "digest": expected["digest"],
    }
    result.ok("Repository v1 inventory matches capability decisions, counts and digest")


def _numeric_version(value: str) -> tuple[int, ...]:
    parts = value.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        raise ValueError(f"invalid numeric version: {value}")
    return tuple(int(part) for part in parts)


def check_release_baseline_contract(contract: dict[str, Any], result: CheckResult) -> None:
    release = load_json(ASSETS / 'package/release-contract.json', result)
    if not isinstance(release, dict):
        return
    package_version = release.get("packageVersion")
    stage = release.get("releaseStage")
    if not isinstance(package_version, str) or not re.fullmatch(r"\d+\.\d+(?:\.\d+)?", package_version):
        result.errors.append("release contract packageVersion must be numeric")
        return
    if stage not in ('DEVELOPMENT', 'STABLE'):
        result.errors.append("release contract releaseStage must be DEVELOPMENT or STABLE")
        return
    baseline_ref = release.get("baselineTag")
    if stage == 'STABLE' and (not isinstance(baseline_ref, str) or not re.fullmatch(r"[0-9a-f]{40}", baseline_ref)):
        result.errors.append("release contract baselineTag must be an existing commit reference")
    expected_mode = 'PACKAGE_MANIFEST' if stage == 'DEVELOPMENT' else 'IMPLEMENTATION_COMMIT_WITH_RELEASE_ENVELOPE'
    if release.get("baselineMode") != expected_mode:
        result.errors.append("release contract baselineMode must use the canonical release envelope mode")
    envelope_files = release.get("releaseEnvelopeFiles")
    expected_envelope_files = {"CHANGELOG.md", "assets/package/release-contract.json", "FILE_MANIFEST.sha256"}
    if not isinstance(envelope_files, list) or set(envelope_files) != expected_envelope_files:
        result.errors.append("release contract releaseEnvelopeFiles must cover only the canonical release metadata files")

    required_critical = {
        "SKILL.md",
        "CHANGELOG.md",
        "assets/package/release-contract.json",
        "assets/runtime/runtime-contract.json",
        "assets/storage/storage-contract.json",
        "assets/review/review-contract.json",
        "assets/development/capability-pack-contract.json",
        "assets/development/capability-result.schema.json",
        "assets/documents/structured-docs/design.template.md",
        "context-routing.json",
        "ai/source-map.json",
        "FILE_MANIFEST.sha256",
    }
    critical = release.get("criticalBaselineFiles")
    if not isinstance(critical, list) or any(not isinstance(item, str) for item in critical):
        result.errors.append("release contract criticalBaselineFiles must be a string array")
        critical = []
    elif len(critical) != len(set(critical)):
        result.errors.append("release contract criticalBaselineFiles must be unique")
    if set(critical) != required_critical:
        result.errors.append("release contract criticalBaselineFiles must cover the canonical package baseline")
    for relative in critical:
        if not checked_path(ROOT / relative).is_file():
            result.errors.append(f"critical baseline file does not exist: {relative}")
    expected_command = "python3 scripts/baseline_guard.py -g ProjectManagerAgent status --json"
    if release.get("baselineGuardCommand") != expected_command:
        result.errors.append("release contract baselineGuardCommand is not canonical")

    if release.get("storageModelVersion") != 6:
        result.errors.append("release contract current storageModelVersion must remain 6")

    skill_text = read_check_text(ROOT / "SKILL.md", encoding="utf-8")
    if "当前 Skill 包版本" in skill_text or re.search(r"（RC\d+）", skill_text):
        result.errors.append("SKILL.md must not duplicate the current package version or RC stage")
    for token in ("assets/package/release-contract.json", "scripts/baseline_guard.py", "status --json"):
        if token not in skill_text:
            result.errors.append(f"SKILL.md missing release/baseline pointer: {token}")

    for relative in (
        "scripts/baseline_guard.py",
        "references/recovery-archive/baseline-integrity.md",
    ):
        if not checked_path(ROOT / relative).is_file():
            result.errors.append(f"missing baseline integrity component: {relative}")
    permissions = contract.get("scriptPermissions", {}) if isinstance(contract, dict) else {}
    expected_permission = {"mode": "STATIC", "allowedAgents": ["ProjectManagerAgent"]}
    if permissions.get("baseline_guard.status") != expected_permission:
        result.errors.append("runtime contract baseline_guard.status permission is invalid")

    source_map = load_json(ROOT / "ai" / "source-map.json", result)
    if isinstance(source_map, dict):
        authoritative = source_map.get("authoritativeSources", {})
        if authoritative.get("baselineGuard") != "scripts/baseline_guard.py":
            result.errors.append("ai/source-map.json must point to baseline_guard.py")
        if authoritative.get("baselineTag") != "assets/package/release-contract.json#/baselineTag":
            result.errors.append("ai/source-map.json baselineTag pointer is invalid")

    routing = load_materialized_routing(result)
    if isinstance(routing, dict):
        baseline_resource = (routing.get("resourceCatalog") or {}).get("baseline-integrity")
        if not isinstance(baseline_resource, dict) or baseline_resource.get("path") != "references/recovery-archive/baseline-integrity.md":
            result.errors.append("context routing must declare baseline-integrity resource")
        conditions = [item for item in routing.get("promptConditions", []) if isinstance(item, dict)]
        matching = [item for item in conditions if item.get("id") == "baseline-integrity"]
        if len(matching) != 1 or matching[0].get("resources") != ["baseline-integrity"]:
            result.errors.append("context routing must declare one baseline-integrity prompt condition")

    changelog_path = ROOT / 'CHANGELOG.md'
    if not checked_path(changelog_path).is_file():
        result.errors.append('missing CHANGELOG.md')
        return
    changelog = read_check_text(changelog_path, encoding="utf-8")
    if f"## {package_version} {stage}" not in changelog:
        result.errors.append("CHANGELOG.md is missing the current release-stage entry")
    if not result.errors:
        result.ok("Initial release identity, baseline integrity, and critical files agree")
