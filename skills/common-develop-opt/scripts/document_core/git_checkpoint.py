#!/usr/bin/env python3
"""Create and validate sequential Git phase checkpoints for common-develop."""
from __future__ import annotations

import sys
from pathlib import Path
_COMPAT_ROOT = Path(__file__).resolve().parent.parent / "compat"
_SCRIPT_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_SCRIPT_ROOT), str(_COMPAT_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import argparse
import contextlib
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from resource_paths import asset_path
from typing import Any, Iterable

from document_core import agent_context
from document_core import simple_yaml
from document_core.file_lock import nonblocking_file_lock
from runtime import work_mode
from runtime import git_connector_transport
from runtime._long_task import task_events

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = asset_path('assets/storage/long-task/git_checkpoints.yaml')
SCHEMA_VERSION = 2
PROTECTED_BRANCH_PATTERNS = ("main", "master", "develop", "release", "release/*", "production", "production/*")
DENIED_PATH_PATTERNS = (
    ".env", ".env.*", "*.pem", "*.key", "id_rsa", "id_ed25519", "*.p12", "*.pfx",
    "**/__pycache__/**", "**/.pytest_cache/**", "**/.mypy_cache/**", "**/.ruff_cache/**",
    "**/node_modules/**", "**/dist/**", "**/build/**", "**/*.tmp", "**/*.log",
)
PHASE_COMMIT_STYLE = {
    "requirement_confirmation": ("docs", "requirement", "requirement confirmation"),
    "requirement_analysis": ("docs", "analysis", "requirement analysis"),
    "business_model": ("docs", "model", "business model"),
    "design": ("docs", "design", "design"),
    "test_design": ("test", "test-design", "test design"),
    "implementation_plan": ("docs", "plan", "implementation plan"),
    "tdd": ("test", "tdd", "TDD"),
    "development": ("feat", "development", "development"),
    "code_review": ("chore", "review", "code review"),
    "testing": ("test", "testing", "testing"),
    "completion_verification": ("chore", "verification", "completion verification"),
}

class GitCheckpointError(ValueError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def run_git(
    repo: Path, *args: str, check: bool = True, timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = ["git", "-C", str(repo), *args]
    command_env = os.environ.copy()
    if env:
        command_env.update(env)
    try:
        result = subprocess.run(
            command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
            env=command_env,
        )
    except subprocess.TimeoutExpired as exc:
        detail = f"git transport timed out after {timeout:g}s" if timeout else "git command timed out"
        if check:
            raise GitCheckpointError(f"git {' '.join(args)} failed: {detail}") from exc
        return subprocess.CompletedProcess(command, 124, stdout="", stderr=detail)
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise GitCheckpointError(f"git {' '.join(args)} failed: {detail}")
    return result


def git_index_path(repo: Path) -> Path:
    raw = run_git(repo, "rev-parse", "--git-path", "index").stdout.strip()
    path = Path(raw)
    return path if path.is_absolute() else (repo / path).resolve()


def index_digest(repo: Path, index_path: Path | None = None) -> str:
    """Digest logical index entries, not the mutable stat-cache bytes."""
    env = {"GIT_INDEX_FILE": str(index_path)} if index_path is not None else None
    result = run_git(repo, "ls-files", "--stage", "-z", check=False, env=env)
    if result.returncode != 0:
        raise GitCheckpointError(f"cannot read Git index for digest: {(result.stderr or result.stdout).strip()}")
    return "sha256:" + hashlib.sha256(result.stdout.encode("utf-8")).hexdigest()


def index_tree(repo: Path, index_path: Path | None = None) -> str:
    env = {"GIT_INDEX_FILE": str(index_path)} if index_path is not None else None
    return run_git(repo, "write-tree", env=env).stdout.strip()


@contextlib.contextmanager
def checkpoint_lock(repo: Path):
    """Serialize checkpoint writers without placing lock artifacts in the worktree."""
    git_dir = Path(run_git(repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = (repo / git_dir).resolve()
    lock_dir = git_dir / "common-develop"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "checkpoint.lock"
    with lock_path.open("a+b") as handle:
        try:
            with nonblocking_file_lock(handle):
                handle.seek(0)
                handle.truncate()
                handle.write(f"pid={os.getpid()}\nstarted_at={now()}\n".encode("ascii"))
                handle.flush()
                yield
        except BlockingIOError as exc:
            raise GitCheckpointError("another Git checkpoint writer is active") from exc


@contextlib.contextmanager
def isolated_index(repo: Path):
    """Run staging/commit against a disposable index, never the user's index."""
    real_index = git_index_path(repo)
    fd, temporary = tempfile.mkstemp(prefix="wk-checkpoint-index-", dir=str(real_index.parent))
    os.close(fd)
    temporary_path = Path(temporary)
    try:
        if real_index.is_file():
            shutil.copyfile(real_index, temporary_path)
        else:
            run_git(repo, "read-tree", "HEAD", env={"GIT_INDEX_FILE": str(temporary_path)})
        yield temporary_path
    finally:
        temporary_path.unlink(missing_ok=True)


def index_env(index_path: Path) -> dict[str, str]:
    return {"GIT_INDEX_FILE": str(index_path)}


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def refresh_real_index_to_head(
    repo: Path,
    *,
    expected_head: str,
    expected_index_digest: str,
    expected_index_tree: str,
) -> dict[str, Any]:
    """Atomically refresh the caller's index only when it remained untouched."""
    real_index = git_index_path(repo)
    lock_path = real_index.with_name(real_index.name + ".lock")
    prepared_path: Path | None = None
    lock_owned = False

    try:
        if lock_path.exists():
            current_digest = index_digest(repo)
            return {
                "status": "PRESERVED_CONCURRENT_CHANGE",
                "reason": "INDEX_LOCK_UNAVAILABLE",
                "digestAfter": current_digest,
                "treeAfter": expected_index_tree if current_digest == expected_index_digest else "",
            }

        current_digest = index_digest(repo)
        current_tree = index_tree(repo)
        if current_digest != expected_index_digest or current_tree != expected_index_tree:
            return {
                "status": "PRESERVED_CONCURRENT_CHANGE",
                "reason": "INDEX_CHANGED_BEFORE_REFRESH",
                "digestAfter": current_digest,
                "treeAfter": current_tree,
            }

        fd, prepared = tempfile.mkstemp(prefix="wk-checkpoint-index-refresh-", dir=str(real_index.parent))
        os.close(fd)
        prepared_path = Path(prepared)
        run_git(repo, "read-tree", expected_head, env=index_env(prepared_path))
        prepared_digest = index_digest(repo, prepared_path)
        prepared_tree = index_tree(repo, prepared_path)
        expected_head_tree = run_git(repo, "rev-parse", f"{expected_head}^{{tree}}").stdout.strip()
        if prepared_tree != expected_head_tree:
            return {
                "status": "FAILED",
                "reason": "PREPARED_INDEX_TREE_MISMATCH",
                "digestAfter": current_digest,
                "treeAfter": current_tree,
            }

        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            lock_owned = True
        except FileExistsError:
            return {
                "status": "PRESERVED_CONCURRENT_CHANGE",
                "reason": "INDEX_LOCK_UNAVAILABLE",
                "digestAfter": current_digest,
                "treeAfter": current_tree,
            }

        current_digest = index_digest(repo)
        if current_digest == expected_index_digest:
            current_tree = expected_index_tree
        else:
            current_tree = ""
        current_head = run_git(repo, "rev-parse", "HEAD").stdout.strip()
        if current_digest != expected_index_digest or current_tree != expected_index_tree:
            return {
                "status": "PRESERVED_CONCURRENT_CHANGE",
                "reason": "INDEX_CHANGED_DURING_REFRESH",
                "digestAfter": current_digest,
                "treeAfter": current_tree,
            }
        if current_head != expected_head:
            return {
                "status": "PRESERVED_HEAD_CHANGE",
                "reason": "HEAD_CHANGED_DURING_REFRESH",
                "digestAfter": current_digest,
                "treeAfter": current_tree,
            }

        os.replace(prepared_path, lock_path)
        prepared_path = None
        _fsync_file(lock_path)
        os.replace(lock_path, real_index)
        lock_owned = False
        _fsync_directory(real_index.parent)

        final_digest = index_digest(repo)
        final_tree = index_tree(repo)
        if final_tree != expected_head_tree or final_digest != prepared_digest:
            return {
                "status": "FAILED",
                "reason": "REFRESHED_INDEX_VERIFICATION_FAILED",
                "digestAfter": final_digest,
                "treeAfter": final_tree,
            }
        return {
            "status": "REFRESHED",
            "reason": "CLEAN_INDEX_UNCHANGED",
            "digestAfter": final_digest,
            "treeAfter": final_tree,
        }
    except (OSError, GitCheckpointError) as exc:
        try:
            digest_after = index_digest(repo)
        except (OSError, GitCheckpointError):
            digest_after = expected_index_digest
        return {
            "status": "FAILED",
            "reason": "INDEX_REFRESH_FAILED",
            "error": str(exc),
            "digestAfter": digest_after,
            "treeAfter": locals().get("current_tree", ""),
        }
    finally:
        if lock_owned:
            lock_path.unlink(missing_ok=True)
        if prepared_path is not None:
            prepared_path.unlink(missing_ok=True)


def read_json_block(path: Path, name: str) -> Any:
    text = path.read_text(encoding="utf-8")
    match = re.search(rf"```json\s+{re.escape(name)}\s*\n(.*?)\n```", text, re.DOTALL)
    if not match:
        raise GitCheckpointError(f"{path}: missing fenced JSON block {name}")
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise GitCheckpointError(f"{path}: invalid JSON block {name}: {exc}") from exc


def version_root(task_dir: Path) -> Path:
    if task_dir.parent.name != "task":
        raise GitCheckpointError(f"task directory must be version/{{VERSION}}/task/{{TARGET_ID}}: {task_dir}")
    return task_dir.parent.parent


def repository_root(task_dir: Path) -> Path:
    result = run_git(task_dir, "rev-parse", "--show-toplevel")
    repo = Path(result.stdout.strip()).resolve()
    try:
        task_dir.resolve().relative_to(repo)
    except ValueError as exc:
        raise GitCheckpointError("task directory is outside the Git repository") from exc
    return repo


def relative(repo: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError as exc:
        raise GitCheckpointError(f"path is outside Git repository: {path}") from exc


def checkpoint_path(task_dir: Path) -> Path:
    return task_dir / "git_checkpoints.yaml"


def ensure_document(task_dir: Path, target_id: str, version: str) -> Path:
    path = checkpoint_path(task_dir)
    if not path.exists():
        text = TEMPLATE.read_text(encoding="utf-8").replace("{{TARGET_ID}}", target_id).replace("{{VERSION}}", version)
        path.write_text(text, encoding="utf-8")
    return path


def load_document(task_dir: Path) -> dict[str, Any]:
    path = checkpoint_path(task_dir)
    if not path.is_file():
        raise GitCheckpointError(f"missing required file: {path}")
    try:
        value = simple_yaml.load(path)
    except (OSError, simple_yaml.YamlError) as exc:
        raise GitCheckpointError(f"invalid git_checkpoints.yaml: {exc}") from exc
    if not isinstance(value, dict):
        raise GitCheckpointError("git_checkpoints.yaml root must be an object")
    return value


def validate_document(task_dir: Path, *, require_resolved: bool = False) -> tuple[list[str], dict[str, Any], list[dict[str, Any]]]:
    errors: list[str] = []
    try:
        doc = load_document(task_dir)
    except GitCheckpointError as exc:
        return [str(exc)], {}, []
    import long_task  # local import avoids module cycle
    state = long_task.runtime_state(task_dir)
    allowed_root = {"schema_version", "target_id", "version", "checkpoints"}
    extra = sorted(set(doc) - allowed_root)
    if extra:
        errors.append(f"git_checkpoints.yaml unknown root fields: {extra}")
    if doc.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"git_checkpoints.yaml schema_version must be {SCHEMA_VERSION}")
    if state:
        if doc.get("target_id") != state.get("target_id"):
            errors.append("git_checkpoints.yaml target_id differs from task-state")
        if doc.get("version") != state.get("version"):
            errors.append("git_checkpoints.yaml version differs from task-state")
    items = doc.get("checkpoints")
    if not isinstance(items, list):
        errors.append("git_checkpoints.yaml checkpoints must be an array")
        items = []
    ids: set[str] = set()
    resolved: list[dict[str, Any]] = []
    allowed_fields = {
        "checkpoint_id", "phase", "iteration_id", "iteration_no", "stage_revision", "stage_outcome_ref",
        "branch", "base_commit", "changed_paths", "content_digest", "commit_title",
        "created_by_agent", "created_at", "supersedes_checkpoint_id",
    }
    repo: Path | None = None
    if require_resolved:
        try:
            repo = repository_root(task_dir)
        except GitCheckpointError as exc:
            errors.append(str(exc))
    for index, item in enumerate(items):
        prefix = f"checkpoints[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be an object")
            continue
        extra = sorted(set(item) - allowed_fields)
        if extra:
            errors.append(f"{prefix} unknown fields: {extra}")
        checkpoint_id = item.get("checkpoint_id")
        if not isinstance(checkpoint_id, str) or not re.fullmatch(r"CP-[A-Za-z0-9_.-]+", checkpoint_id):
            errors.append(f"{prefix} invalid checkpoint_id")
            continue
        if checkpoint_id in ids:
            errors.append(f"duplicate checkpoint_id: {checkpoint_id}")
        ids.add(checkpoint_id)
        for key in ("phase", "iteration_id", "stage_revision", "stage_outcome_ref", "branch", "base_commit", "content_digest", "commit_title", "created_by_agent", "created_at"):
            if not isinstance(item.get(key), str) or not item.get(key):
                errors.append(f"{prefix} missing {key}")
        if item.get("created_by_agent") != "ProjectManagerAgent":
            errors.append(f"{prefix} created_by_agent must be ProjectManagerAgent")
        if not isinstance(item.get("iteration_no"), int) or isinstance(item.get("iteration_no"), bool) or item.get("iteration_no", 0) < 1:
            errors.append(f"{prefix} iteration_no must be positive integer")
        if not isinstance(item.get("changed_paths"), list) or any(not isinstance(path, str) or not path for path in item.get("changed_paths", [])):
            errors.append(f"{prefix} changed_paths must be string array")
        result = dict(item)
        if repo is not None:
            shas = resolve_checkpoint_shas(repo, checkpoint_id)
            result["resolved_commit_shas"] = shas
            if len(shas) != 1:
                errors.append(f"{prefix} must resolve to exactly one Git commit, got {len(shas)}")
        resolved.append(result)
    return errors, doc, resolved


def resolve_checkpoint_shas(repo: Path, checkpoint_id: str) -> list[str]:
    result = run_git(repo, "log", "--all", "--format=%H%x00%B%x00", check=False)
    if result.returncode != 0:
        return []
    chunks = result.stdout.split("\x00")
    shas: list[str] = []
    for index in range(0, len(chunks) - 1, 2):
        sha = chunks[index].strip()
        body = chunks[index + 1]
        if re.search(rf"(?m)^WK-Checkpoint:\s*{re.escape(checkpoint_id)}\s*$", body):
            shas.append(sha)
    return list(dict.fromkeys(shas))


def status_paths(repo: Path) -> list[str]:
    result = run_git(repo, "status", "--porcelain=v1", "-uall")
    paths: list[str] = []
    for raw in result.stdout.splitlines():
        if len(raw) < 4:
            continue
        path = raw[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path.startswith('"') and path.endswith('"'):
            try:
                path = bytes(path[1:-1], "utf-8").decode("unicode_escape")
            except UnicodeDecodeError:
                pass
        paths.append(Path(path).as_posix())
    return sorted(dict.fromkeys(paths))


def in_progress_operation(repo: Path) -> str:
    git_dir = Path(run_git(repo, "rev-parse", "--git-dir").stdout.strip())
    if not git_dir.is_absolute():
        git_dir = repo / git_dir
    probes = {
        "merge": git_dir / "MERGE_HEAD",
        "cherry-pick": git_dir / "CHERRY_PICK_HEAD",
        "revert": git_dir / "REVERT_HEAD",
        "rebase": git_dir / "rebase-merge",
        "rebase-apply": git_dir / "rebase-apply",
    }
    for name, path in probes.items():
        if path.exists():
            return name
    return ""


def branch_name(repo: Path) -> str:
    result = run_git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if result.returncode != 0 or not result.stdout.strip():
        raise GitCheckpointError("detached HEAD is not allowed for automatic checkpoint commits")
    branch = result.stdout.strip()
    if any(fnmatch.fnmatch(branch, pattern) for pattern in PROTECTED_BRANCH_PATTERNS):
        raise GitCheckpointError(f"protected branch does not allow automatic checkpoint commits: {branch}")
    return branch


def normalize_pattern(repo: Path, value: str) -> str:
    path = Path(str(value))
    if path.is_absolute():
        try:
            return path.resolve().relative_to(repo).as_posix()
        except ValueError:
            return ""
    return path.as_posix().lstrip("./")


def allowed_patterns(repo: Path, task_dir: Path, state: dict[str, Any], tasks: list[dict[str, Any]], attempts: list[dict[str, Any]], phase: str) -> list[str]:
    version_dir = version_root(task_dir)
    target = str(state.get("target_id"))
    patterns = [
        relative(repo, task_dir) + "/**",
        relative(repo, version_dir / "requirement" / target) + "/**",
        relative(repo, version_dir / "work.md"),
        relative(repo, version_dir / "work_record.md"),
        relative(repo, version_dir / "requirement" / "requirement_list.md"),
    ]
    current_iteration = ((state.get("artifact_revisions") or {}).get(phase) or {}).get("iteration_id")
    phase_task_ids: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict) or task.get("phase") != phase or task.get("iteration_id") != current_iteration:
            continue
        phase_task_ids.add(str(task.get("id")))
        for value in task.get("allowed_files") or []:
            normalized = normalize_pattern(repo, str(value))
            if normalized:
                patterns.append(normalized)
    for attempt in attempts:
        if not isinstance(attempt, dict) or attempt.get("task_id") not in phase_task_ids or attempt.get("iteration_id") != current_iteration:
            continue
        for value in attempt.get("modified_files") or []:
            normalized = normalize_pattern(repo, str(value))
            if normalized:
                patterns.append(normalized)
    return sorted(dict.fromkeys(patterns))


def path_allowed(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) or path == pattern.rstrip("/**") for pattern in patterns)


def denied_path(path: str) -> bool:
    normalized = path.lstrip("./")
    return any(fnmatch.fnmatch(normalized, pattern) or fnmatch.fnmatch("/" + normalized, pattern) for pattern in DENIED_PATH_PATTERNS)


def content_digest(repo: Path, paths: list[str], checkpoint_rel: str) -> str:
    digest = hashlib.sha256()
    for item in sorted(path for path in paths if path != checkpoint_rel):
        digest.update(item.encode("utf-8"))
        digest.update(b"\0")
        path = repo / item
        if path.is_file():
            digest.update(path.read_bytes())
        else:
            digest.update(b"<deleted>")
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def default_title(target: str, phase: str) -> str:
    commit_type, scope, summary = PHASE_COMMIT_STYLE.get(phase, ("chore", "checkpoint", phase.replace("_", " ")))
    prefix = f"{commit_type}({scope}): complete "
    suffix = f" {summary}"
    available = max(1, 72 - len(prefix) - len(suffix))
    compact_target = target if len(target) <= available else target[: max(1, available - 1)] + "…"
    return f"{prefix}{compact_target}{suffix}"[:72]


def build_message(title: str, changes: list[str], checkpoint_id: str, revision: str) -> str:
    title = title.strip()
    if not title or len(title) > 72 or "\n" in title:
        raise GitCheckpointError("commit title must be one non-empty line of at most 72 characters")
    clean_changes = [item.strip() for item in changes if item.strip()][:3]
    lines = [title]
    if clean_changes:
        lines.append("")
        lines.extend(f"- {item}" for item in clean_changes)
    lines.extend(["", f"WK-Checkpoint: {checkpoint_id}", f"WK-Revision: {revision}"])
    return "\n".join(lines) + "\n"


def current_gate(task_dir: Path, *, phase_override: str = "") -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    import long_task  # local import avoids module cycle
    state = long_task.runtime_state(task_dir)
    tasks = long_task.runtime_tasks(task_dir)
    history = task_events.reduce_events(task_events.read_events(task_dir))
    attempts = [item for item in history.get("attempts", []) if isinstance(item, dict)]
    outcomes = [item for item in history.get("outcomes", []) if isinstance(item, dict)]
    if not isinstance(state, dict) or not isinstance(tasks, list):
        raise GitCheckpointError("long-task runtime documents have invalid root types")
    phase = phase_override or str(state.get("current_phase") or "")
    artifact = ((state.get("artifact_revisions") or {}).get(phase) or {})
    outcome = next((item for item in outcomes if isinstance(item, dict) and item.get("phase") == phase and item.get("is_current") is True), None)
    if not isinstance(outcome, dict):
        raise GitCheckpointError(f"current phase {phase} has no current StageOutcome")
    if artifact.get("status") != "PASSED" or outcome.get("status") != "PASSED":
        raise GitCheckpointError(f"current phase {phase} must have PASSED artifact and StageOutcome")
    if outcome.get("output_revision") != artifact.get("revision"):
        raise GitCheckpointError("current StageOutcome revision differs from phase artifact revision")
    if outcome.get("iteration_id") != artifact.get("iteration_id"):
        raise GitCheckpointError("current StageOutcome iteration differs from phase artifact iteration")
    if state.get("execution_mode") != "SEQUENTIAL":
        raise GitCheckpointError("Git checkpoints only support execution_mode=SEQUENTIAL")
    if state.get("active_task_ids") or state.get("current_attempt_id"):
        raise GitCheckpointError("Git checkpoint requires no running task attempt")
    phase_tasks = [
        task for task in tasks if isinstance(task, dict) and task.get("phase") == phase and task.get("iteration_id") == artifact.get("iteration_id")
    ]
    incomplete = [task.get("id") for task in phase_tasks if task.get("status") not in {"PASSED", "NOT_APPLICABLE"}]
    if incomplete:
        raise GitCheckpointError(f"current phase tasks are incomplete: {incomplete}")
    return state, tasks, attempts, outcome


def checkpoint_id_for(state: dict[str, Any], outcome: dict[str, Any]) -> str:
    phase = str(outcome.get("phase") or state.get("current_phase"))
    return f"CP-{state['version']}-{state['target_id']}-{phase.upper().replace('_', '-')}-{int(outcome['iteration_no']):03d}"


def configured_remote(repo: Path, branch: str) -> str:
    configured = run_git(repo, "config", "--get", f"branch.{branch}.remote", check=False)
    remote = configured.stdout.strip() if configured.returncode == 0 else ""
    if remote and remote != ".":
        return remote
    remotes = [value.strip() for value in run_git(repo, "remote", check=False).stdout.splitlines() if value.strip()]
    if "origin" in remotes:
        return "origin"
    raise GitCheckpointError(
        f"no configured remote for branch {branch}; wk -gp requires branch.<name>.remote or origin"
    )


def remote_url(repo: Path, remote: str) -> str:
    result = run_git(repo, "remote", "get-url", remote, check=False)
    if result.returncode != 0 or not result.stdout.strip():
        raise GitCheckpointError(f"cannot resolve Git remote URL for {remote}")
    return result.stdout.strip()


def remote_branch_head(repo: Path, remote: str, branch: str) -> str:
    ref = f"refs/heads/{branch}"
    result = run_git(repo, "ls-remote", "--heads", remote, ref, check=False, timeout=20)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise GitCheckpointError(f"remote verification failed for {remote}/{branch}: {detail}")
    rows = [line.split() for line in result.stdout.splitlines() if line.strip()]
    matches = [row[0] for row in rows if len(row) >= 2 and row[1] == ref]
    if len(matches) > 1:
        raise GitCheckpointError(f"remote branch {remote}/{branch} resolved more than once")
    return matches[0] if matches else ""


def native_transport_classification(detail: str) -> str:
    text = detail.lower()
    if any(token in text for token in ("non-fast-forward", "fetch first", "rejected", "stale info")):
        return "git_divergence"
    if any(token in text for token in (
        "could not resolve host", "failed to connect", "connection timed out", "network is unreachable",
        "couldn't connect", "connection refused", "proxy", "tls", "ssl", "unable to access",
        "temporary failure in name resolution", "timed out",
    )):
        return "native_transport_unavailable"
    if any(token in text for token in (
        "authentication failed", "could not read username", "permission denied",
        "support for password authentication was removed", "terminal prompts disabled",
    )):
        return "native_auth_unavailable"
    return "remote_push_failed"


def connector_verified_result(
    repo: Path, *, checkpoint: str, sha: str, branch: str, remote: str
) -> dict[str, Any] | None:
    state = git_connector_transport.load_verified_state(repo, checkpoint)
    if not isinstance(state, dict):
        return None
    if not git_connector_transport.verified_state_is_fresh(state):
        return None
    if state.get("localCommitSha") != sha or state.get("branch") != branch or state.get("remote") != remote:
        return None
    local_tree = run_git(repo, "rev-parse", f"{sha}^{{tree}}").stdout.strip()
    if state.get("localTreeSha") != local_tree or state.get("remoteTreeSha") != local_tree:
        return None
    remote_sha = str(state.get("remoteCommitSha") or "")
    if not remote_sha or state.get("remoteHeadSha") != remote_sha:
        return None
    return {
        "status": "ALREADY_PUSHED_CONNECTOR",
        "transport": "GITHUB_CONNECTOR",
        "identityMode": str(state.get("identityMode") or "EXACT_TREE_MESSAGE_EQUIVALENT"),
        "checkpointId": checkpoint,
        "commitSha": sha,
        "remoteCommitSha": remote_sha,
        "remote": remote,
        "branch": branch,
        "remoteHeadSha": remote_sha,
        "remoteTreeSha": local_tree,
        "remoteVerified": True,
        "pullRequestVerification": state.get("pullRequestVerification") or {
            "provider": "GITHUB", "status": "NOT_DISCOVERABLE", "refs": []
        },
        "checkpointState": {
            "canonicalPassed": True,
            "localCheckpointed": True,
            "remoteCheckpointed": True,
        },
    }


def connector_required_result(
    repo: Path, *, remote: str, branch: str, checkpoint: str, sha: str, base_commit: str, reason: str
) -> dict[str, Any]:
    url = remote_url(repo, remote)
    try:
        request = git_connector_transport.build_request(
            repo, remote=remote, remote_url=url, branch=branch, checkpoint_id=checkpoint,
            local_commit_sha=sha, base_commit_sha=base_commit,
        )
    except git_connector_transport.GitConnectorTransportError as exc:
        raise GitCheckpointError(f"{reason}: GitHub Connector fallback unavailable: {exc}") from exc
    path = git_connector_transport.materialize_request(repo, request)
    return {
        "status": "CONNECTOR_REQUIRED",
        "transport": "GITHUB_CONNECTOR",
        "reason": reason,
        "checkpointId": checkpoint,
        "commitSha": sha,
        "remote": remote,
        "branch": branch,
        "remoteVerified": False,
        "connectorRequestId": request["requestId"],
        "connectorRequestDigest": request["requestDigest"],
        "connectorRequestPath": str(path),
        "checkpointState": {
            "canonicalPassed": True,
            "localCheckpointed": True,
            "remoteCheckpointed": False,
        },
    }


def github_pull_request_refs(repo: Path, remote: str, sha: str) -> dict[str, Any]:
    url = remote_url(repo, remote)
    normalized = url.lower()
    if "github.com" not in normalized:
        return {"provider": "OTHER", "status": "NOT_APPLICABLE", "refs": []}
    result = run_git(repo, "ls-remote", remote, "refs/pull/*/head", check=False, timeout=20)
    if result.returncode != 0:
        return {
            "provider": "GITHUB", "status": "NOT_DISCOVERABLE", "refs": [],
            "detail": (result.stderr or result.stdout).strip(),
        }
    refs = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == sha and re.fullmatch(r"refs/pull/\d+/head", parts[1]):
            refs.append(parts[1])
    return {
        "provider": "GITHUB",
        "status": "VERIFIED" if refs else "NO_ASSOCIATED_PR_REF",
        "refs": sorted(dict.fromkeys(refs)),
    }


def latest_checkpoint_record(task_dir: Path, checkpoint_id: str = "") -> dict[str, Any]:
    document = load_document(task_dir)
    checkpoints = document.get("checkpoints")
    if not isinstance(checkpoints, list) or not checkpoints:
        raise GitCheckpointError("no local Git checkpoint is available to push")
    if checkpoint_id:
        matches = [
            item for item in checkpoints
            if isinstance(item, dict) and item.get("checkpoint_id") == checkpoint_id
        ]
        if len(matches) != 1:
            raise GitCheckpointError(f"checkpoint does not resolve uniquely in git_checkpoints.yaml: {checkpoint_id}")
        return matches[0]
    item = checkpoints[-1]
    if not isinstance(item, dict):
        raise GitCheckpointError("latest Git checkpoint record is invalid")
    return item


def push_checkpoint(task_dir: Path, *, agent: str, checkpoint_id: str = "", transport: str = "auto") -> dict[str, Any]:
    if agent != "ProjectManagerAgent":
        raise GitCheckpointError("only ProjectManagerAgent may push Git checkpoints")
    if transport not in {"auto", "native", "connector"}:
        raise GitCheckpointError("git checkpoint transport must be auto, native, or connector")
    task_dir = task_dir.resolve()
    import long_task  # local import avoids module cycle
    state = long_task.runtime_state(task_dir)
    agent_context.authorize_static(agent, "git_checkpoint", "push")
    mode, _ = work_mode.load_mode(version_root(task_dir).parent.parent, str(state.get("version") or ""))
    if not mode.git_checkpoint:
        raise GitCheckpointError("wk -gp/--git-push requires wk -gc/--git-checkpoint")
    if not mode.git_push:
        raise GitCheckpointError("wk -gp/--git-push is not enabled in work.md")
    repo = repository_root(task_dir)
    branch = branch_name(repo)
    record = latest_checkpoint_record(task_dir, checkpoint_id)
    checkpoint = str(record.get("checkpoint_id") or "")
    shas = resolve_checkpoint_shas(repo, checkpoint)
    if len(shas) != 1:
        raise GitCheckpointError(f"checkpoint must resolve to exactly one local Git commit: {checkpoint}")
    sha = shas[0]
    head = run_git(repo, "rev-parse", "HEAD").stdout.strip()
    if head != sha:
        raise GitCheckpointError(
            "wk -gp refuses to push uncheckpointed commits: local HEAD must equal the selected checkpoint commit"
        )
    if str(record.get("branch") or "") != branch:
        raise GitCheckpointError(
            f"checkpoint branch {record.get('branch')} differs from current branch {branch}"
        )
    remote = configured_remote(repo, branch)
    verified = connector_verified_result(repo, checkpoint=checkpoint, sha=sha, branch=branch, remote=remote)
    if verified is not None and transport != "native":
        return verified
    base_commit = str(record.get("base_commit") or "")
    if transport == "connector":
        return connector_required_result(
            repo, remote=remote, branch=branch, checkpoint=checkpoint, sha=sha,
            base_commit=base_commit, reason="connector_transport_selected",
        )
    try:
        remote_before = remote_branch_head(repo, remote, branch)
    except GitCheckpointError as exc:
        classification = native_transport_classification(str(exc))
        if transport == "auto" and classification in {"native_transport_unavailable", "native_auth_unavailable"}:
            return connector_required_result(
                repo, remote=remote, branch=branch, checkpoint=checkpoint, sha=sha,
                base_commit=base_commit, reason=classification,
            )
        raise
    push_status = "ALREADY_PUSHED"
    if remote_before != sha:
        refspec = f"HEAD:refs/heads/{branch}"
        result = run_git(repo, "push", "--porcelain", remote, refspec, check=False, timeout=20)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            classification = native_transport_classification(detail)
            if transport == "auto" and classification in {"native_transport_unavailable", "native_auth_unavailable"}:
                return connector_required_result(
                    repo, remote=remote, branch=branch, checkpoint=checkpoint, sha=sha,
                    base_commit=base_commit, reason=classification,
                )
            raise GitCheckpointError(f"{classification}: git push failed: {detail}")
        push_status = "PUSHED"
    remote_after = remote_branch_head(repo, remote, branch)
    if remote_after != sha:
        raise GitCheckpointError(
            f"remote_checkpoint_verification_failed: {remote}/{branch}={remote_after or '<missing>'} local={sha}"
        )
    pr = github_pull_request_refs(repo, remote, sha)
    return {
        "status": push_status,
        "transport": "NATIVE_GIT",
        "identityMode": "EXACT_COMMIT_SHA",
        "checkpointId": checkpoint,
        "commitSha": sha,
        "remoteCommitSha": sha,
        "remote": remote,
        "branch": branch,
        "remoteHeadSha": remote_after,
        "remoteTreeSha": run_git(repo, "rev-parse", f"{sha}^{{tree}}").stdout.strip(),
        "remoteVerified": True,
        "pullRequestVerification": pr,
        "checkpointState": {
            "canonicalPassed": True,
            "localCheckpointed": True,
            "remoteCheckpointed": True,
        },
    }


def complete_connector_push(task_dir: Path, *, agent: str, receipt: dict[str, Any], checkpoint_id: str = "") -> dict[str, Any]:
    if agent != "ProjectManagerAgent":
        raise GitCheckpointError("only ProjectManagerAgent may complete GitHub Connector checkpoint pushes")
    task_dir = task_dir.resolve()
    from runtime import long_task
    state = long_task.runtime_state(task_dir)
    agent_context.authorize_static(agent, "git_checkpoint", "push")
    mode, _ = work_mode.load_mode(version_root(task_dir).parent.parent, str(state.get("version") or ""))
    if not mode.git_checkpoint or not mode.git_push:
        raise GitCheckpointError("connector completion requires wk -gc and wk -gp")
    repo = repository_root(task_dir)
    branch = branch_name(repo)
    record = latest_checkpoint_record(task_dir, checkpoint_id)
    checkpoint = str(record.get("checkpoint_id") or "")
    shas = resolve_checkpoint_shas(repo, checkpoint)
    if len(shas) != 1:
        raise GitCheckpointError(f"checkpoint must resolve to exactly one local Git commit: {checkpoint}")
    sha = shas[0]
    if run_git(repo, "rev-parse", "HEAD").stdout.strip() != sha:
        raise GitCheckpointError("connector completion requires local HEAD to remain the selected checkpoint commit")
    remote = configured_remote(repo, branch)
    url = remote_url(repo, remote)
    try:
        request = git_connector_transport.build_request(
            repo, remote=remote, remote_url=url, branch=branch, checkpoint_id=checkpoint,
            local_commit_sha=sha, base_commit_sha=str(record.get("base_commit") or ""),
        )
        verified_state = git_connector_transport.validate_receipt(request, receipt)
        state_file = git_connector_transport.persist_verified_state(repo, checkpoint, verified_state)
    except git_connector_transport.GitConnectorTransportError as exc:
        raise GitCheckpointError(f"connector_checkpoint_verification_failed: {exc}") from exc
    return {
        "status": "CONNECTOR_VERIFIED",
        "transport": "GITHUB_CONNECTOR",
        "identityMode": verified_state["identityMode"],
        "checkpointId": checkpoint,
        "commitSha": sha,
        "remoteCommitSha": verified_state["remoteCommitSha"],
        "remote": remote,
        "branch": branch,
        "remoteHeadSha": verified_state["remoteHeadSha"],
        "remoteTreeSha": verified_state["remoteTreeSha"],
        "remoteVerified": True,
        "connectorStatePath": str(state_file),
        "pullRequestVerification": verified_state["pullRequestVerification"],
        "checkpointState": {
            "canonicalPassed": True,
            "localCheckpointed": True,
            "remoteCheckpointed": True,
        },
    }


def _commit_checkpoint_unlocked(task_dir: Path, *, agent: str, title: str = "", changes: list[str] | None = None, phase: str = ""):
    if agent != "ProjectManagerAgent":
        raise GitCheckpointError("only ProjectManagerAgent may create Git checkpoints")
    task_dir = task_dir.resolve()
    # Reuse the complete long-task gate validation without creating a module import cycle at import time.
    import long_task  # local import by design
    state_seed = long_task.runtime_state(task_dir)
    ensure_document(task_dir, str(state_seed.get('target_id') or task_dir.name), str(state_seed.get('version') or ''))
    errors, _, state_for_auth, _ = long_task.validate_task(task_dir)
    if errors:
        raise GitCheckpointError("task is invalid before Git checkpoint: " + "; ".join(errors))
    agent_context.authorize_static(agent, "git_checkpoint", "commit")
    mode, _ = work_mode.load_mode(version_root(task_dir).parent.parent, str(state_for_auth.get("version")))
    if not mode.git_checkpoint:
        raise GitCheckpointError("wk -gc/--git-checkpoint is not enabled in work.md")
    state, tasks, attempts, outcome = current_gate(task_dir, phase_override=phase)
    repo = repository_root(task_dir)
    branch = branch_name(repo)
    operation = in_progress_operation(repo)
    if operation:
        raise GitCheckpointError(f"Git {operation} is in progress")
    if git_index_path(repo).with_name("index.lock").exists():
        raise GitCheckpointError("Git index.lock is present; automatic checkpoint is blocked")
    initial_index_digest = index_digest(repo)
    initial_index_tree = index_tree(repo)
    head = run_git(repo, "rev-parse", "HEAD").stdout.strip()
    head_tree = run_git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
    checkpoint_id = checkpoint_id_for(state, outcome)
    document = load_document(task_dir)
    checkpoints = document.get("checkpoints")
    if not isinstance(checkpoints, list):
        raise GitCheckpointError("git_checkpoints.yaml checkpoints must be an array")
    existing = next((item for item in checkpoints if isinstance(item, dict) and item.get("checkpoint_id") == checkpoint_id), None)
    if existing:
        shas = resolve_checkpoint_shas(repo, checkpoint_id)
        if len(shas) == 1:
            return {
                "status": "ALREADY_COMMITTED",
                "checkpointId": checkpoint_id,
                "commitSha": shas[0],
                "branch": branch,
                "checkpointState": {
                    "canonicalPassed": True,
                    "localCheckpointed": True,
                    "remoteCheckpointed": False,
                },
            }
        raise GitCheckpointError(f"checkpoint record already exists but does not resolve uniquely: {checkpoint_id}")
    # Only a new commit needs a clean index matching HEAD. An already committed
    # checkpoint remains idempotent without touching the caller's index.
    if (
        run_git(repo, "diff", "--cached", "--quiet", check=False).returncode != 0
        or initial_index_tree != head_tree
    ):
        raise GitCheckpointError(
            "Git index must be clean and match HEAD before automatic checkpoint commit; "
            f"preserved index digest={initial_index_digest}"
        )
    before_paths = status_paths(repo)
    checkpoint_rel = relative(repo, checkpoint_path(task_dir))
    patterns = allowed_patterns(repo, task_dir, state, tasks, attempts, str(outcome.get("phase")))
    for path in before_paths:
        if denied_path(path):
            raise GitCheckpointError(f"denied path cannot be committed: {path}")
        if not path_allowed(path, patterns):
            raise GitCheckpointError(f"changed path is outside current sequential task scope: {path}")
    same_phase = [item for item in checkpoints if isinstance(item, dict) and item.get("phase") == outcome.get("phase")]
    supersedes = str(same_phase[-1].get("checkpoint_id")) if same_phase else ""
    summaries = [
        str(item.get("summary")) for item in attempts
        if isinstance(item, dict)
        and item.get("phase") == outcome.get("phase")
        and item.get("iteration_id") == outcome.get("iteration_id")
        and item.get("status") == "PASSED"
        and str(item.get("summary") or "").strip()
    ]
    selected_changes = list(changes or summaries[-3:])[:3]
    commit_title = title.strip() or default_title(str(state["target_id"]), str(outcome["phase"]))
    record = {
        "checkpoint_id": checkpoint_id,
        "phase": outcome["phase"],
        "iteration_id": outcome["iteration_id"],
        "iteration_no": outcome["iteration_no"],
        "stage_revision": outcome["output_revision"],
        "stage_outcome_ref": outcome["id"],
        "branch": branch,
        "base_commit": head,
        "changed_paths": sorted(dict.fromkeys([*before_paths, checkpoint_rel])),
        "content_digest": content_digest(repo, before_paths, checkpoint_rel),
        "commit_title": commit_title,
        "created_by_agent": agent,
        "created_at": now(),
        "supersedes_checkpoint_id": supersedes,
    }
    backup = checkpoint_path(task_dir).read_bytes()
    checkpoints.append(record)
    simple_yaml.dump(document, checkpoint_path(task_dir))
    all_paths = status_paths(repo)
    for path in all_paths:
        if denied_path(path):
            checkpoint_path(task_dir).write_bytes(backup)
            raise GitCheckpointError(f"denied path cannot be committed: {path}")
        if path != checkpoint_rel and not path_allowed(path, patterns):
            checkpoint_path(task_dir).write_bytes(backup)
            raise GitCheckpointError(f"changed path is outside current sequential task scope: {path}")
    message = build_message(commit_title, selected_changes, checkpoint_id, str(outcome["output_revision"]))
    try:
        if not all_paths:
            raise GitCheckpointError("no files available for checkpoint commit")
        if index_digest(repo) != initial_index_digest:
            raise GitCheckpointError("Git index changed concurrently before checkpoint staging")
        with isolated_index(repo) as temporary_index:
            # Re-check after cloning the disposable index.  An external writer
            # may have changed the real index between the preflight check and
            # the clone; fail closed rather than committing its staged changes.
            if index_digest(repo) != initial_index_digest:
                raise GitCheckpointError("Git index changed concurrently while creating isolated checkpoint index")
            temporary_env = index_env(temporary_index)
            run_git(repo, "add", "--", *all_paths, env=temporary_env)
            check_result = run_git(
                repo, "diff", "--cached", "--check", "--", *all_paths, check=False, env=temporary_env
            )
            if check_result.returncode != 0:
                raise GitCheckpointError(
                    f"git diff --cached --check failed: {(check_result.stderr or check_result.stdout).strip()}"
                )
            expected_tree = run_git(repo, "write-tree", env=temporary_env).stdout.strip()
            commit_result = subprocess.run(
                ["git", "-C", str(repo), "commit", "--no-verify", "-F", "-"], input=message, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**os.environ, **temporary_env},
            )
            if commit_result.returncode != 0:
                raise GitCheckpointError(f"git commit failed: {(commit_result.stderr or commit_result.stdout).strip()}")
            commit_tree = run_git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
            if commit_tree != expected_tree:
                raise GitCheckpointError(
                    "checkpoint commit tree differs from the isolated staged tree; possible Hook/index mutation"
                )
    except Exception:
        checkpoint_path(task_dir).write_bytes(backup)
        raise
    sha = run_git(repo, "rev-parse", "HEAD").stdout.strip()
    index_sync = refresh_real_index_to_head(
        repo,
        expected_head=sha,
        expected_index_digest=initial_index_digest,
        expected_index_tree=initial_index_tree,
    )
    final_index_digest = str(index_sync.get("digestAfter") or index_digest(repo))
    final_index_tree = str(index_sync.get("treeAfter") or index_tree(repo))
    shas = resolve_checkpoint_shas(repo, checkpoint_id)
    if shas != [sha]:
        raise GitCheckpointError(f"created commit does not resolve uniquely by checkpoint trailer: {checkpoint_id}")
    result = {
        "status": "COMMITTED",
        "checkpointId": checkpoint_id,
        "commitSha": sha,
        "branch": branch,
        "changedPaths": all_paths,
        "checkpointState": {
            "canonicalPassed": True,
            "localCheckpointed": True,
            "remoteCheckpointed": False,
        },
        "indexDigestBefore": initial_index_digest,
        "indexDigestAfter": final_index_digest,
        "indexTreeBefore": initial_index_tree,
        "indexTreeAfter": final_index_tree,
        "indexPreserved": index_sync.get("status") != "REFRESHED",
        "indexRefreshed": index_sync.get("status") == "REFRESHED",
        "indexSync": {
            **index_sync,
            "targetHead": sha,
            "digestBefore": initial_index_digest,
            "treeBefore": initial_index_tree,
        },
    }
    if index_sync.get("status") in {"PRESERVED_CONCURRENT_CHANGE", "PRESERVED_HEAD_CHANGE"}:
        result["status"] = "COMMITTED_WITH_INDEX_CONCURRENCY"
        result["warning"] = (
            "checkpoint committed, but the real Git index was not refreshed; "
            "inspect and preserve the current index state"
        )
    elif index_sync.get("status") == "FAILED":
        result["status"] = "COMMITTED_WITH_INDEX_REFRESH_FAILED"
        result["warning"] = (
            "checkpoint committed, but refreshing the real Git index failed; "
            "inspect the index before continuing"
        )
    return result


def commit_checkpoint(task_dir: Path, *, agent: str, title: str = "", changes: list[str] | None = None, phase: str = "") -> dict[str, Any]:
    repo = repository_root(Path(task_dir).resolve())
    with checkpoint_lock(repo):
        return _commit_checkpoint_unlocked(task_dir, agent=agent, title=title, changes=changes, phase=phase)


def command_commit(args: argparse.Namespace) -> int:
    agent = agent_context.selected_agent(args)
    task_dir = Path(args.task_dir)
    result = commit_checkpoint(task_dir, agent=agent, title=args.title or "", changes=args.change or [], phase=args.phase or "")
    import long_task  # local import avoids module cycle
    state = long_task.runtime_state(task_dir)
    mode, _ = work_mode.load_mode(version_root(task_dir).parent.parent, str(state.get("version") or ""))
    if mode.git_push:
        result["gitPush"] = push_checkpoint(task_dir, agent=agent, checkpoint_id=str(result.get("checkpointId") or ""))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 3 if isinstance(result.get("gitPush"), dict) and result["gitPush"].get("status") == "CONNECTOR_REQUIRED" else 0


def command_push(args: argparse.Namespace) -> int:
    agent = agent_context.selected_agent(args)
    result = push_checkpoint(
        Path(args.task_dir), agent=agent, checkpoint_id=args.checkpoint_id or "",
        transport=str(getattr(args, "transport", "auto") or "auto"),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 3 if result.get("status") == "CONNECTOR_REQUIRED" else 0


def command_connector_request(args: argparse.Namespace) -> int:
    agent = agent_context.selected_agent(args)
    result = push_checkpoint(
        Path(args.task_dir), agent=agent, checkpoint_id=args.checkpoint_id or "", transport="connector"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_connector_complete(args: argparse.Namespace) -> int:
    agent = agent_context.selected_agent(args)
    if args.receipt_file == "-":
        receipt = json.load(sys.stdin)
    else:
        receipt = json.loads(Path(args.receipt_file).read_text(encoding="utf-8"))
    if not isinstance(receipt, dict):
        raise GitCheckpointError("connector receipt root must be an object")
    result = complete_connector_push(
        Path(args.task_dir), agent=agent, receipt=receipt, checkpoint_id=args.checkpoint_id or ""
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def command_validate(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "git_checkpoint", "validate")
    errors, _, resolved = validate_document(Path(args.task_dir), require_resolved=bool(args.require_resolved))
    payload = {"status": "FAILED" if errors else "PASSED", "errors": errors, "checkpoints": resolved}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(payload["status"])
    return 1 if errors else 0


def command_status(args: argparse.Namespace) -> int:
    agent = agent_context.authorize_command(args, "git_checkpoint", "status")
    errors, _, resolved = validate_document(Path(args.task_dir), require_resolved=True)
    payload = {"status": "FAILED" if errors else "PASSED", "errors": errors, "checkpoints": resolved}
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 1 if errors else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commit = commands.add_parser("commit", help="create one sequential phase checkpoint commit")
    agent_context.add_agent_argument(commit)
    commit.add_argument("--task-dir", required=True)
    commit.add_argument("--title", default="")
    commit.add_argument("--change", action="append", default=[])
    commit.add_argument("--phase", default="", help="backfill a PASSED phase checkpoint after the workflow advanced")
    commit.set_defaults(func=command_commit)
    push = commands.add_parser("push", help="push the latest or selected checkpoint to the configured remote branch and verify it")
    agent_context.add_agent_argument(push)
    push.add_argument("--task-dir", required=True)
    push.add_argument("--checkpoint-id", default="")
    push.add_argument("--transport", choices=["auto", "native", "connector"], default="auto")
    connector_request = commands.add_parser("connector-request", help="materialize a deterministic GitHub Connector request for the selected checkpoint")
    agent_context.add_agent_argument(connector_request)
    connector_request.add_argument("--task-dir", required=True)
    connector_request.add_argument("--checkpoint-id", default="")
    connector_request.set_defaults(func=command_connector_request)
    connector_complete = commands.add_parser("connector-complete", help="validate a GitHub Connector receipt and mark the selected remote checkpoint verified")
    agent_context.add_agent_argument(connector_complete)
    connector_complete.add_argument("--task-dir", required=True)
    connector_complete.add_argument("--checkpoint-id", default="")
    connector_complete.add_argument("--receipt-file", required=True, help="receipt JSON path or - for stdin")
    connector_complete.set_defaults(func=command_connector_complete)
    push.set_defaults(func=command_push)
    validate = commands.add_parser("validate", help="validate checkpoint declarations")
    agent_context.add_agent_argument(validate)
    validate.add_argument("--task-dir", required=True)
    validate.add_argument("--require-resolved", action="store_true")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=command_validate)
    status = commands.add_parser("status", help="resolve checkpoint declarations to Git commit SHAs")
    agent_context.add_agent_argument(status)
    status.add_argument("--task-dir", required=True)
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=command_status)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        return int(args.func(args))
    except (GitCheckpointError, agent_context.AgentAuthorizationError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
