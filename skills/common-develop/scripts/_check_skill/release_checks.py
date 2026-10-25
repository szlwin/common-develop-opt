"""Release checks for the internal runtime."""
from __future__ import annotations
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

import simple_yaml
import agent_context
import semantic_audit
import reference_reader
import context_resolver
import contract_normalization
import design_doc
import case_doc
import cli_help
import merge_docs
import long_task as long_task_runtime
from _core import capability_packs, implementation_strategy
from _check_skill.context import *
from _check_skill.model import CheckResult
from _check_skill.io import load_json, load_yaml
from _check_skill.module_loader import *
from _check_skill.routing import *
from _check_skill.manifest import *

try:
    import jsonschema
except ImportError:  # pragma: no cover
    jsonschema = None

from _check_skill.model import CheckResult
from _check_skill.routing import load_materialized_routing
def check_capability_pack_contract(contract: dict[str, Any], result: CheckResult) -> None:
    """Enforce one Skill authority with three internal, non-versioned capability packs."""
    before = len(result.errors)
    try:
        pack_contract = capability_packs.load_contract()
    except capability_packs.CapabilityPackError as exc:
        result.errors.append(f"capability-pack contract invalid: {exc}")
        return

    result_schema = load_json(ASSETS / "capability-result.schema.json", result)
    if isinstance(result_schema, dict) and jsonschema is not None:
        try:
            jsonschema.Draft202012Validator.check_schema(result_schema)
        except jsonschema.SchemaError as exc:
            result.errors.append(f"capability-result.schema.json is invalid: {exc.message}")

    source_map = load_json(ROOT / "ai" / "source-map.json", result)
    if isinstance(source_map, dict):
        authoritative = source_map.get("authoritativeSources") or {}
        expected = {
            "capabilityPacks": "assets/capability-pack-contract.json",
            "capabilityPackResults": "assets/capability-result.schema.json",
            "capabilityPackQuery": "scripts/capability_packs.py",
            "cliHelp": "scripts/cli_help.py",
            "apiDbVersionDelta": "version/{VERSION}/doc/{MODULE}/changes/*.yaml#operations",
            "apiDbCurrentImplementation": "project-declared code/OpenAPI/DDL/Migration/Schema sources",
            "apiDbDeltaProjectionSchema": "assets/structured-docs/version-delta.schema.json",
            "apiDbDeltaArchive": "scripts/merge_docs.py",
            "artifactRelationSchema": "assets/artifact-relation.schema.json",
            "artifactRelationBuilder": "scripts/artifact_relations.py",
            "dependencyImpactChangesetSchema": "assets/structured-docs/dependency-impact.changeset.schema.json",
            "designTopicArchiveSchema": "assets/structured-docs/design-topic.schema.json",
            "archiveManifestSchema": "assets/structured-docs/archive-manifest.schema.json",
            "currentManifest": "version/{VERSION}/task/{TARGET_ID}/cache/current_manifest.json",
            "currentImpact": "version/{VERSION}/task/{TARGET_ID}/cache/current_impact.json",
            "storageContract": "assets/storage-contract.json",
        }
        for key, value in expected.items():
            if authoritative.get(key) != value:
                result.errors.append(f"ai/source-map.json capability-pack pointer is invalid: {key}")

    permissions = contract.get("scriptPermissions") if isinstance(contract, dict) else {}
    declared_commands = {
        "capability_packs.list", "capability_packs.show", "capability_packs.resolve",
        "capability_packs.validate", "wk.packs", "cli_help.list", "cli_help.show",
        "cli_help.validate", "long_task.help",
    }
    missing_permissions = sorted(
        command for command in declared_commands
        if (permissions or {}).get(command) != {"mode": "DECLARED_AGENT"}
    )
    if missing_permissions:
        result.errors.append(f"capability-pack queries missing DECLARED_AGENT permission: {missing_permissions}")

    declared_clis = set(cli_help.declared_cli_paths(pack_contract))
    for module_path in ("scripts/document_facts.py", "scripts/work_record.py"):
        if module_path in declared_clis:
            result.errors.append(f"module interface must not be exposed as a CLI entrypoint: {module_path}")
    if "scripts/cli_help.py" not in declared_clis:
        result.errors.append("entry layer must expose scripts/cli_help.py for machine-readable command discovery")

    orchestration = (((contract or {}).get("commandInterface") or {}).get("orchestration") or {})
    if not isinstance(orchestration.get("packs"), dict) or orchestration["packs"].get("command") != "packs":
        result.errors.append("Runtime Contract must expose wk packs through the unified orchestration entry")

    release = load_json(ASSETS / "release-contract.json", result)
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

    skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    for token in ("document-facts", "task-runtime", "review-verification", "assets/capability-pack-contract.json"):
        if token not in skill_text:
            result.errors.append(f"SKILL.md missing internal capability-pack boundary: {token}")

    design = capability_packs.resolve("design", "design", pack_contract)
    if [item["id"] for item in design["packs"]] != list(capability_packs.PACK_IDS):
        result.errors.append("design context must compose all three internal capability packs")
    if len(result.errors) == before:
        result.ok("One Skill authority, three internal capability packs, bounded results, and single lifecycle writer agree")


