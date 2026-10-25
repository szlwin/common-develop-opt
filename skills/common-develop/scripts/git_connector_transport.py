#!/usr/bin/env python3
"""Deterministic GitHub Connector transport handoff for wk -gp.

This module never calls GitHub itself.  It materializes the exact Git Data operations
an authorized ProjectManagerAgent must execute through the connected GitHub tool,
and validates the resulting receipt before common-develop may mark a checkpoint as
REMOTE_CHECKPOINTED.
"""
from __future__ import annotations

import base64
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from common_utils import atomic_write_text, canonical_json, now, sha256_bytes as _sha256_bytes, sha256_text

SCHEMA_VERSION = 1
STATE_TTL_SECONDS = 900


class GitConnectorTransportError(ValueError):
    pass


def digest_json(value: Any) -> str:
    return sha256_text(canonical_json(value))


def sha256_bytes(value: bytes) -> str:
    """Hash bytes through the shared digest helper."""
    return _sha256_bytes(value)


def _git(repo: Path, *args: str, binary: bool = False) -> bytes | str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=not binary,
    )
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace") if binary else result.stderr
        stdout = result.stdout.decode("utf-8", errors="replace") if binary else result.stdout
        raise GitConnectorTransportError(f"git {' '.join(args)} failed: {(stderr or stdout).strip()}")
    return result.stdout


def git_text(repo: Path, *args: str) -> str:
    return str(_git(repo, *args)).strip()


def git_bytes(repo: Path, *args: str) -> bytes:
    value = _git(repo, *args, binary=True)
    assert isinstance(value, bytes)
    return value


def normalized_commit_message(repo: Path, sha: str) -> str:
    raw = git_bytes(repo, "cat-file", "commit", sha)
    try:
        _headers, body = raw.split(b"\n\n", 1)
    except ValueError as exc:
        raise GitConnectorTransportError(f"commit object has no message body: {sha}") from exc
    return body.decode("utf-8", errors="strict").rstrip("\n")


def commit_tree(repo: Path, sha: str) -> str:
    return git_text(repo, "rev-parse", f"{sha}^{{tree}}")


def parse_github_repository(url: str) -> str:
    value = url.strip()
    ssh_match = re.fullmatch(r"git@github\.com:([^/\s]+)/([^\s]+?)(?:\.git)?", value)
    if ssh_match:
        repo = ssh_match.group(2)
        if repo.endswith(".git"):
            repo = repo[:-4]
        return f"{ssh_match.group(1)}/{repo}"
    if value.startswith("ssh://"):
        parsed = urlparse(value)
        if (parsed.hostname or "").lower() != "github.com":
            return ""
        parts = [part for part in parsed.path.strip("/").split("/") if part]
    else:
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() != "github.com":
            return ""
        parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) != 2:
        return ""
    owner, repo = parts
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        return ""
    return f"{owner}/{repo}"


def git_metadata_dir(repo: Path) -> Path:
    raw = git_text(repo, "rev-parse", "--git-dir")
    path = Path(raw)
    if not path.is_absolute():
        path = repo / path
    return path.resolve()


