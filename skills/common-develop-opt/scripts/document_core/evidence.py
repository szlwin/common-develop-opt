"""Register and verify immutable evidence for common-develop long tasks."""
from __future__ import annotations

import sys
from pathlib import Path
_COMPAT_ROOT = Path(__file__).resolve().parent.parent / "compat"
_SCRIPT_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_SCRIPT_ROOT), str(_COMPAT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)
from .persistence import (
    atomic_write_bytes,
    atomic_write_text,
    now,
    sha256_bytes,
    sha256_file as _sha256_file,
    task_write_locked,
)

import argparse
import hashlib
import io
import json
import lzma
import os
import re
import subprocess
import sys
import tarfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from document_core import agent_context
from runtime import contract_normalization
from runtime import doc_layout
from . import review_store, storage
from .persistence import load_merge_save
from runtime._core.cli_arguments import (add_agent_argument, add_doc_root_argument, add_file_argument, add_json_argument, add_repeatable_argument, add_task_dir_argument, build_parser as build_common_parser, set_handler)

ROOT = Path(__file__).resolve().parents[2]

# Historical tests and integrations inspect these bounded caches through the
# public evidence facade; keep the symbols visible after the module migration.
RUNTIME = contract_normalization.load_runtime_contract()
KNOWN_AGENTS = set(RUNTIME.get("agents", []))
KNOWN_PHASES = {item.get("id") for item in RUNTIME.get("canonicalPhases", []) if isinstance(item, dict)}
SCHEMA_VERSION = 4
CAPTURE_MODES = {"DIRECT", "SNAPSHOT", "BUNDLE", "GIT_REF", "REVIEW_REF"}
MUTABLE_EVIDENCE_BASENAMES = {
    "task_plan.json", "development_task_plan.json", "task_events.jsonl", "traceability.json", "acceptance.json", "risk.json",
    "work_record.md",
}
STATUSES = {"ACTIVE", "SUPERSEDED", "REVOKED"}
EVIDENCE_TYPES = set(contract_normalization.load_review_contract().get("evidenceTypes", {}).keys())


def evidence_types(entry: dict[str, Any]) -> set[str]:
    """Return every evidence role assigned to one physical Artifact."""
    values = entry.get("used_as_types")
    if isinstance(values, list) and values:
        return {str(value) for value in values if value}
    value = str(entry.get("type") or "")
    return {value} if value else set()

FILE_DIGEST_CACHE_LIMIT = 1024
GIT_VALIDATION_CACHE_LIMIT = 512
INDEX_VALIDATION_CACHE_LIMIT = 128

_FILE_DIGEST_CACHE: dict[tuple[str, int, int, int], str] = {}
_GIT_VALIDATION_CACHE: dict[tuple[str, str, str, str, str, int], list[str]] = {}
_INDEX_VALIDATION_CACHE: dict[tuple[str, str, str], tuple[list[str], dict[str, dict[str, Any]]]] = {}

def _cache_put(cache: dict[Any, Any], key: Any, value: Any, limit: int) -> None:
    """Insert one cache entry and evict the oldest entries above the bound."""
    cache[key] = value
    while len(cache) > limit:
        cache.pop(next(iter(cache)))

def clear_validation_caches() -> None:
    """Clear process-local validation caches between independent task runs."""
    _FILE_DIGEST_CACHE.clear()
    _GIT_VALIDATION_CACHE.clear()
    _INDEX_VALIDATION_CACHE.clear()

