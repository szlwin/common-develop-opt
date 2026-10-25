#!/usr/bin/env python3
"""Report package baseline drift without mutating the common-develop worktree."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import agent_context

ROOT = Path(__file__).resolve().parent.parent
RELEASE_CONTRACT = ROOT / "assets" / "release-contract.json"
RELEASE_ENVELOPE_MODE = "IMPLEMENTATION_COMMIT_WITH_RELEASE_ENVELOPE"
CLEAN_STATUSES = {"CLEAN", "RELEASE_ENVELOPE_CLEAN"}


class BaselineGuardError(ValueError):
    """Raised when the baseline cannot be inspected safely."""


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise BaselineGuardError(detail)
    return result


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _release_envelope_files(release: dict[str, Any]) -> set[str]:
    if release.get("baselineMode") != RELEASE_ENVELOPE_MODE:
        return set()
    files = release.get("releaseEnvelopeFiles")
    if not isinstance(files, list) or any(not isinstance(item, str) or not item for item in files):
        raise BaselineGuardError("release envelope files must be a non-empty string array")
    if len(files) != len(set(files)):
        raise BaselineGuardError("release envelope files must be unique")
    return set(files)


def _package_root(repo: Path | None) -> Path:
    if repo is None:
        return ROOT
    candidate = repo.resolve()
    if (candidate / "assets" / "release-contract.json").is_file():
        return candidate
    nested = candidate / "skills" / "common-develop"
    if (nested / "assets" / "release-contract.json").is_file():
        return nested
    return candidate


def _git_root(path: Path) -> Path:
    result = _git(path, "rev-parse", "--show-toplevel", check=False)
    if result.returncode != 0 or not result.stdout.strip():
        raise BaselineGuardError(f"not a Git worktree: {path}")
    return Path(result.stdout.strip()).resolve()


def _git_relative_package_path(package_root: Path, git_root: Path, relative: str) -> str:
    try:
        prefix = package_root.relative_to(git_root)
    except ValueError as exc:
        raise BaselineGuardError("Skill package must be inside the Git worktree") from exc
    return (prefix / relative).as_posix()


def load_release_contract(package_root: Path) -> dict[str, Any]:
    path = package_root / "assets" / "release-contract.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BaselineGuardError(f"cannot load release contract: {exc}") from exc
    if not isinstance(value, dict):
        raise BaselineGuardError("release contract must be an object")
    return value


def inspect_baseline(repo: Path | None = None, baseline_tag: str | None = None) -> dict[str, Any]:
    package_root = _package_root(repo)
    git_root = _git_root(package_root)
    release = load_release_contract(package_root)
    envelope_files = _release_envelope_files(release)
    tag = baseline_tag or release.get("baselineTag")
    if not isinstance(tag, str) or not tag:
        raise BaselineGuardError("release contract baselineTag is missing")

    tag_result = _git(git_root, "rev-list", "-n1", tag, check=False)
    if tag_result.returncode != 0 or not tag_result.stdout.strip():
        return {
            "status": "INVALID_BASELINE",
            "baselineTag": tag,
            "baselineCommit": None,
            "headCommit": _git(git_root, "rev-parse", "HEAD").stdout.strip(),
            "headMatchesBaseline": False,
            "worktreeClean": False,
            "criticalFileDrift": [],
            "changes": [],
            "recommendedAction": "CREATE_OR_REPAIR_BASELINE_TAG",
            "error": tag_result.stderr.strip() or f"baseline tag does not exist: {tag}",
        }

    baseline_commit = tag_result.stdout.strip()
    head_commit = _git(git_root, "rev-parse", "HEAD").stdout.strip()
    package_prefix = package_root.relative_to(git_root).as_posix()
    status_args = ["status", "--porcelain=v1", "--untracked-files=all"]
    if package_prefix != ".":
        status_args.extend(["--", package_prefix])
    status_lines = [
        line for line in _git(git_root, *status_args).stdout.splitlines()
        if line.strip()
    ]
    changes = [
        {"code": line[:2], "path": line[3:] if len(line) > 3 else ""}
        for line in status_lines
    ]

    critical_files = release.get("criticalBaselineFiles", [])
    if not isinstance(critical_files, list) or any(not isinstance(item, str) for item in critical_files):
        raise BaselineGuardError("release contract criticalBaselineFiles must be a string array")

    drift: list[dict[str, Any]] = []
    for relative in critical_files:
        git_relative = _git_relative_package_path(package_root, git_root, relative)
        baseline_blob = _git(git_root, "show", f"{tag}:{git_relative}", check=False)
        current_path = package_root / relative
        if baseline_blob.returncode != 0:
            state = "NOT_IN_BASELINE"
            baseline_digest = None
        else:
            baseline_bytes = baseline_blob.stdout.encode("utf-8")
            baseline_digest = _digest(baseline_bytes)
            state = "UNCHANGED"
        if not current_path.is_file():
            current_digest = None
            state = "DELETED" if baseline_digest else "MISSING"
        else:
            current_digest = _digest(current_path.read_bytes())
            if baseline_digest is None:
                state = "ADDED"
            elif current_digest != baseline_digest:
                state = "MODIFIED"
        if state != "UNCHANGED":
            drift.append(
                {
                    "path": relative,
                    "state": state,
                    "baselineDigest": baseline_digest,
                    "currentDigest": current_digest,
                }
            )

    head_matches = head_commit == baseline_commit
    worktree_clean = not changes
    drift_paths = {item["path"] for item in drift}
    envelope_result: dict[str, Any] = {
        "enabled": bool(envelope_files),
        "files": sorted(envelope_files),
        "parentMatchesBaseline": False,
        "singleCommitFromBaseline": False,
        "driftIsEnvelopeOnly": False,
    }
    if envelope_files:
        parent_result = _git(git_root, "rev-parse", f"{head_commit}^", check=False)
        parent_commit = parent_result.stdout.strip() if parent_result.returncode == 0 else ""
        range_result = _git(git_root, "rev-list", "--count", f"{baseline_commit}..{head_commit}", check=False)
        expected_drift = envelope_files & set(critical_files)
        envelope_result.update({
            "parentCommit": parent_commit or None,
            "parentMatchesBaseline": parent_commit == baseline_commit,
            "singleCommitFromBaseline": range_result.stdout.strip() == "1",
            "driftIsEnvelopeOnly": drift_paths == expected_drift,
        })
    release_envelope_clean = bool(
        envelope_files
        and worktree_clean
        and envelope_result["parentMatchesBaseline"]
        and envelope_result["singleCommitFromBaseline"]
        and envelope_result["driftIsEnvelopeOnly"]
    )
    if release_envelope_clean:
        status = "RELEASE_ENVELOPE_CLEAN"
        action = "PROCEED"
    elif head_matches and worktree_clean and not drift:
        status = "CLEAN"
        action = "PROCEED"
    elif not head_matches:
        status = "DIVERGED"
        action = "REVIEW_COMMITS_AND_PRESERVE_DRIFT"
    else:
        status = "DIRTY"
        action = "PRESERVE_AND_REVIEW_DRIFT"
    return {
        "status": status,
        "baselineTag": tag,
        "baselineCommit": baseline_commit,
        "headCommit": head_commit,
        "headMatchesBaseline": head_matches,
        "worktreeClean": worktree_clean,
        "criticalFileDrift": drift,
        "changes": changes,
        "baselineMode": release.get("baselineMode", "DIRECT_COMMIT"),
        "releaseEnvelope": envelope_result,
        "recommendedAction": action,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    agent_context.add_agent_argument(parser)
    parser.add_argument("command", choices=["status"])
    parser.add_argument("--repo", type=Path, default=None)
    parser.add_argument("--baseline-tag")
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser


def main_for_args(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    agent = agent_context.authorize_command(args, "baseline_guard", args.command)
    try:
        payload = inspect_baseline(args.repo, args.baseline_tag)
    except BaselineGuardError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(
            f"BASELINE {payload['status']} tag={payload['baselineTag']} "
            f"headMatches={payload['headMatchesBaseline']} clean={payload['worktreeClean']} "
            f"criticalDrift={len(payload['criticalFileDrift'])} changes={len(payload['changes'])}"
        )
    if args.require_clean and payload["status"] not in CLEAN_STATUSES:
        return 1
    return 0


def main() -> int:
    return main_for_args()


if __name__ == "__main__":
    raise SystemExit(main())
