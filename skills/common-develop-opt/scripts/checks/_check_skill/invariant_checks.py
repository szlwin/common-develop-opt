"""Static checks for canonical documentation invariants."""
from __future__ import annotations
from checks._check_skill.layout import checked_path, read_check_text

import json 
import os 
import re 
from pathlib import Path
from resource_paths import formalize
from typing import Any

from checks._check_skill.context import ASSETS, ROOT
from checks._check_skill.model import CheckResult

REGISTRY_PATH = ASSETS / 'package/doc-invariants.json'
MARKER_TEMPLATE = "<!-- common-develop-invariant: {id} -->"


def _load_registry(result: CheckResult) -> dict[str, Any] | None:
    try:
        value = json.loads(read_check_text(REGISTRY_PATH, encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        result.errors.append(f"cannot load doc invariant registry: {exc}")
        return None
    if not isinstance(value, dict) or value.get("schemaVersion") != 2:
        result.errors.append("assets/package/doc-invariants.json must declare schemaVersion 2")
        return None
    invariants = value.get("invariants")
    if not isinstance(invariants, list) or not invariants:
        result.errors.append("assets/package/doc-invariants.json must declare a non-empty invariants list")
        return None
    return value



def _decode_json_pointer_token(token: str) -> str:
    """Decode one RFC 6901 JSON Pointer token."""
    if re.search(r"~(?![01])", token):
        raise ValueError(f"invalid escape in JSON Pointer token {token!r}")
    return token.replace("~1", "/").replace("~0", "~")


def resolve_json_pointer(document: Any, pointer: str) -> Any:
    """Resolve an RFC 6901 JSON Pointer against a JSON-compatible value."""
    if not isinstance(pointer, str):
        raise ValueError("JSON Pointer must be a string")
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        raise ValueError("JSON Pointer must be empty or start with '/'")
    current = document
    for raw_token in pointer[1:].split("/"):
        token = _decode_json_pointer_token(raw_token)
        if isinstance(current, dict):
            if token not in current:
                raise KeyError(token)
            current = current[token]
        elif isinstance(current, list):
            if token == "-" or not token.isdigit():
                raise KeyError(token)
            index = int(token)
            if index >= len(current):
                raise IndexError(index)
            current = current[index]
        else:
            raise KeyError(token)
    return current


def _json_value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int) and not isinstance(value, bool):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _contract_display_path(path: Path) -> str:
    """Render a contract path relative to the active skill root."""
    try:
        return os.path.relpath(str(path), str(ROOT))
    except ValueError:
        return str(path)


def _safe_contract_path(raw_path: Any, result: CheckResult, invariant_id: str) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        result.errors.append(f"doc invariant {invariant_id} contractField.contract must be a non-empty relative path")
        return None
    candidate = Path(raw_path)
    if candidate.is_absolute():
        result.errors.append(f"doc invariant {invariant_id} contractField.contract must be relative: {raw_path}")
        return None
    root_real = os.path.realpath(str(ROOT))
    resolved = formalize(ROOT / candidate, root=ROOT).resolve()
    try:
        if os.path.commonpath([root_real, str(resolved)]) != root_real:
            raise ValueError
    except ValueError:
        result.errors.append(f"doc invariant {invariant_id} contractField.contract escapes skill root: {raw_path}")
        return None
    if not resolved.is_file():
        result.errors.append(f"doc invariant {invariant_id} contract file is missing: {raw_path}")
        return None
    return resolved


def _check_contract_field(
    item: dict[str, Any],
    result: CheckResult,
    contract_cache: dict[Path, Any],
) -> bool:
    """Verify an invariant's machine contract projection, if declared."""
    invariant_id = str(item.get("id") or "<missing>")
    field = item.get("contractField")
    if field is None:
        reason = item.get("contractFieldReason")
        if not isinstance(reason, str) or not reason.strip():
            result.errors.append(
                f"doc invariant {invariant_id} with contractField null must declare contractFieldReason"
            )
        return True
    if not isinstance(field, dict):
        result.errors.append(f"doc invariant {invariant_id} contractField must be an object or null")
        return False
    allowed_keys = {"contract", "jsonPointer", "expected", "valueType", "allowedValues"}
    unknown = sorted(set(field) - allowed_keys)
    if unknown:
        result.errors.append(f"doc invariant {invariant_id} contractField has unknown keys: {unknown}")
    path = _safe_contract_path(field.get("contract"), result, invariant_id)
    pointer = field.get("jsonPointer")
    if not isinstance(pointer, str):
        result.errors.append(f"doc invariant {invariant_id} contractField.jsonPointer must be a string")
        pointer = None
    if path is None or pointer is None:
        return False
    if path not in contract_cache:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            result.errors.append(f"doc invariant {invariant_id} cannot load contract {path.relative_to(ROOT)}: {exc}")
            return False
        if not isinstance(loaded, (dict, list)):
            result.errors.append(f"doc invariant {invariant_id} contract must contain a JSON object or array")
            return False
        contract_cache[path] = loaded
    try:
        actual = resolve_json_pointer(contract_cache[path], pointer)
    except (ValueError, KeyError, IndexError) as exc:
        result.errors.append(
            f"doc invariant {invariant_id} contractField cannot resolve "
            f"{_contract_display_path(path)}#{pointer}: {exc}"
        )
        return False
    expected_type = field.get("valueType")
    if expected_type is not None:
        valid_types = {"null", "boolean", "integer", "number", "string", "array", "object"}
        if expected_type not in valid_types:
            result.errors.append(f"doc invariant {invariant_id} contractField.valueType is invalid: {expected_type}")
        elif _json_value_type(actual) != expected_type and not (expected_type == "number" and _json_value_type(actual) == "integer"):
            result.errors.append(
                f"doc invariant {invariant_id} contractField type mismatch: "
                f"expected={expected_type}, actual={_json_value_type(actual)}"
            )
    if "expected" in field and actual != field["expected"]:
        result.errors.append(
            f"doc invariant {invariant_id} contractField mismatch: "
            f"{_contract_display_path(path)}#{pointer} expected={field['expected']!r}, actual={actual!r}"
        )
    if "allowedValues" in field:
        allowed = field["allowedValues"]
        if not isinstance(allowed, list):
            result.errors.append(f"doc invariant {invariant_id} contractField.allowedValues must be an array")
        elif actual not in allowed:
            result.errors.append(
                f"doc invariant {invariant_id} contractField value is not allowed: "
                f"{_contract_display_path(path)}#{pointer} actual={actual!r}, allowed={allowed!r}"
            )
    return True


def _check_invariant(
    item: dict[str, Any],
    active_docs: dict[str, str],
    result: CheckResult,
    contract_cache: dict[Path, Any] | None = None,
) -> bool:
    invariant_id = item.get("id")
    if not isinstance(invariant_id, str) or not invariant_id:
        result.errors.append("doc invariant must have a non-empty id")
        return False
    if contract_cache is None:
        contract_cache = {}
    _check_contract_field(item, result, contract_cache)
    owner = item.get("owner")
    references = item.get("referenceFiles")
    owner_phrases = item.get("ownerPhrases")
    forbidden = item.get("forbiddenReferencePhrases", [])
    trigger_pattern = item.get("triggerPattern")
    if not isinstance(owner, str) or not isinstance(references, list) or not isinstance(owner_phrases, list):
        result.errors.append(f"doc invariant {invariant_id} has an invalid owner/reference declaration")
        return False
    if owner in references:
        result.errors.append(f"doc invariant {invariant_id} must not list its owner as a reference file")
    files = [owner, *references]
    if len(files) != len(set(files)):
        result.errors.append(f"doc invariant {invariant_id} contains duplicate document paths")
    owner_text = active_docs.get(owner)
    if owner_text is None:
        owner_text = active_docs.get(formalize(ROOT / owner).relative_to(ROOT).as_posix())
    if owner_text is None:
        result.errors.append(f"doc invariant {invariant_id} owner is missing: {owner}")
        return False
    marker = MARKER_TEMPLATE.format(id=invariant_id)
    owner_marker_count = owner_text.count(marker)
    if owner_marker_count != 1:
        result.errors.append(
            f"doc invariant {invariant_id} must have exactly one owner marker in {owner}; found {owner_marker_count}"
        )
    missing_owner_phrases = [phrase for phrase in owner_phrases if not isinstance(phrase, str) or phrase not in owner_text]
    if missing_owner_phrases:
        result.errors.append(f"doc invariant {invariant_id} owner is missing canonical text: {missing_owner_phrases}")
    for path_value in references:
        if not isinstance(path_value, str):
            result.errors.append(f"doc invariant {invariant_id} has a non-string reference path")
            continue
        text = active_docs.get(path_value)
        if text is None:
            text = active_docs.get(formalize(ROOT / path_value).relative_to(ROOT).as_posix())
        if text is None:
            result.errors.append(f"doc invariant {invariant_id} reference is missing: {path_value}")
            continue
        marker_count = text.count(marker)
        if marker_count != 1:
            result.errors.append(
                f"doc invariant {invariant_id} must have exactly one reference marker in {path_value}; found {marker_count}"
            )
        duplicated = [phrase for phrase in forbidden if isinstance(phrase, str) and phrase in text]
        if duplicated:
            result.errors.append(
                f"doc invariant {invariant_id} is fully repeated in non-owner document {path_value}: {duplicated}"
            )
    if isinstance(trigger_pattern, str):
        try:
            pattern = re.compile(trigger_pattern)
        except re.error as exc:
            result.errors.append(f"doc invariant {invariant_id} has invalid triggerPattern: {exc}")
        else:
            for path_value, text in active_docs.items():
                if pattern.search(text) and marker not in text:
                    result.errors.append(
                        f"document {path_value} mentions invariant {invariant_id} without its marker"
                    )
    return True


def check_doc_invariant_drift(result: CheckResult) -> None:
    """Validate one canonical owner and marked summaries for each invariant."""
    initial_errors = len(result.errors)
    registry = _load_registry(result)
    if registry is None:
        return
    active_paths = [ROOT / "SKILL.md", *sorted((ROOT / "references").rglob("*.md")), ASSETS / 'package/work.template.md']
    active_docs: dict[str, str] = {}
    for path in active_paths:
        try:
            active_docs[str(path.relative_to(ROOT))] = read_check_text(path, encoding="utf-8")
        except OSError as exc:
            result.errors.append(f"cannot read active document {path.relative_to(ROOT)}: {exc}")
    ids: set[str] = set()
    owners: dict[str, int] = {}
    checked = 0
    contract_cache: dict[Path, Any] = {}
    contract_checks = 0
    for item in registry.get("invariants", []):
        if not isinstance(item, dict):
            result.errors.append("doc invariant registry entries must be objects")
            continue
        invariant_id = item.get("id")
        if isinstance(invariant_id, str):
            if invariant_id in ids:
                result.errors.append(f"doc invariant id is duplicated: {invariant_id}")
            ids.add(invariant_id)
        owner = item.get("owner")
        if isinstance(owner, str):
            owners[owner] = owners.get(owner, 0) + 1
        before = len(result.errors)
        if _check_invariant(item, active_docs, result, contract_cache):
            checked += 1
        if len(result.errors) == before:
            contract_checks += 1
    result.details["docInvariantDrift"] = {
        "registry": str(REGISTRY_PATH.relative_to(ROOT)),
        "invariants": checked,
        "activeDocuments": len(active_docs),
        "owners": owners,
        "contractChecks": contract_checks,
    }
    if len(result.errors) == initial_errors:
        result.ok("Canonical documentation invariants have one owner and marked summaries")