def _numeric_version(value: str) -> tuple[int, ...]:
    parts = value.split(".")
    if not parts or any(not part.isdigit() for part in parts):
        raise ValueError(f"invalid numeric version: {value}")
    return tuple(int(part) for part in parts)


def check_release_baseline_contract(contract: dict[str, Any], result: CheckResult) -> None:
    release = load_json(ASSETS / "release-contract.json", result)
    if not isinstance(release, dict):
        return
    package_version = release.get("packageVersion")
    stage = release.get("releaseStage")
    if not isinstance(package_version, str) or not re.fullmatch(r"\d+\.\d+", package_version):
        result.errors.append("release contract packageVersion must be numeric major.minor")
        return
    if not isinstance(stage, str) or stage != "STABLE":
        result.errors.append("release contract releaseStage must be STABLE for the initial package")
        return
    baseline_ref = release.get("baselineTag")
    if not isinstance(baseline_ref, str) or not re.fullmatch(r"[0-9a-f]{40}", baseline_ref):
        result.errors.append("release contract baselineTag must be an existing commit reference")
    if release.get("baselineMode") != "IMPLEMENTATION_COMMIT_WITH_RELEASE_ENVELOPE":
        result.errors.append("release contract baselineMode must use the canonical release envelope mode")
    envelope_files = release.get("releaseEnvelopeFiles")
    expected_envelope_files = {"CHANGELOG.md", "assets/release-contract.json", "FILE_MANIFEST.sha256"}
    if not isinstance(envelope_files, list) or set(envelope_files) != expected_envelope_files:
        result.errors.append("release contract releaseEnvelopeFiles must cover only the canonical release metadata files")

    required_critical = {
        "SKILL.md",
        "CHANGELOG.md",
        "assets/release-contract.json",
        "assets/runtime-contract.json",
        "assets/storage-contract.json",
        "assets/review-contract.json",
        "assets/capability-pack-contract.json",
        "assets/capability-result.schema.json",
        "assets/structured-docs/design.template.md",
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
        if not (ROOT / relative).is_file():
            result.errors.append(f"critical baseline file does not exist: {relative}")
    expected_command = "python3 scripts/baseline_guard.py -g ProjectManagerAgent status --json"
    if release.get("baselineGuardCommand") != expected_command:
        result.errors.append("release contract baselineGuardCommand is not canonical")

    if release.get("storageModelVersion") != 6:
        result.errors.append("release contract current storageModelVersion must remain 6")

    skill_text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    if "当前 Skill 包版本" in skill_text or re.search(r"（RC\d+）", skill_text):
        result.errors.append("SKILL.md must not duplicate the current package version or RC stage")
    for token in ("assets/release-contract.json", "scripts/baseline_guard.py", "status --json"):
        if token not in skill_text:
            result.errors.append(f"SKILL.md missing release/baseline pointer: {token}")

    for relative in (
        "scripts/baseline_guard.py",
        "tests/test_baseline_guard.py",
        "references/baseline-integrity.md",
    ):
        if not (ROOT / relative).is_file():
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
        if authoritative.get("baselineTag") != "assets/release-contract.json#/baselineTag":
            result.errors.append("ai/source-map.json baselineTag pointer is invalid")

    routing = load_materialized_routing(result)
    if isinstance(routing, dict):
        baseline_resource = (routing.get("resourceCatalog") or {}).get("baseline-integrity")
        if not isinstance(baseline_resource, dict) or baseline_resource.get("path") != "references/baseline-integrity.md":
            result.errors.append("context routing must declare baseline-integrity resource")
        conditions = [item for item in routing.get("promptConditions", []) if isinstance(item, dict)]
        matching = [item for item in conditions if item.get("id") == "baseline-integrity"]
        if len(matching) != 1 or matching[0].get("resources") != ["baseline-integrity"]:
            result.errors.append("context routing must declare one baseline-integrity prompt condition")

    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## {package_version} {stage}" not in changelog:
        result.errors.append("CHANGELOG.md is missing the current release-stage entry")
    if not result.errors:
        result.ok("Initial release identity, baseline integrity, and critical files agree")