def sha256_file(path: Path) -> str:
    stat = path.stat()
    key = (str(path.resolve()), stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
    cached = _FILE_DIGEST_CACHE.get(key)
    if cached is not None:
        return cached
    value = _sha256_file(path)
    _cache_put(_FILE_DIGEST_CACHE, key, value, FILE_DIGEST_CACHE_LIMIT)
    return value


def version_root_from_task_dir(task_dir: Path) -> Path:
    task_dir = task_dir.resolve()
    if task_dir.parent.name != "task":
        raise ValueError(f"task directory must be version/<VERSION>/task/<TARGET>: {task_dir}")
    return task_dir.parent.parent


def resolve_task_ref(task_dir: Path, ref: str) -> Path:
    clean = str(ref or "").split("#", 1)[0].strip()
    if not clean:
        raise ValueError("empty evidence reference")
    candidate = Path(clean)
    version_root = version_root_from_task_dir(task_dir)
    doc_root = version_root.parent.parent
    project_root = doc_root.parent.resolve()
    candidates = [candidate] if candidate.is_absolute() else [
        task_dir / candidate,
        version_root / candidate,
        doc_root / candidate,
        project_root / candidate,
    ]
    outside = False
    for current in candidates:
        resolved = current.resolve()
        if not resolved.is_relative_to(project_root):
            outside = True
            continue
        if resolved.is_file():
            return resolved
    if outside:
        raise ValueError(f"evidence reference escapes project root: {ref}")
    raise ValueError(f"evidence reference does not resolve: {ref}")




def normalize_task_ref(task_dir: Path, path: Path) -> str:
    return Path(os.path.relpath(path.resolve(), task_dir.resolve())).as_posix()


def project_root_from_task_dir(task_dir: Path) -> Path:
    return version_root_from_task_dir(task_dir).parent.parent.parent.resolve()


def _run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        raise ValueError(result.stderr.decode("utf-8", errors="replace").strip() or "git command failed")
    return result


def git_member_for_source(
    task_dir: Path,
    source_path: Path,
    evidence_type: str,
) -> Optional[dict[str, Any]]:
    """Return an immutable HEAD blob reference when source bytes are committed."""
    project_root = project_root_from_task_dir(task_dir)
    probe = _run_git(project_root, "rev-parse", "--show-toplevel", check=False)
    if probe.returncode != 0:
        return None
    repo = Path(probe.stdout.decode("utf-8").strip()).resolve()
    if not source_path.resolve().is_relative_to(repo):
        return None
    relative = source_path.resolve().relative_to(repo).as_posix()
    commit_result = _run_git(repo, "rev-parse", "HEAD", check=False)
    if commit_result.returncode != 0:
        return None
    commit = commit_result.stdout.decode("utf-8").strip()
    tree = _run_git(repo, "ls-tree", commit, "--", relative, check=False)
    if tree.returncode != 0 or not tree.stdout.strip():
        return None
    first = tree.stdout.decode("utf-8", errors="replace").splitlines()[0]
    header, _, listed_path = first.partition("\t")
    parts = header.split()
    if len(parts) != 3 or parts[1] != "blob" or listed_path != relative:
        return None
    blob_oid = parts[2]
    blob = _run_git(repo, "cat-file", "blob", blob_oid).stdout
    current = source_path.read_bytes()
    if blob != current:
        return None
    return {
        "commit": commit,
        "path": relative,
        "blob_oid": blob_oid,
        "digest": sha256_bytes(blob),
        "size": len(blob),
        "type": evidence_type,
    }




def git_ref_metadata(members: list[dict[str, Any]]) -> dict[str, Any]:
    commits = {str(item["commit"]) for item in members}
    if len(commits) != 1:
        raise ValueError("Git Evidence members must belong to one commit")
    commit = next(iter(commits))
    compact_members = [
        {
            "path": str(item["path"]),
            "blob_oid": str(item["blob_oid"]),
            "digest": str(item["digest"]),
            "size": int(item["size"]),
            "type": str(item["type"]),
        }
        for item in sorted(members, key=lambda value: str(value["path"]))
    ]
    return {"repository": ".", "commit": commit, "members": compact_members}


def git_ref_digest(metadata: dict[str, Any]) -> str:
    members = metadata.get("members") if isinstance(metadata.get("members"), list) else []
    if len(members) == 1:
        return str(members[0].get("digest") or "")
    payload = json.dumps(
        {"commit": metadata.get("commit"), "members": members},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256_bytes(payload)


def validate_git_ref(task_dir: Path, entry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    git_ref = metadata.get("git_ref") if isinstance(metadata.get("git_ref"), dict) else {}
    commit = str(git_ref.get("commit") or "")
    members = git_ref.get("members") if isinstance(git_ref.get("members"), list) else []
    if not commit or not members:
        return ["GIT_REF metadata.git_ref requires commit and members"]
    if entry.get("ref") != f"git:{commit}":
        errors.append("GIT_REF ref must equal git:{commit}")
    project_root = project_root_from_task_dir(task_dir)
    probe = _run_git(project_root, "rev-parse", "--show-toplevel", check=False)
    if probe.returncode != 0:
        return errors + ["GIT_REF project is not a Git repository"]
    repo = Path(probe.stdout.decode("utf-8").strip()).resolve()
    for index, member in enumerate(members):
        prefix = f"GIT_REF member[{index}]"
        if not isinstance(member, dict):
            errors.append(f"{prefix} must be an object")
            continue
        path = str(member.get("path") or "")
        blob_oid = str(member.get("blob_oid") or "")
        cache_key = (str(repo), commit, path, blob_oid, str(member.get("digest") or ""), int(member.get("size") or 0))
        cached_errors = _GIT_VALIDATION_CACHE.get(cache_key)
        if cached_errors is not None:
            errors.extend(f"{prefix} {message}" for message in cached_errors)
            continue
        member_errors: list[str] = []
        tree = _run_git(repo, "ls-tree", commit, "--", path, check=False)
        if tree.returncode != 0 or not tree.stdout.strip():
            member_errors.append(f"path is not present at commit: {path}")
        else:
            header = tree.stdout.decode("utf-8", errors="replace").splitlines()[0].split("\t", 1)[0].split()
            actual_oid = header[2] if len(header) == 3 else ""
            if actual_oid != blob_oid:
                member_errors.append(f"blob_oid mismatch: {path}")
            else:
                blob = _run_git(repo, "cat-file", "blob", blob_oid, check=False)
                if blob.returncode != 0:
                    member_errors.append(f"blob is unreadable: {blob_oid}")
                else:
                    if sha256_bytes(blob.stdout) != member.get("digest"):
                        member_errors.append(f"digest mismatch: {path}")
                    if len(blob.stdout) != member.get("size"):
                        member_errors.append(f"size mismatch: {path}")
        _cache_put(_GIT_VALIDATION_CACHE, cache_key, member_errors, GIT_VALIDATION_CACHE_LIMIT)
        errors.extend(f"{prefix} {message}" for message in member_errors)
    if entry.get("digest") != git_ref_digest(git_ref):
        errors.append("GIT_REF aggregate digest mismatch")
    return errors


def review_ref_digest(record: dict[str, Any]) -> str:
    payload = json.dumps(
        record, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256_bytes(payload)


def validate_review_ref(task_dir: Path, entry: dict[str, Any]) -> list[str]:
    ref = str(entry.get("ref") or "")
    if not ref.startswith("review:"):
        return ["REVIEW_REF ref must use review:{REV_ID}"]
    review_id = ref.split(":", 1)[1]
    try:
        record = review_store.require_record(task_dir, review_id)
    except ValueError as exc:
        return [str(exc)]
    if review_ref_digest(record) != entry.get("digest"):
        return [f"REVIEW_REF digest mismatch: {review_id}"]
    return []


def is_mutable_evidence_source(path: Path) -> bool:
    return path.name in MUTABLE_EVIDENCE_BASENAMES




def snapshot_source(task_dir: Path, source_path: Path, evidence_type: str, revision: str) -> tuple[Path, str]:
    digest = sha256_file(source_path)
    suffix = source_path.suffix or ".snapshot"
    directory = storage.path(task_dir, "objects")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{digest}{suffix}"
    if target.exists():
        if sha256_file(target) != digest:
            raise ValueError(f"snapshot path collision: {target}")
    else:
        atomic_write_bytes(target, source_path.read_bytes())
    return target, digest



def _tar_info(name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.size = size
    info.mtime = 0
    info.mode = 0o644
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


def build_evidence_bundle(
    task_dir: Path,
    items: list[dict[str, Any]],
    *,
    revision: str,
    phase: str,
) -> tuple[Path, str, list[dict[str, Any]]]:
    """Create one deterministic content-addressed archive for multiple Evidence sources."""
    if len(items) < 2:
        raise ValueError("bundle-register requires at least two --item values")
    members: list[dict[str, Any]] = []
    payloads: list[tuple[str, bytes]] = []
    seen_sources: set[str] = set()
    normalized_items: list[tuple[str, Path, str]] = []
    for item in items:
        evidence_type = str(item.get("type") or "")
        ref = str(item.get("ref") or "")
        if evidence_type not in EVIDENCE_TYPES:
            raise ValueError(f"unknown evidence type in bundle: {evidence_type}")
        source_path = resolve_task_ref(task_dir, ref)
        source_ref = normalize_task_ref(task_dir, source_path)
        if source_ref in seen_sources:
            raise ValueError(f"duplicate bundle source: {source_ref}")
        seen_sources.add(source_ref)
        normalized_items.append((source_ref, source_path, evidence_type))
    normalized_items.sort(key=lambda value: value[0])
    for index, (source_ref, source_path, evidence_type) in enumerate(normalized_items, 1):
        data = source_path.read_bytes()
        suffix = source_path.suffix or ".snapshot"
        archive_member = f"files/{index:04d}-{sha256_bytes(source_ref.encode('utf-8'))[:16]}{suffix}"
        member = {
            "source_ref": source_ref,
            "archive_member": archive_member,
            "digest": sha256_bytes(data),
            "size": len(data),
            "type": evidence_type,
        }
        members.append(member)
        payloads.append((archive_member, data))
    manifest = {
        "schema_version": 1,
        "kind": "evidence_bundle",
        "revision": revision,
        "phase": phase,
        "members": members,
    }
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8") + b"\n"
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode="w", format=tarfile.PAX_FORMAT) as archive:
        archive.addfile(_tar_info("manifest.json", len(manifest_bytes)), io.BytesIO(manifest_bytes))
        for name, data in payloads:
            archive.addfile(_tar_info(name, len(data)), io.BytesIO(data))
    compressed = lzma.compress(tar_buffer.getvalue(), format=lzma.FORMAT_XZ, preset=6)
    digest = sha256_bytes(compressed)
    directory = storage.path(task_dir, "objects")
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{digest}.tar.xz"
    if target.exists():
        if sha256_file(target) != digest:
            raise ValueError(f"bundle path collision: {target}")
    else:
        atomic_write_bytes(target, compressed)
    return target, digest, members


def validate_evidence_bundle(path: Path, entry: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
    expected_members = metadata.get("members") if isinstance(metadata.get("members"), list) else []
    if not expected_members:
        return ["BUNDLE evidence metadata.members must be a non-empty array"]
    try:
        raw = lzma.decompress(path.read_bytes(), format=lzma.FORMAT_XZ)
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            names = archive.getnames()
            if "manifest.json" not in names:
                return ["BUNDLE archive missing manifest.json"]
            if any(Path(name).is_absolute() or ".." in Path(name).parts for name in names):
                errors.append("BUNDLE archive contains unsafe member path")
            manifest_stream = archive.extractfile("manifest.json")
            if manifest_stream is None:
                return ["BUNDLE archive manifest is unreadable"]
            manifest = json.loads(manifest_stream.read().decode("utf-8"))
            if manifest.get("kind") != "evidence_bundle" or manifest.get("schema_version") != 1:
                errors.append("BUNDLE manifest contract is invalid")
            if manifest.get("revision") != entry.get("revision"):
                errors.append("BUNDLE manifest revision differs from Evidence")
            if manifest.get("phase") != entry.get("phase"):
                errors.append("BUNDLE manifest phase differs from Evidence")
            actual_members = manifest.get("members") if isinstance(manifest.get("members"), list) else []
            if actual_members != expected_members:
                errors.append("BUNDLE manifest members differ from Evidence metadata")
            for member in actual_members:
                if not isinstance(member, dict):
                    errors.append("BUNDLE member must be an object")
                    continue
                name = str(member.get("archive_member") or "")
                if not name or name not in names:
                    errors.append(f"BUNDLE member is missing: {name}")
                    continue
                stream = archive.extractfile(name)
                if stream is None:
                    errors.append(f"BUNDLE member is unreadable: {name}")
                    continue
                data = stream.read()
                if sha256_bytes(data) != member.get("digest"):
                    errors.append(f"BUNDLE member digest mismatch: {name}")
                if len(data) != member.get("size"):
                    errors.append(f"BUNDLE member size mismatch: {name}")
                if member.get("type") not in EVIDENCE_TYPES:
                    errors.append(f"BUNDLE member type is invalid: {member.get('type')}")
    except (OSError, lzma.LZMAError, tarfile.TarError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        errors.append(f"invalid BUNDLE archive: {exc}")
    return errors

def registry_path(task_dir: Path) -> Path:
    return storage.path(task_dir, "registry")


def default_registry(task_dir: Path, target_id: str = "", version: str = "") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "target_id": target_id or task_dir.name,
        "version": version or version_root_from_task_dir(task_dir).name,
        "evidences": [],
    }


def ensure_registry(task_dir: Path, target_id: str = "", version: str = "") -> Path:
    storage.assert_new_project(Path(task_dir), require_events=False)
    path = registry_path(task_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        atomic_write_text(path, json.dumps({"kind": "EVIDENCE_REGISTRY", **default_registry(task_dir, target_id, version)}, ensure_ascii=False, separators=(",", ":")) + "\n")
    return path



def _expand_stored_index(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    scope_sets = value.get("scope_sets") if isinstance(value.get("scope_sets"), list) else []
    agent_sets = value.get("agent_sets") if isinstance(value.get("agent_sets"), list) else []
    expanded: list[dict[str, Any]] = []
    for raw in value.get("evidences") or []:
        if not isinstance(raw, dict):
            expanded.append(raw)
            continue
        entry = dict(raw)
        scope_ref = entry.pop("s", None)
        agent_ref = entry.pop("a", None)
        if "scope_refs" not in entry:
            entry["scope_refs"] = list(scope_sets[scope_ref]) if isinstance(scope_ref, int) and 0 <= scope_ref < len(scope_sets) else []
        if "used_by_agents" not in entry:
            if isinstance(agent_ref, int) and 0 <= agent_ref < len(agent_sets):
                entry["used_by_agents"] = list(agent_sets[agent_ref])
            else:
                entry["used_by_agents"] = [str(entry.get("generated_by_agent") or "")]
        entry.setdefault("used_as_types", [str(entry.get("type") or "")])
        entry.setdefault("source_ref", "")
        entry.setdefault("command_result_ref", "")
        entry.setdefault("command_result_digest", "")
        entry.setdefault("status", "ACTIVE")
        entry.setdefault("superseded_by", "")
        entry.setdefault("metadata", {})
        git_ref = entry.get("metadata", {}).get("git_ref") if isinstance(entry.get("metadata"), dict) else None
        if isinstance(git_ref, dict):
            git_ref.setdefault("repository", ".")
            members = git_ref.get("members") if isinstance(git_ref.get("members"), list) else []
            if len(members) == 1 and isinstance(members[0], dict):
                members[0].setdefault("digest", str(entry.get("digest") or ""))
                members[0].setdefault("type", str(entry.get("type") or ""))
        expanded.append(entry)
    result.pop("scope_sets", None)
    result.pop("agent_sets", None)
    result["evidences"] = expanded
    return result


def _compact_index_for_storage(value: dict[str, Any]) -> dict[str, Any]:
    stored: list[dict[str, Any]] = []
    for raw in value.get("evidences") or []:
        if not isinstance(raw, dict):
            stored.append(raw)
            continue
        entry = dict(raw)
        # Keep relationship fields on each JSONL record.  A registry line must
        # remain independently readable; global scope/agent tables would make
        # append-only updates lossy and force whole-file rewrites.
        used_by = list(entry.get("used_by_agents", []) or [])
        if used_by == [str(entry.get("generated_by_agent") or "")]:
            entry.pop("used_by_agents", None)
        if entry.get("used_as_types") == [entry.get("type")]:
            entry.pop("used_as_types", None)
        for key, default in (
            ("source_ref", ""), ("command_result_ref", ""), ("command_result_digest", ""),
            ("status", "ACTIVE"), ("superseded_by", ""),
        ):
            if entry.get(key, default) == default:
                entry.pop(key, None)
        metadata = dict(entry.get("metadata") or {}) if isinstance(entry.get("metadata"), dict) else {}
        git_ref = metadata.get("git_ref") if isinstance(metadata.get("git_ref"), dict) else None
        if isinstance(git_ref, dict):
            git_ref = dict(git_ref)
            if git_ref.get("repository") == ".":
                git_ref.pop("repository", None)
            members = []
            for member in git_ref.get("members") or []:
                if not isinstance(member, dict):
                    continue
                compact_member = dict(member)
                if len(git_ref.get("members") or []) == 1:
                    if compact_member.get("digest") == entry.get("digest"):
                        compact_member.pop("digest", None)
                    if compact_member.get("type") == entry.get("type"):
                        compact_member.pop("type", None)
                members.append(compact_member)
            git_ref["members"] = members
            metadata["git_ref"] = git_ref
        if metadata:
            entry["metadata"] = metadata
        else:
            entry.pop("metadata", None)
        stored.append(entry)
    result = {
        "schema_version": value.get("schema_version", SCHEMA_VERSION),
        "target_id": value.get("target_id", ""),
        "version": value.get("version", ""),
        "evidences": stored,
    }
    return result

def load_registry(task_dir: Path) -> dict[str, Any]:
    path = ensure_registry(task_dir)
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            raise ValueError("empty evidence registry")
        header = json.loads(lines[0])
        if not isinstance(header, dict) or header.get("kind") != "EVIDENCE_REGISTRY":
            raise ValueError("evidence registry must start with EVIDENCE_REGISTRY header")
        value = {"schema_version": header.get("schema_version", SCHEMA_VERSION), "target_id": header.get("target_id", ""), "version": header.get("version", ""), "evidences": [json.loads(line) for line in lines[1:]]}
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid evidence index: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("evidence index must be an object")
    return _expand_stored_index(value)


@task_write_locked
def save_registry(task_dir: Path, value: dict[str, Any]) -> None:
    _INDEX_VALIDATION_CACHE.clear()
    path = ensure_registry(task_dir)
    compact = _compact_index_for_storage(value)
    header = {"kind": "EVIDENCE_REGISTRY", "schema_version": compact.get("schema_version", SCHEMA_VERSION), "target_id": compact.get("target_id", ""), "version": compact.get("version", "")}
    lines = [json.dumps(header, ensure_ascii=False, separators=(",", ":"))]
    lines.extend(json.dumps(item, ensure_ascii=False, separators=(",", ":")) for item in compact.get("evidences", []))
    atomic_write_text(path, "\n".join(lines) + "\n")


def entries_by_id(index: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries = index.get("evidences", [])
    if not isinstance(entries, list):
        return {}
    return {
        str(item.get("evidence_id")): item
        for item in entries
        if isinstance(item, dict) and isinstance(item.get("evidence_id"), str)
    }


def next_id(index: dict[str, Any]) -> str:
    highest = 0
    for evidence_id in entries_by_id(index):
        if evidence_id.startswith("EVD-") and evidence_id[4:].isdigit():
            highest = max(highest, int(evidence_id[4:]))
    return f"EVD-{highest + 1:06d}"


def _validation_dependency_key(
    task_dir: Path,
    index: dict[str, Any],
    *,
    current_revisions: Optional[set[str]] = None,
) -> tuple[tuple[str, str, str] | None, dict[str, str]]:
    """Fingerprint the registry and every mutable local artifact it validates.

    Stat-only cache keys can reuse a clean result after content tampering.  The
    dependency digest is deliberately content based; local files are small and
    Git-backed evidence has its own immutable-object cache.
    """
    index_file = registry_path(task_dir)
    local_digests: dict[str, str] = {}
    try:
        index_digest = sha256_bytes(index_file.read_bytes())
    except OSError:
        return None, local_digests
    dependencies: set[Path] = set()
    entries = index.get("evidences") if isinstance(index.get("evidences"), list) else []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        # Historical Evidence remains available by ID for explicit audit, but
        # ordinary lifecycle validation must not repeatedly stat/hash every
        # superseded revision. Keep the index in the result while excluding
        # old revisions from the dependency digest.
        if current_revisions is not None and str(entry.get("revision") or "") not in current_revisions:
            continue
        command_ref = str(entry.get("command_result_ref") or "")
        if command_ref:
            try:
                dependencies.add(resolve_task_ref(task_dir, command_ref).resolve())
            except ValueError:
                pass
        mode = str(entry.get("capture_mode") or "")
        if mode in {"GIT_REF", "REVIEW_REF"}:
            if mode == "REVIEW_REF":
                dependencies.add(review_store.registry_path(task_dir).resolve())
            continue
        ref = str(entry.get("ref") or "")
        if ref:
            try:
                dependencies.add(resolve_task_ref(task_dir, ref).resolve())
            except ValueError:
                pass
    dependency_hash = hashlib.sha256()
    if current_revisions is not None:
        dependency_hash.update(b"CURRENT_REVISIONS\0")
        for revision in sorted(current_revisions):
            dependency_hash.update(revision.encode("utf-8"))
            dependency_hash.update(b"\0")
    for path in sorted(dependencies, key=lambda item: str(item)):
        dependency_hash.update(str(path).encode("utf-8"))
        dependency_hash.update(b"\0")
        try:
            digest = sha256_bytes(path.read_bytes())
        except OSError as exc:
            digest = f"MISSING:{type(exc).__name__}"
        local_digests[str(path)] = digest
        dependency_hash.update(digest.encode("utf-8"))
        dependency_hash.update(b"\0")
    return (str(index_file.resolve()), index_digest, dependency_hash.hexdigest()), local_digests


def validate_registry(
    task_dir: Path,
    value: Optional[dict[str, Any]] = None,
    *,
    current_revisions: Optional[set[str]] = None,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    errors: list[str] = []
    try:
        index = value if value is not None else load_registry(task_dir)
    except ValueError as exc:
        return [str(exc)], {}
    cache_key: tuple[str, str, str] | None = None
    local_digests: dict[str, str] = {}
    if value is None:
        cache_key, local_digests = _validation_dependency_key(
            task_dir, index, current_revisions=current_revisions
        )
        if cache_key is not None:
            cached = _INDEX_VALIDATION_CACHE.get(cache_key)
            if cached is not None:
                return cached
    if index.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"evidence index schema_version must be {SCHEMA_VERSION}")
    if not isinstance(index.get("target_id"), str) or not index.get("target_id"):
        errors.append("evidence index missing target_id")
    if not isinstance(index.get("version"), str) or not index.get("version"):
        errors.append("evidence index missing version")
    entries = index.get("evidences")
    if not isinstance(entries, list):
        errors.append("evidence index evidences must be an array")
        return errors, {}
    result: dict[str, dict[str, Any]] = {}
    allowed_fields = {
        "evidence_id", "type", "ref", "source_ref", "capture_mode", "digest", "revision", "phase", "generated_by_agent",
        "generated_at", "command_result_ref", "command_result_digest", "scope_refs", "used_by_agents", "used_as_types", "status", "superseded_by", "metadata",
    }
    for i, entry in enumerate(entries):
        prefix = f"evidence-index[{i}]"
        if not isinstance(entry, dict):
            errors.append(f"{prefix} must be an object")
            continue
        unknown = sorted(set(entry) - allowed_fields)
        if unknown:
            errors.append(f"{prefix} contains unknown fields: {unknown}")
        evidence_id = entry.get("evidence_id")
        if not isinstance(evidence_id, str) or not evidence_id:
            errors.append(f"{prefix} missing evidence_id")
            continue
        if not re.fullmatch(r"EVD-[0-9]{6}", evidence_id):
            errors.append(f"{prefix} invalid evidence_id: {evidence_id}")
        if evidence_id in result:
            errors.append(f"duplicate evidence_id: {evidence_id}")
        result[evidence_id] = entry
        if entry.get("type") not in EVIDENCE_TYPES:
            errors.append(f"{prefix} invalid type: {entry.get('type')}")
        used_as_types = entry.get("used_as_types")
        if (
            not isinstance(used_as_types, list) or not used_as_types
            or any(value not in EVIDENCE_TYPES for value in used_as_types)
            or len(set(used_as_types)) != len(used_as_types)
            or entry.get("type") not in used_as_types
        ):
            errors.append(f"{prefix} used_as_types must be unique declared types including type")
        capture_mode = entry.get("capture_mode")
        source_ref = entry.get("source_ref")
        if capture_mode not in CAPTURE_MODES:
            errors.append(f"{prefix} invalid capture_mode: {capture_mode}")
        if not isinstance(source_ref, str):
            errors.append(f"{prefix} source_ref must be a string")
        if capture_mode == "SNAPSHOT" and not source_ref:
            errors.append(f"{prefix} SNAPSHOT evidence requires source_ref")
        if capture_mode == "BUNDLE" and source_ref:
            errors.append(f"{prefix} BUNDLE evidence source_ref must be empty; members belong in metadata")
        if capture_mode == "GIT_REF" and not (
            isinstance(entry.get("metadata"), dict)
            and isinstance(entry.get("metadata", {}).get("git_ref"), dict)
        ):
            errors.append(f"{prefix} GIT_REF requires metadata.git_ref")
        if capture_mode == "REVIEW_REF" and source_ref:
            errors.append(f"{prefix} REVIEW_REF source_ref must be empty")
        for key in ("ref", "digest", "revision", "phase", "generated_by_agent", "generated_at"):
            if not isinstance(entry.get(key), str) or not entry.get(key):
                errors.append(f"{prefix} missing {key}")
        if not isinstance(entry.get("digest"), str) or not re.fullmatch(r"[a-f0-9]{64}", str(entry.get("digest") or "")):
            errors.append(f"{prefix} digest must be a SHA-256 hex digest")
        generated_at = entry.get("generated_at")
        if isinstance(generated_at, str) and generated_at:
            try:
                parsed = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    errors.append(f"{prefix} generated_at requires timezone")
            except ValueError:
                errors.append(f"{prefix} generated_at is not ISO-8601")
        if entry.get("generated_by_agent") not in KNOWN_AGENTS:
            errors.append(f"{prefix} unknown generated_by_agent: {entry.get('generated_by_agent')}")
        used_by_agents = entry.get("used_by_agents")
        if (
            not isinstance(used_by_agents, list) or not used_by_agents
            or any(agent not in KNOWN_AGENTS for agent in used_by_agents)
            or len(set(used_by_agents)) != len(used_by_agents)
        ):
            errors.append(f"{prefix} used_by_agents must contain unique declared Agents")
        if entry.get("phase") not in KNOWN_PHASES:
            errors.append(f"{prefix} unknown phase: {entry.get('phase')}")
        if entry.get("status") not in STATUSES:
            errors.append(f"{prefix} invalid status: {entry.get('status')}")
        scope_refs = entry.get("scope_refs")
        if (
            not isinstance(scope_refs, list)
            or any(not isinstance(x, str) or not x for x in scope_refs)
            or len(set(scope_refs)) != len(scope_refs)
        ):
            errors.append(f"{prefix} scope_refs must be a unique string array")
        validate_content = (
            current_revisions is None
            or str(entry.get("revision") or "") in current_revisions
        )
        command_result_ref = entry.get("command_result_ref", "")
        command_result_digest = entry.get("command_result_digest", "")
        if not isinstance(command_result_ref, str):
            errors.append(f"{prefix} command_result_ref must be a string")
        elif command_result_ref and validate_content:
            if not isinstance(command_result_digest, str) or not re.fullmatch(r"[a-f0-9]{64}", command_result_digest):
                errors.append(f"{prefix} command_result_digest must be a SHA-256 hex digest")
            try:
                command_result_path = resolve_task_ref(task_dir, command_result_ref)
                if validate_content and isinstance(command_result_digest, str) and entry.get("status") == "ACTIVE":
                    actual_digest = local_digests.get(str(command_result_path.resolve())) or sha256_file(command_result_path)
                    if actual_digest != command_result_digest:
                        errors.append(f"{prefix} command_result_digest mismatch: {evidence_id}")
            except ValueError as exc:
                errors.append(f"{prefix} {exc}")
        elif command_result_ref and not validate_content:
            # Keep historical command metadata addressable without resolving
            # the old file on every current lifecycle gate.
            pass
        elif command_result_digest not in ("", None):
            errors.append(f"{prefix} command_result_digest requires command_result_ref")
        if not isinstance(entry.get("superseded_by", ""), str):
            errors.append(f"{prefix} superseded_by must be a string")
        if not isinstance(entry.get("metadata", {}), dict):
            errors.append(f"{prefix} metadata must be an object")
        if capture_mode == "GIT_REF" and validate_content:
            errors.extend(f"{prefix} {message}" for message in validate_git_ref(task_dir, entry))
        elif capture_mode == "REVIEW_REF" and validate_content:
            errors.extend(f"{prefix} {message}" for message in validate_review_ref(task_dir, entry))
        elif validate_content:
            try:
                path = resolve_task_ref(task_dir, str(entry.get("ref") or ""))
                if capture_mode == "DIRECT" and is_mutable_evidence_source(path):
                    errors.append(f"{prefix} mutable evidence source requires SNAPSHOT or GIT_REF capture: {path.name}")
                normalized_path = normalize_task_ref(task_dir, path)
                if capture_mode in {"SNAPSHOT", "BUNDLE"} and "evidence/objects/" not in normalized_path:
                    errors.append(f"{prefix} immutable object ref must be under evidence/objects")
                # A superseded DIRECT reference intentionally points at a file that may
                # have changed. Its historical digest is retained for audit linkage;
                # only active evidence is required to match the mutable working file.
                if entry.get("digest") and entry.get("status") == "ACTIVE":
                    actual_digest = local_digests.get(str(path.resolve())) or sha256_file(path)
                    if actual_digest != entry.get("digest"):
                        errors.append(f"{prefix} digest mismatch: {evidence_id}")
                if capture_mode == "BUNDLE":
                    errors.extend(f"{prefix} {message}" for message in validate_evidence_bundle(path, entry))
            except ValueError as exc:
                errors.append(f"{prefix} {exc}")
        if entry.get("status") == "ACTIVE" and entry.get("superseded_by"):
            errors.append(f"{prefix} ACTIVE evidence must not have superseded_by")
        if entry.get("status") == "SUPERSEDED" and not entry.get("superseded_by"):
            errors.append(f"{prefix} SUPERSEDED evidence requires superseded_by")
    for evidence_id, entry in result.items():
        superseded_by = entry.get("superseded_by")
        if superseded_by and superseded_by not in result:
            errors.append(f"evidence {evidence_id} superseded_by unknown evidence: {superseded_by}")
    outcome = (errors, result)
    if cache_key is not None:
        _INDEX_VALIDATION_CACHE.clear()
        _cache_put(_INDEX_VALIDATION_CACHE, cache_key, outcome, INDEX_VALIDATION_CACHE_LIMIT)
    return outcome


@task_write_locked
def register_evidence(
    task_dir: Path,
    *,
    agent: str,
    evidence_type: str,
    ref: str,
    revision: str,
    phase: str,
    scope_refs: Optional[list[str]] = None,
    command_result_ref: str = "",
    metadata: Optional[dict[str, Any]] = None,
    capture_mode: str = "AUTO",
) -> dict[str, Any]:
    if agent not in KNOWN_AGENTS:
        raise ValueError(f"unknown Agent: {agent}")
    if evidence_type not in EVIDENCE_TYPES:
        raise ValueError(f"unknown evidence type: {evidence_type}")
    if not revision:
        raise ValueError("revision is required")
    if phase not in KNOWN_PHASES:
        raise ValueError(f"unknown phase: {phase}")
    command_result_digest = ""
    normalized_command_result_ref = ""
    if command_result_ref:
        command_result_path = resolve_task_ref(task_dir, command_result_ref)
        command_result_digest = sha256_file(command_result_path)
        normalized_command_result_ref = Path(
            os.path.relpath(command_result_path, task_dir.resolve())
        ).as_posix()
    source_path = resolve_task_ref(task_dir, ref)
    requested_mode = str(capture_mode or "AUTO").upper()
    if requested_mode not in CAPTURE_MODES | {"AUTO"}:
        raise ValueError(f"unknown evidence capture mode: {capture_mode}")
    git_member = git_member_for_source(task_dir, source_path, evidence_type)
    if requested_mode == "GIT_REF" and git_member is None:
        raise ValueError("GIT_REF requires source bytes to match a tracked file at HEAD")
    if requested_mode == "AUTO" and git_member is not None:
        resolved_mode = "GIT_REF"
    elif requested_mode == "SNAPSHOT" or (requested_mode == "AUTO" and is_mutable_evidence_source(source_path)):
        resolved_mode = "SNAPSHOT"
    else:
        resolved_mode = requested_mode if requested_mode != "AUTO" else "DIRECT"
    if resolved_mode == "DIRECT" and is_mutable_evidence_source(source_path):
        raise ValueError(f"mutable evidence source requires SNAPSHOT capture: {source_path.name}")
    normalized_source_ref = normalize_task_ref(task_dir, source_path) if resolved_mode in {"SNAPSHOT", "GIT_REF"} else ""
    if resolved_mode == "SNAPSHOT":
        path, digest = snapshot_source(task_dir, source_path, evidence_type, revision)
        normalized_ref = normalize_task_ref(task_dir, path)
        stable_metadata = metadata or {}
    elif resolved_mode == "GIT_REF":
        assert git_member is not None
        git_metadata = git_ref_metadata([git_member])
        digest = git_ref_digest(git_metadata)
        normalized_ref = f"git:{git_metadata['commit']}"
        stable_metadata = dict(metadata or {})
        stable_metadata["git_ref"] = git_metadata
    else:
        path, digest = source_path, sha256_file(source_path)
        normalized_ref = normalize_task_ref(task_dir, path)
        stable_metadata = metadata or {}
    scopes = list(dict.fromkeys(scope_refs or []))

    def merge(index: dict[str, Any]) -> dict[str, Any]:
        for item in index.get("evidences", []):
            if not isinstance(item, dict):
                continue
            # One physical artifact/revision/phase is registered once. Reviewer and
            # criterion usage is a relationship, not a new Evidence entity.
            if (
                item.get("status") == "ACTIVE"
                and item.get("ref") == normalized_ref
                and item.get("source_ref", "") == normalized_source_ref
                and item.get("capture_mode") == resolved_mode
                and item.get("digest") == digest
                and item.get("revision") == revision
                and item.get("phase") == phase
                and item.get("command_result_ref", "") == normalized_command_result_ref
                and item.get("command_result_digest", "") == command_result_digest
            ):
                item["scope_refs"] = list(dict.fromkeys(list(item.get("scope_refs") or []) + scopes))
                item["used_by_agents"] = list(dict.fromkeys(list(item.get("used_by_agents") or [item.get("generated_by_agent")]) + [agent]))
                item["used_as_types"] = list(dict.fromkeys(list(item.get("used_as_types") or [item.get("type")]) + [evidence_type]))
                if stable_metadata:
                    stable = item.setdefault("metadata", {})
                    if isinstance(stable, dict):
                        for key, value in stable_metadata.items():
                            stable.setdefault(key, value)
                return item
        entry = {
            "evidence_id": next_id(index),
            "type": evidence_type,
            "ref": normalized_ref,
            "source_ref": normalized_source_ref,
            "capture_mode": resolved_mode,
            "digest": digest,
            "revision": revision,
            "phase": phase,
            "generated_by_agent": agent,
            "used_by_agents": [agent],
            "used_as_types": [evidence_type],
            "generated_at": now(),
            "command_result_ref": normalized_command_result_ref,
            "command_result_digest": command_result_digest,
            "scope_refs": scopes,
            "status": "ACTIVE",
            "superseded_by": "",
            "metadata": stable_metadata,
        }
        index.setdefault("evidences", []).append(entry)
        return entry

    return load_merge_save(
        lambda: load_registry(task_dir),
        lambda value: save_registry(task_dir, value),
        merge,
    )


@task_write_locked
def register_bundle(
    task_dir: Path,
    *,
    agent: str,
    items: list[dict[str, Any]],
    revision: str,
    phase: str,
    scope_refs: Optional[list[str]] = None,
    command_result_ref: str = "",
) -> dict[str, Any]:
    if agent not in KNOWN_AGENTS:
        raise ValueError(f"unknown Agent: {agent}")
    if not revision:
        raise ValueError("revision is required")
    if phase not in KNOWN_PHASES:
        raise ValueError(f"unknown phase: {phase}")
    git_members: list[dict[str, Any]] = []
    for item in items:
        evidence_type = str(item.get("type") or "")
        source = resolve_task_ref(task_dir, str(item.get("ref") or ""))
        member = git_member_for_source(task_dir, source, evidence_type)
        if member is None:
            git_members = []
            break
        git_members.append(member)
    if len(git_members) == len(items) and len(git_members) >= 2:
        git_metadata = git_ref_metadata(git_members)
        digest = git_ref_digest(git_metadata)
        members = git_metadata["members"]
        capture_mode = "GIT_REF"
        normalized_ref = f"git:{git_metadata['commit']}"
        entry_metadata = {"git_ref": git_metadata}
    else:
        path, digest, members = build_evidence_bundle(
            task_dir, items, revision=revision, phase=phase
        )
        capture_mode = "BUNDLE"
        normalized_ref = normalize_task_ref(task_dir, path)
        entry_metadata = {"bundle_schema_version": 1, "members": members}
    command_result_digest = ""
    normalized_command_result_ref = ""
    if command_result_ref:
        command_result_path = resolve_task_ref(task_dir, command_result_ref)
        command_result_digest = sha256_file(command_result_path)
        normalized_command_result_ref = normalize_task_ref(task_dir, command_result_path)
    types = list(dict.fromkeys(str(member["type"]) for member in members))
    scopes = list(dict.fromkeys(scope_refs or []))
    def merge(index: dict[str, Any]) -> dict[str, Any]:
        for item in index.get("evidences", []):
            if not isinstance(item, dict):
                continue
            if (
                item.get("status") == "ACTIVE"
                and item.get("ref") == normalized_ref
                and item.get("capture_mode") == capture_mode
                and item.get("digest") == digest
                and item.get("revision") == revision
                and item.get("phase") == phase
                and item.get("command_result_ref", "") == normalized_command_result_ref
                and item.get("command_result_digest", "") == command_result_digest
            ):
                item["scope_refs"] = list(dict.fromkeys(list(item.get("scope_refs") or []) + scopes))
                item["used_by_agents"] = list(dict.fromkeys(list(item.get("used_by_agents") or []) + [agent]))
                item["used_as_types"] = list(dict.fromkeys(list(item.get("used_as_types") or []) + types))
                return item
        entry = {
            "evidence_id": next_id(index),
            "type": types[0],
            "ref": normalized_ref,
            "source_ref": "",
            "capture_mode": capture_mode,
            "digest": digest,
            "revision": revision,
            "phase": phase,
            "generated_by_agent": agent,
            "used_by_agents": [agent],
            "used_as_types": types,
            "generated_at": now(),
            "command_result_ref": normalized_command_result_ref,
            "command_result_digest": command_result_digest,
            "scope_refs": scopes,
            "status": "ACTIVE",
            "superseded_by": "",
            "metadata": entry_metadata,
        }
        index.setdefault("evidences", []).append(entry)
        return entry

    return load_merge_save(
        lambda: load_registry(task_dir),
        lambda value: save_registry(task_dir, value),
        merge,
    )


@task_write_locked
def change_status(
    task_dir: Path,
    *,
    evidence_id: str,
    status: str,
    replacement_id: str = "",
) -> dict[str, Any]:
    if status not in {"SUPERSEDED", "REVOKED"}:
        raise ValueError("evidence status change must be SUPERSEDED or REVOKED")
    def merge(index: dict[str, Any]) -> dict[str, Any]:
        entries = entries_by_id(index)
        entry = entries.get(evidence_id)
        if not entry:
            raise ValueError(f"unknown evidence_id: {evidence_id}")
        if entry.get("status") != "ACTIVE":
            raise ValueError(f"only ACTIVE evidence can change status: {evidence_id}")
        if status == "SUPERSEDED":
            replacement = entries.get(replacement_id)
            if not replacement:
                raise ValueError(f"unknown replacement evidence_id: {replacement_id}")
            if replacement.get("status") != "ACTIVE":
                raise ValueError(f"replacement evidence is not ACTIVE: {replacement_id}")
            if (
                replacement.get("revision") == entry.get("revision")
                and replacement.get("digest") == entry.get("digest")
                and replacement.get("ref") == entry.get("ref")
            ):
                raise ValueError("replacement evidence must represent a different revision, content, or canonical ref")
            entry["superseded_by"] = replacement_id
        else:
            if replacement_id:
                raise ValueError("REVOKED evidence must not specify replacement_id")
            entry["superseded_by"] = ""
        entry["status"] = status
        return entry

    return load_merge_save(
        lambda: load_registry(task_dir),
        lambda value: save_registry(task_dir, value),
        merge,
    )


@task_write_locked
def repair_history(
    task_dir: Path,
    *,
    evidence_id: str,
) -> dict[str, Any]:
    """Rebind a historical DIRECT record to its existing immutable object.

    This is deliberately narrower than status migration: the Evidence ID and
    historical digest remain unchanged, while the canonical source path is
    retained in ``source_ref`` and ``ref`` becomes the content-addressed
    object.  Only a ProjectManagerAgent may invoke the CLI wrapper.
    """
    task_dir = Path(task_dir).resolve()

    def merge(index: dict[str, Any]) -> dict[str, Any]:
        entry = entries_by_id(index).get(evidence_id)
        if not isinstance(entry, dict):
            raise ValueError(f"unknown evidence_id: {evidence_id}")
        mode = str(entry.get("capture_mode") or "")
        if mode == "SNAPSHOT":
            # Idempotent re-runs are allowed, but still verify the immutable
            # object before returning the record.
            object_path = resolve_task_ref(task_dir, str(entry.get("ref") or ""))
            if not object_path.is_relative_to(storage.path(task_dir, "objects").resolve()):
                raise ValueError(f"SNAPSHOT evidence object is outside evidence/objects: {entry.get('ref')}")
            if sha256_file(object_path) != entry.get("digest"):
                raise ValueError(f"evidence object digest mismatch: {evidence_id}")
            return entry
        if mode != "DIRECT":
            raise ValueError(f"history repair requires ACTIVE DIRECT evidence: {evidence_id}")
        if entry.get("status") != "ACTIVE":
            raise ValueError(f"history repair requires ACTIVE evidence: {evidence_id}")
        digest = str(entry.get("digest") or "")
        if not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError(f"history repair requires a valid evidence digest: {evidence_id}")
        objects = storage.path(task_dir, "objects").resolve()
        candidates = [path for path in objects.glob(f"{digest}.*") if path.is_file()]
        matches = [path for path in candidates if sha256_file(path) == digest]
        if len(matches) != 1:
            raise ValueError(
                f"history repair requires exactly one matching immutable object for {evidence_id}; "
                f"found {len(matches)}"
            )
        source_path = resolve_task_ref(task_dir, str(entry.get("ref") or ""))
        source_ref = normalize_task_ref(task_dir, source_path)
        metadata = dict(entry.get("metadata") or {})
        metadata.setdefault("history_repair", {
            "kind": "DIRECT_TO_SNAPSHOT",
            "repaired_at": now(),
            "original_ref": str(entry.get("ref") or ""),
        })
        entry["source_ref"] = source_ref
        entry["capture_mode"] = "SNAPSHOT"
        entry["ref"] = normalize_task_ref(task_dir, matches[0])
        entry["metadata"] = metadata
        return entry

    return load_merge_save(
        lambda: load_registry(task_dir),
        lambda value: save_registry(task_dir, value),
        merge,
    )


def require_evidence(
    task_dir: Path,
    evidence_id: str,
    *,
    expected_revision: str = "",
    allowed_types: Optional[set[str]] = None,
    expected_phase: str = "",
    allow_historical: bool = False,
    validated_entries: Optional[dict[str, dict[str, Any]]] = None,
) -> dict[str, Any]:
    if validated_entries is None:
        # Current lifecycle gates always provide an expected revision.  Scope
        # the registry validation to that revision so unrelated historical
        # entries remain addressable for audit without repeatedly resolving
        # their old files and digests.  Callers without a revision retain the
        # explicit full-registry validation path.
        errors, entries = validate_registry(
            task_dir,
            current_revisions={str(expected_revision)} if expected_revision else None,
        )
        if errors:
            raise ValueError("; ".join(errors))
    else:
        entries = validated_entries
    entry = entries.get(evidence_id)
    if not entry:
        raise ValueError(f"unknown evidence_id: {evidence_id}")
    if not allow_historical:
        # Amendment-scoped invalidation is derived from the append-only event
        # ledger.  It deliberately does not mutate the immutable registry, but
        # every current lifecycle gate must reject the ID until replacement
        # Evidence is registered for the new revision.
        try:
            from runtime._long_task import task_events
            reduced = task_events.reduce_events(task_events.read_events(task_dir))
            current = (task_events.project_state({}, reduced).get("active_amendment") or {})
            invalidated = set(current.get("invalidated_evidence_ids") or []) if isinstance(current, dict) else set()
        except (OSError, ValueError, KeyError) as exc:
            raise ValueError(f"cannot determine current Amendment Evidence policy: {exc}") from exc
        if evidence_id in invalidated:
            raise ValueError(f"evidence is invalidated by the current Amendment: {evidence_id}")
    if not allow_historical and entry.get("status") != "ACTIVE":
        raise ValueError(f"evidence is not ACTIVE: {evidence_id}")
    if expected_revision and entry.get("revision") != expected_revision:
        raise ValueError(
            f"evidence revision mismatch: {evidence_id} expected={expected_revision} actual={entry.get('revision')}"
        )
    if allowed_types and not (evidence_types(entry) & allowed_types):
        raise ValueError(
            f"evidence type is not allowed: {evidence_id} "
            f"types={sorted(evidence_types(entry))}"
        )
    if expected_phase and entry.get("phase") != expected_phase:
        raise ValueError(f"evidence phase mismatch: {evidence_id} expected={expected_phase} actual={entry.get('phase')}")
    return entry



@task_write_locked
def link_usage(
    task_dir: Path,
    evidence_id: str,
    *,
    agent: str,
    scope_refs: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Attach Reviewer/criterion usage to an existing artifact without cloning it."""
    if agent not in KNOWN_AGENTS:
        raise ValueError(f"unknown Agent: {agent}")
    def merge(index: dict[str, Any]) -> dict[str, Any]:
        entry = entries_by_id(index).get(evidence_id)
        if not isinstance(entry, dict):
            raise ValueError(f"unknown evidence_id: {evidence_id}")
        if entry.get("status") != "ACTIVE":
            raise ValueError(f"evidence is not ACTIVE: {evidence_id}")
        entry["used_by_agents"] = list(dict.fromkeys(list(entry.get("used_by_agents") or [entry.get("generated_by_agent")]) + [agent]))
        entry["scope_refs"] = list(dict.fromkeys(list(entry.get("scope_refs") or []) + list(scope_refs or [])))
        return entry

    return load_merge_save(
        lambda: load_registry(task_dir),
        lambda value: save_registry(task_dir, value),
        merge,
    )


def query_entries(
    task_dir: Path,
    *,
    phase: str = "",
    revision: str = "",
    evidence_type: str = "",
    scope: str = "",
    status: str = "ACTIVE",
    limit: int = 20,
) -> list[dict[str, Any]]:
    # A bounded query can target an exact current revision.  In that mode,
    # validate content only for the requested revision; unrelated historical
    # entries remain addressable even when their old DIRECT source has since
    # changed.  An unscoped query retains the explicit full-registry behavior
    # for compatibility; callers that need current-only behavior should pass
    # ``--revision``.
    validation_revisions = {revision} if revision else None
    errors, entries = validate_registry(task_dir, current_revisions=validation_revisions)
    if errors:
        raise ValueError("; ".join(errors))
    values = []
    for entry in entries.values():
        if status and entry.get("status") != status:
            continue
        if phase and entry.get("phase") != phase:
            continue
        if revision and entry.get("revision") != revision:
            continue
        if evidence_type and evidence_type not in evidence_types(entry):
            continue
        if scope and scope not in set(entry.get("scope_refs") or []):
            continue
        values.append(entry)
    values.sort(key=lambda item: (str(item.get("phase")), str(item.get("revision")), str(item.get("evidence_id"))))
    return values[: max(0, limit)] if limit else values


def command_snapshot_register(args: argparse.Namespace) -> int:
    agent_context.authorize_declared(args.agent, "evidence", "snapshot-register")
    entry = register_evidence(
        Path(args.task_dir), agent=args.agent, evidence_type=args.type, ref=args.source_ref,
        revision=args.revision, phase=args.phase, scope_refs=args.scope,
        command_result_ref=args.command_result_ref, metadata={}, capture_mode="SNAPSHOT",
    )
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


def command_bundle_register(args: argparse.Namespace) -> int:
    agent_context.authorize_declared(args.agent, "evidence", "bundle-register")
    items: list[dict[str, Any]] = []
    for value in args.item:
        if "=" not in value:
            raise ValueError("bundle --item must use TYPE=REF")
        evidence_type, ref = value.split("=", 1)
        items.append({"type": evidence_type.strip(), "ref": ref.strip()})
    entry = register_bundle(
        Path(args.task_dir).resolve(), agent=args.agent, items=items,
        revision=args.revision, phase=args.phase, scope_refs=args.scope,
        command_result_ref=args.command_result_ref,
    )
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0









def command_query(args: argparse.Namespace) -> int:
    agent_context.authorize_declared(args.agent, "evidence", "query")
    task_dir = Path(args.task_dir)
    items = query_entries(
        task_dir, phase=args.phase, revision=args.revision, evidence_type=args.type,
        scope=args.scope, status=args.status, limit=args.limit,
    )
    compact = [
        {
            "evidence_id": item.get("evidence_id"),
            "type": item.get("type"),
            "used_as_types": item.get("used_as_types"),
            "ref": item.get("ref"),
            "revision": item.get("revision"),
            "phase": item.get("phase"),
            "status": item.get("status"),
            "scope_refs": item.get("scope_refs"),
        }
        for item in items
    ]
    print(json.dumps({"count": len(compact), "items": compact}, ensure_ascii=False, indent=2))
    return 0

def command_register(args: argparse.Namespace) -> int:
    agent_context.authorize_declared(args.agent, "evidence", "register")
    entry = register_evidence(
        Path(args.task_dir), agent=args.agent, evidence_type=args.type, ref=args.ref,
        revision=args.revision, phase=args.phase, scope_refs=args.scope,
        command_result_ref=args.command_result_ref, metadata={}, capture_mode=getattr(args, "capture_mode", "AUTO"),
    )
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    agent_context.authorize_declared(args.agent, "evidence", "validate")
    errors, entries = validate_registry(Path(args.task_dir))
    payload = {"status": "PASSED" if not errors else "FAILED", "errors": errors, "count": len(entries)}
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


def command_show(args: argparse.Namespace) -> int:
    agent_context.authorize_declared(args.agent, "evidence", "show")
    entry = require_evidence(Path(args.task_dir), args.evidence_id, allow_historical=args.allow_historical)
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


def command_status(args: argparse.Namespace) -> int:
    agent_context.authorize_static(args.agent, "evidence", "status")
    entry = change_status(
        Path(args.task_dir), evidence_id=args.evidence_id,
        status=args.status, replacement_id=args.replacement_id,
    )
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


def command_repair_history(args: argparse.Namespace) -> int:
    agent_context.authorize_static(args.agent, "evidence", "repair-history")
    entry = repair_history(Path(args.task_dir), evidence_id=args.evidence_id)
    print(json.dumps(entry, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = build_common_parser(__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    register = sub.add_parser("register")
    add_task_dir_argument(register)
    add_agent_argument(register)
    register.add_argument("--type", required=True, choices=sorted(EVIDENCE_TYPES))
    register.add_argument("--ref", required=True)
    register.add_argument("--revision", required=True)
    register.add_argument("--phase", required=True)
    add_repeatable_argument(register, "--scope", default=[])
    register.add_argument("--command-result-ref", default="")
    register.add_argument("--capture-mode", choices=["AUTO", "DIRECT", "SNAPSHOT", "GIT_REF"], default="AUTO")
    set_handler(register, command_register)
    snapshot = sub.add_parser("snapshot-register", help="capture an immutable snapshot and register it as evidence")
    add_agent_argument(snapshot)
    add_task_dir_argument(snapshot)
    snapshot.add_argument("--type", required=True, choices=sorted(EVIDENCE_TYPES))
    snapshot.add_argument("--source-ref", required=True)
    snapshot.add_argument("--revision", required=True)
    snapshot.add_argument("--phase", required=True)
    add_repeatable_argument(snapshot, "--scope", default=[])
    snapshot.add_argument("--command-result-ref", default="")
    set_handler(snapshot, command_snapshot_register)
    bundle = sub.add_parser("bundle-register", help="capture multiple sources in one deterministic immutable Evidence bundle")
    add_agent_argument(bundle)
    add_task_dir_argument(bundle)
    add_repeatable_argument(bundle, "--item", required=True, help="repeat TYPE=REF; at least two items")
    bundle.add_argument("--revision", required=True)
    bundle.add_argument("--phase", required=True)
    add_repeatable_argument(bundle, "--scope", default=[])
    bundle.add_argument("--command-result-ref", default="")
    set_handler(bundle, command_bundle_register)
    validate = sub.add_parser("validate")
    add_agent_argument(validate)
    add_task_dir_argument(validate)
    set_handler(validate, command_validate)
    query = sub.add_parser("query", help="return a bounded Evidence subset instead of loading the full index")
    add_agent_argument(query)
    add_task_dir_argument(query)
    query.add_argument("--phase", default="")
    query.add_argument("--revision", default="")
    query.add_argument("--type", default="", choices=[""] + sorted(EVIDENCE_TYPES))
    query.add_argument("--scope", default="")
    query.add_argument("--status", default="ACTIVE", choices=[""] + sorted(STATUSES))
    query.add_argument("--limit", type=int, default=20)
    set_handler(query, command_query)
    show = sub.add_parser("show")
    add_agent_argument(show)
    add_task_dir_argument(show)
    show.add_argument("--evidence-id", required=True)
    show.add_argument("--allow-historical", action="store_true")
    set_handler(show, command_show)
    status = sub.add_parser("status", help="supersede or revoke an ACTIVE evidence record")
    add_agent_argument(status)
    add_task_dir_argument(status)
    status.add_argument("--evidence-id", required=True)
    status.add_argument("--status", required=True, choices=["SUPERSEDED", "REVOKED"])
    status.add_argument("--replacement-id", default="")
    set_handler(status, command_status)
    repair = sub.add_parser(
        "repair-history",
        help="rebind an ACTIVE DIRECT record to its existing immutable object",
    )
    add_agent_argument(repair)
    add_task_dir_argument(repair)
    repair.add_argument("--evidence-id", required=True)
    set_handler(repair, command_repair_history)
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        doc_layout.validate_cli_namespace(args)
        return int(args.func(args))
    except (OSError, ValueError, agent_context.AgentAuthorizationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