def transport_dir(repo: Path) -> Path:
    path = git_metadata_dir(repo) / "common-develop" / "git-connector"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _safe_name(checkpoint_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", checkpoint_id)


def request_path(repo: Path, checkpoint_id: str) -> Path:
    return transport_dir(repo) / f"{_safe_name(checkpoint_id)}.request.json"


def state_path(repo: Path, checkpoint_id: str) -> Path:
    return transport_dir(repo) / f"{_safe_name(checkpoint_id)}.state.json"


def write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GitConnectorTransportError(f"invalid connector JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise GitConnectorTransportError(f"connector JSON root must be an object: {path}")
    return value


def changed_tree_entries(repo: Path, base_sha: str, target_sha: str) -> list[dict[str, Any]]:
    raw = git_bytes(repo, "diff", "--name-only", "-z", base_sha, target_sha)
    paths = [item.decode("utf-8", errors="strict") for item in raw.split(b"\0") if item]
    entries: list[dict[str, Any]] = []
    for path in paths:
        ls = git_bytes(repo, "ls-tree", "-z", target_sha, "--", path)
        if not ls:
            entries.append({
                "path": path,
                "operation": "DELETE",
                "connectorTreeElement": {"path": path, "sha": None},
            })
            continue
        first = ls.split(b"\0", 1)[0]
        try:
            meta, actual_path = first.split(b"\t", 1)
            mode_b, type_b, sha_b = meta.split(b" ", 2)
        except ValueError as exc:
            raise GitConnectorTransportError(f"cannot parse git ls-tree for {path}") from exc
        actual = actual_path.decode("utf-8", errors="strict")
        if actual != path:
            raise GitConnectorTransportError(f"git ls-tree path mismatch: expected {path}, got {actual}")
        mode = mode_b.decode("ascii")
        object_type = type_b.decode("ascii")
        object_sha = sha_b.decode("ascii")
        item: dict[str, Any] = {
            "path": path,
            "operation": "UPSERT",
            "mode": mode,
            "type": object_type,
            "localObjectSha": object_sha,
            "connectorTreeElement": {"path": path, "mode": mode, "type": object_type, "sha": object_sha},
        }
        if object_type == "blob":
            content = git_bytes(repo, "cat-file", "blob", object_sha)
            item.update({
                "size": len(content),
                "contentSha256": sha256_bytes(content),
                "contentBase64": base64.b64encode(content).decode("ascii"),
                "connectorCreateBlob": {"encoding": "base64"},
            })
        elif object_type != "commit":
            raise GitConnectorTransportError(
                f"GitHub Connector transport only supports blob/commit tree entries; {path} is {object_type}"
            )
        entries.append(item)
    return entries


def _load_states(repo: Path) -> list[dict[str, Any]]:
    root = transport_dir(repo)
    states: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.state.json")):
        try:
            value = read_json(path)
        except GitConnectorTransportError:
            continue
        if value.get("schemaVersion") == SCHEMA_VERSION and value.get("status") == "VERIFIED":
            states.append(value)
    return states


def known_remote_equivalent(repo: Path, local_commit_sha: str) -> dict[str, Any] | None:
    matches = [item for item in _load_states(repo) if item.get("localCommitSha") == local_commit_sha]
    if not matches:
        return None
    matches.sort(key=lambda item: str(item.get("verifiedAt") or ""))
    return matches[-1]


def build_request(
    repo: Path,
    *,
    remote: str,
    remote_url: str,
    branch: str,
    checkpoint_id: str,
    local_commit_sha: str,
    base_commit_sha: str,
) -> dict[str, Any]:
    repository = parse_github_repository(remote_url)
    if not repository:
        raise GitConnectorTransportError("GitHub Connector fallback only supports github.com remotes")
    head = git_text(repo, "rev-parse", "HEAD")
    if head != local_commit_sha:
        raise GitConnectorTransportError("connector request requires local HEAD to equal the checkpoint commit")
    local_tree = commit_tree(repo, local_commit_sha)
    base_tree = commit_tree(repo, base_commit_sha)
    message = normalized_commit_message(repo, local_commit_sha)
    base_message = normalized_commit_message(repo, base_commit_sha)
    previous = known_remote_equivalent(repo, base_commit_sha)
    request: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "provider": "GITHUB_CONNECTOR",
        "operation": "PUBLISH_OR_VERIFY_CHECKPOINT",
        "repository": repository,
        "remote": remote,
        "remoteUrl": remote_url,
        "branch": branch,
        "checkpointId": checkpoint_id,
        "localCheckpoint": {
            "commitSha": local_commit_sha,
            "treeSha": local_tree,
            "normalizedMessage": message,
            "normalizedMessageSha256": sha256_bytes(message.encode("utf-8")),
        },
        "localBase": {
            "commitSha": base_commit_sha,
            "treeSha": base_tree,
            "normalizedMessageSha256": sha256_bytes(base_message.encode("utf-8")),
        },
        "knownRemoteParentSha": str(previous.get("remoteCommitSha") or "") if previous else "",
        "treeChanges": changed_tree_entries(repo, base_commit_sha, local_commit_sha),
        "constraints": {
            "currentBranchOnly": True,
            "forceAllowed": False,
            "pushTags": False,
            "createPullRequest": False,
            "merge": False,
            "release": False,
            "deploy": False,
            "requireExactTargetTreeSha": True,
            "requireEquivalentParentTreeAndMessage": True,
            "requireNormalizedCommitMessageMatch": True,
            "verifyRemoteBranchHead": True,
            "verifyPullRequestHeadWhenDiscoverable": True,
            "localHistoryRewriteAllowed": False,
        },
        "connectorProcedure": [
            "Read the current branch head through the GitHub connector. Never force-update it. If that head is already equivalent to localCheckpoint by exact tree SHA and normalized commit-message SHA-256, return resultMode=ALREADY_PRESENT with NO_CHANGE after verifying its parent is equivalent to localBase.",
            "Otherwise resolve the remote parent: use knownRemoteParentSha when present; otherwise require the current remote head to be equivalent to localBase by exact tree SHA and normalized commit-message SHA-256. A missing current branch may be created only for this exact branch from an equivalent localBase parent.",
            "For every UPSERT blob, call create_blob with contentBase64 and require the returned Git blob SHA to equal localObjectSha. DELETE entries create no blob.",
            "Call create_tree with the resolved remote-parent tree as base_tree_sha and the requested tree elements; require the returned tree SHA to equal localCheckpoint.treeSha exactly.",
            "Call create_commit with the exact normalizedMessage, exact target tree and resolved remote parent. The remote commit SHA may differ from localCheckpoint.commitSha because connector author/committer metadata is provider-owned.",
            "Move only the current branch ref with force=false, or create only this current branch if it was absent. Never create another branch, tag or PR.",
            "Read back the current branch head and require it to equal the connector-created remote commit SHA. If an existing PR for this branch is discoverable, require its head SHA to equal the same remote commit SHA.",
            "Return a receipt matching assets/git-connector-receipt.schema.json; common-develop will validate it before REMOTE_CHECKPOINTED becomes true.",
        ],
        "createdAt": now(),
    }
    digest_source = dict(request)
    digest_source.pop("createdAt", None)
    request["requestDigest"] = digest_json(digest_source)
    request["requestId"] = f"GCR-{_safe_name(checkpoint_id)}-{request['requestDigest'][:12]}"
    return request


def materialize_request(repo: Path, request: dict[str, Any]) -> Path:
    path = request_path(repo, str(request.get("checkpointId") or "checkpoint"))
    write_json(path, request)
    return path


def _validate_equivalent_parent(request: dict[str, Any], parent: dict[str, Any]) -> None:
    local_base = request.get("localBase") if isinstance(request.get("localBase"), dict) else {}
    known_remote = str(request.get("knownRemoteParentSha") or "")
    sha = str(parent.get("sha") or "")
    if not sha:
        raise GitConnectorTransportError("connector receipt resolvedParent.sha is required")
    if known_remote and sha != known_remote:
        raise GitConnectorTransportError("git_divergence: connector parent differs from known remote parent")
    if str(parent.get("treeSha") or "") != str(local_base.get("treeSha") or ""):
        raise GitConnectorTransportError("git_divergence: connector parent tree differs from local checkpoint base tree")
    if str(parent.get("normalizedMessageSha256") or "") != str(local_base.get("normalizedMessageSha256") or ""):
        raise GitConnectorTransportError("git_divergence: connector parent message differs from local checkpoint base")


def validate_receipt(request: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    if receipt.get("schemaVersion") != SCHEMA_VERSION:
        raise GitConnectorTransportError(f"connector receipt schemaVersion must be {SCHEMA_VERSION}")
    if receipt.get("provider") != "GITHUB_CONNECTOR":
        raise GitConnectorTransportError("connector receipt provider must be GITHUB_CONNECTOR")
    for key in ("requestDigest", "repository", "branch", "checkpointId"):
        if receipt.get(key) != request.get(key):
            raise GitConnectorTransportError(f"connector receipt {key} does not match request")
    parent = receipt.get("resolvedParent") if isinstance(receipt.get("resolvedParent"), dict) else {}
    _validate_equivalent_parent(request, parent)
    local = request.get("localCheckpoint") if isinstance(request.get("localCheckpoint"), dict) else {}
    result_mode = str(receipt.get("resultMode") or "PUBLISHED")
    if result_mode not in {"PUBLISHED", "ALREADY_PRESENT"}:
        raise GitConnectorTransportError("connector receipt resultMode is invalid")
    blob_results = receipt.get("blobResults") if isinstance(receipt.get("blobResults"), list) else []
    expected_blobs = {
        str(item.get("path")): str(item.get("localObjectSha"))
        for item in request.get("treeChanges", [])
        if isinstance(item, dict) and item.get("operation") == "UPSERT" and item.get("type") == "blob"
    }
    actual_blobs = {
        str(item.get("path")): str(item.get("sha"))
        for item in blob_results if isinstance(item, dict)
    }
    if result_mode == "PUBLISHED" and expected_blobs != actual_blobs:
        raise GitConnectorTransportError("connector receipt blobResults do not exactly match requested Git blob SHAs")
    if result_mode == "ALREADY_PRESENT" and actual_blobs and expected_blobs != actual_blobs:
        raise GitConnectorTransportError("connector ALREADY_PRESENT blobResults, when supplied, must match requested Git blob SHAs")
    tree = receipt.get("tree") if isinstance(receipt.get("tree"), dict) else {}
    if tree.get("sha") != local.get("treeSha"):
        raise GitConnectorTransportError("remote_checkpoint_verification_failed: connector tree SHA differs from local checkpoint tree")
    commit = receipt.get("commit") if isinstance(receipt.get("commit"), dict) else {}
    remote_commit_sha = str(commit.get("sha") or "")
    if not re.fullmatch(r"[0-9a-f]{40,64}", remote_commit_sha):
        raise GitConnectorTransportError("connector receipt commit.sha is invalid")
    if commit.get("treeSha") != local.get("treeSha"):
        raise GitConnectorTransportError("connector remote commit tree differs from local checkpoint tree")
    if commit.get("parentSha") != parent.get("sha"):
        raise GitConnectorTransportError("connector remote commit parent differs from resolved parent")
    if commit.get("normalizedMessageSha256") != local.get("normalizedMessageSha256"):
        raise GitConnectorTransportError("connector remote commit message differs from local checkpoint message")
    ref = receipt.get("refUpdate") if isinstance(receipt.get("refUpdate"), dict) else {}
    if ref.get("force") is not False:
        raise GitConnectorTransportError("connector ref update must use force=false")
    if ref.get("branch") != request.get("branch"):
        raise GitConnectorTransportError("connector ref update branch differs from current branch")
    if ref.get("afterSha") != remote_commit_sha:
        raise GitConnectorTransportError("connector ref update afterSha differs from remote checkpoint commit")
    if ref.get("operation") not in {"UPDATE_REF_NON_FORCE", "CREATE_CURRENT_BRANCH", "NO_CHANGE"}:
        raise GitConnectorTransportError("connector ref update operation is not allowed")
    before_sha = str(ref.get("beforeSha") or "")
    if result_mode == "PUBLISHED":
        if ref.get("operation") == "UPDATE_REF_NON_FORCE" and before_sha != str(parent.get("sha") or ""):
            raise GitConnectorTransportError("git_divergence: connector non-force ref update did not start from the resolved parent")
        if ref.get("operation") == "CREATE_CURRENT_BRANCH" and before_sha:
            raise GitConnectorTransportError("connector current-branch creation requires an empty beforeSha")
        if ref.get("operation") == "NO_CHANGE":
            raise GitConnectorTransportError("PUBLISHED connector receipt cannot use NO_CHANGE")
    else:
        if ref.get("operation") != "NO_CHANGE" or before_sha != remote_commit_sha:
            raise GitConnectorTransportError("ALREADY_PRESENT connector receipt must use NO_CHANGE at the verified remote commit")
    branch_verification = receipt.get("remoteVerification") if isinstance(receipt.get("remoteVerification"), dict) else {}
    if branch_verification.get("branchHeadSha") != remote_commit_sha:
        raise GitConnectorTransportError("remote_checkpoint_verification_failed: connector branch HEAD is not the remote checkpoint commit")
    pr = receipt.get("pullRequestVerification") if isinstance(receipt.get("pullRequestVerification"), dict) else {}
    status = pr.get("status")
    if status not in {"VERIFIED", "NO_ASSOCIATED_PR", "NOT_DISCOVERABLE"}:
        raise GitConnectorTransportError("connector pullRequestVerification.status is invalid")
    if status == "VERIFIED":
        refs = pr.get("refs") if isinstance(pr.get("refs"), list) else []
        if not refs:
            raise GitConnectorTransportError("VERIFIED pullRequestVerification requires refs")
        for item in refs:
            if not isinstance(item, dict) or item.get("headSha") != remote_commit_sha:
                raise GitConnectorTransportError("connector PR head does not equal the remote checkpoint commit")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "status": "VERIFIED",
        "transport": "GITHUB_CONNECTOR",
        "requestDigest": request["requestDigest"],
        "requestId": request["requestId"],
        "repository": request["repository"],
        "remote": request["remote"],
        "branch": request["branch"],
        "checkpointId": request["checkpointId"],
        "localCommitSha": local["commitSha"],
        "localTreeSha": local["treeSha"],
        "remoteParentSha": parent["sha"],
        "remoteCommitSha": remote_commit_sha,
        "remoteTreeSha": local["treeSha"],
        "remoteHeadSha": remote_commit_sha,
        "identityMode": "EXACT_TREE_MESSAGE_EQUIVALENT",
        "resultMode": result_mode,
        "pullRequestVerification": pr,
        "providerVerifiedAt": str(receipt.get("verifiedAt") or ""),
        "verifiedAt": now(),
    }


def persist_verified_state(repo: Path, checkpoint_id: str, state: dict[str, Any]) -> Path:
    path = state_path(repo, checkpoint_id)
    write_json(path, state)
    return path


def load_verified_state(repo: Path, checkpoint_id: str) -> dict[str, Any] | None:
    path = state_path(repo, checkpoint_id)
    if not path.is_file():
        return None
    try:
        value = read_json(path)
    except GitConnectorTransportError:
        return None
    if value.get("schemaVersion") != SCHEMA_VERSION or value.get("status") != "VERIFIED":
        return None
    return value


def verified_state_is_fresh(state: dict[str, Any], max_age_seconds: int = STATE_TTL_SECONDS) -> bool:
    raw = str(state.get("verifiedAt") or "")
    if not raw:
        return False
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - stamp.astimezone(timezone.utc)).total_seconds()
    return -60 <= age <= max_age_seconds
